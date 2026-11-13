# Daily AI cover spend, its limit, and the cost bar Implementation Plan

## Overview

Count what AI-enhanced covers (FR-019) cost each UTC day, let the owner set a daily limit on the status page (default $1.00), stop starting generations once today's spend reaches it, and show the spend in two places: on the status page, and on the board as a one-pixel bar along the bottom of the plain cover while its enhanced frame is generated.

## Current State Analysis

- `enhance.generate` already parses OpenRouter's `usage.cost` into `GeneratedImage.cost` (`src/spotify_to_led/enhance.py:230`), but `Enhancer.enhance` only logs it (`enhance.py:372`). Nothing sums it, persists it or acts on it.
- The enhancer already has a "no generation until the next day" mechanism: a 401/402/403 refusal sets `_paused_until = next_utc_midnight(...)` (`enhance.py:333`, `enhance.py:262`), `enhance()` returns `None` without a request while paused (`enhance.py:320`), and `CoverLoader._will_enhance` checks `paused_until` so a paused cover goes straight to `READY` plain with no dot (`covers.py:292`).
- Owner choices live in `OwnerSettings` (`owner_settings.py:23`, only `ai_enhanced`), persisted to `var/settings.json` by `OwnerSettingsStore` with an atomic temp-file + `os.replace` + directory fsync write (`owner_settings.py:55`). `_from_json` indexes `raw["ai_enhanced"]`, so a missing key raises `KeyError` and the whole file loads as defaults (`owner_settings.py:97`).
- `PUT /settings` takes exactly `{"ai_enhanced": StrictBool}` with `extra="forbid"` (`main.py:349`), refuses turning on without a key with 409, saves file-first, and calls `enhancer.forget()` on a change (`main.py:359`).
- The generating state is the plain cover with a green top-left dot: `select_presentation` returns `Presentation(COVER, url, generating=True)` for `CoverStatus.GENERATING` (`display.py:224`), and `_frame_for` draws `with_generating_marker` then `with_stale_marker` on copies (`display.py:350`). `Presentation` is a frozen, value-compared dataclass, so any new field in it triggers a redraw when it changes (`display.py:126`).
- `/status`'s `enhancement` block reports `available`, `enabled`, `paused_until` and `cover` (`main.py:393`); the page renders it in `renderEnhancement` / `enhancementCoverLine` and guards its checkbox against poll races with `enhanceChanges` / `enhanceSaving` (`static/index.html:146`).
- Tests isolate every `var/` path through env vars in `use_transport` (`tests/test_app.py:130`).

## Desired End State

- Every successful generation's reported cost is added to today's (UTC) total, kept in `var/ai-spend.json`, so a restart keeps the day's total and a new UTC day starts from $0.
- The owner sets a daily limit on the status page; it is saved in `var/settings.json` beside `ai_enhanced`, defaults to $1.00, and is always a positive amount.
- A generation starts only while today's spend is below the limit. The call that crosses it is allowed; after it, new covers show plain at once, with no dot and no bar, and no OpenRouter request, until 00:00 UTC or until the owner raises the limit. Enhanced frames already in the cache still show.
- While a cover's enhanced frame is generated, the dotted plain cover also carries a bar on its bottom pixel row: filled from the left to `spent / limit` of the width, green below 75 %, amber from 75 %, red from 90 %, black for the rest. No other presentation has the bar.
- The status page shows today's spend against the limit with a bar scaled to the limit, a field to change the limit, and says when the limit has stopped generation and when it resets.

Verify with `uv run pytest`, and on the Pi by watching a new cover generate (bar visible, then gone on the enhanced frame) and by setting a limit below today's spend (next new cover stays plain, page says why).

### Key Discoveries:

- OpenRouter's daily key limit "resets automatically at midnight UTC" (OpenRouter API docs, `limit_reset`), which is also when the existing refusal pause ends (`enhance.py:262`). One day boundary for the whole service.
- `GET /api/v1/key` reports `usage_daily`; rejected in favour of an own ledger (see brief) — it would cost a request per cover and count other uses of the key.
- A cover's frame is drawn marker-by-marker on copies in `_frame_for` (`display.py:350`); the bar slots in there, before the dot and the stale marker, so FR-018's marker stays on top.
- `PUT /settings` has a test that a body with an unknown field is refused (`tests/test_app.py:728`); the new optional field must keep that strictness.

## What We're NOT Doing

- No query of OpenRouter's `usage_daily` / `GET /api/v1/key`; the total is our own ledger of reported costs.
- Calls billed but abandoned midway (item changed, switch flipped) are not counted — they report no cost to us. Already an accepted cost in FR-019; the ledger inherits the hole. A successful answer without `usage.cost` counts as $0.
- No bar on the enhanced frame, the plain-after-limit cover, idle, the placeholder or the disc, and no timed "show the new total for a few seconds" redraw.
- No "no limit" setting, and no weekly or monthly limits.
- No history of past days; the file holds only the current day.
- Changing the limit does not call `enhancer.forget()`; images given up on stay given up on until the switch is flipped, as today.
- No change to the 3-attempt retry policy; the limit is checked once, before the first attempt.

## Implementation Approach

The limit is a third reason, beside the refusal pause and the give-up memory, for the enhancer to answer `None` without a request, so it lives in `Enhancer` and is read by `CoverLoader._will_enhance` the same way `paused_until` is. The running total is a small `SpendLedger` that the enhancer owns and writes after each success. The limit itself is an owner choice, so it joins `OwnerSettings`; the enhancer reads it through a callable, the way the display reads the switch, so a change from the page applies at once. The board bar is a pure overlay in `frames`, chosen by `select_presentation` from a fraction passed in, so the display rule stays testable without a ledger.

## Critical Implementation Details

- **Settings file compatibility**: an existing `var/settings.json` is `{"ai_enhanced": true}`. `_from_json` must treat a missing `daily_limit_usd` as the default while keeping `ai_enhanced`; the current `KeyError` → all-defaults path would silently switch enhancement off on the Pi at deploy.
- **Marker order**: bar first, then the green dot, then the stale marker, each on a copy. The bar spans the full bottom row and the stale marker sits top-right, so they never overlap, but the order keeps FR-018's marker last as the hard rule requires.

## Phase 1: Spend ledger and limit gate

### Overview

Count today's spend persistently, add the daily limit to the owner's settings, and make the enhancer refuse to start a generation at or over it.

### Changes Required:

#### 1. Atomic JSON write, shared

**File**: `src/spotify_to_led/owner_settings.py`

**Intent**: Lift the temp-file + `os.replace` + directory-fsync body of `OwnerSettingsStore.save` into a module function so the ledger writes its file the same way, without a second copy.

**Contract**: `write_json_atomically(path: Path, data: object) -> None`; `OwnerSettingsStore.save` calls it; behaviour (stale temp cleanup, cleanup on failure) unchanged, so the existing store tests still pass untouched.

#### 2. Daily limit setting

**File**: `src/spotify_to_led/owner_settings.py`

**Intent**: Add the owner's daily limit, $1.00 by default, validated strictly on load.

**Contract**: `OwnerSettings.daily_limit_usd: float = 1.0`; `DEFAULT_DAILY_LIMIT_USD = 1.0`. `_from_json`: `ai_enhanced` required as now; `daily_limit_usd` optional (missing → default), else must be an `int`/`float` (not `bool`), finite and `> 0`, or the file is unreadable as today. Saved JSON shape becomes `{"ai_enhanced": ..., "daily_limit_usd": ...}`.

#### 3. Spend ledger

**File**: `src/spotify_to_led/spend.py` (new)

**Intent**: Hold today's UTC date and the USD spent on it, loaded once at start, kept in memory, written on each record; a different day reads as $0.

**Contract**: `SpendLedger(path: Path, *, clock: Clock = utc now)`; `spent_today() -> float` (0.0 when the stored day is not today's UTC date); `record(cost: float) -> None` (rolls the day over if needed, adds, writes `{"day": "YYYY-MM-DD", "spent_usd": float}` via `write_json_atomically`; a write `OSError` is logged as a WARNING and the in-memory total still counts, so the gate holds); `resets_at() -> datetime` = `enhance.next_utc_midnight(clock())`. A missing file is $0; an unreadable or malformed one (not an object, bad date, non-numeric/negative/non-finite spend) logs a WARNING and starts at $0.

#### 4. File location setting

**File**: `src/spotify_to_led/config.py`

**Intent**: Make the ledger's path configurable like every other `var/` file.

**Contract**: `ai_spend_file: Path = Path("var/ai-spend.json")` (env `AI_SPEND_FILE`), with a one-line comment in the existing style.

#### 5. Enhancer records cost and gates on the limit

**File**: `src/spotify_to_led/enhance.py`

**Intent**: Add each success's reported cost to the ledger, and answer `None` with no request while today's spend is at or over the limit, logging once per refusal-to-start at INFO.

**Contract**: `Enhancer(..., ledger: SpendLedger, daily_limit: Callable[[], float], ...)`. New properties `spent_today -> float` and `limit_reached -> bool` (`ledger.spent_today() >= daily_limit()`). `enhance()` returns `None` without a request when `limit_reached` (checked with `paused_until` and `gave_up` at the top, not between retries). On success, `ledger.record(cost)` when `cost is not None`, before the render. Module docstring gains the limit as a fourth policy bullet.

#### 6. Loader skips generation at the limit

**File**: `src/spotify_to_led/covers.py`

**Intent**: A cover wanted enhanced while the limit is reached goes `READY` plain at once, with no source download, exactly like a paused one.

**Contract**: `_will_enhance` also requires `not self._enhancer.limit_reached`; module docstring's "any image while generation is paused" line names the limit too.

#### 7. Wiring and `PUT /settings`

**File**: `src/spotify_to_led/main.py`

**Intent**: Build the ledger in the lifespan and hand the enhancer the ledger and a callable reading the current limit; let the page change the limit.

**Contract**: `SpendLedger(settings.ai_spend_file)` built only where the `Enhancer` is; `daily_limit=lambda: app.state.owner_settings.daily_limit_usd`. `SettingsChange` keeps `extra="forbid"` and gets two optional fields, `ai_enhanced: StrictBool | None = None` and `daily_limit_usd: StrictFloat` that is finite and `> 0` (or `None`), with at least one present (an empty body is a 422). The update merges into the current settings; the 409 applies only when `ai_enhanced` is `True` without a key; `forget()` only when `ai_enhanced` changed. The response returns both fields.

#### 8. Tests

**Files**: `tests/test_spend.py` (new), `tests/test_owner_settings.py`, `tests/test_enhance.py`, `tests/test_covers.py`, `tests/test_app.py`

**Intent**: Pin the ledger, the setting, the gate and the route.

**Contract**: `use_transport` also sets `AI_SPEND_FILE` under `tmp_path`. Cases listed under Testing Strategy.

### Success Criteria:

#### Automated Verification:

- Full suite passes: `uv run pytest`
- Ledger tests pass: `uv run pytest tests/test_spend.py`
- A settings file without `daily_limit_usd` loads with its `ai_enhanced` kept and the $1.00 default: `uv run pytest tests/test_owner_settings.py`
- At or over the limit the enhancer makes no request and the loader shows plain at once; one call crossing the limit is allowed: `uv run pytest tests/test_enhance.py tests/test_covers.py`
- `PUT /settings` accepts `{"daily_limit_usd": 0.5}` alone, refuses `0`, negative, `"1"`, `true`, unknown fields and `{}`, and keeps `ai_enhanced` unchanged when only the limit is sent: `uv run pytest tests/test_app.py`

#### Manual Verification:

- On the Pi, after a new cover generates, `var/ai-spend.json` holds today's UTC date and a total matching the "cover enhanced ... (cost $...)" log lines
- After `pm2 restart`, the total in the file is unchanged and the existing `settings.json` still has enhancement on

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 2: Cost bar on the board

### Overview

Draw today's spend against the limit as a bar on the bottom row of the dotted plain cover, and nowhere else.

### Changes Required:

#### 1. The bar overlay

**File**: `src/spotify_to_led/frames.py`

**Intent**: A pure overlay, documented beside the two markers in the module docstring.

**Contract**: `with_spend_bar(frame: Image.Image, fraction: float) -> Image.Image` returns a copy whose bottom pixel row (1 board pixel, not scaled by the design grid) is black, with the leftmost `ceil(clamp(fraction, 0, 1) * width)` pixels filled. Fill colour by `fraction`: `< 0.75` → `GENERATING_MARKER_COLOUR`; `< 0.90` → `STALE_MARKER_COLOUR` (amber); else `SPEND_BAR_RED = (255, 0, 0)`. Constants `SPEND_BAR_AMBER_FROM = 0.75`, `SPEND_BAR_RED_FROM = 0.90`.

#### 2. Presentation carries the fraction while generating

**File**: `src/spotify_to_led/display.py`

**Intent**: Put the spend fraction into the generating presentation so it is part of what the loop compares, and draw it.

**Contract**: `Presentation.spend_fraction: float | None = None`, set only for a `GENERATING` cover; `__str__` adds `(spend NN%)` when set. `select_presentation(..., spend_fraction: float | None = None)` passes it into the generating presentation only. `run_display(..., spend_fraction: Callable[[], float] | None = None)` calls it each sample (only when `covers` is given) and passes it in. `_frame_for` applies `with_spend_bar` before `with_generating_marker` when `spend_fraction` is not `None`. Module docstring's FR-019 bullet mentions the bar.

#### 3. Wiring

**File**: `src/spotify_to_led/main.py`

**Intent**: Give the display the fraction from the enhancer and the current limit.

**Contract**: passed only with an enhancer: `lambda: enhancer.spent_today / app.state.owner_settings.daily_limit_usd`.

#### 4. Tests

**Files**: `tests/test_frames.py`, `tests/test_display.py`

**Intent**: Pin the bar's geometry and colours and the rule that only the generating cover has it.

**Contract**: Cases listed under Testing Strategy.

### Success Criteria:

#### Automated Verification:

- Full suite passes: `uv run pytest`
- Bar geometry and colours at 0 %, 50 %, 74 %, 75 %, 89 %, 90 %, 100 % and over 100 %, at 16, 32 and 64 px, with the rest of the frame untouched: `uv run pytest tests/test_frames.py`
- Only a generating cover's presentation carries `spend_fraction`; a change of fraction while generating redraws; the stale marker is still drawn over a barred frame: `uv run pytest tests/test_display.py`

#### Manual Verification:

- On the real board, a never-seen cover shows the bottom bar with the green dot while generating, and the enhanced frame replaces it with no bar
- With the limit lowered on the page so today's spend is about 80 % / 95 % of it, the next generating frame shows an amber / red bar readable from the desk

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 3: Status page

### Overview

Report today's spend and the limit in `/status`, and let the page show them with a bar and change the limit.

### Changes Required:

#### 1. `/status` spend block

**File**: `src/spotify_to_led/main.py`

**Intent**: Name the spend fields one by one, as the rest of `/status` does.

**Contract**: `enhancement.spend = {"spent_usd": float | null, "limit_usd": float, "limit_reached": bool, "resets_at": datetime | null}`. `limit_usd` always comes from the owner settings; the other three are `null`/`false` without an enhancer. `_enhanced_cover`'s docstring names the limit among the reasons for `"plain"`.

#### 2. Page: spend line, bar and limit field

**File**: `src/spotify_to_led/static/index.html`

**Intent**: In the AI-enhanced covers section, show "Today: $0.27 of $1.00 (resets HH:MM)" with a bar scaled to the limit using the board's thresholds and colours (green / amber / red fill, black track), a number input for the limit with a Save button, and a cover-line reason "plain, because today's limit of $X is reached; generation resumes at <time>".

**Contract**: The limit is sent as `PUT /settings {"daily_limit_usd": <number>}`; a failure shows "The limit could not be saved." in the note, as the checkbox does. The input is not overwritten by a poll while it has focus or a save is in flight (same guard idea as `enhanceChanges` / `enhanceSaving`). Every value goes in through `textContent` / style width, never HTML. Amounts shown with 2 decimals, or 3 below $0.10.

#### 3. Tests

**File**: `tests/test_app.py`

**Intent**: Pin the `/status` shape and the page's wiring.

**Contract**: Cases listed under Testing Strategy.

### Success Criteria:

#### Automated Verification:

- Full suite passes: `uv run pytest`
- `/status` reports `spend` with and without a key, `limit_reached` true once the ledger is at the limit, and the page contains the limit form's `PUT /settings` call: `uv run pytest tests/test_app.py`

#### Manual Verification:

- On the status page, the spend line and bar match `var/ai-spend.json`, and the bar's colour changes as the limit is lowered past 75 % and 90 % of today's spend
- Saving a limit below today's spend makes the next new cover stay plain, and the page says the limit is reached and when it resets; raising it again lets the next new cover generate
- A poll during typing in the limit field does not overwrite what is typed

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 4: Docs

### Overview

Record the limit, the ledger and the bar where the project's living docs describe FR-019.

### Changes Required:

#### 1. PRD

**File**: `context/foundation/prd.md`

**Intent**: Add a dated note under FR-019: the owner-set daily limit ($1.00 default, per UTC day, the crossing call allowed), the ledger counting reported costs only (abandoned billed calls not counted, as already accepted), and the bar on the generating frame.

**Contract**: One `> Changed 2026-11-13 (display-usage): ...` note under FR-019; nothing else in the PRD changes.

#### 2. CLAUDE.md and module docstrings

**Files**: `CLAUDE.md`, `src/spotify_to_led/main.py`

**Intent**: Mention the spend bar beside the green dot, the daily limit, and `var/ai-spend.json` beside `var/spotify-token.json` in the repository overview; same in `main.py`'s module docstring.

**Contract**: Sentence-level edits in the existing paragraphs; no new sections.

### Success Criteria:

#### Automated Verification:

- Full suite still passes: `uv run pytest`
- `grep -n "display-usage" context/foundation/prd.md` finds the FR-019 note

#### Manual Verification:

- The FR-019 note and CLAUDE.md read correctly against the shipped behaviour

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful.

---

## Testing Strategy

### Unit Tests:

- `SpendLedger`: missing file → $0; records accumulate and persist (new instance reads the total); a stored earlier day reads $0 and the next record starts a new day; rollover exactly at 00:00 UTC with an injected clock; malformed files (not JSON, not an object, bad date, `"spent_usd": "x"`, negative, `NaN`) → $0 and a WARNING; a failed write keeps the in-memory total; `resets_at` is the next UTC midnight.
- `OwnerSettings`: default limit 1.0; round trip with a limit; file without `daily_limit_usd` keeps `ai_enhanced`; `0`, negative, `true`, `"1"`, `Infinity` limits → defaults with a warning; saved shape has both keys.
- `Enhancer`: success records the reported cost; a missing cost records nothing; at `spent == limit` and over it no request is made and `None` is returned; at `spent` just under the limit one call is made and its cost may take the total over; the limit callable is read live (raising it lets the next cover generate).
- `CoverLoader`: with the limit reached, a cover wanted enhanced goes `READY` plain inside `want`'s task with no source download; a cached enhanced frame is still `READY` enhanced.
- `frames.with_spend_bar`: filled width uses `ceil`, colour thresholds at exactly 0.75 and 0.90, black remainder, other rows untouched, input frame untouched.
- `select_presentation` / `_frame_for`: `spend_fraction` only on generating covers; bar + dot + stale marker drawn together correctly.

### Integration Tests:

- `tests/test_app.py`: `PUT /settings` partial bodies and refusals; `/status` `spend` block with and without a key; the lifespan builds the ledger at `AI_SPEND_FILE`.

### Manual Testing Steps:

1. On the Pi with enhancement on, play a never-seen album: the dotted plain cover shows the bottom bar; the enhanced frame shows no bar.
2. Compare the page's spend line with `var/ai-spend.json` and the log's cost lines.
3. Set the limit just below today's spend; play another never-seen album: plain cover, no dot, no bar; page explains.
4. Raise the limit; skip to a never-seen album: it generates again.
5. `pm2 restart spotify-to-led`; the page shows the same total and limit.

## Performance Considerations

The ledger is read once at start and written once per successful generation (a few per hour at most); the gate reads memory. The bar is one row paste on a 32x32 copy.

## Migration Notes

The existing `var/settings.json` (`{"ai_enhanced": true}`) loads with the $1.00 default and is rewritten with both keys on the next save. `var/ai-spend.json` is created on the first successful generation after deploy; until then today's spend is $0, so spending already done today before the deploy is not counted.

## References

- Change notes: `context/changes/display-usage/change.md`
- FR-019 and its accepted costs: `context/foundation/prd.md:203`
- Prior change: `context/archive/2026-11-11-ai-enhanced-covers/plan.md`, `context/archive/2026-11-13-show-basic-cover/plan.md`
- Refusal pause to mirror: `src/spotify_to_led/enhance.py:313`, `src/spotify_to_led/covers.py:292`
- Marker drawing: `src/spotify_to_led/frames.py:170`, `src/spotify_to_led/display.py:350`
- OpenRouter: daily `limit_reset` resets at midnight UTC (API keys reference); `GET /api/v1/key` `usage_daily`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Spend ledger and limit gate

#### Automated

- [x] 1.1 Full suite passes: `uv run pytest` — 1b4163d
- [x] 1.2 Ledger tests pass: `uv run pytest tests/test_spend.py` — 1b4163d
- [x] 1.3 A settings file without `daily_limit_usd` loads with its `ai_enhanced` kept and the $1.00 default: `uv run pytest tests/test_owner_settings.py` — 1b4163d
- [x] 1.4 At or over the limit the enhancer makes no request and the loader shows plain at once; one call crossing the limit is allowed: `uv run pytest tests/test_enhance.py tests/test_covers.py` — 1b4163d
- [x] 1.5 `PUT /settings` accepts `{"daily_limit_usd": 0.5}` alone, refuses `0`, negative, `"1"`, `true`, unknown fields and `{}`, and keeps `ai_enhanced` unchanged when only the limit is sent: `uv run pytest tests/test_app.py` — 1b4163d

#### Manual

- [x] 1.6 On the Pi, after a new cover generates, `var/ai-spend.json` holds today's UTC date and a total matching the "cover enhanced ... (cost $...)" log lines — 1b4163d
- [x] 1.7 After `pm2 restart`, the total in the file is unchanged and the existing `settings.json` still has enhancement on — 1b4163d

### Phase 2: Cost bar on the board

#### Automated

- [x] 2.1 Full suite passes: `uv run pytest`
- [x] 2.2 Bar geometry and colours at 0 %, 50 %, 74 %, 75 %, 89 %, 90 %, 100 % and over 100 %, at 16, 32 and 64 px, with the rest of the frame untouched: `uv run pytest tests/test_frames.py`
- [x] 2.3 Only a generating cover's presentation carries `spend_fraction`; a change of fraction while generating redraws; the stale marker is still drawn over a barred frame: `uv run pytest tests/test_display.py`

#### Manual

- [ ] 2.4 On the real board, a never-seen cover shows the bottom bar with the green dot while generating, and the enhanced frame replaces it with no bar
- [ ] 2.5 With the limit lowered on the page so today's spend is about 80 % / 95 % of it, the next generating frame shows an amber / red bar readable from the desk

### Phase 3: Status page

#### Automated

- [ ] 3.1 Full suite passes: `uv run pytest`
- [ ] 3.2 `/status` reports `spend` with and without a key, `limit_reached` true once the ledger is at the limit, and the page contains the limit form's `PUT /settings` call: `uv run pytest tests/test_app.py`

#### Manual

- [ ] 3.3 On the status page, the spend line and bar match `var/ai-spend.json`, and the bar's colour changes as the limit is lowered past 75 % and 90 % of today's spend
- [ ] 3.4 Saving a limit below today's spend makes the next new cover stay plain, and the page says the limit is reached and when it resets; raising it again lets the next new cover generate
- [ ] 3.5 A poll during typing in the limit field does not overwrite what is typed

### Phase 4: Docs

#### Automated

- [ ] 4.1 Full suite still passes: `uv run pytest`
- [ ] 4.2 `grep -n "display-usage" context/foundation/prd.md` finds the FR-019 note

#### Manual

- [ ] 4.3 The FR-019 note and CLAUDE.md read correctly against the shipped behaviour
