# Spotify authorization and playback read (S-01) -- Plan Brief

> Full plan: `context/changes/spotify-integration/plan.md`
> Frame brief: `context/changes/spotify-integration/frame.md`

## What & Why

The service must authorize the owner's Spotify account once over PKCE and then keep knowing what is playing, unattended. From the frame: the actual problem is not how Spotify's API works (the docs are sufficient and the assumptions largely hold), but how the service owns a short-lived, mutable credential: where the rotating token and its authorization timestamp persist on the Pi, and how the service behaves when the credential ends, which Spotify now guarantees happens at least every 6 months.

## Starting Point

A FastAPI service with one supervised board task and `/healthz`; no Spotify code. `SPOTIFY_CLIENT_ID` and `SPOTIFY_REDIRECT_URI` are provisioned but unread. `infrastructure.md` assumes the token lives in `/etc/spotify-to-led/env`, which the daemon user cannot atomically replace.

## Desired End State

The owner opens `/authorize` once on the Pi's hostname and consents to one read-only scope. From then on `GET /playback` reports the playing, paused or absent item within about 3 s of a change, across restarts. When the credential ends, the service discards it, reports `authorization_needed`, and resumes as soon as the owner authorizes again, with no restart. Tokens never appear in logs or responses.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Client layer | Raw HTTP via `httpx2.AsyncClient`, promoted to runtime | No library removes the async refresh, 204/429/`invalid_grant` handling or routes S-01 must write anyway | Frame |
| `invalid_grant` | Routine end-of-credential: discard, report, wait for re-auth | Spotify caps refresh tokens at 6 months and says do not retry | Frame |
| Token store | `var/spotify-token.json` in the checkout, atomic write, mode 0600, path configurable | No sudo step, owned by `pi-n8n`, untouched by git rollback; env file stays read-only config | Plan |
| Paused item | Its own `paused` state, same item kept; board later shows the cover with a pause icon | Owner wants the cover to stay while paused, labelled as paused | Plan |
| Laptop parity | Mocked Spotify only; first live auth on the Pi | No extra redirect URI to register; FR-016 parity rests on `MockTransport` tests | Plan |
| Verification surface | Temporary `GET /playback` JSON route, replaced by S-07 | Checkable from a browser through the Access-gated hostname | Plan |
| Poll cadence | Fixed 3 s, `Retry-After` honoured on 429 | Meets NFR-01 (about 10 calls/30 s); an idle slowdown would break the start-playing latency | Plan |
| Scope | `user-read-currently-playing` only | Read-only hard rule; the one endpoint needs nothing else | Frame |

## Scope

**In scope:**
- Optional Spotify settings; PKCE helpers; code exchange and refresh
- Atomic token store with `authorized_at`
- Reader loop with failure causes (`authorization_needed`, `network`, `api_error`, `rate_limited`)
- `/authorize`, `/callback`, `/playback`
- Doc fixes in `infrastructure.md`, `tech-stack.md`, `.env.example`
- Live verification on the Pi

**Out of scope:**
- Any board output: idle display, artwork, staleness marker, pause icon
- Status page and re-auth button (S-07)
- A 6-month expiry warning
- A live laptop account
- Spotify libraries, extra scopes, ETags, adaptive cadence

## Architecture / Approach

The new `spotify_to_led.spotify` package has three parts:
- `auth` (PKCE and the token POSTs)
- `token_store` (one JSON file, temp file plus rename)
- `playback` and `reader` (parse responses into a frozen snapshot; a never-raising loop)

The loop waits for a token, refreshes it before expiry, polls every 3 s and classifies each outcome. The snapshot keeps the last known item while recording the failure cause and when it started, which is the shape S-06 (mark after 30 s) and S-07 (name the cause) need. `main.py` runs the reader as a second supervised task and adds the three routes; `/healthz` is untouched.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Spotify client core | Settings, PKCE, token calls, atomic token store, mocked tests | Secrets leaking into error messages |
| 2. Playback reader | Believed-state parsing and the unattended poll loop | Undocumented response bodies (ads, private session) |
| 3. Wiring, docs and live verification | Routes, lifespan task, runtime `httpx2`, doc fixes, Pi observation | `--no-dev` deploy dropping `httpx2`; live behaviour differing from mocks |

**Prerequisites:** F-01, F-02, F-03 done; owner available for the browser consent and the access-revocation test.
**Estimated effort:** about 2-3 sessions across 3 phases, plus an hour or more of unattended observation on the Pi.

## Open Risks & Assumptions

- Spotify's bodies for paused-after-stop, private sessions and ads are undocumented; the plan observes and records them rather than assuming.
- Whether a refresh invalidates the old refresh token is unconfirmed officially; persisting before use makes either answer safe.
- The rate limit number is unpublished; 3 s polling rests on community evidence.
- The 3 s cadence leaves about 1 s of NFR-01's budget for S-04's render and push.
- Handoff: the pause-icon presentation is a fifth case for FR-009's display rule, to be carried into the PRD by S-02/S-04.

## Success Criteria (Summary)

- After one browser consent, `/playback` on the Pi tracks play, pause, episode and stop within about 3 s, and survives a pm2 restart.
- Revoking access leads to `authorization_needed` and re-authorizing resumes with no restart.
- No token, code or verifier appears in logs, responses or the env file.
