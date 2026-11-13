<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Daily AI cover spend, its limit, and the cost bar

- **Plan**: context/changes/display-usage/plan.md
- **Scope**: Full plan
- **Reviewed phases**: 1, 2, 3, 4
- **Date**: 2026-11-13
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical, 3 warnings, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | WARNING |

Plan drift: every planned change in Phases 1–4 is present and matches its intent and contract. All items under "What We're NOT Doing" were respected. The extras are benign and within intent: `Enhancer.spend_resets_at`, the owner-requested restyle of the limit form, tests in `tests/test_config.py`, the limit reason added to the "enhanced" and "gave_up" page lines, and the `var/settings.json` mention in CLAUDE.md. Every automated success criterion passes: 696 passed, 1 skipped. Every manual check was confirmed by the owner on the Pi.

## Findings

### F1 — A non-finite or negative reported cost breaks the limit and the display loop

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/enhance.py:240 (`_cost`), src/spotify_to_led/spend.py:51 (`record`)
- **Detail**: `_cost` accepts any int or float, and the JSON decoder accepts `NaN` and `Infinity`. A `NaN` cost makes today's total `NaN`, which causes two failures:
  - `spent >= limit` is always False, so the limit stops working.
  - `spend_fraction` becomes `NaN`, and `math.ceil(nan)` in `frames.with_spend_bar` raises on every sample. `NaN != NaN` also makes every sample look like a new presentation, so the loop logs an exception each second and the generating frame never reaches the board.

  A negative cost would lower the total. The load path already refuses these values (`spend.py:109`); the record path does not.
- **Fix**: Have `_cost` return `None` for a non-finite or negative value, the same check the ledger applies on load, and add a test with `NaN`, `Infinity` and a negative cost.
- **Decision**: FIXED — `_cost` returns `None` for a non-finite or negative cost; tests cover NaN, Infinity and a negative cost (`test_enhance.py`).

### F2 — A missing `usage.cost` silently disables the limit

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/enhance.py:414
- **Detail**: A successful call without a reported cost records nothing and logs "not reported" only at INFO. If the endpoint or model stops reporting cost, spending is no longer capped and nothing on the page says so. The plan accepted "counts as $0" as a known risk, but there is no way to notice it happening.
- **Fix A ⭐ Recommended**: Log a WARNING when a successful call reports no cost
  - Strength: Makes the failure visible in `pm2 logs` with a one-line change, while keeping "only reported costs" as the plan decided.
  - Tradeoff: The cap still does not hold while costs go unreported; it only becomes noticeable.
  - Confidence: HIGH — the INFO line already exists; only its level changes.
  - Blind spot: Nobody watches the logs unprompted, and the page shows nothing.
- **Fix B**: Count a conservative per-call estimate (e.g. $0.01) when no cost is reported
  - Strength: The limit keeps binding whatever OpenRouter returns.
  - Tradeoff: Reverses the "reported costs only" decision, so the total stops matching OpenRouter's bill, and an estimate constant needs maintaining when the model changes.
  - Confidence: MED — the right estimate depends on the model.
  - Blind spot: How often OpenRouter's Images API actually omits `usage.cost` is unverified.
- **Decision**: FIXED via Fix A — a success without a usable cost logs a WARNING ("cost not reported; not counted toward today's limit"); tested.

### F3 — The lifespan's `spend_fraction` wiring has no test

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: src/spotify_to_led/main.py:219
- **Detail**: Passing `None`, or dropping the `spend_fraction` argument to `run_display`, would keep every test green while the board never shows the bar. The existing lifespan test only checks how the enhancer is wired.
- **Fix**: Add a test in tests/test_app.py that captures the `spend_fraction` callable the lifespan hands to `run_display` and checks it returns `ledger spend / daily_limit_usd`, using a seeded `AI_SPEND_FILE` and a saved limit.
- **Decision**: FIXED — `test_the_lifespan_gives_the_display_todays_spend_over_the_live_limit` checks the callable handed to `run_display` reads spend / live limit; the no-enhancer test also asserts `spend_fraction` is `None`.

### F4 — The page's "generation resumes at" reads as if the current cover will update

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/static/index.html (`enhancementCoverLine`)
- **Detail**: A cover made plain by the limit stays plain after the limit is raised or after midnight UTC, until the track changes, because the same `want()` is a no-op. This matches how the refusal pause behaves, but "generation resumes at HH:MM" suggests the cover on show will be enhanced then.
- **Fix**: Word it "new covers are enhanced again from HH:MM".
- **Decision**: FIXED — the page says "new covers are enhanced again from HH:MM".

### F5 — A limit below $0.01 shows as "0.00" in the field

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/static/index.html (`limit.value = spend.limit_usd.toFixed(2)`)
- **Detail**: The server accepts any positive limit, such as 0.001, but the field shows it with two decimals as "0.00". That value fails the field's own `min="0.01"`, so saving from that state needs a retype.
- **Fix**: Format the field like the spend line, with 3 decimals below $0.10.
- **Decision**: FIXED — the field shows 3 decimals below $0.10, and accepts them (`min`/`step` 0.001).

### F6 — `write_json_atomically` lives in owner_settings.py and is still duplicated in token_store.py

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/spotify_to_led/owner_settings.py (`write_json_atomically`), src/spotify_to_led/spotify/token_store.py:52
- **Detail**: Because the helper lives in owner_settings.py, `spend.py` imports from `owner_settings`, which is an odd dependency direction. `TokenStore` keeps its own copy of the same temp-file, replace and fsync code.
- **Fix**: Leave it for a separate refactor: move the helper to a small shared module and use it in `TokenStore` as well. That is outside this change's scope.
- **Decision**: FIXED — new `src/spotify_to_led/state_file.py` holds `write_json_atomically(path, data, *, mode)`; `OwnerSettingsStore`, `SpendLedger` and `TokenStore` (with `mode=0o600`) all use it, and its test moved to `tests/test_state_file.py`.
