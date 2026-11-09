# Artwork on the Board (S-04) — Plan Brief

> Full plan: `context/changes/artwork-on-the-board/plan.md`
> Frame brief: `context/changes/artwork-on-the-board/frame.md`

## What & Why

The board should show the cover of whatever is playing and change it when the item changes.
From the frame: S-04 introduces the first board content that carries data and costs time
(a downloaded, rendered cover) into a display rule and transport built for payload-free,
instant, static frames. The future animation and LLM work stress exactly those two points
(*what kind of payload* the board receives, and *how long producing it takes*), so S-04 only
needs to get those right, not make "the whole functionality" open.

## Starting Point

The display loop shows idle or a green play-triangle placeholder, chosen by a two-value enum and
drawn inline (`display.py`). The reader already parses artwork URLs (album images for tracks,
own images for episodes) and keeps `failure` and `failure_since` on the snapshot, but the
display ignores failures.

## Desired End State

The board shows the playing item's cover, rendered with a treatment the owner chose on the real
board, and swaps it within 5 s of a skip. A failed download shows the placeholder and retries
on its own. After 30 s of failed Spotify reads, a showing cover gets a corner marker that clears
when reads recover. The Pi can take this build without breaking the "never unmarked stale" rule.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Track vs album art (OQ5) | Album images for tracks; episode images for podcasts | Spotify tracks have no images of their own, so there is nothing to choose. | Frame |
| Extensibility | One `render_cover(image, size)` seam; no GIF transport or plugin machinery | The future work crosses only the payload and render-time points; the rest is already indifferent. | Frame |
| Staleness at ship | Build FR-018's marker for a showing cover now (30 s grace) | Keeps `main` deployable to the Pi without waiting on OQ2. | Plan |
| Blind with no cover | Unchanged (idle / placeholder) | That is OQ2, still S-06's. | Frame |
| Item changeover | Keep the previous presentation until the new cover is ready | One push per skip, no flash; the gap is far inside 5 s. | Plan |
| Download failure | Placeholder, retry with 5 s→60 s backoff while the item stays | Never shows another item's cover; recovers unattended. | Plan |
| Rendering choice (OQ3) | Bake-off of 3 on the board: plain downscale, boosted downscale, palette-quantized | Covers both PRD options plus the boosted middle ground. | Plan |
| Cache | Only the currently wanted cover, in memory | The persistent FR-017 cache is S-05's. | Plan |

## Scope

**In scope:**
- Three renderer candidates, a bake-off script, the owner's choice and FR-007's bar
- Image selection (smallest ≥ board size), bounded download, cover loader with retry
- Display rule: cover / placeholder / idle, changeover, 30 s stale marker; wiring in `main.py`
- Pi rollout, NFR-01 and marker checks on hardware, PRD/roadmap/CLAUDE.md updates

**Out of scope:**
- Persistent frame cache and generic no-artwork symbol (S-05)
- Blind-with-no-cover state and final display-rule sign-off (S-06)
- GIF/animation payloads, LLM rendering, skip settle window

## Architecture / Approach

`run_display` samples once a second as today. Each sample it tells a `CoverLoader` which
artwork URL is wanted. The loader downloads with the app's existing `httpx2` client in its own
task, renders through `render_cover`, and reports pending, ready or failed. The rule turns the
snapshot, the loader state, the previous presentation and the clock into a hashable
`Presentation` (kind, cover URL, stale), so the existing redraw-on-change and
redraw-on-reconnect logic stays as it is. The loop keeps the frame it is showing, so a
changeover or a reconnect can still draw it.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Rendering candidates, marker & bake-off | Owner-chosen treatment, FR-007 bar, marker tuned on the board | No candidate reads well at 32x32 |
| 2. Cover pipeline | Selection, download, render seam, retrying loader | CDN latency eating the 5 s budget |
| 3. Display rule & loop | Covers, changeover, placeholder fallback, stale marker, wiring | Test harness rework breaks S-03's reconnect coverage |
| 4. Pi rollout & docs | Verified on hardware; OQ3/OQ5 closed; S-06 narrowed | Marker legibility from across the room |

**Prerequisites:** S-02/S-03 done (they are); Spotify authorized on the Pi; owner at the board for Phases 1 and 4.
**Estimated effort:** ~3–4 sessions; Phases 1 and 4 need the owner at the board.

## Open Risks & Assumptions

- Assumes the 64 px Spotify image is enough for a 32x32 render; selection falls back to larger ones.
- The marker is judged on covers here, but S-06 still owns its final cross-room legibility sign-off.
- Within the 30 s grace, and during the brief changeover, the board can show an unmarked cover that is no longer current. The PRD accepts both explicitly.

## Success Criteria (Summary)

- The playing cover is on the board and changes within 5 s of a skip.
- A cover the service cannot vouch for carries the marker after 30 s, and loses it on recovery.
- FR-007 has a written, falsifiable bar, met on the real board.
