---
change_id: bluetooth-link-self-recovery
roadmap_item: S-03
date: 2026-11-08
---

## Conventions

The repository is public, so every real value is a placeholder: `<board address>` for the board's Bluetooth MAC, `pi` for the SSH alias the deploy script targets. Board observations are the owner's; log lines are read from `~/.pm2/logs/spotify-to-led-out.log` over SSH (`grep -a`: the file contains a binary byte). Times are the Pi's local time (CET).

## Phase 2.1: Deploy (2026-11-08)

The Pi's checkout was already on `feat/bluetooth-link-self-recovery`.

| Check | Result |
| --- | --- |
| `scripts/deploy.sh` at `2070a4e`, 20:44 | exit 0, `/healthz` answered `{"status":"ok"}` |
| `pm2 show spotify-to-led` after the deploy | `online`, 0 unstable restarts (the total of 3 reflects earlier deploys) |
| `vcgencmd get_throttled` before the physical tests, 20:43 | `0x50005`: under-voltage and throttling **active now**, as well as since boot |

The new build connected on its first attempt:

```
20:44:14,176 INFO idotmatrix.connection_manager: connected to <board address>
20:44:14,207 INFO spotify_to_led.display: board connected
20:44:14,207 INFO spotify_to_led.display: board presentation: idle
```

The `20:44:12 disconnected` line just before it is the old process letting go of the link during the pm2 reload.

## Physical tests

### (a) Board power-cycle, 20:45: passed

Board switched off for about 10 s and on again. Owner: the image came back 5-10 s after switching on, nobody touching the Pi.

```
20:45:12,278 INFO idotmatrix.connection_manager: disconnected from <board address>
20:45:12,338 INFO spotify_to_led.display: board link lost; reconnecting
20:45:12,338 INFO idotmatrix.connection_manager: connecting to <board address>...
20:45:19,525 INFO idotmatrix.connection_manager: connected to <board address>
20:45:19,531 INFO spotify_to_led.display: board connected
20:45:19,531 INFO spotify_to_led.display: board link came up again; redrawing idle
```

The display loop took the reconnect, not the library (`board link lost; reconnecting` 60 ms after the drop), and the first attempt succeeded.

### (b) Board away ~1.5 min with playback started meanwhile, 20:45-20:47: passed, as a power-off

The board was **switched off**, not carried out of range. Playback was started while it was off, and the board was switched on at 20:47. Owner: the triangle appeared, as expected.

```
20:45:46,552 INFO idotmatrix.connection_manager: disconnected from <board address>
20:45:46,643 INFO spotify_to_led.display: board link lost; reconnecting
20:46:03,567 INFO spotify_to_led.spotify.reader: Spotify playback: playing track '...'
20:46:16,699 WARNING spotify_to_led.display: board connect failed (BleakDeviceNotFoundError: Device with address <board address> was not found.); retrying in 5.0 s
20:46:51,778 WARNING spotify_to_led.display: board connect failed (BleakDeviceNotFoundError: ...); retrying in 10.0 s
20:47:19,521 INFO idotmatrix.connection_manager: connected to <board address>
20:47:19,563 INFO spotify_to_led.display: board connected
20:47:19,563 INFO spotify_to_led.display: board presentation: playing
```

Each failed attempt took about 30 s (a full scan) before its backoff, so every retry scanned again. The backoff restarted at 5 s for this outage. The presentation that changed while the link was down was the one drawn on reconnect.

A switch-off always ends a scan with `BleakDeviceNotFoundError`, which caches no device path. So (b) does not exercise the found-but-connect-failed case that research traced (research.md, "Gap"). Only a real out-of-range or edge-of-range trip can produce that case.

`vcgencmd get_throttled` at 20:52: `0x50000`. Under-voltage is no longer active, only flagged since boot.

### Overnight board-off, 2026-11-08 20:49 to 2026-11-09 05:36: passed, as a power-off

This is extra long-outage evidence, not an out-of-range test. The owner switched the board off at about 20:48 and on again at 05:36. Owner: the image appeared about 1 min after switching on, nobody touching the Pi. The Pi logged the drop at 20:49:01 (`board link lost; reconnecting`). The gap of up to a minute is either the owner's clock or BlueZ taking that long to report the drop.

pm2-logrotate split the window at midnight, so it spans `spotify-to-led-out__2026-11-09_00-00-00.log` (to 23:59) and `spotify-to-led-out.log` (from 00:00).

| Check | Result |
| --- | --- |
| Failed connects in the window | 354, all `BleakDeviceNotFoundError`: one each at 5, 10, 20 and 40 s backoff, then 125 at the 60 s ceiling before midnight and 225 after |
| Scan per attempt | every attempt took about 30 s before failing (a full scan), so none hit a cached device path |
| Unexpected errors in the display loop | 0 |
| Log volume | about 700 lines (one WARNING plus the library's `connecting to` per attempt) over 8.8 h, in line with the about 2,000 lines/day estimate in `display.py` |
| `pm2 show spotify-to-led`, 05:38 | `online`, uptime 8 h, 0 unstable restarts |
| `journalctl --list-boots` | no boot since 2026-11-08 19:28 |

```
20:49:01,848 INFO    spotify_to_led.display: board link lost; reconnecting
20:49:31,894 WARNING spotify_to_led.display: board connect failed (BleakDeviceNotFoundError ...); retrying in 5.0 s
...
05:36:39,061 WARNING spotify_to_led.display: board connect failed (BleakDeviceNotFoundError ...); retrying in 60.0 s
05:37:39,061 INFO    idotmatrix.connection_manager: connecting to <board address>...
05:37:59,796 INFO    idotmatrix.connection_manager: connected to <board address>
05:37:59,839 INFO    spotify_to_led.display: board link came up again; redrawing idle
```

The attempt in flight at switch-on (05:36:08 to 05:36:39) still ended in `BleakDeviceNotFoundError`, probably because the board was not advertising yet. The next attempt, one 60 s backoff later, connected. By the Pi's clock that is about 2 min from switch-on to redraw, within the about 2 min pass bar. The 60 s ceiling sets that worst case.

`vcgencmd get_throttled` at 05:38: `0x50005`. Under-voltage and throttling are active again.

### (c) and (d): not run as out-of-range tests

The owner decided on 2026-11-09 to skip (d), the about 5 min at the edge of range, and not to repeat (b) or (c) as carry-away trips.
- (b) stands as the about 1.5 min switch-off above.
- (c) is covered by the overnight switch-off. It lasted far longer than 15 min and showed retries at the 60 s ceiling, each one scanning.
- The board was never physically carried out of range.

What this leaves unproven on hardware: the found-but-connect-failed case that research traced. A scan reaches the board at marginal range, the connect then times out, and BlueZ drops the device. Phase 1 removed the library's reconnect loop that would have stranded the link there, and every display-loop retry discards the client (`test_each_connect_after_a_drop_uses_a_fresh_link`, `test_a_library_reported_drop_marks_the_link_down_and_starts_no_reconnect_loop`). So the protection rests on the unit tests and on the S-02 cold-boot evidence for the same retry path (`context/archive/2026-11-08-unattended-idle-board/verification.md`, Phase 3.4), not on an out-of-range observation.

## FR-013 sign-off

FR-013 ("Service can re-establish the Bluetooth link after a drop with no human action") is **signed off for drops caused by the board losing power**, on four results with nobody touching the board or the Pi:
- (a) a 10 s switch-off, redrawn on the first reconnect
- (b) a 1.5 min switch-off, with the changed presentation drawn on reconnect
- the 8.8 h overnight switch-off, with 354 retries, each scanning, and recovery about 2 min after switch-on
- 0 unstable restarts throughout

**Out-of-range drops are accepted without a physical test** (owner decision above). The gap is recorded under Open follow-ups. If the board is ever seen stuck after being carried away, the log signature to look for is `board connect failed (BleakError: device 'dev_...' not found)` repeating in about 2 ms per attempt, with no scan in between.

Under-voltage was active at 20:43 and 05:38 (`0x50005`) and inactive at 20:52 (`0x50000`). No test failed, so it confounded nothing here. It still threatens NFR-02 (see the S-02 verification, "Power supply").

## 7-day unattended run (NFR-02)

Restarted **2026-11-08 20:44 CET**, at the deploy of `2070a4e`, on the owner's decision that the run should cover the build that ships. The S-02 count, started 19:40 the same day, ends there. Run (a), (b) and the overnight outage were board power-offs. They did not restart the service (uptime 8 h at 05:38), so they do not restart the count. The run ends **2026-11-15 20:44 at the earliest**. A deploy, a manual restart or a reboot restarts the count. Evidence to read at the end:
- `pm2 show spotify-to-led` (restarts, uptime)
- `journalctl --list-boots`
- the display and reader lines in `~/.pm2/logs/spotify-to-led-out*.log`

## Open follow-ups

- **Out-of-range test:** not run (see above). Worth a carry-away trip, and an edge-of-range linger, whenever convenient. A failure there would be a defect against FR-013.
- **Host:** replace or confirm the Pi's power supply. Under-voltage was active during this rollout.
- **Pi checkout:** switch it back to `main` after the merge, or later deploys pull the feature branch.
- **Unverified library edge (from Phase 1):** bleak could report a late disconnect for an old link after a new one is up, which would mark the new link DOWN once and cost one unneeded reconnect. Not seen in any log of this rollout.
