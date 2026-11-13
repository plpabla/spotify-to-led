---
starter_id: fastapi
package_manager: uv
project_name: spotify-to-led
hints:
  language_family: python
  team_size: solo
  deployment_target: self-host
  ci_provider: github-actions
  ci_default_flow: manual-promotion
  bootstrapper_confidence: first-class
  path_taken: custom
  quality_override: false
  self_check_answers:
    typed: true
    from_official_starter: false
    conventions: true
    docs_current: true
    can_judge_agent: true
  has_auth: true
  has_payments: false
  has_realtime: false
  has_ai: false
  has_background_jobs: true
---

## Why this stack

A solo owner building a single-tenant background service for a Raspberry Pi in 3 after-hours weeks. The registry has no default for this product type, so the custom path ran with Python chosen because the iDotMatrix, Bluetooth and Spotify libraries are Python and asyncio-based. FastAPI passes all four quality gates and runs the Spotify reader, display-state logic and Bluetooth reconnect loop as background tasks beside a tiny web server that serves one static page with vanilla JS and the Spotify re-authorization route. The scaffold is the minimal uv recipe (uv init, uv add fastapi uvicorn) with the standard app package layout, because the only official template (full-stack) brings Postgres, React and login that the PRD rules out. Deployment is self-host on the Pi, run through uv under pm2 with pm2 startup for reboot survival. CI is GitHub Actions for checks only, with manual promotion to the Pi.

> Superseded, 2026-11-03: the pm2 sentence above was a proposal, chosen because pm2 was already installed on the Pi, not a researched decision. @context/foundation/infrastructure.md supersedes it -- the supervisor is a systemd system unit. Self-host on the Pi, uv, GitHub Actions checks-only and manual promotion are all unchanged.

> Superseded, 2026-11-06: the pm2 sentence above stands again. F-03 found `cloudflared` already running under pm2 on the Pi, so the systemd recommendation's premise no longer held, and @context/foundation/infrastructure.md now records the Pi's existing pm2 daemon as the supervisor, with `ecosystem.config.js` as the declaration and pm2-logrotate for the logs. Frontmatter is unchanged.

> Recorded, 2026-11-04: the test runner is pytest with pytest-asyncio, configured in `pyproject.toml` under `[tool.pytest.ini_options]` with `asyncio_mode = "auto"` so async tests need no per-test decorator, and `testpaths = ["tests"]`. This is the project's first and only `[tool.*]` section; no linter, formatter or type checker is configured, and `typed: true` above remains a self-check on annotating new code rather than a checker in CI.

> Recorded, 2026-11-04: a GPL-3.0-or-later runtime dependency is accepted. The board library is `idotmatrix-api-client` 0.3.0, pinned exactly, and it is licensed GPL-3.0-or-later. This is compatible because the project is already GPLv3 itself: `LICENSE` at the repository root is the GNU GPL version 3, and the repository is public at `github.com/plpabla/spotify-to-led`, so the source is distributed and the copyleft terms already apply to it. The deployment shape is not what makes this safe -- the shared licence is. Revisit this acceptance if the project is ever relicensed to permissive terms or the board library is vendored into a differently-licensed work, not merely when distribution becomes a goal, because distribution is already happening.

> Recorded, 2026-11-04: `httpx2` is a dev dependency, required by `fastapi.testclient.TestClient`. Starlette 1.10 renamed its test-client dependency from `httpx` to `httpx2`, so the older name does not satisfy the import and the test client raises on construction without it. It sits in the `dev` group beside pytest as `httpx2>=2.16.0`; nothing in the runtime dependencies uses it.

> Recorded, 2026-11-08: `httpx2` is now a runtime dependency, `httpx2>=2.16.0` under `[project].dependencies`, because the Spotify client is raw HTTP over `httpx2.AsyncClient` rather than a Spotify library (spotipy is synchronous; tekore's auto-refresh is sync-only and it pins the old `httpx<0.29`). This supersedes the 2026-11-04 note's "nothing in the runtime dependencies uses it". It had to move: `scripts/deploy.sh` syncs with `--no-dev`, so a dev-only `httpx2` would be absent on the Pi. The test client still uses it too.

> Recorded, 2026-11-11: AI-enhanced covers (FR-019, S-08) call OpenRouter's Images API (`POST https://openrouter.ai/api/v1/images`) as raw HTTP over the same `httpx2.AsyncClient`, in `src/spotify_to_led/enhance.py`, with no SDK and no new dependency. The model is a setting (`OPENROUTER_MODEL`, default `black-forest-labs/flux-3-image-klein`); the prompt is a constant in that module. OpenRouter was chosen over a model on the Pi: a hosted call took about 9 s and $0.009 per cover in the board bake-off, and the Pi has no accelerator. Updated 2026-11-13 (select-model): `OPENROUTER_MODEL` is gone. The models on offer are listed in `models.txt` at the repo root (one OpenRouter id per line, `#` comments, the first id the default; `MODEL_LIST_FILE` names another file), read by `src/spotify_to_led/models.py`, and the owner picks one on the status page; the choice is saved in `var/settings.json`. A missing or empty list is warned about and acts as `black-forest-labs/flux-3-image-klein`.
