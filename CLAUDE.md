# Repository Guidelines

`spotify-to-led` is a single-tenant Python 3.14 / FastAPI background service that mirrors the artwork of the currently-playing Spotify item onto an iDotMatrix LED board over Bluetooth, running on a Raspberry Pi. `src/spotify_to_led/main.py` is the real entrypoint: a FastAPI `app` whose lifespan builds a board transport from settings and, when `SPOTIFY_CLIENT_ID` is set, runs the Spotify reader (`src/spotify_to_led/spotify/`) as a supervised task that polls currently-playing every 3 s. A second supervised task, the display loop (`src/spotify_to_led/display.py`), connects to the board and reconnects with backoff whenever the link is down, and shows the playing item's cover from the reader's state, pushing when that choice changes or the board link comes up again. Covers are picked, downloaded and rendered (`render_cover`, a plain area downscale) off the loop by a `CoverLoader` (`src/spotify_to_led/covers.py`); every rendered cover is kept in `var/covers/<board size>/<image-id>.png` by a `FrameCache` (`src/spotify_to_led/frame_cache.py`), so a cover seen before, even before a restart, needs no download; a cover gets a corner staleness marker once reads have failed for more than 30 s. An episode without images of its own uses its show's. An item with no artwork at all (an ad, a local file) shows the generic no-artwork symbol, a grey vinyl disc; the playing placeholder stands in while an item's cover is still downloading or failed to download, and idle shows when nothing is playing or Spotify is not configured. It serves `GET /healthz`, `/authorize` and `/callback` (PKCE authorization), and a temporary JSON `/playback` that S-07 replaces. The token set lives in `var/spotify-token.json`, not in the env file. The blind-with-no-cover state and the status page are not built yet.

## Hard rules

From @context/foundation/prd.md. A diff that breaks one of these is wrong even if it runs.

- **Read-only against Spotify.** No play, pause, skip or volume, and no write scopes in the OAuth request.
- **Never present a cover the service cannot vouch for** without a visible staleness marker (FR-018). Stale is allowed; stale-and-unlabelled is the regression.
- **No auth in the application.** No roles, no sign-up, no login, no gated routes in service code. The only gate is a Cloudflare Access policy at the edge.
- **Spotify is the only source**, and v1 draws one static frame per item: no animation, no AI-enhanced covers.

## Project structure

- `src/spotify_to_led/` -- the package (src layout, `uv_build` backend); import path is `spotify_to_led.*`.
- `context/foundation/` -- living cross-change docs (@context/foundation/prd.md, @context/foundation/tech-stack.md). Edit in place; do not create dated copies.
- `context/changes/<change-id>/` -- change-scoped research, plans and reviews. Anything tied to a single change belongs here, never in `foundation/`.
- `context/archive/` -- completed changes, read-only by convention. Do not write here.

## Build and development commands

- `uv sync` -- create `.venv/` and install dependencies. No manual venv or activation step; see @README.md.
- `uv run python -m spotify_to_led.main` -- run the service.
- `uv add <pkg>` -- add a dependency so `uv.lock` stays in sync. `uv.lock` and `.python-version` are tracked deliberately.

## Coding style and tooling

Python >= 3.14; annotate new code, per the `typed: true` self-check in @context/foundation/tech-stack.md. No linter, formatter or type checker is configured -- `pyproject.toml` has no `[tool.*]` section. Do not add one unasked.

## Testing

The test runner is pytest with pytest-asyncio, configured under `[tool.pytest.ini_options]` in `pyproject.toml` with `asyncio_mode = "auto"`, so async tests need no decorator. Run it with `uv run pytest`. The choice is recorded in @context/foundation/tech-stack.md, which is where a future runner or CI change belongs too, rather than only in `pyproject.toml`. No CI workflow exists yet.

## Commits

`origin` is `git@github.com:plpabla/spotify-to-led.git`, a public GitHub repository. `git log` shows short lowercase subjects ("prd and hello-world in place"); match that plain style. Conventional Commit prefixes are not in use.
