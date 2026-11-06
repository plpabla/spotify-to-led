# Edge Surface and Spotify App Registration Implementation Plan

## Overview

Carry out roadmap item F-03: publish the project's subdomain through the Cloudflare Tunnel connector that already serves n8n, gate it with its own Cloudflare Access application, and register the exact HTTPS callback URI with a dedicated Spotify app. This writes no application code. Every outward-facing step is done by the owner, because `infrastructure.md` reserves edge and account changes for a human. The change is proven against F-01's `GET /healthz`, and the outcome is recorded without putting the real hostname into the public repository.

> Revised 2026-11-05, before any step ran: the owner confirmed that n8n and its `cloudflared` connector run **on the same Pi** that drives the board, not on another LAN host as the research and `infrastructure.md` assumed. The route therefore targets `http://localhost:8000`. That removes the DHCP reservation and the `HOST=0.0.0.0` bind from the plan, and the app keeps its default `127.0.0.1`. Phase 1 and its Progress rows were rewritten, and Phases 2 to 4 were adjusted to match.

## Current State Analysis

- **Wiring is decided, with one correction.** `context/changes/spotify-redirect-uri-policy/research.md` chose Option A: reuse the existing n8n tunnel and add a public hostname to it, rather than a second tunnel. It also chose to gate the whole hostname, scope Access to the owner's email, and use Authorization Code with PKCE. The PRD's Access Control and Existing Infrastructure sections (`context/foundation/prd.md:244-273`) reflect that. The research assumed the connector runs on a different LAN host, so it routed to `http://<pi-lan-ip>:PORT` and needed a reserved lease (`prd.md:270`, roadmap Open Question 7). The owner confirmed on 2026-11-05 that the connector runs on the Pi itself (hostname `pi-n8n`), so the route is `http://localhost:8000` and the reservation is unnecessary.
- **Inherited and not rebuilt here:** the domain on Cloudflare DNS, a tunnel connector on the Pi that already survives reboots, and an Access login method that already fronts n8n (`prd.md:260-262`).
- **F-01 is merged into `main`** (PR #1, `89a51a8`), and this branch is based on it. `src/spotify_to_led/main.py` is a FastAPI app with `GET /healthz` returning `{"status": "ok"}`. `src/spotify_to_led/config.py` defines a `Settings` model with `host: str = "127.0.0.1"` and `port: int = 8000`. Keys are unprefixed and case-insensitive, and unknown keys are ignored (`extra="ignore"`). F-01's app has already run on the Pi and driven the real board (`infrastructure.md` risk register, closed 2026-11-04). The owner confirmed it answers on `0.0.0.0:8000` from a LAN browser, so the app itself works on the Pi.
- **Spotify Development Mode changed in February 2026** (migration guide, fetched 2026-11-05). The app owner needs an active Premium subscription, or the app stops working. New apps allow 5 users. The per-developer client ID limit was raised to 25 in July 2026. Get Currently Playing Track and Get Playback State are still listed, and no image fields were removed.

## Desired End State

- `https://<sub>.<owner-domain>/healthz` returns `{"status": "ok"}` from the Pi to a browser logged in through Access. The same URL from an unauthenticated client is redirected to the Cloudflare Access login and never reaches the Pi.
- The app listens on `127.0.0.1:8000` only. The connector on the same Pi is its only route in, so nothing on the LAN can reach the app without passing Access.
- A dedicated Spotify Development Mode app exists, owned by a Premium account, with exactly `https://<sub>.<owner-domain>/callback` as its redirect URI and the owner's account as an authorized user.
- `/etc/spotify-to-led/env` on the Pi holds `SPOTIFY_CLIENT_ID` and `SPOTIFY_REDIRECT_URI`. It is owned by the service user with mode `0600`, outside the git tree, and is ready for F-02's `EnvironmentFile=` and S-01's settings.
- The outcome is recorded in the repo using placeholders only.

### Key Discoveries:

- With the connector on the Pi, `localhost` in the route means the Pi only if `cloudflared` runs natively or in a container with host networking. In a Docker bridge network, `localhost` is the container itself and the route returns a 502. Phase 1 checks this before the edge is touched.
- F-01's default bind `127.0.0.1` (`src/spotify_to_led/config.py:39`) is exactly what this wiring wants, so no `HOST` override is needed. Binding `0.0.0.0` would expose the ungated app to the whole LAN, which is a path around Access.
- Access applications are per-hostname, so n8n's policy does not cover the new subdomain (`prd.md:269`, research "Wiring" section).
- Do not add a second `cloudflared` replica of the n8n tunnel anywhere. Replicas share one config and are load-balanced (research "The trap in between"). The existing single connector is used as-is.
- Spotify matches the redirect URI exactly. One reported "not secure" rejection was caused by a trailing space in the pasted URI (research, contradiction table).
- An Access session that expires mid-consent can strand the authorization code. A day-long session makes this rare, and re-running the flow fixes it (`infrastructure.md` Unknown Unknowns).

## What We're NOT Doing

- No application code. The `/callback` route, PKCE and the token exchange belong to S-01. The status page belongs to S-07.
- No DHCP reservation or static IP. The route targets `localhost`, so the Pi's LAN address is irrelevant to it.
- No `HOST=0.0.0.0`. The app stays on `127.0.0.1`.
- No new tunnel, no second connector and no change to the existing connector's install (Option B is not needed).
- No systemd unit and no deploy script. That is F-02. The F-01 app is started by hand for verification only.
- No Spotify client secret on the Pi. The PKCE flow never sends it, so it stays in the Spotify dashboard.
- No real hostname, client ID or LAN IP committed to the public repo.
- No real OAuth consent. Completing a consent needs S-01's callback route.
- No monitoring of the Access policy. The PRD accepts that a removed policy would go unnoticed by the service.

## Implementation Approach

Work from the inside out so each layer is proven before the next one depends on it. Phase 1 proves that the connector can reach the app over the Pi's own loopback, and that the app is not reachable from the LAN. Phase 2 creates the Access gate before the public route exists, so the hostname is never public without a gate. Then it proves both directions: allowed when logged in, refused when anonymous. Phase 3 is independent of the edge and can run in parallel with Phases 1 and 2. Phase 4 writes down what was done, using placeholders, and corrects the foundation documents' "different LAN host" assumption.

The implementer should use the `cloudflare` skill for current Zero Trust dashboard naming, which changes often, instead of relying on remembered menu paths.

## Critical Implementation Details

**Ordering: gate before route.** Create and save the Access application for `<sub>.<owner-domain>` before adding the public hostname route. Once the route is added, the DNS record makes the hostname live. The Access application is the page's only gate, so the reverse order leaves an ungated window. If a route already exists by mistake, delete it until Access is in place.

## Phase 1: Connector location and local reachability

### Overview

Prove that the n8n connector on the Pi can reach F-01's app over loopback, and that the app is not exposed on the LAN.

### Changes Required:

#### 1. Connector networking check

**File**: the Pi's `cloudflared` install (inspect only, no change)

**Intent**: Confirm that `localhost` seen by the connector is the Pi's own loopback, which is what the Phase 2 route depends on.

**Contract**: `cloudflared` runs either as a native service, or in a container with `NetworkMode` `host`. **Stop condition:** if it runs in a Docker bridge network, stop and report it. The route target would then have to change (host gateway address plus a non-loopback bind), and that needs a plan revision rather than an improvised fix.

#### 2. F-01 app on loopback

**File**: the Pi's repo checkout, updated to `main` (`git pull`, `uv sync --frozen`); the run is temporary, for verification only

**Intent**: Run F-01's app with its default bind, so that only processes on the Pi, the connector included, can reach it.

**Contract**: Start the app with no `HOST` override (default `127.0.0.1`) and `PORT` left at `8000`. Port 8000 must not already be in use on the Pi; n8n uses its own port.

### Success Criteria:

#### Automated Verification:

- On the Pi, `curl -s http://localhost:8000/healthz` returns `{"status":"ok"}`
- On the Pi, `ss -ltn | grep ':8000'` shows exactly one listener, on `127.0.0.1:8000`
- On the Pi, `cloudflared` runs natively (`systemctl is-active cloudflared` prints `active`) or with host networking (`docker inspect -f '{{.HostConfig.NetworkMode}}' <container>` prints `host`)

#### Manual Verification:

- From a browser on another LAN machine, `http://pi-n8n.local:8000/healthz` does not load (connection refused), confirming that the app is not exposed on the LAN

**Implementation Note**: After this phase, pause for owner confirmation before touching the edge.

---

## Phase 2: Cloudflare edge, gate first

### Overview

Create the Access application for the new subdomain, then add an ingress rule to the existing locally-managed tunnel and publish its DNS record, then prove that the gate allows the owner and refuses everyone else.

> Revised 2026-11-06, during Phase 1: the n8n tunnel is **locally managed**. pm2 runs `cloudflared tunnel run n8n`, which reads `~/.cloudflared/config.yml` (user `pi-n8n`). Its routes are `ingress:` rules in that file, not dashboard public hostnames, and DNS is created with `cloudflared tunnel route dns`, using the `cert.pem` beside it. Change 2 below replaces the dashboard route step.

### Changes Required:

#### 1. Cloudflare Access application

**File**: Cloudflare Zero Trust dashboard (outside the repo)

**Intent**: Give the new hostname its own edge gate, mirroring the n8n setup so no new identity configuration is introduced.

**Contract**: A self-hosted Access application whose single application domain is `<sub>.<owner-domain>`, covering the whole hostname with no path. It uses the login method already used for n8n and a 24-hour session duration. It has one Allow policy whose Include rule is the owner's email only, with no Bypass and no Everyone rule. Before saving, check that n8n's own Access application is not a wildcard (`*.<owner-domain>`). If it is, note it in `verification.md`, because the new application must still be the more specific match.

#### 2. Ingress rule and DNS route

**File**: `/home/pi-n8n/.cloudflared/config.yml` on the Pi (outside the repo), plus one DNS record in the owner's Cloudflare zone

**Intent**: Publish the subdomain on the tunnel that already runs on the Pi for n8n, pointing at the app on the same machine, without disturbing n8n's rule.

**Contract**:
- Back up the file first (`cp config.yml config.yml.bak`).
- Insert one rule **after** the n8n rule and **before** the final `- service: http_status:404` catch-all: `- hostname: <sub>.<owner-domain>` with `service: http://localhost:8000`. The n8n rule and the tunnel/credentials keys stay unchanged.
- Validate with `cloudflared tunnel ingress validate`, and confirm that `cloudflared tunnel ingress rule https://<sub>.<owner-domain>` selects the new rule.
- Apply with `pm2 restart cloudflared`. n8n's public URL drops for a few seconds.
- Create the DNS record last, because that is what makes the hostname live: `cloudflared tunnel route dns n8n <sub>.<owner-domain>`. This adds a proxied CNAME to the tunnel.
- Do not create a new tunnel or a second connector.
- Gate-before-route therefore means: the Access application exists before the DNS record does.

### Success Criteria:

#### Automated Verification:

- `curl -sI https://<sub>.<owner-domain>/healthz` (no cookie) returns a `302` whose `Location` points at `<team>.cloudflareaccess.com`
- `curl -sI https://<sub>.<owner-domain>/callback?code=x` (no cookie) returns the same Access redirect, which shows the gate covers every path and not only `/healthz`
- On the Pi, `cloudflared tunnel ingress validate` reports the config as valid
- On the Pi, `cloudflared tunnel ingress rule https://<sub>.<owner-domain>` selects the rule whose service is `http://localhost:8000`

#### Manual Verification:

- In a browser logged in through Access, `https://<sub>.<owner-domain>/healthz` shows `{"status":"ok"}`, and the Pi's console logs the request
- In a private window, the same URL shows the Access login page. After the owner logs in with the n8n login method, the page loads.
- A login attempt with an email other than the owner's is refused
- n8n still works as before (its hostname, its Access gate)

**Implementation Note**: Pause after this phase for owner confirmation of the manual checks.

---

## Phase 3: Spotify app registration

### Overview

Create a dedicated Spotify Development Mode app with the exact callback URI, and stash the values S-01 needs on the Pi. This phase is independent of Phases 1 and 2 and can run in parallel with them.

### Changes Required:

#### 1. Spotify developer app

**File**: Spotify Developer Dashboard (outside the repo)

**Intent**: Register the redirect URI in an app used only by this project, so that revoking it or rotating its secret never affects anything else.

**Contract**:
- The account creating the app has an active Spotify Premium subscription. Development Mode apps stop working when the owner's Premium lapses.
- A new app named for this project, with the Web API selected.
- Redirect URIs contain exactly one entry, `https://<sub>.<owner-domain>/callback`. It is typed or pasted with no trailing space, no trailing slash and no port, and it is re-read after saving.
- The owner's Spotify account is listed under User Management. Add it if it is not there.

#### 2. Pi environment file

**File**: `/etc/spotify-to-led/env` on the Pi (outside the git tree)

**Intent**: Give F-02's `EnvironmentFile=` and S-01's settings one fixed home for the real values. They stay off GitHub, and no `git pull` can clobber them (`infrastructure.md` Operational Story, Secrets).

**Contract**: The directory `/etc/spotify-to-led/` holds the file `env`, owned by the service user (the repo owner) with mode `0600`. Keys, unprefixed to match F-01's `Settings` convention:
- `SPOTIFY_CLIENT_ID=<client id from the dashboard>`
- `SPOTIFY_REDIRECT_URI=https://<sub>.<owner-domain>/callback`

The value must be identical to the registered URI. S-01 pins it from this key and never derives it from the request. No `HOST` key, because the default `127.0.0.1` is correct. No client secret goes in this file. Nothing reads the file until F-02 and S-01 wire it in, and F-01's `extra="ignore"` keeps unknown keys harmless.

### Success Criteria:

#### Automated Verification:

- On the Pi, `sudo stat -c '%a %U' /etc/spotify-to-led/env` prints `600 <service-user>`
- On the Pi, `sudo grep -cE '^(SPOTIFY_CLIENT_SECRET|HOST)=' /etc/spotify-to-led/env` prints `0`

#### Manual Verification:

- The Spotify dashboard's redirect URI, compared character by character, is identical to `SPOTIFY_REDIRECT_URI` in the Pi's env file
- The app's settings show the owner's account in User Management, and the account page shows Premium as active

**Implementation Note**: Pause after this phase for owner confirmation.

---

## Phase 4: Record the outcome

### Overview

Write down what was configured and what was proven, with placeholders for every real value, and correct the foundation documents where this change disproved an assumption.

### Changes Required:

#### 1. Verification record

**File**: `context/changes/edge-surface-and-spotify-app-registration/verification.md`

**Intent**: Give S-01, S-07 and the archive an auditable record of the edge state, in the style of `context/changes/bootstrap-verification/verification.md`.

**Contract**: Dated sections for each phase:
- The connector's location and how it runs (natively, under pm2, as `tunnel run n8n`, locally managed).
- The app's loopback-only bind.
- The Access application's settings: domain, login method, 24h session, Allow email-only, and whether n8n's application is a wildcard.
- The route: an ingress rule in the existing tunnel's `config.yml` to `http://localhost:8000`, and a DNS record created with `tunnel route dns`.
- The Spotify app: dedicated, Development Mode, Premium confirmed, one redirect URI, owner listed.
- The env file path and keys, with no values.
- The output of each check, with the hostname replaced by `<sub>.<owner-domain>`.

#### 2. Infrastructure facts

**File**: `context/foundation/infrastructure.md`

**Intent**: Correct the connector-location assumption and record the facts this change surfaced, so later changes don't have to rediscover them.

**Contract**:
- **Unknown Unknowns, "The tunnel connector is on a different LAN host than the Pi":** append a `> Superseded, <date>:` note, in the style F-01 used. The connector runs on the Pi itself and the route targets `localhost:8000`. uvicorn trusts forwarded headers from `127.0.0.1` by default, so the forwarded-header trap no longer applies, but the redirect URI is still pinned from configuration. The app must keep `127.0.0.1`, because `0.0.0.0` would expose it ungated to the LAN.
- **Risk Register, "OAuth `redirect_uri` built as `http://<lan-ip>`":** append an `Updated <date>:` note that likelihood drops, since the proxy is on loopback. Pinning from config stays the mitigation.
- **Risk Register:** add a row. Lapsed Spotify Premium stops a Development Mode app; Likelihood L, Impact H. Mitigation: it presents as an authorization failure, so it falls under the staleness marker (FR-018), and the S-07 page names the cause.
- **Operational Story, Secrets:** name the env-file path `/etc/spotify-to-led/env`, and state that no client secret is stored because the flow is PKCE.

#### 3. PRD Existing Infrastructure

**File**: `context/foundation/prd.md`

**Intent**: Mark the DHCP-reservation delta as not needed, so the PRD stops listing work that does not apply.

**Contract**: Under `## Existing Infrastructure`, append to the "Reserve a DHCP lease or assign a static IP" bullet a `Not needed, <date>:` note. The connector runs on the Pi, so the route targets `localhost`. Touch nothing else in that section.

#### 4. Roadmap Open Question 7

**File**: `context/foundation/roadmap.md`

**Intent**: Close the open question this change answered.

**Contract**: In `## Open Roadmap Questions`, item 7, prefix `RESOLVED <date> --` and state that no reservation is needed because the connector runs on the Pi and routes to `localhost`. Mark the matching F-03 "Unknowns" bullet the same way. Touch nothing else. The F-03 status itself is changed by the 10x skills, not by hand.

### Success Criteria:

#### Automated Verification:

- `git diff main --name-only` lists only files under `context/`
- `git grep -nE '[a-z0-9-]+\.cloudflareaccess\.com|192\.168\.[0-9]|(^|[^0-9])10\.[0-9]+\.[0-9]+\.[0-9]|172\.(1[6-9]|2[0-9]|3[01])\.' -- context/changes/edge-surface-and-spotify-app-registration` prints nothing (no real Access team domain or private LAN IP; `<team>` placeholders do not match)

#### Manual Verification:

- The owner skims `verification.md` and confirms the real subdomain, domain, client ID and LAN IP appear nowhere in it, and that it matches what was configured

---

## Testing Strategy

### Unit Tests:

- None. This change adds no code.

### Integration Tests:

- None automated in CI. The `curl` checks in Phases 1 to 3 are the integration surface: loopback reachability, the edge gate in both directions, and env-file shape.

### Manual Testing Steps:

1. With F-01's app running on `127.0.0.1:8000` on the Pi, confirm `http://pi-n8n.local:8000/healthz` does not load from another LAN machine.
2. Open `https://<sub>.<owner-domain>/healthz` in a logged-in browser and see `{"status":"ok"}`.
3. Open the same URL in a private window and see the Access login.
4. Try a non-owner email at the Access login and see it refused.
5. Confirm n8n is unaffected.
6. Compare the Spotify redirect URI with the env file's value character by character.

## Performance Considerations

None. The route only carries the occasional status-page and OAuth traffic, and the board path stays outbound-only.

## Migration Notes

None. Rollback: first delete the DNS record for `<sub>.<owner-domain>` in the Cloudflare dashboard, which removes exposure (`cloudflared` has no DNS-delete command). Then restore `config.yml.bak` and `pm2 restart cloudflared`, then delete the Access application, then the Spotify app.

## References

- Research: `context/changes/spotify-redirect-uri-policy/research.md`
- PRD: `context/foundation/prd.md:244-273` (Access Control, Existing Infrastructure)
- Infrastructure: `context/foundation/infrastructure.md` (Operational Story: Approval, Secrets; Unknown Unknowns; Risk Register)
- Roadmap item: `context/foundation/roadmap.md`, F-03
- F-01 settings and route: `src/spotify_to_led/config.py:39-40`, `src/spotify_to_led/main.py:77`; archived change `context/archive/2026-11-03-app-skeleton-and-board-transport/`
- Spotify Development Mode changes: <https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide>, <https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security>

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` -- <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Connector location and local reachability

#### Automated

- [x] 1.1 On the Pi, `curl -s http://localhost:8000/healthz` returns `{"status":"ok"}` -- 54a13dd
- [x] 1.2 On the Pi, `ss -ltn | grep ':8000'` shows exactly one listener, on `127.0.0.1:8000` -- 54a13dd
- [x] 1.3 On the Pi, `cloudflared` runs natively or with host networking -- 54a13dd

#### Manual

- [x] 1.4 From a browser on another LAN machine, `http://pi-n8n.local:8000/healthz` does not load -- 54a13dd

### Phase 2: Cloudflare edge, gate first

#### Automated

- [x] 2.1 `curl -sI https://<sub>.<owner-domain>/healthz` (no cookie) returns a `302` whose `Location` points at `<team>.cloudflareaccess.com` -- a3628c4
- [x] 2.2 `curl -sI https://<sub>.<owner-domain>/callback?code=x` (no cookie) returns the same Access redirect -- a3628c4
- [x] 2.7 On the Pi, `cloudflared tunnel ingress validate` reports the config as valid -- a3628c4
- [x] 2.8 On the Pi, `cloudflared tunnel ingress rule https://<sub>.<owner-domain>` selects the `http://localhost:8000` rule -- a3628c4

#### Manual

- [x] 2.3 In a browser logged in through Access, `https://<sub>.<owner-domain>/healthz` shows `{"status":"ok"}`, and the Pi's console logs the request -- a3628c4
- [ ] 2.4 In a private window, the same URL shows the Access login page, and it loads after the owner logs in
- [ ] 2.5 A login attempt with an email other than the owner's is refused
- [x] 2.6 n8n still works as before (its hostname, its Access gate) -- a3628c4

### Phase 3: Spotify app registration

#### Automated

- [x] 3.1 On the Pi, `sudo stat -c '%a %U' /etc/spotify-to-led/env` prints `600 <service-user>` -- ad5c518
- [x] 3.2 On the Pi, `sudo grep -cE '^(SPOTIFY_CLIENT_SECRET|HOST)=' /etc/spotify-to-led/env` prints `0` -- ad5c518

#### Manual

- [x] 3.3 The Spotify dashboard's redirect URI, compared character by character, is identical to `SPOTIFY_REDIRECT_URI` in the Pi's env file -- ad5c518
- [x] 3.4 The app's settings show the owner's account in User Management, and the account page shows Premium as active -- ad5c518

### Phase 4: Record the outcome

#### Automated

- [x] 4.1 `git diff main --name-only` lists only files under `context/`
- [x] 4.2 `git grep` over the change folder prints nothing (no real Access team domain or private LAN IP)

#### Manual

- [x] 4.3 The owner skims `verification.md` and confirms no real subdomain, domain, client ID or LAN IP appears in it, and that it matches what was configured
