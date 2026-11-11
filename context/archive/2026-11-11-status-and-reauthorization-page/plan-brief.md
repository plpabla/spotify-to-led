# Status and Re-authorization Page (S-07) — Plan Brief

> Full plan: `context/changes/status-and-reauthorization-page/plan.md`
> Frame brief: `context/changes/status-and-reauthorization-page/frame.md`

## What & Why

> **The actual problem to plan around is**: deliver S-07 as the roadmap specifies it: one page,
> independent of the board task, that reports the believed-playing item (or that nothing is),
> board link health, which cause is behind a failed read, and offers Spotify re-authorization,
> never exposing the refresh credential.

It is the last open item of M-1, and the only surface that can say the board link is down or
why the board's stale marker is on.

## Starting Point

The reader's `PlaybackSnapshot` already separates the item from the read-failure cause and its
start time, and the temporary JSON `/playback` serves it. The board's link state and the display
loop's last presentation are not reachable from any route, there is no page, and `/callback`
answers in plain text.

## Desired End State

`https://<sub>.<owner-domain>/` shows a self-refreshing page with four parts: playback, Spotify
read health (cause and duration), the board (link, what was last sent, stale or not, push failed),
and authorization (granted and estimated expiry, with an always-visible re-authorize link that
moves to the top when needed). Authorizing returns the owner to the page. `/playback` is gone.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Scope | S-07 only; no cover formatting, no AI placeholder | The owner chose closing M-1; formatting costs a cache variant and needs an FR | Frame |
| Page technology | One static `index.html`, vanilla JS, polling JSON | Declared in `tech-stack.md`; no templates or build step | Research |
| Data route | `GET /status`, field by field, replaces `/playback` | Keeps the no-token guarantee `/playback` already has | Plan |
| Board section | Link up/down, plus last presentation, stale flag and push result | Lets the page be compared with the physical board, and catches "link up, pushes failing" | Plan |
| Dead display task | Reported as link down | `infrastructure.md:126` requires a board-task crash to read as link down | Research |
| After `/callback` | 303 to `/?authorized=1` with a fixed notice; errors point to `/` | The owner sees the page turn healthy; nothing from the query is echoed | Plan |
| Re-auth action | Always visible, promoted when needed; granted date + ~182-day expiry estimate; Access-expiry hint | Six-month token expiry makes renewal routine, not only a breakage path | Plan |
| Untrusted text | Spotify names go in via `textContent` only | Episode and track names are attacker-influenceable | Plan |

## Scope

**In scope:**
- `DisplayStatus` seam in `display.py`; transport, task and status on `app.state`
- `GET /status`; removal of `/playback`
- `src/spotify_to_led/static/index.html` served at `/`
- `/callback` success redirect and error back-links
- Tests, `CLAUDE.md` and docstring updates, a verification record from the Pi

**Out of scope:**
- Cover treatments, AI enhancement, cover thumbnail, history, controls
- Any auth in the app; a "renew soon" threshold; changes to `/healthz` or the display rule

## Architecture / Approach

The display loop writes a two-field status object as it attempts and pushes presentations.
`lifespan` puts the transport, the display task and that object on `app.state`. `/status` reads
them and the reader's snapshot and returns explicit JSON. The static page polls it every 3 s and
renders it.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Status data | `DisplayStatus`, `/status` replacing `/playback`, tests | A token reaching the JSON through a careless field; guarded by an explicit mapping and test |
| 2. The page and the callback | `index.html` at `/`, callback redirect, tests | Untrusted Spotify text injected into the DOM; guarded by `textContent` and a grep |
| 3. Verification on the Pi and docs | Edge, board-off, outage and re-auth checks; docs | The Access session expiring mid-consent during the round trip; re-run fixes it |

**Prerequisites:** S-01, F-03 and S-06 done (they are); Pi access for Phase 3.
**Estimated effort:** ~1–2 sessions across 3 phases.

## Open Risks & Assumptions

- The expiry date is an estimate (granted + 182 days); Spotify does not report it.
- Assumes the static file is read from the source checkout (pm2 runs from it), not from a built wheel.

## Success Criteria (Summary)

- From a phone off the home network, the owner can tell what is playing, whether the board is
  reachable, and why the board is marked stale.
- The owner can re-authorize Spotify from the page and land back on it, with no restart.
- No token or authorization code ever appears on the page or in the logs.
