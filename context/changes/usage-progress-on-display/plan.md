# AI spend health bar Implementation Plan

## Overview

Turn the AI spend bar, on the board and on the status page, from a bar that fills up with today's spend into a health bar that shows the share of today's budget still left. It starts full and green, turns amber once less than half the budget is left, and red once less than 30 % is left. It drains only as spend is recorded and refills at 00:00 UTC (or when the owner raises the limit).

## Current State Analysis

- The board bar is drawn by `frames.with_spend_bar(frame, fraction)` (`src/spotify_to_led/frames.py:196-216`), where `fraction` is today's spend over the daily limit. It lights the leftmost `ceil(clamp(fraction) × width)` pixels of the bottom row and leaves the rest black. The colour is `SPEND_BAR_GREEN` `(0,160,0)` below `SPEND_BAR_AMBER_FROM = 0.75`, the stale marker's amber `(255,176,0)` below `SPEND_BAR_RED_FROM = 0.90`, and `SPEND_BAR_RED` `(255,0,0)` from there (`frames.py:84-92`). At $0 spent the row is all black.
- The bar is drawn only over the plain cover while its enhanced frame generates (`display.py:403`), with the spend from before that call. A generation starts only while spend is below the limit, so on the board `fraction < 1` whenever the bar is drawn.
- The status page copies the board's thresholds and colours (`static/index.html:176-179`), fills `#enhance-spend-fill` to `fraction × 100 %` over a black track (`index.html:19-20, 228-232`), and writes "Today: $x of $y (resets hh:mm)" (`index.html:226-227`). `/status` already carries `spend.spent_usd` and `spend.limit_usd`.
- The current behaviour is pinned down by the bar tests in `tests/test_frames.py:259-349`, by the FR-019 note "Changed 2026-11-13 (display-usage)" in `context/foundation/prd.md:208` ("green, amber from 75 %, red from 90 %, black for the rest") and its follow-up at `prd.md:209` ("at $0 spent the bar is an unlit bottom row"), and by the CLAUDE.md summary ("the bar showing today's AI spend against the owner's daily limit").

## Desired End State

- Board: while a cover's enhanced frame generates, the bottom row's leftmost `ceil(left × width)` pixels are lit, where `left = 1 − clamp(fraction, 0, 1)`, and the rest is black. The lit colour is green when `left ≥ 0.50`, amber when `0.30 ≤ left < 0.50`, and red when `left < 0.30`. At $0 spent the whole row is green. While any budget is left, at least one pixel is lit.
- Status page: the bar's width is `left × 100 %` with the same colour bands and colours, and the line reads "Left today: $L of $Y (spent $S, resets hh:mm)".
- The PRD's FR-019 notes, the `frames.py` docstring and the CLAUDE.md summary describe the health bar.
- To check it: `uv run pytest` passes, and on the status page the bar and the line match the board's colour band for a given spend.

### Key Discoveries:

- `display.py`, the `Presentation.spend_fraction` field and the log text "(spend 80%)" keep their meaning (spend over limit). Only the drawing turns it into budget left, so the display loop and `tests/test_display.py` need no logic change (`tests/test_display.py:1149-1150` builds its expected frames by calling `with_spend_bar` itself).
- `tests/test_display.py:84-85` has a comment saying the $0 bar is "a black bottom row". After this change it is a fully green row.
- Floating-point arithmetic is fine at the band edges that matter: `1 − 0.5 == 0.5` exactly, so 50 % spent is green. `1 − 0.7` is `0.30000000000000004`, so 70 % spent is amber, which matches "30 % exactly is amber".

## What We're NOT Doing

- No change to when the bar is drawn (only over a generating cover), its height (one board pixel), its position (bottom row, filled from the left) or how it coexists with the stale marker.
- No change to `/status`, `spend.py`, `display.py` logic, or the spend arithmetic and reset time.
- No time-of-day drain: the bar moves only with recorded spend.
- No yellow or gradient: the middle band reuses the existing amber.
- No renaming of `with_spend_bar`, `spend_fraction` or the "spend bar" wording elsewhere on the page.

## Implementation Approach

Keep the input as it is (spend fraction) and turn it around inside the one drawing function. That leaves the display loop, the presentation and the logs untouched. Re-express the thresholds as budget left, and mirror them in the page's JavaScript constants, as the page already does today.

## Phase 1: Board health bar

### Overview

`with_spend_bar` draws budget left with the new bands, the tests pin the new behaviour, and the living docs describe it.

### Changes Required:

#### 1. Bar drawing and constants

**File**: `src/spotify_to_led/frames.py`

**Intent**: Draw the bar as the budget left instead of the spend, with green / amber / red bands at 50 % and 30 % left, so the bar reads as a health bar that drains during the day.

**Contract**: `with_spend_bar(frame, fraction) -> Image` keeps its signature, and `fraction` is still spend over limit. Inside, `left = 1 − clamp(fraction, 0, 1)` and `lit = ceil(left × width)`. Colour: `SPEND_BAR_GREEN` if `left ≥ 0.50`, `STALE_MARKER_COLOUR` if `left ≥ 0.30`, otherwise `SPEND_BAR_RED`. Replace `SPEND_BAR_AMBER_FROM` / `SPEND_BAR_RED_FROM` with constants expressed in budget left (e.g. `SPEND_BAR_AMBER_BELOW = 0.50`, `SPEND_BAR_RED_BELOW = 0.30`). Update the constants' comment, the module docstring paragraph on `with_spend_bar` (`frames.py:24-33`) and the function docstring: full and green at no spend, never empty while budget is left, empty only at or over the limit.

#### 2. Bar tests

**File**: `tests/test_frames.py`

**Intent**: Replace the fill-up expectations with health-bar ones, including the band edges and the rounding rule.

**Contract**: The parametrized table for a 32-px row (fraction → lit, colour) must include: `0.0 → 32 green`, `0.5 → 16 green`, `0.51 → 16 amber`, `0.7 → 10 amber`, `0.71 → 10 red`, `0.99 → 1 red`, `1.0 → 0` (all black), `1.4 → 0`. Replace "rounds any spend up to a lit pixel" with "any budget left keeps at least one lit pixel" (e.g. `0.999` → one red pixel then black). "Below zero" becomes a full green row (`-0.2`). The stale-marker coexistence test uses a fraction that leaves a lit row (e.g. `0.0`, full green) so it still checks that the bar and the marker don't overlap. Keep the tests for "only the bottom row changes", "input not mutated" and "green dimmer than placeholder".

#### 3. Display test comment

**File**: `tests/test_display.py`

**Intent**: Correct the comment at lines 84-85, which says that at nothing spent the bar is a black bottom row. It is now a full green row.

**Contract**: Comment only. The `NO_SPEND` expected frames are built by calling `with_spend_bar`, so they stay correct.

#### 4. Living docs

**File**: `context/foundation/prd.md`, `CLAUDE.md`

**Intent**: Keep the requirement and the repo summary true to the new bar.

**Contract**: Under FR-019, append a new dated note, "Changed 2026-11-16 (usage-progress-on-display)", saying that the bar now shows the share of today's limit still left, rounded up to whole pixels: full and green at $0, green from 50 % left, amber from 30 %, red below 30 %, black for the rest, and empty only at the limit. It also replaces the colour rule in the 2026-11-13 note and the "unlit bottom row at $0" remark. Do not edit the older notes. In CLAUDE.md, change "the bar showing today's AI spend against the owner's daily limit" to describe the bar as showing how much of today's AI budget is left, as a health bar.

### Success Criteria:

#### Automated Verification:

- The full test suite passes: `uv run pytest`
- The new spend bar tests in `tests/test_frames.py` cover 0.0, 0.5, 0.51, 0.7, 0.71, 0.99, 1.0, 1.4, −0.2 and 0.999, and pass at every supported board size: `uv run pytest tests/test_frames.py -k spend_bar`
- No code still references the old constant names: `grep -rn "SPEND_BAR_AMBER_FROM\|SPEND_BAR_RED_FROM" src tests` returns nothing

#### Manual Verification:

- On the real board, a generating cover at low spend shows a full or nearly full dim green bottom row that does not overpower the cover
- On the real board, the amber and red bands are distinguishable from green from desk distance

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 2: Status page to match

### Overview

The page bar shows the same budget-left value, in the same bands and colours as the board, with a line that names both what is left and what was spent.

### Changes Required:

#### 1. Page bar and line

**File**: `src/spotify_to_led/static/index.html`

**Intent**: Make the page's bar and text consistent with the board's health bar.

**Contract**: Replace `SPEND_BAR_AMBER_FROM` / `SPEND_BAR_RED_FROM` with the budget-left thresholds (0.50, 0.30), keeping the comment that ties them to `frames.SPEND_BAR_*`. In `renderEnhancement`, compute `left = 1 − clamp(spent/limit, 0, 1)`, set the fill width to `left × 100 %`, and pick the colour by the same rule as the board (green if `left ≥ 0.50`, amber if `left ≥ 0.30`, else red). The line becomes `"Left today: " + usd(max(limit − spent, 0)) + " of " + usd(limit) + " (spent " + usd(spent) + ", resets " + clock(resets_at) + ")"`. The black track behind the fill stays.

### Success Criteria:

#### Automated Verification:

- The full test suite still passes: `uv run pytest`
- No old threshold names remain in the page: `grep -n "SPEND_BAR_AMBER_FROM\|SPEND_BAR_RED_FROM" src/spotify_to_led/static/index.html` returns nothing

#### Manual Verification:

- With `var/ai-spend.json` set to $0, $0.40, $0.60 and $0.80 against a $1.00 limit, the page bar is full green, 60 % green, 40 % amber and 20 % red, and the line reads e.g. "Left today: $0.60 of $1.00 (spent $0.40, resets hh:mm)"
- With spend at or over the limit, the page bar is empty and the line shows "Left today: $0.00 of $1.00 (spent $1.00, ...)"
- During a real generation, the page bar's colour band matches the board's bottom row

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful.

---

## Testing Strategy

### Unit Tests:

- The table in `tests/test_frames.py` covers both band edges from each side (0.5 / 0.51 spent, 0.7 / 0.71 spent), the full and empty ends, clamping beyond both ends, and rounding up at the last sliver.
- The existing structural tests (only the bottom row changes, no input mutation, the bar and the marker don't overlap) stay.

### Integration Tests:

- The display-loop tests in `tests/test_display.py` keep checking that the bar is drawn only on a generating cover and is redrawn when the spend changes. They build their expected frames through `with_spend_bar`, so they follow the new drawing.

### Manual Testing Steps:

1. Run the service with AI covers on and a fresh `var/ai-spend.json`, and play a never-seen cover: the board shows a full green bottom row while the enhanced frame generates.
2. Set today's spend to $0.60 of $1.00 and play another never-seen cover: the bar is 40 % amber on the board, and the page matches.
3. Set today's spend to $0.80: the bar is 20 % red on both.

## References

- Previous change that introduced the bar: `context/archive/2026-11-13-display-usage/`
- Bar drawing: `src/spotify_to_led/frames.py:84-92`, `frames.py:196-216`
- Page bar: `src/spotify_to_led/static/index.html:19-20`, `index.html:176-179`, `index.html:223-233`
- Requirement: `context/foundation/prd.md:208-209` (FR-019)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Board health bar

#### Automated

- [x] 1.1 The full test suite passes: `uv run pytest`
- [x] 1.2 The new spend bar tests cover 0.0, 0.5, 0.51, 0.7, 0.71, 0.99, 1.0, 1.4, −0.2 and 0.999, and pass at every supported board size
- [x] 1.3 No code still references the old constant names

#### Manual

- [ ] 1.4 On the real board, a low-spend generating cover shows a full or nearly full dim green row that does not overpower the cover
- [ ] 1.5 On the real board, the amber and red bands are distinguishable from green from desk distance

### Phase 2: Status page to match

#### Automated

- [ ] 2.1 The full test suite still passes: `uv run pytest`
- [ ] 2.2 No old threshold names remain in the page

#### Manual

- [ ] 2.3 At $0, $0.40, $0.60 and $0.80 of $1.00, the page bar is full green, 60 % green, 40 % amber and 20 % red, with the "Left today" line
- [ ] 2.4 At or over the limit, the page bar is empty and the line shows $0.00 left
- [ ] 2.5 During a real generation, the page bar's colour band matches the board's bottom row
