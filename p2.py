from datetime import datetime, timezone
from kalshi import get

TK = "KXATPMATCH-26SEP02AUGKHA-AUG"
def ts(h, m=0, d=2, mo=9):
    return int(datetime(2026, mo, d, h, m, tzinfo=timezone.utc).timestamp())

print("=== does /markets/trades accept min_ts/max_ts? ===")
d = get("/markets/trades", ticker=TK, min_ts=ts(19), max_ts=ts(19,1), limit=100)
tr = (d or {}).get("trades", [])
print(f"  19:00-19:01Z -> {len(tr)} trades, cursor={'yes' if (d or {}).get('cursor') else 'no'}")
if tr:
    print(f"  sample created_time={tr[0]['created_time']}")

print("\n=== contracts traded per 60s window, Sep 2 (match was ~19:00-22:00Z) ===")
for h in range(12, 24):
    for mm in (0, 30):
        d = get("/markets/trades", ticker=TK,
                min_ts=ts(h, mm), max_ts=ts(h, mm)+60, limit=1000)
        tr = (d or {}).get("trades", [])
        n = sum(float(t.get("count_fp") or 0) for t in tr)
        print(f"  {h:02d}:{mm:02d}Z  trades={len(tr):4d}  contracts={n:10.0f}")
