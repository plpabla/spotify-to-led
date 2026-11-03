# Repository Guidelines

`spotify-to-led` is a single-tenant Python 3.14 / FastAPI background service that mirrors the artwork of the currently-playing Spotify item onto an iDotMatrix LED board over Bluetooth, running on a Raspberry Pi. It is pre-implementation: `src/spotify_to_led/main.py` is still the uv placeholder.

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

No test suite, framework or CI workflow exists yet. When introducing the first one, record the choice in `context/foundation/` rather than only in `pyproject.toml`.

## Commits

No remote is configured. `git log` shows short lowercase subjects ("prd and hello-world in place"); match that plain style. Conventional Commit prefixes are not in use.
