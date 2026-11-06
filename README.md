# spotify-to-led

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```
uv sync
```

This creates `.venv/` and installs the dependencies. No manual venv step or activation is needed.

## Run

```
uv run python -m spotify_to_led.main
```

`uv run` uses `.venv/` automatically. To activate it by hand instead (Git Bash: `source .venv/Scripts/activate`, PowerShell: `.venv\Scripts\Activate.ps1`), then run `python -m spotify_to_led.main`.

## Deploy

On the Pi the service runs under pm2, declared in `ecosystem.config.js`. From the development machine (Git Bash):

```
PI_HOST=<alias> scripts/deploy.sh
```

- `PI_HOST` is an SSH alias that logs in as the pm2 daemon's user (default `pi`). Never deploy with `sudo pm2`: pm2 commands talk to the daemon of the user who runs them.
- `PI_REPO` is the checkout path, relative to that user's home (default `spotify-to-led`).

The script pulls with `--ff-only`, runs `uv sync --frozen --no-dev`, reloads the app with `--update-env`, saves the pm2 dump, and exits non-zero unless `/healthz` answers within about 20 seconds.

Logs, on the Pi:

```
pm2 logs spotify-to-led                          # follow
pm2 logs spotify-to-led --lines 200 --nostream   # last 200 lines
pm2 show spotify-to-led                          # status and restart count
```

The files are `~/.pm2/logs/spotify-to-led-out.log` and `spotify-to-led-error.log`, rotated by pm2-logrotate.
