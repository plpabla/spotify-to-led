<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: AI-enhanced covers (S-08)

- **Plan**: context/changes/ai-enhanced-covers/plan.md
- **Scope**: Full plan
- **Reviewed phases**: 1, 2, 3, 4
- **Date**: 2026-11-11
- **Verdict**: NEEDS ATTENTION
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

Evidence: `uv run pytest` gives 538 passed, 1 skipped, and `tests/test_enhance.py` 49 passed. All 18 Progress rows are `[x]`, and the manual rows are backed by `verification.md`. Every planned file is in the diff `main..HEAD`. The only unplanned files are `rendering.py` and `test_rendering.py`, which hold `render_enhanced` (LANCZOS, chosen in the bake-off). Nothing leaks: the key is a `SecretStr`, scrubbed from errors and absent from `/status` and `PUT`; values reach the page via textContent; `PUT /settings` refuses non-JSON bodies, tested against FastAPI 0.162; the cache key cannot escape its directory; no Spotify token goes to OpenRouter.

## Findings

### F1 — A malformed OpenRouter answer kills the loader task, stranding the placeholder and paying again on every sighting

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/enhance.py:171 (`generate`), src/spotify_to_led/enhance.py:209-217 (`_decode_image`), src/spotify_to_led/covers.py:376 (`CoverLoader._enhance`)
- **Detail**:
  - Three gaps let an exception escape:
    - `_decode_image` catches only `(OSError, DecompressionBombError)`, but Pillow's `load()` also raises `SyntaxError`/`ValueError`. Reproduced in this review: a PNG with a corrupted second IDAT chunk escapes as `SyntaxError: broken PNG file`.
    - `generate` catches `httpx2.TransportError`, but `DecodingError` and `TooManyRedirects` are `RequestError` subclasses outside it (verified).
    - `Enhancer.enhance` and `CoverLoader._enhance` don't guard the call either.
  - The exception ends `_load`, and the state stays `GENERATING`. The board then shows the placeholder for the rest of the item, because `want` with the same wish is a no-op.
  - The image never enters the give-up memory, so every later sighting makes another paid call.
  - `FrameCache.load` already catches `(OSError, ValueError, SyntaxError, DecompressionBombError)` for the same reason.
- **Fix**: Widen `_decode_image` to `FrameCache.load`'s exception set, and map `httpx2.RequestError` (non-transport) after a response to `EnhanceFailed`. Add a backstop in `CoverLoader._enhance` that turns any unexpected exception into plain (logged once). Add a test with a corrupt PNG answer.
- **Decision**: FIXED. The backstop went into `Enhancer.enhance` rather than `CoverLoader._enhance`, because the give-up memory lives there, so an unexpected error also stops repeat spending. Tests added: damaged-chunk PNG, `DecodingError`, and an unexpected exception that gives up with no second call. Break-checked.

### F2 — Decoding and shrinking the model's answer runs on the event loop

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/enhance.py:206-215, src/spotify_to_led/enhance.py:356
- **Detail**: Each generated image is base64-decoded, run through `Image.open().load()` (about 1024 px) and through `render_enhanced`, all on the loop it shares with the display, BLE and `/status`. The cost is about 25 ms on a desktop, and estimated at 100–250 ms on the Pi (not measured). It happens once per generated image, never per sample. The Pi run showed no visible effect, but it contradicts `covers.py`'s claim that heavy work stays off the display loop.
- **Fix**: Wrap decode + `render_enhanced` in `asyncio.to_thread`, or note it as accepted in the `covers.py` docstring.
- **Decision**: FIXED. `generate` decodes via `asyncio.to_thread(_decode_image, ...)` and `Enhancer.enhance` renders via `asyncio.to_thread(render_enhanced, ...)`. The suite passed 3 runs in a row; no dedicated test, since thread placement is not behaviour.

### F3 — A cancelled or timed-out generation may still be billed

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/covers.py:239 (`want` → `_cancel`), src/spotify_to_led/enhance.py:338-352 (retry on timeout)
- **Detail**: A generation is lost when it's cancelled in flight: by skipping within the 9–20 s it takes, or by switching off. If OpenRouter finishes it anyway, it may still bill, nothing is cached, and the next sighting pays again. A 90 s timeout is retryable, so one image can cost up to 3 POSTs. This is by design and bounded, but nowhere in FR-019 is it written down.
- **Fix**: Add one sentence to FR-019's notes that abandoned generations may be billed and are not cached.
- **Decision**: FIXED. An "Accepted cost" note was added under FR-019 in `context/foundation/prd.md`.

### F4 — Minor contract and plan deviations not written back to the plan

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: src/spotify_to_led/main.py (`_enhanced_cover`), tests/test_display.py:1055, src/spotify_to_led/enhance.py:349-351
- **Detail**: The behaviour is tested or documented in code, but the plan's contract text doesn't match it:
  - `/status` `enhancement.cover` is also `null` when the switch is off, and while the plain cover is PENDING/FAILED. The plan says `null` only when no cover is wanted.
  - The flip-redraw integration test is in `test_display.py`, not `test_app.py`.
  - Each retry also logs an INFO line, though the plan says each outcome is logged once.
- **Fix**: Add a short "Deviations" note to `verification.md` listing these, so the plan stays a truthful reference.
- **Decision**: FIXED. A "Deviations from the plan" section was added to `verification.md`, covering these plus the agreed post-plan decisions and the review fixes.

### F5 — Bake-off judged 4 covers, not the planned 6–10

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: context/changes/ai-enhanced-covers/verification.md (Phase 1)
- **Detail**: Row 1.4 is ticked, but only 4 covers were judged (the proof-of-concept notebook set), with no dark/bright/text-heavy spread. `verification.md` records this honestly, and the owner judged the verdict clear. FR-019's bar ("every enhanced cover in the bake-off set") rests on that small set.
- **Fix**: Accept as recorded, or judge a few more varied covers on the board when convenient and append them to `verification.md`.
- **Decision**: ACCEPTED. The owner judged the verdict clear on the four covers, and the feature has run live on the board since.
