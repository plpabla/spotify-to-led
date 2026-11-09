# Artwork on the Board (S-04) Implementation Plan

## Overview

Replace the temporary playing placeholder with the real cover of whatever is playing: pick a
Spotify image, download it, render it to the board's size with a treatment chosen by looking at
real covers on the real board, and push it. When reads have been failing for more than 30 s,
a cover that is showing gets FR-018's corner marker, so `main` stays deployable to the Pi.
The step from artwork to board frame is one named function, which is the seam later animation
or LLM work replaces.

## Current State Analysis

From the frame brief (`frame.md`), settled and not re-investigated here:

- **OQ5 (track vs album art) is moot.** Spotify's `TrackObject` has no images; cover art exists
  only as `album.images`, which `playback.py:117-119` already reads. Episodes carry their own
  `images` (`playback.py:122`).
- **OQ3 (rendering treatment) is decided only on the board**, per the PRD's resolution path.
- **Staleness is narrower than "wait for S-06".** FR-018 fully specifies the marker when a cover
  is in hand; only the no-cover case is blocked by OQ2.
- **The future work stresses two points only**: the transport payload (animation is an on-device
  GIF upload, `idotmatrix/modules/gif.py:50`) and the time it takes to produce a frame. Reader,
  connect/retry and redraw-on-reconnect are indifferent to both.

From the code:

- `display.py:68-87`: `Presentation` is a two-member `StrEnum`; `frame_for` is pure and
  synchronous; `select_presentation` ignores `failure`.
- `display.py:93-122`: `run_display` decides a redraw by comparing `(Presentation, connections)`
  and pushes inline. That comparison survives if `Presentation` stays hashable.
- `playback.py:45-79`: `Artwork(url, width, height)`, widest first; the snapshot keeps the last
  item through failed reads and carries `failure` and `failure_since`.
- `main.py:106-118`: the lifespan builds one `httpx2.AsyncClient` (only when Spotify is
  configured) and passes `current()` to the display task.
- `tests/test_display.py:37-88`: `FakeBoard` identifies each push by matching its pixels against
  `frame_for(presentation)`, which cannot represent arbitrary covers.
- `tests/test_spotify_reader.py:3-10,146`: the established pattern for HTTP tests is
  `httpx2.MockTransport` plus a fake clock and a fake sleep.
- `scripts/` holds only `deploy.sh`. The Pi changes only through it, and the 7-day unattended
  observation is running on it.

## Desired End State

With Spotify playing, the board shows the item's cover, rendered with the treatment the owner
chose on the board, and replaces it within 5 s of an item change (NFR-01). While the new
cover downloads, the previous presentation stays; if the download fails, the playing
placeholder shows and the download is retried with backoff. An item with no artwork shows the
placeholder. Once reads have failed for more than 30 s, a showing cover carries a corner marker;
it clears when reads recover. FR-007 in the PRD names the treatment and a falsifiable bar. Verified
by `uv run pytest`, by the PNG stub on the laptop, and on the Pi's board.

### Key Discoveries:

- Spotify returns images widest first (640 / 300 / 64 for albums), with nullable dimensions
  (`playback.py:155-165`), so selection must tolerate missing widths.
- The connect retry's backoff shape (5 s doubling to 60 s, `display.py:56-58`) is the project's
  retry convention; cover download retries reuse it.
- The board accepts one BLE connection, so the bake-off script on the Pi needs the service
  stopped (`pm2 stop spotify-to-led`) while it runs.
- `frames.py` designs glyphs on a 16x16 grid scaled by whole pixels so 16/32/64 boards match;
  the marker follows the same rule.

## What We're NOT Doing

- No on-disk or multi-entry frame cache (FR-017 is S-05). The loader holds only the cover
  currently wanted.
- No generic no-artwork symbol (FR-010 is S-05); the playing placeholder stands in for it.
- No board state for "blind with no cover to mark" (OQ2, S-06). Today's behaviour (idle or
  placeholder) stays for that case.
- No GIF/animation transport payload, no async LLM renderer, no plugin or registry machinery.
  The seam is the single `render_cover` function and is documented as such.
- No settle window for rapid skipping (accepted known risk in the PRD).
- No changes to the Spotify reader, the `/playback` route, or the connect/retry logic.

## Implementation Approach

Phase 1 builds the three renderer candidates and the marker as pure functions, and a script
that shows them on the board, so the owner settles OQ3 before any pipeline depends on a
choice. Phase 2 keeps only the winner and builds the cover pipeline beside the display loop:
pick an image, download it with the existing HTTP client, render it, and hold the result in a
small loader that runs off the loop and retries on failure. Phase 3 changes the display rule
so a presentation can name a cover and be marked stale, and wires the loader in. Phase 4 rolls
out to the Pi, verifies NFR-01 and the marker on hardware, and closes the open questions in the
foundation docs.

## Critical Implementation Details

**Timing & lifecycle**: the download must never run inside `run_display`'s sample iteration.
The loop only asks the loader for the state of the cover it wants; the loader's own task does
the I/O. Otherwise a slow CDN delays link-down detection and redraw on reconnect, which S-03
depends on. The loader's task is cancelled on shutdown before the transport closes.

**State sequencing**: while the wanted cover is pending, the rule keeps the previous
presentation. The loop must therefore retain the rendered frame of the cover it is showing,
not look it up in the loader, because the loader has already moved on to the new URL. A
reconnect redraw during a changeover still has to draw the old cover.

## Phase 1: Rendering candidates, marker and bake-off

### Overview

Write the three candidate treatments and the stale marker, and a script that pushes a fixed set
of covers each way to the board. The owner picks the treatment and writes FR-007's bar.

### Changes Required:

#### 1. Renderer candidates

**File**: `src/spotify_to_led/rendering.py` (new)

**Intent**: Turn a decoded cover image into a board frame three ways, for comparison: (1) plain
area downscale, (2) downscale with a contrast and saturation boost, (3) stylized: downscale
quantized to a small palette. Phase 2 deletes the two that lose.

**Contract**: each candidate is `(source: Image.Image, size: int) -> Image.Image`, returning an
RGB `(size, size)` image that passes `validate_frame`. A non-square source is center-cropped to
a square first. Any source mode (RGB, RGBA, P, L, CMYK) is accepted and converted.

#### 2. Stale marker

**File**: `src/spotify_to_led/frames.py`

**Intent**: Add FR-018's marker as a function that returns a copy of a frame with a small
bright block in one corner, separated from the cover by a black ring so it reads against
any artwork. Geometry and colour are module constants, tuned on the board like
`IDLE_MAX_CHANNEL`.

**Contract**: `with_stale_marker(frame: Image.Image) -> Image.Image`; the input is not mutated.
Designed on the 16-grid and scaled by `size // 16`. Starting point: a 2x2-cell amber block in
the top-right corner inside a 1-cell black ring. The module docstring is updated to list it.

#### 3. Bake-off script

**File**: `scripts/cover_bakeoff.py` (new)

**Intent**: Push each cover in a fixed set to the configured transport once per candidate,
then the same covers with the marker over each candidate, holding each frame a few seconds and
printing what is on the board. Also write an upscaled contact sheet PNG to `var/frames/` for
review on the laptop.

**Contract**: `uv run python scripts/cover_bakeoff.py <image-url>...` uses `load_settings()` and
`create_transport()`, so on the Pi it drives the real board and on the laptop the PNG stub. The
owner supplies 6–10 image URLs chosen for variety (dark, bright, text-heavy, photographic,
minimal), e.g. read from `/playback`.

#### 4. Tests

**File**: `tests/test_rendering.py` (new), `tests/test_frames.py`

**Intent**: Each candidate returns a valid frame for square, non-square and non-RGB sources at
every supported size. The marker changes only its corner region and leaves the input untouched.

**Contract**: parameterised over `SUPPORTED_BOARD_SIZES`.

### Success Criteria:

#### Automated Verification:

- Renderer and marker tests pass: `uv run pytest tests/test_rendering.py tests/test_frames.py`
- Full suite passes: `uv run pytest`
- The bake-off script runs against the PNG stub on the laptop and writes the contact sheet

#### Manual Verification:

- Bake-off run on the Pi's board (service stopped, then restarted with `pm2 start`), judged from desk distance
- Owner picks the treatment and writes it, plus a falsifiable acceptance bar, into PRD FR-007
- The marker is visible over every cover in the set from across the room, or its constants are tuned until it is

**Implementation Note**: After completing this phase and all automated verification passes, pause
here for the owner's bake-off and choice before Phase 2.

---

## Phase 2: Cover pipeline

### Overview

Keep only the chosen treatment, and build the path from a playing item to a rendered frame,
running off the display loop.

### Changes Required:

#### 1. The render seam

**File**: `src/spotify_to_led/rendering.py`, `scripts/cover_bakeoff.py`

**Intent**: Delete the two losing candidates and expose the winner as the one function the rest
of the service calls. The docstring names it as the seam an animated or LLM-generated cover
replaces later. Reduce the bake-off script to the winner with and without the marker, so it
stays usable for tuning the marker and S-05's symbol.

**Contract**: `render_cover(source: Image.Image, size: int) -> Image.Image`.

#### 2. Artwork selection and download

**File**: `src/spotify_to_led/covers.py` (new)

**Intent**: Pick the image to download for an item and fetch it through the existing HTTP
client, so the service downloads the smallest image that still covers the board.

**Contract**: `select_artwork(item: PlayingItem | None, size: int) -> Artwork | None`: the
smallest image whose known width is at least `size`; if none qualifies, the first (widest) one;
`None` when there is no artwork. Fetching uses a bounded timeout (about 4 s, inside NFR-01's
budget) and treats a non-2xx status, a transport error or an undecodable body as a failure.

#### 3. Cover loader

**File**: `src/spotify_to_led/covers.py`

**Intent**: Hold the one cover the display currently wants, downloading and rendering it in its
own task and retrying a failure with the connect retry's backoff while the same URL stays
wanted. Wanting a different URL cancels the old work.

**Contract**: `CoverLoader(client: httpx2.AsyncClient, size: int, *, sleep: Sleep)` with
`want(url: str | None) -> None`, `state(url: str) -> CoverState`, and `aclose()`. `CoverState`
is pending, ready (carrying the frame) or failed. A failure stays `failed` during the backoff
sleep and becomes `ready` on a later success. One INFO line per download success, and a
WARNING per failure with the cause. URLs may be logged; nothing else is.

#### 4. Tests

**File**: `tests/test_covers.py` (new)

**Intent**: Selection over widths, missing widths, a single image and no images. Loader: pending,
then ready; a failure becomes failed, retries with doubling delays and recovers; switching the
URL cancels the old download; `aclose` leaves no task running.

**Contract**: `httpx2.MockTransport` serving a small generated PNG, plus a fake sleep, following
`tests/test_spotify_reader.py`.

### Success Criteria:

#### Automated Verification:

- Cover tests pass: `uv run pytest tests/test_covers.py`
- Full suite passes: `uv run pytest`
- No references to the deleted candidates remain: `grep -rn` for their names in `src/` `scripts/` `tests/` returns nothing

---

## Phase 3: Display rule and loop

### Overview

Let a presentation name a cover and be marked stale, put the loader beside the loop, and wire it
into the app.

### Changes Required:

#### 1. Presentation and rule

**File**: `src/spotify_to_led/display.py`

**Intent**: Extend the rule to the cases this slice owns, replacing the "interim" choices
recorded in the module docstring.

**Contract**: `Presentation` becomes a frozen, hashable value: kind (`IDLE`, `PLACEHOLDER`,
`COVER`), the cover URL for `COVER`, and `stale`. The rule, given the snapshot, the loader state
of the wanted cover, the previous presentation and the current time:

- no reader, `UNKNOWN` or `NOTHING_PLAYING` → `IDLE` (unchanged, including while reads fail: OQ2)
- `PLAYING`/`PAUSED` with no selectable artwork → `PLACEHOLDER`
- wanted cover ready → `COVER(url)`
- wanted cover pending → the previous presentation (changeover); `PLACEHOLDER` if there is none
- wanted cover failed → `PLACEHOLDER`
- a `COVER` result, kept or new, is `stale` when `failure_since` is more than 30 s before now

`STALE_AFTER_SECONDS = 30` is a module constant.

#### 2. Loop

**File**: `src/spotify_to_led/display.py`

**Intent**: Each sample, tell the loader which cover is wanted and choose with the rule. When a
redraw is due, draw the retained cover frame, with the marker when stale. Redraw triggers stay
as they are: a presentation change or a new connection.

**Contract**: `run_display(transport, size, current, *, covers: CoverLoader | None = None,
clock: Clock = <utc now>, sleep: Sleep = asyncio.sleep)`. The loop keeps `(url, frame)` for the
cover it last chose. The "board presentation" INFO line names the kind, the URL for covers and
the stale flag.

#### 3. Wiring

**File**: `src/spotify_to_led/main.py`

**Intent**: When Spotify is configured, build a `CoverLoader` on the existing HTTP client and
pass it to the display task; close it in `finally` after the display task is cancelled and
before the client closes. Update the module docstring's description of what the display shows.

**Contract**: no new routes or settings; without Spotify the loader is `None` and the display
shows idle as today.

#### 4. Frames docstring

**File**: `src/spotify_to_led/frames.py`

**Intent**: The playing placeholder is no longer deleted by S-04: it is the stand-in for a cover
that is not available yet, failed to download, or does not exist, until S-05's generic symbol.

**Contract**: docstring only.

#### 5. Tests

**File**: `tests/test_display.py`

**Intent**: Rework `FakeBoard` to record the pushed frames and compare them to expected images
instead of matching `frame_for`. Cover the rule table: each case above, a 29 s vs 31 s failure
boundary, the marker clearing when reads recover, paused keeping its cover, the changeover
keeping the previous cover, and a reconnect during a changeover redrawing the old cover. Loop:
one push per item change, no push while pending, placeholder on failure, then the cover once a
retry succeeds. Keep the existing connect, reconnect and push-failure tests passing.

**Contract**: a fake loader with scripted states and a fake clock; no network.

### Success Criteria:

#### Automated Verification:

- Display tests pass: `uv run pytest tests/test_display.py`
- Full suite passes: `uv run pytest`
- App starts and `/healthz` answers with the stub transport: `uv run python -m spotify_to_led.main`, then `curl -fsS http://127.0.0.1:8000/healthz`

#### Manual Verification:

- On the laptop with the PNG stub and Spotify configured, `var/frames/latest.png` shows the playing cover and changes on skip
- With the laptop's network cut for over 30 s while a cover shows, `latest.png` gains the marker, and loses it after reconnecting

---

## Phase 4: Pi rollout and docs

### Overview

Promote to the Pi, verify the slice on the real board, and close S-04's open questions in the
foundation documents.

### Changes Required:

#### 1. Rollout

**File**: none (`scripts/deploy.sh`)

**Intent**: Deploy with the existing script and confirm the service and the unattended run
continue (`pm2 ls`, `/healthz`).

**Contract**: no deploy changes.

#### 2. Hardware verification record

**File**: `context/changes/artwork-on-the-board/verification.md` (new)

**Intent**: Record the bake-off outcome, the NFR-01 timings, the marker observation, and the log
lines that show them, as earlier slices did.

**Contract**: follows `context/archive/2026-11-08-unattended-idle-board/verification.md` in shape.

#### 3. Foundation docs

**File**: `context/foundation/prd.md`, `context/foundation/roadmap.md`, `CLAUDE.md`

**Intent**: Mark OQ3 resolved (pointing at FR-007's new bar) and OQ5 resolved by the API in the
PRD and roadmap. Record in the roadmap that S-04 delivered FR-018's marker for a showing cover, so
S-06 narrows to the no-cover case (still blocked on OQ2), the full display rule and the marker's
final legibility sign-off. Update CLAUDE.md's description of the service, which says cover
rendering and the marker are not built.

**Contract**: edits in place under the existing sections; no new sections.

### Success Criteria:

#### Automated Verification:

- Deploy succeeds: `scripts/deploy.sh` exits 0 and `/healthz` answers on the Pi
- Full suite passes on the deployed commit: `uv run pytest`

#### Manual Verification:

- Ten track skips on the Pi each change the board's cover within 5 s (NFR-01), stopwatch or log timestamps
- A podcast episode shows its own artwork
- Cutting the Pi's internet for over 30 s while a cover shows puts the marker on it, and it clears within a few seconds of the connection returning
- Pausing keeps the cover; stopping playback returns to idle
- Docs updated: FR-007 bar, OQ3/OQ5 resolved, S-06 narrowed, CLAUDE.md current

---

## Testing Strategy

### Unit Tests:

- Renderer output shape and mode for every size and source mode; marker region only.
- Artwork selection over width variants and missing data.
- Loader states, retry backoff, cancellation on URL change, clean shutdown.
- The display rule table, including the 30 s boundary and the changeover.

### Integration Tests:

- `run_display` with a fake board, fake loader, fake clock: item change, pending, failure,
  recovery, reconnect during changeover, stale marker on and off.

### Manual Testing Steps:

1. Bake-off on the board; pick the treatment; write FR-007's bar.
2. Laptop stub: play, skip, cut the network for 30 s, restore.
3. Pi: ten skips timed, a podcast, network cut for 30 s, pause, stop.

## Performance Considerations

NFR-01's 5 s runs from item change to board: up to 3 s for the reader poll, up to 1 s for the
display sample, then the download, render and push. Downloading the ~64 px image and a bounded
fetch timeout keep the download inside what is left. Rendering a 64 px image is negligible.

## Migration Notes

None: no stored state is added. Rolling back is the documented `git checkout <sha>` route in
`infrastructure.md`.

## References

- Frame brief: `context/changes/artwork-on-the-board/frame.md`
- Display rule and loop: `src/spotify_to_led/display.py:68-122`
- Playback model: `src/spotify_to_led/spotify/playback.py:45-79,117-127`
- Lifespan wiring: `src/spotify_to_led/main.py:106-136`
- HTTP test pattern: `tests/test_spotify_reader.py:3-10,146`
- PRD: FR-007, FR-008, FR-017, FR-018, NFR-01, OQ2, OQ3, OQ5 in `context/foundation/prd.md`
- Roadmap: S-04, S-05, S-06 in `context/foundation/roadmap.md`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Rendering candidates, marker and bake-off

#### Automated

- [x] 1.1 Renderer and marker tests pass: `uv run pytest tests/test_rendering.py tests/test_frames.py` — ee27439
- [x] 1.2 Full suite passes: `uv run pytest` — ee27439
- [x] 1.3 The bake-off script runs against the PNG stub on the laptop and writes the contact sheet — ee27439

#### Manual

- [ ] 1.4 Bake-off run on the Pi's board (service stopped, then restarted with `pm2 start`), judged from desk distance
- [ ] 1.5 Owner picks the treatment and writes it, plus a falsifiable acceptance bar, into PRD FR-007
- [ ] 1.6 The marker is visible over every cover in the set from across the room, or its constants are tuned until it is

### Phase 2: Cover pipeline

#### Automated

- [ ] 2.1 Cover tests pass: `uv run pytest tests/test_covers.py`
- [ ] 2.2 Full suite passes: `uv run pytest`
- [ ] 2.3 No references to the deleted candidates remain: `grep -rn` for their names in `src/` `scripts/` `tests/` returns nothing

### Phase 3: Display rule and loop

#### Automated

- [ ] 3.1 Display tests pass: `uv run pytest tests/test_display.py`
- [ ] 3.2 Full suite passes: `uv run pytest`
- [ ] 3.3 App starts and `/healthz` answers with the stub transport: `uv run python -m spotify_to_led.main`, then `curl -fsS http://127.0.0.1:8000/healthz`

#### Manual

- [ ] 3.4 On the laptop with the PNG stub and Spotify configured, `var/frames/latest.png` shows the playing cover and changes on skip
- [ ] 3.5 With the laptop's network cut for over 30 s while a cover shows, `latest.png` gains the marker, and loses it after reconnecting

### Phase 4: Pi rollout and docs

#### Automated

- [ ] 4.1 Deploy succeeds: `scripts/deploy.sh` exits 0 and `/healthz` answers on the Pi
- [ ] 4.2 Full suite passes on the deployed commit: `uv run pytest`

#### Manual

- [ ] 4.3 Ten track skips on the Pi each change the board's cover within 5 s (NFR-01), stopwatch or log timestamps
- [ ] 4.4 A podcast episode shows its own artwork
- [ ] 4.5 Cutting the Pi's internet for over 30 s while a cover shows puts the marker on it, and it clears within a few seconds of the connection returning
- [ ] 4.6 Pausing keeps the cover; stopping playback returns to idle
- [ ] 4.7 Docs updated: FR-007 bar, OQ3/OQ5 resolved, S-06 narrowed, CLAUDE.md current
