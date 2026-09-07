#!/bin/bash
set -euo pipefail
cd /home/ubuntu/kalshi-us-open
mkdir -p data/reviews
exec 9>data/reviews/review.lock
flock -n 9 || exit 0
review_stamp=$(date -u +%Y%m%dT%H%M%SZ)
# Preserve an explicit update even when authentication, usage limits or the job fail.
review_finished=0
finish_update() {
    review_exit=$?
    {
        printf '\n- %s UTC: ' "$review_stamp"
        if [[ "$review_exit" -eq 0 && "$review_finished" -eq 1 && -s "data/reviews/${review_stamp}-review.md" ]]; then
            tr '\n' ' ' < "data/reviews/${review_stamp}-review.md"
            printf '\n'
        else
            printf 'Review incomplete (exit %s); inspect %s-execution.log.\n' "$review_exit" "$review_stamp"
        fi
    } >> data/reviews/updates.md
    /usr/bin/python3 review_notify.py --review "$review_stamp" --exit-code "$review_exit" || true
}
trap finish_update EXIT
python3 paper_report.py --record > "data/reviews/${review_stamp}-health.json"
/home/ubuntu/.local/bin/codex exec -C /home/ubuntu/kalshi-us-open -s danger-full-access -c 'approval_policy="never"' --color never -o "data/reviews/${review_stamp}-review.md" - < ops/paper-review.md > "data/reviews/${review_stamp}-execution.log" 2>&1

review_finished=1
