# pm2-Supervised Service and Persistent Logs Implementation Plan

## Overview

Run spotify-to-led under the pm2 daemon that already supervises `cloudflared` and n8n on the Pi, instead of the systemd unit F-02 was originally scoped around. The service has to survive a cold boot with no human action (FR-014), must never be permanently given up on, and its logs must survive a reboot so the 7-day NFR-02 run produces evidence. The supervisor decision is re-made against the host as it actually is (`frame.md`), and the foundation docs that recorded systemd are superseded.

## Current State Analysis

- The app is one FastAPI process. `main.py:36-46` swallows every board failure and keeps serving, so a BLE fault never exits the process. The only way it crash-loops is a startup failure, e.g. a `Settings()` validation error (`config.py:44-75`), which raises inside `lifespan` (`main.py:52`) and ends uvicorn.
- Config enters through `Settings`, which reads `.env` relative to the working directory (`config.py:23-28`). F-03 put the Pi's real config in `/etc/spotify-to-led/env` (mode 0600, owned by the service user) on the assumption that a systemd `EnvironmentFile=` would load it (`archive/2026-11-05-edge-surface-and-spotify-app-registration/verification.md`, Phase 3). pm2 has no env-file directive, and pydantic-settings silently ignores an `env_file` that does not exist.
- On the Pi (verified 2026-11-06, `frame.md`): pm2 v7.0.3 runs as `pi-n8n`, booted by `pm2-pi-n8n.service` (enabled, `pm2 resurrect` from the saved dump). `pi-n8n` is in `bluetooth`, but the live daemon (started Sep 27) lacks gid 103, and children inherit the daemon's groups. journald is already persistent by default config. `pm2-logrotate` is absent and `cloudflared-error.log` has grown to 28 MB.
- pm2 defaults that matter: `kill_timeout` 1600 ms (SIGINT, then SIGKILL); `max_restarts` 16 consecutive restarts under `min_uptime` before the app is marked `errored` and abandoned. `exp_backoff_restart_delay` grows the delay to a 15 s ceiling and resets it after 30 s of stable uptime (pm2 docs, restart-strategies). The docs do not say whether `max_restarts` still applies with backoff on, so the plan proves it on the Pi.
- Docs still describe systemd: `infrastructure.md` (frontmatter `:4`, Recommendation `:15-19`, scoring `:44-68`, BLE traps `:92`, Operational Story `:98-102`, risk rows `:108-109`, `:112`, `:118`, Getting Started steps 4-5 `:132-133`), `tech-stack.md:31`, roadmap F-02 (`:72`, `:151-174`, `:398`) and the observability/deploy snapshot (`:111-117`). Code comments: `main.py:28`, `main.py:68-70`, `main.py:89-90`, `config.py:3`.
- No `scripts/` directory and no ecosystem file exist. `.env` is gitignored. Node 24 is on the development machine, so `node --check` is available for the ecosystem file.

## Desired End State

After a power cut, the Pi comes back and `pm2 ls` shows `spotify-to-led` online next to `cloudflared` and n8n, with nobody touching it. A startup crash loop slows to 15 s retries but never reaches `errored`. `~/.pm2/logs/spotify-to-led-out.log` still holds the lines from before the reboot, rotated and bounded by pm2-logrotate. One command from the Windows PC (`scripts/deploy.sh`) pulls, syncs, reloads and exits non-zero if `/healthz` does not answer. The foundation docs name pm2 as the supervisor and explain why the earlier systemd decision was reversed.

### Key Discoveries:

- `main.py:36-46`: board failures never exit the process, so pm2's restart limit only matters for startup crashes (`frame.md`, hypothesis 2).
- `config.py:23-28`: `env_file=".env"` is relative to the working directory. Instantiation-time `_env_file` overrides it, and a missing file is ignored silently, so a typo in the path would silently fall back to the stub transport.
- `pm2-pi-n8n.service` runs `pm2 resurrect`: anything not captured by `pm2 save` is gone after a reboot.
- The existing supersede pattern in foundation docs is an inline note, `Superseded, <date>: ...` (`infrastructure.md:134`, `tech-stack.md:31`). Follow it rather than rewriting the history.

## What We're NOT Doing

- No systemd unit for the app, no journald config change (`Storage=` is already effectively persistent).
- No in-app BLE reconnect or adapter-readiness retry. Getting the board back after a cold boot is S-02/S-03 (FR-013).
- No status page, Spotify code or new routes. `/healthz` stays as is.
- No change to how `cloudflared` or n8n are configured, beyond the shared daemon restart and the shared log rotation they now get.
- No CI workflow and no rollback automation beyond what `deploy.sh` does.
- No rewriting of `frame.md` or archived changes: they keep the old change id as history.

## Implementation Approach

Decide on paper first, then build what the Pi needs in the repo, then roll out on the Pi in an order that makes every host-wide action reversible and observed, then write down what was observed. The only code change is the env-file selector in `Settings`. Everything else is a declarative pm2 file, a shell script and docs.

## Critical Implementation Details

- **Ordering on the Pi.** Run `pm2 save` before restarting the daemon, so the dump reflects the current `cloudflared`/n8n state. Run it again after starting the app and after deleting the crash-loop probe, or the next reboot resurrects the wrong set.
- **Output buffering.** Python block-buffers stdout when it is not a terminal. Without `PYTHONUNBUFFERED=1` in the ecosystem `env`, log lines arrive late, and the last lines before a crash or SIGKILL are lost: exactly the lines the 7-day run needs.
- **Stale env in pm2.** pm2 keeps an app's environment from its first start. Reloads must pass `--update-env`, or an edited ecosystem `env` block is ignored.
- **Which daemon.** pm2 commands talk to the daemon of the user who runs them. Deploy and every Phase 3 step run as the daemon's user (`pi-n8n`), never with `sudo pm2`. Non-interactive SSH may not have `uv` or `pm2` on `PATH`, so the deploy script runs its remote part in a login shell.

## Phase 1: Record the decision

### Overview

Make the docs say pm2 before any code depends on it, so the implementation is checked against the right foundation.

### Changes Required:

#### 1. Branch

**Intent**: The branch name follows the renamed change.

**Contract**: `git branch -m feat/systemd-unit-and-persistent-logs feat/pm2-supervised-service` (local only; nothing was pushed).

#### 2. Infrastructure decision

**File**: `context/foundation/infrastructure.md`

**Intent**: Supersede the systemd recommendation with pm2, keeping the original reasoning visible and explaining what changed: F-03 found `cloudflared` running under pm2, so the "two-layer supervision for no gain" objection now points the other way, the kill window is one setting, and the boot race does not apply to a process that never exits on a BLE fault.

**Contract**:
- Frontmatter `recommended_platform` names pm2. Leave `runner_up` as is.
- `Superseded, 2026-11-06:` notes after the Recommendation, under the scoring matrix, under the Pi + pm2 paragraph/shortlist entry, on BLE trap `:92` (the daemon's groups, not the unit's, are what count), on the risk rows `:108`, `:109`, `:112`, `:118` (pm2 equivalents: backoff instead of `StartLimitIntervalSec=0`, pm2 logs plus pm2-logrotate instead of `Storage=persistent`, SSH fallback reads `pm2 logs`), and on Getting Started steps 4-5.
- Rewrite in place (current-state sections, not history): Operational Story **Secrets** (env file selected by `SPOTIFY_TO_LED_ENV_FILE`), **Rollback** (`git checkout <sha> && uv sync --frozen --no-dev && pm2 reload ecosystem.config.js --update-env`), **Logs** (`pm2 logs spotify-to-led`, `pm2 logs spotify-to-led --lines 200 --nostream`, `pm2 show spotify-to-led` for restart counts, files under `~/.pm2/logs/`).
- The "Supersedes a prior note in tech-stack.md" section gets a dated note that the supersession is itself reversed.

#### 3. Tech stack

**File**: `context/foundation/tech-stack.md`

**Intent**: Record that the original pm2 sentence stands again, with a pointer to `infrastructure.md`.

**Contract**: A new `> Superseded, 2026-11-06:` note directly after the 2026-11-03 note at `:31`. Frontmatter unchanged.

#### 4. Roadmap F-02

**File**: `context/foundation/roadmap.md`

**Intent**: The item's outcome stays (boot survival, no permanent give-up, logs that survive a reboot). Only the mechanism and the title change.

**Contract**: At a glance `:72` and the summary row `:398` reworded to pm2. `### F-02:` heading becomes "Supervised service and logs that survive a reboot". Outcome, Unlocks and Risk drop the systemd/journald/start-limiter specifics in favour of the pm2 ones. The Deploy / Observability snapshot lines `:111-117` get a superseded note. The Change ID is already `pm2-supervised-service` and Status is `planning`.

### Success Criteria:

#### Automated Verification:

- Branch is renamed: `git branch --show-current` prints `feat/pm2-supervised-service`
- No foundation doc names the old change id: `git grep -n "systemd-unit-and-persistent-logs" -- context/foundation` prints nothing
- Each superseded spot carries a dated note: `grep -c "Superseded, 2026-11-06" context/foundation/infrastructure.md` is at least 8 and `context/foundation/tech-stack.md` has at least 1

#### Manual Verification:

- Reading `infrastructure.md` top to bottom, a newcomer ends up with pm2 as the supervisor and understands why systemd was reversed, without contradicting statements left unmarked

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 2: Repo artifacts

### Overview

Everything the Pi needs that lives in the repo: the env-file selector, the pm2 declaration, the deploy script, and wording that no longer says journald or systemd.

### Changes Required:

#### 1. Env-file selection

**File**: `src/spotify_to_led/config.py`, `src/spotify_to_led/main.py`

**Intent**: Let the process read `/etc/spotify-to-led/env` without a symlink or a copy, and fail loudly if the named file is missing instead of silently running on defaults (which would drive the stub, not the board).

**Contract**:
- New variable `SPOTIFY_TO_LED_ENV_FILE`, deliberately prefixed so it cannot collide with the unprefixed app keys.
- One loader used by both `Settings()` call sites in `main.py` (`:52`, `:91`): unset reads `.env` exactly as today. Set means read that file instead of `.env`, and a path that does not exist raises at startup with the path in the message.
- Precedence unchanged: process environment overrides the file.
- Module docstring at `config.py:3` no longer says systemd `EnvironmentFile`.

#### 2. Config tests

**File**: `tests/test_config.py`

**Intent**: Pin the selector's behaviour, hermetically like the existing tests.

**Contract**: Add `SPOTIFY_TO_LED_ENV_FILE` to the cleaned keys. Cases: unset keeps the `.env` behaviour; set to a `tmp_path` file reads its values; set to a missing path raises; a process env value beats the file's.

#### 3. pm2 declaration

**File**: `ecosystem.config.js` (new, repo root)

**Intent**: The whole supervision policy in one reviewed file, the pm2 equivalent of the unit file `infrastructure.md` described.

**Contract**: One app, `name: "spotify-to-led"`, `cwd: __dirname`, `script: ".venv/bin/python"`, `args: "-m spotify_to_led.main"`, `interpreter: "none"`. `env`: `PYTHONUNBUFFERED: "1"`, `SPOTIFY_TO_LED_ENV_FILE: "/etc/spotify-to-led/env"`. `kill_timeout: 10000`. `exp_backoff_restart_delay: 100`. `merge_logs: true`, so log file names carry no pid suffix and stay stable across restarts. Default log directory (`~/.pm2/logs`). No `watch`. Host-specific values (`BOARD_TRANSPORT`, `IDOT_MAC`) stay in the env file, not here. If the Phase 3 crash-loop probe ends in `errored`, add a large `max_restarts` here and repeat the probe (see Phase 3).

#### 4. Deploy script

**File**: `scripts/deploy.sh` (new)

**Intent**: One command from the Windows PC (Git Bash) that promotes `main`-tip code to the Pi and reports success with a real exit code.

**Contract**: `set -euo pipefail`. `PI_HOST` (default `pi`, an SSH alias that logs in as the pm2 daemon's user) and `PI_REPO` (default `spotify-to-led`, relative to that user's home). Remote steps run in a login shell, in order: `git pull --ff-only`, `uv sync --frozen --no-dev`, `pm2 startOrReload ecosystem.config.js --update-env`, `pm2 save`, then poll `curl -fsS http://127.0.0.1:8000/healthz` for up to ~20 s. Any failing step makes the script exit non-zero. No real hostnames in the file.

#### 5. Wording

**File**: `src/spotify_to_led/main.py`, `.env.example`, `README.md`

**Intent**: Comments and docs say what actually captures the logs and loads the config.

**Contract**: `main.py:28` "so journald captures it" becomes "so pm2's log files capture it". `main.py:68-70` "the exact journal" becomes the log the unattended run is read from. `main.py:89-90` refers to `ecosystem.config.js`. The `.env.example` header says the Pi's file is selected by `SPOTIFY_TO_LED_ENV_FILE` and must also carry `BOARD_TRANSPORT=idotmatrix` and `IDOT_MAC`. A new README `## Deploy` section covers `scripts/deploy.sh`, its two variables, and the pm2 log commands.

### Success Criteria:

#### Automated Verification:

- Test suite passes, including the new selector cases: `uv run pytest`
- Ecosystem file parses: `node --check ecosystem.config.js`
- Ecosystem file loads with the agreed policy: `node -e "const a=require('./ecosystem.config.js').apps[0]; if(a.kill_timeout!==10000||a.exp_backoff_restart_delay!==100||a.interpreter!=='none'||a.env.PYTHONUNBUFFERED!=='1') process.exit(1)"`
- Deploy script parses: `bash -n scripts/deploy.sh`
- No journald or EnvironmentFile wording left in code: `git grep -nE "journald|EnvironmentFile|unit file" -- src` prints nothing

#### Manual Verification:

- Locally, `SPOTIFY_TO_LED_ENV_FILE=does-not-exist uv run python -m spotify_to_led.main` fails at startup with a message that names the path

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 3: Pi rollout

### Overview

Bring the app up under pm2 on the Pi, with every host-wide action observed right away, then prove the three requirements: no permanent give-up, clean stop, and survival of a real reboot with logs intact. All steps run as `pi-n8n` unless `sudo` is shown. The Phase 2 branch must be on `origin` (or merged) so the Pi can pull it.

### Changes Required:

#### 1. Preconditions

**Intent**: Catch permission and config gaps before anything is restarted.

**Contract**: `id -nG pi-n8n` lists `bluetooth`. `sudo stat -c '%a %U' /etc/spotify-to-led/env` is `600 pi-n8n` (the daemon's user must own it, or it cannot read it). The env file gains `BOARD_TRANSPORT=idotmatrix` and `IDOT_MAC=<board address>`. The checkout exists at `~/spotify-to-led` and `uv sync --frozen --no-dev` succeeds there.

#### 2. Daemon restart for the bluetooth group

**Intent**: The live daemon lacks gid 103, so it is restarted once, with `cloudflared` and n8n coming back from the saved dump.

**Contract**: `pm2 save`, then `sudo systemctl restart pm2-pi-n8n`. Afterwards the daemon's `/proc/<pid>/status` `Groups:` line includes 103, and `cloudflared` and n8n are `online`.

#### 3. Log rotation

**Intent**: Bound every pm2 log on the SD card while keeping well over 7 days of history for this app.

**Contract**: `pm2 install pm2-logrotate`, then `pm2 set pm2-logrotate:max_size 10M`, `pm2 set pm2-logrotate:retain 14`, `pm2 set pm2-logrotate:compress false`, leaving the default daily `rotateInterval`. These settings apply to `cloudflared` and n8n too.

#### 4. First deploy and policy checks

**Intent**: Start the app through the same path every later slice uses, then prove the restart and stop policies.

**Contract**:
- From the Windows PC: `PI_HOST=<alias> scripts/deploy.sh` exits 0.
- Crash-loop probe: `pm2 start /bin/false --name crashloop --interpreter none --exp-backoff-restart-delay=100`, wait at least 3 minutes, check `pm2 jlist` shows `restart_time` above 16 and a status other than `errored`. Then `pm2 delete crashloop` and `pm2 save`. If it went `errored`: add a large `max_restarts` to `ecosystem.config.js` and to the probe, redeploy, and repeat the probe.
- Clean stop: `pm2 restart spotify-to-led`. The app log shows uvicorn's normal shutdown lines, and the pm2 log shows no SIGKILL for it.

#### 5. Real reboot

**Intent**: The only honest proof of FR-014 and of logs surviving a reboot.

**Contract**: `sudo reboot`, touch nothing, then check: `pm2 ls` shows all three apps `online`; `curl -s http://127.0.0.1:8000/healthz` is `{"status":"ok"}`; `~/.pm2/logs/spotify-to-led-out.log` still holds pre-reboot lines; `journalctl --list-boots` lists at least 2 boots.

### Success Criteria:

#### Manual Verification:

- Preconditions hold: `bluetooth` group, env file `600 pi-n8n` with `BOARD_TRANSPORT` and `IDOT_MAC`, `uv sync` succeeds in the checkout
- After the daemon restart its `Groups:` include 103, and `cloudflared` and n8n are online with both public hostnames still answering through Access
- pm2-logrotate is installed with `max_size 10M`, `retain 14`, `compress false`
- `scripts/deploy.sh` exits 0 from the Windows PC, `pm2 ls` shows `spotify-to-led` online, and the board shows the test pattern
- App log lines appear in `~/.pm2/logs/spotify-to-led-out.log` within a second of being written (unbuffered)
- The crash-loop probe passes 16 restarts without reaching `errored`, then is deleted and the dump re-saved
- `pm2 restart spotify-to-led` shuts down cleanly inside `kill_timeout`, with no SIGKILL
- After `sudo reboot` with no human action: all three apps online, `/healthz` ok, pre-reboot app log lines present, `journalctl --list-boots` shows at least 2 boots

**Implementation Note**: This phase is all on-device. Record each result as it happens; Phase 4 writes them up.

---

## Phase 4: Record the outcome

### Overview

Write what the Pi actually showed, in the same shape F-03 used, and correct any foundation detail that only the rollout could settle.

### Changes Required:

#### 1. Verification record

**File**: `context/changes/pm2-supervised-service/verification.md` (new)

**Intent**: The evidence for this change, readable without the conversation.

**Contract**: Same conventions as `archive/2026-11-05-edge-surface-and-spotify-app-registration/verification.md`: placeholders for every real value (`<sub>.<owner-domain>`, `<board address>`, `<alias>`), one `## Phase N` section per rollout step with a Check / Result table, and whether the crash-loop contingency (`max_restarts`) was needed.

#### 2. Foundation touch-ups

**File**: `context/foundation/infrastructure.md`

**Intent**: The Operational Story commands match what was really run on the Pi.

**Contract**: Edit only lines Phase 3 proved wrong or incomplete. If nothing differed, leave the file untouched.

### Success Criteria:

#### Automated Verification:

- `verification.md` exists with a section per Phase 3 step: `grep -c "^## Phase" context/changes/pm2-supervised-service/verification.md` is at least 5
- No real MAC address in the change folder: `git grep -nE "([0-9A-F]{2}:){5}[0-9A-F]{2}" -- context/changes/pm2-supervised-service` prints nothing

#### Manual Verification:

- No real hostname, team domain or LAN address in the diff (`git diff main` read by the owner)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding.

---

## Testing Strategy

### Unit Tests:

- `Settings` env-file selection: unset, set to an existing file, set to a missing path, process env beats the file.

### Integration Tests:

- None automated. pm2 behaviour is proven on the Pi (Phase 3), which is the only place it can be.

### Manual Testing Steps:

1. Missing env-file path fails at startup with the path in the message (local).
2. Crash-loop probe on the Pi passes 16 restarts without `errored`.
3. `pm2 restart` stops cleanly within `kill_timeout`.
4. A real `sudo reboot`: the app comes back unattended and its pre-reboot log lines are still there.

## Performance Considerations

None at runtime. pm2-logrotate bounds disk use: with `max_size 10M` and `retain 14`, worst case is about 150 MB per log file across the host, well within the SD card.

## Migration Notes

Rollback of the supervisor change: `pm2 delete spotify-to-led && pm2 save`. The daemon restart and pm2-logrotate are independent of the app; `pm2 uninstall pm2-logrotate` reverts the rotation if it misbehaves for `cloudflared` or n8n.

## References

- Frame brief: `context/changes/pm2-supervised-service/frame.md`
- Code: `src/spotify_to_led/main.py:27-46`, `:49-71`, `:88-92`; `src/spotify_to_led/config.py:1-28`
- Decision superseded: `context/foundation/infrastructure.md:4`, `:13-68`, `:92`, `:98-102`, `:108-118`, `:132-133`; `context/foundation/tech-stack.md:31`
- Roadmap: `context/foundation/roadmap.md:72`, `:111-117`, `:151-174`, `:398`
- Premise-changing finding: `context/archive/2026-11-05-edge-surface-and-spotify-app-registration/verification.md`
- pm2 docs (Context7 `/websites/pm2_keymetrics_io`): application-declaration (`kill_timeout`, `max_restarts`, `min_uptime`, log files), restart-strategies (`exp_backoff_restart_delay`)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` -- <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Record the decision

#### Automated

- [x] 1.1 Branch is renamed: `git branch --show-current` prints `feat/pm2-supervised-service` -- 381fcbd
- [x] 1.2 No foundation doc names the old change id: `git grep -n "systemd-unit-and-persistent-logs" -- context/foundation` prints nothing -- 381fcbd
- [x] 1.3 Each superseded spot carries a dated note: `grep -c "Superseded, 2026-11-06" context/foundation/infrastructure.md` is at least 8 and `context/foundation/tech-stack.md` has at least 1 -- 381fcbd

#### Manual

- [x] 1.4 Reading `infrastructure.md` top to bottom, a newcomer ends up with pm2 as the supervisor and understands why systemd was reversed, without contradicting statements left unmarked -- 381fcbd

### Phase 2: Repo artifacts

#### Automated

- [x] 2.1 Test suite passes, including the new selector cases: `uv run pytest`
- [x] 2.2 Ecosystem file parses: `node --check ecosystem.config.js`
- [x] 2.3 Ecosystem file loads with the agreed policy
- [x] 2.4 Deploy script parses: `bash -n scripts/deploy.sh`
- [x] 2.5 No journald or EnvironmentFile wording left in code: `git grep -nE "journald|EnvironmentFile|unit file" -- src` prints nothing

#### Manual

- [x] 2.6 Locally, `SPOTIFY_TO_LED_ENV_FILE=does-not-exist uv run python -m spotify_to_led.main` fails at startup with a message that names the path

### Phase 3: Pi rollout

#### Manual

- [ ] 3.1 Preconditions hold: `bluetooth` group, env file `600 pi-n8n` with `BOARD_TRANSPORT` and `IDOT_MAC`, `uv sync` succeeds in the checkout
- [ ] 3.2 After the daemon restart its `Groups:` include 103, and `cloudflared` and n8n are online with both public hostnames still answering through Access
- [ ] 3.3 pm2-logrotate is installed with `max_size 10M`, `retain 14`, `compress false`
- [ ] 3.4 `scripts/deploy.sh` exits 0 from the Windows PC, `pm2 ls` shows `spotify-to-led` online, and the board shows the test pattern
- [ ] 3.5 App log lines appear in `~/.pm2/logs/spotify-to-led-out.log` within a second of being written (unbuffered)
- [ ] 3.6 The crash-loop probe passes 16 restarts without reaching `errored`, then is deleted and the dump re-saved
- [ ] 3.7 `pm2 restart spotify-to-led` shuts down cleanly inside `kill_timeout`, with no SIGKILL
- [ ] 3.8 After `sudo reboot` with no human action: all three apps online, `/healthz` ok, pre-reboot app log lines present, `journalctl --list-boots` shows at least 2 boots

### Phase 4: Record the outcome

#### Automated

- [ ] 4.1 `verification.md` exists with a section per Phase 3 step: `grep -c "^## Phase" context/changes/pm2-supervised-service/verification.md` is at least 5
- [ ] 4.2 No real MAC address in the change folder: `git grep -nE "([0-9A-F]{2}:){5}[0-9A-F]{2}" -- context/changes/pm2-supervised-service` prints nothing

#### Manual

- [ ] 4.3 No real hostname, team domain or LAN address in the diff (`git diff main` read by the owner)
