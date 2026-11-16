<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: AI spend health bar

- **Plan**: context/changes/usage-progress-on-display/plan.md
- **Scope**: Full plan
- **Reviewed phases**: 1, 2
- **Date**: 2026-11-16
- **Verdict**: APPROVED
- **Findings**: 0 critical, 1 warning, 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | WARNING |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

Success criteria: `uv run pytest` gives 745 passed, 2 skipped. `uv run pytest tests/test_frames.py -k spend_bar` gives 46 passed. The grep for the old constant names finds nothing in `src` or `tests`. All five manual checks (1.4, 1.5, 2.3–2.5) were confirmed by the owner after a redeploy on the Pi.

## Findings

### F1 — display.py docstring still describes the bar as showing spend

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: src/spotify_to_led/display.py:28-30
- **Detail**: The module docstring says the generating cover carries "the spend bar along its bottom row: today's AI spend as a fraction of the owner's daily limit, as it stood before this generation". The bar now shows the budget left. Only its input is still the spend fraction. The plan updated the docstrings in `frames.py` but missed this one.
- **Fix**: Reword the sentence to say that the bar shows how much of the daily limit was still left before this generation, as a health bar.
- **Decision**: FIXED

### F2 — A limit saved earlier below a cent now fails the page's validation

- **Severity**: 💡 OBSERVATION
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Scope Discipline
- **Location**: src/spotify_to_led/static/index.html:40, :202-203; src/spotify_to_led/main.py:403-408
- **Detail**: The extra owner-requested change set the limit field to `min="0.01" step="0.01"`. `PUT /settings` still accepts any finite amount above 0, and the page still fills the field with `toFixed(3)` below $0.10. A limit saved earlier with sub-cent precision, such as 0.005 or 0.015, therefore loads as a value the browser rejects (below `min`, or off the step). Saving it again unchanged is blocked by native validation, which tells the owner the nearest valid values. Typing any whole-cent amount saves normally. Nothing is lost: the server keeps applying the stored limit. The comment at index.html:202 ("so a limit under $0.10 is not shown rounded") now applies only to those old values.
- **Fix A ⭐ Recommended**: Accept it, and update the index.html:202 comment to say that new limits are whole cents and that the three-decimal fill only exists so an old sub-cent limit is not shown rounded.
  - Strength: A one-line comment change. The owner's real limit is a whole-cent amount, and the browser already explains the problem if it ever happens.
  - Tradeoff: The page accepts less than the API does.
  - Confidence: HIGH — single-owner service, and the stored value is never changed silently.
  - Blind spot: The current value in `var/settings.json` on the Pi has not been checked.
- **Fix B**: Round the limit to whole cents on the server as well (the `PUT /settings` validator), with a minimum of $0.01.
  - Strength: The page and the server would apply the same rule.
  - Tradeoff: A server and API behaviour change outside this plan. It needs a PRD note under FR-019 ("always a positive amount") and new tests, and a stored 0.005 would still need migrating or clamping when loaded.
  - Confidence: MED — touches `owner_settings.py` loading as well.
  - Blind spot: Whether anything else writes sub-cent limits has not been checked.
- **Decision**: FIXED (via Fix A)

### F3 — The 0.7 test row's colour depends on float rounding

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: tests/test_frames.py:275
- **Detail**: At exactly 70 % spent, `1 - 0.7` is `0.30000000000000004`, so the colour is amber. A spend/limit ratio that rounds to `0.7000000000000001` would give red. The row `(0.7, 10, AMBER)` passes only because of how that number rounds. The board and the page use the same expression, so they always agree, and the 50 % edge is exact. This is cosmetic: it affects one colour step at a single spend value.
- **Fix**: Leave it as it is. The row matches the agreed rule that exactly 30 % left is amber. Or move the row to 0.69 to make the test independent of the rounding.
- **Decision**: FIXED (row moved to 0.69)
