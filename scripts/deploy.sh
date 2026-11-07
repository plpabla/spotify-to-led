#!/usr/bin/env bash
# Pull the branch the Pi's checkout tracks, reload it under pm2, and fail unless
# /healthz answers.
#
# PI_HOST  SSH alias that logs in as the pm2 daemon's user (default: pi)
# PI_REPO  checkout path, relative to that user's home (default: spotify-to-led)
#
# The remote part sources ~/.profile, because a non-interactive SSH session may
# not have uv or pm2 on PATH. It deliberately avoids `bash -l`: a login shell
# runs ~/.bash_logout on exit, and Debian's fails without a tty, turning a
# successful deploy into exit 1. Never run it under sudo: pm2 commands talk to
# the daemon of the user who runs them.
set -euo pipefail

PI_HOST="${PI_HOST:-pi}"
PI_REPO="${PI_REPO:-spotify-to-led}"

# ssh re-parses its remote arguments in the Pi's shell, so PI_REPO is quoted for
# that shell too, and `--` keeps a PI_HOST starting with `-` from being an option.
ssh -- "$PI_HOST" bash -s -- "$(printf %q "$PI_REPO")" <<'REMOTE'
. "$HOME/.profile"
set -euo pipefail
if ! command -v uv >/dev/null || ! command -v pm2 >/dev/null; then
  echo "uv or pm2 is not on PATH after ~/.profile; see infrastructure.md Getting Started step 4" >&2
  exit 1
fi
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
