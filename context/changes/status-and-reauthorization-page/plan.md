# Status and Re-authorization Page (S-07) Implementation Plan

## Overview

Serve one page at `/` that tells the owner, from any device behind the Cloudflare Access gate,
what the service believes is playing (or that nothing is), whether the board link is alive and
what the board was last sent, which cause is behind a failure to read Spotify, and that carries
the Spotify re-authorization action. It replaces the temporary JSON `/playback` route. This is
roadmap S-07, the last open item of M-1 (US-02, US-06, FR-004, FR-005, FR-015).

## Current State Analysis

From the frame brief (`context/changes/status-and-reauthorization-page/frame.md`): S-07 is the
only M-1 item still open, and the owner chose S-07 alone. Cover formatting options and any AI
placeholder are out of scope.

In the code:

- The believed state is already shaped for this page. `PlaybackSnapshot`
  (`src/spotify_to_led/spotify/playback.py:73`) keeps the item apart from read health:
  `failure` (`authorization_needed` / `network` / `api_error` / `rate_limited`),
  `failure_since`, `last_read_at`, `authorized_at`.
- `/playback` (`src/spotify_to_led/main.py:232`) serves that snapshot as JSON with each field
  named, so no token can leak through a field added elsewhere. Its docstring says S-07 replaces it.
- The board is invisible to the routes. The transport is a local inside `lifespan`
  (`main.py:104`), and the display task (`main.py:129`) is not stored. `BoardTransport` exposes
  `link_state` and `connections` (`src/spotify_to_led/board/transport.py:20-36`).
- `run_display` (`src/spotify_to_led/display.py:176`) knows the presentation it last chose
  (`previous`) and whether the last push failed (`push_failed`), but keeps both as locals.
- `/callback` (`main.py:188`) answers with fixed plain-text sentences, and success says "You can
  close this tab."
- `/healthz` stays process-only by design (`main.py:164-172`).
- No static assets exist yet. `tech-stack.md:29` declares "one static page with vanilla JS".

## Desired End State

Opening `https://<sub>.<owner-domain>/` shows a page that refreshes itself every few seconds and
reports:

- **Playback**: the playing or paused item (kind, name, subtitle), "nothing playing", "not read
  yet", or "Spotify is not configured".
- **Reading Spotify**: healthy, with the time of the last good read, or failing, with the named
  cause and how long it has been failing. It also says whether the board is now marked stale,
  which happens past 30 s.
- **Board**: link up or down. It is down also when the display task has ended. The page shows the
  presentation last sent (cover, idle, placeholder or no-artwork, and whether it was marked) and
  whether that push failed.
- **Spotify authorization**: when it was granted and an estimated expiry date (granted + 182
  days), plus a re-authorize action that is always visible. The action moves to the top, with the
  cause named, when authorization is needed or was never done. Beside it is the hint that an
  expired Access session mid-consent is fixed by running it again.

After a successful re-authorization, `/callback` lands the owner back on the page with a fixed
"authorized" notice. `/playback` is gone. Verify with `uv run pytest`, then on the Pi through the
Cloudflare edge.

### Key Discoveries:

- `infrastructure.md:126`: the status route must stay independent of the board task, and a crash
  there reads as link down.
- `infrastructure.md:128`: document the Access-expiry recovery next to the re-auth button.
- `infrastructure.md:110`: refresh tokens end 6 months after authorization, and refreshing does
  not extend them, so re-authorization is routine.
- `main.py:209`: Spotify's error code is echoed only when it matches `SPOTIFY_ERROR_CODE`. The
  new notice follows the same rule and echoes nothing from the query.
- Track and show names come from Spotify and are untrusted text for the DOM.

## What We're NOT Doing

- Selectable cover treatments (contrast, palette) and any AI-enhancement placeholder (frame brief:
  deferred; S-08 owns AI).
- Any auth, login or gating in service code (hard rule; Cloudflare Access is the only gate).
- Exposing the read failure's internal detail string (for example `HTTP 503`). The page names the
  cause class; the detail stays in the log.
- A cover thumbnail on the page, playback controls (hard rule: read-only), or history.
- A "renew soon" warning threshold. The page shows the estimated expiry date only.
- Changing `/healthz` or the display rule.

## Implementation Approach

The display loop publishes a tiny read-only status object, and `lifespan` makes the transport,
the display task and that object available on `app.state` next to the reader. A new `GET /status`
JSON route builds an explicit, field-by-field view from them and replaces `/playback`. A single
static `index.html` with inline vanilla JS is served at `/` and polls `/status`. `/callback`
redirects to `/?authorized=1` on success. All rendering of Spotify-sourced text uses
`textContent`.

## Critical Implementation Details

- **Link down when the task died**: the transport's `link_state` alone can read `UP` after the
  display task has ended, because no one drives reconnects any more. The route reports `down`
  whenever `display_task.done()`, whatever the transport says.
- **Untrusted text**: item `name` and `subtitle` are attacker-influenceable (anyone can name a
  podcast episode). Never assign them through `innerHTML`.

## Phase 1: Status data

### Overview

Make the display's last choice and push result, the link state and the task's liveness readable by
routes. Replace `/playback` with `/status`.

### Changes Required:

#### 1. Display status holder

**File**: `src/spotify_to_led/display.py`

**Intent**: Let `run_display` record, as it goes, the presentation it last attempted and whether
that push succeeded, so the page can report what the board was sent without reaching into the
loop.

**Contract**: A small mutable `DisplayStatus` (fields: `presentation: Presentation | None`,
`push_failed: bool`), passed to `run_display` as an optional keyword (`status: DisplayStatus | None
= None`). It is updated when a presentation is attempted and after each push. It is written only by
the loop and read by routes; there is no locking, since both run on the one event loop.

#### 2. Expose board state to routes

**File**: `src/spotify_to_led/main.py`

**Intent**: Store the transport, the display task and the `DisplayStatus` on `app.state` in
`lifespan`, beside `spotify_reader`.

**Contract**: `app.state.transport`, `app.state.display_task`, `app.state.display_status`.

#### 3. `GET /status` replaces `/playback`

**File**: `src/spotify_to_led/main.py`

**Intent**: One JSON answer the page renders, built field by field, as `/playback` is today.

**Contract**:

```
{
  "configured": bool,
  "playback": {status, item{kind,id,name,subtitle,artwork[]}|null, failure, failure_since,
               last_read_at} | null when not configured,
  "authorization": {authorized_at, expires_around} | null when not configured,
  "board": {link: "up"|"down", task_running: bool,
            presentation: {kind, url, stale} | null, push_failed: bool},
  "stale_after_seconds": 30,
  "now": <server UTC timestamp>
}
```

`expires_around` = `authorized_at + 182 days`, or null. `link` is `"down"` if the task is done.
Remove `/playback`, `SNAPSHOT_FIELDS` and their tests, and move the useful assertions to `/status`.

#### 4. Tests

**File**: `tests/test_app.py`, `tests/test_display.py`

**Intent**: Cover the new seam and route, including the guardrails.

**Contract**: `/status` without Spotify (`configured: false`, board section present); end to end
after authorization (playing item present, no access or refresh token anywhere in the body,
replacing the `/playback` end-to-end test); `link: "down"` when the display task has ended;
`expires_around` = `authorized_at + 182 days`; `run_display` fills `DisplayStatus` with the chosen
presentation and flips `push_failed` on a failing push.

### Success Criteria:

#### Automated Verification:

- Test suite passes: `uv run pytest`
- `/playback` no longer exists, and nothing in `src/` or `tests/` references it: `grep -rn "/playback" src tests` is empty
- The `/status` end-to-end test asserts no access or refresh token appears in the body

**Implementation Note**: After this phase passes, pause for confirmation before Phase 2.

---

## Phase 2: The page and the callback

### Overview

Serve the page at `/`, rendering `/status`, and bring the owner back to it after authorizing.

### Changes Required:

#### 1. Static page

**File**: `src/spotify_to_led/static/index.html` (new)

**Intent**: One self-contained HTML file with inline CSS and vanilla JS. It fetches `/status` on
load and every 3 s and renders the four sections from "Desired End State". The failure causes get
plain-language names (`authorization_needed` → "Spotify needs re-authorization", `network` →
"cannot reach Spotify", `api_error` → "Spotify answered with an error", `rate_limited` → "rate
limited by Spotify"). Durations come from the `now` in the response, so the Pi's clock and the
phone's clock never mix. If `/status` itself fails, the page says the service is not answering.

**Contract**: The re-authorize action is a plain link to `/authorize`. It is promoted (moved to
the top, with the cause named) when `playback.failure == "authorization_needed"` or
`authorization.authorized_at` is null. It is hidden when `configured` is false. The
Access-expiry hint sits beside it. With `?authorized=1` in the URL, the page shows a fixed
"Spotify authorization succeeded" notice and nothing taken from the query. All Spotify text goes
in through `textContent`.

#### 2. Serve the page

**File**: `src/spotify_to_led/main.py`

**Intent**: `GET /` returns the HTML file, resolved relative to the package, so it works from the
checkout under pm2 and in tests.

**Contract**: `GET /` → `200 text/html`. The file is read from
`Path(__file__).parent / "static" / "index.html"`.

#### 3. Callback lands on the page

**File**: `src/spotify_to_led/main.py`

**Intent**: Success redirects to the page. Error answers keep their fixed sentences and point
back to the page.

**Contract**: Success → `303` to `/?authorized=1`. Each error sentence gains "Status page: /".
Status codes are unchanged, and the code is still never echoed.

#### 4. Tests

**File**: `tests/test_app.py`

**Contract**: `GET /` serves HTML that references `/status` and `/authorize`; a successful callback
is a 303 to `/?authorized=1`; the existing callback error tests still pass with their codes; the
page source contains no `innerHTML` (a cheap guard on the untrusted-text rule).

### Success Criteria:

#### Automated Verification:

- Test suite passes: `uv run pytest`
- `index.html` has no `innerHTML`: `grep -c innerHTML src/spotify_to_led/static/index.html` prints 0

#### Manual Verification:

- On the laptop (`uv run python -m spotify_to_led.main`, stub board), the page renders the not-configured and the playing cases legibly on a desktop and a phone-width window

**Implementation Note**: After this phase passes, pause for confirmation before Phase 3.

---

## Phase 3: Verification on the Pi and docs

### Overview

Prove the page through the real edge against the real board, then update the living docs.

### Changes Required:

#### 1. Living docs

**File**: `CLAUDE.md`, `src/spotify_to_led/main.py` (module and `/healthz` docstrings)

**Intent**: Replace "a temporary JSON `/playback` that S-07 replaces" and "The status page is not
built yet" with the page and `/status`. Drop the `/healthz` docstring's "scheduled for S-07".

**Contract**: The overview paragraph in `CLAUDE.md`, and the docstrings named above.

#### 2. Verification record

**File**: `context/changes/status-and-reauthorization-page/verification.md` (new)

**Intent**: Record the manual checks below with times and log excerpts, as earlier slices did.

### Success Criteria:

#### Automated Verification:

- Test suite passes: `uv run pytest`
- No stale mentions remain: `grep -n "playback\`\|not built yet" CLAUDE.md` is empty

#### Manual Verification:

- Deployed to the Pi: the page opens through the Cloudflare edge from a phone off the home Wi-Fi, and shows the playing item and link up
- Board switched off: the page shows link down within a few seconds while the page keeps answering; after it is switched back on, link up and the presentation are redrawn
- Spotify outage (outbound HTTPS rejected, as in S-06) for over 30 s: the page names "cannot reach Spotify", shows the failing duration and that the board is marked stale; it clears after the outage
- Re-authorize round trip from the page: Spotify consent, back on the page with the notice, and authorized-at and expiry updated
- The page body and `pm2 logs` contain no access token, refresh token or authorization code

## Testing Strategy

### Unit Tests:

- `DisplayStatus` updates: attempted presentation and push success or failure (`tests/test_display.py`).
- `/status` field mapping: not configured, never authorized, authorized, task ended → link down.

### Integration Tests:

- The lifespan end to end with `FakeSpotify`: authorize, callback (303), then `/status` reports the playing item with no tokens.

### Manual Testing Steps:

1. Open the page through the edge from a phone and check every section.
2. Switch the board off and on; watch the board section.
3. Reject outbound HTTPS for over 30 s; watch the cause, the duration and the stale flag; restore.
4. Click re-authorize; complete consent; confirm the landing notice and updated dates.

## Performance Considerations

A 3 s poll from one owner's browser against an in-memory snapshot is negligible on the Pi.

## Migration Notes

`/playback` is removed. Nothing outside the tests consumes it. The S-01 verification notes that
mention it are archived history and stay as they are.

## References

- Frame brief: `context/changes/status-and-reauthorization-page/frame.md`
- Roadmap S-07: `context/foundation/roadmap.md`
- PRD: US-06, FR-004, FR-005, FR-015 (`context/foundation/prd.md`)
- `src/spotify_to_led/main.py:164-261`, `src/spotify_to_led/display.py:176-260`,
  `src/spotify_to_led/spotify/playback.py:37-82`, `src/spotify_to_led/board/transport.py:13-42`
- `context/foundation/infrastructure.md:110, 126, 128`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Status data

#### Automated

- [x] 1.1 Test suite passes: `uv run pytest`
- [x] 1.2 `/playback` no longer exists, and nothing in `src/` or `tests/` references it: `grep -rn "/playback" src tests` is empty
- [x] 1.3 The `/status` end-to-end test asserts no access or refresh token appears in the body

### Phase 2: The page and the callback

#### Automated

- [ ] 2.1 Test suite passes: `uv run pytest`
- [ ] 2.2 `index.html` has no `innerHTML`: `grep -c innerHTML src/spotify_to_led/static/index.html` prints 0

#### Manual

- [ ] 2.3 On the laptop (`uv run python -m spotify_to_led.main`, stub board), the page renders the not-configured and the playing cases legibly on a desktop and a phone-width window

### Phase 3: Verification on the Pi and docs

#### Automated

- [ ] 3.1 Test suite passes: `uv run pytest`
- [ ] 3.2 No stale mentions remain: `grep -n "playback\`\|not built yet" CLAUDE.md` is empty

#### Manual

- [ ] 3.3 Deployed to the Pi: the page opens through the Cloudflare edge from a phone off the home Wi-Fi, and shows the playing item and link up
- [ ] 3.4 Board switched off: the page shows link down within a few seconds while the page keeps answering; after it is switched back on, link up and the presentation are redrawn
- [ ] 3.5 Spotify outage (outbound HTTPS rejected, as in S-06) for over 30 s: the page names "cannot reach Spotify", shows the failing duration and that the board is marked stale; it clears after the outage
- [ ] 3.6 Re-authorize round trip from the page: Spotify consent, back on the page with the notice, and authorized-at and expiry updated
- [ ] 3.7 The page body and `pm2 logs` contain no access token, refresh token or authorization code
