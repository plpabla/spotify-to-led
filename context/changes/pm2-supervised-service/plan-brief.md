# pm2-Supervised Service and Persistent Logs -- Plan Brief

> Full plan: `context/changes/pm2-supervised-service/plan.md`
> Frame brief: `context/changes/pm2-supervised-service/frame.md`

## What & Why

F-02's supervisor decision rests on a premise F-03 disproved (no production process under pm2 on this Pi), so F-02 must first re-decide the supervisor against the host as it is. The requirements stay fixed: boot survival with no human action (FR-014), a process that cannot be permanently given up on, and logs that survive a reboot for NFR-02. This plan settles on pm2 and delivers those three requirements through it.

## Starting Point

The app is a FastAPI process that never exits on a board fault (`main.py:36-46`) and reads config from `.env` in its working directory. On the Pi, pm2 v7 already runs `cloudflared` and n8n as `pi-n8n` and is boot-started by `pm2-pi-n8n.service`. The daemon predates the `bluetooth` group membership, journald is already persistent, and pm2 logs are unrotated. The foundation docs still say "systemd unit + journald".

## Desired End State

After a power cut the Pi comes back with `spotify-to-led` online in `pm2 ls` next to `cloudflared` and n8n, with no human action. A startup crash loop slows to 15 s retries and never reaches `errored`. The app's pre-reboot log lines are still in `~/.pm2/logs`, bounded by pm2-logrotate. One command from the Windows PC deploys and reports success or failure with an exit code.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Supervisor | pm2, the existing daemon | One place to see and manage everything on a single-owner Pi, and the status page already depends on pm2 surviving reboot. | Frame / Plan |
| Restart policy | `exp_backoff_restart_delay: 100`, proven on the Pi; large `max_restarts` only if the probe shows `errored` | The docs do not say whether backoff lifts the 16-restart limit, so it is measured, not assumed. | Plan |
| Stop window | `kill_timeout: 10000` | pm2's 1.6 s default could cut BLE teardown short; one setting fixes it. | Frame |
| Config loading | `SPOTIFY_TO_LED_ENV_FILE` selects the file `Settings` reads; a missing path fails startup | Keeps F-03's single 0600 file, is testable, and avoids silently running on stub defaults. | Plan |
| Log rotation | Install pm2-logrotate host-wide: `max_size 10M`, `retain 14`, no compression | Bounds SD-card use (also for the 28 MB `cloudflared` log) while keeping more than 7 days. | Plan |
| bluetooth group | One `systemctl restart pm2-pi-n8n`, after `pm2 save` | The live daemon lacks gid 103; bouncing `cloudflared` and n8n once is the accepted cost. | Frame |
| Change id | Renamed to `pm2-supervised-service` (folder, roadmap, branch) | The id should name the mechanism actually used. | Plan |
| Deploy script | In scope: `scripts/deploy.sh` with a `/healthz` check | The restart command is pm2-specific and every later slice redeploys. | Plan |

## Scope

**In scope:**
- Superseding systemd in `infrastructure.md`, `tech-stack.md` and roadmap F-02
- `Settings` env-file selector and tests; `ecosystem.config.js`; `scripts/deploy.sh`; README deploy section; journald/systemd wording in code
- Pi rollout: daemon restart, pm2-logrotate, first deploy, crash-loop probe, clean-stop check, real reboot
- `verification.md` with the observed results

**Out of scope:**
- Any systemd unit or journald config change
- In-app BLE reconnect / adapter readiness (S-02/S-03)
- Status page, Spotify code, new routes, CI

## Architecture / Approach

`pm2-pi-n8n.service` (systemd, already there) resurrects the pm2 daemon. The daemon runs `.venv/bin/python -m spotify_to_led.main` from the checkout, with `PYTHONUNBUFFERED=1` and `SPOTIFY_TO_LED_ENV_FILE=/etc/spotify-to-led/env`, as declared in a committed `ecosystem.config.js`. stdout/stderr go to `~/.pm2/logs/spotify-to-led-{out,error}.log`, rotated by pm2-logrotate. `deploy.sh` runs, over SSH in a login shell: pull, `uv sync`, `pm2 startOrReload --update-env`, `pm2 save`, then polls `/healthz`.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Record the decision | Branch renamed; foundation docs say pm2, with dated supersede notes | Leaving an unmarked systemd statement that contradicts the decision |
| 2. Repo artifacts | Env-file selector + tests, ecosystem file, deploy script, wording | `PYTHONUNBUFFERED` or `--update-env` missed, so logs lag or env is stale |
| 3. Pi rollout | App online under pm2, restart/stop policies proven, reboot survived | Daemon restart bounces `cloudflared`/n8n; a missed `pm2 save` loses the app on reboot |
| 4. Record the outcome | `verification.md`, Operational Story matching reality | Real hostnames or the board MAC leaking into the public repo |

**Prerequisites:** SSH access to the Pi as `pi-n8n` with sudo; the board's address; the Phase 2 branch pushed so the Pi can pull it.
**Estimated effort:** ~2-3 sessions across 4 phases, most of the wall-clock time in Phase 3 (crash-loop wait and reboot).

## Open Risks & Assumptions

- If backoff does not lift pm2's `max_restarts`, the contingency is a large `max_restarts`. The probe decides which one ships.
- Assumes `/etc/spotify-to-led/env` is owned by the daemon's user (`pi-n8n`). Phase 3 checks this first.
- A cold boot can still race BlueZ. The app logs the board failure and keeps serving; getting the board back is S-02/S-03, not a supervisor concern.
- Only one boot is recorded in the journal so far: log persistence across a reboot is configured but first observed in Phase 3.

## Success Criteria (Summary)

- A real `sudo reboot` brings `spotify-to-led` back online with `/healthz` ok and nobody touching the Pi.
- A process that crashes on every start keeps being restarted at most 15 s apart and never ends up `errored`.
- The app's pre-reboot log lines are readable after the reboot, and pm2 logs are bounded on disk.
