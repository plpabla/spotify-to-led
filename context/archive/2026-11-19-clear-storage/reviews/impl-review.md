<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Board reset on repeated upload rejections

- **Plan**: context/changes/clear-storage/plan.md
- **Scope**: Full plan
- **Reviewed phases**: 1, 2, 3
- **Date**: 2026-11-19
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical, 2 warnings, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | WARNING |

Automated criteria re-run on 73725ad: transport tests 25 passed, display tests 135 passed, app tests 112 passed, full suite 773 passed / 2 skipped, `scripts/clear_board.py` absent. Every planned change verified MATCH; no unplanned files; all six "What We're NOT Doing" items respected.

## Findings

### F1 — A second click during an in-flight reset sends another reset

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/display.py:351 (with src/spotify_to_led/main.py:554-557)
- **Detail**: The loop clears `status.reset_requested` before awaiting `transport.reset()`, which can take up to 20 s (two 10 s steps). In that window `POST /board/reset` sees no pending reset and accepts a second request, so a second clear + RESET goes out right after the first.
- **Fix**: Clear `status.reset_requested` after `transport.reset()` returns (after the try/except), so the route's "A reset is already pending." covers the in-flight reset too.
- **Decision**: FIXED — reset_requested cleared after transport.reset() returns; test_a_reset_in_flight_still_counts_as_pending

### F2 — A failed or timed-out RESET does not discard a possibly stale link

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/board/idotmatrix_transport.py:151-165
- **Detail**: The library connects implicitly inside `send_*` when not connected; a 10 s timeout can cancel that connect midway, leaving the stale-BlueZ-path state `_discard_stale_link` exists to clear (`connect()` already discards on failure). The protocol docstring promises a raising reset leaves the transport usable. Research open question 4 left this unresolved.
- **Fix**: In `reset()`, when the RESET step raises (including timeout), call `self._discard_stale_link()` before re-raising, as `connect()` does.
  - Strength: Reuses the existing recovery primitive; `_discard_stale_link` is a no-op while the link is up, so a healthy link is untouched.
  - Tradeoff: One more pin-dependent private-field access path (already accepted for `connect`).
  - Confidence: HIGH — same pattern as `connect()` in the same class.
  - Blind spot: Whether RESET can actually hang on hardware is unobserved; on 2026-11-19 11:57:39 it completed in under 0.2 s.
- **Decision**: FIXED — reset() discards the stale link when the RESET step raises, then re-raises; asserted in test_a_failed_board_reset_reaches_the_caller

### F3 — "Accepted again after the reset" is logged after a reset that failed

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/display.py:359
- **Detail**: `reset_unconfirmed = True` is set even when `transport.reset()` raised, so the next landed push logs "board accepted a frame again after the reset" although no reset happened — misleading in `pm2 logs`, which are the evidence of unattended recovery.
- **Fix**: Set `reset_unconfirmed = True` only when `transport.reset()` returned without raising.
- **Decision**: FIXED — reset_unconfirmed armed only after a reset that did not raise; test_a_push_landing_after_a_failed_reset_does_not_claim_the_reset_worked

### F4 — The re-push may land before the board reboots

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/display.py:354-363
- **Detail**: The frame is re-pushed in the same sample as RESET, which is not acked. If the board rebooted a moment later without dropping the link it could stay blank. Not observed: the manual reset at 11:57:39 was followed by a landed push at 11:57:40, no link drop, and the cover stayed up.
- **Fix**: None now; watch `pm2 logs` the next time an automatic reset fires.
- **Decision**: ACCEPTED — not observed on hardware (11:57:39 reset kept link and cover); watch pm2 logs on the next automatic reset

### F5 — Manual check 3.5 ticked without observing the link-down state

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: context/changes/clear-storage/plan.md (Progress 3.5)
- **Detail**: "No reset line on a fresh start" was observed (`last_reset_at: null` after deploy). "Button disabled while the link is down" was not exercised on hardware; it is covered by the page logic (`board.link !== "up"`) but no app test asserts the disabled state.
- **Fix**: Accept as covered by code reading, or switch the board off once and look at the button.
- **Decision**: ACCEPTED — fresh-start half observed; link-down half covered by the page logic (button disabled unless board.link is "up")
