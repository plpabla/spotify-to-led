# Staleness marker completes the display rule Implementation Plan

## Overview

Roadmap S-06. Once the service has been unable to read playback for more than 30 s, the
board marks whatever it is showing, not only a cover: idle, the playing placeholder and the
no-artwork disc get the same amber corner marker a stale cover already gets. That answers
PRD Open Question 2 (blind with no cover to fall back on), completes FR-009's display rule,
and is then signed off on the Pi with a real outage and a cross-room legibility check.

## Current State Analysis

- S-04 already marks a **cover** after more than `STALE_AFTER_SECONDS` (30) of failed or
  stopped reads (`src/spotify_to_led/display.py:179-192`), and S-05 kept that.
- Every other presentation returns from `select_presentation` before the staleness step:
  no reader / `UNKNOWN` / `NOTHING_PLAYING` → `IDLE` (`display.py:162-163`), no artwork →
  `NO_ARTWORK` (`:166-167`), no loader or a failed download → `PLACEHOLDER` (`:168-169`,
  `:177-178`), and a pending cover that keeps a non-cover previous (`:179-180`).
- So these blind cases show an unmarked frame today, for as long as reads fail:
  - cold start with no token or no network: `UNKNOWN` → plain idle;
  - reads failing after "nothing playing": plain idle, still claiming nothing plays;
  - an item whose cover failed to download, or no loader: plain placeholder (S-04 review F7);
  - an ad or local file: plain disc, though the ad may long have ended.
- `_frame_for` applies `frames.with_stale_marker` only on the cover branch
  (`display.py:276-291`); `Presentation.__str__` labels only covers "(stale)"
  (`display.py:127-130`).
- `frames.with_stale_marker` (`src/spotify_to_led/frames.py:152`) works on any frame. Its
  footprint (top-right `2 cells + 2 px` square: 4 px at 16, 6 px at 32, 10 px at 64) does not
  overlap a lit pixel of the idle note, the play triangle or the disc at any supported size.
- The reader sets `failure_since` at once when it holds no token (`AUTHORIZATION_NEEDED`,
  `spotify/reader.py:163-166`), so an unauthorized cold start is "blind" after 30 s under the
  existing `_is_stale`; a slow-to-start reader with no failure and no read yet is not.
- Two tests pin the old behaviour and must be inverted:
  `test_the_placeholder_is_never_marked` and `test_the_symbol_is_never_marked`
  (`tests/test_display.py:337-349`).
- The marker never triggered on hardware: S-04's outage test was not run, because unplugging
  Ethernet left the Pi online over Wi-Fi (roadmap S-06 Risk).

## Desired End State

- While a reader exists, every presentation the rule chooses — cover, idle, placeholder,
  no-artwork disc — carries the stale flag exactly when `_is_stale(snapshot, now)` holds, and
  is drawn with the unchanged full-brightness amber marker. The flag clears on the first
  successful read.
- With no reader (Spotify not configured), the board shows plain idle, never marked.
- The log names the marker for any kind: `board presentation: idle (stale)`.
- PRD OQ2 is resolved, FR-009 and Business Logic say so, the roadmap no longer marks S-06
  blocked, and CLAUDE.md no longer says the blind-with-no-cover state is unbuilt.
- On the Pi, cutting outbound HTTPS for ~90 s marks the playing cover, and separately marks
  idle when nothing plays; both clear after the rule is removed. The owner has judged the
  marker legible from across the room over a cover and over idle in a dark room, recorded in
  `context/changes/staleness-marker-and-display-rule/verification.md` and in OQ4.

### Key Discoveries:

- `select_presentation` is pure and fully table-tested without a board (`display.py:150`);
  the change is confined to it, `_frame_for` and `__str__`.
- `_is_stale` already covers both failing reads (`failure_since`) and a reader that stopped
  without failing (`last_read_at`) (`display.py:186-192`); no reader change is needed.
- A pending changeover returns `previous` and then recomputes staleness for a kept cover
  (`display.py:173-183`); the same recompute must now apply to a kept idle, placeholder or
  disc, so a kept presentation is never stuck marked or unmarked from an earlier sample.
- The loop already pushes on a change of the stale flag alone and retries a failed push of a
  marker (`test_a_stale_marker_whose_push_failed_is_retried_rather_than_left_unmarked`), so
  no loop change is needed beyond the frame.

## What We're NOT Doing

- No dedicated "blind" glyph, and no distinct marker per cause; the web page (S-07) names the
  cause.
- No marker when Spotify is not configured (no reader): that is a setup mode, not blindness.
- No dimmed marker over idle: one marker, same look in every state.
- No change to the 30 s grace, the marker's shape, colour or position, or the reader.
- No staleness guarantee while the board link itself is down (PRD Non-Goals).
- No status page (S-07).

## Implementation Approach

Restructure `select_presentation` so that every branch after the no-reader check produces a
`chosen` presentation, and the single final step applies `stale=_is_stale(snapshot, now)` to
it whatever its kind. Draw the marker in `_frame_for` for any stale presentation. Then record
the decision in the foundation docs, and verify on the Pi.

## Phase 1: Marker on every presentation

### Overview

The rule, frame and log changes with their tests, the docstrings that describe the rule, and
the OQ2 decision written into the PRD, roadmap and CLAUDE.md.

### Changes Required:

#### 1. Display rule

**File**: `src/spotify_to_led/display.py`

**Intent**: Make staleness a qualifier on every presentation while a reader exists, so the
board never shows an unmarked idle, placeholder or disc the service cannot vouch for.

**Contract**: `select_presentation(snapshot, cover, previous, now)` keeps its signature.
`snapshot is None` → `IDLE` (unmarked, returned before any staleness check). For any other
snapshot the chosen presentation (idle for `UNKNOWN`/`NOTHING_PLAYING`, `NO_ARTWORK`,
`PLACEHOLDER`, a ready cover, or a kept `previous` during a pending changeover) is returned as
`replace(chosen, stale=_is_stale(snapshot, now))`. A kept `previous` that was stale is
re-evaluated, never carried over. The module constants `IDLE`, `PLACEHOLDER`, `NO_ARTWORK`
stay unmarked values.

#### 2. Frame and log

**File**: `src/spotify_to_led/display.py`

**Intent**: Draw the marker over whichever frame a stale presentation names, and say so in
the log line for every kind.

**Contract**: `_frame_for` builds the base frame per kind as today, then returns
`frames.with_stale_marker(base)` when `presentation.stale`. `Presentation.__str__` appends
` (stale)` for any stale kind: `idle (stale)`, `placeholder (stale)`, `no_artwork (stale)`,
`cover <url> (stale)`; unmarked strings are unchanged.

#### 3. Docstrings

**Files**: `src/spotify_to_led/display.py` (module docstring), `src/spotify_to_led/frames.py`
(module docstring and `with_stale_marker` if it says "cover")

**Intent**: The rule list says the marker applies to every presentation once reads have been
failing or stopped for more than 30 s, that no reader shows plain idle, and drops the "symbol
claims nothing a failed read could make untrue" and "that case stays S-06's" wording. The
frames docstring notes the marker clears every glyph at every supported size.

**Contract**: Prose only.

#### 4. Tests

**Files**: `tests/test_display.py`, `tests/test_frames.py`

**Intent**: Pin the completed rule and the marker's fit over the glyphs.

**Contract**:
- Replace `test_the_placeholder_is_never_marked` and `test_the_symbol_is_never_marked` with
  tests that the placeholder (failed download, and no loader) and the disc (failing reads,
  and reads stopped) are marked after more than 30 s and not at 29 s.
- Idle is marked after more than 30 s of failing reads for both `UNKNOWN` and
  `NOTHING_PLAYING`, and not at 29 s; `UNKNOWN` with no failure and no read yet is plain idle.
- No reader is plain idle regardless of time.
- A pending changeover that keeps idle while blind returns stale idle; a stale kept
  presentation clears when reads recover.
- `str()` of each stale kind reads as listed in change 2.
- Loop: with failing reads past the grace and nothing playing, the pushed frame equals
  `frames.with_stale_marker(frames.idle(size))`, and a recovered read pushes plain idle.
- Frames: for idle, placeholder and no-artwork at 16, 32 and 64, no lit glyph pixel lies inside
  the marker's footprint.

#### 5. Decision docs

**Files**: `context/foundation/prd.md`, `context/foundation/roadmap.md`, `CLAUDE.md`

**Intent**: Record the OQ2 answer where it is asked and stop calling the case unbuilt.

**Contract**:
- `prd.md`: OQ2 marked RESOLVED 2026-11-10 with the decision (the marker goes on whatever the
  board shows; no reader stays plain idle), original question kept for the record, in the
  style of OQ3/OQ5. An `> Implemented` / decision note under FR-009 and under FR-018 (the
  marker is no longer confined to "the last shown cover"). Business Logic's last sentence
  points at the resolution instead of "unresolved".
- `roadmap.md`: S-06's first Unknown struck through and RESOLVED, Blockers stay `--`, Open
  Roadmap Question 2 RESOLVED, Backlog Handoff note no longer "and Open Question 2 resolved".
  Do not touch Status fields (the skills own them).
- `CLAUDE.md`: the overview's staleness clause says any presentation gets the marker once reads
  have failed for more than 30 s (plain idle when Spotify is not configured); the closing
  sentence says only the status page is not built yet.

### Success Criteria:

#### Automated Verification:

- Full suite passes: `uv run pytest`
- No test or docstring still claims the placeholder or the symbol is never marked: `git grep -n -E "never.marked" -- src tests` returns nothing

#### Manual Verification:

- On the laptop with the stub transport and Spotify configured but `var/spotify-token.json` moved aside, `var/frames/latest.png` shows idle without the marker at first and with the amber marker after about 30 s, and the log shows `board presentation: idle (stale)`; restoring the token file and restarting returns plain idle or the playing cover

**Implementation Note**: After completing this phase and all automated verification passes,
pause here for manual confirmation from the human that the manual testing was successful
before proceeding to the next phase.

---

## Phase 2: Pi outage and legibility sign-off

### Overview

Deploy to the Pi, make it lose Spotify for real, and have the owner judge the marker on the
board. This is the hardware check S-04 could not run, and the marker part of OQ4.

### Changes Required:

#### 1. Outage test on the Pi

**File**: none (procedure; results go to `verification.md`)

**Intent**: Prove the marker appears and clears on hardware for a cover and for idle.

**Contract**: Deploy with `scripts/deploy.sh`. With a cover playing, run
`sudo iptables -I OUTPUT -p tcp --dport 443 -j REJECT`, watch `pm2 logs spotify-to-led` over
LAN SSH, wait ~90 s, then `sudo iptables -D OUTPUT -p tcp --dport 443 -j REJECT`. Repeat
with nothing playing. The rule rejects packets on the reader's open keep-alive connection
too, which is why it is used rather than an `/etc/hosts` edit. Delete the rule even if a step
fails; `sudo iptables -S OUTPUT` must show no REJECT rule at the end.

#### 2. Legibility judgement

**File**: none (owner observation)

**Intent**: Settle whether a few corner pixels carry the signal at 32x32, over a cover and over
the dim idle note in a dark room.

**Contract**: Judged on the real board from normal across-the-room distance during the outage
windows above, with the room lit and dark.

#### 3. Record the evidence

**Files**: `context/changes/staleness-marker-and-display-rule/verification.md`,
`context/foundation/prd.md`, `context/foundation/roadmap.md`

**Intent**: Keep the hardware evidence with the change, and close the marker part of OQ4.

**Contract**: `verification.md` in the shape of the earlier changes' (date, deployed commit,
each step with the log lines seen and the owner's judgement). PRD OQ4 and roadmap Open Roadmap
Question 4 / S-06's second Unknown record the marker's legibility result; if the owner judges
it illegible, stop and re-plan the marker rather than tuning it inside this phase.

### Success Criteria:

#### Automated Verification:

- Full suite passes on the Pi: `uv run pytest`
- After the tests, no REJECT rule remains: `sudo iptables -S OUTPUT` lists none

#### Manual Verification:

- With a cover playing and outbound 443 rejected, the marker appears on the cover after about 30 s (log `board presentation: cover <url> (stale)`) and clears within a few seconds of deleting the rule
- With nothing playing and outbound 443 rejected, idle gains the marker after about 30 s (log `board presentation: idle (stale)`) and loses it after the rule is deleted
- The owner reads the marker from across the room over a cover and over idle, with the room lit and dark, and the result is recorded in `verification.md` and OQ4

**Implementation Note**: This phase is owner-driven on hardware; pause for confirmation of each
manual item before closing the change.

---

## Testing Strategy

### Unit Tests:

- `select_presentation` table: every kind × {fresh, 29 s failing, 31 s failing, 31 s stopped},
  no reader at any age, pending changeover keeping idle and keeping a cover, recovery.
- `Presentation.__str__` for every stale kind.
- Marker footprint against every glyph at 16, 32 and 64.

### Integration Tests:

- `run_display` with the fake board: blind and nothing playing pushes marked idle, recovery
  pushes plain idle.

### Manual Testing Steps:

1. Laptop: stub transport, token file moved aside, watch `var/frames/latest.png` and the log.
2. Pi: iptables outage with a cover playing, then with nothing playing; remove the rule.
3. Owner judges the marker across the room, lit and dark.

## Performance Considerations

None: one image copy and two pastes per pushed frame, only when the presentation changes.

## Migration Notes

None. Cached cover frames are unmarked renders and stay valid; the marker is drawn at push time.

## References

- Roadmap S-06 and Open Roadmap Questions 2 and 4: `context/foundation/roadmap.md`
- PRD FR-009, FR-018, US-02, US-03, US-04, OQ2, OQ4: `context/foundation/prd.md`
- S-04 review F7 (unmarked placeholder): `context/archive/2026-11-09-artwork-on-the-board/reviews/impl-review.md:97`
- S-04 marker design: `context/archive/2026-11-09-artwork-on-the-board/plan.md`
- Display rule: `src/spotify_to_led/display.py:150-192`, frame: `display.py:276-291`
- Marker overlay: `src/spotify_to_led/frames.py:152`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Marker on every presentation

#### Automated

- [x] 1.1 Full suite passes: `uv run pytest` — 041bc76
- [x] 1.2 No test or docstring still claims the placeholder or the symbol is never marked: `git grep -n -E "never.marked" -- src tests` returns nothing — 041bc76

#### Manual

- [ ] 1.3 Laptop stub with the token file moved aside shows idle, then idle with the marker after about 30 s and the log line `board presentation: idle (stale)`; restoring the token clears it

### Phase 2: Pi outage and legibility sign-off

#### Automated

- [x] 2.1 Full suite passes on the Pi: `uv run pytest` — 1d628e5
- [x] 2.2 After the tests, no REJECT rule remains: `sudo iptables -S OUTPUT` lists none — 1d628e5

#### Manual

- [x] 2.3 With a cover playing and outbound 443 rejected, the cover gains the marker after about 30 s and loses it after the rule is deleted — 1d628e5
- [x] 2.4 With nothing playing and outbound 443 rejected, idle gains the marker after about 30 s and loses it after the rule is deleted — 1d628e5
- [x] 2.5 The owner reads the marker from across the room over a cover and over idle, lit and dark, recorded in `verification.md` and OQ4 — 1d628e5
