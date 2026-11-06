---
change_id: edge-surface-and-spotify-app-registration
roadmap_item: F-03
date: 2026-11-06
---

## Conventions

The repository is public, so every real value is a placeholder: `<sub>.<owner-domain>` for the published hostname, `<team>` for the Access team domain, `<service-user>` for the Pi account that owns the repo checkout. No LAN address appears because none is used: the route targets `localhost`. Check results below are as observed by the owner and recorded in the plan's `## Progress`; literal terminal transcripts were not kept.

## Phase 1: Connector location and local reachability (2026-11-06)

- **Connector:** `cloudflared` runs natively on the Pi (hostname `pi-n8n`), the same machine that drives the board. It is supervised by pm2 as `cloudflared tunnel run n8n`. The tunnel is locally managed: its routes are `ingress:` rules in `/home/pi-n8n/.cloudflared/config.yml`, not dashboard public hostnames. Not a Docker container, so `localhost` in a route is the Pi's own loopback.
- **App bind:** F-01's app, from `main`, started by hand with no `HOST` override. It listens on `127.0.0.1:8000` only.

| Check | Result |
| --- | --- |
| `curl -s http://localhost:8000/healthz` on the Pi | `{"status":"ok"}` |
| `ss -ltn \| grep ':8000'` on the Pi | one listener, `127.0.0.1:8000` |
| Connector runs natively or with host networking | native process under pm2 (the plan's `systemctl is-active cloudflared` probe does not apply; pm2, not systemd, supervises it) |
| `http://pi-n8n.local:8000/healthz` from another LAN machine | does not load (refused) |

## Phase 2: Cloudflare edge, gate first (2026-11-06)

- **Access application:** self-hosted, single application domain `<sub>.<owner-domain>`, whole hostname, no path. Login method Google, the same one n8n uses. Session duration 24 hours. One Allow policy, Include the owner's email only; no Bypass, no Everyone rule. n8n's own Access application is **not** a wildcard, so there is no overlap to resolve.
- **Order:** the Access application was saved before the DNS record existed.
- **Route:** one ingress rule added to the existing tunnel's `config.yml`, after the n8n rule and before the `http_status:404` catch-all: `hostname: <sub>.<owner-domain>`, `service: http://localhost:8000`. Backup kept as `config.yml.bak`. Applied with `pm2 restart cloudflared`. DNS record created last with `cloudflared tunnel route dns n8n <sub>.<owner-domain>` (proxied CNAME to the tunnel). No new tunnel, no second connector.

| Check | Result |
| --- | --- |
| `curl -sI https://<sub>.<owner-domain>/healthz`, no cookie | `302`, `Location` at `<team>.cloudflareaccess.com` |
| `curl -sI https://<sub>.<owner-domain>/callback?code=x`, no cookie | same Access redirect: the gate covers every path |
| `cloudflared tunnel ingress validate` on the Pi | valid |
| `cloudflared tunnel ingress rule https://<sub>.<owner-domain>` on the Pi | selects the `http://localhost:8000` rule |
| `/healthz` in a browser logged in through Access | `{"status":"ok"}`, request logged on the Pi's console |
| n8n after the change | unchanged: its hostname and its Access gate work as before |
| 2.4 Private window shows the Access login, then loads after the owner logs in | **pending**, not yet verified |
| 2.5 A non-owner email is refused at the Access login | **pending**, not yet verified |

## Phase 3: Spotify app registration (2026-11-06)

- **Spotify app:** a new app dedicated to this project, in Development Mode, Web API selected. The owning account has active Premium. Exactly one redirect URI, `https://<sub>.<owner-domain>/callback`, re-read after saving. The owner's account is listed under User Management.
- **Env file:** `/etc/spotify-to-led/env` on the Pi, outside the git tree, owned by `<service-user>`, mode `0600`. Keys: `SPOTIFY_CLIENT_ID`, `SPOTIFY_REDIRECT_URI`. No `HOST` key (the default `127.0.0.1` is correct) and no client secret (PKCE never sends it). Nothing reads the file until F-02 and S-01 wire it in. The repo's `.env.example` documents the same keys with placeholder values.

| Check | Result |
| --- | --- |
| `sudo stat -c '%a %U' /etc/spotify-to-led/env` | `600 <service-user>` |
| `sudo grep -cE '^(SPOTIFY_CLIENT_SECRET\|HOST)=' /etc/spotify-to-led/env` | `0` |
| Dashboard redirect URI vs `SPOTIFY_REDIRECT_URI`, character by character | identical |
| User Management lists the owner; account shows Premium active | yes |

## Phase 4: Record the outcome (2026-11-06)

- `git diff main --name-only` lists files under `context/` plus `.env.example`, which Phase 3 added at the repo root. It carries only the placeholder `led.example.com`, so the plan's "only `context/`" criterion was accepted with that one exception.
- No real Access team domain or private LAN address in this change folder (`git grep` check in the plan).
- Foundation updates: `infrastructure.md` (connector location superseded, redirect-URI risk updated, Premium-lapse risk added, env-file path in Secrets), `prd.md` (DHCP reservation not needed), `roadmap.md` (Open Question 7 and the F-03 unknown resolved).
