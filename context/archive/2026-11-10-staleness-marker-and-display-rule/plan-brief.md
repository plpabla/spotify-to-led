# Staleness marker completes the display rule — Plan Brief

> Full plan: `context/changes/staleness-marker-and-display-rule/plan.md`

## What & Why

Roadmap S-06. The board must never present something the service cannot vouch for without
saying so (FR-018), and PRD Open Question 2 asked what to show when the service is blind and
has no cover to mark. The answer: the same amber corner marker goes on whatever the board is
showing (idle, the playing placeholder or the no-artwork disc, as well as a cover) once reads
have failed for more than 30 s. That completes FR-009's display rule.

## Starting Point

S-04 marks a stale **cover** only. Idle, placeholder and disc return from
`select_presentation` (`src/spotify_to_led/display.py:150`) before the staleness step, so a
cold start without a token, an outage after "nothing playing", a failed cover download or an
ad all stay up unmarked for as long as reads fail. The marker has never been seen to trigger on
hardware (S-04's outage test left the Pi online over Wi-Fi).

## Desired End State

While a reader exists, every presentation carries the marker exactly when the existing
`_is_stale` rule says so, and loses it on the first good read; the log says e.g.
`board presentation: idle (stale)`. With Spotify not configured, idle stays plain. On the Pi,
a real outage marks the playing cover and, separately, idle, and the owner has judged the
marker legible across the room, lit and dark. OQ2 is resolved in the PRD and roadmap.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) |
| --- | --- | --- |
| OQ2: blind with no cover | Mark whatever is shown, same 30 s rule | One rule and one signal for every presentation, reusing the existing overlay, no new glyph to legibility-test. |
| Spotify not configured (no reader) | Plain idle, never marked | That is a setup/dev mode, not blindness, and stub runs stay clean. |
| Marker over the dim idle note | Same full-brightness marker everywhere | One signal with one look; brightness at night is judged on the board. |
| Hardware outage method | `iptables` REJECT on outbound 443 for ~90 s | Keeps LAN SSH for live `pm2 logs`, and breaks the reader's keep-alive connection, which a hosts-file edit would not. |
| Cold start before the first read | Plain idle until 30 s of failures | Uses `_is_stale` as is; a slow but healthy start is not marked. |
| If the owner finds the marker illegible | Stop and re-plan the marker | Redesigning the marker is out of this change's scope. |

## Scope

**In scope:**
- `select_presentation`, `_frame_for` and `Presentation.__str__` in `display.py`, with tests
- Docstrings in `display.py` / `frames.py`, and the OQ2 decision in `prd.md`, `roadmap.md`, `CLAUDE.md`
- Pi outage test (cover and idle), legibility sign-off, `verification.md`, the OQ4 marker result

**Out of scope:**
- A dedicated blind glyph, cause-specific markers, a dimmed marker
- Changes to the 30 s grace, marker shape/colour/position, or the reader
- Staleness while the board link is down (PRD Non-Goal); the status page (S-07)

## Architecture / Approach

Every branch of `select_presentation` after the no-reader check yields a chosen presentation,
and one final step applies `stale=_is_stale(snapshot, now)` whatever its kind. A presentation
kept through a changeover is re-evaluated, never carried over. `_frame_for` draws the base
frame and overlays `frames.with_stale_marker` when stale. The loop already pushes on a change
of the flag alone and retries a failed marker push, so it is not touched.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Marker on every presentation | Rule, frame, log and tests; OQ2 recorded in the docs; laptop stub check | A kept presentation carrying a stale flag from an earlier sample (covered by tests) |
| 2. Pi outage and legibility sign-off | Real outage on the Pi for a cover and for idle; owner's cross-room judgement; `verification.md` | The marker proves illegible across the room, which forces a re-plan |

**Prerequisites:** S-04 and S-05 merged (done); SSH to the Pi with sudo; the board within sight.
**Estimated effort:** ~1 session for phase 1, ~1 short session at the Pi for phase 2.

## Open Risks & Assumptions

- A slow Wi-Fi at boot (>30 s to the first read) shows a briefly marked idle; accepted as true.
- Rejecting outbound 443 briefly cuts all HTTPS from the Pi, including other Pi services.
- Assumes the marker clears every glyph at 16/32/64 px; a test pins it.

## Success Criteria (Summary)

- No presentation the service cannot vouch for stays on the board unmarked beyond 30 s, while the board link is up.
- The owner can tell from across the room that the board can no longer vouch for what it shows, over a cover and over idle.
- OQ2 is closed in the PRD and roadmap, and S-06 is no longer blocked.
