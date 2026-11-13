<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Select the AI enhancement model

- **Plan**: context/changes/select-model/plan.md
- **Scope**: Full plan
- **Reviewed phases**: 1, 2, 3, 4
- **Date**: 2026-11-13
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical, 1 warning, 4 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | WARNING |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS |

Plan adherence: no DRIFT, no MISSING. Benign EXTRA items: warn-once in models.py (agreed with the owner), loader-side pause/limit refusal, `_load(fallback=)` + `_unenhanced`, PUT /settings added to CLAUDE.md's route list, the separate button row, blurring the select after a save. "What We're NOT Doing" holds. Success criteria: `uv run pytest` 743 passed, 2 skipped; both greps clean; all 14 Progress rows ticked, with the manual ones confirmed by the owner on the hardware.

## Findings

### F1 — A very fast regeneration is never pushed to the board

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/display.py:331-345 (with src/spotify_to_led/covers.py:279-307)
- **Detail**: The display pushes only when `(presentation, connections)` changes, and `Presentation` holds no frame identity. A regeneration goes READY enhanced → GENERATING → READY enhanced for the same URL. If the whole regeneration finishes between two 1 s samples, the display never sees GENERATING, the presentation is equal, and the new frame (already in `retained` and the cache) is not pushed. The board keeps the old enhanced frame while /status says "enhanced". This is not a staleness breach, because both frames show the same item. It is unlikely at about 9 s per generation, more likely with turbo or flash models now in models.txt.
- **Fix A ⭐ Recommended**: Treat a new retained frame as a change in the display loop: mark `due` when `retained[2] is not` the frame last pushed for the same presentation, and add a display test where a regeneration completes between two samples.
  - Strength: Fixes it at the source; it covers any future path that swaps a frame under an unchanged presentation.
  - Tradeoff: Touches the display loop's push rule, which is the board's most-tested core.
  - Confidence: MED — the identity check is simple, but the loop has backoff/redraw interplay to respect.
  - Blind spot: Whether the cached-hit `want()` path hands out a fresh Image object each sample (it doesn't today, as the state is held).
- **Fix B**: Add a regeneration counter to `CoverState` and carry it into `Presentation`, so a regenerated cover is a different presentation.
  - Strength: Keeps the "push on presentation change" rule pure and visible in /status.
  - Tradeoff: Widens `Presentation` and its JSON for one rare case; more tests to adjust.
  - Confidence: MED — straightforward, but more files touched.
  - Blind spot: Effects on `select_presentation` equality used elsewhere.
- **Decision**: FIXED via Fix A (display.py pushes when the cover's retained frame changes; test_a_regeneration_finished_between_two_samples_draws_the_new_frame)

### F2 — Regenerate can bypass the daily limit when a model reports no cost

- **Severity**: 💡 OBSERVATION
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/enhance.py:442-455
- **Detail**: A success with no reported cost is logged with a WARNING and not added to today's spend. This hole predates the change, but each image used to be generated at most once. Now Regenerate can be pressed repeatedly on one cover, so a listed model that never reports cost is never stopped by the limit. Access is still gated by Cloudflare.
- **Fix**: Count an unreported-cost success at a nominal estimate (e.g. today's default model price) so the limit still advances.
  - Strength: Keeps the daily limit meaningful for every listed model.
  - Tradeoff: The spend shown becomes partly estimated; needs a constant and a doc line.
  - Confidence: MED — depends on how often listed models omit cost.
  - Blind spot: Haven't verified which of the listed models report cost.
- **Decision**: FIXED (enhance.py counts an unreported cost at UNREPORTED_COST_ESTIMATE_USD = $0.009; test updated; PRD note says so)

### F3 — Regenerate button lacks the poll guard the other controls use

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/spotify_to_led/static/index.html:214-216, 350
- **Detail**: Toggle, limit and model ignore polls fetched before their own action (`*ChangesAtFetch`). The button only has a `regenerating` flag, so a poll in flight during the POST can briefly re-enable it. A click in that window gets a harmless 409 that stays in the note.
- **Fix**: Add a `regenerateChanges` counter captured in `refresh()`, as for the other controls.
- **Decision**: FIXED (regenerateChanges counter captured in refresh(), as for the other controls)

### F4 — Regenerating with no plain frame on hand shows the placeholder meanwhile

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/covers.py:298-302
- **Detail**: When the plain frame is in neither memory nor the cache, regeneration goes PENDING and the board drops from the enhanced cover to the playing placeholder during the download. This is intended and documented in the module docstring, and rare.
- **Fix**: None needed; accept as documented.
- **Decision**: ACCEPTED (intended and documented in covers.py; rare)

### F5 — Uncommitted models.txt edit

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: models.txt
- **Detail**: The working tree adds `openai/gpt-image-5-mini` and `google/gemini-nano-banana-2.1`; not on the branch.
- **Fix**: Commit it to the branch (or drop it) before merging.
- **Decision**: FIXED (models.txt committed with the review fixes)
