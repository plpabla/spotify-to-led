---
change_id: pm2-supervised-service
roadmap_item: F-02
date: 2026-11-07
---

## Conventions

The repository is public, so every real value is a placeholder: `<sub>.<owner-domain>` for the published hostnames, `<board address>` for the board's Bluetooth MAC, `<alias>` for the SSH host the deploy script targets. The Pi account `pi-n8n` is named because the foundation docs already name it. Results are as observed by the owner on the Pi, pasted into the implementation session, and recorded in the plan's `## Progress`. Times are the Pi's local time.

Two plan premises did not hold and are recorded where they surfaced: non-interactive SSH reaches `pm2` through a login shell (it does not, in either sense; see Phase 3.4), and journald on the Pi is persistent by default (it is volatile; see Phase 3.5). Two code adaptations came out of the rollout, both in `fb98111`: `scripts/deploy.sh` sources `~/.profile` instead of running a login shell, and the board transport's `close()` gives up on the BLE disconnect after 5 s.

## Phase 2: Local check of the env-file selector (2026-11-06)

| Check | Result |
| --- | --- |
| `SPOTIFY_TO_LED_ENV_FILE=does-not-exist uv run python -m spotify_to_led.main` on the development machine | exits 1, `FileNotFoundError: SPOTIFY_TO_LED_ENV_FILE is set to 'does-not-exist', but no file exists there` |
| Same command on the Pi, in the checkout | same error, same message |

## Phase 3.1: Preconditions (2026-11-06)

- **Env file:** `/etc/spotify-to-led/env` had neither board key. `BOARD_TRANSPORT=idotmatrix` and `IDOT_MAC=<board address>` were added in this step with `sudoedit`, which kept the mode and owner.
- **Dev packages:** a `uv run` on the Pi had pulled the dev group into `.venv`; `uv sync --frozen --no-dev` removed its 9 packages again.

| Check | Result |
| --- | --- |
| `id -nG pi-n8n` | includes `bluetooth` |
| `sudo stat -c '%a %U' /etc/spotify-to-led/env` | `600 pi-n8n` |
| `sudo grep -cE '^(BOARD_TRANSPORT=idotmatrix\|IDOT_MAC=)' /etc/spotify-to-led/env` | `0` before the edit, `2` after |
| `uv sync --frozen --no-dev` in `~/spotify-to-led` | succeeds |

## Phase 3.2: Daemon restart for the bluetooth group (2026-11-06)

`pm2 save`, then `sudo systemctl restart pm2-pi-n8n`. `cloudflared` and n8n came back from the dump.

| Check | Result |
| --- | --- |
| `Groups:` line of the pm2 daemon's `/proc/<pid>/status` | includes `103`; `getent group bluetooth` is `bluetooth:x:103:pi-n8n` |
| `pm2 ls` 5 s after the restart | `cloudflared` and n8n `online`, 0 restarts |
| Both public hostnames (`<sub>.<owner-domain>` for n8n and for this app) | answer through the Access login |

## Phase 3.3: Log rotation (2026-11-06)

`pm2 install pm2-logrotate`, then the three `pm2 set` commands from the plan. The settings apply to every pm2 app on the host.

| Check | Result |
| --- | --- |
| `pm2 conf pm2-logrotate` | `max_size 10M`, `retain 14`, `compress false`, `rotateInterval 0 0 * * *`, `workerInterval 30` |
| First daily rotation | at 00:00 on 2026-11-07: `spotify-to-led-out__2026-11-07_00-00-00.log` and `spotify-to-led-error__2026-11-07_00-00-00.log`, live files restarted empty |

## Phase 3.4: First deploy and policy checks (2026-11-06 to 2026-11-07)

- **`pm2` not on PATH.** The first `PI_HOST=<alias> scripts/deploy.sh` stopped at `bash: line 5: pm2: command not found`, exit 127, after `git pull` and `uv sync`. pm2 comes from nvm (`~/.nvm/versions/node/v24.18.0/bin/pm2`), which only `~/.bashrc` loads, below its guard for non-interactive shells. Fix on the host: the two nvm lines added to `~/.profile`.
- **Exit 1 on success.** The second deploy reached `{"status":"ok"}` and still exited 1. Under `bash -l`, Debian's `~/.bash_logout` runs `clear_console`, which fails without a tty, and `set -e` was still active. Reproduced locally with a fake home. Fix in `fb98111`: the remote part runs as `bash -s` and sources `~/.profile` itself.
- **False-positive health check.** On the first successful deploy, pm2's instance was crash-looping on `[Errno 98] ... address already in use` (exit code 3, 21:29-21:31) while a copy of the app started by hand held port 8000, and the deploy's `/healthz` poll got its answer from that copy. Recorded as a known limitation in `infrastructure.md` and the README; no code change.
- **SIGKILL with the board unreachable.** At 22:00:54 the board dropped and the library began reconnecting. A `pm2 restart` at 22:01:06 sent SIGINT, the lifespan reached `transport.close()`, and the library's `disconnect()` waited behind the in-flight connect attempt (about 30 s) until pm2 sent SIGKILL at 22:01:16. Fix in `fb98111`: `close()` gives up after 5 s and logs a warning, with `tests/test_idotmatrix_transport.py`.
- **Crash-loop contingency:** not needed. `ecosystem.config.js` carries no `max_restarts`.

| Check | Result |
| --- | --- |
| `PI_HOST=<alias> scripts/deploy.sh; echo "exit=$?"` with `fb98111` | ends `{"status":"ok"}`, `exit=0` |
| `pm2 ls` | `spotify-to-led` `online` next to `cloudflared` and n8n |
| Board | shows the test pattern after the deploy |
| Unbuffered logs: `tail -f ~/.pm2/logs/spotify-to-led-out.log` across a `pm2 restart` at 21:59:29 | the new process's lines at 21:59:32-33, within a second; uvicorn's own lines go to `spotify-to-led-error.log` (stderr) |
| Backoff in the 21:29 crash loop | delays grew from 1 s to about 16 s over 18 consecutive restarts, no `errored` |
| Crash-loop probe: `pm2 start /bin/false --name crashloop --interpreter none --exp-backoff-restart-delay=100`, 200 s | `23 waiting restart`; then `pm2 delete crashloop`, `pm2 save` |
| Clean stop, board on: `pm2 restart spotify-to-led` (2026-11-07 19:36:38) | `exited with code [0] via signal [SIGINT]` |
| Clean stop, board off during reconnect (19:37:18) | warning `board disconnect did not finish within 5.0 s; shutting down anyway` at 19:37:17, then `exited with code [0] via signal [SIGINT]` |
| Clean stop, board off with the first `connect()` pending (started 20:12:42, stopped 20:12:48) | `exited with code [0] via signal [SIGINT]` in under a second |

## Phase 3.5: Real reboot (2026-11-07)

- **Journal not persistent.** After the first `sudo reboot` (19:55:58), `journalctl --list-boots` listed only the new boot: the previous one, running since 2026-09-20, was gone. `systemd-analyze cat-config systemd/journald.conf` showed `/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf` with `Storage=volatile`, and `/var/log/journal` did not exist. The plan and the frame had assumed the opposite.
- **Fix on the host:** `/etc/systemd/journald.conf.d/50-persistent.conf` with `Storage=persistent` and `SystemMaxUse=100M`, then `sudo systemctl restart systemd-journald` and `sudo journalctl --flush`. It sorts after the `40-rpi` file, so `cat-config` shows `Storage=persistent` last. `/var/log/journal` exists, `journalctl --disk-usage` 16M. Then a second `sudo reboot` (20:02:46).
- **`cloudflared` at boot:** restarted 1 time after the first reboot and 3 times after the second, before the network was up; pm2 brought it back each time.
- **Board after a cold start:** connects when the board is already on. A process started while the board is off fails its first connect once (`board task failed`) and does not retry; that is S-02/S-03 (FR-013), out of scope here.

| Check | Result |
| --- | --- |
| `pm2 ls` after each reboot, nobody touching the Pi | `cloudflared`, n8n and `spotify-to-led` `online`; `spotify-to-led` 0 restarts |
| `curl -s http://127.0.0.1:8000/healthz` | `{"status":"ok"}` |
| Last stop before each shutdown | `exited with code [0] via signal [SIGINT]` (19:55:58, 20:02:46) |
| Pre-reboot app lines: `grep -c '^2026-11-07 19:[34]' ~/.pm2/logs/spotify-to-led-out.log` | `14` after both reboots |
| `journalctl --list-boots` after the second reboot | 2 boots: `-1` from 19:56:15 to 20:02:48, `0` from 20:03:01 |

## Phase 4: Record the outcome (2026-11-07)

- Foundation updates: `infrastructure.md` corrected in four places that said journald was persistent by default, Getting Started gained the journald drop-in (step 5) and the nvm-in-`~/.profile` prerequisite plus the false-positive limitation (step 4). The README's Deploy section names the limitation too.
- Phase 3 implementation review: `reviews/impl-review-phase-3.md`, five findings, all fixed.
