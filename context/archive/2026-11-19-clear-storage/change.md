---
change_id: clear-storage
title: Reset the board automatically when it keeps rejecting image uploads
status: archived
created: 2026-11-19
updated: 2026-11-19
archived_at: 2026-11-19T11:08:08Z
---

## Notes

Add automatic recovery to the service for an iDotMatrix board that gets stuck rejecting every image upload, so the owner never has to SSH in and run a one-off script again.

## What happened (2026-11-18/19, evidence from the Pi's pm2 logs)

- From 2026-11-18 07:08 every frame push failed with `UploadError: the device rejected the upload (notification 0500020000)` while the BLE link stayed up. Before that, pushes had been succeeding normally. Over 1,200 failed pushes were logged; the display loop just retried with its doubling push backoff forever (1, 2, 4 ... 32 s) and the board kept showing a stale frame with no marker tied to the push failure.
- `0500020000` decodes (idotmatrix 0.3.0, `util/upload_ack.py`) as upload type 2 (TYPE_IMAGE), status 0 → `UploadStatus.ERROR`; the library's comment gives "not enough space" as an example meaning.
- Nothing on our side changed: the last deploy was 2026-11-16 (f8c249e), and idotmatrix-api-client 0.3.0 has been installed unchanged since 2026-11-04.
- The board then disappeared from BLE for ~13 h (`BleakDeviceNotFoundError`, likely powered off), came back at 2026-11-19 11:16, reconnected fine, and the very next push was rejected again. So neither a reconnect nor a power-off cleared it.
- While the service held the link, the owner's phone app could not connect to the board (the board accepts one central at a time). That is how the problem surfaced ("I cannot connect to the board").

## Experiment that identified the fix

`scripts/clear_board.py` (uncommitted, in the working tree) was run on the Pi with the service stopped. It pushes a 32x32 test frame, then escalates, pushing again after each step:

1. test push before anything → rejected (`0500020000`)
2. `client.materials.clear()` (DELETE_MATERIALS on all 12 slots) → still rejected
3. `client.gif.clear_storage()` → still rejected
4. `client.common.reset()` (the RESET command; library docstring: "resets the device and its internals. Can fix issues that appear over time") → push OK

After the reset the board rebooted: the service's first reconnect timed out (TimeoutError), the next one connected at 11:21:18, and the enhanced cover was drawn by 11:21:32. `/status` shows `link: up`, `push_failed: false`.

Conclusion: the "storage full" hypothesis was wrong — clearing storage did not help; the device RESET command did. The change name says clear-storage, but the cleanup the app needs is a board reset (whether a storage clear should also run before or after the reset is an open question for the research/plan; the experiment shows it is not sufficient on its own).

## Required change

- When pushes to a connected board keep failing with the device's upload rejection (`UploadError` carrying an ERROR status, as opposed to an ack timeout or a dropped link), the service resets the board via the library's `common.reset()` and then lets the display loop reconnect and redraw what it wants to show.
- Trigger threshold to be decided in planning (e.g. N consecutive rejections of the same presentation, or rejections lasting longer than T); it must not reset on a single transient failure, and it must not loop resetting a board that stays broken — after a reset that does not help, back off (e.g. at most one reset per hour) and keep logging.
- Expect the link to drop when the board reboots after a reset: the existing reconnect path (`display._connect`, fresh scan via `_discard_stale_link`) should handle it, and the redraw-on-reconnect behaviour (connections count growing) must still apply. The first connect after the reset can time out while the board boots — that should be fine with the existing backoff, but verify.
- The reset belongs behind the `BoardTransport` boundary (`src/spotify_to_led/board/`), e.g. a `reset()` method on `IDotMatrixBoardTransport` with a no-op/fake on the stub transport, so `display.py` decides when and the transport decides how; tests drive it with the fake.
- Observability: log at WARNING when a reset is triggered (with the rejection count/duration) and INFO when the first push after it succeeds; surface it in `/status` (e.g. last board reset time) and on the status page, so the owner can see it happened.
- Hard rules still apply: FR-018 — while pushes are failing the board may show a stale frame; consider whether the status page should flag push failures more visibly. No auth, read-only Spotify, no animation are unaffected.
- Decide what to do with `scripts/clear_board.py`: keep it as a manual diagnostic (renamed to reflect that reset is the fix, documented in README) or drop it once the app does this itself.
- Library note: idotmatrix-api-client is pinned to exactly 0.3.0; `common.reset()` sends `build_command(protocol.RESET)` with `response=True`. Check whether it waits for an ack and how it behaves when the board reboots mid-call (it may raise or hang — bound it with a timeout).

## Success criteria

- A board stuck returning `0500020000` recovers on its own within the chosen threshold, with no SSH and no manual restart, and the current cover appears again.
- No reset storms: a board that keeps rejecting after a reset is not reset again until the cooldown elapses.
- Unit tests with the fake transport cover: reset after the threshold, no reset on transient failures or on ack timeouts/link drops, cooldown, redraw after the post-reset reconnect.
- Verified once on the real Pi (deployed, logs show the reset path if/when it triggers; at minimum the manual script path still works).
