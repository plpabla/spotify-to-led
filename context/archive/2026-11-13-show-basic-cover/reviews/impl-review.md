<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Show the plain cover while the AI-enhanced version is generated

- **Plan**: context/changes/show-basic-cover/plan.md
- **Scope**: Full plan (Phase 2 reviewed with its manual item 2.4 still pending)
- **Reviewed phases**: 1, 2, 3
- **Date**: 2026-11-13
- **Verdict**: APPROVED
- **Findings**: 0 critical, 2 warnings, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | WARNING |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | WARNING |

Automated criteria re-run on 0ee1482: `tests/test_frames.py` 75 passed; display/loader/app 214 passed; full suite 574 passed, 1 skipped; `GENERATING` in `tests/test_display.py` only as the new `GENERATING_A` expectations.

## Findings

### F1 — Manual check 2.4 not done; status-page text untested

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: context/changes/show-basic-cover/plan.md:348
- **Detail**: The local stub-transport run (log lines and status-page text while generating) was skipped in favour of the board check, as recorded in verification.md. The log line and the `/status` field are covered by tests; the rendered page wording (`index.html:139`, `:184`) is not.
- **Fix**: While a cover generates on the Pi, glance at the status page's board and AI-enhanced lines, then tick 2.4.
- **Decision**: FIXED — the owner checked the status page on the Pi; 2.4 ticked

### F2 — Unplanned change to the test `until` helpers

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: tests/test_covers.py:116, tests/test_display.py:1107
- **Detail**: Both helpers moved from a fixed number of loop turns to a 5 s time bound, fixing flakiness that already existed on main (2 of 12 runs failed at HEAD), because generation decodes and renders in worker threads. Test-only and benign, recorded in commit e483147, but not in the plan. Also two small extras: `GENERATING_MARKER_INSET_PIXELS` (follows from dropping the ring) and `test_a_cover_being_generated_says_so_in_the_log`.
- **Fix**: Keep; no action beyond this record.
- **Decision**: ACCEPTED — keep the time-bounded helpers; no change

### F3 — Ringless dot can blend into green or bright corners

- **Severity**: 🔍 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/frames.py:84-86, :190-201
- **Detail**: Without the ring, the (0, 160, 0) dot can vanish on a cover that is green or bright in its top-left corner; on a 16 px board it is a single pixel. It carries no FR-018 weight (the plain cover under it is vouched for; staleness keeps its ringed marker), and the ringless look was the owner's choice on the real board.
- **Fix**: Leave as is; revisit the colour only if the dot proves hard to spot.
- **Decision**: ACCEPTED — leave the ringless dot and its colour; revisit only if it proves hard to spot
