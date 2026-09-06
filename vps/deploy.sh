#!/usr/bin/env bash
# Ship winner_taker to the VPS and install its systemd units.
#
#   ./vps/deploy.sh kalshi-vps                 paper only (no trading key)
#   ./vps/deploy.sh --live kalshi-vps          also ship the trading key + live unit
#   ./vps/deploy.sh --code-only kalshi-vps     push code, restart what is running
#
# Pass the ssh CONFIG ALIAS, not user@ip -- the key is bound to the alias by
# IdentityFile, so the raw form fails with Permission denied (publickey).
#
# WHAT GETS SHIPPED, and why it is not a plain rsync of the directory:
# only files git is TRACKING. ../kalshi-tennis/vps/deploy.sh makes the same
# point by deploying through `git pull` -- "git ships only committed work, so
# `git log` on the box says exactly what is running, and it never touches
# untracked files". This repo has no remote yet, so the same guarantee is had
# by feeding `git ls-files` to rsync: uncommitted edits, capture, logs, the
# venv and the keys cannot ride along by accident. The commit sha is written
# to the box as DEPLOYED_SHA so it can still answer "what is running?".
#
# If you create a private GitHub repo for this, switch to the kalshi-tennis
# model (git push here, git pull there) -- it is strictly better, because the
# box can then be audited without trusting this script.
#
# THE BOX NEEDS NO VENV. requirements.txt is pinned to what it already has
# system-wide (Python 3.14.4, websockets 17.0.1, cryptography 46.0.5), which is
# the whole reason the laptop develops against those versions.
set -euo pipefail

LIVE=0
CODE_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --live) LIVE=1; shift ;;
    --code-only) CODE_ONLY=1; shift ;;
    -*) echo "unknown flag $1" >&2; exit 2 ;;
    *) break ;;
  esac
done
HOST="${1:?usage: deploy.sh [--live] [--code-only] <ssh-alias>}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DIR=kalshi-us-open
CAP="${CAP:-150}"

cd "$SRC"
if [ -n "$(git status --porcelain)" ]; then
  echo "working tree is dirty. Commit first -- the box should run a named" >&2
  echo "commit, not whatever happened to be on disk:" >&2
  git status --short >&2
  exit 1
fi
SHA="$(git rev-parse --short HEAD)"

echo "==> shipping $(git ls-files | wc -l) tracked files at $SHA"
ssh "$HOST" "mkdir -p $DIR/data/live"
git ls-files -z | rsync -az --files-from=- --from0 ./ "$HOST:$DIR/"
ssh "$HOST" "echo $SHA > $DIR/DEPLOYED_SHA"

if [ "$CODE_ONLY" = 0 ]; then
  # The read-only key is all paper needs: it makes no authenticated call at
  # all, and the websocket handshake is signed with the read-only key.
  echo "==> shipping read-only key"
  rsync -az read_only_api_key.rsa read_only_key_id "$HOST:$DIR/"
  ssh "$HOST" "chmod 600 $DIR/read_only_api_key.rsa"

  if [ "$LIVE" = 1 ]; then
    echo "==> shipping TRADING key (this box can now place orders)"
    rsync -az trade_api_key.rsa trade_api_id.txt "$HOST:$DIR/"
    ssh "$HOST" "chmod 600 $DIR/trade_api_key.rsa"
  fi

  # Seed the caps only if they are not already there. Overwriting would silently
  # revert a cap the account was raised to, and the config is edited in place
  # while the bot runs.
  ssh "$HOST" "test -f $DIR/data/live/winner_taker.json || \
    printf '{\n  \"hard_cap\": %s,\n  \"enabled\": true\n}\n' '$CAP' \
      > $DIR/data/live/winner_taker.json"

  echo "==> installing units"
  U=$(ssh "$HOST" 'echo $USER'); H=$(ssh "$HOST" 'echo $HOME')
  for unit in winner-taker-paper winner-taker-live; do
    [ "$unit" = winner-taker-live ] && [ "$LIVE" = 0 ] && continue
    sed -e "s|@USER@|$U|g" -e "s|@HOME@|$H|g" "vps/$unit.service" \
      | ssh "$HOST" "cat > /tmp/$unit.service && \
          sudo mv /tmp/$unit.service /etc/systemd/system/$unit.service"
  done
  ssh "$HOST" "sudo systemctl daemon-reload"
fi

echo "==> checking the box can actually run it"
ssh "$HOST" "cd $DIR && python3 -c '
import sys, websockets, cryptography
print(\"  python\", sys.version.split()[0], \"websockets\", websockets.__version__,
      \"cryptography\", cryptography.__version__)
import winner_taker, discovery, kalshi
print(\"  imports OK, deployed\", open(\"DEPLOYED_SHA\").read().strip())
'"

# Shipping the files is not deploying them: the unit goes on running whatever
# it imported at start. Measured 2026-09-04 -- a --code-only deploy reported
# success while the box kept running a two-day-old discovery.py, because
# nothing here ever restarted it.
#
# PAPER restarts on its own account: it holds nothing and risks nothing, and
# the cost of it being down is a hole in the observation log. LIVE never does.
# It is Restart=no on purpose, it may be holding open legs, and a deploy script
# is the wrong thing to decide that its positions should change hands.
if ssh "$HOST" 'systemctl is-active --quiet winner-taker-paper'; then
  echo "==> restarting paper onto $SHA"
  ssh "$HOST" 'sudo systemctl restart winner-taker-paper'
fi
if ssh "$HOST" 'systemctl is-active --quiet winner-taker-live'; then
  echo "==> LIVE is running and was NOT restarted -- it is still on its old"
  echo "    code. It may be holding legs, so restart it deliberately:"
  echo "      ssh $HOST 'sudo systemctl restart winner-taker-live'"
fi

RUNNING=$(ssh "$HOST" 'systemctl is-active winner-taker-paper winner-taker-live 2>/dev/null | tr "\n" " "' || true)
echo "==> units: $RUNNING"
cat <<EOF

next:
  ssh $HOST 'sudo systemctl enable --now winner-taker-paper'
  ssh $HOST 'journalctl -u winner-taker-paper -f'
  ssh $HOST 'tail -f $DIR/data/live/winner_taker_obs_*.csv'
EOF
