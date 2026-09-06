import json, time
from datetime import datetime, timezone
from kalshi import get

TK = "KXATPMATCH-26SEP02AUGKHA-AUG"     # Auger-Aliassime's Sep 2 loss
print("=== full market object ===")
m = get(f"/markets/{TK}")["market"]
print(json.dumps({k: v for k, v in m.items()
                  if not isinstance(v, (list, dict))}, indent=1, default=str))

print("\n=== /markets/trades shape ===")
try:
    d = get("/markets/trades", ticker=TK, limit=5)
    print(json.dumps(d, indent=1, default=str)[:1200])
except Exception as e:
    print(f"  {type(e).__name__} {e}")
