---
project: spotify-to-led
researched_at: 2026-11-03
recommended_platform: Raspberry Pi (self-hosted) + systemd system unit + uv-managed Python 3.14
runner_up: Raspberry Pi + Docker Compose
context_type: mvp
tech_stack:
  language: Python >=3.14
  framework: FastAPI + uvicorn
  runtime: uv-managed CPython (python-build-standalone, aarch64-unknown-linux-gnu)
---

## Recommendation

**Deploy on the Raspberry Pi itself, supervised by a systemd system unit, with the interpreter and venv managed by `uv`. Promote by hand over SSH, wrapped in a one-command script runnable from the Windows PC.**

This is not a choice between hosting providers, because the project does not have that choice. The PRD states the constraint as permanent: "Because Bluetooth requires physical proximity, this service can never be centralized or hosted -- it must always run next to its own board." FR-012 and FR-013 require the service to hold and re-establish a GATT link to a specific board in the owner's room. That eliminates every managed platform in one step, before any scoring.

Within what remains, systemd wins because it is the only supervisor already present on Raspberry Pi OS Trixie, it is the mechanism the PRD's primary success criterion actually describes ("starts as a system service and resumes working after a reboot"), and it puts the fewest moving parts between the owner and the one genuinely hard component, the BLE transport. The two research findings that could have invalidated the whole approach both came back clean: Trixie ships BlueZ 5.82, well above `bleak`'s 5.55 floor, and `uv python install 3.14` has real `aarch64-unknown-linux-gnu` builds, so the `>=3.14` requirement in `tech-stack.md` is satisfiable on-device without compiling CPython on a Pi.

The owner's stated operational constraints all point the same way: bare OS with no Docker, SSH access, git already configured on the Pi, manual promotion plus a convenience script, and a stubbed board transport for the local Windows loop.

## Platform Comparison

### Hard filter applied first

Six platforms were researched and then dropped before scoring. Recorded here so the exclusion is auditable rather than invisible.

| Platform | Dropped because |
|---|---|
| Cloudflare Workers + Pages | No BLE. Additionally cannot run `bleak`, `dbus-fast` or Pillow: the Workers Python runtime is Pyodide-based with no D-Bus and no native Bluetooth stack. |
| Vercel | No BLE, and no persistent process to hold a GATT connection or run the reconnect loop (FR-013). |
| Netlify | Same as Vercel. Functions are request-scoped. |
| Fly.io | Could host the FastAPI half competently, but has no radio in the owner's room. |
| Railway | As Fly.io. |
| Render | As Fly.io. |

A hybrid split was considered explicitly and rejected: run the Spotify poller and renderer on a PaaS, leave a thin BLE agent on the Pi. It fails on cost/benefit, not feasibility. It introduces a WAN round trip inside a 5-second budget, a second deployment target inside a 3-week after-hours window, and a new authentication boundary between the two halves, in exchange for nothing a single-user service needs. It would also move the Spotify refresh credential off the Pi, which the PRD guardrails forbid ("The Spotify refresh credential never leaves the device it was created on").

### Scoring matrix

Scored against `references/agent-friendly-criteria.md`. All three surviving candidates run on hardware the owner administers, so criterion 2 cannot be passed by any viable option and therefore does not discriminate. It is scored honestly rather than quietly reweighted.

| Candidate | CLI-first | Managed/serverless | Agent-readable docs | Stable deploy API | MCP / integration | Total |
|---|---|---|---|---|---|---|
| **Pi + systemd + uv** | Pass | Fail (inapplicable) | Pass | Pass | Partial | **3 Pass, 1 Partial** |
| Pi + Docker Compose | Pass | Partial | Pass | Partial | Partial | 2 Pass, 3 Partial |
| Pi + pm2 + uv | Pass | Fail (inapplicable) | Partial | Partial | Fail | 1 Pass, 2 Partial, 2 Fail |

**Pi + systemd + uv.** CLI-first passes outright: `systemctl`, `journalctl`, `git` and `uv` are all non-interactive and all reachable over SSH, so an agent can complete deploy, restart, log-read and rollback unattended. The one browser-bound step is adding the public hostname route and the Access application in the Cloudflare dashboard, which is one-time setup rather than an operational loop, and is scriptable via `cloudflared` or the Cloudflare API if desired. Managed/serverless fails: the owner patches the OS. Docs pass strongly, since systemd, BlueZ and Debian all publish man pages (`manpages.debian.org/trixie/`), `uv`'s docs are MDX in a public repo, and `bleak`'s are on Read the Docs. Deploy API passes: every step returns a real exit code, and rollback is `git checkout <sha> && uv sync && systemctl restart`. MCP is partial, because the GitHub MCP server covers the repo side and the Cloudflare MCP servers cover tunnel and Access, but the Pi itself is driven over SSH rather than a typed tool surface.

**Pi + Docker Compose.** The runner-up, and a genuinely reasonable choice. Scores better than systemd on exactly one axis: rollback is a tag swap rather than a git checkout plus dependency resync, which is cleaner. It loses on the project's hardest requirement. Letting BLE out of a container needs `network_mode: host`, a bind mount of `/run/dbus`, and either elevated capabilities or careful D-Bus policy work, which means the Bluetooth path, the single most failure-prone layer in the system, is also the layer carrying the most container-specific configuration. Building Pillow and `dbus-fast` wheels for arm64 on the Pi itself is slow enough to make the edit-test loop painful in an after-hours budget. Deploy API is scored Partial for that build cost, not for determinism. And the owner is on a bare OS, so this is new surface introduced during the only three weeks available.

**Pi + pm2 + uv.** This is what `tech-stack.md` currently records, and pm2 is already installed on the Pi, so it carries no installation cost. The objections are about fit, not setup. pm2 treats a Python process as a generic interpreter, so its process model gives nothing Python-specific back. Its graceful-shutdown contract sends SIGINT and then SIGKILL after a default 1.6 seconds, which is actively hostile to an asyncio service that must close a GATT handle and flush a final board state cleanly. `pm2 startup` generates a systemd unit anyway, so the end state is systemd supervising pm2 supervising the service, with the restart policy and the boot ordering split across two layers that have to agree. Docs score Partial because pm2's documentation is web-only, Node-centric, and thin on Python. MCP scores Fail.

### Shortlisted Platforms

#### 1. Pi + systemd + uv, manual promotion via SSH script (Recommended)

Won on the two things that matter inside a three-week after-hours budget with a hardware-dependent requirement: it adds no runtime that is not already on Trixie, and it expresses the primary success criterion directly. "Starts as a system service and resumes after a reboot with no human action" is a sentence about a systemd unit. Everything about operating it (restart policy, boot ordering against `bluetooth.target`, log retention, failure escalation) is a property of one declarative file that lives in the repo and can be reviewed in a diff.

#### 2. Pi + Docker Compose

Cleanest rollback story of the three, and the right answer if this service later grows siblings on the same Pi that need isolation from each other. For v1 it concentrates configuration risk exactly where the project can least afford it, on the BLE transport, and spends scarce time on image builds.

#### 3. Pi + pm2 + uv

Already installed and has a good CLI, which is why it was the initial proposal. It ends up layered on top of systemd regardless, and its default kill window conflicts with clean GATT teardown. Keeping it means owning a two-layer supervision story for no gain.

## Anti-Bias Cross-Check: Pi + systemd + uv

Notably, none of the findings below argue that systemd is the wrong supervisor. Every one of them is about getting the unit and its surroundings right. That is the actual risk surface of this recommendation.

### Devil's Advocate -- Weaknesses

1. **`bluetooth.target` is not a readiness signal, and the standard Pi recipe is stale on Trixie.** `After=bluetooth.target` guarantees ordering, not that the adapter is powered and `org.bluez` has published an adapter object. Worse, Raspberry Pi OS Trixie **removed `hciuart.service`** entirely, so the widely-copied `After=hciuart.service bluetooth.target` line orders against a unit that does not exist and fails silently. A cold boot after a power cut races BlueZ.
2. **`Restart=always` plus systemd's start limiter can disable the service permanently.** A BLE library that raises on a missing adapter will exit fast. systemd's defaults (`StartLimitBurst=5` within `StartLimitIntervalSec=10`) then place the unit in `failed` state and stop retrying. That is FR-014, the *primary* success criterion, failing with no board output and no status page to consult, because the page is served by the process that died.
3. **uv's interpreter and venv location are deploy-user-scoped and mutated in place.** The managed CPython lives under the deploying user's `~/.local/share/uv`. A unit with a different `User=`, or one `sudo git pull`, and the unit cannot resolve Python. Separately, `uv sync` rewrites `.venv` in place, so a deploy mutates the running process's environment *before* the restart lands.
4. **Journald may be volatile.** On Pi images the journal is frequently in `/run/log/journal`, i.e. RAM-backed. The evidence for the 7-day unattended NFR, which the PRD notes can only be obtained by observation, can be erased by the very reboot being investigated.
5. **The service is its own only observability surface.** It is simultaneously the board driver and the status page. One unhandled exception in the render path produces precisely the state the guardrails forbid: a stale cover on the board with no staleness marker, and nothing available to query about why.

### Pre-Mortem -- How This Could Fail

Week one went perfectly: the milestone cover hit the board, driven from the laptop. systemd came later and seemed trivial, so the unit was copied from a Bookworm blog post, with `Restart=always` and `After=hciuart.service bluetooth.target`. It worked, because the Pi was never cold-booted during development. Every test restarted a unit on a machine whose Bluetooth stack had been up for days. In week three the 7-day run began. On day two a power blip rebooted the Pi. The unit lost the race with BlueZ, burned its five permitted restarts inside ten seconds, and systemd stopped it. The board went on displaying the cover from forty hours earlier, unmarked, because a staleness marker can only be shown by pushing to a board that nothing was talking to. The status page was down too, so no surface anywhere carried a signal. The owner noticed days later, from across the room, wondering why the album never changed. The 7-day property was unprovable, the primary success criterion was false, and the guardrail that mattered most, never present a cover the service cannot vouch for, had been violated for two days by the deployment layer rather than by any application code.

### Unknown Unknowns

- **The tunnel connector is on a different LAN host than the Pi**, per the PRD's Existing Infrastructure section, which pins the hostname route to the Pi's LAN address. uvicorn only trusts forwarded headers from `127.0.0.1` by default, so `--proxy-headers` alone is insufficient: `X-Forwarded-Proto: https` is discarded and FastAPI constructs the Spotify `redirect_uri` as `http://192.168.x.x:8000/callback`. Spotify requires an exact HTTPS match, so re-authorization (FR-005, FR-015) fails in a way that presents as a Spotify fault. Either widen `--forwarded-allow-ips` to the connector's address or pin the redirect URI from configuration and never derive it from the request.
- **`idotmatrix` 0.0.9 declares `asyncio` as a runtime dependency.** The current PyPI `asyncio` (4.0.0) is a deliberately empty metadata-only wheel, so it resolves harmlessly today and `import asyncio` still hits the stdlib. But it will sit in `uv.lock` as a junk pin, and the package's existence is a maintenance signal: no release since 2025-04-13, no `requires-python` declared, and only the `Programming Language :: Python :: 3` classifier. Nothing has tested this library against Python 3.14, which `tech-stack.md` mandates. There are at least three forks (`markusressel`, `brmk`, `derkalle4`) with divergent APIs.
  > Superseded, 2026-11-04: the project does not use `idotmatrix` 0.0.9. `pyproject.toml` pins `idotmatrix-api-client==0.3.0`, the maintained fork published under its own name, so the junk `asyncio` pin never reaches `uv.lock` and the three-divergent-forks question above is settled rather than open. 0.3.0 declares `requires-python >=3.12,<3.15`, which covers the 3.14 that `tech-stack.md` mandates, and depends on `bleak>=0.22.3`, `cryptography>=46.0.7`, `pillow>=12.2.0` and `watchdog>=6.0.0`; `watchdog` shows up in the lock only as that transitive dependency. Its API is explicitly incompatible with 0.0.9's, which is why the pin is exact rather than a floor. What is left unverified is narrower than the original bullet: the library imports on 3.14, but nothing yet confirms a 32x32 board driven over BlueZ, which stays the least-verified combination in the stack.
- **Non-root BLE has two separate traps.** The service user must be in the `bluetooth` group for the D-Bus policy in `/etc/dbus-1/system.d/bluetooth.conf` to permit talking to `org.bluez`, and group membership is only read when the unit starts. Additionally, a `systemd --user` unit requires `loginctl enable-linger` or it is torn down at SSH logout, which would make "survives reboot" appear to pass in interactive testing and fail in reality. Use a system unit with an explicit `User=`.
- **The Spotify OAuth callback only survives Cloudflare Access because the browser already holds the Access JWT.** If the Access session expires during the Spotify consent redirect, the `?code=` parameter lands on the Access login page. Access does restore the original URL after login, but the authorization code has a short lifetime and may expire first. Re-running the flow fixes it; knowing that in advance saves an hour.
- **Trixie is a different OS from most Pi documentation on the internet.** It ships Python 3.13 and BlueZ 5.82, enforces PEP 668 (`externally-managed-environment`) on the system Python, and has dropped `hciuart.service`. Bookworm-era recipes for Bluetooth service ordering and for `pip install` should be assumed stale.

## Operational Story

- **Preview deploys**: none, and none should be built. There is one board and one Spotify account, so a second concurrent instance would contend for the same BLE peripheral and the same rate-limited API. The substitute for a preview environment is the stubbed board transport chosen for the local loop: run the service on the Windows PC with a fake transport that writes the rendered 32x32 frame to a PNG, giving full fidelity on the Spotify and render layers and none on transport, exactly the parity carve-out FR-016 already states. Verification of the board layer happens only on the Pi.
- **Secrets**: the Spotify client ID/secret and the refresh token live in a single file on the Pi, referenced from the unit via `EnvironmentFile=`, owned by the service user with mode `0600`, and outside the git working tree so no `git pull` can clobber it and no commit can leak it. This is what satisfies the guardrail that the refresh credential never leaves the device it was created on. Nothing Spotify-related belongs in GitHub Secrets, because CI never deploys. Rotation is manual: re-authorize through the web page, which rewrites the token file in place.
- **Rollback**: `git -C <repo> checkout <known-good-sha> && uv sync --frozen && sudo systemctl restart spotify-to-led`, then `systemctl is-active` to confirm. Time to revert is dominated by `uv sync`, so seconds to a minute. Caveat: there is no database and therefore no migration to unwind, but the token file is *not* in git, so a rollback does not restore a previous credential state and does not need to.
- **Approval**: an agent may read logs, run the deploy script, restart the unit, and roll back, all unattended, because every one of those is reversible and blast-radius-limited to one device. A human is required for anything that touches the edge or the account: creating or modifying the Cloudflare Access application, changing the tunnel's public hostname route, registering redirect URIs in the Spotify app settings, and completing an OAuth consent. Those are outward-facing, affect a domain shared with a live n8n deployment, and are not reversible by a local restart.
- **Logs**: `journalctl -u spotify-to-led -f` to follow, `journalctl -u spotify-to-led --since '-1h' --no-pager` for a bounded read, and `systemctl status spotify-to-led` for restart counts and the current state. All read-only and all SSH-reachable. Set `Storage=persistent` in `/etc/systemd/journald.conf` first, or the 7-day evidence will not survive a reboot (see risk register).

## Risk Register

| Risk | Source | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| Cold-boot race: unit starts before BlueZ has a powered adapter | Devil's advocate | H | H | Order with `After=bluetooth.target dbus.service` and `Wants=bluetooth.target`. Do **not** reference `hciuart.service`, which does not exist on Trixie. Treat readiness as an application concern: retry adapter discovery in-process with backoff rather than relying on unit ordering. |
| `StartLimitBurst` permanently stops the unit, silently killing FR-014 | Devil's advocate | M | H | Set `Restart=always`, `RestartSec=10`, and `StartLimitIntervalSec=0` to disable the limiter, so a hardware fault yields indefinite slow retries rather than a dead unit. Prefer in-process retry so the process stays alive and the status page keeps answering. |
| OAuth `redirect_uri` built as `http://<lan-ip>` and rejected by Spotify | Unknown unknowns | H | H | Pin the redirect URI from configuration; never derive it from the inbound request. If derived, pass `--proxy-headers` *and* `--forwarded-allow-ips=<connector-ip>` to uvicorn. Verify by reading the exact string sent in the authorize call. |
| `idotmatrix` untested on Python 3.14; unmaintained since 2025-04 | Research finding | M | H | Smoke-test import and a single frame push on the Pi against 3.14 in week 1, before the milestone depends on it. Isolate it behind the stub-able transport interface already planned, so swapping forks is a one-file change. Pin the exact version in `uv.lock` and note the junk `asyncio` pin. Updated 2026-11-04: the fork swap has already happened -- the dependency is `idotmatrix-api-client` 0.3.0, pinned exactly, which declares `>=3.12,<3.15` support and carries no `asyncio` pin, so that part of the mitigation is done. The import smoke test and the stub-able transport interface still stand. 32x32-on-BlueZ remains the least-verified combination, so the single frame push on the Pi is the step that actually closes this risk. |
| Volatile journald erases evidence for the 7-day NFR | Devil's advocate | M | M | Set `Storage=persistent` in `/etc/systemd/journald.conf` and cap with `SystemMaxUse=`. Do this before starting the 7-day run, not after. |
| uv interpreter unresolvable from the unit, or `.venv` mutated under the running process | Devil's advocate | M | M | Run as a system unit with an explicit `User=` matching the repo owner, set `ExecStart` to the absolute `.venv/bin/python`, and never deploy with `sudo git`. Have the deploy script order operations as `git pull` then `uv sync` then `restart`, accepting a brief window where venv and process disagree. |
| Non-root BLE denied by D-Bus policy, or user unit killed at SSH logout | Unknown unknowns | M | M | Add the service user to the `bluetooth` group and confirm with a non-interactive `bleak` scan. Use a **system** unit with `User=`, not a `--user` unit, so `enable-linger` is irrelevant. |
| Single process is both driver and observability surface | Devil's advocate | M | H | Keep the FastAPI status route independent of the board task: supervise the BLE loop as an asyncio task whose crash is caught and reported as link-down on the page (FR-015) rather than propagating and killing the server. |
| Access session expiry strands the Spotify authorization code | Unknown unknowns | L | L | Known-good recovery is to re-run the authorization. Document it on the status page next to the re-auth button. |
| Tunnel route breaks and re-authorization becomes unreachable | PRD assumption | L | M | Accepted by the PRD: the board display depends only on outbound calls to Spotify, so a tunnel outage degrades re-auth only. Keep a documented SSH fallback for reading state via `journalctl`. |
| Bookworm-era Pi documentation applied to a Trixie system | Unknown unknowns | M | M | Verify against `manpages.debian.org/trixie/` and the Raspberry Pi Trixie release notes rather than blog posts. Confirm `bluetoothctl --version` reports 5.82 and `python3 --version` reports 3.13 before trusting any guide. |

## Supersedes a prior note in tech-stack.md

`context/foundation/tech-stack.md` records "run through uv under pm2 with pm2 startup for reboot survival". That was a proposal reflecting the fact that pm2 is already installed on the Pi, not a researched decision. This document supersedes it: the supervisor is a systemd system unit. The reasoning is in the Pi + pm2 paragraph above, and a pointer has been added to `tech-stack.md` so the two documents do not contradict each other silently. The frontmatter keys in `tech-stack.md` are unchanged, since `deployment_target: self-host` and `ci_default_flow: manual-promotion` both remain accurate.

## Getting Started

Validated against the versions this project actually pins, not against general Pi documentation. Trixie is not Bookworm, and several common recipes are stale on it.

1. **Confirm the baseline on the Pi before anything else.** `cat /etc/os-release && bluetoothctl --version && python3 --version && uname -m`. Expect Debian 13, BlueZ 5.82, Python 3.13, `aarch64`. If `uname -m` is not `aarch64`, stop: the uv-managed 3.14 build and the GitHub runner both assume 64-bit.
2. **Install uv and the pinned interpreter**, then clone and sync. `curl -LsSf https://astral.sh/uv/install.sh | sh`, then `uv python install 3.14`, then `git clone <repo> && cd spotify-to-led && uv sync`. Do not `pip install` into the system Python: Trixie enforces PEP 668 and will refuse. `.python-version` is tracked, so `uv sync` picks 3.14 without further flags.
3. **Grant BLE access and prove it independently of the app.** `sudo usermod -aG bluetooth $USER`, log out and back in, then run a two-line `bleak` scan via `uv run python -c` and confirm the board appears. Getting a device address here, before any application code depends on it, is what keeps the week-1 milestone from turning into a permissions debugging session.
4. **Write the unit, with the Trixie-correct ordering and the limiter disabled.** `After=bluetooth.target dbus.service`, `Wants=bluetooth.target`, `User=<repo owner>`, `WorkingDirectory=<repo>`, `ExecStart=<repo>/.venv/bin/python -m spotify_to_led.main`, `EnvironmentFile=<path outside repo>`, `Restart=always`, `RestartSec=10`, `StartLimitIntervalSec=0`. No `hciuart.service`. Then `sudo systemctl enable --now spotify-to-led` and verify the real requirement with `sudo reboot` followed by `systemctl is-active` once it is back.
5. **Set journald persistent before the long run.** `Storage=persistent` in `/etc/systemd/journald.conf`, then `sudo systemctl restart systemd-journald`. Cheap now, and the only way the 7-day NFR produces evidence that survives a reboot.
6. **Add the edge route and the deploy script last.** In the Cloudflare dashboard, add the public hostname on the existing connector pointing at the Pi's reserved LAN address, add an Access application scoped to the owner's email for that hostname, and register `https://<sub>.<domain>/callback` in the Spotify app settings. Then write the one-command promotion script (`ssh pi 'cd repo && git pull && uv sync --frozen && sudo systemctl restart spotify-to-led && systemctl is-active spotify-to-led'`) so promotion from the Windows PC is a single invocation with a real exit code.

## Out of Scope

The following were not evaluated in this research:

- Docker image configuration (the Compose option was scored, not designed).
- CI/CD pipeline setup. GitHub Actions remains checks-only per `tech-stack.md`; a self-hosted ARM64 runner on the Pi was researched and confirmed viable but not recommended, since the owner chose manual promotion plus a script and a runner would add a second always-on daemon to keep alive.
- Production-scale architecture: multi-region, HA, DR. Structurally inapplicable to a single-tenant service bound to one radio and one board.
- The rendering treatment for 32x32 and the legibility of the display states, which remain open questions 2 through 4 in the PRD and are resolvable only by looking at the real board.
