---
date: 2026-11-08T20:28:18+01:00
researcher: Claude (Opus 5.5) for pawel
git_commit: bcc3a25
branch: main
repository: plpabla/spotify-to-led
topic: "What link-recovery behaviour is already implemented before S-03 (bluetooth-link-self-recovery)"
tags: [research, codebase, board-transport, display-loop, idotmatrix, bleak, FR-013]
status: complete
last_updated: 2026-11-08
last_updated_by: Claude (Opus 5.5) for pawel
---

# Research: What link recovery is already implemented before S-03

**Date**: 2026-11-08T20:28:18+01:00
**Researcher**: Claude (Opus 5.5) for pawel
**Git Commit**: bcc3a25
**Branch**: main
**Repository**: plpabla/spotify-to-led

## Research Question

S-03 (`bluetooth-link-self-recovery`, roadmap S-03, FR-013, US-05): check what is implemented so far, since earlier changes (F-01, S-02) already added parts of it.

## Summary

Most of S-03's mechanism is already in place. S-02 added it, and it was verified on hardware with **board power-cycles and Pi cold boots only**:

1. **Initial connect is retried forever** by the display loop: 5 s backoff doubling to a 60 s cap, with a fresh Bluetooth client after every failure (`display.py:126-148`, `idotmatrix_transport.py:98-110`).
2. **A drop after a successful connect** is retried by the library's own auto-reconnect loop, which also runs forever (5 s doubling to 60 s), plus a 30 s heartbeat that detects silent link death (`idotmatrix_transport.py:68-69`, `config.py:43`, `connection_manager.py:644-701`).
3. **The stale client is discarded on drop.** `_on_disconnected` clears the manager's cached transport, so the first reconnect attempt scans again (`idotmatrix_transport.py:90-96, 112-129`). This was review F1, fixed with Fix B.
4. **The board is redrawn when the link comes back.** `connections` counts DOWN-to-UP edges, and the display loop pushes again when the count changes (`idotmatrix_transport.py:79-88`, `display.py:107-119`, commit `e43e150`).
5. **Failures cannot end the display task or the server.** The connect loop and every sample catch `Exception` (`display.py:120-122, 133`), and the lifespan treats board faults as something to report (`main.py:1-8, 127-134`).

What S-03 still owns, according to the roadmap and the S-02 verification:

- **Out-of-range test:** carry the board out of range and back, physically. Never run.
- **FR-013 sign-off.**
- **A code gap found in this research (inferred from source, not observed).** The library's reconnect loop reuses one Bluetooth transport for every reconnect attempt after the first one following a drop. The S-02 stale-device-path trap can therefore come back during a long or marginal out-of-range absence. Details below.

## Detailed Findings

### Initial connect: display loop retry (done in S-02)

- `run_display` calls `_connect` once, before the sampling loop (`src/spotify_to_led/display.py:103`).
- `_connect` loops until `transport.connect()` succeeds. Delay is `CONNECT_RETRY_INITIAL_SECONDS = 5.0`, doubling to `CONNECT_RETRY_MAX_SECONDS = 60.0` (`display.py:58-59, 126-148`).
- The traceback is logged on the first failure only, then one WARNING line per attempt (`display.py:136-143`).
- On a failed connect, `IDotMatrixBoardTransport.connect` calls `_discard_stale_link()` before re-raising (`src/spotify_to_led/board/idotmatrix_transport.py:103-107`), so the next attempt builds a new `BleakTransport` and scans again.
- After the first success the display loop never calls `connect` again. The docstring says so: "Only the first connect is retried here" (`display.py:31-32`).

### Drop after connect: library auto-reconnect (configured since F-01, patched in S-02)

- The transport enables `set_auto_reconnect(True)` and `set_heartbeat(heartbeat_seconds)` (`idotmatrix_transport.py:68-69`). The heartbeat defaults to 30.0 s (`src/spotify_to_led/config.py:43`) and is overridable via `BOARD_HEARTBEAT_SECONDS` (`.env.example:17`).
- Library 0.3.0 (pinned exactly). On a disconnect reported by bleak, `_on_disconnected` → `_handle_disconnect` schedules listener callbacks and then starts `_reconnect_loop` if no loop is already running (`.venv/Lib/site-packages/idotmatrix/connection_manager.py:586-613`).
- `_reconnect_loop` runs while auto-reconnect is active and the link is not connected. It sleeps `delay` (5 s, doubling to 60 s: `connection_manager.py:48-49`) and then calls `self.connect()`. It catches `Exception` per attempt and stops only on `CancelledError` (`connection_manager.py:685-701`). No attempt limit exists in this path.
- Heartbeat: when the device is silent for `interval` and does not answer the heartbeat command, `_close_dead_link` runs. It fires `_handle_disconnect` (which starts the reconnect loop), clears `self._transport` and builds a new one (`connection_manager.py:644-683`).
- S-02 added `_on_disconnected` in our adapter. It sets `LinkState.DOWN` and calls `_discard_stale_link()`, which sets `manager._transport = None` only when `manager.is_connected()` is false (`idotmatrix_transport.py:90-96, 125-129`). The listener is scheduled before the reconnect loop's first 5 s sleep, so the reset lands before the first reconnect attempt (comment at `idotmatrix_transport.py:92-95`).

### Gap: reconnect attempts after the first one reuse one transport (inferred)

Source trace, on the drop path only:

1. A drop clears `manager._transport`, through our listener or `_close_dead_link`.
2. The first `_reconnect_loop` attempt calls `connect()` → `_create_transport()`. With `_transport` cleared, it builds a new `BleakTransport` (`connection_manager.py:164-187, 236-238`). That builds a single `BleakClient` in `__init__` (`.venv/Lib/site-packages/idotmatrix/transport.py:68-72`).
3. If that attempt fails, nothing clears `_transport`. Our `_discard_stale_link` runs only from our own `connect()` and `_on_disconnected` (`idotmatrix_transport.py:96, 106`), and the library's reconnect calls the manager's `connect`, not ours. Our listener does not fire either: `_handle_disconnect` returns early when `_connected` is already false (`connection_manager.py:601-602`).
4. Every later attempt finds `_transport` set for the same target and reuses it: "reusing existing transport" (`connection_manager.py:178-180`).
5. bleak's BlueZ client caches `_device_path` the first time a scan finds the device, and resets it only in `__init__` and `unpair` (`.venv/Lib/site-packages/bleak/backends/bluezdbus/client.py:82, 133-143, 515`).

Result: suppose a reconnect attempt's scan **finds** the board but the connect then fails. A board at the edge of range is the plausible case (TimeoutError, as in the S-02 cold-boot failure). If BlueZ then removes the device object, every later attempt in that loop fails immediately with "device ... not found" and never scans again. The display loop does not cover this: it never reconnects after the first success. The link would stay down until a process restart. This is exactly the mechanism S-02 observed and fixed for the initial connect (`context/archive/2026-11-08-unattended-idle-board/verification.md:55-68`).

The gap does not apply when attempts fail with `BleakDeviceNotFoundError` from the scan itself. In that case no path is cached and the next attempt scans again. That is likely why a board that is simply switched off recovers. This conditional is inferred from the bleak source above. No out-of-range run has tested it.

Confidence: the reuse is certain from source. Whether it happens in practice depends on BlueZ removing the device object between attempts, which was observed on this Pi in S-02 but not timed (the review F1 blind spot: `context/archive/2026-11-08-unattended-idle-board/reviews/impl-review.md:39`). The F1 review recorded this interaction as unverified: "Interaction with `_reconnect_loop` holding the old transport is unverified" (`impl-review.md:44`).

### Redraw after reconnect (done in S-02, `e43e150`)

- `BoardTransport.connections` is part of the protocol (`src/spotify_to_led/board/transport.py:27-36`).
- `_mark_up` increments it only on a DOWN-to-UP edge, so our `connect()` and the library's `on_connected` listener do not double-count (`idotmatrix_transport.py:79-88, 110`).
- The display loop compares `(presentation, connections)` with the last attempted pair and pushes when either differs. It logs "board link came up again; redrawing ..." when only the count changed (`display.py:106-119`).
- A push that fails while the link is down is recorded as attempted. It is drawn again on the next reconnect (`display.py:116-119`, test `test_a_push_that_failed_is_drawn_again_after_the_next_reconnect` at `tests/test_display.py:233`).

### Supervision: failures stay inside the task

- `run_display` catches `Exception` around each sample, and `_connect` catches `Exception` per attempt (`display.py:108-122, 131-145`). `CancelledError` is the documented exit (`display.py:100-101`).
- The lifespan starts the display as a plain `asyncio.create_task` and cancels it on shutdown (`main.py:117, 126`). Nothing restarts the task if it ends by a non-`Exception` `BaseException` other than cancellation. That case is not reachable from the inspected code paths and is noted only for completeness.
- `link_state` is tracked (`idotmatrix_transport.py:71-73`), but no route exposes it. `/healthz` deliberately reports only that the process is serving (`main.py:144-152`). Reporting link-down on a page is FR-015 / S-07, not S-03. The infrastructure risk register asks for "a crash is caught and reported as link-down on the page" (`context/foundation/infrastructure.md:126`). The "caught" half is in place. The "reported" half belongs to S-07.

### Test coverage of the recovery paths

Unit tests (no Bluetooth needed):

- `tests/test_idotmatrix_transport.py`:
  - `test_a_failed_connect_makes_the_next_attempt_use_a_fresh_link` (`:82`)
  - `test_a_drop_makes_the_librarys_reconnect_use_a_fresh_link` (`:101`)
  - `test_a_disconnect_that_finds_the_link_back_up_keeps_it` (`:114`)
  - `test_each_time_the_link_comes_up_is_counted_once` (`:138`)
- `tests/test_display.py`:
  - backoff retry (`:163`)
  - traceback once (`:176`)
  - redraw on reconnect (`:218`)
  - failed push redrawn on reconnect (`:233`)
  - cancellation during retry and during an in-flight connect (`:294, :323`)

No test covers the library's reconnect loop making two or more failed attempts after a drop, which is the gap above. The existing drop test asserts only that `_transport` is `None` immediately after `_on_disconnected` (`tests/test_idotmatrix_transport.py:101-111`).

## Code References

- `src/spotify_to_led/board/idotmatrix_transport.py:7-20`: module docstring stating the recovery boundary. It already says out-of-range drops and FR-013 remain S-03.
- `src/spotify_to_led/board/idotmatrix_transport.py:79-96`: `_mark_up`, listener callbacks.
- `src/spotify_to_led/board/idotmatrix_transport.py:98-129`: `connect`, `_discard_stale_link` (sets the library's private `_connection_manager._transport`; relies on the exact 0.3.0 pin).
- `src/spotify_to_led/display.py:90-148`: `run_display`, `_connect`.
- `src/spotify_to_led/board/transport.py:20-42`: `BoardTransport` protocol including `connections`.
- `src/spotify_to_led/config.py:43`: `board_heartbeat_seconds = 30.0`.
- `.venv/Lib/site-packages/idotmatrix/connection_manager.py:164-187`: `_create_transport` reuse rule.
- `.venv/Lib/site-packages/idotmatrix/connection_manager.py:586-613`: disconnect handling, reconnect loop start.
- `.venv/Lib/site-packages/idotmatrix/connection_manager.py:644-701`: heartbeat, `_close_dead_link`, `_reconnect_loop`.
- `.venv/Lib/site-packages/bleak/backends/bluezdbus/client.py:82, 133-143, 515`: device-path caching.

## Architecture Insights

- **Two retry paths by design.** The display loop owns the initial connect and the library owns reconnects after a drop. Both use 5 s → 60 s backoff so their log lines look alike (`display.py:55-59`). S-03 only needs to touch one of these paths if the gap above is closed inside the adapter, or it can move all retries into the display loop. That choice is for the plan.
- **Library internals are patched through private fields** (`_connection_manager._transport`), and this is accepted only because of the exact pin (`idotmatrix_transport.py:121-123`). A fix for the gap will likely touch the same private surface, or wrap the manager's `connect`. Any version bump must re-check both.
- **Evidence is log lines.** The display-loop docstring treats `pm2 logs` lines as the proof of unattended recovery (`display.py:34-39`). An out-of-range test would be read from the same `~/.pm2/logs/spotify-to-led-out.log` lines (`idotmatrix: disconnected`, `Reconnection attempt failed: ...`, `connected`, `board link came up again`).

## Historical Context (from prior changes)

- `context/foundation/roadmap.md:286-300` (S-03 Unknowns): says reconnect is restored and redraw is addressed in S-02, and S-03 still owns the out-of-range test and FR-013 sign-off. **Supported** by the code above. The statement "the library's reconnect after a drop now uses a fresh Bluetooth client" is **partial**: true for the first reconnect attempt after a drop, not for later attempts in the same loop.
- `context/foundation/roadmap.md:286-292` (F-01 observation, 2026-11-04): ~30 s board-off, library reconnected after 5 s delay, no redraw. **Supported** as history. The "no redraw" part is superseded by `e43e150`.
- `context/archive/2026-11-08-unattended-idle-board/verification.md:103-115`: board power-cycle on `e43e150` recovered in about 14 s with a redraw. **Supported**. It is the only post-drop hardware evidence, and it covers a single short outage.
- `context/archive/2026-11-08-unattended-idle-board/verification.md:128-130`: the 7-day unattended run (NFR-02) started 2026-11-08 19:40 CET and ends 2026-11-15 19:40 at the earliest. **A deploy restarts the count.** Any S-03 code change deployed before then resets it. This is a sequencing constraint for the plan.
- `context/archive/2026-11-08-unattended-idle-board/reviews/impl-review.md:28-45` (F1): the drop-path stale-link reset was chosen as Fix B. The unverified interaction with `_reconnect_loop` named there is the gap this research traces.
- `context/archive/2026-11-08-unattended-idle-board/verification.md:117-126`: the Pi is under-powered (under-voltage flags). This is a host risk, and it could confound a Bluetooth range test (weak radio, brownouts).
- `context/foundation/prd.md:205-207`: FR-013 is unbounded by decision, with no limit on duration or attempts and no defined display while disconnected. `prd.md:289`: no staleness guarantee while the board is unreachable.

## Related Research

- `context/archive/2026-11-08-unattended-idle-board/verification.md`: hardware evidence for connect retry, cold boot and power-cycle redraw.
- `context/archive/2026-11-08-unattended-idle-board/reviews/impl-review.md`: F1/F2 on stale-link reset.
- `context/foundation/lessons.md`: not present.

## Open Questions

1. **Does the reuse gap happen on this Pi?** It needs an out-of-range test long enough for two or more reconnect attempts, ideally with the board drifting at the edge of range so a scan finds it but the connect times out. Logs to watch for: `Reconnection attempt failed: device 'dev_...' not found` repeating at about 2 ms per attempt without recovery.
2. **Fix shape (plan decision).** Options:
   - Reset `_transport` after each failed library reconnect attempt, for example by wrapping or replacing the manager's `connect` inside the adapter.
   - Disable the library's auto-reconnect and have the display loop own all reconnects through `_connect`, which already discards a stale link on failure.
   - Accept the risk and only test.
3. **Timing against the 7-day run.** Deploy S-03 code before 2026-11-15 19:40 (restarting NFR-02's clock), or after.
4. **How to judge "out of range" physically.** The PRD does not define a distance, a duration or a pass bar beyond "link returns with no human action" (`prd.md:205`). The plan needs a concrete test script: how far, how long, and how many repetitions.
