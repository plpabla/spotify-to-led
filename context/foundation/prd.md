---
project: "spotify-to-led"
version: 2
status: draft
created: 2026-11-02
context_type: greenfield
product_type: other # background service that runs continuously; local web page is an accessory to it
target_scale:
  users: small
  qps: low
  data_volume: small
timeline_budget:
  mvp_weeks: 3
  hard_deadline: null
  after_hours_only: true
---

# spotify-to-led

## Vision & Problem Statement

The owner has an iDotMatrix LED pixel board that currently has no job worth doing -- it sits unused. While working at the desk with Spotify playing, there is no physical, ambient indication of the current track; that information exists only on a screen. The cost today is idle hardware: there is no workaround in place, because there is no current use for the board at all.

The insight: the parts exist, the glue does not. Spotify exposes what is currently playing along with cover art, and community libraries already speak the board's protocol. Nobody has bundled them into something that just runs. A second motive, stated explicitly by the owner: "I want to play around with that hardware" -- the build itself is part of the point, not only its output. Because Bluetooth requires physical proximity, this service can never be centralized or hosted -- it must always run next to its own board. That is a permanent constraint on the shape of the product.

## User & Persona

Primary persona: the project owner. A single named user with one Spotify account and one iDotMatrix board. Context: at a desk, music playing through Spotify, board on a shelf or desk within sight. The moment they reach for this product: whenever music is playing and the board is showing nothing related to it.

Single-tenant by choice -- not a hosted service, not a distributable for other board owners (see Non-Goals).

## Success Criteria

### Primary

- The service runs unattended on the Raspberry Pi: it starts as a system service and resumes working after a reboot with no human action.
- When the item playing on the Spotify account changes, the board shows the correct state for it within 5 seconds (see Non-Functional Requirements).
- The Bluetooth link to the board recovers on its own after a drop, without replugging the board or attaching a monitor to the Pi.
- A web page served by the service reports what the service believes is playing and whether the board link is alive, and allows re-authorizing Spotify. Reached over the owner's existing Cloudflare-fronted subdomain rather than by a bare LAN address, for the redirect-URI reason recorded under Open Question 6.

Named internal milestone (not a success criterion -- a checkpoint so there is a provable result in week 1 rather than a cliff at week 3): the artwork of the currently-playing item appears on the board once, driven from the laptop, happy path only.

### Secondary

- None. The single item originally captured here (a considered idle display) was promoted to must-have and is now FR-011. Asked again whether anything should replace it, the owner chose to leave it empty: everything that matters for v1 is must-have.

### Guardrails

- The board must never present a cover as current when the service cannot vouch for it. Keeping the last cover is allowed, and is the chosen behaviour (FR-018), provided it carries a visible staleness marker. Unlabelled staleness is the regression, not staleness itself -- wrong-but-confident is what is forbidden. This guarantee cannot hold while the board itself is unreachable, since nothing can be pushed to it; that gap is recorded in Non-Goals.
- Spotify request rate must stay within the provider's published limits. Being throttled or having the token revoked by over-requesting counts as a failure even if the display otherwise works.
- The Spotify refresh credential never leaves the device it was created on, and the local web page never exposes it.

## User Stories

### US-01: Artwork of the currently-playing item appears on the board

- **Given** the service is running on the Pi with a valid Spotify authorization and a live board link
- **When** the owner starts playing something, or the playing item changes
- **Then** the board shows that item's artwork, rendered for a 32x32 display

#### Acceptance Criteria

- The displayed artwork corresponds to the item Spotify reports as currently playing, not the previous one.
- The change is visible within the latency target recorded in Non-Functional Requirements.
- Podcast episodes are treated like albums -- their artwork is used (FR-010).
- If the item has genuinely no artwork, the board shows the generic symbol instead (FR-010).
- Artwork that has already been rendered once is reused rather than downloaded and rendered again (FR-017).

### US-02: Spotify authorization breaks and the owner is told

- **Given** the service is running and its Spotify authorization has become invalid (revoked, expired, credentials changed)
- **When** the service has been refused for more than the grace period
- **Then** the board keeps the last cover with the staleness marker, and the web page reports that re-authorization is needed

#### Acceptance Criteria

- The board does not clear the cover and does not show a dedicated error glyph -- it keeps what it had and marks it.
- The staleness marker is the same marker used for any other read failure (FR-018); the board does not distinguish the cause.
- The web page names authorization as the cause and offers the re-authorization action (FR-005).
- After re-authorizing from the web page, normal display resumes with no restart and no physical access to the Pi.

### US-03: Nothing is playing

- **Given** the service is running with a valid authorization and a live board link
- **When** playback is stopped, or has never started since the service came up
- **Then** the board shows the idle display rather than the last cover

#### Acceptance Criteria

- The idle display is distinguishable from the generic no-artwork symbol and from a cover carrying the staleness marker. There is no longer an error icon to distinguish it from (FR-004, superseded).
- No previously shown cover remains visible once playback has stopped (stale-cover guardrail).

### US-04: The connection drops while something is playing

- **Given** the service is running with a live board link and a cover showing
- **When** it becomes unable to read playback -- network down, API failing or rate-limiting, or authorization refused -- for more than 30 seconds
- **Then** the board keeps showing that cover and adds the staleness marker in a corner

#### Acceptance Criteria

- Within the 30-second grace period, no marker appears and the cover is shown unchanged.
- The marker is removed as soon as a successful read confirms what is playing.
- The marker does not replace or obscure enough of the cover to make the artwork unreadable.
- If the board itself is what became unreachable, no marker can be pushed and the previous image simply remains -- this limitation is recorded in Non-Goals, not treated as a defect.

### US-05: The Pi reboots and the service comes back on its own

- **Given** the service is installed on the Pi as a system service with a valid Spotify authorization
- **When** the Pi is restarted, or power is cut and restored
- **Then** the service starts again by itself and resumes showing the correct state for the account, with no human action

#### Acceptance Criteria

- Nothing is required of the owner after the reboot: no monitor, no keyboard, no login, no manual start (FR-014).
- The Spotify authorization survives the reboot and is not re-entered (FR-003).
- The Bluetooth link to the board is re-established without the owner touching the board (FR-012, FR-013).
- Artwork rendered before the reboot is reused rather than downloaded and rendered again (FR-017).
- How long the resume takes is deliberately not committed to (see Non-Goals).

### US-06: The owner checks what the service believes

- **Given** the service is running and the owner is on the same local network
- **When** the owner opens the local web page
- **Then** the page reports the item the service believes is playing and whether the board link is alive, and offers re-authorization

#### Acceptance Criteria

- The page names the believed-playing item, or states that nothing is playing, consistent with the presentation currently on the board (Business Logic).
- The page reports whether the board link is alive, including the case where the board is unreachable and therefore cannot report anything itself (Non-Goals).
- When the service cannot read playback, the page names the cause -- network failure, API errors or rate limiting, or failed authorization -- which the board's single marker deliberately does not distinguish (FR-018).
- The page offers the re-authorization action (FR-005) and never exposes the Spotify refresh credential (Guardrails).
- The page is reachable from the local network with no sign-in (Access Control).

## Functional Requirements

Each FR carries a `> Socratic:` blockquote recording the counter-argument considered in the challenge round and how it was resolved.

### Spotify source

- FR-001: Owner can authorize the Spotify account once during setup so the service can read playback. Priority: must-have

  > Socratic: Counter-argument accepted: "headless OAuth is the hidden cost" -- performing a browser redirect flow on a Pi with no browser is the most annoying part of this build, and "once during setup" makes it sound trivial. Resolution: kept, cost acknowledged rather than hidden. The local web page (FR-015) is the redirect surface that makes a headless setup workable, which is part of why that page is load-bearing.

- FR-002: Service can determine what is currently playing on the authorized Spotify account. Priority: must-have

  > Socratic: Counter-argument accepted: the original wording said "poll", which bakes an implementation choice into the PRD before the stack is chosen. Resolution: reworded to the capability ("determine what is currently playing").

- FR-003: Service can maintain its Spotify authorization without human action for as long as the stored credential remains valid. Priority: must-have

  > Socratic: Counter-argument accepted: "an unkeepable promise" -- authorization can be revoked server-side at any time, so an unconditional "stays valid with no human action" cannot be honoured. Resolution: reworded with the validity condition made explicit. Breakage is handled by FR-004 and FR-005 rather than denied here.

- FR-004: Service can keep the last cover on the board with the generic staleness marker when it cannot authenticate to Spotify, and report on the local web page that re-authorization is needed. Priority: must-have

  > REVISED after Phase 7, on the owner's instruction that a lost connection should keep the last cover with a warning rather than clear the board. This SUPERSEDES the original FR-004 ("display a distinct error icon on the board when it cannot authenticate"), which was the owner's own Phase 4 addition. Authorization failure is now one of the three blindness causes treated identically on the board (see FR-018); the web page is the only place that says which cause it is and that action is needed.
  > Socratic: the original counter-arguments were that the ambient object becomes an error console and that a 32x32 board cannot carry three distinguishable non-cover states; both were dismissed at the time. The revision resolves the second one incidentally -- there is no longer a full-screen error icon -- at the cost of the board no longer being able to tell the owner that action is required. That cost was stated when the choice was made and accepted.

- FR-005: Owner can re-authorize the Spotify account from the local web page. Priority: must-have

  > Socratic: Counter-arguments considered: it collides with the no-auth decision (an ungated LAN page can start an OAuth flow), and SSH would be cheaper than building a web flow. Resolution: stands as written.

### Display selection

- FR-006: Service can retrieve the cover art for the currently-playing item. Priority: must-have

  > Socratic: Counter-arguments considered: "the cover" hides an unmade choice between track and album art; and repetition, not retrieval, is the real problem (one album means many identical pushes). Resolution: stands as written.

- FR-007: Service can render a cover onto the board's 32x32 resolution, by faithful downscale or by a stylized or dominant-colour treatment, whichever reads better on a pixel board. Priority: must-have

  > Socratic: Counter-argument accepted: "a faithful downscale may be the wrong goal" -- at 1024 pixels a shrunk photo may read worse than a stylized or colour-field rendering, and the original wording foreclosed that. Resolution: reworded to permit either treatment. The original "so the album remains recognizable" bar was dropped as untestable at this size; the open question about what "good" means at 32x32 is recorded.
  > Decided 2026-11-09 (S-04 bake-off on the real board): the treatment is a **faithful downscale** -- area-average (box) downscale with no further processing (`render_cover` in `src/spotify_to_led/rendering.py`), chosen over a contrast/saturation-boosted downscale and a 16-colour palette quantization. The owner tried the boosted and then the palette treatment as the pick before settling on the plain downscale for now; none of the three is sufficient on its own, and AI enhancement is slotted post-v1 as roadmap `S-08`. Acceptance bar: from normal desk viewing distance, every cover in the fixed bake-off set is distinguishable from every other. Recognising the album is deliberately not required at this size; that is S-08's goal.

- FR-008: Service can display the rendered cover on the board and replace it when the playing item changes. Priority: must-have

  > Socratic: Counter-arguments considered: rapid skipping thrashes the Bluetooth link with no settle window; and a same-album track change re-pushes identical pixels. Resolution: stands as written.

- FR-009: Service can select what the board shows according to a single defined display-state rule covering every case: cover available and vouched for, cover available but unvouched, no artwork available, and nothing playing. Priority: must-have

  > Socratic: Counter-argument accepted: the original FR-009/010/011 were three separate requirements all answering "what shows when there is no cover", and should be one rule with cases. Resolution: merged. FR-009 is now that rule; FR-010, FR-011 and FR-018 define three of its cases concretely. The rule itself is the product's domain logic (see Business Logic).
  > REVISED after Phase 7: the original case list was "cover available, no artwork available, nothing playing, playback state unknown, and authorization failed". The last two no longer produce distinct board output -- both now keep the last cover with a staleness marker (FR-004, FR-018) -- so the board has four presentations while the service still distinguishes more causes internally and reports them on the web page.
  > Decided 2026-11-10 (S-06, Open Question 2): the rule is complete. Staleness is a qualifier on every presentation, not only on a cover: once the service has been unable to read playback for more than 30 seconds, whatever the board shows -- cover, idle, the playing placeholder or the no-artwork symbol -- carries FR-018's marker until a read succeeds. With no reader at all (Spotify not configured) the board shows plain idle, unmarked, since that is a setup mode rather than blindness. Implemented in `select_presentation` (`src/spotify_to_led/display.py`).

- FR-010: Service can use the artwork of any playing item type, including podcast episodes, and falls back to a generic symbol only when no artwork exists at all. Priority: must-have

  > Socratic: Counter-argument accepted: "podcasts do have artwork" -- the earlier decision to treat all non-music items as a generic symbol discards images the API already returns. Resolution: revised. This SUPERSEDES the Phase 4 decision "non-music items out of scope, show a generic symbol". The generic symbol is now reserved for items with genuinely no artwork (for example ads).

- FR-011: Service can show a considered idle display when nothing is playing. Priority: must-have

  > Socratic: Counter-argument accepted: "idle is the common state, so it must be must-have" -- music plays a few hours a day, so for most of its life this is an idle object, and leaving its dominant state optional means the thing on the shelf is undefined most of the time. Resolution: PROMOTED from nice-to-have to must-have. It therefore also moves out of Success Criteria's Secondary list.

- FR-017: Service can reuse a previously rendered board image for the same artwork, keyed on the artwork's own identity, instead of downloading and rendering it again. Priority: must-have

  > Implemented 2026-11-10 (S-05): the key is the image id from the cover URL (`https://i.scdn.co/image/<id>`), and an entry is `var/covers/<board size>/<id>.png`, so a board-size change never serves a frame drawn for another size. A URL without such an id is shown but not cached. Variants of one image (for example an AI-enhanced frame) are meant to take a suffix after the id.
  > Cache shape: keyed on the artwork's own identity (its URL or image id), so one entry serves every track on an album and the track-art-versus-album-art question in Open Question 5 does not need answering first. Only the rendered 32x32 frame is stored, not the original download. Entries are unbounded and persist across restarts and reboots, which matters because FR-014 makes reboots a normal event rather than an exception. Accepted consequence: because originals are not kept, changing the rendering pipeline means discarding the whole cache and re-downloading every image. This was stated when the choice was made.
  > Socratic: Counter-arguments considered: premature at this scale (one cover is roughly 50KB and a rescale is microseconds against a 5-second latency target); it is a v2 requirement wearing v1 clothes, since its stated justification is future work that is not scoped; and it creates a new way to be stale, because a cached image can outlive the artwork it represents and the stale-cover guardrail is harder to honour once the service can display something it did not just fetch. Resolution: stands as written, must-have. This FR is also the owner acting on the FR-006 counter-argument they had dismissed -- that repetition, not retrieval, is the real problem.

- FR-018: Service can display a persistent marker in a corner of the last shown cover once it has been unable to read playback for more than 30 seconds, for any cause -- network failure, API errors or rate limiting, or failed authorization. Priority: must-have

  > Marker shape: a small persistent corner marker, chosen over a border (which would cost the outer pixel ring on an already-scarce 32x32 panel), over alternating cover and glyph (which is motion, and animation is a v1 non-goal), and over dimming (which reads as night mode rather than staleness). One marker serves all three causes; the web page is where the cause is named.
  > Grace period: 30 seconds, roughly six missed reads at the 5-second cadence implied by the latency NFR. Chosen over 10 and 15 seconds as more tolerant of transient home-network noise.
  > Socratic: Counter-arguments considered: the board still lies for the first 30 seconds, since a track change during the grace period shows the wrong album with no marker at all, which time-boxes the wrong-but-confident failure rather than removing it; a few corner pixels are invisible during the ambient desk glance that is the actual viewing situation; and one marker for three causes discards the distinction that matters, since a blip, a rate-limit and a dead login demand different responses and only the web page separates them. Resolution: stands as written, must-have.
  > Decided 2026-11-10 (S-06, Open Question 2): the marker is no longer confined to "the last shown cover". The same marker, at the same full brightness and after the same 30-second grace, goes on whatever the board shows, including idle, the playing placeholder and the no-artwork symbol; it clears every one of those glyphs at every supported board size. Plain idle with Spotify not configured is never stale.

- FR-019: Owner can switch on, from the local web page, an AI step that redraws each playing cover as a frame designed for the board, and switch it off again; the choice survives restarts. Priority: should-have

  > Added 2026-11-11 (PRD v2, roadmap S-08, M-2). The step sends the cover to a hosted image model through OpenRouter (`black-forest-labs/flux-3-image-klein` by default, about $0.009 per image) with a prompt asking for one recognisable subject as bold pixel art on black, and shrinks the answer to the board with a LANCZOS downscale. Switched off, the service makes no OpenRouter request at all. Each image is generated at most once: the enhanced frame is kept beside the plain one (FR-017), so a repeat, including after a restart, needs no request.
  > While a never-seen cover generates, the board shows the playing placeholder, then the enhanced frame; a generation that fails falls back to the plain cover, and a refusal for credit or key (the owner's key carries a daily limit) pauses all generation until the next 00:00 UTC. Switching the step either way redraws the current item. FR-018's marker applies to an enhanced cover exactly as to a plain one.
  > Acceptance bar (board bake-off, 2026-11-11, see `context/changes/ai-enhanced-covers/verification.md`): from normal desk viewing distance, every enhanced cover in the bake-off set reads better than its plain downscale. Text on the cover is acceptable when it is the cover's main subject.
  > The cover leaves the Pi in this step. Covers are public images; the Spotify refresh credential still never leaves the device.

### Board link

- FR-012: Service can connect to the board over Bluetooth. Priority: must-have

  > Socratic: Counter-arguments considered: it restates what the board library already does; and "connect" skips the real work, which is identifying and binding to one specific board and persisting that choice. Resolution: stands as written.

- FR-013: Service can re-establish the Bluetooth link after a drop with no human action. Priority: must-have

  > Socratic: Counter-arguments considered: unbounded recovery is untestable (no limit on duration or attempts, no defined display meanwhile); and recovery is sometimes impossible when the board is off or out of range. Resolution: stands as written.

### Operation

- FR-014: Owner can run the service unattended on the Raspberry Pi so it resumes after a reboot with no human action. Priority: must-have

  > Socratic: Counter-arguments considered: it is deployment rather than product; and it is the cheapest item on a list of hard ones, which flatters the scope. Resolution: stands as written -- unattended operation is the primary success criterion, so it belongs here.

- FR-015: Owner can use a local web page that reports the believed-playing item and board link health, and from which Spotify can be re-authorized. Priority: must-have

  > Socratic: Counter-argument accepted: "it is no longer a status page" -- FR-005 put the re-auth action on it, making it load-bearing infrastructure rather than an optional read-only view. Resolution: reworded from "view" to reflect that it carries an action and cannot be cut from v1 without also cutting FR-005.

- FR-016: Owner can run the service on a laptop for development and testing, with parity guaranteed for the Spotify and rendering layers but not for board transport behaviour. Priority: must-have

  > Socratic: Counter-argument accepted: "parity breaks exactly where it matters" -- Bluetooth stacks differ between a laptop and a Pi, so "the same service on both" holds for everything except the layer most likely to fail. Resolution: reworded to scope the parity claim honestly rather than drop the requirement.

## Non-Functional Requirements

- Within 5 seconds of the playing item changing on the account, the board shows the state that corresponds to the new item.
  With FR-019 switched on, the playing placeholder is that state while a never-seen cover generates (about 9 s on the Pi, measured 2026-11-11); the enhanced frame follows when it is ready.
- The service operates for at least 7 consecutive days with no human intervention and no degradation in the above timing.

Verification consequence of the second property: seven consecutive days can only be observed by leaving a real build running on the Pi for a week. Against the three-week budget in the frontmatter, that means the Pi has to be running something real well before the final week, or the property goes unproven. This is a sequencing constraint for the roadmap step, not an additional requirement.

Two further outside-observable properties are recorded under Success Criteria as guardrails rather than repeated here: Spotify request rate stays within published limits, and the Spotify refresh credential leaves no trace off the device it was created on.

The following were offered and explicitly not selected, so they are not requirements: absence of visible flicker during rapid skipping, a bounded recovery time after reboot, and a no-torn-frames guarantee. Each remains a known risk rather than a commitment -- flicker in particular is the same concern raised and dismissed in the FR-008 challenge.

## Business Logic

From what the Spotify account reports, the service decides which one of a defined set of board presentations to show -- a cover it can vouch for, that same cover carrying a staleness marker when it cannot, a generic symbol, or the idle display -- and renders any available artwork for a 32x32 display.

The inputs the rule consumes are all user-facing: what the account says is playing (its identity and its type), whether playback is actually active, whether the account can be read at all right now, and the artwork associated with the playing item if any exists. The output is exactly one of the four presentations, plus -- when artwork is available -- a rendered image sized for the board.

The owner encounters the rule by looking at the board: every moment of the day it is asserting one of those four presentations, and the rule is what picks which. The web page reports the same decision in words, so the owner can tell whether a blank board means "nothing is playing" or "cannot read the account right now". This is why the rule is the product rather than a detail of it: the hardware only ever shows one image, so choosing the right one is the whole job.

Revision recorded in the shape notes after the rule was first written: blindness (network, API errors, authorization) is no longer a state of its own but a qualifier on the cover state. After a 30-second grace period the board keeps the last cover and asserts "this is what was playing, and I can no longer confirm it". The causes therefore collapse into four board presentations; the service still distinguishes why it is blind -- nothing playing, network failure, API errors or rate limiting, failed authorization -- but only the web page names the cause. What to show when the service is blind and has no cover to fall back on was resolved on 2026-11-10: the marker goes on whatever the board shows (Open Question 2, FR-009).

## Access Control

Single user; no auth **in the application**. The service runs on the owner's own hardware (Raspberry Pi in production, laptop in dev). Flat user model -- no roles, no sign-up, no application login, no gated routes in the service's own code, and the service never sees or stores a user credential of its own.

The MVP does include a small local web page: a status surface showing what the service believes is currently playing and whether the board link is alive, from which Spotify can be re-authorized (FR-015). That page is published on a subdomain of a domain the owner already owns, through the Cloudflare Tunnel connector already running on this network, and gated at the Cloudflare edge by a Cloudflare Access policy scoped to the owner's email. The page is therefore reachable from any device the owner is logged in on, including off the home network, and from no other device.

Narrowed from "no auth, that page is ungated" after research resolved Open Question 6. Spotify no longer permits a plain LAN HTTP address as a registered redirect URI, so the re-authorization surface has to be published over HTTPS on a real hostname; publishing it makes it internet-reachable, and an edge gate is what keeps that within the risk the owner accepted. The original intent survives intact -- no authentication is built into this product -- but the claim "no sign-in" does not, because there is one, at the edge rather than in the service. Unlike the n8n service sharing this domain, this page has no login of its own, so the Access policy is its only gate.

The paragraph below is unchanged by this narrowing: the real gate on the product's data was always Spotify account authorization rather than app login, and still is.

The real gate in this product is Spotify account authorization, not app login. At product level: the owner authorizes the Spotify account once during setup. The service stays authorized without further human action. The product explicitly acknowledges that this authorization can break (revoked, expired, changed password) and must surface a visible failure state when it does, after which the owner re-authorizes manually. Per FR-004 and FR-018, that failure state is the staleness marker on the board and the named cause on the web page.

## Existing Infrastructure

The following already runs on the owner's network, serving an existing n8n deployment, and this project consumes it rather than building it. It is a **dependency and an assumption, not a non-goal** -- "out of scope" would wrongly imply nobody has to do anything:

- The owner's domain, on Cloudflare DNS.
- A Cloudflare Tunnel connector, already running and already surviving reboots.
- A Cloudflare Access configuration with a working login method, already fronting n8n.

Consequence for this product: it assumes a publicly valid HTTPS hostname can be pointed at a service on this network without this project installing, configuring or supervising a tunnel, and without any inbound port being opened. If that assumption ever stops holding, the re-authorization surface is what breaks; the board display does not depend on it, since the service only makes outbound calls to Spotify.

What this project **does** have to do, because none of it is inherited from the n8n setup:

- Add a public hostname route for this project's subdomain on the existing connector, pointing at the Pi.
- Add a Cloudflare Access application for that hostname. Access applications are per-hostname, so the policy protecting n8n does not extend to this one. This page has no login of its own, unlike n8n, so that policy is its only gate.
- Reserve a DHCP lease or assign a static IP for the Pi, since the hostname route is pinned to its LAN address. Not needed, 2026-11-06: the tunnel connector runs on the Pi itself, so the route targets `localhost` and the Pi's LAN address is irrelevant to it.
- Register `https://<sub>.<owner-domain>/callback` as a redirect URI in the Spotify app settings.

These four belong in the setup steps for the roadmap and plan steps. They are the deltas between "the domain is already behind Cloudflare" and "this project's page is reachable and gated".

## Non-Goals

Functional non-goals:

- **Anyone else's board or account.** No hosted service, no installable release for other iDotMatrix owners, no multi-account support. Single-tenant by choice -- and structurally forced: Bluetooth requires the service to run physically next to its own board, so a centralized version cannot exist.
- **Controlling playback.** The product is strictly read-only against the Spotify account. No play, pause, skip or volume from the board or the web page. This also rules out a whole category of authorization scope.
- **Sources other than Spotify.** No Apple Music, no local files, no system-audio detection, no scrobbling services. One source only.
- **Animated covers.** The owner intends them later, and FR-017 exists partly to prepare for them. The board shows one static frame per item, derived from the artwork Spotify returns.
  Narrowed 2026-11-11 (PRD v2): this Non-Goal read "AI-enhanced and animated covers, in v1". AI-enhanced covers are now FR-019, off unless the owner switches them on; animation stays out.

Non-functional non-goals:

- **No flicker-freedom guarantee.** Rapid skipping may produce visible churn on the board. Offered as a requirement and declined.
- **No bounded recovery time after reboot.** The service resumes without human action (FR-014), but how quickly is not committed to.
- **No no-torn-frames guarantee.** A partially drawn image may be briefly visible.
- **No staleness guarantee while the board is unreachable.** FR-018 marks a cover the service cannot vouch for, but it can only do that by pushing to the board. If the Bluetooth link is what failed, whatever the board last received stays up with no marker and there is nothing the service can do about it. The web page still reports the link as down (FR-015). Recorded as a stated hole in the guarantee rather than a defect.

Explicitly NOT a non-goal, recorded so it is not mistaken for one:

- **Animation, visualizers and scrolling text** were offered as a non-goal and declined. Nothing in the v1 FR set needs them -- the display is one static frame at a time -- but they are not ruled out for later. Animated covers are the separate functional non-goal above; AI-enhanced covers are FR-019.

## Open Questions

1. **Project name confirmation.** -- Owner. The shape notes' quality cross-check and Open Question 1 say the name was never set, but the frontmatter carries `project: spotify-to-led`. That value was used. Confirm it is the intended name or replace it.
2. **What shows when the service is blind and has no cover to fall back on?** -- RESOLVED 2026-11-10 -- see FR-009 and FR-018: whatever the board would otherwise show -- idle, the playing placeholder or the no-artwork symbol -- carries the same staleness marker under the same 30-second rule as a cover; no dedicated state. With no reader (Spotify not configured) the board stays plain idle. The original question is kept below for the record. Owner. FR-018 keeps the last cover and marks it, but on a cold start -- or when playback was already stopped before the connection dropped -- there is no cover to keep. The generic no-artwork symbol, the idle display and a dedicated state are all plausible and all mean different things. Needs resolving before FR-009's rule is complete.
3. **What counts as a good 32x32 rendering?** -- RESOLVED 2026-11-09 -- see FR-007: a board bake-off chose the faithful (area) downscale, and FR-007 now carries a falsifiable bar. The original question is kept below for the record. Owner. The Socratic round dropped "so the album remains recognizable" from FR-007 as untestable at 1024 pixels, and permitted either a faithful downscale or a stylized/dominant-colour treatment. No acceptance bar has replaced it, so FR-007 currently cannot be failed. Not answerable on paper -- it is an observation about a physical board. Resolution path: during the named internal milestone, render a fixed set of real covers both ways on the actual board, judge them from the normal desk viewing distance, and write the chosen treatment and a falsifiable acceptance bar back into FR-007.
4. **Can the display states be told apart at 32x32 from across a room?** -- Owner. Three visual elements must each be instantly readable: the generic no-artwork symbol (FR-010), the idle display (FR-011), and the staleness marker (FR-018), the last of which must coexist with artwork rather than replace it. The counter-argument was raised three times across the challenge rounds and dismissed each time; the constraint is still real. The marker case is the sharpest, since a few corner pixels are the whole signal that a cover cannot be trusted. Same resolution path as question 3: decided by looking at the real board during the internal milestone, not settled in this document. Until then FR-010, FR-011 and FR-018 name the three elements without committing to their legibility. Marker part settled 2026-11-11 (S-06): during two real outages on the Pi the owner read the marker from across the room over a cover in a lit room, and over idle in a lit room and a dark one (`context/changes/staleness-marker-and-display-rule/verification.md`).
5. **Track artwork or album artwork?** -- RESOLVED 2026-11-09 -- moot: in the Spotify Web API a track has no images of its own, only its album's (`album.images`), so the cover is always the album art; episodes carry their own images. The original question is kept below for the record. Owner. FR-006 says "the cover art for the currently-playing item", which differs between singles, compilations and remasters. The FR stands as written, but the choice underneath it has not been made.
6. **RESOLVED 2026-11-03 -- Does Spotify's redirect-URI policy allow the local web page to be the re-authorization surface?** No, not as a plain LAN HTTP address, but the requirement survives unchanged. Spotify permits HTTP only for loopback IP literals and requires HTTPS for every other host; `localhost` is banned by name. Enforced for new apps from 2025-04-09 and for all apps from 2025-11-27, both already past. The question's own conditional does **not** follow: FR-005 does not need a different mechanism and FR-015 remains load-bearing, because a redirect URI is an exact-match string that only the browser dereferences -- the page can still initiate the flow and perform the token exchange. **Decision:** publish the page on a subdomain of the owner's existing domain through the Cloudflare Tunnel connector already running on this network, register `https://<sub>.<owner-domain>/callback` with Spotify, and gate the hostname with Cloudflare Access scoped to the owner's email. Consequences: the Access Control section above is narrowed, and the first accepted risk below is replaced. Full evidence, the three rejected alternatives and the wiring trade-offs are in `context/changes/spotify-redirect-uri-policy/research.md`.

Accepted risks (recorded so they are not mistaken for open questions):

- ~~An ungated LAN page can start an OAuth flow (FR-005 + Access Control). Anyone on the home network could re-point the service at their own Spotify account. Raised in the challenge round and dismissed.~~ **Superseded 2026-11-03 by the Open Question 6 decision.** This risk was accepted on LAN-scoped reasoning, and that reasoning does not survive publishing the page on a public hostname -- the exposure would have widened from the home network to anyone who learns the subdomain. It is now mitigated rather than accepted: the Cloudflare Access policy on the hostname means no one but the owner can reach the page at all, which closes the original LAN case as a side effect. What remains accepted in its place is the dependency itself: the page's only gate lives at the Cloudflare edge, so a misconfigured or removed Access policy silently exposes an OAuth-initiating page, and nothing in the service's own code would notice.
- Rapid skipping may thrash the Bluetooth link with no settle window, and a same-album track change re-pushes identical pixels (FR-008). Raised and dismissed.
- Bluetooth recovery is asserted without bound -- no limit on duration or attempts, and no defined display while disconnected (FR-013). Raised and dismissed.
- Headless OAuth on the Pi is the most awkward part of setup (FR-001). Acknowledged rather than solved; the local web page is what makes it workable.
- A cached frame can outlive the artwork it represents, since entries are keyed on artwork identity and never expire (FR-017). Raised in the FR-017 challenge and dismissed.
