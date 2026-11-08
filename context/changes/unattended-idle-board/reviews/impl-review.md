<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Unattended idle board (S-02)

- **Plan**: context/changes/unattended-idle-board/plan.md
- **Scope**: Full plan
- **Reviewed phases**: 1, 2, 3
- **Date**: 2026-11-08
- **Verdict**: APPROVED
- **Findings**: 0 critical, 1 warning, 6 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

Success criteria re-run on 2026-11-08: phase 1 tests 39 passed, phase 2 tests 35 passed, full suite 169 passed and 1 skipped. Criterion 3.1 was checked by `/healthz` on the Pi (answers, checkout at `4fb1b16`) rather than by re-running `deploy.sh`, which would restart the 7-day run. Every manual row has an owner observation recorded in `verification.md`.

Scope: the only change outside the plan is the owner-approved fix `4fb1b16` (`idotmatrix_transport.py` and its test), documented in `verification.md` Phase 3.4. Two unlisted tests (an error in a sample is survived; a board that comes up later shows idle) and the roadmap status bookkeeping are aligned with the plan's intent.

## Findings

### F1 -- The stale-link trap can still strike after a later drop, and the docstring says otherwise

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM -- real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/board/idotmatrix_transport.py:7-12, :88-101
- **Detail**: `_discard_stale_link` runs only on `connect` failures from the display loop, which never calls `connect` again after the first success. After a drop BlueZ reports (board powered off), the library's `_reconnect_loop` retries with the same `BleakTransport` (`connection_manager.py:597-613, 685-701`). bleak resets `_device_path` only in `__init__` and `unpair` (`bluezdbus/client.py:82, :515`). If BlueZ has dropped the device object by then, every reconnect fails instantly, as in run B before `4fb1b16`. The library resets the client itself only on a heartbeat-detected dead link (`_close_dead_link`). The F-01 power-cycle of about 30 s did recover, so it likely depends on how long the board is gone. The module docstring still says auto-reconnect plus heartbeat "covers a link that drops after a successful connection", which contradicts the S-03 follow-up in `verification.md`.
- **Fix A ⭐ Recommended**: Correct the docstring now, and note in `verification.md` that a long board outage during the 7-day run is expected to need a restart (an S-03 gap, not a new bug); leave the code fix to S-03
  - Strength: No change to the running build, so the 7-day clock keeps running; S-03 already owns drops and their physical tests.
  - Tradeoff: The 7-day run stays exposed to a board power-cycle until S-03.
  - Confidence: HIGH -- the gap is verified in bleak's and the library's source.
  - Blind spot: How long BlueZ keeps an unpaired device object before removing it is not measured.
- **Fix B**: Also reset the transport from `_on_disconnected` now
  - Strength: Closes the gap before the 7-day run is exposed to it.
  - Tradeoff: Pulls S-03 scope forward with no physical drop test here, and a redeploy restarts the 7-day clock.
  - Confidence: MEDIUM -- a reset from the disconnect callback races the library's own reconnect task.
  - Blind spot: Interaction with `_reconnect_loop` holding the old transport is unverified.
- **Decision**: FIXED via Fix B (reset on disconnect, guarded; docstring corrected). Redeploy restarts the 7-day run.

### F2 -- The fix's test does not prove the retry reaches a fresh link

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: tests/test_idotmatrix_transport.py:63-76
- **Detail**: The test asserts that the private `_transport` field is cleared and then calls `_create_transport()` directly. It never retries `transport.connect()`. If a library bump renamed the field, the test would plant an unused attribute and the real `BleakTransport` would try a real BLE connect, failing slowly or confusingly.
- **Fix**: Inject a second fake link through `manager._transport_factory` and assert that a second `transport.connect()` reaches it.
- **Decision**: FIXED as part of F1 (the test now retries connect through `_transport_factory`).

### F3 -- Cancellation while a connect is in flight is untested

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: tests/test_display.py:249-262
- **Detail**: The cancellation test cancels during the retry sleep. Shutdown while `transport.connect()` itself is pending (the pm2-stop case) is covered only by reading the code (`except Exception` does not catch `CancelledError`), not by a test.
- **Fix**: Add a test with a fake whose `connect` awaits an Event; cancel the task and assert it ends with `CancelledError` and logs no warning.
- **Decision**: FIXED (test_cancelling_while_a_connect_is_in_flight_ends_the_task_without_a_warning; fails in 2 s, not hangs, if cancellation is swallowed).

### F4 -- The placeholder can claim "playing" for days while reads fail

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/display.py:15-16, :75-76
- **Detail**: `failure` is ignored by design (S-06), so if reads fail while the status is PLAYING or PAUSED, the triangle stays up indefinitely. The placeholder is not a cover, so this does not breach the FR-018 hard rule today. Once S-04 puts real covers behind the same rule, the same path would show an unmarked stale cover.
- **Fix**: Record in the follow-ups that S-06 must land with or before S-04 (or that S-04 must handle `failure`), so a real cover is never shown unmarked through this path.
- **Decision**: FIXED (constraint recorded in follow-ups/review-fixes.md and in the roadmap under S-04's Risk).

### F5 -- The log-volume estimate leaves out the library's own line

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/spotify_to_led/display.py docstring ("about 1,500 lines"); plan Critical Implementation Details
- **Detail**: The library also logs INFO `connecting to <board address>...` on every attempt, so the real count is about double. Against the 30 s scan per attempt (about 90 s per attempt at the ceiling) the total is still about 1,900 lines a day, well inside pm2-logrotate's 10 MB.
- **Fix**: Reword the docstring to say each attempt costs two lines and roughly 2,000 lines a day with the board off.
- **Decision**: FIXED (display.py docstring: two lines per attempt, about 2,000 a day).

### F6 -- verification.md cites a plan section that does not exist

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: context/changes/unattended-idle-board/verification.md:11
- **Detail**: It cites "(plan, Open Risks)". That assumption is in `plan-brief.md`'s "Open Risks & Assumptions", not in `plan.md`.
- **Fix**: Change the citation to `plan-brief.md`, Open Risks & Assumptions.
- **Decision**: FIXED (citation now points at plan-brief.md, Open Risks & Assumptions).

### F7 -- The private reset does not check whether a link is up

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/board/idotmatrix_transport.py:101
- **Detail**: Safe in 0.3.0. `_connected` is set only after `transport.connect()` succeeds, and bleak's failed connect unwinds its own D-Bus state. But if a future library version raised after the link was up, the reset would orphan a live client and its heartbeat.
- **Fix**: Clear the field only when the manager does not report the link as connected (`if not manager.is_connected()`).
- **Decision**: FIXED as part of F1 (the `is_connected` guard is required for the disconnect reset).
