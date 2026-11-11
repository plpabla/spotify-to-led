# Verification: status-and-reauthorization-page (S-07)

## Deploy (2026-11-11)

Branch `feat/status-and-reauthorization-page` at `5f83dde` was pushed. The Pi's checkout was
switched to it from `feat/staleness-marker-and-display-rule`, and
`PI_HOST=pi scripts/deploy.sh` ran. `/healthz` answered after the reload. A first `/status`
on the Pi reported `configured: true`, `nothing_playing`, authorization granted
2026-11-08T16:32:20Z with `expires_around` 2027-05-09, board link up, presentation idle. `/`
answered `200 text/html`.

## Phase 2 manual check (laptop)

| Check | Result |
| --- | --- |
| 2.3 page legible on desktop and phone width | Pass for the not-configured case, judged by the owner. The laptop has no `SPOTIFY_CLIENT_ID`, and authorizing there is not possible (the registered redirect URI is the Pi's, and copying the token off the Pi is forbidden), so the playing case was judged on the Pi in 3.3. |

## Phase 3 manual checks (Pi)

| Check | Result |
| --- | --- |
| 3.3 page through the Cloudflare edge from a phone off the home Wi-Fi, item and link up shown | Pass (owner) |
| 3.4 board switched off: link down, page keeps answering; back on: link up, redrawn | Pass (owner) |
| 3.5 Spotify outage over 30 s: cause named, duration, board marked stale; clears afterwards | Pass, see below |
| 3.6 re-authorize round trip from the page, landing notice, dates updated | Pass (owner). Granted 2026-11-11T13:25:40Z, `expires_around` 2027-05-12. The access log shows `GET /callback HTTP/1.1" 303` with no query string. |
| 3.7 no token or code on the page or in `pm2 logs` | Pass, see below |

### 3.5 outage

The outage was `sudo iptables -I OUTPUT -p tcp --dport 443 -j REJECT`, removed after 90 s by a
`nohup` timer, as in S-06. `/status` was sampled on the Pi (`curl 127.0.0.1:8000`), so this
check covers the route and the board, not how the page renders through the edge during an
outage:

| UTC | failure | failure_since | presentation | link |
| --- | --- | --- | --- | --- |
| 13:26:31 | outage starts | | | |
| 13:26:36 | none | - | idle, unmarked | up |
| 13:26:51 | network | 13:26:36 | idle, unmarked | up |
| 13:27:06 | network | 13:26:36 | idle, **stale** | up |
| 13:27:47 | network | 13:26:36 | idle, stale | up |
| 13:28:12 | none | - | idle, unmarked | up |

Afterwards, `sudo iptables -S OUTPUT` lists only `-P OUTPUT ACCEPT`.

### 3.7 secrets

The current access and refresh tokens were read from `var/spotify-token.json` on the Pi and
counted, never printed, in all 12 pm2 log files, rotated ones included. Every count was 0, and
`code=` occurs 0 times. Neither token appears in the `/status` body.
