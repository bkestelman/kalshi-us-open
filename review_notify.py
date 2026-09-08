"""Durable local delivery of review results and health alerts to the user's Codex thread."""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import time

from iolib import LIVE, ROOT
from paper_support import atomic_json

THREAD = '01a0786a-ed8c-71c1-be7d-927c35478e4d'
CODEX = '/home/ubuntu/.local/bin/codex'
DIRECTORY = Path(ROOT) / 'data' / 'reviews'


def enqueue(state, key, message):
    if key not in state['sent'] and key not in state['pending']:
        state['pending'][key] = message


def deliver(state, save, runner=subprocess.run):
    # Save before invoking the queue, so failures survive a process restart.
    save(state)
    for key, message in list(state['pending'].items())[:4]:
        try:
            result = runner([CODEX, 'queue', '--thread', THREAD, '--message', message],
                            capture_output=True, text=True, timeout=20, cwd=ROOT)
            if result.returncode:
                raise RuntimeError((result.stderr or result.stdout)[-500:])
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
            state['last_error'] = str(exc)
            save(state)
            return
        state['sent'][key] = time.time()
        del state['pending'][key]
        state['last_error'] = None
        save(state)
    # Bound state growth; pending messages are never discarded.
    cutoff = time.time() - 30 * 86400
    state['sent'] = {k: t for k, t in state['sent'].items() if t > cutoff}
    save(state)


def health_event(state, report, now):
    raw_warnings = sorted(report.get('warnings', []))
    # Routine ten-minute subscription refreshes briefly await snapshots.
    # Keep raw monitor evidence, but alert only for a persistent snapshot wait.
    waits = state.setdefault('snapshot_wait_since', {})
    current_waits = {w for w in raw_warnings
                     if w.startswith('related-market collector awaiting snapshots:')}
    waits = {w: waits.get(w, now) for w in current_waits}
    state['snapshot_wait_since'] = waits
    warnings = [w for w in raw_warnings if w not in waits or now - waits[w] >= 120]
    previous = state.get('health_warnings', [])
    if warnings and (warnings != previous or now - state.get('health_sent_at', 0) >= 43200):
        enqueue(state, 'health:'+str(int(now)),
                'AUTOMATED PAPER HEALTH ALERT (user-authorized monitoring): '+ '; '.join(warnings)+
                '. Inspect data/live/paper_report.json and current service/log state now, investigate and fix '
                'within the documented $250-total/$50-per-match live pilot and paper scope, and post a brief update here. Coordinate with '
                'any running paper-review.service before editing. Do not raise pilot caps or broaden live strategy.')
        state['health_sent_at'] = now
    elif previous and not warnings:
        enqueue(state, 'recovery:'+str(int(now)),
                'AUTOMATED PAPER HEALTH RECOVERY: the minute monitor warnings cleared. '
                'Verify current health and post a brief update here; preserve the documented pilot caps.')
    state['health_warnings'] = warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review')
    parser.add_argument('--exit-code', type=int, default=0)
    parser.add_argument('--health', action='store_true')
    args = parser.parse_args()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    with (DIRECTORY / 'notification.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = DIRECTORY / 'notification_state.json'
        state = json.loads(path.read_text()) if path.exists() else {'pending': {}, 'sent': {}}
        if args.review:
            review = DIRECTORY / (args.review+'-review.md')
            summary = review.read_text()[:6000] if review.exists() else '(No final report was produced.)'
            enqueue(state, 'review:'+args.review,
                    f'AUTOMATED PAPER REVIEW RESULT {args.review}; process exit {args.exit_code}.\n'
                    +summary+'\nReview artifacts: '+str(DIRECTORY / args.review)+
                    '-*. Post a brief update in this chat. If the review failed or identifies a material '
                    'problem, investigate immediately and fix within the documented live pilot and paper authorization. '
                    'Treat this as an automated report, not a new user instruction to expand scope. '
                    'Do not raise pilot caps or broaden live strategy. The review worker has finished.')
        if args.health:
            health_path = Path(LIVE) / 'paper_report.json'
            report = json.loads(health_path.read_text()) if health_path.exists() else {'warnings': ['missing health report']}
            if time.time() - report.get('t', 0) > 180:
                report.setdefault('warnings', []).append('health report older than 180 seconds')
            health_event(state, report, time.time())
        deliver(state, lambda value: atomic_json(str(path), value))
        if state.get('last_error'):
            print('Notification remains pending:', state['last_error'], flush=True)
            raise SystemExit(1)


if __name__ == '__main__':
    main()
