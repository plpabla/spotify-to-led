# Board reset on repeated upload rejections — Implementation Plan

## Overview

When the iDotMatrix board keeps rejecting image uploads while the BLE link stays up, the service recovers it on its own: it clears the board's stored materials and sends the RESET command, at most once an hour automatically, then redraws at once. The owner sees when the last reset happened on the status page and can trigger one by hand with a "Reset board" button. This replaces the one-off `scripts/clear_board.py` used on 2026-11-19.

## Current State Analysis

- A rejected upload surfaces as the library's `UploadError("the device rejected the upload (notification …)")` (`.venv/Lib/site-packages/idotmatrix/connection_manager.py:424`); the same class is raised for an ack timeout (`:420`), so only the message tells them apart.
- `display._push` swallows every exception and returns a bool (`src/spotify_to_led/display.py:434-450`); the push retry (1 s doubling to 60 s, `display.py:110-111`) restarts for each new presentation (`display.py:357`). Nothing counts rejections, so on 2026-11-18 the board stayed stuck for >24 h with 1,143 rejection warnings.
- `BoardTransport` has `link_state`, `connections`, `connect`, `push_frame`, `close` and no reset (`src/spotify_to_led/board/transport.py:20-42`).
- The library has `client.reset()` → `common.reset()` → one write-with-response of `RESET` without waiting for a device ack (`client.py:258-262`, `modules/common.py:162-176`), and `client.materials.clear()` → `DELETE_MATERIALS` for all 12 slots, acknowledged (`modules/materials.py:42-56`).
- On 2026-11-19: clearing slots and GIF storage did not fix the board; RESET did; the link was still up ~2 s later and the next push succeeded (change.md experiment).
- `/status` reports `DisplayStatus{presentation, push_failed}` via `_board_json` (`src/spotify_to_led/main.py:634-657`); the page renders it in `renderBoard` (`src/spotify_to_led/static/index.html:135-154`). `POST /regenerate` is the pattern for a JSON-only owner action (`main.py:478-516`, button `index.html:334-360`).

## Desired End State

- Three rejected uploads in a row (counted across presentation changes, cleared by any successful push) make the display loop clear the board's storage and reset it, provided no reset happened in the last hour; the push is retried immediately afterwards.
- `POST /board/reset` (empty JSON body) asks the display loop to do the same on its next sample, ignoring the hourly limit; the status page has a "Reset board" button for it.
- `/status` → `board.last_reset_at` is the ISO time of the last reset attempt in this process lifetime, or `null`; the page shows it.
- Logs: WARNING when a reset starts (automatic with the rejection count, or manual), WARNING if a step fails, INFO on the first successful push after it.
- `scripts/clear_board.py` is gone; CLAUDE.md and the PRD describe the recovery.

Verify with `uv run pytest` and, on the Pi, pressing "Reset board" and reading `pm2 logs`.

### Key Discoveries:

- Rejection vs ack timeout is distinguishable only by message text (`connection_manager.py:420,424`); safe because the library is pinned to exactly 0.3.0 (`pyproject.toml:10`).
- A reset may leave the link up (observed once), so redraw-on-reconnect (`display.py:335`, grown `connections`) cannot be relied on; the loop must make the retry due itself.
- `tests/test_app.py:366` asserts the exact board key set; it changes.
- `FakeBoard` (`tests/test_display.py:137-184`) fails pushes with `OSError` only; it needs a rejection mode and a reset counter.

## What We're NOT Doing

- No reset on ack timeouts, `BleakGATTProtocolError`, other push errors or link drops — only on the device's explicit rejection.
- No persistence of reset history; `last_reset_at` is in memory and forgotten on restart.
- No reset count or reset outcome field in `/status` (logs carry the outcome).
- No change to FR-018's staleness rule or the push retry backoff values.
- No keeping of `scripts/clear_board.py`; the status page button is the manual tool.
- No library upgrade.

## Implementation Approach

Display decides *when*, transport decides *how*. The adapter translates the library's rejection into a project-level `BoardRejectedUpload` and implements `reset()` (clear storage, then RESET, each bounded). `run_display` counts consecutive `BoardRejectedUpload`s, owns the cooldown and the manual request flag, and records `last_reset_at` on `DisplayStatus`, which the route and page expose.

## Critical Implementation Details

- **State sequencing**: run a reset (automatic or manual) inside the loop's sample, before deciding whether a push is due, so only the loop ever talks to the board; after it, set the retry due immediately and keep `retry_delay` at its initial value so a still-rejecting board falls back onto the normal backoff.
- **Timing**: the storage clear failing or timing out must not stop the RESET; the RESET failing must not escape the loop. Both use the adapter's bounded-wait pattern (`idotmatrix_transport.py:36-38`).

## Phase 1: Transport — rejection error and reset

### Overview

Give the board boundary a typed rejection and a reset operation, so the display loop never imports idotmatrix.

### Changes Required:

#### 1. Board contract

**File**: `src/spotify_to_led/board/transport.py`

**Intent**: Declare the rejection the display loop reacts to and the reset operation every transport offers.

**Contract**: `class BoardRejectedUpload(Exception)` — the board refused a frame upload (as opposed to not answering or the link failing). `BoardTransport` gains `async def reset(self) -> None`: clear the board's stored materials, then reset it; may raise; must not leave the transport unusable. Update the protocol docstring.

#### 2. iDotMatrix adapter

**File**: `src/spotify_to_led/board/idotmatrix_transport.py`

**Intent**: Translate the library's rejection into `BoardRejectedUpload` and implement `reset()` with bounded waits.

**Contract**:
- `push_frame`: catch `idotmatrix.connection_manager.UploadError`; if its message contains `"rejected the upload"` raise `BoardRejectedUpload(str(error)) from error`, else re-raise unchanged. Comment that this message match is pinned to 0.3.0 like `_discard_stale_link`.
- `reset()`: `await self._client.materials.clear()` under a timeout (`RESET_STEP_TIMEOUT_SECONDS = 10.0`); any exception or timeout there is logged at WARNING and swallowed; then `await self._client.reset()` under the same timeout, errors propagated to the caller.

#### 3. Stub transport

**File**: `src/spotify_to_led/board/stub.py`

**Intent**: Satisfy the protocol.

**Contract**: `async def reset(self) -> None` that does nothing to the PNG (log at INFO).

#### 4. Tests

**File**: `tests/test_idotmatrix_transport.py`, `tests/test_stub_transport.py`

**Intent**: Prove the translation and reset ordering with a duck-typed fake client (existing pattern: `transport._client = …`, `test_idotmatrix_transport.py:44,199`).

**Contract**: tests that (a) a rejection `UploadError` becomes `BoardRejectedUpload`; (b) an ack-timeout `UploadError` and an `OSError` pass through unchanged; (c) `reset()` calls `materials.clear()` then `reset()` in that order; (d) a raising or hanging `materials.clear()` still leads to `reset()`; (e) a raising `reset()` propagates; (f) stub `reset()` is a no-op.

### Success Criteria:

#### Automated Verification:

- Transport tests pass: `uv run pytest tests/test_idotmatrix_transport.py tests/test_stub_transport.py`
- Full suite passes: `uv run pytest`

**Implementation Note**: After completing this phase and all automated verification passes, pause here for confirmation before proceeding to the next phase.

---

## Phase 2: Display loop — automatic and manual reset

### Overview

Count rejections, reset with a cooldown, honour a manual request, retry immediately, and record the reset time.

### Changes Required:

#### 1. Display status

**File**: `src/spotify_to_led/display.py`

**Intent**: Carry the last reset time out and the manual request in.

**Contract**: `DisplayStatus` gains `last_reset_at: datetime | None = None` (written by the loop) and `reset_requested: bool = False` (set by the route, cleared by the loop). Update the docstring: `reset_requested` is the one field a route writes; still one event loop, no lock.

#### 2. Reset policy in `run_display`

**File**: `src/spotify_to_led/display.py`

**Intent**: Reset a board stuck rejecting uploads, at most hourly unless the owner asks.

**Contract**:
- Constants: `RESETS_AFTER_REJECTIONS = 3`, `RESET_COOLDOWN_SECONDS = 3600`.
- `_push` returns the outcome so the loop can tell a `BoardRejectedUpload` from other failures (e.g. return the exception or `None`); logging unchanged.
- A loop-local `rejections` counter: +1 per `BoardRejectedUpload`, reset to 0 on any successful push; other failures leave it unchanged; persists across presentation changes and reconnects.
- Each sample (after the link check, before choosing whether to push): reset when `status.reset_requested`, or when `rejections >= 3` and (`last_reset_at is None` or `clock() - last_reset_at >= cooldown`). A reset: log WARNING ("resetting the board after N rejected uploads" / "resetting the board on the owner's request"), set `status.last_reset_at = clock()`, clear `reset_requested`, zero `rejections`, `await transport.reset()` with exceptions logged at WARNING (never escaping), then make the push due now (`retry_in = 0`, `push_failed` path or force via `attempted = None`-equivalent) with `retry_delay` at its initial value.
- After a reset, the first successful push logs INFO "board accepted a frame again after the reset".
- Below the threshold or inside the cooldown, behaviour is today's retry/backoff unchanged.

#### 3. Tests

**File**: `tests/test_display.py`

**Intent**: Drive the policy with the fake board and injected clock/sleep.

**Contract**: `FakeBoard` gains `rejecting_pushes: int` (pushes raising `BoardRejectedUpload`) and `resets: int` (+ optional `failing_resets`). Tests:
- 3 rejections in a row reset once, then the frame is pushed again immediately and `last_reset_at` equals the clock.
- 2 rejections, a success, 2 rejections → no reset.
- Rejections spanning a presentation change (2 on A, 1 on B) → reset.
- `OSError` push failures never reset, and do not clear the count.
- A board still rejecting after a reset is not reset again before 3600 s on the clock, and is reset again after.
- `reset_requested` triggers a reset on the next sample even inside the cooldown and with 0 rejections, and is cleared.
- A raising `transport.reset()` is logged, keeps the loop running, still records `last_reset_at`.

### Success Criteria:

#### Automated Verification:

- Display tests pass: `uv run pytest tests/test_display.py`
- Full suite passes: `uv run pytest`

**Implementation Note**: After completing this phase and all automated verification passes, pause here for confirmation before proceeding to the next phase.

---

## Phase 3: Status, manual trigger and docs

### Overview

Expose the reset time, add the owner's manual trigger, drop the script, document, and verify on the Pi.

### Changes Required:

#### 1. `/status` and the reset route

**File**: `src/spotify_to_led/main.py`

**Intent**: Report the last reset and let the owner request one.

**Contract**:
- `_board_json` adds `"last_reset_at"`: ISO 8601 UTC string or `None`.
- `class BoardResetRequest(BaseModel)` with `extra="forbid"` (empty JSON object, like `RegenerateRequest`).
- `@app.post("/board/reset", status_code=202)`: 409 `"The board link is down."` when the display task has ended or `link_state` is DOWN; 409 `"A reset is already pending."` when `reset_requested` is set; otherwise set `display_status.reset_requested = True` and return `{"reset_requested": true}`. Non-JSON bodies are 422 as for `/regenerate`. Update the module docstring's route list.

#### 2. Status page

**File**: `src/spotify_to_led/static/index.html`

**Intent**: Show the last reset and offer the button.

**Contract**: in `renderBoard`, when `board.last_reset_at` is set, a line "Board reset at <local time>." A "Reset board" button (class `action`) in the board section posting `{}` as JSON to `/board/reset`, disabled while a request is in flight or the link is down, showing the 409 text on refusal — following the regenerate button's pattern (`index.html:334-360`).

#### 3. Remove the script

**File**: `scripts/clear_board.py`

**Intent**: The button replaces it.

**Contract**: delete the (uncommitted) file.

#### 4. Docs

**File**: `CLAUDE.md`, `context/foundation/prd.md`

**Intent**: Keep the project description and requirements true.

**Contract**: CLAUDE.md overview: the display loop resets a board that rejects 3 uploads in a row (storage clear + RESET, at most hourly), and the route list gains `POST /board/reset` (JSON only). PRD: an "Updated 2026-11-19 (clear-storage)" note under FR-013 describing automatic and manual board reset and `last_reset_at` on the status page.

#### 5. Tests

**File**: `tests/test_app.py`

**Intent**: Cover the new field and route.

**Contract**: update the key-set assertion at `tests/test_app.py:366` to include `last_reset_at`; `last_reset_at` serialises from a `DisplayStatus` (pattern at `tests/test_app.py:1479`); `POST /board/reset` → 202 and sets the flag; 409 when link down, when the task has ended, when already pending; 422 for a non-JSON or non-empty body; the page posts to `/board/reset` (pattern `test_the_status_page_regenerates_through_post_regenerate`, `tests/test_app.py:1687`).

### Success Criteria:

#### Automated Verification:

- App tests pass: `uv run pytest tests/test_app.py`
- Full suite passes: `uv run pytest`
- The script is gone: `test ! -e scripts/clear_board.py`

#### Manual Verification:

- After deploying to the Pi (`PI_HOST=pi scripts/deploy.sh`), pressing "Reset board" on the status page logs the manual reset WARNING in `pm2 logs spotify-to-led`, the board shows the current cover again within about a minute, and the page shows "Board reset at …".
- The status page shows no reset line on a fresh start and the button is disabled while the link is down.

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful.

---

## Testing Strategy

### Unit Tests:

- Transport: rejection translation, pass-through of other errors, reset ordering and bounds.
- Display: threshold across presentation changes, success clears the count, non-rejection failures neutral, cooldown on the injected clock, manual bypass, failing reset contained, immediate retry.
- App: `/status` field, route acceptance/refusals/422, page wiring.

### Integration Tests:

- None beyond the FastAPI test client; the stuck board state cannot be induced on demand.

### Manual Testing Steps:

1. Deploy to the Pi.
2. Press "Reset board"; read `pm2 logs spotify-to-led --lines 50 --nostream` for the reset WARNING and the redraw.
3. Confirm the page shows the reset time and the board shows the current cover.

## Performance Considerations

Negligible: one counter compare per sample; a reset blocks the loop for at most 2 × 10 s.

## Migration Notes

None. `last_reset_at` is additive in `/status`; no persisted state changes.

## References

- Research: `context/changes/clear-storage/research.md`
- Change notes and 2026-11-19 experiment: `context/changes/clear-storage/change.md`
- Push retry decision: `context/archive/2026-11-09-artwork-on-the-board/reviews/impl-review.md:24-41`
- JSON-only owner action pattern: `src/spotify_to_led/main.py:478-516`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Transport — rejection error and reset

#### Automated

- [x] 1.1 Transport tests pass: `uv run pytest tests/test_idotmatrix_transport.py tests/test_stub_transport.py` — bd87375
- [x] 1.2 Full suite passes: `uv run pytest` — bd87375

### Phase 2: Display loop — automatic and manual reset

#### Automated

- [x] 2.1 Display tests pass: `uv run pytest tests/test_display.py` — 3c50be1
- [x] 2.2 Full suite passes: `uv run pytest` — 3c50be1

### Phase 3: Status, manual trigger and docs

#### Automated

- [x] 3.1 App tests pass: `uv run pytest tests/test_app.py`
- [x] 3.2 Full suite passes: `uv run pytest`
- [x] 3.3 The script is gone: `test ! -e scripts/clear_board.py`

#### Manual

- [ ] 3.4 After deploying to the Pi (`PI_HOST=pi scripts/deploy.sh`), pressing "Reset board" on the status page logs the manual reset WARNING in `pm2 logs spotify-to-led`, the board shows the current cover again within about a minute, and the page shows "Board reset at …".
- [ ] 3.5 The status page shows no reset line on a fresh start and the button is disabled while the link is down.
