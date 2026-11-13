# Daily AI cover spend, its limit, and the cost bar — Plan Brief

> Full plan: `context/changes/display-usage/plan.md`

## What & Why

AI-enhanced covers (FR-019) cost about $0.009 each, and today nothing adds that up or stops it. This change counts each UTC day's spend, lets the owner cap it from the status page, and shows it: on the page, and on the board as a small bar while a new cover is being generated.

## Starting Point

`enhance.generate` already reads OpenRouter's reported `usage.cost`, but the enhancer only logs it. The enhancer already knows how to stop until the next 00:00 UTC (after a credit/key refusal), and the board already has a "generating" frame: the plain cover with a green top-left dot.

## Desired End State

Every successful generation's cost lands in `var/ai-spend.json` for the current UTC day, surviving restarts. Once today's spend reaches the owner's limit ($1.00 unless changed), new covers show plain with no request until 00:00 UTC or a higher limit. While a cover generates, its dotted frame carries a one-pixel bar along the bottom showing spend against the limit; the status page shows the same figures, a matching bar, and a field to change the limit.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) |
| --- | --- | --- |
| Day boundary | 00:00–24:00 UTC | OpenRouter's daily key limit resets at midnight UTC, the same instant the existing refusal pause ends. |
| Limit check | Start a generation only while spend < limit; the crossing call is allowed | Simple and exact against reported costs; overshoot is at most one call (~1 cent). |
| Persistence | Own file `var/ai-spend.json`, atomic writes | A restart or crash loop must not re-grant the day's budget. |
| Default limit | $1.00, always set, positive only | About 110 covers a day; the bar always has a scale. |
| Source of the total | Own ledger of `usage.cost` from successful calls | No extra OpenRouter request, counts covers only; abandoned billed calls stay uncounted, as FR-019 already accepts. |
| Bar timing | Only on the dotted plain cover while generating, showing spend before that call | Exactly "at the time of new cover generation", with no timers. |
| Bar look | Bottom pixel row; fill green < 75 %, amber from 75 %, red from 90 %; rest black | Smallest footprint, near-limit stands out across the room. |
| Changing the limit | Applies at once, does not reset given-up images | The enhancer reads the limit live, like the display reads the switch. |

## Scope

**In scope:**
- `SpendLedger` (new `spend.py`), `AI_SPEND_FILE` setting, shared atomic JSON write
- `OwnerSettings.daily_limit_usd` with backward-compatible loading; `PUT /settings` accepting the limit alone
- Enhancer records cost and refuses at the limit; loader skips generation at the limit
- `frames.with_spend_bar`, `Presentation.spend_fraction` on generating covers only
- `/status` `enhancement.spend` block; page spend line, bar, limit field, "limit reached" reason
- PRD FR-019 note, CLAUDE.md and `main.py` docstring

**Out of scope:**
- Querying OpenRouter's `usage_daily`; counting abandoned billed calls
- A bar on any frame other than the generating one; timed redraws
- "No limit", weekly/monthly limits, spend history

## Architecture / Approach

The limit becomes a third "answer `None` without a request" reason in `Enhancer`, beside the refusal pause and the give-up memory, and `CoverLoader._will_enhance` checks it the same way it checks `paused_until`. The enhancer owns the ledger and reads the limit through a callable over `app.state.owner_settings`. The display gets a `spend_fraction()` callable, puts the value into the generating `Presentation` (so a change redraws), and `_frame_for` draws bar → dot → stale marker on copies.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Spend ledger and limit gate | Persisted daily total, `daily_limit_usd`, enforced limit, `PUT /settings` for it | Loading the Pi's existing `settings.json` (no limit key) must keep enhancement on |
| 2. Cost bar on the board | Bottom-row bar on the generating frame | Readability of a 1 px bar on the real board |
| 3. Status page | Spend line, bar, limit field, reason when limited | Poll overwriting the limit field while typing |
| 4. Docs | FR-019 note, CLAUDE.md, docstrings | — |

**Prerequisites:** branch `feat/display-usage` from latest `main`; an OpenRouter key on the Pi for manual checks.
**Estimated effort:** ~2–3 sessions across 4 phases.

## Open Risks & Assumptions

- Spend already made today before the deploy is not counted; the ledger starts at $0.
- A response without `usage.cost` counts as $0; if OpenRouter stops reporting cost, the limit silently stops binding (the existing "cost not reported" log line is the signal).
- A corrupt `ai-spend.json` resets the day to $0 with a warning (atomic writes make this unlikely).
- A 1 px bar may prove hard to read on the board; the colours and thresholds are constants to tune.

## Success Criteria (Summary)

- The owner can see on the page what AI covers cost today, against a limit they set.
- Once the limit is reached, no more OpenRouter generation requests are made that UTC day unless the limit is raised.
- While a new cover generates, the board shows how much of the day's budget is used, in green, amber or red.
