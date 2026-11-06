# Edge Surface and Spotify App Registration -- Plan Brief

> Full plan: `context/changes/edge-surface-and-spotify-app-registration/plan.md`
> Research: `context/changes/spotify-redirect-uri-policy/research.md`

## What & Why

Roadmap item F-03: make the project's subdomain reach the Pi through the Cloudflare Tunnel connector that already serves n8n, gate it with its own Access application, and register the exact HTTPS callback with Spotify. S-01 cannot authorize the account without a registered redirect URI that the browser can reach. This change fixes that string before any code depends on it, which retires the riskiest item in the infrastructure risk register: a `redirect_uri` built as `http://<lan-ip>` and rejected by Spotify.

## Starting Point

The domain, the tunnel connector and a working Access login method already exist for n8n. The research settled the wiring (Option A: reuse the n8n tunnel). It assumed the connector runs on another LAN host, but the owner confirmed on 2026-11-05 that n8n and its `cloudflared` run on this same Pi. So the route targets `localhost`, and the PRD's DHCP-reservation step no longer applies. F-01 is merged into `main`. Its app has `/healthz`, has already driven the real board from the Pi, and binds `127.0.0.1` by default.

## Desired End State

`https://<sub>.<owner-domain>/healthz` answers from the Pi for the logged-in owner and redirects everyone else to the Access login. The app listens on loopback only, so the LAN cannot bypass the gate. A dedicated Spotify app has exactly one redirect URI, and `/etc/spotify-to-led/env` on the Pi holds the matching values for F-02 and S-01. The outcome is recorded in the repo using placeholders only.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Wiring | Option A: existing connector (on the Pi) routes to `localhost:8000` | Reuses proven infra; no LAN address involved, so no reservation is needed | Research, corrected by owner |
| Gate scope | Whole hostname behind Access | One page at one address for FR-015 | Research |
| Real hostname in repo | Placeholders only | The repo is public, and the PRD already uses placeholders | Plan |
| Spotify app | New dedicated Development Mode app | Revoking or rotating it never affects anything else | Plan |
| Access policy | Allow owner email, n8n's login method, 24h session | No new identity setup, and the session rarely lapses mid-consent | Plan |
| Verification depth | `/healthz` via hostname, plus an anonymous-request refusal | Proves both reachability and that the gate actually enforces | Plan |
| Order | Access app created before the route | The hostname is never public without its only gate | Plan |
| Bind address | Keep F-01's default `127.0.0.1` | The connector is on the same Pi; `0.0.0.0` would expose the app ungated to the LAN | Plan (revised) |
| Client secret | Not stored on the Pi | The PKCE flow never sends it | Plan |
| Env file | `/etc/spotify-to-led/env`, `0600`, service user | Outside git, one home for F-02 and S-01 | Plan (per infrastructure.md) |

## Scope

**In scope:**
- Checking the connector's networking (native or host network) and the app's loopback-only bind
- Access application and public hostname route
- Spotify app, redirect URI, user listing, Premium check
- The Pi env file
- `verification.md`, plus corrections to `infrastructure.md` and the PRD (connector location) and resolving roadmap Open Question 7

**Out of scope:**
- Any application code (`/callback` and PKCE are S-01, the status page is S-07)
- The systemd unit (F-02)
- Option B, a second connector, and a DHCP reservation
- A real OAuth consent
- Monitoring of the Access policy

## Architecture / Approach

Browser -> Cloudflare edge (Access check) -> existing tunnel -> connector on the Pi -> `http://localhost:8000` -> F-01 app on the same Pi. Spotify only ever sees the string `https://<sub>.<owner-domain>/callback`, which the browser dereferences. The work is done inside out: loopback reachability, then gate, then route. The Spotify registration runs in parallel.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Connector location and local reachability | Connector reaches `localhost:8000`; app not on the LAN | `cloudflared` in a Docker bridge network (stop and revise) |
| 2. Cloudflare edge, gate first | Access app, then an ingress rule in the locally-managed tunnel's `config.yml` and a `tunnel route dns` record; allowed and refused checks | DNS record created before the gate leaves an ungated window; restarting the connector briefly drops n8n |
| 3. Spotify app registration | Dedicated app, exact URI, Pi env file | Owner's Premium lapses; whitespace or trailing slash in the URI |
| 4. Record the outcome | `verification.md` and foundation updates | Real hostname or IP leaking into the public repo |

**Prerequisites:** owner access to the Pi, the Cloudflare Zero Trust dashboard and the Spotify Developer Dashboard; an active Spotify Premium subscription; the Pi's checkout updated to `main` (which now carries F-01) for the verification run.
**Estimated effort:** about one evening across 4 phases, almost all of it dashboard work by the owner.

## Open Risks & Assumptions

- Re-authorization depends on n8n's connector staying healthy on the Pi. Both now share one machine, so the reachability concern is gone, but the dependency on n8n's connector config remains. The board display does not depend on it.
- A misconfigured or removed Access policy would silently expose the page. The PRD accepts this, and the Phase 2 negative check proves the policy is in force today.
- Spotify Development Mode depends on the owner keeping Premium. This is recorded as a risk-register row in Phase 4.
- The `/callback` path is only proven up to the edge. The real exchange is verified in S-01.

## Success Criteria (Summary)

- The owner opens `https://<sub>.<owner-domain>/healthz` on any logged-in device and sees the Pi answer. Anyone else hits the Access login.
- Spotify holds exactly the redirect URI that the Pi's env file holds, so S-01 can pin it from config.
- Nothing identifying the domain, the LAN or the Spotify app is committed to the public repo.
