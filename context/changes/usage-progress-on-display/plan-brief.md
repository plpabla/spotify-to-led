# AI spend health bar — Plan Brief

> Full plan: `context/changes/usage-progress-on-display/plan.md`

## What & Why

The AI spend bar, both on the board's bottom row while an enhanced cover generates and on the status page, now fills up as the day's AI spend grows. The owner wants it to read like a game health bar instead: it starts full and green, drains as the budget is used, warns as it runs low and turns red near the end.

## Starting Point

`frames.with_spend_bar` lights `ceil(spend/limit × width)` pixels: green, amber from 75 % spent, red from 90 %, and an all-black row at $0. The status page copies those thresholds and colours, and shows "Today: $x of $y (resets hh:mm)".

## Desired End State

While a cover is being enhanced, the board's bottom row shows the share of today's budget still left: a full green row at $0, green down to 50 % left, amber from 50 % down to 30 %, red below 30 %, and never empty while any budget remains. The status page bar matches it exactly and reads "Left today: $0.60 of $1.00 (spent $0.40, resets hh:mm)".

## Key Decisions Made

| Decision          | Choice                                                     | Why (1 sentence) |
| ----------------- | ---------------------------------------------------------- | ---------------- |
| What drains it    | Recorded spend (budget left = 1 − spent/limit), refilled at 00:00 UTC | The bar's whole purpose in FR-019 is to show how close the limit is; uses data already in hand. |
| Colour bands      | Green at ≥ 50 % left, amber at 30 %–<50 %, red below 30 % | The owner's thresholds; 50 % exactly is green and 30 % exactly is amber. |
| Middle colour     | Reuse the stale marker's amber (255,176,0)                 | One warning colour on the board, already seen on the real hardware. |
| Rounding          | Lit pixels = ceil(left × width)                            | The bar never looks empty while generating; it empties only at or over the limit. |
| Page text         | "Left today: $L of $Y (spent $S, resets hh:mm)"            | The text matches the draining bar while the spend stays visible. |
| Where to invert   | Inside `with_spend_bar`; its input stays the spend fraction | The display loop, the presentation and the logs stay untouched. |

## Scope

**In scope:**
- `frames.py` bar drawing, constants and docstrings
- `tests/test_frames.py` bar tests and a comment in `tests/test_display.py`
- `static/index.html` bar width, colour and line
- FR-019 note in `prd.md`, summary line in `CLAUDE.md`

**Out of scope:**
- When the bar is drawn, its height, its position, how it coexists with the stale marker
- `/status`, `spend.py`, display-loop logic, the reset time
- A time-of-day drain, a yellow band or a gradient, and any renaming

## Architecture / Approach

One function draws the board bar, and the page mirrors its constants in JavaScript. Both turn spend/limit into budget left, clamp it to 0..1, apply the same two thresholds and colours, and fill from the left over black.

## Phases at a Glance

| Phase                     | What it delivers                                              | Key risk |
| ------------------------- | ------------------------------------------------------------- | -------- |
| 1. Board health bar       | New drawing, boundary tests, PRD and CLAUDE.md updated        | A full green row at low spend may draw more attention over a cover than the old empty row |
| 2. Status page to match   | Page bar and line in line with the board                      | Thresholds duplicated in JavaScript can drift (same as today) |

**Prerequisites:** none; works on `feat/usage-progress-on-display`.
**Estimated effort:** one short session, two phases.

## Open Risks & Assumptions

- A full-width green row on every generating cover is more visible than today's mostly black row. The bar stays at the dimmer `(0,160,0)` green and is judged on the real board in Phase 1.
- On the board the bar is drawn only below the limit, so its red band shows only in the last 30 % before generation stops for the day.

## Success Criteria (Summary)

- At a glance, the board and the page show how much of today's AI budget is left, in the same colours.
- `uv run pytest` passes, with the band edges and rounding pinned down by tests.
