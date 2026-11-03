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
