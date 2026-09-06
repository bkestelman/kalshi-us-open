from datetime import datetime, timezone, timedelta
from kalshi import get, paginate

print("=== does the /markets LIST carry volume_fp and the touch? ===")
ms = paginate("/markets", "markets", series_ticker="KXATPMATCH", status="open", limit=1000)
print(f"  {len(ms)} open ATP match markets")
k = ms[0]
for f in ("ticker","volume_fp","volume_24h_fp","yes_bid_dollars","yes_ask_dollars",
          "last_price_dollars","open_interest_fp","status","expected_expiration_time"):
    print(f"    {f} = {k.get(f)!r}")

print("\n=== per-60s contract volume: pre-match vs in-play, several matches ===")
def vol(tk, t0):
    d = get("/markets/trades", ticker=tk, min_ts=t0, max_ts=t0+60, limit=1000)
    tr = (d or {}).get("trades", [])
    return sum(float(t.get("count_fp") or 0) for t in tr), len(tr)

TKS = ["KXATPMATCH-26SEP02AUGKHA-AUG", "KXWTAMATCH-26AUG30KEYKOR-KEY",
       "KXWTAMATCH-26AUG30RAKKRE-KRE", "KXATPMATCH-26AUG30PRISHE-PRI",
       "KXWTAMATCH-26SEP02KEYBON-KEY"]
print(f"  {'market':34s} {'vol_fp':>10s} | contracts per 60s at T-minus (from close_time)")
print(f"  {'':34s} {'':>10s} | -8h    -4h    -3h   -2h    -1h    -30m   -10m")
for tk in TKS:
    m = get(f"/markets/{tk}")["market"]
    close = datetime.fromisoformat(m["close_time"].replace("Z","+00:00")).timestamp()
    cells = []
    for mins in (480, 240, 180, 120, 60, 30, 10):
        v, n = vol(tk, int(close - mins*60))
        cells.append(f"{v:6.0f}")
    print(f"  {tk:34s} {float(m['volume_fp']):10.0f} | " + " ".join(cells))
