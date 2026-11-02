---
project: spotify-to-led
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
  # soft target: done during 2026; recorded as a note, not a hard date
created: 2026-10-29
updated: 2026-10-29
checkpoint:
  current_phase: 8
  phases_completed: [1, 2, 3, 4, 5, 6, 7]
  gray_areas_resolved:
    - topic: "pain category"
      decision: "missing capability + personal delight / ambient object"
    - topic: "primary persona scope"
      decision: "single named user (project owner); one Spotify account, one board"
    - topic: "trigger moment"
      decision: "at the desk with music playing; hardware otherwise unused"
    - topic: "cost of status quo"
      decision: "none -- no workaround exists because the board has no current use; cost is idle hardware"
    - topic: "insight"
      decision: "the parts exist, the glue does not; plus explicit wish to play around with the hardware"
    - topic: "human-facing surface"
      decision: "small local web page on the LAN showing now-playing + board link health; no CLI, not headless"
    - topic: "access gate"
      decision: "N/A -- single user, single device, no auth, flat model"
    - topic: "Spotify authorization"
      decision: "authorize once at setup; manual re-auth if it breaks, with a visible failure state"
    - topic: "v1 boundary"
      decision: "runs unattended on the Pi (idle handling, BLE reconnect, reboot survival) + local status page; laptop happy path recorded as internal milestone 1"
    - topic: "timeline"
      decision: "2-3 weeks after-hours; recorded as mvp_weeks 3 -- within default target, no sustained-effort acknowledgment required"
    - topic: "board resolution"
      decision: "32x32 iDotMatrix"
    - topic: "non-music and missing-artwork items"
      decision: "SUPERSEDED during the Socratic round -- podcast artwork is now used like album artwork (FR-010); the generic symbol is reserved for items with no artwork at all"
    - topic: "auth-failure signalling"
      decision: "SUPERSEDED after Phase 7 -- originally a distinct error icon on the board. Auth failure now shows the generic staleness marker over the last cover, with the cause named only on the web page. Re-auth from the web page still stands (page is not read-only)"
    - topic: "domain rule framing"
      decision: "state selection -- service picks one of five board states from reported playback and renders artwork for 32x32"
    - topic: "latency target"
      decision: "board reflects a change within 5 seconds"
    - topic: "additional NFRs"
      decision: "7 days unattended with no degradation; flicker-freedom, bounded reboot recovery and no-torn-frames offered and declined"
    - topic: "secondary success criteria"
      decision: "intentionally empty -- idle display promoted to must-have, nothing replaces it"
    - topic: "product kind"
      decision: "background service that runs continuously; local web page is an accessory"
    - topic: "scale"
      decision: "small (single user) -- carried from the Phase 1 persona decision, not re-asked"
    - topic: "timing"
      decision: "no hard deadline; soft target within 2026; after-hours only"
    - topic: "100x scale probe"
      decision: "Bluetooth proximity makes a hosted version impossible -- permanent constraint, recorded in Vision"
    - topic: "non-goals"
      decision: "no other boards or accounts; no playback control; no sources besides Spotify. Animation/visualizers offered and declined as a non-goal, so not ruled out"
    - topic: "rendered-image caching (FR-017, added after Phase 7)"
      decision: "keyed on artwork identity; stores only the rendered 32x32 frame; unbounded and persistent across reboots; must-have"
    - topic: "cache vs future rendering changes"
      decision: "originals not kept, so AI-enhanced or animated rendering requires discarding the cache and re-downloading -- accepted knowingly"
    - topic: "AI-enhanced and animated covers"
      decision: "future scope, not v1; recorded under Forward: technical-roadmap and as a v1 non-goal"
    - topic: "behaviour when the service goes blind (FR-018, added after Phase 7)"
      decision: "keep the last cover and add a persistent corner marker after a 30-second grace period; applies to network failure, API errors or rate limiting, and failed authorization"
    - topic: "stale-cover guardrail wording"
      decision: "reworded -- staleness is allowed when labelled; unlabelled staleness is the regression. Not a reversal: the guardrail always forbade wrong-but-confident"
    - topic: "FR-004 supersession"
      decision: "distinct on-board error icon for auth failure REMOVED; auth failure now uses the same generic staleness marker and the web page names the cause. Cost stated and accepted"
    - topic: "marker form"
      decision: "small corner marker, chosen over border (pixel cost), alternating glyph (motion, and animation is a v1 non-goal) and dimming (reads as night mode)"
    - topic: "board unreachable"
      decision: "staleness guarantee explicitly does not hold when the Bluetooth link is down -- nothing can be pushed; recorded as a stated hole in Non-Goals"
  frs_drafted: 18
  quality_check_status: warned
---

# Shape notes

Seed idea, verbatim from the user:

> I want to create an app which will be running on a server and checks periodically
> what is being played on given spotify account to get a cover of that song/album and
> display it on a pixel board which is accessed via bluetooth. app will be running on
> Raspberry Pi or laptop (laptop for dev & testing). Spotify provides API for that
> purpose. For LED board (idot matrix) I see there are libraries so I can utilize them
> (https://github.com/derkalle4/python3-idotmatrix-client or
> https://github.com/markusressel/idotmatrix-api-client)

## Vision & Problem Statement

The owner has an iDotMatrix LED pixel board that currently has no job worth doing -- it
sits unused. While working at the desk with Spotify playing, there is no physical,
ambient indication of the current track; that information exists only on a screen. The
cost today is idle hardware: there is no workaround in place, because there is no
current use for the board at all.

The insight: the parts exist, the glue does not. Spotify's Web API exposes what is
currently playing along with cover art, and community Python libraries already speak the
board's Bluetooth protocol. Nobody has bundled them into something that just runs. A
second motive, stated explicitly by the owner: "I want to play around with that
hardware" -- the build itself is part of the point, not only its output.

Pain category (user-selected): missing capability; personal delight / ambient object.

Insight surfaced by the scale probe in Phase 6: because Bluetooth requires physical
proximity, this service can never be centralized or hosted -- it must always run next to
its own board. A hundred boards would be a hundred independent copies, never one service.
That is a permanent constraint on the shape of the product, not something that only bites
at scale.

## User & Persona

Primary persona: the project owner. A single named user with one Spotify account and one
iDotMatrix board. Context: at a desk, music playing through Spotify, board on a shelf or
desk within sight. The moment they reach for this product: whenever music is playing and
the board is showing nothing related to it.

Single-tenant by choice -- not a hosted service, not a distributable for other board
owners (see Non-Goals once captured).

## Success Criteria

### Primary

- The service runs unattended on the Raspberry Pi: it starts as a system service and
  resumes working after a reboot with no human action.
- When the item playing on the Spotify account changes, the board shows the correct state
  for it within 5 seconds (see Non-Functional Requirements).
- The Bluetooth link to the board recovers on its own after a drop, without replugging
  the board or attaching a monitor to the Pi.
- A local web page on the LAN reports what the service believes is playing and whether
  the board link is alive, and allows re-authorizing Spotify.

Named internal milestone (not a success criterion -- a checkpoint so there is a provable
result in week 1 rather than a cliff at week 3): the artwork of the currently-playing item
appears on the board once, driven from the laptop, happy path only.

### Secondary

- None. The single item originally captured here (a considered idle display) was promoted
  to must-have during the Socratic round and is now FR-011. Asked again in Phase 5 whether
  anything should replace it, the owner chose to leave it empty: everything that matters
  for v1 is must-have.

### Guardrails

- The board must never present a cover as current when the service cannot vouch for it.
  Keeping the last cover is allowed, and is the chosen behaviour (FR-018), provided it
  carries a visible staleness marker. Unlabelled staleness is the regression, not staleness
  itself -- wrong-but-confident is what is forbidden. This guarantee cannot hold while the
  board itself is unreachable, since nothing can be pushed to it; that gap is recorded in
  Non-Goals.
- Spotify request rate must stay within the provider's published limits. Being throttled
  or having the token revoked by over-requesting counts as a failure even if the display
  otherwise works.
- The Spotify refresh credential never leaves the device it was created on, and the local
  web page never exposes it.

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

- The idle display is distinguishable from the error icon and the generic no-artwork symbol.
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

## Functional Requirements

Each FR carries a `> Socratic:` blockquote recording the counter-argument considered in the
challenge round and how it was resolved.

### Spotify source

- FR-001: Owner can authorize the Spotify account once during setup so the service can read playback. Priority: must-have

  > Socratic: Counter-argument accepted: "headless OAuth is the hidden cost" -- performing a
  > browser redirect flow on a Pi with no browser is the most annoying part of this build,
  > and "once during setup" makes it sound trivial. Resolution: kept, cost acknowledged
  > rather than hidden. The local web page (FR-015) is the redirect surface that makes a
  > headless setup workable, which is part of why that page is load-bearing.

- FR-002: Service can determine what is currently playing on the authorized Spotify account. Priority: must-have

  > Socratic: Counter-argument accepted: the original wording said "poll", which bakes an
  > implementation choice into the PRD before the stack is chosen. Resolution: reworded to
  > the capability ("determine what is currently playing"). Polling is noted under
  > `## Forward: tech-stack` as the likely mechanism, not as a requirement.

- FR-003: Service can maintain its Spotify authorization without human action for as long as the stored credential remains valid. Priority: must-have

  > Socratic: Counter-argument accepted: "an unkeepable promise" -- authorization can be
  > revoked server-side at any time, so an unconditional "stays valid with no human action"
  > cannot be honoured. Resolution: reworded with the validity condition made explicit.
  > Breakage is handled by FR-004 and FR-005 rather than denied here.

- FR-004: Service can keep the last cover on the board with the generic staleness marker when it cannot authenticate to Spotify, and report on the local web page that re-authorization is needed. Priority: must-have

  > REVISED after Phase 7, on the owner's instruction that a lost connection should keep the
  > last cover with a warning rather than clear the board. This SUPERSEDES the original
  > FR-004 ("display a distinct error icon on the board when it cannot authenticate"), which
  > was the owner's own Phase 4 addition. Authorization failure is now one of the three
  > blindness causes treated identically on the board (see FR-018); the web page is the only
  > place that says which cause it is and that action is needed.
  > Socratic: the original counter-arguments were that the ambient object becomes an error
  > console and that a 32x32 board cannot carry three distinguishable non-cover states; both
  > were dismissed at the time. The revision resolves the second one incidentally -- there is
  > no longer a full-screen error icon -- at the cost of the board no longer being able to
  > tell the owner that action is required. That cost was stated when the choice was made and
  > accepted.
- FR-005: Owner can re-authorize the Spotify account from the local web page. Priority: must-have
  > Socratic: Counter-arguments considered: it collides with the no-auth decision (an ungated
  > LAN page can start an OAuth flow), and SSH would be cheaper than building a web flow.
  > Resolution: stands as written.

### Display selection

- FR-006: Service can retrieve the cover art for the currently-playing item. Priority: must-have

  > Socratic: Counter-arguments considered: "the cover" hides an unmade choice between track
  > and album art; and repetition, not retrieval, is the real problem (one album means many
  > identical pushes). Resolution: stands as written.

- FR-007: Service can render a cover onto the board's 32x32 resolution, by faithful downscale or by a stylized or dominant-colour treatment, whichever reads better on a pixel board. Priority: must-have

  > Socratic: Counter-argument accepted: "a faithful downscale may be the wrong goal" -- at
  > 1024 pixels a shrunk photo may read worse than a stylized or colour-field rendering, and
  > the original wording foreclosed that. Resolution: reworded to permit either treatment.
  > The original "so the album remains recognizable" bar was dropped as untestable at this
  > size; the open question about what "good" means at 32x32 is recorded.

- FR-008: Service can display the rendered cover on the board and replace it when the playing item changes. Priority: must-have

  > Socratic: Counter-arguments considered: rapid skipping thrashes the Bluetooth link with
  > no settle window; and a same-album track change re-pushes identical pixels. Resolution:
  > stands as written.

- FR-009: Service can select what the board shows according to a single defined display-state rule covering every case: cover available and vouched for, cover available but unvouched, no artwork available, and nothing playing. Priority: must-have

  > Socratic: Counter-argument accepted: the original FR-009/010/011 were three separate
  > requirements all answering "what shows when there is no cover", and should be one rule
  > with cases. Resolution: merged. FR-009 is now that rule; FR-010, FR-011 and FR-018 define
  > three of its cases concretely. The rule itself is the product's domain logic (see
  > Business Logic).
  > REVISED after Phase 7: the original case list was "cover available, no artwork available,
  > nothing playing, playback state unknown, and authorization failed". The last two no longer
  > produce distinct board output -- both now keep the last cover with a staleness marker
  > (FR-004, FR-018) -- so the board has four presentations while the service still
  > distinguishes more causes internally and reports them on the web page.
- FR-010: Service can use the artwork of any playing item type, including podcast episodes, and falls back to a generic symbol only when no artwork exists at all. Priority: must-have

  > Socratic: Counter-argument accepted: "podcasts do have artwork" -- the earlier decision
  > to treat all non-music items as a generic symbol discards images the API already returns.
  > Resolution: revised. This SUPERSEDES the Phase 4 decision "non-music items out of scope,
  > show a generic symbol". The generic symbol is now reserved for items with genuinely no
  > artwork (for example ads).

- FR-011: Service can show a considered idle display when nothing is playing. Priority: must-have

  > Socratic: Counter-argument accepted: "idle is the common state, so it must be must-have"
  > -- music plays a few hours a day, so for most of its life this is an idle object, and
  > leaving its dominant state optional means the thing on the shelf is undefined most of the
  > time. Resolution: PROMOTED from nice-to-have to must-have. It therefore also moves out of
  > Success Criteria's Secondary list.

- FR-017: Service can reuse a previously rendered board image for the same artwork, keyed on the artwork's own identity, instead of downloading and rendering it again. Priority: must-have
  > Cache shape: keyed on the artwork's own identity (its URL or image id), so one entry
  > serves every track on an album and the track-art-versus-album-art question in Open
  > Question 5 does not need answering first. Only the rendered 32x32 frame is stored, not
  > the original download. Entries are unbounded and persist across restarts and reboots,
  > which matters because FR-014 makes reboots a normal event rather than an exception.
  > Accepted consequence: because originals are not kept, changing the rendering pipeline
  > (see `## Forward: technical-roadmap`) means discarding the whole cache and
  > re-downloading every image. This was stated when the choice was made.
  > Socratic: Counter-arguments considered: premature at this scale (one cover is roughly
  > 50KB and a rescale is microseconds against a 5-second latency target); it is a v2
  > requirement wearing v1 clothes, since its stated justification is future work that is
  > not scoped; and it creates a new way to be stale, because a cached image can outlive the
  > artwork it represents and the stale-cover guardrail is harder to honour once the service
  > can display something it did not just fetch. Resolution: stands as written, must-have.
  > This FR is also the owner acting on the FR-006 counter-argument they had dismissed --
  > that repetition, not retrieval, is the real problem.

- FR-018: Service can display a persistent marker in a corner of the last shown cover once it has been unable to read playback for more than 30 seconds, for any cause -- network failure, API errors or rate limiting, or failed authorization. Priority: must-have
  > Marker shape: a small persistent corner marker, chosen over a border (which would cost
  > the outer pixel ring on an already-scarce 32x32 panel), over alternating cover and glyph
  > (which is motion, and animation is a v1 non-goal), and over dimming (which reads as night
  > mode rather than staleness). One marker serves all three causes; the web page is where
  > the cause is named.
  > Grace period: 30 seconds, roughly six missed reads at the 5-second cadence implied by the
  > latency NFR. Chosen over 10 and 15 seconds as more tolerant of transient home-network
  > noise.
  > Socratic: Counter-arguments considered: the board still lies for the first 30 seconds,
  > since a track change during the grace period shows the wrong album with no marker at all,
  > which time-boxes the wrong-but-confident failure rather than removing it; a few corner
  > pixels are invisible during the ambient desk glance that is the actual viewing situation;
  > and one marker for three causes discards the distinction that matters, since a blip, a
  > rate-limit and a dead login demand different responses and only the web page separates
  > them. Resolution: stands as written, must-have.

### Board link

- FR-012: Service can connect to the board over Bluetooth. Priority: must-have

  > Socratic: Counter-arguments considered: it restates what the board library already does;
  > and "connect" skips the real work, which is identifying and binding to one specific board
  > and persisting that choice. Resolution: stands as written.

- FR-013: Service can re-establish the Bluetooth link after a drop with no human action. Priority: must-have
  > Socratic: Counter-arguments considered: unbounded recovery is untestable (no limit on
  > duration or attempts, no defined display meanwhile); and recovery is sometimes impossible
  > when the board is off or out of range. Resolution: stands as written.

### Operation

- FR-014: Owner can run the service unattended on the Raspberry Pi so it resumes after a reboot with no human action. Priority: must-have

  > Socratic: Counter-arguments considered: it is deployment rather than product; and it is
  > the cheapest item on a list of hard ones, which flatters the scope. Resolution: stands as
  > written -- unattended operation is the primary success criterion, so it belongs here.

- FR-015: Owner can use a local web page that reports the believed-playing item and board link health, and from which Spotify can be re-authorized. Priority: must-have

  > Socratic: Counter-argument accepted: "it is no longer a status page" -- FR-005 put the
  > re-auth action on it, making it load-bearing infrastructure rather than an optional
  > read-only view. Resolution: reworded from "view" to reflect that it carries an action and
  > cannot be cut from v1 without also cutting FR-005.

- FR-016: Owner can run the service on a laptop for development and testing, with parity guaranteed for the Spotify and rendering layers but not for board transport behaviour. Priority: must-have
  > Socratic: Counter-argument accepted: "parity breaks exactly where it matters" --
  > Bluetooth stacks differ between a laptop and a Pi, so "the same service on both" holds
  > for everything except the layer most likely to fail. Resolution: reworded to scope the
  > parity claim honestly rather than drop the requirement.

## Non-Functional Requirements

- Within 5 seconds of the playing item changing on the account, the board shows the state
  that corresponds to the new item.
- The service operates for at least 7 consecutive days with no human intervention and no
  degradation in the above timing.

Two further outside-observable properties are recorded under Success Criteria as guardrails
rather than repeated here: Spotify request rate stays within published limits, and the
Spotify refresh credential leaves no trace off the device it was created on.

The following were offered and explicitly not selected, so they are not requirements:
absence of visible flicker during rapid skipping, a bounded recovery time after reboot, and
a no-torn-frames guarantee. Each remains a known risk rather than a commitment -- flicker
in particular is the same concern raised and dismissed in the FR-008 challenge.

## Business Logic

From what the Spotify account reports, the service decides which one of a defined set of
board states to show -- artwork, generic symbol, idle, unknown, or authorization error --
and renders any available artwork for a 32x32 display.

The inputs the rule consumes are all user-facing: what the account says is playing (its
identity and its type), whether playback is actually active, whether the account can be
read at all right now, and the artwork associated with the playing item if any exists. The
output is exactly one of the five states, plus -- when artwork is available -- a rendered
image sized for the board.

The owner encounters the rule by looking at the board: every moment of the day it is
asserting one of those five states, and the rule is what picks which. The web page reports
the same decision in words, so the owner can tell whether a blank board means "nothing is
playing" or "cannot read the account right now". This is why the rule is the product
rather than a detail of it: the hardware only ever shows one image, so choosing the right
one is the whole job.

The hardest case in the rule used to be distinguishing "briefly cannot read playback" from
"nothing is playing" -- treating a momentary failure as silence makes the board flicker,
treating it as continuity risks an unlabelled stale cover. That dilemma is now resolved by a
third option the owner specified after Phase 7: the board keeps the last cover and says so.
Blindness is therefore no longer a state of its own but a qualifier on the cover state, and
after a 30-second grace period the board asserts "this is what was playing, and I can no
longer confirm it".

The consequence is that the rule's five causes collapse into four board presentations. The
service still distinguishes why it is blind -- network, API errors, authorization -- but the
board shows the same marker for all three, and the web page is the only surface that names
the cause. Deciding what to show when the service is blind and has no cover to fall back on
is unresolved and recorded under Open Questions.

## Access Control

Single user; no auth. The service runs on the owner's own hardware (Raspberry Pi in
production, laptop in dev) on the owner's own network. Flat user model -- no roles, no
sign-up, no sign-in, no gated routes.

The MVP does include a small local web page: a status surface on the LAN showing what the
service believes is currently playing and whether the board link is alive. That page is
ungated, consistent with the single-user model.

The real gate in this product is Spotify account authorization, not app login. At product
level: the owner authorizes the Spotify account once during setup. The service stays
authorized without further human action. The product explicitly acknowledges that this
authorization can break (revoked, expired, changed password) and must surface a visible
failure state when it does -- on the board, on the status page, or both -- after which the
owner re-authorizes manually.

## Non-Goals

Functional non-goals:

- **Anyone else's board or account.** No hosted service, no installable release for other
  iDotMatrix owners, no multi-account support. Single-tenant by choice -- and structurally
  forced: Bluetooth requires the service to run physically next to its own board, so a
  centralized version cannot exist.
- **Controlling playback.** The product is strictly read-only against the Spotify account.
  No play, pause, skip or volume from the board or the web page. This also rules out a
  whole category of authorization scope.
- **Sources other than Spotify.** No Apple Music, no local files, no system-audio
  detection, no scrobbling services. One source only.
- **AI-enhanced and animated covers, in v1.** The owner intends both later (recorded under
  `## Forward: technical-roadmap`), and FR-017 exists partly to prepare for them. Neither is
  in v1: the v1 board shows one static frame per item, derived from the artwork Spotify
  returns. Ruled out now so the rendering work in v1 stays small.

Non-functional non-goals:

- **No flicker-freedom guarantee.** Rapid skipping may produce visible churn on the board.
  Offered as a requirement in Phase 5 and declined.
- **No bounded recovery time after reboot.** The service resumes without human action
  (FR-014), but how quickly is not committed to.
- **No no-torn-frames guarantee.** A partially drawn image may be briefly visible.
- **No staleness guarantee while the board is unreachable.** FR-018 marks a cover the
  service cannot vouch for, but it can only do that by pushing to the board. If the
  Bluetooth link is what failed, whatever the board last received stays up with no marker
  and there is nothing the service can do about it. The web page still reports the link as
  down (FR-015). Recorded as a stated hole in the guarantee rather than a defect.

Explicitly NOT a non-goal, recorded so it is not mistaken for one:

- **Animation, visualizers and scrolling text** were offered as a non-goal and declined.
  Nothing in the v1 FR set needs them -- the display is one static frame at a time -- but
  they are not ruled out for later. The AI-enhanced and animated rendering the owner plans
  is the separate functional non-goal above: out of v1, intended afterwards.

## Open Questions

1. **What is the project called?** -- Owner. Blocks: `/10x-prd` cannot write the `project`
   frontmatter field without it and will ask before generating. Asked three times during
   this session and still unset.
2. **What shows when the service is blind and has no cover to fall back on?** -- Owner.
   FR-018 keeps the last cover and marks it, but on a cold start -- or when playback was
   already stopped before the connection dropped -- there is no cover to keep. The generic
   no-artwork symbol, the idle display and a dedicated state are all plausible and all mean
   different things. Needs resolving before FR-009's rule is complete.
3. **What counts as a good 32x32 rendering?** -- Owner. The Socratic round dropped
   "so the album remains recognizable" from FR-007 as untestable at 1024 pixels, and
   permitted either a faithful downscale or a stylized/dominant-colour treatment. No
   acceptance bar has replaced it, so FR-007 currently cannot be failed.
4. **Can the display states be told apart at 32x32 from across a room?** -- Owner.
   Three visual elements must each be instantly readable: the generic no-artwork symbol
   (FR-010), the idle display (FR-011), and the staleness marker (FR-018), the last of which
   must coexist with artwork rather than replace it. The counter-argument was raised three
   times across the challenge rounds and dismissed each time; the constraint is still real.
   The marker case is the sharpest, since a few corner pixels are the whole signal that a
   cover cannot be trusted.
5. **Track artwork or album artwork?** -- Owner. FR-006 says "the cover art for the
   currently-playing item", which differs between singles, compilations and remasters.
   The FR stands as written, but the choice underneath it has not been made.

Accepted risks (recorded so they are not mistaken for open questions):

- An ungated LAN page can start an OAuth flow (FR-005 + Access Control). Anyone on the
  home network could re-point the service at their own Spotify account. Raised in the
  challenge round and dismissed.
- Rapid skipping may thrash the Bluetooth link with no settle window, and a same-album
  track change re-pushes identical pixels (FR-008). Raised and dismissed.
- Bluetooth recovery is asserted without bound -- no limit on duration or attempts, and no
  defined display while disconnected (FR-013). Raised and dismissed.
- Headless OAuth on the Pi is the most awkward part of setup (FR-001). Acknowledged rather
  than solved; the local web page is what makes it workable.
- A cached frame can outlive the artwork it represents, since entries are keyed on artwork
  identity and never expire (FR-017). Raised in the FR-017 challenge and dismissed.

## Forward: technical-roadmap

Informational only. NOT part of the PRD schema and NOT v1 scope. Captured here because the
owner volunteered it as the rationale for FR-017, and a downstream planning step should see
it.

- **AI enhancement of covers.** The owner intends to add AI-assisted generation or
  enhancement of the board image rather than only downscaling the original artwork. This is
  the reason the cache exists: once rendering is expensive, re-deriving the same frame on
  every track change stops being acceptable.
- **Animated covers.** The owner intends to make covers animated at some later point.
  Animation was offered as a v1 non-goal in Phase 6 and declined, so it is deliberately not
  ruled out -- see `## Non-Goals`.

Accepted consequence of the FR-017 cache shape: only rendered 32x32 frames are stored, not
the originals they came from. Introducing either of the above changes the rendering
pipeline, which means the entire cache must be discarded and every image re-downloaded. The
owner was told this when choosing what to cache and chose it anyway.

## Forward: tech-stack

Informational only. NOT part of the PRD schema. Captured here so the downstream
tech-stack-selection step can pick it up.

- User volunteered: app runs on a Raspberry Pi in production, laptop for dev and testing.
- User volunteered: Spotify Web API as the now-playing / cover-art source.
- User volunteered two candidate board libraries (both Python):
  - https://github.com/derkalle4/python3-idotmatrix-client
  - https://github.com/markusressel/idotmatrix-api-client
- Implied: Bluetooth (BLE) as the board transport.

No stack decision is made or implied by this shaping session.

## Quality cross-check

Ran at the close of Phase 7. All six greenfield checks pass:

| Element                    | Result                                                                                                |
| -------------------------- | ----------------------------------------------------------------------------------------------------- |
| Access Control             | present -- single user, no auth, flat model; Spotify authorization identified as the real gate        |
| Business Logic             | present -- one declarative sentence (state selection across five board states)                        |
| Project artifacts          | present -- shape-notes.md with a valid checkpoint block                                               |
| Timeline-cost acknowledged | present -- mvp_weeks 3, within the default target, so no sustained-effort acknowledgment was required |
| Non-Goals                  | present -- 3 functional, 3 non-functional, plus one explicitly-not-a-non-goal                         |
| Preserved behavior         | n/a (greenfield)                                                                                      |

One gap outside the six, which is why `quality_check_status` is `warned` rather than
`accepted`:

- **`project` frontmatter field is null.** Consequence: `/10x-prd` will stop and ask for the
  project name before it can write frontmatter. Routed to Open Question 1.
