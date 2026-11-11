<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Status and Re-authorization Page (S-07)

- **Plan**: context/changes/status-and-reauthorization-page/plan.md
- **Scope**: Full plan
- **Reviewed phases**: 1, 2, 3
- **Date**: 2026-11-11
- **Verdict**: APPROVED
- **Findings**: 0 critical, 1 warning, 4 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Findings

### F1 — The page decides staleness by a different rule from the board

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/static/index.html:99-112 (vs src/spotify_to_led/display.py:197)
- **Detail**: The "Reading Spotify" section calls the board stale only when `now - failure_since > 30 s`. `display._is_stale` also marks the board when `last_read_at` is older than 30 s. In a normal outage, `failure_since` is only set once the first read fails, about 8 s after the last good read (3 s poll plus a 5 s timeout). So the board is marked about 8 s before the page says it is. If reads stop without failing (the reader task ends or hangs), the page shows "Healthy." in green while the board carries the marker. The Board section, which reads `presentation.stale`, is correct, so the two sections contradict each other on the one page meant to explain the marker.
- **Fix A ⭐ Recommended**: Compute staleness on the server and render it
  - Approach: make `_is_stale` public in `display.py`, add `playback.stale` to `/status`, have the page use it, and say "no read for N s" instead of "Healthy." when reads are stale without a failure.
  - Strength: One definition of FR-018's rule, already tested in `test_display.py`. The page can no longer drift from the board.
  - Tradeoff: Adds one field to `/status` and widens `display.py`'s public surface slightly.
  - Confidence: HIGH — `_is_stale` is pure and takes exactly the snapshot and clock `/status` already has.
  - Blind spot: None significant.
- **Fix B**: Word the Reading section from `board.presentation.stale`
  - Approach: no server change; the page reuses the Board section's flag.
  - Strength: Smallest diff.
  - Tradeoff: It reflects the frame last attempted, which can lag the believed state by a sample, and it does not fix "Healthy." during stalled reads.
  - Confidence: MED — it covers the outage case but not stalled reads.
  - Blind spot: How it reads while the board link is down and no push happens.
- **Decision**: FIXED (Fix A) — `display.is_stale` made public; `/status` reports `playback.stale`; the page uses it and says "No good read for N s" instead of "Healthy." when reads stall; parametrized test, break-checked.

### F2 — "The service is not answering" also covers an expired Access sign-in

- **Severity**: 🔍 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/static/index.html:179-182
- **Detail**: When the Cloudflare Access session expires, `fetch("/status")` is redirected cross-origin to the login page and throws. The owner then reads "not answering" while the Pi is fine, and a reload would fix it. A rendering error lands in the same branch.
- **Fix**: Extend the message: "…, or your Cloudflare Access sign-in has expired; reload the page."
- **Decision**: FIXED — the not-answering alert now adds that an expired Cloudflare Access sign-in may be the cause and to reload the page.

### F3 — The authorization notice returns on every reload

- **Severity**: 🔍 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/static/index.html:46-48
- **Detail**: `?authorized=1` stays in the URL, so a reload or a bookmark shows "authorization succeeded" again, possibly long after a later failure.
- **Fix**: After showing the notice, call `history.replaceState(null, "", "/")`.
- **Decision**: FIXED — `history.replaceState(null, "", "/")` after the notice is shown.

### F4 — If building the frame fails, the page reports it as sent

- **Severity**: 🔍 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/display.py:268-285
- **Detail**: `status.presentation` is set before `_frame_for` runs. If `_frame_for` raises (its "cannot happen" `RuntimeError`), the loop's `except` logs it, `push_failed` stays False, and the page says the frame was sent. The loop never retrying in that case predates this change.
- **Fix**: Set `status.push_failed = True` in the display loop's `except Exception` branch.
- **Decision**: FIXED — the display loop's catch-all sets `status.push_failed = True`.

### F5 — Two thin spots in the new tests

- **Severity**: 🔍 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: tests/test_app.py:401, tests/test_display.py:578
- **Detail**: Only the Spotify-error callback test asserts the "Status page: /" pointer; the other three error answers carry it untested. The `DisplayStatus` test records only an idle presentation, never a cover or a stale one.
- **Fix**: Assert the pointer in the other callback error tests, and add a case recording a stale cover.
- **Decision**: FIXED — the forged/missing-state and both 502 callback tests assert "Status page: /"; new test records a stale idle presentation in `DisplayStatus`; both break-checked.
