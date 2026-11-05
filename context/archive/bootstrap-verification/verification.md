---
starter_id: fastapi
project_name: spotify-to-led
package_manager: uv
cwd_strategy: native-cwd
phase_3_status: ok
date: 2026-11-03
---

## Hand-off

Single-tenant background service for a Raspberry Pi. Python/FastAPI chosen because the iDotMatrix, Bluetooth and Spotify libraries are Python and asyncio-based. Minimal uv recipe, self-host on the Pi under pm2, GitHub Actions for checks only with manual promotion.

Hints: language python, team solo, deployment self-host, ci github-actions (manual-promotion), confidence first-class, path_taken custom, quality_override false. Self-check: typed, conventions, docs_current, can_judge_agent all true; from_official_starter false.
Feature flags true: has_auth, has_background_jobs.

## Pre-scaffold verification

Package check skipped (uv recipe, no create-* CLI). GitHub fastapi/fastapi last pushed 2026-11-02: fresh.

## Scaffold log

Command: `uv init . && uv add fastapi uvicorn` (exit 0), run directly in the current directory.
Files written: main.py, pyproject.toml, README.md, .python-version, uv.lock, .venv/. Pre-existing files (CLAUDE.md, .gitignore, context/) untouched.
Installed: fastapi 0.162.0, uvicorn 0.54.0 plus 12 transitive packages. CPython 3.14.0.
Notes: uv named the project `20-led-board` (directory name), not `spotify-to-led`. Root `.gitignore` is unchanged and lacks Python entries (.venv has its own ignore file; add `__pycache__/` as needed).

## Post-scaffold audit

`pip-audit` was not installed; ran `uvx pip-audit` on `uv export` of the lockfile. 14 packages, 0 known vulnerabilities (0 critical, 0 high, 0 moderate, 0 low).

## Hints recorded but not acted on

bootstrapper_confidence, path_taken, self_check_answers, team_size, deployment_target, ci_provider, ci_default_flow, has_auth, has_background_jobs. No CI or app-layout scaffolding in v1; the hand-off mentions an app package layout that still needs creating.

## Next steps

- Rename the project in pyproject.toml if `spotify-to-led` is wanted.
- Create the app package and replace the placeholder main.py.
- Add Python entries to .gitignore.
