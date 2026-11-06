#!/usr/bin/env bash
# Promote the checked-out branch tip on the Pi and fail unless /healthz answers.
#
# PI_HOST  SSH alias that logs in as the pm2 daemon's user (default: pi)
# PI_REPO  checkout path, relative to that user's home (default: spotify-to-led)
#
# The remote part runs in a login shell, because a non-interactive SSH session
# may not have uv or pm2 on PATH. Never run it under sudo: pm2 commands talk to
# the daemon of the user who runs them.
set -euo pipefail

PI_HOST="${PI_HOST:-pi}"
PI_REPO="${PI_REPO:-spotify-to-led}"

ssh "$PI_HOST" bash -l -s -- "$PI_REPO" <<'REMOTE'
set -euo pipefail
cd "$HOME/$1"
git pull --ff-only
uv sync --frozen --no-dev
pm2 startOrReload ecosystem.config.js --update-env
pm2 save
for _ in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8000/healthz; then
    echo
    exit 0
  fi
  sleep 1
done
echo "healthz did not answer within 20 s" >&2
exit 1
REMOTE
