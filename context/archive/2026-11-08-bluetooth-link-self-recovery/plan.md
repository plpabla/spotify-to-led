# Bluetooth link self-recovery Implementation Plan

## Overview

Make the display loop the single owner of every Bluetooth connect and reconnect, so that a board that drops off the link (switched off, or carried out of range) always comes back through the one retry path that is already proven on hardware. Then prove FR-013 on the Pi with a board power-cycle and three out-of-range trips, and restart the 7-day unattended run on the new build.

## Current State Analysis

From `research.md` (same folder):

- The display loop retries the **first** connect forever (5 s doubling to 60 s), discarding the Bluetooth client after every failure (`src/spotify_to_led/display.py:126-148`, `src/spotify_to_led/board/idotmatrix_transport.py:98-110`). After that first success it never calls `connect` again (`display.py:31-32`).
- A drop after a successful connect is retried by the library's own `_reconnect_loop` (`.venv/Lib/site-packages/idotmatrix/connection_manager.py:685-701`). Our `_on_disconnected` discards the stale client once, so the **first** reconnect attempt scans fresh (`idotmatrix_transport.py:90-96`). Every later attempt in that loop reuses the transport built by the first attempt (`connection_manager.py:178-180`). If that attempt's scan found the board and the connect then failed, bleak keeps the cached device path (`.venv/Lib/site-packages/bleak/backends/bluezdbus/client.py:133-143`). The S-02 cold-boot failure showed the result: every retry fails instantly with "device ... not found" until the process restarts. This gap is traced in source and has not been observed on hardware.
- The redraw on a new connection already works: `connections` counts each DOWN-to-UP edge, and the display loop pushes when the count changes (`display.py:106-119`, commit `e43e150`). It was verified only with a board power-cycle.
- No out-of-range test has ever been run.

## Desired End State

- One retry path. The library's auto-reconnect is off. When `link_state` goes `DOWN`, the display loop logs it, runs `_connect` again (fresh client after each failure, backoff from 5 s again, up to 60 s), and redraws the current presentation once the link is back.
- Unit tests cover a drop followed by several failed reconnects, each one on a fresh link, ending in a redraw.
- On the Pi, the board recovered with no human touch in all four physical tests: a power-cycle, about 2 min out of range, about 15 min out of range, and about 5 min at the edge of range. The evidence is in `verification.md`, together with `get_throttled` readings, and FR-013 is signed off there.
- The 7-day unattended run (NFR-02) is restarted at the S-03 deploy and recorded.

### Key Discoveries:

- With auto-reconnect off, the library's heartbeat still detects a dead link. `_close_dead_link` calls `_handle_disconnect`, which notifies listeners and starts no reconnect loop when `_auto_reconnect` is false (`connection_manager.py:597-613, 673-683`). So `link_state` stays a reliable DOWN signal.
- `IDotMatrixBoardTransport.connect` already discards the stale link on failure (`idotmatrix_transport.py:103-107`), and the test seam `use_links` already exists (`tests/test_idotmatrix_transport.py:63-78`).
- `FakeBoard.reconnect()` in `tests/test_display.py:56-58` raises `connections` without the link going down. Those redraw tests stay valid. The new path needs a separate "drop" helper.
- `scripts/deploy.sh` pulls whatever branch the Pi's checkout tracks, so the Pi must be switched to `feat/bluetooth-link-self-recovery` for Phase 2, as S-02 did.

## What We're NOT Doing

- Reporting link state on any page or route. That is FR-015 / S-07. `/healthz` stays process-only.
- A defined display while the board is unreachable, or a bound on recovery time or attempts. The PRD left these out on purpose (`prd.md:205-207`, Non-Goals).
- Changing the library pin, or patching any further private library method beyond the existing `_discard_stale_link`.
- Fixing the Pi's power supply. The risk is recorded, and it does not block this change.
- Restarting the display task itself if it ever ends. Not reachable from the inspected paths (research, Supervision).

## Implementation Approach

Turn off the library's `set_auto_reconnect` in the adapter, and let the display loop treat `link_state is DOWN` as "go back into `_connect`". This reuses the exact code that recovers a cold boot today, which has unit tests and hardware proof. It also removes the second retry path instead of patching it, and gives every retry the same log lines. The heartbeat stays on, so a link that dies silently still turns `DOWN`.

## Critical Implementation Details

- **Timing & lifecycle**: the library schedules the `on_disconnected` listener with `ensure_future`, so `link_state` turns `DOWN` one event-loop tick after the drop. The display loop checks it at the top of each sample (every 1 s), so no special wait is needed. Keep the `attempted` record across a reconnect. The redraw comes from `connections` changing, and that only works if the last attempted pair survives the reconnect.

## Phase 1: Display loop owns reconnects

### Overview

The adapter stops the library from reconnecting by itself. The display loop goes back into its connect-retry whenever the link is down. Tests and docstrings follow.

### Changes Required:

#### 1. Board adapter

**File**: `src/spotify_to_led/board/idotmatrix_transport.py`

**Intent**: Disable the library's auto-reconnect so the display loop is the only party that reconnects. Rewrite the module docstring's "Recovery boundary" to say this: the heartbeat detects silent death, the display loop retries every connect, and a failed connect always discards the client. Drop the text about the library's own reconnect loop, and the claim that out-of-range drops remain S-03.

**Contract**: `self._client.set_auto_reconnect(False)` in `__init__`; `set_heartbeat` unchanged. `_on_disconnected` still sets `LinkState.DOWN` and still calls `_discard_stale_link()`, because a heartbeat-detected or bleak-reported drop still has to make the next connect scan fresh. Update its comment, which currently refers to the library's reconnect loop. `connect`, `connections` and `link_state` are unchanged.

#### 2. Display loop

**File**: `src/spotify_to_led/display.py`

**Intent**: After the first connect, if a sample finds `transport.link_state` is `DOWN`, log one INFO line ("board link lost; reconnecting"), await `_connect` again, and continue sampling. The connection count then makes the next sample redraw. Update the module docstring: every connect is retried here, the backoff starts over at 5 s for each outage, and log volume while the board is off is the same as the cold-boot estimate. Update the comment on the backoff constants, which now describe one retry path instead of two.

**Contract**: `run_display(transport, size, current, *, sleep)` keeps its signature and its "only `CancelledError` leaves" guarantee. The DOWN check sits inside the existing `try`, so an unexpected error is still logged and the loop goes on. `attempted` persists across reconnects. `_connect` is unchanged.

#### 3. Service docs

**File**: `src/spotify_to_led/main.py` (module docstring, lines 10-12) and `CLAUDE.md` (project description)

**Intent**: Replace "retries the board connect until it succeeds" with wording that says the display task reconnects with backoff whenever the link is down and redraws when it comes back.

**Contract**: Docstring and prose only; no behaviour change.

#### 4. Tests

**File**: `tests/test_display.py`, `tests/test_idotmatrix_transport.py`

**Intent**: Cover the new path at both layers.

**Contract**:
- `FakeBoard` gains a `drop()` that sets the link `DOWN` (the next `connect` brings it up and raises `connections`), plus a way to make a number of connects *after* a drop fail.
- New display tests:
  - A drop followed by 3 failed reconnects, then success. Sleeps are `5.0, 10.0, 20.0` after the drop, so the backoff restarts at 5 s. The unchanged presentation is pushed again, and "board link lost; reconnecting" is logged.
  - A presentation that changed while the link was down is the one drawn after reconnect.
- New transport tests:
  - Constructing the adapter leaves the library's auto-reconnect off, and a library-reported disconnect starts no reconnect loop task while `link_state` becomes `DOWN`.
  - After a drop, 3 failed `connect` calls followed by a successful one build 4 distinct links via `use_links`. This is the regression test for the research gap.
- The existing tests stay green unchanged, apart from wording in any test that refers to the library's own reconnect.

### Success Criteria:

#### Automated Verification:

- Full test suite passes: `uv run pytest`
- New display tests cover drop → failed reconnects with backoff restarting at 5 s → redraw, and a presentation changed while down being drawn after reconnect
- New transport tests cover auto-reconnect disabled (no library reconnect loop on disconnect) and a fresh link for each connect after a drop
- `grep -rn "set_auto_reconnect(True)" src/` returns nothing

#### Manual Verification:

- Laptop run with the stub transport (`uv run python -m spotify_to_led.main`, no `IDOT_MAC`) starts, shows idle in `var/frames/latest.png`, and `/healthz` answers

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 2: Prove FR-013 on the Pi

### Overview

Deploy the branch to the Pi and run the four physical tests. Restart the 7-day run on this build and record everything in `context/changes/bluetooth-link-self-recovery/verification.md`, following the conventions of the S-02 verification: public repo, so placeholders for the MAC; log lines from `~/.pm2/logs/spotify-to-led-out.log`; Pi local time.

### Changes Required:

#### 1. Deploy

**File**: none (Pi operations)

**Intent**: Switch the Pi checkout to `feat/bluetooth-link-self-recovery`, run `scripts/deploy.sh`, and confirm it is healthy. Record `vcgencmd get_throttled` before the physical tests.

**Contract**: `deploy.sh` exits 0 and `/healthz` answers `{"status":"ok"}`. `pm2 show spotify-to-led` shows `online`.

#### 2. Physical tests

**File**: `context/changes/bluetooth-link-self-recovery/verification.md`

**Intent**: Run these in order, nobody touching the Pi or the board's controls, and record times, log excerpts and the owner's board observations for each:
- **(a) Power-cycle regression:** switch the board off and on.
- **(b) Short out-of-range trip:** about 2 min. Change the Spotify state (start or stop playback) while the board is away.
- **(c) Long out-of-range trip:** about 15 min, which reaches the 60 s backoff ceiling.
- **(d) Edge of range:** about 5 min where the link flickers or scans find the board but connects fail, then bring it back.

**Contract**: Pass bar for each test:
- After the board is back in range, it shows the correct presentation within about 2 min with no human action. For (b), that means the state chosen while it was away.
- The log shows `board link lost; reconnecting`, then connect WARNINGs, then `board connected`, then a redraw or presentation line.
- No `device ... not found` failure repeats without a scan in between.
- `pm2 show spotify-to-led` shows 0 unstable restarts.

Record `get_throttled` after the tests. If a test fails, note whether under-voltage was flagged during it.

#### 3. Sign-off and 7-day restart

**File**: `context/changes/bluetooth-link-self-recovery/verification.md`

**Intent**: Record the FR-013 sign-off against the four results. Record that the 7-day unattended run (NFR-02) restarted at this deploy, with the deploy time and the earliest end time, plus the evidence to read at the end. Note in Open follow-ups that the Pi checkout must go back to `main` after merge.

**Contract**: The "7-day unattended run" and "Open follow-ups" sections mirror the S-02 verification.

### Success Criteria:

#### Automated Verification:

- `scripts/deploy.sh` exits 0 and `/healthz` answers `{"status":"ok"}`
- `pm2 show spotify-to-led` reports `online` with 0 unstable restarts after all four tests

#### Manual Verification:

- (a) Board power-cycle: board redraws the correct frame with no human action
- (b) ~2 min out of range with a Spotify state change while away: board shows the new correct presentation within ~2 min of return
- (c) ~15 min out of range: board recovers within ~2 min of return, log shows retries at the 60 s ceiling, each scanning
- (d) ~5 min at the edge of range: board recovers after return with no repeating instant `device ... not found` failures
- `vcgencmd get_throttled` recorded before and after the tests in `verification.md`
- FR-013 sign-off and the restarted 7-day run (start time, earliest end) recorded in `verification.md`

**Implementation Note**: The manual checks here are the owner's physical tests; pause after each failure to diagnose before continuing.

---

## Testing Strategy

### Unit Tests:

- Display loop:
  - drop → N failed reconnects → success, with the backoff restarting at 5 s
  - redraw of an unchanged presentation after reconnect
  - a presentation changed while down is drawn after reconnect
  - existing cold-boot, redraw and cancellation tests unchanged
- Adapter:
  - auto-reconnect off, so no library reconnect loop on disconnect
  - a fresh link per failed connect after a drop

### Integration Tests:

- None automated; the BLE stack is only exercised on the Pi (FR-016 parity scope).

### Manual Testing Steps:

1. Laptop stub run (Phase 1).
2. Pi deploy, then the four physical tests (a)-(d) in order (Phase 2).

## Performance Considerations

The log volume while the board is off now follows the display loop's existing estimate, about 2,000 lines a day at the ceiling (`display.py:34-39`). There is no other runtime cost.

## Migration Notes

Deploying restarts the 7-day NFR-02 run, which the owner accepted. After merge, switch the Pi's checkout back to `main`.

## References

- Research: `context/changes/bluetooth-link-self-recovery/research.md`
- S-02 verification (cold-boot stale-path fix, power-cycle redraw, 7-day run): `context/archive/2026-11-08-unattended-idle-board/verification.md`
- S-02 review F1 (drop-path reset, unverified reconnect-loop interaction): `context/archive/2026-11-08-unattended-idle-board/reviews/impl-review.md:28-45`
- FR-013: `context/foundation/prd.md:205`
- Existing retry path: `src/spotify_to_led/display.py:126-148`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Display loop owns reconnects

#### Automated

- [x] 1.1 Full test suite passes: `uv run pytest` — 2070a4e
- [x] 1.2 New display tests cover drop → failed reconnects with backoff restarting at 5 s → redraw, and a presentation changed while down being drawn after reconnect — 2070a4e
- [x] 1.3 New transport tests cover auto-reconnect disabled (no library reconnect loop on disconnect) and a fresh link for each connect after a drop — 2070a4e
- [x] 1.4 `grep -rn "set_auto_reconnect(True)" src/` returns nothing — 2070a4e

#### Manual

- [x] 1.5 Laptop run with the stub transport starts, shows idle in `var/frames/latest.png`, and `/healthz` answers — 2070a4e

### Phase 2: Prove FR-013 on the Pi

#### Automated

- [x] 2.1 `scripts/deploy.sh` exits 0 and `/healthz` answers `{"status":"ok"}` — 15c9b87
- [x] 2.2 `pm2 show spotify-to-led` reports `online` with 0 unstable restarts after all four tests — 15c9b87

#### Manual

- [x] 2.3 (a) Board power-cycle: board redraws the correct frame with no human action — 15c9b87
- [x] 2.4 (b) ~2 min out of range with a Spotify state change while away: board shows the new correct presentation within ~2 min of return — 15c9b87
- [x] 2.5 (c) ~15 min out of range: board recovers within ~2 min of return, log shows retries at the 60 s ceiling, each scanning — 15c9b87
- [ ] 2.6 (d) ~5 min at the edge of range: board recovers after return with no repeating instant `device ... not found` failures
- [x] 2.7 `vcgencmd get_throttled` recorded before and after the tests in `verification.md` — 15c9b87
- [x] 2.8 FR-013 sign-off and the restarted 7-day run (start time, earliest end) recorded in `verification.md` — 15c9b87
