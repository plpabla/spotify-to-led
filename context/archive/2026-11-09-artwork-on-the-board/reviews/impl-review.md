<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Artwork on the Board (S-04)

- **Plan**: context/changes/artwork-on-the-board/plan.md
- **Scope**: Full plan (phases 3 and 4 each keep one manual row open, 3.5 and 4.5, waived by the owner)
- **Reviewed phases**: 1, 2, 3, 4
- **Date**: 2026-11-09
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical, 4 warnings, 5 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | WARNING |

## Findings

### F1 — A failed push of the stale marker (or a new cover) is never retried

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/display.py:223-226
- **Detail**: `attempted` is recorded before the push, and a failed `push_frame` (a GATT write error or timeout) leaves `link_state` UP. Every later sample then sees `wanted == attempted` and pushes nothing until the presentation changes or the link drops. When the failed push was `COVER x (stale)`, the board keeps the unmarked cover for the whole outage. When it was a new item's cover, the previous item's cover stays up, unmarked, while a different item plays. Both break the hard rule. "A failed push is not retried" was an accepted boundary in S-02, when only idle and the placeholder existed; the marker makes it load-bearing. No test covers it.
- **Fix A ⭐ Recommended**: Record whether the last push succeeded, and retry a failed push on later samples with a small backoff (e.g. the sample interval doubling to the 60 s cap).
  - Strength: Closes the gap for the marker and for a new cover with one loop-local change; testable with the existing FakeBoard `failing_pushes`.
  - Tradeoff: Repeated writes to a board in a bad state; the backoff bounds them.
  - Confidence: HIGH — the redraw machinery already exists; only the "succeeded" bit is missing.
  - Blind spot: Whether a failed push on this board ever leaves the link genuinely up, rather than also dropping it (not observed on hardware yet).
- **Fix B**: Treat a failed push as a lost link (the transport marks DOWN and discards the client), so the existing reconnect path redraws.
  - Strength: Reuses the reconnect-and-redraw path S-03 already proved on hardware.
  - Tradeoff: A transient write error costs a full reconnect (a scan of up to about 30 s), and the transport changes for a display concern.
  - Confidence: MED — depends on how the library reacts to a forced reconnect mid-session.
  - Blind spot: Interaction with the library's heartbeat and listener.
- **Decision**: FIXED (Fix A) — display.py retries a failed push 1 s doubling to 60 s while the presentation stays wanted; tests cover the retry backoff, a new presentation pushed at once, and a failed stale-marker push being retried

### F2 — Staleness depends only on `failure_since`, so a reader that stops reading leaves the cover unmarked

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/display.py:165-168
- **Detail**: `_is_stale` reads only `snapshot.failure_since`. If the reader task ever ends, or stops ticking without calling `_fail`, the snapshot freezes at its last success with `failure_since=None`, and the cover is shown as current forever. Today the only exit is an exception from the sleep inside `_pause` (`spotify/reader.py:159, 264-268`), which `asyncio.sleep` does not raise, so it is unlikely but unguarded. The comparison also uses wall-clock UTC on a Pi without an RTC, so a backward clock step delays the marker by the size of the step.
- **Fix**: Judge staleness by the age of the last successful read (`now - last_read_at > 30 s`, keeping `failure_since` as a fast path), which covers both "reads failing" and "reads not happening".
  - Strength: The marker follows what the hard rule actually means, "can the service vouch for it", regardless of how reading stops.
  - Tradeoff: Changes the rule's input and its tests; while reads are healthy, `last_read_at` refreshes every 3 s, so nothing visible changes then.
  - Confidence: MED — needs checking that `last_read_at` is set on every successful read, including `nothing_playing`.
  - Blind spot: The reader's back-off on 429 (`Retry-After` up to an hour) is recorded as a failure, so this does not change that case.
- **Decision**: FIXED — `_is_stale` also marks a cover when the last successful read (`last_read_at`) is over 30 s old; rule tests at 29 s and 31 s; module docstring updated

### F3 — The fetch timeout is per network phase, not a total

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/covers.py:110
- **Detail**: `timeout=4.0` sets each of connect, read, write and pool to 4 s, and the read timeout restarts with every chunk, so a slow or trickling CDN can keep a URL pending well past 4 s. During that time the changeover keeps the previous item's cover up, unmarked, while reads succeed: longer than the sub-second window the plan accepted, and outside NFR-01's budget.
- **Fix**: Wrap the fetch in `asyncio.timeout(FETCH_TIMEOUT_SECONDS)` so 4 s is a real total.
- **Decision**: FIXED — `fetch_cover` wraps the request in `asyncio.timeout(FETCH_TIMEOUT_SECONDS)`, so 4 s bounds the whole download; test with a trickling server

### F4 — The PRD names a function that no longer exists

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: context/foundation/prd.md:170
- **Detail**: FR-007's decision note says the treatment is "`render_downscale` in `src/spotify_to_led/rendering.py`". Phase 2 renamed it `render_cover` (bfa4372), so this foundation doc now points at nothing.
- **Fix**: Change the name to `render_cover`.
- **Decision**: FIXED — FR-007's note now names `render_cover`

### F5 — The loader's state is not tied to its URL (defence in depth)

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/covers.py:192, 196
- **Detail**: `_load(url)` writes `self._state` without checking that `url` is still the wanted one; correctness relies on `task.cancel()` raising at the next await. That holds in CPython today. If a library ever swallowed the cancellation, the old cover would be retained under the new URL and not redrawn.
- **Fix**: Return from `_load` without writing when `self._url != url`.
- **Decision**: FIXED — `_load` returns without writing state when its URL is no longer the wanted one; test drives a download that completes after the URL changed

### F6 — No size cap on the downloaded body or the decoded image

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/covers.py:113
- **Detail**: `response.content` buffers the whole body, and Pillow only raises above about 179 MP; decode and render run on the event loop. Negligible for Spotify's 64 px CDN images, which arrive over TLS from Spotify's own API, but unbounded in principle.
- **Fix**: Reject bodies over a small limit (e.g. 2 MB) and set a low `Image.MAX_IMAGE_PIXELS` for cover decoding.
- **Decision**: FIXED — `fetch_cover` rejects bodies over `MAX_COVER_BYTES` (2 MB) and images over `MAX_COVER_PIXELS` (4096x4096) before decoding; tests for both limits

### F7 — The placeholder is never marked while the service is blind

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/display.py:147-148, 156-157
- **Detail**: For an item without artwork, or whose download failed, the placeholder ("something is playing") stays up unmarked even after reads have failed for hours. That is outside FR-018's literal cover rule, and it is the blind-without-a-cover case S-06 owns (OQ2).
- **Fix**: None in S-04; note it in S-06's scope when it is planned.
- **Decision**: FIXED (noted) — roadmap S-06 now says its blind case includes the unmarked placeholder

### F8 — The rule's docstring implies the placeholder after idle; the code keeps idle

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: src/spotify_to_led/display.py:155
- **Detail**: When playback starts from idle, a pending cover keeps idle (the previous presentation), not the placeholder. That is deliberate and tested (`tests/test_display.py:269`), but the comment and module docstring read as if "nothing shown yet" means the placeholder.
- **Fix**: Reword the comment and docstring to say a pending cover keeps whatever was chosen last, idle included.
- **Decision**: FIXED — the display docstring and the changeover comment say a pending cover keeps whatever was chosen last, idle included

### F9 — The offline-marker check never ran on hardware

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: plan.md Progress 3.5, 4.5
- **Detail**: The marker's trigger is covered by unit and loop tests (break-checked) and its look was judged on the board, but the Pi never lost Spotify (Ethernet unplugged, Wi-Fi stayed up). The owner chose to close without it; the roadmap carries it to S-06. Separately, 3.4 was run on the Pi rather than the laptop stub, which is a stronger check.
- **Fix**: None now; S-06 runs the outage with Wi-Fi off too.
- **Decision**: ACCEPTED — the owner chose to close S-04 without the hardware outage test (the Pi stayed online over Wi-Fi); carried to S-06 in the roadmap
