# Show the plain cover while the AI-enhanced version is generated — Plan Brief

> Full plan: `context/changes/show-basic-cover/plan.md`

## What & Why

With AI-enhanced covers on, a never-seen cover shows the play-triangle placeholder for the
seconds (sometimes a minute or more) its enhanced frame takes to generate. The plain cover is
already in hand by then, so the board can show the right album at once and upgrade it when the
enhanced frame arrives.

## Starting Point

The loader enters `GENERATING` only after the plain frame is loaded and stored, but the state
carries no frame, and the display rule maps it to the placeholder (`display.py:217-219`). PRD
FR-019 and the NFR-01 note describe that placeholder behaviour.

## Desired End State

A cover being enhanced shows its plain frame with a small green dot in the top-left corner;
the enhanced frame replaces it without the dot, or, on failure, give-up or pause, the plain
cover stays and the dot goes. Switching enhancement on over a plain cover adds the dot instead
of flashing the placeholder. The staleness marker still applies on top, and `/status` and the
page say the dot is up.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) |
| --- | --- | --- |
| What shows while generating | The plain cover, not the placeholder | It is already in hand and is the right album |
| On-board hint | A dot over the plain cover while generating | The owner wants to see from the board that an upgrade is coming |
| Dot design | 1x1 cell (2 px on 32x32), green, one pixel in from the top-left corner, no ring (the ring was dropped after the board check) | Green already means "on its way"; half the amber marker's size and in the other top corner, so the two never read alike |
| Dot colour | A green dimmer than the placeholder's, tuned on the Pi | Glyph colours on this board are judged on the real board |
| Changeover while the plain frame downloads | Unchanged: previous presentation kept | Not part of this change; the plain frame is not on hand yet |
| Failed plain download | Unchanged: placeholder | There is no cover to show |
| How the frame reaches the display | `GENERATING` carries the plain frame; `Presentation.generating` | Reuses the retain-and-redraw path; a field change already forces a push |
| Verification | Unit tests plus a check on the real board | The extra push and the dot's legibility only show on hardware |

## Scope

**In scope:** the dot overlay; `GENERATING` carrying the plain frame; `Presentation.generating`
and its drawing; `/status` field and page text; tests; PRD FR-019/NFR-01 and CLAUDE.md wording;
a Pi check with colour tuning.

**Out of scope:** the enhancer, its retries and pause; the frame cache; the plain path with
enhancement off; the `PENDING` changeover; any animation or progress beyond the dot; a dot on
idle, the placeholder or the disc.

## Architecture / Approach

`CoverLoader` hands out `CoverState(GENERATING, plain_frame)`. `select_presentation` turns it
into `Presentation(COVER, url, generating=True)`. The loop retains the frame as for a ready
cover, and `_frame_for` draws plain → generating dot → staleness marker (each on a copy). When
the loader becomes `READY`, the presentation changes (`enhanced=True`, or `generating=False`)
and the board redraws.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Generating marker glyph | `frames.with_generating_marker`, tested at 16/32/64 | Little; pure function |
| 2. Plain cover while generating | Loader state, rule, loop, `/status`, page, tests | Retained-frame key mismatch raising in `_frame_for` |
| 3. Docs and Pi check | PRD/CLAUDE.md wording; board verdict and final dot colour | A 2 px dot may be hard to spot from across the room |

**Prerequisites:** the Pi with an OpenRouter key and enhancement available; a cover never
enhanced before for the board check.
**Estimated effort:** ~1–2 sessions, plus a short board session.

## Open Risks & Assumptions

- The dot's legibility is unproven until Phase 3; its colour is a starting value.
- One extra BLE push per newly enhanced cover is assumed harmless (the board already redraws on enhancement flips).
- The "one static frame per item" non-goal is read as "no animation"; a plain-then-enhanced redraw already existed with the placeholder.

## Success Criteria (Summary)

- With enhancement on, a new cover shows the right album within about 5 s, marked with the green dot, then the enhanced version.
- A failed or paused generation leaves the plain cover without the dot; nothing regresses with enhancement off.
- The owner can tell the dot from the staleness marker on the real board.
