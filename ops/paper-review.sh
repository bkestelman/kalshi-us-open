#!/bin/bash
set -euo pipefail
cd /home/ubuntu/kalshi-us-open
mkdir -p data/reviews
exec 9>data/reviews/review.lock
flock -n 9 || exit 0
review_stamp=$(date -u +%Y%m%dT%H%M%SZ)
python3 paper_report.py --record > "data/reviews/${review_stamp}-health.json"
/home/ubuntu/.local/bin/codex exec -C /home/ubuntu/kalshi-us-open -s danger-full-access -c 'approval_policy="never"' --color never -o "data/reviews/${review_stamp}-review.md" - < ops/paper-review.md > "data/reviews/${review_stamp}-execution.log" 2>&1
