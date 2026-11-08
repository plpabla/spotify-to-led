# Spotify authorization and playback read (S-01) Implementation Plan

## Overview

Teach the service to authorize the owner's Spotify account once via Authorization Code with PKCE, persist the rotating token set itself, and keep reading what is playing every 3 seconds, unattended, for as long as the credential stays valid. S-01 ends at "the service knows what is playing": nothing new is drawn on the board. The believed state is exposed through a temporary JSON route so the slice can be verified before S-07's status page exists.

The frame (`frame.md`) reframed the problem: the API mechanics hold, and what the plan must get right is how the service owns a short-lived, mutable credential -- where the rotating token and its authorization timestamp live on the Pi, and treating `invalid_grant` (guaranteed at least every 6 months) as a routine event rather than an edge case.

## Current State Analysis

- `src/spotify_to_led/main.py` is a FastAPI app whose lifespan builds a board transport and runs one background task (`push_test_pattern`, `main.py:36-46`) that catches and logs every failure so `/healthz` keeps answering. No Spotify code exists.
- `src/spotify_to_led/config.py` has no Spotify fields. `.env.example` already documents `SPOTIFY_CLIENT_ID` (empty) and `SPOTIFY_REDIRECT_URI` (a placeholder `https://led.example.com/callback`); the Pi's `/etc/spotify-to-led/env` carries real values.
- `httpx2` 2.16.0 is in the `dev` group only (`pyproject.toml:21-25`); `tech-stack.md:39` records that nothing in the runtime uses it.
- `infrastructure.md:110` says the refresh token lives in `/etc/spotify-to-led/env` and is "rewritten in place". The frame verified on the Pi that `/etc/spotify-to-led` is `755 root:root`, so the daemon user `pi-n8n` cannot do an atomic temp-file-and-rename there, and the file also holds hand-edited config.
- Spotify facts carried from the frame (fetched 2026-11-08): PKCE needs no secret; access tokens last 3600 s; a refresh response may omit `refresh_token` (keep the old one); refresh tokens live 6 months from authorization and refreshing does not extend them; expiry is `400 invalid_grant` -- do not retry, discard, re-authorize. `GET /v1/me/player/currently-playing?additional_types=track,episode` needs scope `user-read-currently-playing`, returns 204 with no body when nothing is active, `currently_playing_type` in `track/episode/ad/unknown`, `item` may be `null`; rate limit is a rolling 30 s window with an unpublished number and `429 + Retry-After`.
- `var/` is gitignored and already used for stub frames (`config.py` `stub_frame_dir`).

## Desired End State

On the Pi, the owner opens `https://<sub>.<domain>/authorize`, consents to the single read-only scope, lands back on `/callback`, and from then on `GET /playback` reports the item playing (track or episode, with artwork URLs), the paused item, or that nothing is playing, within about 3 seconds of a change, across restarts, and for as long as the refresh token stays valid. When the credential ends, the service discards it, reports `authorization_needed`, and resumes as soon as the owner authorizes again, with no restart. The token never appears in logs or in any response.

### Key Discoveries:

- `scripts/deploy.sh:29` runs `uv sync --frozen --no-dev`: a dev-only `httpx2` would be absent on the Pi and the service would fail on import.
- `ecosystem.config.js` sets `cwd: __dirname`, so a relative `var/spotify-token.json` resolves inside the checkout owned by `pi-n8n` on the Pi, with no new setup step.
- `.env.example` ships `SPOTIFY_CLIENT_ID=` (empty string) next to a non-empty placeholder redirect URI: a laptop `.env` copied from it must read as "Spotify not configured", not as an error.
- Pattern to follow: background work that swallows its own failures and reports state (`main.py:36-46`), settings validated at construction (`config.py:72-80`), tests driving the app through `TestClient` with a monkeypatched factory (`tests/test_app.py:70-77`).

## What We're NOT Doing

- No board output: the idle display (S-02), artwork rendering (S-04), the staleness marker (S-06) and the new **pause icon over the paused cover** (owner decision during this planning: a paused item keeps its cover and gets a pause icon on top). S-01 only models `paused` as a distinct state; drawing it, and adding it as a fifth presentation to FR-009's display rule, belongs to the display slices.
- No status page, re-authorization button or cause wording (S-07). `/playback` is a temporary JSON route that S-07 replaces.
- No proactive warning of the 6-month expiry; `authorized_at` is recorded and exposed so S-07 can show it.
- No live Spotify on the laptop: the laptop runs mocked tests only (owner decision); no loopback redirect URI is registered. The first live authorization is on the Pi.
- No Spotify library (spotipy, tekore): raw HTTP, per the frame.
- No write or extra scopes, no `user-read-playback-state`, no playback control.
- No ETag/conditional requests and no adaptive cadence (an idle-period slowdown would break NFR-01 for the nothing-playing to playing transition).
- No edits to the PRD; the pause-icon decision is recorded here and in the brief for S-02/S-04 to carry into FR-009.

## Implementation Approach

A small `spotify_to_led.spotify` package with three responsibilities, each tested in isolation against `httpx2.MockTransport`:

1. **Auth** -- pure PKCE helpers plus the two token POSTs, returning a token set or raising a distinct "authorization revoked" error on `invalid_grant`.
2. **Token store** -- load / atomic save / discard of one JSON file, mode 0600.
3. **Reader** -- parses `currently-playing` into an immutable snapshot and runs the poll loop: wait for a token, refresh before expiry, read, classify the outcome, sleep, repeat. It never raises out of its loop, logs only on change, and wakes on an event that `/callback` sets.

`main.py` wires them: an `httpx2.AsyncClient` and the reader are created in the lifespan when a client ID is configured, the reader runs as a second supervised task beside the existing board task, and three routes (`/authorize`, `/callback`, `/playback`) sit beside `/healthz`.

The believed state separates *what is playing* from *whether the last read succeeded*: on a read failure the last known item is kept and the failure cause plus its start time are set. That is the shape S-06 needs (keep the cover, mark it after 30 s) and S-07 needs (name the cause).

## Critical Implementation Details

- **Dependency group**: promote `httpx2` with `uv remove --dev httpx2` then `uv add "httpx2>=2.16.0"` so `uv.lock` stays in sync; the deploy's `--no-dev` sync otherwise drops it (see Key Discoveries).
- **Refresh-token rotation ordering**: persist the new token set (keeping the old `refresh_token` when the response omits one, and always preserving `authorized_at` from the original grant) *before* using the new access token. If the write fails, log it and keep running in memory; a lost rotation surfaces later as `invalid_grant`, which is already the routine recovery path.
- **Secrets in logs**: the authorization `code`, `code_verifier`, access and refresh tokens must never be logged or returned, including inside exception messages from failed POSTs (log status code and Spotify's `error` field only).

## Phase 1: Spotify client core

### Overview

Settings, PKCE, the two token calls and the token store: everything needed to obtain and keep a credential, with no loop and no routes yet.

### Changes Required:

#### 1. Spotify settings

**File**: `src/spotify_to_led/config.py`

**Intent**: Add the Spotify configuration as optional, so a machine without it (the laptop, the test suite) still starts and simply runs no reader, while a half-configured Pi fails at startup with a readable error.

**Contract**: New fields `spotify_client_id: str | None = None`, `spotify_redirect_uri: str | None = None`, `spotify_token_file: Path = Path("var/spotify-token.json")`. An empty or whitespace `spotify_client_id` normalises to `None`. A model validator rejects a set client ID with a missing redirect URI. A property (e.g. `spotify_enabled`) is true iff a client ID is set. The redirect URI is used verbatim, never derived from a request.

#### 2. PKCE and token calls

**File**: `src/spotify_to_led/spotify/auth.py` (new; plus `spotify/__init__.py`)

**Intent**: Build the authorize URL and perform the code exchange and refresh POSTs against `accounts.spotify.com`, so the rest of the service only sees a typed token set or a clear "re-authorization needed" signal.

**Contract**:
- `SCOPE = "user-read-currently-playing"` -- the only scope requested (hard rule: read-only).
- A `PendingAuthorization` value holding `state` and `code_verifier`; a factory creating one (verifier from `secrets`, 43-128 chars of the unreserved set; challenge = base64url(SHA-256(verifier)) without padding).
- `authorize_url(client_id, redirect_uri, pending) -> str` with `client_id, response_type=code, redirect_uri, code_challenge_method=S256, code_challenge, state, scope`.
- `async exchange_code(client, client_id, redirect_uri, code, code_verifier) -> TokenSet` (form POST, `grant_type=authorization_code`, no Authorization header).
- `async refresh(client, client_id, token_set) -> TokenSet` (form POST, `grant_type=refresh_token`); keeps the old `refresh_token` when none is returned and carries `authorized_at` forward.
- `TokenSet` (frozen dataclass): `access_token`, `refresh_token`, `expires_at` (UTC datetime from `expires_in`), `authorized_at` (UTC datetime of the code exchange), `scope`.
- `AuthorizationRevoked` exception raised on `400` with `error == "invalid_grant"`; any other failure raises a distinct transient error (`SpotifyAuthError` or similar) carrying status and Spotify's `error` field but no request body.

#### 3. Token store

**File**: `src/spotify_to_led/spotify/token_store.py` (new)

**Intent**: Persist the token set as mutable runtime state owned by the process, atomically, so a crash mid-write can never leave a half-written credential, and so the env file stays read-only configuration.

**Contract**: `TokenStore(path: Path)` with `load() -> TokenSet | None` (missing file -> `None`; unreadable or malformed JSON -> log a warning without contents and return `None`), `save(token_set) -> None` (create parent dirs; write a temp file in the same directory opened with mode `0o600`, flush + fsync, `os.replace` onto the target), `discard() -> None` (unlink, tolerating absence). JSON keys mirror `TokenSet`, datetimes as ISO-8601 UTC.

#### 4. Tests

**Files**: `tests/test_config.py` (extend), `tests/test_spotify_auth.py`, `tests/test_token_store.py` (new)

**Intent**: Pin the behaviour above against `httpx2.MockTransport`, so the laptop verifies the Spotify layer with no account.

**Contract**: Cases listed under Testing Strategy, Phase 1.

### Success Criteria:

#### Automated Verification:

- Full suite passes: `uv run pytest`
- Settings tests cover: no client ID -> Spotify disabled; empty `SPOTIFY_CLIENT_ID=` with placeholder redirect URI -> disabled, no error; client ID without redirect URI -> validation error; default token file is `var/spotify-token.json`
- Auth tests cover: authorize URL carries exactly the seven parameters and `scope=user-read-currently-playing`; challenge matches the RFC 7636 test vector; exchange and refresh send form bodies without an Authorization header; refresh without `refresh_token` keeps the old one and preserves `authorized_at`; `400 invalid_grant` raises `AuthorizationRevoked`; other errors raise the transient error with no token or code in its message
- Token store tests cover: save/load round trip; missing file -> `None`; malformed file -> `None`; discard of a missing file is a no-op; on POSIX the saved file mode is `0600`; a failure during write leaves the previous file intact

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human before proceeding to the next phase.

---

## Phase 2: Playback reader

### Overview

Turn `currently-playing` responses into the believed state, and run the unattended poll loop that keeps the token fresh and classifies every failure.

### Changes Required:

#### 1. Believed-state model and parsing

**File**: `src/spotify_to_led/spotify/playback.py` (new)

**Intent**: One immutable snapshot of what the service believes, shaped for the downstream display rule: what is playing is kept separate from whether reading currently works.

**Contract**:
- `PlaybackStatus` enum: `UNKNOWN` (no successful read since start), `PLAYING`, `PAUSED`, `NOTHING_PLAYING`.
- `ReadFailure` enum: `AUTHORIZATION_NEEDED`, `NETWORK`, `API_ERROR`, `RATE_LIMITED`.
- `Artwork(url, width, height)`; `PlayingItem(kind, id, name, subtitle, artwork: tuple[Artwork, ...])` where `kind` is `track/episode/ad/unknown`, `subtitle` is the artists joined for a track or the show name for an episode, `artwork` comes from `item.album.images` (track) or `item.images` (episode) in Spotify's order (widest first), and `id`/`name` may be `None`.
- `PlaybackSnapshot(status, item, failure, failure_since, last_read_at, authorized_at)` (frozen).
- `parse_currently_playing(status_code, body) -> (PlaybackStatus, PlayingItem | None)`: 204 or empty body -> `NOTHING_PLAYING`; an item with `is_playing: true` -> `PLAYING`; with `is_playing: false` -> `PAUSED` (owner decision: a paused item stays the believed item); `item: null` -> a `PlayingItem` of kind `currently_playing_type` with no id, name or artwork, status from `is_playing`.

#### 2. Reader loop

**File**: `src/spotify_to_led/spotify/reader.py` (new)

**Intent**: Keep reading the account with no human action for as long as the credential is valid, never crash, and always leave an accurate snapshot behind.

**Contract**: `SpotifyReader(client, settings, store, *, interval=3.0, clock/sleep injectable for tests)` exposing `snapshot` (property), `begin_authorization() -> str` (creates and holds one pending authorization, last call wins, returns the authorize URL), `async complete_authorization(code, state)` (rejects an unknown or mismatched `state`, exchanges the code, saves, sets the wake event; the pending authorization is consumed either way), and `async run()`. Loop behaviour, per tick:
- No token -> failure `AUTHORIZATION_NEEDED`, wait on the wake event (no polling).
- Access token expiring within 60 s -> refresh first; `AuthorizationRevoked` -> `store.discard()`, failure `AUTHORIZATION_NEEDED`, wait.
- `GET https://api.spotify.com/v1/me/player/currently-playing?additional_types=track,episode` with a 5 s timeout.
- 200/204 -> update status and item, clear failure, set `last_read_at`.
- 401 -> refresh once and retry on the next tick (same revoked handling).
- 429 -> `RATE_LIMITED`; next sleep is `max(Retry-After, interval)` (default 30 s when the header is missing).
- Other 4xx/5xx -> `API_ERROR`; transport errors and timeouts -> `NETWORK`.
- On any failure the previous `status` and `item` are kept; `failure_since` is set on the first failure of a run and cleared on success.
- Sleep `interval` (3 s) between reads; the wake event also interrupts the sleep.
- Logs at INFO only when status, item identity or failure cause changes; at WARNING when authorization is needed; never tokens, codes or verifiers. Any unexpected exception inside a tick is logged and the loop continues.

#### 3. Tests

**Files**: `tests/test_playback_parse.py`, `tests/test_spotify_reader.py` (new)

**Intent**: Drive every branch above with canned responses and a fake clock, so no real time passes and no account is needed.

**Contract**: Cases listed under Testing Strategy, Phase 2.

### Success Criteria:

#### Automated Verification:

- Full suite passes: `uv run pytest`
- Parsing tests cover 204, playing track, paused track, playing episode (artwork from `item.images`), `item: null` ad, `item: null` unknown type, and missing `images`
- Reader tests cover: no token -> `AUTHORIZATION_NEEDED` and no GET issued; `complete_authorization` wakes the loop and the next snapshot is read; mismatched `state` is rejected and nothing is saved; near-expiry token is refreshed before the GET and the new set is persisted; 401 triggers one refresh; `invalid_grant` discards the file and waits; 429 honours `Retry-After`; 5xx -> `API_ERROR`; transport error -> `NETWORK`; item kept and `failure_since` stable across consecutive failures; success clears the failure; an unexpected exception does not end `run()`
- A test captures logs across a full authorize-refresh-read cycle and asserts no access token, refresh token, code or verifier string appears

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human before proceeding to the next phase.

---

## Phase 3: Wiring, docs and live verification

### Overview

Expose the reader through the app, promote `httpx2` to a runtime dependency, correct the foundation docs for the new token location, then deploy and verify live on the Pi.

### Changes Required:

#### 1. App wiring

**File**: `src/spotify_to_led/main.py`

**Intent**: Run the reader as a second supervised task beside the board task and add the three routes, keeping `/healthz` unchanged.

**Contract**:
- Lifespan: when `settings.spotify_enabled`, create one `httpx2.AsyncClient`, a `TokenStore(settings.spotify_token_file)` and the `SpotifyReader`, store the reader on `app.state`, and start `reader.run()` as a task; on shutdown cancel the task (tolerating completion) and close the client. When disabled, log once at INFO that Spotify is not configured.
- `GET /authorize` -> `302` to `reader.begin_authorization()`; `503` with a short text when Spotify is not configured.
- `GET /callback` -> on `error` query param: `400` naming Spotify's error; on missing/mismatched `state`: `400`; on exchange failure: `502`; on success: `200` plain text saying authorization succeeded and the tab can be closed. Never echoes the code.
- `GET /playback` (temporary, docstring says S-07 replaces it) -> JSON of the snapshot: `configured`, `status`, `item` (kind, id, name, subtitle, artwork list), `failure`, `failure_since`, `last_read_at`, `authorized_at`. No token fields, ever.

#### 2. Dependency promotion

**Files**: `pyproject.toml`, `uv.lock`

**Intent**: Make `httpx2` a runtime dependency so the Pi's `--no-dev` sync installs it.

**Contract**: `httpx2>=2.16.0` moves from `[dependency-groups].dev` to `[project].dependencies`, via `uv remove --dev httpx2 && uv add "httpx2>=2.16.0"`.

#### 3. App tests

**File**: `tests/test_app.py` (extend)

**Intent**: Cover the routes and the degradation behaviour at the app level, using the same monkeypatch style as the existing board tests.

**Contract**: Cases listed under Testing Strategy, Phase 3.

#### 4. Docs

**Files**: `.env.example`, `context/foundation/infrastructure.md`, `context/foundation/tech-stack.md`

**Intent**: Remove the now-wrong assumption that the token lives in the env file, and record the dependency change, in each doc's existing dated-note style.

**Contract**:
- `.env.example`: Spotify section header no longer says "ignored until then"; note that an empty `SPOTIFY_CLIENT_ID` disables the reader; add commented `# SPOTIFY_TOKEN_FILE=var/spotify-token.json` with one line on what it holds.
- `infrastructure.md` Secrets bullet (line 110): an "Updated, 2026-11-08" note -- the refresh token is not in `/etc/spotify-to-led/env`; the service writes the token set atomically to `var/spotify-token.json` in the checkout (mode 0600, owned by `pi-n8n`), and the env file stays read-only config; refresh tokens expire 6 months after authorization (Spotify, from 2026-07-20) so re-authorization via `/authorize` is a routine event; `git clean -fdx` deletes the token and forces a re-authorization.
- `tech-stack.md`: a "Recorded, 2026-11-08" note that `httpx2` is now a runtime dependency for the raw-HTTP Spotify client, superseding the 2026-11-04 note's "nothing in the runtime dependencies uses it".

#### 5. Live verification record

**File**: `context/changes/spotify-integration/verification.md` (new, written during manual verification)

**Intent**: Record what the live account actually returned for the frame's soft points (paused, private session, stopped, rotation), as earlier changes did.

**Contract**: One section per manual check below with date, observation and the `/playback` JSON (no tokens).

### Success Criteria:

#### Automated Verification:

- Full suite passes: `uv run pytest`
- `httpx2` is listed under `[project].dependencies` and absent from the `dev` group in `pyproject.toml`, and `uv lock --check` passes
- App tests cover: without a client ID the app starts, `/healthz` is 200, `/authorize` is 503 and `/playback` reports `configured: false`; with a client ID `/authorize` redirects to `accounts.spotify.com/authorize` with the configured redirect URI; `/callback` with a bad `state` or an `error` param is 400; `/playback` JSON contains no token values after a mocked authorization

#### Manual Verification:

- Deploy with `scripts/deploy.sh`; `pm2 ls` shows the app online and `/healthz` answers
- Before authorizing, `/playback` shows `failure: authorization_needed` and the logs show no polling
- Opening `https://<sub>.<domain>/authorize` in a browser shows a Spotify consent screen listing only the currently-playing permission, and `/callback` reports success
- `var/spotify-token.json` on the Pi is mode `-rw-------`, owned by `pi-n8n`, and `/etc/spotify-to-led/env` is unchanged
- Playing a track, pausing it, playing a podcast episode and stopping playback are each reflected in `/playback` within about 3 seconds (`playing`, `paused` with the same item, `episode` with artwork, then `nothing_playing` or `paused`, as observed and recorded)
- A private-session read is observed and its `/playback` result recorded
- After `pm2 restart spotify-to-led` the reader resumes without re-authorization
- After more than an hour, `expires_at` in the token file has advanced, the logs show no `rate_limited` and no errors
- Removing the app's access at spotify.com/account/apps leads to `authorization_needed` and a discarded token file; authorizing again via `/authorize` resumes reading with no restart
- `pm2 logs spotify-to-led --lines 500 --nostream` contains no token, code or verifier value
- Observations recorded in `context/changes/spotify-integration/verification.md`

**Implementation Note**: The consent step and the account-access removal are human-only (`infrastructure.md` Approval). The agent may deploy and read logs; the owner performs the browser steps.

---

## Testing Strategy

### Unit Tests:

- **Phase 1**: settings enable/disable and validation; PKCE challenge against RFC 7636 Appendix B; authorize URL parameters and the single scope; exchange/refresh request bodies and headers; refresh without a new `refresh_token`; `invalid_grant` vs other errors; error messages free of secrets; token store round trip, missing, malformed, discard, mode 0600 (POSIX only, skipped on Windows), atomic write leaving the old file on failure.
- **Phase 2**: every `parse_currently_playing` case listed above; reader branches driven by a `MockTransport` handler that returns a scripted sequence, with injected clock and sleep so tests run instantly; log capture for secret leakage.

### Integration Tests:

- **Phase 3**: `TestClient` over the real app with Spotify disabled (existing tests keep passing unchanged) and with a client ID plus a mocked Spotify transport covering authorize -> callback -> `/playback`.

### Manual Testing Steps:

1. Deploy, check `/playback` before authorization.
2. Authorize from a browser through the Access-gated hostname.
3. Walk through play, pause, episode, private session and stop; record each `/playback` result.
4. Restart under pm2; confirm no re-authorization.
5. Leave it for over an hour; confirm a refresh happened and no rate limiting.
6. Revoke access at spotify.com, confirm `authorization_needed`, re-authorize, confirm it resumes.

## Performance Considerations

At a 3 s interval the reader makes about 10 calls per 30 s window, within the community-reported safe range (1 s polling is throttled). Detection latency is at most interval plus request time (about 3-4 s), leaving roughly 1 s of NFR-01's 5 s budget for S-04's render and push; S-04 should re-check this budget. A 429 backs off by `Retry-After`, never faster than the interval.

## Migration Notes

None for data. On the Pi, the first deploy needs one owner-performed authorization through `/authorize`; nothing is moved out of `/etc/spotify-to-led/env`, which never held a token.

## References

- Frame brief: `context/changes/spotify-integration/frame.md`
- PRD: `context/foundation/prd.md` (FR-001, FR-002, FR-003, FR-016, US-02, Guardrails)
- Roadmap: `context/foundation/roadmap.md` (S-01)
- Infrastructure: `context/foundation/infrastructure.md:99,104,110`
- Supervised-task pattern: `src/spotify_to_led/main.py:36-46`
- Settings pattern: `src/spotify_to_led/config.py:72-102`
- App test pattern: `tests/test_app.py:70-77`
- Deploy sync flags: `scripts/deploy.sh:29`
- Prior PKCE decision: `context/archive/spotify-redirect-uri-policy/research.md`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` -- <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Spotify client core

#### Automated

- [x] 1.1 Full suite passes: `uv run pytest` -- fb47843
- [x] 1.2 Settings tests cover disabled, empty client ID, missing redirect URI and default token file -- fb47843
- [x] 1.3 Auth tests cover URL parameters, PKCE vector, request bodies, refresh rotation, invalid_grant and secret-free errors -- fb47843
- [x] 1.4 Token store tests cover round trip, missing, malformed, discard, mode 0600 and atomic write -- fb47843

### Phase 2: Playback reader

#### Automated

- [x] 2.1 Full suite passes: `uv run pytest` -- 5084aae
- [x] 2.2 Parsing tests cover 204, playing, paused, episode, null-item ad and unknown, missing images -- 5084aae
- [x] 2.3 Reader tests cover authorization wait and wake, state check, refresh, 401, invalid_grant, 429, 5xx, network, kept item and loop survival -- 5084aae
- [x] 2.4 Log capture across a full cycle contains no token, code or verifier -- 5084aae

### Phase 3: Wiring, docs and live verification

#### Automated

- [x] 3.1 Full suite passes: `uv run pytest` -- 6e1b7fa
- [x] 3.2 httpx2 is a runtime dependency and `uv lock --check` passes -- 6e1b7fa
- [x] 3.3 App tests cover disabled Spotify, authorize redirect, callback rejections and token-free /playback -- 6e1b7fa

#### Manual

- [x] 3.4 Deploy with scripts/deploy.sh; app online and /healthz answers
- [x] 3.5 Before authorizing, /playback shows authorization_needed and no polling
- [x] 3.6 Browser consent lists only the currently-playing permission and /callback reports success
- [x] 3.7 Token file is 0600 owned by pi-n8n and the env file is unchanged
- [ ] 3.8 Play, pause, episode and stop are reflected in /playback within about 3 seconds
- [ ] 3.9 Private-session read observed and recorded
- [ ] 3.10 Reader resumes after pm2 restart without re-authorization
- [ ] 3.11 After an hour the token has refreshed, with no rate limiting or errors
- [ ] 3.12 Revoking access leads to authorization_needed; re-authorizing resumes with no restart
- [ ] 3.13 pm2 logs contain no token, code or verifier
- [ ] 3.14 Observations recorded in verification.md
