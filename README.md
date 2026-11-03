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
