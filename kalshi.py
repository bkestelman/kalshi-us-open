"""Kalshi REST/WS primitives: signing, books, orders, fees.

Distilled from ../kalshi-tennis (maker_bot.signed, collector_ws.ws_headers,
maker_bot.Book) down to what a taker needs. A taker rests nothing, so there is
no cancel path, no queue model and no flatten -- roughly a tenth of what the
maker carries.

Two keys, deliberately separate:
  read-only  -- market data, the websocket, anything public
  trading    -- portfolio and orders
`signed` is the only thing that touches the trading key, and it loads it
lazily, so a paper session on a box with no trading key runs fine -- which is
what lets vps/deploy.sh withhold the trading key until you ask for --live.
"""
import base64
import http.client
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from iolib import secret

HOST = "api.elections.kalshi.com"
BASE = f"https://{HOST}/trade-api/v2"
API = "/trade-api/v2"
WS_HOST = f"wss://{HOST}"
WS_PATH = "/trade-api/ws/v2"

EPS = 1e-9


def fee(p):
    """Kalshi taker fee per contract at price p. Charged on the sale, and it
    is ~1c at the prices this strategy sells at, which is most of the gate."""
    return 0.07 * p * (1 - p)


def sign(priv, key_id, method, path):
    ts = str(int(time.time() * 1000))
    sig = priv.sign((ts + method + path.split("?")[0]).encode(),
                    padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                salt_length=hashes.SHA256().digest_size),
                    hashes.SHA256())
    return {"KALSHI-ACCESS-KEY": key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "KALSHI-ACCESS-TIMESTAMP": ts}


_keys = {}


def _load(id_file, key_file):
    if id_file not in _keys:
        _keys[id_file] = (
            secret(id_file).decode().strip(),
            serialization.load_pem_private_key(
                secret(key_file), password=None, backend=default_backend()))
    return _keys[id_file]


def ws_headers():
    """Read-only signature for the websocket handshake."""
    key_id, priv = _load("read_only_key_id", "read_only_api_key.rsa")
    return sign(priv, key_id, "GET", WS_PATH)


# urllib opens a fresh TCP+TLS connection per call, which is 100-300 ms of
# handshake in front of every order. One kept-alive connection per thread
# removes that; it is pure latency, unrelated to the rate limit.
_conn = threading.local()


def _connection():
    c = getattr(_conn, "c", None)
    if c is None:
        c = _conn.c = http.client.HTTPSConnection(HOST, timeout=10)
    return c


def _drop():
    c = getattr(_conn, "c", None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
    _conn.c = None


def signed(method, path, body=None):
    """Signed REST call on the TRADING key. -> (status, parsed-or-text).

    A pooled connection can be closed by the server between calls, and that
    surfaces on the NEXT request rather than at close time, so one retry on a
    fresh connection is expected traffic rather than an error.
    """
    data = json.dumps(body).encode() if body is not None else None
    for attempt in (0, 1):
        key_id, priv = _load("trade_api_id.txt", "trade_api_key.rsa")
        headers = sign(priv, key_id, method, path)
        headers["Content-Type"] = "application/json"
        try:
            c = _connection()
            c.request(method, path, body=data, headers=headers)
            r = c.getresponse()
            raw = r.read()                  # must drain to reuse the socket
            if r.status >= 400:
                return r.status, raw.decode(errors="replace")[:300]
            if not raw:
                return r.status, None
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, raw.decode(errors="replace")[:300]
        except Exception as e:
            _drop()
            if attempt:
                return -1, f"{type(e).__name__}: {e}"


def get(path, **params):
    """Unauthenticated GET. Market status, results and event metadata are
    public, so discovery needs no key at all."""
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == 2:
                raise
            time.sleep(1 + 2 * attempt)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1 + 2 * attempt)


V1 = f"https://{HOST}/v1"


def get_v1(path, **params):
    """Unauthenticated GET against the app's v1 API.

    This is NOT the documented trade-api/v2. It is used for exactly one thing
    -- `sports_paging_actual_start`, the live label the Kalshi app shows and
    the only place the exchange says a tennis match has actually STARTED
    (discovery.match_phases). Every caller must degrade to the documented
    schedule when this returns nothing, because an undocumented endpoint is
    free to change shape without notice.
    """
    url = f"{V1}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for attempt in range(2):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == 1:
                raise
            time.sleep(1)
        except Exception:
            if attempt == 1:
                raise
            time.sleep(1)


def paginate(path, key, **params):
    """Walk a cursor-paginated list endpoint.

    Kalshi returns an EMPTY PAGE rather than a 429 when it throttles a cursor
    walk, so a page of zero with a cursor still set is throttling, not the end
    of the list (../kalshi/tourn_taker.py hit this and silently truncated).
    """
    out, cursor = [], None
    for _ in range(200):
        p = dict(params)
        if cursor:
            p["cursor"] = cursor
        d = get(path, **p)
        if not d:
            break
        page = d.get(key) or []
        out.extend(page)
        cursor = d.get("cursor")
        if not cursor:
            break
        if not page:
            time.sleep(1.0)             # throttled: back off, do not stop
    return out


class Book:
    """One market's two ladders, in YES terms.

    Kalshi publishes a yes ladder and a no ladder for the same market; the
    best ask in yes terms is 1 - (best no bid). Consolidating within the
    ticker is correct and is not the same thing as consolidating across the
    two complement legs of an event, which FINDINGS.md measured as a null.
    """
    __slots__ = ("yes", "no", "since", "last_t")

    def __init__(self):
        self.yes, self.no = {}, {}
        self.since = {}          # yes price -> when this level became non-empty
        self.last_t = 0.0

    def snapshot(self, m, t):
        self.yes = {round(float(p), 4): float(q)
                    for p, q in (m.get("yes_dollars_fp") or [])}
        self.no = {round(float(p), 4): float(q)
                   for p, q in (m.get("no_dollars_fp") or [])}
        self.since = {p: t for p, q in self.yes.items() if q > EPS}
        self.last_t = t

    def delta(self, m, t):
        side = m["side"]
        d = self.yes if side == "yes" else self.no
        p = round(float(m["price_dollars"]), 4)
        d[p] = d.get(p, 0.0) + float(m["delta_fp"])
        if d[p] <= EPS:
            d.pop(p, None)
        if side == "yes":
            if d.get(p, 0.0) > EPS:
                self.since.setdefault(p, t)
            else:
                self.since.pop(p, None)
        self.last_t = t

    def bid(self):
        y = [p for p, q in self.yes.items() if q > EPS]
        return max(y) if y else None

    def ask(self):
        n = [p for p, q in self.no.items() if q > EPS]
        return round(1 - max(n), 4) if n else None

    def bid_size(self):
        b = self.bid()
        return self.yes.get(b, 0.0) if b is not None else 0.0

    def mid(self):
        b, a = self.bid(), self.ask()
        return (b + a) / 2 if (b is not None and a is not None and b < a) else None
