<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Staleness marker completes the display rule

- **Plan**: context/changes/staleness-marker-and-display-rule/plan.md
- **Scope**: Full plan
- **Reviewed phases**: 1, 2
- **Date**: 2026-11-11
- **Verdict**: APPROVED
- **Findings**: 0 critical, 2 warnings, 3 observations

Phase 1 has one unchecked Manual row (1.3, skipped by the owner); its code landed in `041bc76` and
is reviewed here. Automated criteria re-run: `uv run pytest` 361 passed, 1 skipped (362 passed on
the Pi); `git grep -n -E "never.marked" -- src tests` returns nothing.

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | WARNING |

## Findings

### F1 — OQ4 legibility wording overstates the evidence

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: context/foundation/prd.md:304, context/foundation/roadmap.md:391-396, context/foundation/roadmap.md:500-502
- **Detail**: The owner judged the marker over a cover in a lit room (window 1) and over idle in a lit and a dark room (window 2). The PRD OQ4 note, the roadmap S-06 Unknown and roadmap Open Question 4 say "over a cover and over idle, in a lit room and a dark one", which reads as if the cover was also judged in the dark. `verification.md` states it precisely. Progress row 2.5's title asks for both in both conditions; the dark-room cover case is the gap.
- **Fix**: Reword the three notes to "over a cover in a lit room, and over idle in a lit room and a dark one", matching `verification.md`.
- **Decision**: FIXED — the three notes reworded to match `verification.md`

### F2 — Roadmap S-06 prose still describes the old behaviour

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: context/foundation/roadmap.md:411-413 (also :373-375 and the At-a-glance row :79)
- **Detail**: The S-06 Risk paragraph still says, in the present tense, that "the placeholder stays up unmarked however long reads fail (S-04 review, F7)", which this change made untrue. The Outcome and the At-a-glance row still say "vouch for the cover it is showing … a marker appears on it", narrower than what was delivered. The plan did not list these lines, so this is doc drift, not missing work.
- **Fix**: Add "Resolved 2026-11-10: the placeholder now carries the marker like every presentation." after the F7 sentence, and widen the Outcome to "the board can no longer vouch for what it is showing".
- **Decision**: FIXED — F7 sentence marked resolved (with the outage test), Outcome and At-a-glance row widened to "what it is showing"

### F3 — Not everything was exercised on hardware

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: context/changes/staleness-marker-and-display-rule/plan.md (Progress 1.3); verification.md
- **Detail**: The laptop stub check (1.3) was skipped by the owner, and the marker over the placeholder and the disc was not seen on the board. Both are recorded openly in `verification.md`, the rule is break-checked by unit and loop tests, and the footprint test proves the marker clears both glyphs at 16/32/64 px.
- **Fix**: None required; leave 1.3 unchecked for `/10x-archive` to surface.
- **Decision**: SKIPPED — accepted as recorded in `verification.md`

### F4 — Idle staleness tests use only the network failure kind

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: tests/test_display.py:251
- **Detail**: The old idle test had an `AUTHORIZATION_NEEDED` row (failing for 1 h); it moved into the new staleness test as `NETWORK` only. `_is_stale` ignores the failure kind, so behaviour is covered, but the unauthorized cold start is the most likely long blind period in practice (US-02).
- **Fix**: Add one `AUTHORIZATION_NEEDED` case to `test_idle_is_stale_only_after_more_than_30_s_of_failing_reads`.
- **Decision**: FIXED — the test is parametrized over `NETWORK` and `AUTHORIZATION_NEEDED`

### F5 — A reader that neither reads nor fails is never marked

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/display.py:184-190 (`_is_stale`, unchanged)
- **Detail**: A snapshot with `failure_since` and `last_read_at` both `None` is never stale, so idle would stay unmarked if the reader stalled before its first read without recording a failure. The plan chose this deliberately ("a slow but healthy start is not marked"), and `SpotifyReader.run` catches every exception into a failure and only ends on cancellation (`spotify/reader.py:148-159`), with a 5 s timeout per request, so the case is not reachable in practice.
- **Fix**: None required; revisit only if the reader ever gains a path that can block before its first tick.
- **Decision**: SKIPPED — accepted as designed
