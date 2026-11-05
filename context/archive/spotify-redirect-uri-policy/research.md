---
date: 2026-11-03T18:04:12+01:00
researcher: pawel
git_commit: 69d3c7fa3da31257059e3a1d0360fbf67b23131a
branch: main
repository: 20_led_board
topic: "Does Spotify's redirect-URI policy allow the local web page to be the re-authorization surface? (PRD Open Question 6)"
tags: [research, spotify, oauth, redirect-uri, fr-005, fr-015, headless-setup, cloudflare-tunnel]
status: complete
last_updated: 2026-11-03
last_updated_by: pawel
last_updated_note: "Two owner inputs. (1) Owner owns a domain and can reverse-proxy through Cloudflare: added the Cloudflare Tunnel mechanism and made it the recommendation, superseding the SSH-tunnel recommendation, and retired the private-IP-HTTPS open question as moot. (2) Owner already runs n8n behind Cloudflare on that domain: added the Option A / Option B wiring section, recommended Option A (reuse the existing tunnel, route to the Pi's LAN IP, no connector on the Pi), and corrected the FR-014 note, which had treated a second boot-critical connector as unconditional when it applies to Option B only."
---

# Research: Spotify redirect-URI policy vs. the LAN web page as re-authorization surface

**Date**: 2026-11-03T18:04:12+01:00
**Researcher**: pawel
**Git Commit**: 69d3c7fa3da31257059e3a1d0360fbf67b23131a
**Branch**: main
**Repository**: 20_led_board

## Research Question

PRD Open Question 6 ([prd.md:279](../../foundation/prd.md#L279)): does Spotify's redirect-URI policy allow the local web page to be the re-authorization surface? FR-005 ([prd.md:157](../../foundation/prd.md#L157)) and FR-015 ([prd.md:215](../../foundation/prd.md#L215)) assume the owner can finish an OAuth redirect by pointing a browser at the service on the LAN. Marked `Block: yes (resolve before the tech-stack step)`.

## Summary

**The assumption as written is dead: a plain LAN HTTP redirect URI is not registrable.** Spotify's current published rule permits HTTP for exactly one host form -- loopback IP literals -- and requires HTTPS for everything else. A LAN address such as `http://192.168.1.50:8080/callback` or `http://raspberrypi.local:8080/callback` is a non-loopback HTTP URI and is therefore disallowed. `localhost` is separately banned by name. This was enforced for apps created on or after 2025-04-09 and for all existing apps from 2025-11-27, a date **already 11 months past** as of this research.

**But FR-005 and FR-015 survive, because the obstacle is the host in the registered string, not the LAN page itself.** The registered redirect URI only has to be an exact-match string that the *browser* can land on; Spotify's servers never connect to it. Four mechanisms keep the owner's re-auth experience on the Pi's own web page, all compatible with the FR-015 wording and with the guardrail that the refresh credential never leaves the Pi ([prd.md:51](../../foundation/prd.md#L51)):

1. **Owned domain fronted by Cloudflare Tunnel (recommended).** Register `https://<sub>.<owner-domain>/callback`. A `cloudflared` replica on the Pi makes an outbound-only connection to Cloudflare, which terminates a publicly valid TLS certificate at the edge and proxies to the Pi's FastAPI app. This is the only mechanism that satisfies Spotify's HTTPS rule *and* FR-015 exactly as written -- re-auth works from any device, including a phone, with no tunnel command and no certificate warning. Added after the owner stated they already own a domain and can reverse-proxy through Cloudflare. Conditions and caveats in "The Cloudflare path" below; the two that matter are that the page becomes internet-reachable (so it must be gated) and that PKCE becomes advisable rather than optional.
2. **SSH port-forward onto a loopback redirect.** Register `http://127.0.0.1:PORT/callback`. The owner tunnels that port from laptop to Pi, opens `http://127.0.0.1:PORT` in the laptop browser, and the whole flow -- including the code-for-token exchange -- happens inside the Pi's process. Cheapest in code: zero new components, zero third parties, nothing exposed. Cost: re-auth requires one SSH command, so it is not "open the page on any device and click". Best fallback if the Cloudflare path is unwanted.
3. **Authorization-code hand-paste.** Register a loopback URI, let the laptop browser land on a dead `127.0.0.1` page, and copy the `code=` parameter from the address bar into a field on the Pi's page, which performs the exchange. The exchange only needs `redirect_uri` to *match* the authorize request -- it does not need to be reachable from the Pi. Cost: a manual copy-paste inside the code's short validity window.
4. **Hosted HTTPS relay page.** Register an HTTPS page you control (static, client-side only) that forwards the code to a user-supplied LAN address. This is what OwnTone -- a near-identical headless LAN music server -- actually adopted after rejecting loopback as impractical for headless servers. Cost: a second deployed artifact, and the code transits a page served by a third-party host. Superseded for this project by mechanism 1, which gets the same "works from any device" property without a second artifact.

**Retired sub-question:** whether `https://<private-LAN-IP>:PORT/callback` is registrable is now moot. It was the awkward way to get HTTPS onto a local address, and it needed a certificate no public CA will issue for a private IP. Mechanism 1 obtains a publicly valid certificate for a real hostname instead. Recorded in Open Questions as retired rather than deleted, with the evidence that prompted it.

**Process note:** the question is labelled "resolve before the tech-stack step", but [tech-stack.md](../../foundation/tech-stack.md) already exists and already commits FastAPI to serving "the Spotify re-authorization route" ([tech-stack.md:29](../../foundation/tech-stack.md#L29)). That commitment is unharmed -- all three mechanisms above keep the route on the Pi's FastAPI app -- so the gate was passed without damage, but it was passed.

## Detailed Findings

### The rule itself (authoritative, fetched live 2026-11-03)

Spotify's redirect-URI concept page states three requirements verbatim:

- "Use HTTPS for your redirect URI, unless you are using a loopback address, when HTTP is permitted."
- "If you are using a loopback address, use the explicit IPv4 or IPv6, like `http://127.0.0.1:PORT` or `http://[::1]:PORT` as your redirect URI."
- "`localhost` is not allowed as redirect URI."

Source: <https://developer.spotify.com/documentation/web-api/concepts/redirect_uri>, confirmed by two independent reads today (direct fetch and Context7 retrieval of the same page).

Applied to this product: the PRD's assumed surface is the service "on the LAN" ([prd.md:248](../../foundation/prd.md#L248)), i.e. a non-loopback host over HTTP. That is the exact combination the first requirement excludes. The ban is on the **host-plus-scheme pair**, not on local addresses generally -- `127.0.0.1` is a local address that *is* permitted over HTTP.

Two further details from the same page that matter downstream:

- Exact-match is required: "The definition of the redirect URI must exactly match the redirect URI you provide when you create your app. The only exception is for loopback IP literals, which can dynamically be assigned ports."
- Dynamic ports are a loopback-only privilege: register the loopback literal with no port and supply the port in the authorization request. The page states "this is only supported for loopback IP literals, and not for other redirect URIs."

### Enforcement status: past, not pending

- New apps: validations enforced from **2025-04-09**.
- Existing apps: migration required by **2025-11-27**, after which "requests relying on deprecated flows or redirect URIs will fail" and non-migrated apps "stop working" (<https://developer.spotify.com/blog/2025-10-14-reminder-oauth-migration-27-nov-2025>).

Both dates precede today (2026-11-03), so this is settled policy, not a deprecation to plan around. The same announcement also removed the **implicit grant flow** and directs apps to "Authorization Code Flow with PKCE or another supported flow". This does not disturb the tech-stack choice: a Python service holding a client secret uses Authorization Code, with PKCE optional.

Spotify's own migration tutorial confirms the two concrete rewrites: change non-loopback `http://` to `https://` "if possible", and if not possible "remove them"; and rewrite `http://localhost:8888/callback` to `http://127.0.0.1:8888/callback` (<https://developer.spotify.com/documentation/web-api/tutorials/migration-insecure-redirect-uri>).

### Contradiction resolved: does the dashboard still accept HTTP loopback?

Secondary sources circulate a claim that the dashboard now refuses even `http://127.0.0.1`, with the message "This redirect URI is not secure." If true, it would eliminate mechanisms 1 and 2 and force the relay page. It is not supported. Each report was adjudicated separately:

| Report | Date | Claim | Verdict |
|---|---|---|---|
| [hermes-official-plugins#114](https://github.com/NousResearch/hermes-official-plugins/pull/114) | resolved 2026-10-19 | Dashboard refuses to save `http://127.0.0.1:43827/spotify/callback` | **Contradicted.** Cause was a trailing space in the pasted URI; re-added without it, the same URI saved. PR kept HTTP loopback as the default and documented the misleading message. |
| [go-spotify-cli#34](https://github.com/Envoy49/go-spotify-cli/issues/34) | closed 2026-10-19 | Spotify "only accepts `https://`" | **Contradicted.** Maintainer concluded "plain `http://` is still allowed for the loopback IP"; the real break was `localhost`. Fixed by moving to `http://127.0.0.1:4949`. |
| [Omarchy-Spotify#115](https://github.com/stappmus/Omarchy-Spotify/issues/115) | opened 2026-10-04, still open | `http://127.0.0.1:8989/login` refused; hypothesises pre-existing loopback URIs keep working while newly added ones are refused | **Unsupported.** No resolution, no corroboration, and the author offers the mechanism as a guess. Contradicted by the two cases above from the same month. |

Conclusion on this inspected set of three reports: HTTP loopback registration works, and the "not secure" message is reported at least once as a whitespace artifact. Confidence is high but not absolute -- the cheapest possible validation is to paste `http://127.0.0.1:8080/callback` into the app's dashboard settings before committing to mechanism 1 or 2. That is a 30-second check and should be the first action taken on this finding.

### Precedent: how comparable headless LAN products solved it

Two independent precedents exist, both for software shaped exactly like this one (a LAN-resident service with no local browser):

**OwnTone** (self-hosted music server) lost `http://owntone.local:3689` to this rule. The maintainers evaluated five options and recorded why four lost ([owntone-server#1858](https://github.com/owntone/owntone-server/issues/1858)): Let's Encrypt impractical for local domains; self-signed certificates problematic on mobile browsers; loopback redirect "inconvenient for headless servers"; Spotify Connect zeroconf auth uncertain on token scope. They adopted a **GitHub-Pages-hosted HTTPS page** (`https://owntone.github.io/owntone-oauth/spotify/`) that takes the redirect and lets the user name their server address dynamically.

**Home Assistant** broke on `http://homeassistant.local:8123` and moved to a **cloud relay**, `https://my.home-assistant.io/redirect/oauth` ([community thread](https://community.home-assistant.io/t/spotify-integration-broken-by-new-redirect-uri-rules/908767)). The same thread documents a community workaround for headless installs: **SSH port forwarding** to reach the instance on `127.0.0.1:8123`, under Spotify's loopback exception. The thread also reports that `.local` hostnames and private IPs did not work as registered redirect URIs under the enforced rules -- the basis for the open sub-question below.

The significant asymmetry: both projects ship to **many** users, which makes per-user SSH setup unacceptable and justifies building and hosting a relay. This product is single-tenant by explicit choice ([prd.md:256](../../foundation/prd.md#L256)), so the cost that pushed OwnTone and Home Assistant toward a relay does not apply here, and the option they dismissed as "inconvenient for headless servers" (mechanism 2) is a viable one for a one-owner build.

The same asymmetry explains why neither project could take the path this project can. A redirect URI on an owned domain is per-deployment infrastructure: OwnTone and Home Assistant cannot require every user to own a domain, so they had to centralize it as a relay they operate. A single owner who already owns a domain gets the relay's whole benefit -- a publicly valid HTTPS endpoint -- without operating anything for anyone else. The precedents therefore do not argue against mechanism 1; they argue against *shipping* mechanism 1 to strangers, which this product explicitly never does.

### The Cloudflare path (mechanism 1), added 2026-11-03 on owner input

The owner owns a domain and can reverse-proxy through Cloudflare, which removes the constraint that made this question hard: the need for a browser-reachable HTTPS endpoint that resolves to a service with no public address. Cloudflare Tunnel supplies exactly that.

**Shape.** A `cloudflared` replica runs on the Pi and makes an **outbound-only** connection to Cloudflare's network; a public hostname route maps `<sub>.<owner-domain>` to `http://localhost:PORT` on the Pi. No inbound ports, no router port-forward, no DHCP fragility -- the tunnel reference states tunnels are "outbound-only connections - No inbound ports or firewall changes" and that the free tier carries unlimited tunnels and unlimited traffic (`references/tunnel/README.md`, `references/tunnel/gotchas.md` in the bundled `cloudflare` skill). The ingress config is three lines of YAML mapping hostname to `http://localhost:PORT`, or equivalent dashboard config with a token.

**Prerequisite, stated because it is not automatic:** the domain must already be on Cloudflare DNS. The current docs note that you must "add a website to Cloudflare" before publishing applications through a tunnel (<https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-remote-tunnel/>). The owner's statement that they can reverse-proxy through Cloudflare implies this is already true; if the domain is registered elsewhere with nameservers untouched, that is a setup step, not a blocker.

**The one real consequence: the page stops being LAN-only.** A public hostname is reachable by anyone who learns it, which escalates the accepted risk at [prd.md:283](../../foundation/prd.md#L283) from "anyone on the home network could re-point the service at their own Spotify account" to "anyone on the internet who learns the hostname". That risk was accepted on LAN-scoped reasoning and does **not** transfer. The fix is in the same product family: put **Cloudflare Access** in front of the hostname with an Allow policy scoped to the owner's email, authenticated by one-time PIN so no external identity provider is needed. Access policy actions are Allow / Block / Bypass / Service Auth, combined from Include/Require/Exclude rules (<https://developers.cloudflare.com/cloudflare-one/access-controls/policies/>); with Access enabled, "Cloudflare checks every HTTP request bound for that site to ensure that the request has a valid CF_Authorization cookie" and blocks requests without it.

**Access does not break the Spotify callback.** This was the mechanism's main risk and it checks out: Access preserves OAuth callback query parameters in its authentication return URL, so if the owner's Access session has lapsed when Spotify issues the redirect, they log in and Access returns them to the full callback URL with `code=` intact. The Pi's own outbound calls to the Spotify API are unaffected either way, since Access gates inbound HTTP only.

**Second-order cost of gating:** Access applies to the whole hostname, so the status page -- the part of FR-015 the owner glances at, not just the re-auth action -- also sits behind a login. For a page consulted at a glance, an Access session lapse is friction on the common path to protect the rare one. Two shapes are available and this is a product choice, not a research gap: gate the whole hostname (simplest, safest), or keep the status view on the LAN address and expose only the callback route through the tunnel (preserves the glanceable page, but then the status page and the re-auth surface live at two addresses, which is a wording change for FR-015).

**Use PKCE with this mechanism.** Cloudflare is a reverse proxy, so TLS terminates at the edge and the authorization code is visible to the edge at that hop -- inherent to proxying, not a Cloudflare defect, and the same property that makes the HTTPS certificate work. The refresh credential is unaffected: it is minted on the Pi by the token exchange and never transits the tunnel, so the guardrail at [prd.md:51](../../foundation/prd.md#L51) holds. For the short-lived code, PKCE removes the consequence entirely: the code becomes unusable without the `code_verifier` that never leaves the Pi. Spotify's own migration guidance already directs apps to "Authorization Code Flow with PKCE or another supported flow", so this costs nothing and is the deciding reason to prefer PKCE over plain Authorization Code here. Mechanisms 2 and 3 do not need PKCE for this reason, since nothing terminates TLS between browser and Pi.

**Net effect on the question.** Mechanism 1 is the only option that leaves FR-005, FR-015 and US-02 true as currently written, and it also retires the "re-auth needs a laptop" caveat that mechanism 2 forces onto FR-001. Its cost is an Access policy plus one honest change to the Access Control section, which currently says the page is ungated and LAN-only ([prd.md:246-248](../../foundation/prd.md#L246-L248)). Whether it also costs new software on the Pi depends on the wiring choice below.

### Wiring: reuse the existing tunnel, or run a second one? (added 2026-11-03 on owner input)

The owner already runs n8n on this domain behind Cloudflare, with reboot survival already solved for that connector. Two wirings follow, and they differ in what new thing has to stay alive.

**Option A -- reuse the existing tunnel, add a public hostname pointing at the Pi's LAN address.** A tunnel can publish many hostnames, and `cloudflared` can proxy to anything the host it runs on can reach: "The machine running the tunnel can connect to any IP/Port that it can reach on its own (WAN or LAN)" ([community thread](https://community.cloudflare.com/t/can-a-cloudflare-tunnel-be-used-to-access-a-http-service-on-another-machine-on-the-same-network/452760)). So the n8n host's existing connector maps `<sub>.<owner-domain>` to `http://<pi-lan-ip>:PORT`. **No `cloudflared` on the Pi at all.** This is the owner's reading and it is correct: nothing new has to survive a reboot on the Pi beyond the app itself, which pm2 already covers. Costs: the Pi needs a reserved DHCP lease or static IP, because the route is now pinned to its address; and the n8n host becomes a dependency of this project's re-auth surface, so taking that machine down takes the re-auth path with it. Neither touches the board display, which is outbound-only.

**Option B -- a separate tunnel with its own `cloudflared` on the Pi**, mapping the hostname to `http://localhost:PORT`. Self-contained: no dependency on the Pi's LAN address and none on the n8n host. Costs: a second connector to install and keep starting on boot, on the Pi, where that is not yet solved.

**The trap in between, worth stating because it looks like the cheap option and is not:** do not install `cloudflared` on the Pi as an **additional replica of the n8n tunnel**. Replicas of one tunnel share a tunnel UUID and a single config, and traffic is load-balanced across them -- the bundled reference states replicas are "load balanced automatically" and "share same tunnel UUID, get unique connector IDs" (`references/tunnel/gotchas.md`). A `localhost` service definition would then mean the n8n box on some requests and the Pi on others, producing an intermittently wrong origin rather than a clean failure. Replicas are for serving identical config redundantly; two different machines serving two different services need either Option A's LAN-IP route or Option B's separate tunnel.

**Recommended: Option A**, on the owner's reasoning. It reuses proven infrastructure, adds nothing to the Pi, and the static-IP requirement is a one-line router change. Option B is the better answer only if the n8n host's availability should not gate this project.

**Access is per-hostname, so the new subdomain is not covered by whatever protects n8n.** Each Access application is defined against a selected hostname; protecting one subdomain does not protect another on the same domain, absent explicit wildcard configuration (<https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/>). Adding the subdomain therefore means adding its own Access application and policy, under either option. Easy to miss precisely because the domain is already "behind Cloudflare".

### Consequences for the affected requirements

- **FR-005** ("re-authorize from the local web page", [prd.md:157](../../foundation/prd.md#L157)) does not need a different mechanism, contrary to the Open Question's own conditional. It needs the *redirect target* restated: the page still initiates the flow and still performs the token exchange; only the host in the registered URI changes.
- **FR-015** ([prd.md:215](../../foundation/prd.md#L215)) stays load-bearing. Under all four mechanisms the page remains where re-auth happens. Only mechanism 1 keeps it true for *any* device without a prior tunnel command.
- **FR-001's** acknowledged cost ([prd.md:140-142](../../foundation/prd.md#L140-L142)) depends on the mechanism chosen. Under mechanism 1 the Socratic note stands unchanged -- the web page genuinely is what makes headless OAuth workable. Under mechanisms 2 and 3 the honest restatement is that re-auth needs a laptop and one tunnel command or a copy-paste, not merely a browser on the LAN.
- **US-02's** acceptance criterion that re-auth completes "with no restart and no physical access to the Pi" ([prd.md:80](../../foundation/prd.md#L80)) holds under all four mechanisms -- neither an SSH tunnel nor an Access login is physical access.
- **The ungated-LAN-page accepted risk** ([prd.md:283](../../foundation/prd.md#L283)) moves in *opposite directions* depending on the mechanism, which is the sharpest trade-off in this research. Mechanisms 2 and 3 *reduce* it for free: if the redirect only completes over a loopback tunnel, a random LAN guest can no longer finish an OAuth flow against their own account even though the page is reachable. Mechanism 1 *enlarges* it, from the home network to the internet, and therefore requires Cloudflare Access to stay within the risk the owner actually accepted. The accepted risk was dismissed on LAN-scoped reasoning and that reasoning does not survive a public hostname.
- **The Access Control section** ([prd.md:246-248](../../foundation/prd.md#L246-L248)) is the only place mechanism 1 contradicts the PRD as written: it states "Single user; no auth", a flat model with "no sign-up, no sign-in, no gated routes", and a page on the LAN. With Cloudflare Access in front, there *is* a sign-in -- at the edge rather than in the application, so the FastAPI app still has no auth code -- and the route is gated. This needs an owner edit to that section, not a workaround.
- **FR-014** (unattended operation, resumes after reboot with no human action, [prd.md:211](../../foundation/prd.md#L211)) is unaffected under Option A wiring, which is the recommended one: no connector runs on the Pi, so the only thing needing boot survival there is the app, already covered by the planned "pm2 with pm2 startup" ([tech-stack.md:29](../../foundation/tech-stack.md#L29)). Under Option B it does gain a second boot-critical component, and the plan would need to say whether pm2 or `cloudflared`'s own service installer supervises it. Corrects an earlier note in this document that treated the extra connector as unconditional; it is a property of Option B only.
- **The refresh-credential guardrail** ([prd.md:51](../../foundation/prd.md#L51)) is satisfied by all four: in each, the Pi performs the code-for-token exchange and is the only holder of the refresh token. The weakest two for the short-lived authorization code (not the refresh token) are mechanism 4, where the code transits a third-party-hosted static page, and mechanism 1, where TLS terminates at Cloudflare's edge -- both neutralized by PKCE.

## Code References

No application code exists at this commit; the repository contains foundation documents only.

- `context/foundation/prd.md:279` - Open Question 6, the question resolved here
- `context/foundation/prd.md:157-159` - FR-005, re-authorization from the local web page
- `context/foundation/prd.md:215-217` - FR-015, the local web page as load-bearing surface
- `context/foundation/prd.md:140-142` - FR-001 and its headless-OAuth cost note
- `context/foundation/prd.md:51` - guardrail: refresh credential never leaves the device
- `context/foundation/prd.md:248-250` - Access Control: ungated LAN page carrying the re-auth action
- `context/foundation/prd.md:283` - accepted risk: ungated LAN page can start an OAuth flow
- `context/foundation/tech-stack.md:29` - FastAPI commitment to serve "the Spotify re-authorization route"

## Architecture Insights

- **A redirect URI is a string contract, not a network path.** Spotify validates it by exact match and the user's browser is the only party that dereferences it. Every mechanism here exploits that: the registered host can be a loopback literal that means something different on the browser's machine than on the Pi.
- **Loopback is the privileged host form** in the current policy: it is the only one allowed over HTTP, and the only one permitted a dynamic port. A design that can arrange for the browser to reach the service over loopback gets both concessions; one that insists on a real LAN hostname gets neither.
- **Single-tenancy is the lever.** The Bluetooth-proximity constraint that forces this service to live next to its board ([prd.md:24](../../foundation/prd.md#L24)) also means exactly one person ever authorizes it. That makes both a one-command SSH tunnel and a personally-owned domain acceptable where a shipped product could assume neither.
- **The real choice is where the HTTPS boundary lives, and it trades convenience against exposure.** Spotify's rule forces one of exactly two concessions: either the browser reaches the service over loopback (keeping everything local but requiring the owner to arrange that reachability themselves), or something publicly trusted terminates TLS in front of the service (removing all owner effort at re-auth time but putting the page on the internet). There is no third position, because a locally-hosted certificate for a private address is trusted by no browser out of the box. Mechanism 1 takes the second concession and buys back the lost privacy with Cloudflare Access and PKCE.

## Historical Context (from prior changes)

Not applicable -- `context/changes/` and `context/archive/` contain only their README files at this commit; this is the first change folder.

The relevant prior reasoning lives in the foundation documents instead: the shape notes record the same headless-OAuth concern and the SSH-versus-web-page trade-off at [shape-notes.md:227-230](../../foundation/shape-notes.md#L227-L230) and [shape-notes.md:263](../../foundation/shape-notes.md#L263). Notably, "SSH would be cheaper than building a web flow" was raised as a counter-argument to FR-005 and dismissed. This research does not revive it -- the web page is still needed for status reporting (FR-015) and still hosts the exchange -- but it does mean SSH re-enters the setup story through the tunnel, which is closer to that dismissed counter-argument than the PRD currently admits.

## Related Research

None -- this is the first research artifact in the repository.

## Open Questions

1. **RETIRED 2026-11-03 -- Is `https://<private-LAN-IP>:PORT/callback` registrable?** Moot once the owner supplied a domain fronted by Cloudflare. Recorded for the evidence: the official redirect-URI page does not mention private IPs in either direction; the Home Assistant thread reports that private IPs and `.local` hostnames did not work under the enforced rules; no source found confirms a successful registration. Inspected scope was the Spotify redirect-URI concept page, the migration tutorial, the 2025-10-14 migration blog post, and five community reports. Nothing downstream depends on resolving it.
2. **Does the dashboard accept `http://127.0.0.1:PORT/callback` today?** High confidence yes per the three-report adjudication above, but confirmable in 30 seconds by pasting it into the app settings. Needed only if mechanism 2 or 3 is chosen; mechanism 1 registers an ordinary HTTPS URI and does not depend on this.
3. **RESOLVED 2026-11-03 -- Gate the whole hostname, or only tunnel the callback route?** Whole hostname. The owner confirmed n8n already sits behind Cloudflare Access with a working login method, so adding an Access application for the new subdomain reuses existing configuration rather than introducing a new mechanism. Keeping FR-015 as one page at one address was the deciding factor. Written into [prd.md Access Control](../../foundation/prd.md#L244).
3a. **Does the Pi have a reserved DHCP lease or static IP?** Required by the recommended Option A wiring, which pins the public hostname to the Pi's LAN address. Unverified here -- a router setting, not observable from this repository. If the Pi's address moves, re-auth breaks silently while the board keeps working, which is the most confusing possible failure mode for this project. Either reserve the lease or choose Option B.
4. **RESOLVED 2026-11-03 -- Does the owner accept the Access Control rewrite that mechanism 1 requires?** Yes, narrowed rather than dropped, on the owner's instruction: the product is still single-user with no authentication of its own, and the edge gate is named explicitly. Applied to the PRD along with a new Existing Infrastructure section, the Open Question 6 resolution, and the replacement of the superseded ungated-LAN-page risk. Nothing blocking remains between this research and a plan.
5. **Unaffected by this research but still open:** Open Questions 1-5 in the PRD ([prd.md:274-278](../../foundation/prd.md#L274-L278)). Question 6 was the only one flagged blocking.
