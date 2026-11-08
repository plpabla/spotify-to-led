# Unattended idle board (S-02) -- Plan Brief

> Full plan: `context/changes/unattended-idle-board/plan.md`

## What & Why

The board should come back by itself after the Pi loses power and show the idle display when nothing is playing, with no monitor, keyboard, login or manual start. This is the roadmap's north star: it makes the PRD's primary success criterion ("runs unattended, resumes after a reboot") true, and it is what lets the 7-day unattended observation (NFR-02) begin.

## Starting Point

The service connects to the board once at startup and pushes a diagnostic test pattern once. If the board is off or Bluetooth is not ready, that single attempt fails and nothing retries it (seen on the Pi during F-02). The Spotify reader already knows whether something is playing, but nothing turns that into what the board shows.

## Desired End State

After a power cut, the board shows a dim music note by itself whenever nothing is playing. If the board was off during boot, switching it on later is enough. While music plays or is paused it shows a temporary bright play triangle, until S-04 replaces it with artwork. The pm2 log records every connect attempt and frame change, and the 7-day run has a recorded start.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) |
| --- | --- | --- |
| Idle look | Dim, small music note centered on black, drawn in code | Reads as deliberately idle rather than broken, is calm for the many idle hours, and leaves room for a different S-05 symbol. |
| Playing or paused, before S-04 | A dedicated temporary placeholder (bright play triangle) | The board never claims idle while music plays, without reusing the diagnostic test pattern. |
| No successful read yet (cold boot, unauthorized, no Spotify) | Idle | Matches US-03's "never started since the service came up"; Open Question 2 stays with S-06. |
| Read failures | Ignored in this slice: the board keeps its last presentation | The staleness marker and the blind-with-no-cover answer belong to S-06. |
| Board-link scope | Retry the first connect forever, 5 s doubling to 60 s; re-push after a drop stays S-03 | Closes the observed cold-boot gap with a clean boundary to S-03. |
| Push policy | Sample the snapshot every 1 s, push only when the presentation changes; a failed push waits for the next change | Keeps the reader untouched and stays well inside the 5 s latency budget. |
| Done bar | Two real power cuts (board on; board off then on), then start the 7-day clock without gating on it | Proves both cold-boot paths without blocking S-03/S-04 for a week. |

## Scope

**In scope:**
- Idle frame and temporary playing placeholder
- A pure display rule mapping reader state to a presentation
- A supervised display task with retry-forever first connect, replacing the test-pattern push
- Tests, a docstring update and a `CLAUDE.md` update
- Pi rollout, legibility check, two power-cut runs, `verification.md`, roadmap note

**Out of scope:**
- Artwork, rendering, the cover cache (S-04, S-05)
- Staleness marker and Open Question 2 (S-06)
- Re-push after a link drop, retry of failed pushes (S-03)
- Status page and link state on any route (S-07)
- New config keys, pm2 or deploy-script changes

## Architecture / Approach

`display.select_presentation(snapshot)` is pure: PLAYING/PAUSED → placeholder; UNKNOWN, NOTHING_PLAYING or no reader → idle. `display.run_display(transport, size, current)` is the single owner of the board. It connects with backoff, then once a second reads `current()` (the reader's snapshot, or `None`) and pushes `frame_for(presentation)` only when the presentation changes. It catches and logs everything itself, like `SpotifyReader.run`, so the server keeps answering whatever the board does. `main.py`'s lifespan starts it after the reader, in place of `push_test_pattern`.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Frames and display rule | `frames.idle`, `frames.playing_placeholder`, `display.select_presentation`, unit tests | The glyph may need redesign once seen on the board |
| 2. Display task with connect retry | `run_display` replacing the one-shot push, app tests, docs | Log volume and retry behaviour must hold for days, not just in tests |
| 3. Pi rollout and cold-boot verification | Legibility judged, two power cuts passed, 7-day clock started | A real cold boot can expose BlueZ timing not seen in restarts |

**Prerequisites:** F-02 (pm2 supervision) and S-01 (Spotify reader) are done; the Pi has a valid Spotify grant; the owner is at the Pi to cut power.
**Estimated effort:** ~2 sessions of code across phases 1-2, plus one hands-on session at the Pi for phase 3.

## Open Risks & Assumptions

- Assumes `IDotMatrixClient.connect()` can be retried indefinitely after a failure without leaking BLE state; verified only by reading the library, and confirmed in Phase 3's board-off run.
- An unauthorized or blind service shows idle (or its last presentation), so the board alone cannot tell "nothing playing" from "cannot read" until S-06 and S-07.
- After a link drop the board keeps its old frame until the next presentation change; this is S-03's known gap.
- NFR-02 is unproven when this slice closes; a failure during the 7 days reopens this work.

## Success Criteria (Summary)

- After pulling the Pi's power with the board on, the board shows the idle display by itself.
- With the board off at boot, switching it on later brings up the idle display with no restart.
- Music starting and stopping moves the board between the placeholder and idle within 5 s, and the 7-day run has a recorded start.
