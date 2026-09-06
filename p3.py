from datetime import datetime, timezone
from kalshi import get

def ts(mo,d,h,m=0): return int(datetime(2026,mo,d,h,m,tzinfo=timezone.utc).timestamp())

for TK, label in (("KXATPMATCH-26SEP02AUGKHA-AUG", "Auger-Aliassime LOSS (ticker says Sep 2)"),
                  ("KXWTAMATCH-26AUG30KEYKOR-KEY", "Keys (ticker says Aug 30)")):
    m = get(f"/markets/{TK}")["market"]
    print(f"\n=== {label} ===")
    print(f"  expected_expiration_time = {m['expected_expiration_time']}")
    print(f"  open_time                = {m['open_time']}")
    print(f"  close_time               = {m['close_time']}   <- match actually ended")
    print(f"  settlement_ts            = {m.get('settlement_ts')}")
    print(f"  volume_fp                = {m.get('volume_fp')}")
    exp = datetime.fromisoformat(m['expected_expiration_time'].replace("Z","+00:00"))
    clo = datetime.fromisoformat(m['close_time'].replace("Z","+00:00"))
    print(f"  close_time - expected     = {(clo-exp).total_seconds()/3600:+.1f} h")
    print(f"  WATCH window = expected-7h .. expected+12h "
          f"= {(exp.timestamp()-7*3600)} .. {(exp.timestamp()+12*3600)}")
    inwin = exp.timestamp()-7*3600 <= clo.timestamp() <= exp.timestamp()+12*3600
    print(f"  was the match's END inside the watch window? {inwin}")

print("\n=== contracts per 60s on Sep 3 for AUGKHA (min_ts/max_ts) ===")
TK="KXATPMATCH-26SEP02AUGKHA-AUG"
for h in range(14, 20):
    for mm in (0,30):
        d = get("/markets/trades", ticker=TK, min_ts=ts(9,3,h,mm),
                max_ts=ts(9,3,h,mm)+60, limit=1000)
        tr=(d or {}).get("trades",[])
        print(f"  Sep3 {h:02d}:{mm:02d}Z trades={len(tr):4d} "
              f"contracts={sum(float(t.get('count_fp') or 0) for t in tr):9.0f}")
