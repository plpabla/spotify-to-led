---
change_id: unattended-idle-board
roadmap_item: S-02
date: 2026-11-08
---

## Conventions

The repository is public, so every real value is a placeholder: `<board address>` for the board's Bluetooth MAC, `pi` for the SSH alias the deploy script targets. The Pi account `pi-n8n` is named because the foundation docs already name it. Board observations are the owner's; log lines are read from `~/.pm2/logs/spotify-to-led-out.log` and the journal over SSH. Times are the Pi's local time (CET). The Pi has no battery-backed clock, so log lines written right after a boot, before NTP sync, run up to about a minute behind the owner's wall-clock times.

One plan assumption did not hold and is recorded where it surfaced: that `IDotMatrixClient.connect()` can be retried indefinitely after a failure (`plan-brief.md`, Open Risks & Assumptions). It cannot; see Phase 3.4. The fix is `4fb1b16`. A host finding unrelated to the code also surfaced: the Pi is under-powered (see "Power supply").

The Pi's checkout was switched from `main` to `feat/unattended-idle-board` for this rollout. Switch it back to `main` after the merge, or later deploys pull the feature branch.

## Phase 3.1: Deploy (2026-11-08)

| Check | Result |
| --- | --- |
| `scripts/deploy.sh` at `3acf251`, 18:24 | exit 0, `/healthz` answered `{"status":"ok"}` |
| `scripts/deploy.sh` at `4fb1b16` (the fix), 18:52 | exit 0, `/healthz` answered `{"status":"ok"}` |
| `pm2 show spotify-to-led` after the deploys | `online`, 0 unstable restarts (the total restart count only reflects earlier deploys and reloads) |

The board was off at the first deploy. The display loop logged the first failure with its traceback and every later one as a single WARNING line, as planned:

```
18:24:55 WARNING spotify_to_led.display: board connect failed (BleakDeviceNotFoundError: Device with address <board address> was not found.); retrying in 5.0 s
```

With the board off, each attempt takes about 30 s (bleak's scan) before the backoff delay, so attempts land every 35 s at first and every 90 s at the ceiling: fewer log lines than the plan's estimate.

## Phase 3.2: Legibility on the real board (2026-11-08)

Switched on at 18:29 with no restart, the board connected and showed the idle note by itself (`18:29:48 board connected`, `board presentation: idle`).

| Check | Result |
| --- | --- |
| Idle note from normal desk viewing distance | reads as deliberate and "on"; owner: "looks good". `IDLE_MAX_CHANNEL` stays at 64, no tuning |
| Idle versus the playing placeholder | clearly distinct |
| Placeholder brightness | too bright, accepted: the placeholder is deleted by S-04 |

## Phase 3.5: Music on the Pi (2026-11-08)

| Event | Reader | Board |
| --- | --- | --- |
| Playback started | `18:31:36,056 paused track 'Seraph'` | `18:31:36,819 presentation: playing` |
| Paused | `18:31:45 paused` | triangle stays, as designed: a paused item keeps its cover (`playback.py`) |
| Spotify quit | `18:32:44,959 nothing_playing` | `18:32:44,972 presentation: idle` |

Both switches land well inside 5 s of the reader's report.

An unexplained pm2 restart at 18:26:31 (`Stopping app`, `exited with code [0] via signal [SIGINT]`, the same entries a deploy produces) happened while the connect was being retried. It was a requested stop, not a crash, and it showed that a stop in the middle of a retry exits cleanly in under a second.

## Phase 3.4: Cold boot with the board off (2026-11-08)

**First attempt, on `3acf251`: failed.** Power cut 18:43, board switched on at 18:47, no note by 18:51. Log:

```
18:48:15 INFO  connecting to <board address>...
18:48:46 WARNING board connect failed (TimeoutError: ); retrying in 60.0 s
18:49:46 WARNING board connect failed (BleakError: device 'dev_<board address>' not found); retrying in 60.0 s
18:50:46 WARNING board connect failed (BleakError: device 'dev_<board address>' not found); retrying in 60.0 s
```

The same pattern explains the first run A attempt (boot starting about 18:32), where the board stayed on the triangle: one `TimeoutError`, then only instant `device ... not found` failures.

Cause: bleak's BlueZ backend caches the device path the first time a scan finds the board (`bleak/backends/bluezdbus/client.py:133-149`), and the library reuses one `BleakClient` per transport. When the connect after that scan times out (a board still booting), BlueZ removes the device object, and every later connect fails in about 2 ms against the dead path without scanning again. Nothing recovers it.

Fix, `4fb1b16`: `IDotMatrixBoardTransport.connect` drops the library's cached transport after any failed connect, so the next attempt builds a fresh client and scans again. The library does the same reset for a link its heartbeat finds dead (`connection_manager.py:673-683`). It sets a private field, accepted because the library is pinned to exactly 0.3.0; a version bump must re-check it. Regression test: `test_a_failed_connect_makes_the_next_attempt_use_a_fresh_link`.

On the fix, still warm from the stuck state, the board connected after three `TimeoutError` attempts that each scanned again (18:54:27 to 18:56:04).

**Second attempt, on `4fb1b16`: passed.** Power cut and restore about 19:06-19:08 with the board off; board switched on at 19:11; note visible right away.

```
19:07:32 WARNING board connect failed (BleakDeviceNotFoundError ...); retrying in 5.0 s
19:08:08 WARNING ... retrying in 10.0 s
19:08:48 WARNING ... retrying in 20.0 s
19:09:38 WARNING ... retrying in 40.0 s
19:10:48 WARNING ... retrying in 60.0 s
19:11:54 INFO    board connected
19:11:54 INFO    board presentation: idle
```

`pm2 show spotify-to-led`: 0 restarts.

An attempt in between (power cut 18:58, restored 19:02) never came back on the network: no SSH, no ping until the owner power-cycled again. `journalctl --list-boots` has no entry for that boot, so it never reached the point where the journal is written, long before pm2 or this app start. See "Power supply".

## Phase 3.3: Cold boot with the board on (2026-11-08)

The first run A, on `3acf251`, failed for the reason recorded under 3.4 and passed on a second power cycle at 18:40 (the first connect succeeded, so the stale path never arose). Repeated on the fix:

Board on and showing the triangle; Pi power cut 19:15, Spotify stopped while the Pi was off, power restored 19:16, nobody touching anything. The board changed from the triangle to the note by itself at 19:16.

```
19:15:10 INFO presentation: playing          (before the cut)
19:15:40 INFO Spotify playback: nothing_playing   (pre-NTP clock, after the restore)
19:15:47 INFO board connected
19:15:47 INFO board presentation: idle
```

Connected on the first attempt. `pm2 show spotify-to-led`: 0 restarts.

## Power supply

The Pi (kernel `6.18.34+rpt-rpi-2712`, a Pi 5) is under-powered:

| Check | Result |
| --- | --- |
| `journalctl -b 0 -k \| grep -c 'Undervoltage detected'` | 8 on the 19:06 boot, 1 on the 19:16 boot |
| `vcgencmd get_throttled` | `0x50000`: under-voltage has occurred and throttling has occurred since boot |

The boot that never came up (19:02) is consistent with a brownout. This is a host risk, not an application one, but it threatens NFR-02: a Pi that hangs at boot fails the unattended run and leaves no log. Check that the supply is the official 5 V / 5 A (27 W) Pi 5 unit and that nothing else draws from the Pi's USB ports.

## 7-day unattended run (NFR-02)

Started **2026-11-08 19:31 CET**, at the redeploy of `7fddf47` (the review fixes, including the reset on disconnect). It first started at 19:16 on `4fb1b16`; the review's F1 fix changed runtime behaviour, so the owner chose to redeploy and restart the count. A clean reboot at 19:28 (`systemd-reboot.service`, requested, not a power loss) came just before the redeploy; the app came back by itself after it. The owner chose to start now and carry the power-supply finding as an open risk rather than wait for a new supply. A deploy, a manual restart or a reboot caused by the supply restarts the count. Evidence to read at the end: `pm2 show spotify-to-led` (restarts, uptime), `journalctl --list-boots`, and the display and reader lines in `~/.pm2/logs/spotify-to-led-out.log`.

## Open follow-ups

- **S-03:** the library's own auto-reconnect after a dropped link would reuse the same transport and fall into the same stale-path trap. After the S-02 review (F1) `_discard_stale_link` also runs from the disconnect listener, guarded so a live link is never discarded, so the library's reconnect builds a fresh client too. This is unit-tested only; S-03's physical drop tests are where it is proven on the board.
- **Host:** replace or confirm the Pi's power supply (see above).
- **Pi checkout:** switch back to `main` after the merge.
