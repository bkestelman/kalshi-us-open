"""Shared paths and capture-file IO.

Two things every script here needs and none of them agreed on:

* WHERE THE REPO IS. `ROOT` is derived from this file, so a checkout works
  wherever it lands; `KALSHI_DATA` overrides the data directory if the
  capture should live on a different volume from the code. Copied from
  ../kalshi-tennis, where the same file is `KALSHI_TENNIS_DATA`; point that
  env var here and the analysis scripts read the same capture.

* HOW THE CAPTURE IS STORED. The ws feed is 1.9-7.6 GB/day raw and gzips
  14x, so the collectors write `.jsonl.gz`. Readers must accept either, since
  2026-07-30..08-02 were captured uncompressed. `open_capture` and
  `capture_files` hide the difference: a glob for "ws_20260801.jsonl" matches
  the .gz too, and a day present in both forms yields only one file.
"""
import glob as _glob
import gzip
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
LIVE = os.environ.get("KALSHI_DATA", os.path.join(ROOT, "data", "live"))


def secret(name):
    """Read an API key/id that must never be committed (see .gitignore)."""
    with open(os.path.join(ROOT, name), "rb") as f:
        return f.read()


def _gz_lines(path):
    """Yield lines from a gzip file, tolerating a truncated final member.

    Capture is routinely analysed while it is still being written, and the
    collector's last flush is mid-member until it closes. Python's gzip raises
    EOFError there and loses the whole read; a partial trailing line is simply
    the newest few seconds of capture, so stop cleanly instead.
    """
    with gzip.open(path, "rt", encoding="utf-8") as f:
        try:
            for line in f:
                yield line
        except (EOFError, OSError, gzip.BadGzipFile):
            return


def open_capture(path, mode="rt"):
    """Line-iterable handle for a capture file, gzipped by extension.

    Returns a context manager in both cases, so callers can keep using
    `with open_capture(fn) as f: for line in f:` regardless of format.
    """
    if not path.endswith(".gz"):
        return open(path, mode, encoding="utf-8")
    if "r" not in mode:
        return gzip.open(path, mode, encoding="utf-8")

    class _Reader:
        def __enter__(self):
            return _gz_lines(path)

        def __exit__(self, *exc):
            return False

    return _Reader()


def capture_files(pattern):
    """Capture files matching `pattern`, compressed or not, in day order.

    `pattern` may be a bare name ("ws_*.jsonl"), relative to LIVE, or an
    absolute path. A day should exist in only one form -- `DayWriter` keeps
    appending in whichever form the day started in -- but if both are present
    the raw file is returned first, since that is the only order in which they
    could have been written.

    Whitespace separates ALTERNATIVES ("ws_202607*.jsonl ws_20260801*.jsonl"),
    because a date range rarely falls inside one glob and the obvious way to
    ask for one silently matched nothing: glob has no brace expansion, so the
    whole string was one pattern with a space in it and a run over a fifth of
    the capture reported a clean zero rather than an error.
    """
    pats = [q for q in pattern.split() if q] or [pattern]
    pats = [q if os.path.isabs(q) else os.path.join(LIVE, q) for q in pats]
    found = set()
    for q in pats:
        found |= set(_glob.glob(q)) | set(_glob.glob(q + ".gz"))
    out = {}
    for p in found:
        out.setdefault(p[:-3] if p.endswith(".gz") else p, []).append(p)
    return [p for _, v in sorted(out.items()) for p in sorted(v)]


class DayWriter:
    """Append-only, UTC-day-rotating, gzipped JSONL sink.

    The collectors used to open()/write()/close() once PER MESSAGE, which at
    millions of messages a day is pure syscall overhead. This keeps one handle
    open and flushes on a timer instead, so a kill -9 loses at most
    `flush_sec` of capture rather than the whole buffer.
    """

    def __init__(self, prefix, level=6, flush_sec=5.0, compress=True):
        self.prefix = prefix
        self.level = level
        self.flush_sec = flush_sec
        self.compress = compress
        self.day = None
        self.fh = None
        self._last = 0.0

    def path(self, day):
        """Where this day's capture goes.

        A day that was already started uncompressed STAYS uncompressed, even
        when compression is on. Otherwise restarting a collector mid-day would
        leave the same day split across a .jsonl and a .jsonl.gz, which is a
        silent data loss waiting to happen in every reader.
        """
        p = os.path.join(LIVE, f"{self.prefix}_{day}.jsonl")
        if self.compress and not os.path.exists(p):
            return p + ".gz"
        return p

    def write(self, line, day, now):
        if day != self.day:
            self.close()
            self.day = day
            os.makedirs(LIVE, exist_ok=True)
            p = self.path(day)
            self.fh = (gzip.open(p, "at", compresslevel=self.level,
                                 encoding="utf-8")
                       if p.endswith(".gz") else open(p, "a"))
        self.fh.write(line)
        if now - self._last >= self.flush_sec:
            self.fh.flush()
            self._last = now

    def close(self):
        if self.fh:
            try:
                self.fh.close()
            except Exception:
                pass
        self.fh = None


def prune(prefix, keep_days, log=print):
    """Delete captures older than the newest `keep_days` days. keep_days<=0
    keeps everything -- the local box has 220 GB and wants the history; a
    6.7 GB VPS does not."""
    if keep_days <= 0:
        return
    days = sorted({os.path.basename(p).split("_")[-1].split(".")[0]
                   for p in capture_files(f"{prefix}_*.jsonl")})
    for d in days[:-keep_days] if len(days) > keep_days else []:
        for p in capture_files(f"{prefix}_{d}.jsonl") + \
                _glob.glob(os.path.join(LIVE, f"{prefix}_{d}.jsonl.gz")):
            try:
                os.remove(p)
                log(f"PRUNE {os.path.basename(p)}")
            except OSError as e:
                log(f"PRUNE FAILED {p}: {e}")
