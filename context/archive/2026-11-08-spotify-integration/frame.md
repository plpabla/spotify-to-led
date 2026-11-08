# Frame Brief: Spotify authorization and playback read (S-01)

> Framing step before /10x-plan. This document captures what is *actually*
> at issue, separated from what was initially assumed.

## Reported Observation

S-01 (roadmap `spotify-authorization-and-playback-read`) must be implemented
against Spotify's Web API: authorize the owner's account once via PKCE, then
determine what is playing unattended. No Spotify code exists yet; only
`SPOTIFY_CLIENT_ID` and `SPOTIFY_REDIRECT_URI` are provisioned (`.env.example`,
`/etc/spotify-to-led/env`).

## Initial Framing (preserved)

- **User's stated cause or approach**: we may not know enough about how the
  Spotify API and surrounding web docs actually behave today to implement this
  correctly; the roadmap/research assumptions (PKCE with no secret, pinned
  redirect URI, currently-playing endpoint, within rate limits) need checking.
- **User's proposed direction**: check the API and other web documentation
  before planning.
- **Pre-dispatch narrowing**: concern is "not separated yet", leaning towards
  auth/token lifecycle and reading the playing item. Scope: S-01 ends at "the
  service knows what is playing" with no board output (idle display is S-02,
  artwork S-04, status page S-07). Library: use one if it exists, but first
  establish whether raw API calls are simple enough to not need one.

## Dimension Map

1. **Auth handshake (PKCE)** -- client-ID-only PKCE, pinned redirect, read-only
   scopes.  <- initial framing
2. **Token lifecycle** -- access-token lifetime, refresh-token rotation and
   expiry, what revocation looks like; whether "authorize once" survives
   unattended.  <- initial framing
3. **Reading the playing item** -- one endpoint covering nothing-playing,
   episodes, ads, private sessions; survived the Feb 2026 Development Mode
   changes.  <- initial framing
4. **Rate limits vs. the 5 s budget (NFR-01)** -- a poll cadence that meets
   NFR-01 and stays inside published limits.
5. **Client layer** -- whether a Python library fits async + app-owned
   callback + 3.14, or raw HTTP is small enough.

## Hypothesis Investigation

| Hypothesis | Evidence | Verdict |
| --- | --- | --- |
| 1. PKCE handshake works as assumed | `tutorials/code-pkce-flow`: `GET accounts.spotify.com/authorize` with `client_id, response_type=code, redirect_uri, code_challenge_method=S256, code_challenge` (+ `state`, `scope`); verifier 43-128 chars; `POST /api/token` form-encoded with `code_verifier`, no Authorization header. `concepts/redirect_uri`: exact match, HTTPS. | HOLDS |
| 2a. Refresh works unattended | `tutorials/refreshing-tokens`: `grant_type=refresh_token, refresh_token, client_id`; access token `expires_in: 3600`; "a refresh token might not be included... continue using the existing token". Rotation that invalidates the old token: community + tekore docs only, not stated officially. | HOLDS WITH CAVEAT |
| 2b. "Authorize once" holds indefinitely | `tutorials/refreshing-tokens`: "Refresh tokens issued to apps registered in the Developer Dashboard have a lifetime of 6 months... Refreshing an access token does not extend the refresh token's lifetime." `blog/2026-06-18-refresh-token-expiration`: covers PKCE; existing apps from 2026-07-20; expiry is `400 invalid_grant`; "do not retry", discard and re-authorize; no advance warning; store your own authorization timestamp. Verified first-hand 2026-11-08. Absent from every doc under `context/`. | BROKEN as "once ever"; fits FR-003's "while the credential remains valid" |
| 2c. Token can live in `/etc/spotify-to-led/env`, rewritten in place (`infrastructure.md:110`) | Pi, 2026-11-08: `/etc/spotify-to-led` is `755 root:root`, `env` is `600 pi-n8n:pi-n8n`, dir NOT writable by `pi-n8n`. The service can only truncate-and-rewrite the one file that also holds the hand-edited `SPOTIFY_CLIENT_ID`, `BOARD_TRANSPORT`, `IDOT_MAC`; no temp-file-and-rename is possible there. The token is mutable runtime state written on every rotation, not configuration. | BROKEN (the doc's assumption, not the API) |
| 3. One endpoint reports what is playing | `reference/get-the-users-currently-playing-track` (scope `user-read-currently-playing`, `additional_types=track,episode`); `currently_playing_type` is `track/episode/ad/unknown`; `item` and `progress_ms` may be `null`; 204 = no body; artwork `item.album.images` / `item.images`, "widest first", no sizes guaranteed. Paused/ad/private-session bodies undocumented. Feb 2026 guide removes no player fields or endpoints. No push API exists. | HOLDS WITH CAVEAT |
| 4. Polling fits the rate limit | `concepts/rate-limits`: rolling 30 s window, number unpublished, lower in Development Mode, 429 + `Retry-After`. Community: 1 s polling throttled; no endorsed interval. 3-4 s polling is ~8-10 calls/30 s and meets NFR-01. ETag support on player endpoints unverified. | HOLDS WITH CAVEAT |
| 5. A library is needed | spotipy 2.26.0: sync (`requests`), pulls `redis`. tekore 6.2.0: PKCE helpers but auto-refresh is sync-only and pins plain `httpx<0.29` (stalled at 0.28.1). spotifyaio: no OAuth help. async-spotify: abandoned 2022. `httpx2` 2.16.0 is already in `uv.lock` (dev group only), with `AsyncClient`. Raw surface: build authorize URL, 2 token POSTs, 1 GET; est. 100-150 lines. | NONE -- raw HTTP is the evidence-backed fit |

## Narrowing Signals

- Owner reads the 6-month cap as compatible with the PRD ("still fits"):
  FR-001/FR-003 need no rewording, but re-authorization is a routine,
  scheduled event, not only a breakage path.
- Pi directory ownership check (above) rules out writing token state next to
  the env file atomically.
- Owner's library position ("use one if it helps, but check whether raw calls
  suffice") is answered by dimension 5: none of the candidates removes the
  work S-01 must write anyway (async refresh, 204/429/`invalid_grant`
  handling, routes, persistence).

## Cross-System Convention

The convention for long-running OAuth clients is to treat the token set as
mutable state persisted atomically by the process (Spotify's own guidance:
persist a returned refresh token, record the authorization time, discard on
`invalid_grant`). Configuration stays read-only to the process. The project's
current `infrastructure.md:110` merges the two, which predates both the
6-month cap and any check of the directory permissions.

## Reframed (or Confirmed) Problem Statement

> **The actual problem to plan around is**: not *how Spotify's API works*
> (the docs are sufficient and the assumptions largely hold), but *how the
> service owns a short-lived, mutable credential*: where the rotating token
> and its authorization timestamp persist on the Pi, and how the service
> behaves when the credential ends, which Spotify now guarantees happens at
> least every 6 months.

The initial framing was right that the docs needed checking, and the check
paid off: it surfaced the 6-month cap, which no project document knows about.
The API mechanics themselves (PKCE, endpoint, scopes) hold and need no
redesign. What the plan can no longer take for granted is the token store
that `infrastructure.md` already assumed, and an `invalid_grant` path that
is a normal event rather than an edge case.

## Confidence

**HIGH** -- the 6-month cap is verified first-hand on two official pages; the
directory ownership is verified on the Pi; the library verdict rests on
current PyPI metadata and `uv.lock`. The only soft points (rotation
invalidating the old token, undocumented ad/private-session bodies, the
unpublished rate number) do not change the problem statement; the plan
should treat them as behaviours to observe against the live account.

## What Changes for /10x-plan

The plan should decide where token state lives (the env file in a root-owned
directory cannot be atomically replaced, and holds config that must not be
corrupted), record the authorization time, and treat `invalid_grant` as a
routine end-of-credential event. Use raw HTTP via `httpx2.AsyncClient`
(promoted from dev to runtime) rather than a library. Poll
`currently-playing` with `additional_types=track,episode` at a cadence
derived from NFR-01, honour `Retry-After`. `infrastructure.md:110` will need
an edit once the token location is decided.

## References

- Source files: `src/spotify_to_led/config.py`, `src/spotify_to_led/main.py`,
  `.env.example`, `pyproject.toml`, `uv.lock` (httpx2 2.16.0, dev group)
- Foundation: `context/foundation/infrastructure.md:104,110,121,127`,
  `context/foundation/prd.md:50-51,140-159`, `context/foundation/roadmap.md` (S-01)
- Prior research: `context/archive/spotify-redirect-uri-policy/research.md`
  (PKCE decision), `context/archive/2026-11-05-edge-surface-and-spotify-app-registration/plan.md:14`
  (Feb 2026 Development Mode notes)
- Spotify docs (fetched 2026-11-08):
  - https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow
  - https://developer.spotify.com/documentation/web-api/tutorials/refreshing-tokens
  - https://developer.spotify.com/blog/2026-06-18-refresh-token-expiration
  - https://developer.spotify.com/documentation/web-api/reference/get-the-users-currently-playing-track
  - https://developer.spotify.com/documentation/web-api/reference/get-information-about-the-users-current-playback
  - https://developer.spotify.com/documentation/web-api/concepts/rate-limits
  - https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide
- Libraries: pypi.org JSON for spotipy, tekore, spotifyaio, async-spotify, httpx, httpx2
- Investigations: three parallel sub-agents (PKCE/token lifecycle; playback
  endpoints and limits; library vs raw HTTP), plus first-hand verification of
  the 6-month cap and the Pi directory check.
