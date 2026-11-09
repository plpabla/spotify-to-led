---
project: spotify-to-led
version: 1
status: draft
created: 2026-11-03
updated: 2026-11-09
prd_version: 1
main_goal: quality
top_blocker: decisions
milestone_id: unattended-truthful-board
milestone_seq: 1
milestone_status: open
---

# Roadmap: spotify-to-led

> Derived from `context/foundation/prd.md` (v1), `context/foundation/tech-stack.md`,
> `context/foundation/infrastructure.md`, `context/foundation/shape-notes.md`
> (`## Forward: technical-roadmap`), `context/changes/spotify-redirect-uri-policy/research.md`
> and `context/changes/bootstrap-verification/verification.md`, plus an auto-researched
> codebase baseline.
> Edit-in-place; the next milestone continues this file. Archive only when an independent
> roadmap replaces it.
> Slices below are listed in dependency order. The "At a glance" table is the index.

## Milestone

**M-1: Unattended truthful board** -- Status: open

- **Intent:** The board, running unattended on the Raspberry Pi, shows the correct presentation
  for the Spotify account across every case the display rule covers, and never presents a cover
  it cannot vouch for without marking it. Proves the two things v1 is actually judged on:
  it runs by itself, and it does not lie.
- **Source materials:** `context/foundation/prd.md` (v1) and the rest of `context/foundation/`,
  plus the change-scoped research in `context/changes/`.
- **Done when:** every F-NN and S-NN below is `done`, except `S-08`, which is post-v1 (M-2).
- **Scope anchors:** FR-001 through FR-018 (all 18 are must-have), US-01 through US-06, both
  Non-Functional Requirements, the Access Control section, and the four setup deltas named in
  the PRD's Existing Infrastructure section.

## Vision recap

The owner has an iDotMatrix LED pixel board with no job worth doing, and Spotify playing on a
desk with no physical indication of what is on. The parts exist and the glue does not: Spotify
exposes the currently-playing item and its cover art, community libraries already speak the
board's protocol, and nobody has bundled them into something that just runs. A second motive is
stated outright in the PRD -- "I want to play around with that hardware" -- so the build is part
of the point, not only its output.

One constraint shapes everything downstream: Bluetooth needs physical proximity, so this service
can never be centralized or hosted. It must always run next to its own board.

## North star

**S-02: Board shows the idle display on the Pi, unattended, surviving a cold boot** -- this is
the first slice that makes the PRD's primary success criterion true ("the service runs unattended
on the Raspberry Pi: it starts as a system service and resumes working after a reboot with no
human action"), and landing it is what allows the 7-day unattended observation to begin at all.

> "North star" here means the smallest end-to-end flow whose successful delivery would prove the
> product actually works, placed as early as its prerequisites allow because everything after it
> only matters if this holds. It is scoped to the idle display rather than to artwork because a
> slice has to be user-visible: a supervised service with nothing on the board is a foundation, and
> the idle display is the smallest real presentation that makes "runs unattended" observable
> from across the room.

## At a glance

| ID   | Change ID                                   | Outcome (user can ...)                                                                                      | Prerequisites    | PRD refs                                        | Status   |
| ---- | ------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ---------------- | ----------------------------------------------- | -------- |
| F-01 | `app-skeleton-and-board-transport`          | (foundation) a real service package replaces the placeholder, pushing frames through one swappable transport | --               | FR-012, FR-016                                  | done        |
| F-02 | `pm2-supervised-service`                    | (foundation) the service is supervised by the Pi's existing pm2 daemon, and its logs survive a reboot       | F-01             | FR-014, NFR-02                                  | done        |
| F-03 | `edge-surface-and-spotify-app-registration` | (foundation) the service's hostname resolves, is gated at the edge, and is registered with Spotify         | --               | Access Control, Existing Infrastructure, FR-001 | done        |
| S-01 | `spotify-integration`                       | authorize the Spotify account once and have the service keep reading it unattended                          | F-01, F-03       | US-02, FR-001, FR-002, FR-003, FR-016           | done |
| S-02 | `unattended-idle-board`                     | power-cycle the Pi and have the board come back by itself showing the idle display                          | F-02, S-01       | US-03, US-05, FR-011, FR-012, FR-014, NFR-02    | done |
| S-03 | `bluetooth-link-self-recovery`              | switch the board off and on, or carry it out of range and back, and have the link return with no human action | S-02             | US-05, FR-013                                   | done |
| S-04 | `artwork-on-the-board`                      | see the artwork of whatever is playing, and see it change when the item changes                             | S-02             | US-01, FR-006, FR-007, FR-008, FR-016, NFR-01   | in-progress |
| S-05 | `all-item-types-and-frame-reuse`            | see artwork for any item type including podcasts, with repeated artwork never re-rendered                   | S-04             | US-01, FR-010, FR-017                           | proposed |
| S-06 | `staleness-marker-and-display-rule`         | tell at a glance that the board can no longer vouch for the cover it is showing                             | S-04             | US-02, US-04, FR-004, FR-009, FR-018            | blocked  |
| S-07 | `status-and-reauthorization-page`           | open one page, read what the service believes and whether the board link is alive, and re-authorize Spotify | S-01, F-03, S-06 | US-02, US-06, FR-004, FR-005, FR-015            | proposed |
| S-08 | `ai-enhanced-covers`                        | see an AI-enhanced version of the playing cover, made for the 32x32 panel rather than shrunk to it          | S-04, S-05       | -- (PRD v1 non-goal; post-v1, M-2)              | deferred |

## Streams

Navigation aid -- groups items that share a Prerequisites chain. Canonical ordering still lives
in the dependency graph below; this table is the proposed reading order across parallel tracks.

| Stream | Theme                 | Chain                                | Note                                                                                                            |
| ------ | --------------------- | ------------------------------------ | --------------------------------------------------------------------------------------------------------------- |
| A      | Unattended operation  | `F-01` -> `F-02` -> `S-02` -> `S-03` | Carries the north star. Sequenced first because `main_goal: quality` refuses to defer the boot story.           |
| B      | Account access        | `F-03` -> `S-01`                     | Starts in parallel with Stream A at `F-01`/`F-03`, joins it at `S-02`. Holds the human-gated edge work.         |
| C      | What the board shows  | `S-04` -> `S-05` / `S-06`            | The display rule, built up one presentation at a time. `S-05` and `S-06` are siblings, not a chain.             |
| D      | The owner's window in | `S-07`                               | Standalone tail: the only surface that names *why* the service is blind, which the board deliberately will not. |
| E      | Beyond v1             | `S-04` -> `S-05` -> `S-08`           | Post-v1 (M-2). Starts only after M-1 closes; the PRD keeps AI-enhanced covers out of v1.                        |

## Baseline

What is already in place in the codebase as of `2026-11-03` (auto-researched, user-confirmed).
Foundations below assume these are present and do NOT re-scaffold them.

- **Frontend:** absent -- declared as one static page with vanilla JS per `tech-stack.md`. No
  templates, static assets or JS exist.
- **Backend / API:** partial -- `fastapi` and `uvicorn` are real dependencies
  (`pyproject.toml:7-10`, `uv.lock`) and installed under CPython 3.14.0. Zero application code:
  `src/spotify_to_led/main.py` is still the uv `print("Hello from 20-led-board!")` placeholder,
  with no ASGI app object and no routes.
- **Data:** absent, and no database by design (`infrastructure.md`: "there is no database"). The
  FR-017 rendered-frame cache has no store, path or on-disk key scheme yet.
- **Auth:** absent in the application **by design and correctly so** -- the PRD's Access Control
  section forbids auth in service code. The only gate is a Cloudflare Access application at the
  edge, which does not exist yet: Access applications are per-hostname, so the policy fronting
  the owner's n8n deployment does not extend to this subdomain.
- **Deploy / infra:** absent -- declared as Pi + systemd system unit + uv with manual promotion.
  No unit file, no deploy script, no `.github/`, no `scripts/`. Already live and inherited rather
  than built here: Cloudflare DNS, a tunnel connector that already survives reboots, and a working
  Access login method.
- **Observability:** absent -- declared as journald. Nothing configured, and
  `Storage=persistent` is not set, which `infrastructure.md` flags as the thing that erases the
  evidence for the 7-day requirement.
- Superseded, 2026-11-06: the supervisor is pm2, not a systemd unit, and the logs are pm2's
  on-disk files under `~/.pm2/logs`, rotated by pm2-logrotate. journald on the Pi turned out to be
  persistent by default config anyway. See `infrastructure.md` and F-02.
  Corrected, 2026-11-07: journald was volatile (Raspberry Pi OS ships `Storage=volatile`), so
  the rollout added `/etc/systemd/journald.conf.d/50-persistent.conf` (`Storage=persistent`,
  `SystemMaxUse=100M`). See `context/archive/2026-11-06-pm2-supervised-service/verification.md`.
- **Incidental:** `.env` already carries `IDOT_MAC`, so the board's Bluetooth address has been
  obtained. That is part of `infrastructure.md`'s Getting Started step 3 already done.

## Foundations

### F-01: Service package and swappable board transport

- **Outcome:** (foundation) a real `spotify_to_led` service package replaces the uv placeholder and
  can push a single 32x32 frame to the board through one transport interface that has both a real
  Bluetooth implementation and a stub that writes the frame to a PNG.
- **Change ID:** `app-skeleton-and-board-transport`
- **PRD refs:** FR-012, FR-016
- **Unlocks:** `S-01` and `S-02` directly, and every slice downstream of them -- nothing can be
  planned against a `print()` placeholder. Reduced the unknown "is `idotmatrix` 0.0.9 usable
  on Python 3.14, and which of the three forks" to a one-file swap behind the interface, and then
  closed it outright: `idotmatrix-api-client` 0.3.0 is pinned and proven on the board (2026-11-04). Creates
  the verification path FR-016 names: the laptop loop runs the Spotify and rendering layers at
  full fidelity against the stub, with no board attached.
- **Prerequisites:** --
- **Parallel with:** F-03
- **Blockers:** --
- **Unknowns:**
  - ~~Which `idotmatrix` implementation to pin~~ -- RESOLVED 2026-11-04: `idotmatrix-api-client`
    0.3.0, pinned exactly, proven on the Pi against a real 32x32 board. Neither 0.0.9 nor the
    `brmk` / `derkalle4` forks were adopted, and no further fork evaluation was done: the transport
    interface makes a later swap a one-file change, which was the point of building it.
- **Risk:** Sequenced first because it is the only item with no prerequisites that everything else
  needs, and because it is where the `top_blocker: external` risk gets contained. The thing that
  could go wrong is scope: this must stay a skeleton plus one interface and a single-frame smoke
  test on the Pi, not a finished board driver. If `idotmatrix` turns out to be broken on 3.14, this
  is the cheapest possible place to discover it.
- **Status:** done

### F-02: Supervised service and logs that survive a reboot

- **Outcome:** (foundation) the service is supervised by the pm2 daemon that already runs
  `cloudflared` and n8n on the Pi, comes back after a cold boot with no human action, is never
  marked `errored` and abandoned by pm2's restart limit, and writes rotated logs to disk that
  persist across reboots.
- **Change ID:** `pm2-supervised-service`
- **PRD refs:** FR-014, NFR-02
- **Unlocks:** `S-02`, the north star -- "resumes after a reboot with no human action" is a
  sentence about the supervisor's boot path, here `pm2-<user>.service` running `pm2 resurrect`.
  Creates the only viable verification path for NFR-02 (the 7-day unattended property): logs
  that are still on disk after the very reboot being investigated, bounded by pm2-logrotate.
- **Prerequisites:** F-01
- **Parallel with:** S-01
- **Blockers:** --
- **Unknowns:** --
- **Risk:** `main_goal: quality` is why this is a foundation rather than a tail-end deployment
  chore. `infrastructure.md`'s pre-mortem is a description of this item being deferred: a startup
  crash loop burns the supervisor's restart budget and it gives up -- leaving a two-day-old cover
  on the board with no marker and no status page to ask, because the page is served by the process
  that died. Under pm2 the guards are exponential backoff (and, if the Pi shows it is needed, a
  large `max_restarts`), a `kill_timeout` long enough for clean GATT teardown, unbuffered output,
  `pm2 save` after every change to the app set, and one daemon restart so the app inherits the
  `bluetooth` group. They are recorded in the risk register, so the danger is skipping them, not
  finding them.
- **Status:** done

### F-03: Edge surface and Spotify app registration

- **Outcome:** (foundation) the project's subdomain resolves to the Pi through the connector that
  already runs on this network, is gated by its own Cloudflare Access application scoped to the
  owner, and is registered with Spotify as an exact-match redirect URI.
- **Change ID:** `edge-surface-and-spotify-app-registration`
- **PRD refs:** Access Control, Existing Infrastructure, FR-001
- **Unlocks:** `S-01` (no Spotify read is possible until the account has been authorized once, and
  the authorization cannot complete without a registered redirect URI the browser can reach) and
  `S-07`. Retires the highest-likelihood, highest-impact entry in `infrastructure.md`'s risk
  register -- a `redirect_uri` built as `http://<lan-ip>` and rejected by Spotify -- by fixing the
  exact string before any code depends on it.
- **Prerequisites:** -- (the tunnel connector, Cloudflare DNS and a working Access login method
  are inherited, not built here)
- **Parallel with:** F-01
- **Blockers:** Every step here is human-only by policy. `infrastructure.md` records that an agent
  may deploy, restart and roll back unattended, but "a human is required for anything that touches
  the edge or the account: creating or modifying the Cloudflare Access application, changing the
  tunnel's public hostname route, registering redirect URIs in the Spotify app settings, and
  completing an OAuth consent." These are outward-facing and affect a domain shared with a live
  n8n deployment.
- **Unknowns:**
  - ~~Does the Pi have a reserved DHCP lease or a static IP?~~ -- RESOLVED 2026-11-06: not needed,
    because the connector runs on the Pi and routes to `localhost`. Owner. Block: no -- a router setting,
    and Option B (a separate connector on the Pi) is the documented fallback. Worth resolving
    early anyway: if the Pi's address moves, re-authorization breaks silently while the board
    keeps working, which the research calls the most confusing possible failure mode here.
- **Risk:** Four discrete configuration deltas, not an auth layer -- this foundation writes no
  application code and installs no tunnel. The named trap is adding `cloudflared` on the Pi as an
  extra *replica* of the n8n tunnel: replicas share one UUID and config and are load-balanced, so a
  `localhost` service definition would resolve to the n8n box on some requests and the Pi on
  others, producing an intermittently wrong origin instead of a clean failure. Sequenced in
  parallel with F-01 because it is the item whose lead time is outside the owner's keyboard.
- **Status:** done

## Slices

### S-01: Authorize Spotify once and keep reading the account

- **Outcome:** Owner can authorize the Spotify account once from a browser on any device, after
  which the service keeps determining what is playing with no further human action for as long as
  the stored credential stays valid.
- **Change ID:** `spotify-integration` (renamed 2026-11-08 from `spotify-authorization-and-playback-read`,
  the ID the change was actually opened under in `context/changes/`)
- **PRD refs:** US-02, FR-001, FR-002, FR-003, FR-016
- **Prerequisites:** F-01, F-03
- **Parallel with:** F-02
- **Blockers:** Completing the OAuth consent is a human step (see F-03).
- **Unknowns:**
  - None blocking. Two decisions are already made in the research and should be carried into the
    plan rather than rediscovered: use Authorization Code with PKCE (TLS terminates at the
    Cloudflare edge, so PKCE removes the consequence of the code being visible at that hop), and
    pin the redirect URI from configuration instead of deriving it from the inbound request.
- **Risk:** Sequenced before the north star because nothing can assert "nothing is playing"
  without first being able to read the account. The failure mode to watch is the one
  `infrastructure.md` lists under unknown unknowns: the connector is on a different LAN host than
  the Pi, and uvicorn only trusts forwarded headers from `127.0.0.1`, so a derived redirect URI
  becomes `http://192.168.x.x:8000/callback` and Spotify rejects it in a way that presents as a
  Spotify fault. Also note the one-off trap that an Access session expiring mid-consent can strand
  the authorization code on a login page; re-running the flow fixes it. Superseded, 2026-11-06:
  F-03 found the connector runs on the Pi and routes to `localhost:8000`, so the forwarded-header
  trap no longer applies; pinning the redirect URI from `SPOTIFY_REDIRECT_URI` still stands.
- **Status:** done

### S-02: Board shows the idle display, unattended, across a cold boot

- **Outcome:** Owner can cut power to the Pi and have the board come back by itself showing the
  considered idle display when nothing is playing, with no monitor, keyboard, login or manual
  start.
- **Change ID:** `unattended-idle-board`
- **PRD refs:** US-03, US-05, FR-011, FR-012, FR-014, NFR-02
- **Prerequisites:** F-02, S-01
- **Parallel with:** --
- **Blockers:** --
- **Unknowns:**
  - ~~Is the idle display legible and distinguishable at 32x32 from normal desk viewing distance?~~
    -- RESOLVED 2026-11-08: yes. A dim warm-white eighth note centered on black
    (`IDLE_MAX_CHANNEL = 64`) was judged on the real board from desk distance as deliberate and
    "on", and clearly distinct from the temporary playing placeholder. See
    `context/archive/2026-11-08-unattended-idle-board/verification.md`. Owner. Block: no -- the PRD states
    this is decided by looking at the real board rather than settled on paper, so it resolves by
    delivering this slice.
- **Risk:** The north star, and the slice that starts the clock on NFR-02. The 7-day unattended
  property can only be observed by leaving a real build running on the Pi, so until this lands
  that property cannot begin to be proven -- which is why `main_goal: quality` places it ahead of
  all artwork work. The specific thing to verify is a genuine cold boot, not a `systemctl restart`:
  `infrastructure.md`'s pre-mortem turns on the fact that every test restarted a unit on a machine
  whose Bluetooth stack had been up for days. Treat adapter readiness as an application concern
  with in-process retry, because `After=bluetooth.target` orders the unit without guaranteeing a
  powered adapter.
- **Status:** done

### S-03: The board link comes back on its own after a drop

- **Outcome:** Owner can switch the board off and on, or carry it out of range and back, and the
  service re-establishes the Bluetooth link and resumes the correct display without the owner
  touching the board or the Pi.
- **Change ID:** `bluetooth-link-self-recovery`
- **PRD refs:** US-05, FR-013
- **Prerequisites:** S-02
- **Parallel with:** S-04
- **Blockers:** --
- **Unknowns:**
  - Reconnect restores the link but nothing re-pushes a frame. Observed on hardware during F-01,
    2026-11-04: the board was switched off for ~30 seconds, the library reconnected on its own
    after its 5-second initial delay, the 30-second heartbeat resumed, and the panel went on
    showing the frame it already had, unmarked, for at least two minutes on a healthy link. F-01
    pushes exactly one frame by design, so this is not a defect there, but it is the concrete
    reason this item's outcome says "resumes the correct display" rather than just "re-establishes
    the link". Note the FR-018 edge: the PRD's non-goal carve-out covers the window while the board
    is *unreachable*, and does not cover a recovered link still presenting an unvouched frame. The
    library exposes `ConnectionListener(on_connected=...)`, which already fires on reconnect, so
    the hook to re-push from exists. Addressed 2026-11-08 in S-02 (`unattended-idle-board`), on
    the owner's decision: the display loop redraws whenever the link comes up again, and the
    library's reconnect after a drop now uses a fresh Bluetooth client. Both were verified with a
    board power-cycle only; this slice still owns the out-of-range test and the FR-013 sign-off.
    See `context/archive/2026-11-08-unattended-idle-board/verification.md`.
- **Risk:** Separated from S-02 because it is its own success criterion ("the Bluetooth link to the
  board recovers on its own after a drop") and its own failure surface, and because it is testable
  only by physically interfering with the board. The PRD accepts this as unbounded -- no limit on
  duration or attempts, and no defined display while disconnected -- so the real risk is that the
  retry loop crashes the process instead of staying alive: `infrastructure.md` requires the BLE loop
  to be supervised as a task whose failure is caught and reported as link-down, so one unhandled
  exception in it cannot take down the page that would explain it.
- **Status:** done

### S-04: Artwork of the playing item appears on the board

- **Outcome:** Owner can see the artwork of whatever is playing rendered for the 32x32 board, and
  see it replaced when the playing item changes.
- **Change ID:** `artwork-on-the-board`
- **PRD refs:** US-01, FR-006, FR-007, FR-008, FR-016, NFR-01
- **Prerequisites:** S-02
- **Parallel with:** S-03
- **Blockers:** --
- **Unknowns:**
  - What counts as a good 32x32 rendering -- a faithful downscale or a stylized / dominant-colour
    treatment? Owner. Block: no -- the PRD states this is not answerable on paper and prescribes
    the resolution path: render a fixed set of real covers both ways on the actual board, judge
    them from normal desk viewing distance, and write the chosen treatment and a falsifiable
    acceptance bar back into FR-007. Until then FR-007 cannot be failed.
  - Track artwork or album artwork? Owner. Block: no -- FR-017's cache is keyed on the artwork's
    own identity specifically so this does not need answering first.
- **Risk:** This is the slice that makes the product worth looking at, and the first one whose
  acceptance bar is genuinely undefined, which is why it carries two non-blocking unknowns rather
  than pretending to a spec it does not have. NFR-01 (the 5-second budget from item change to
  correct board state) is verified here. The PRD also accepts two known costs rather than treating
  them as defects: rapid skipping may thrash the link with no settle window, and a same-album track
  change re-pushes identical pixels -- the second is what S-05 addresses. Added 2026-11-08 from
  the S-02 review: the display rule (`display.py`) ignores read failures, so while reads fail the
  board keeps whatever presentation it last chose. With S-02's placeholder that is harmless; once
  this slice puts real covers behind the same rule it becomes an unmarked stale cover, which the
  hard rules forbid. So S-04 must either ship with S-06's marker or not ship covers before S-06.
- **Status:** in-progress

### S-05: Every item type is covered, and repeated artwork is not re-rendered

- **Outcome:** Owner can see artwork for any playing item type including podcast episodes, sees the
  generic symbol only for items with genuinely no artwork at all, and never waits for the same
  artwork to be downloaded and rendered twice -- including across restarts and reboots.
- **Change ID:** `all-item-types-and-frame-reuse`
- **PRD refs:** US-01, FR-010, FR-017
- **Prerequisites:** S-04
- **Parallel with:** S-06
- **Blockers:** --
- **Unknowns:**
  - Is the generic no-artwork symbol legible and distinguishable at 32x32? Owner. Block: no --
    same resolution path as the other legibility questions: decided by looking at the real board.
- **Risk:** Split out from S-04 because its risk is independent: the cache is the one mechanism that
  lets the board display something it did not just fetch, so a cached frame can outlive the artwork
  it represents. The PRD raised that and accepted it. Two cache properties are already decided and
  must survive into the plan rather than being re-litigated: only the rendered 32x32 frame is stored
  (not the original), and entries are unbounded and persist across reboots -- which matters because
  FR-014 makes reboots a normal event. The accepted consequence, stated when the choice was made, is
  that changing the rendering pipeline later means discarding the whole cache and re-downloading
  every image.
- **Status:** proposed

### S-06: The staleness marker completes the display rule

- **Outcome:** Owner can tell at a glance that the board can no longer vouch for the cover it is
  showing, because after a 30-second grace period a persistent corner marker appears on it -- for
  any cause, whether network failure, API errors or rate limiting, or refused authorization.
- **Change ID:** `staleness-marker-and-display-rule`
- **PRD refs:** US-02, US-04, FR-004, FR-009, FR-018
- **Prerequisites:** S-04
- **Parallel with:** S-05
- **Blockers:** --
- **Unknowns:**
  - What does the board show when the service is blind and has no cover to fall back on? Owner.
    Block: **yes**. On a cold start, or when playback had already stopped before the connection
    dropped, there is nothing to keep and mark. The generic no-artwork symbol, the idle display and
    a dedicated state are all plausible and all mean different things. The PRD states this must be
    resolved before FR-009's rule is complete, and FR-009's rule is what this slice delivers.
  - Can a few corner pixels actually carry the signal at 32x32 from across a room? Owner. Block:
    no -- resolved by observation on the real board, like the other legibility questions. This is
    the sharpest of them, since the marker is the whole signal that a cover cannot be trusted.
- **Risk:** This slice is where the product's hardest guardrail is either honoured or broken, which
  is why `main_goal: quality` does not allow it to be cut or deferred past S-07. It is also the
  slice the PRD is most honest about: within the 30-second grace period the board still shows the
  wrong album with no marker at all, which time-boxes the wrong-but-confident failure rather than
  removing it, and that was accepted explicitly. Note the stated hole in the guarantee: if the
  Bluetooth link is what failed, no marker can be pushed at all and whatever the board last
  received simply stays up. That is recorded in Non-Goals, not treated as a defect, and S-07 is
  where it becomes visible instead.
- **Status:** blocked

### S-07: One page reports what the service believes, and re-authorizes Spotify

- **Outcome:** Owner can open one page from any device they are logged in on, read the item the
  service believes is playing or that nothing is, see whether the board link is alive, see which
  cause is behind a failure to read the account, and re-authorize Spotify -- with no restart and no
  physical access to the Pi.
- **Change ID:** `status-and-reauthorization-page`
- **PRD refs:** US-02, US-06, FR-004, FR-005, FR-015
- **Prerequisites:** S-01, F-03, S-06
- **Parallel with:** --
- **Blockers:** --
- **Unknowns:** --
- **Risk:** Sequenced last because it reports on states that must exist before they can be
  reported, and it depends on S-06 for the one thing only this page does: naming *which* cause the
  board's single marker deliberately refuses to distinguish. It is not low-stakes, though -- it is
  the only surface that can tell the owner the board link is down, which is precisely the case the
  board itself cannot signal. Two constraints carry forward: the page must never expose the Spotify
  refresh credential, and `infrastructure.md` requires the status route to stay independent of the
  board task, so a crash in the BLE loop is reported as link-down rather than killing the server and
  taking the explanation with it. The riskiest part of this slice -- OAuth completing over the
  Cloudflare edge -- is already proven in S-01, which is deliberate.
- **Status:** proposed

### S-08: AI-enhanced covers (post-v1, M-2)

- **Outcome:** Owner can see the playing item's cover regenerated by an AI step into an image
  made for the 32x32 panel, rather than a downscale of the original, and see it replace the
  plain rendering when the item changes.
- **Change ID:** `ai-enhanced-covers`
- **PRD refs:** none in v1. AI-enhanced covers are a PRD v1 Non-Goal, recorded as intended
  afterwards (`shape-notes.md` `## Forward: technical-roadmap`). Planning this slice needs a PRD
  revision that turns it into a requirement first.
- **Prerequisites:** S-04 (the `render_cover` seam it replaces), S-05 (the frame cache, which is
  what makes an expensive render acceptable on every repeat).
- **Parallel with:** --
- **Blockers:** M-1 must close first; the hard rules forbid AI-enhanced covers in v1.
- **Unknowns:**
  - Where the model runs, and at what cost: on the Pi, or a hosted image API? Owner. Block: yes
    for planning. The cover leaves the device in the hosted case. Covers are public, so the
    Spotify refresh credential is the only thing that must never leave the Pi, and it would not.
  - How it fits NFR-01's 5 s budget: a generation call is likely slower. One option is to show
    the plain rendering first and swap in the enhanced frame when it is ready. Owner. Block: no.
  - What "better" means at 32x32, and its falsifiable bar. Same resolution path as FR-007: judge
    real covers on the real board against the S-04 plain downscale. Owner. Block: no.
  - The staleness rule has to cover an enhanced frame too: FR-018's marker applies to any cover
    presentation, however it was rendered.
- **Risk:** Recorded 2026-11-09 from the S-04 bake-off: the owner kept the plain area downscale
  for now, but judged none of the three good enough on its own, and wants AI enhancement.
  The accepted FR-017 consequence lands here: only rendered frames are cached, not originals, so
  introducing this step discards the whole cache and re-downloads every image. Keep the step
  behind `render_cover` (S-04), and make the cache key carry the renderer's identity (S-05) if
  both renderings must coexist.
- **Status:** deferred

## Backlog Handoff

| Roadmap ID | Change ID                                   | Suggested issue title                                                             | Ready for `/10x-plan` | Notes                                                                       |
| ---------- | ------------------------------------------- | --------------------------------------------------------------------------------- | --------------------- | --------------------------------------------------------------------------- |
| F-01       | `app-skeleton-and-board-transport`          | Replace the uv placeholder with a service package and a swappable board transport | yes                   | Run `/10x-plan app-skeleton-and-board-transport`                            |
| F-02       | `pm2-supervised-service`                    | Supervise the service under pm2 with rotated logs that survive a reboot           | no                    | Needs F-01                                                                  |
| F-03       | `edge-surface-and-spotify-app-registration` | Publish and gate the service hostname, and register the Spotify redirect URI      | yes                   | Run `/10x-plan edge-surface-and-spotify-app-registration`. Human-only steps |
| S-01       | `spotify-integration`                       | Authorize Spotify once and read currently-playing unattended                      | no                    | Needs F-01, F-03                                                            |
| S-02       | `unattended-idle-board`                     | Show the idle display unattended, surviving a cold boot                           | no                    | North star. Needs F-02, S-01                                                |
| S-03       | `bluetooth-link-self-recovery`              | Re-establish the board link after a drop with no human action                     | no                    | Needs S-02                                                                  |
| S-04       | `artwork-on-the-board`                      | Render and display the playing item's artwork at 32x32                            | no                    | Needs S-02. Resolves the rendering-treatment and track-vs-album questions   |
| S-05       | `all-item-types-and-frame-reuse`            | Cover every item type and reuse rendered frames across reboots                    | no                    | Needs S-04                                                                  |
| S-06       | `staleness-marker-and-display-rule`         | Add the staleness marker and complete the display-state rule                      | no                    | Needs S-04 **and** Open Question 2 resolved                                 |
| S-07       | `status-and-reauthorization-page`           | Serve the status page and the Spotify re-authorization action                     | no                    | Needs S-01, F-03, S-06                                                      |
| S-08       | `ai-enhanced-covers`                        | Regenerate the playing cover with an AI step for the 32x32 panel                  | no                    | Post-v1 (M-2). Needs M-1 closed, S-04, S-05, and a PRD revision             |

## Open Roadmap Questions

1. **Confirm the project name.** -- Owner. Block: none. `pyproject.toml` now reads
   `spotify-to-led`, which resolves the mismatch the bootstrap log recorded (uv had named it
   `20-led-board` after the directory). Confirm `spotify-to-led` is intended, or replace it.
2. **What shows when the service is blind and has no cover to fall back on?** -- Owner. Block:
   `S-06`. The single blocking question in this roadmap, and the one to resolve first if you want
   the display rule complete. The PRD states it must be answered before FR-009's rule is complete.
3. **What counts as a good 32x32 rendering, and what is the falsifiable acceptance bar?** --
   Owner. Block: none, but it gates `S-04`'s acceptance criteria. Resolved by rendering real covers
   both ways on the real board, then writing the chosen treatment back into FR-007.
4. **Can the three non-cover visual elements be told apart at 32x32 from across a room?** --
   Owner. Block: none, but it gates the acceptance criteria of `S-02` (idle display), `S-05`
   (generic no-artwork symbol) and `S-06` (staleness marker). Decided by looking at the real board,
   not on paper.
5. **Track artwork or album artwork?** -- Owner. Block: none; gates `S-04`. FR-017's cache shape
   was chosen so this does not need answering first.
6. **Which `idotmatrix` implementation gets pinned?** -- RESOLVED 2026-11-04:
   `idotmatrix-api-client` 0.3.0, pinned exactly. It is the maintained fork, published under its own
   name, declaring `>=3.12,<3.15` and carrying no junk `asyncio` pin. Evidence is hardware, not
   paper: on the Pi's aarch64 under CPython 3.14 it drove a real 32x32 board over BlueZ, showing the
   four-quadrant test pattern with correct orientation and channel order, and recovered the link
   after a board power-cycle. The original concern is kept below for the record.
   PyPI 0.0.9 had no release since 2025-04-13, declared no `requires-python`, carried a junk
   `asyncio` dependency pin, and had never been tested against the Python 3.14 the stack mandates.
   Three forks existed with divergent APIs. This was the roadmap's `top_blocker: external` in
   concrete form, and F-01's transport interface is the mitigation that made it reversible.
7. **Does the Pi have a reserved DHCP lease or a static IP?** -- RESOLVED 2026-11-06 -- no
   reservation is needed: the tunnel connector runs on the Pi itself and the route targets
   `localhost:8000`. The original question is kept below for the record. Owner. Block: none; affects
   `F-03`. Required by the recommended Option A wiring, which pins the public hostname to the Pi's
   LAN address. Not observable from this repository.

## Parked

- **Anyone else's board or account.** Why parked: PRD Non-Goals -- no hosted service, no
  installable release for other iDotMatrix owners, no multi-account support. Structurally forced as
  well as chosen, since Bluetooth requires the service to run next to its own board.
- **Controlling playback.** Why parked: PRD Non-Goals -- strictly read-only against Spotify. No
  play, pause, skip or volume, which also rules out a whole category of OAuth scope.
- **Sources other than Spotify.** Why parked: PRD Non-Goals -- one source only. No Apple Music, no
  local files, no system-audio detection, no scrobbling.
- **AI-enhanced covers.** Why parked: PRD Non-Goals for v1, and recorded in
  `shape-notes.md` `## Forward: technical-roadmap` as intended afterwards. Now slotted as `S-08`
  for M-2 (2026-11-09, after the S-04 bake-off). FR-017's cache exists
  partly to prepare for it, because once rendering is expensive, re-deriving the same frame on
  every track change stops being acceptable.
- **Animated covers.** Why parked: PRD Non-Goals for v1, intended later per the same forward note.
  The v1 board shows one static frame per item.
- **A flicker-freedom guarantee during rapid skipping.** Why parked: offered as a requirement and
  explicitly declined. Remains a known risk rather than a commitment.
- **A bounded recovery time after reboot.** Why parked: offered and declined. The service resumes
  without human action (FR-014), but how quickly is not committed to.
- **A no-torn-frames guarantee.** Why parked: offered and declined. A partially drawn image may be
  briefly visible.
- **A staleness guarantee while the board itself is unreachable.** Why parked: a stated hole in the
  guarantee rather than a defect. A marker can only be shown by pushing to the board, so if the
  link is what failed, the last image stays up unmarked. `S-07` is where this becomes visible.

Recorded so it is not mistaken for parked: **animation, visualizers and scrolling text** were
offered as a non-goal and *declined*. Nothing in the v1 requirement set needs them, but they are
not ruled out for later.

## Milestone History

(Empty -- M-1 is the first milestone. Append-only; closure entries are written when a milestone
closes.)

## Done

(Empty on first generation. `/10x-archive` appends here and flips the matching item's `Status` to
`done` when a change is archived. Do not pre-populate.)

- **F-01: (foundation) a real `spotify_to_led` service package replaces the uv placeholder and can
  push a single 32x32 frame to the board through one transport interface that has both a real
  Bluetooth implementation and a stub that writes the frame to a PNG** -- Archived 2026-11-04 ->
  `context/archive/2026-11-03-app-skeleton-and-board-transport/`. Lesson: --.
- **F-03: (foundation) the project's subdomain resolves to the Pi through the connector that
  already runs on this network, is gated by its own Cloudflare Access application scoped to the
  owner, and is registered with Spotify as an exact-match redirect URI** -- Archived 2026-11-06 ->
  `context/archive/2026-11-05-edge-surface-and-spotify-app-registration/`. Lesson: --.
- **F-02: (foundation) the service is supervised by the pm2 daemon that already runs
  `cloudflared` and n8n on the Pi, comes back after a cold boot with no human action, is never
  marked `errored` and abandoned by pm2's restart limit, and writes rotated logs to disk that
  persist across reboots** -- Archived 2026-11-07 ->
  `context/archive/2026-11-06-pm2-supervised-service/`. Lesson: --.
- **S-01: Owner can authorize the Spotify account once from a browser on any device, after which
  the service keeps determining what is playing with no further human action for as long as the
  stored credential stays valid** -- Archived 2026-11-08 ->
  `context/archive/2026-11-08-spotify-integration/`. Lesson: --.
- **S-02: Owner can cut power to the Pi and have the board come back by itself showing the
  considered idle display when nothing is playing, with no monitor, keyboard, login or manual
  start** -- Archived 2026-11-08 ->
  `context/archive/2026-11-08-unattended-idle-board/`. Lesson: --.
- **S-03: Owner can switch the board off and on, or carry it out of range and back, and the
  service re-establishes the Bluetooth link and resumes the correct display without the owner
  touching the board or the Pi** -- Archived 2026-11-09 ->
  `context/archive/2026-11-08-bluetooth-link-self-recovery/`. Lesson: --.
