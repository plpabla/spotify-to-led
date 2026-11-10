# Every item type and frame reuse Implementation Plan

## Overview

Roadmap S-05 (FR-010, FR-017, US-01). The board shows artwork for any playing item type,
including podcast episodes that carry only their show's art, and a new generic no-artwork
symbol (a vinyl disc) only for items that genuinely have no artwork, such as ads and local
files. Every rendered cover is kept on disk under the image's own id, so a cover that has
been on the board once is never downloaded or rendered again, across restarts and reboots.

## Current State Analysis

- Episodes are already requested (`additional_types=track,episode`, `spotify/reader.py:53`)
  and parsed from `item.images` (`spotify/playback.py:117-120`). An episode whose own
  `images` is empty gets no artwork, although its `show.images` would have some.
- Ads arrive as `item: null` and become a `PlayingItem` with `artwork=()`
  (`spotify/playback.py:108-111`); a local file has an empty `album.images`. Both select the
  playing placeholder today (`display.py:155-156`).
- The playing placeholder (`frames.playing_placeholder`, a green triangle) currently stands
  for three cases: cover not downloaded yet, download failed, and no artwork at all.
  `frames.py:9-12` says S-05's symbol replaces it.
- In `select_presentation`, `cover is None` means both "no loader" and "no URL to want"
  (`display.py:155`), so the no-artwork case cannot be told apart from the no-loader case
  without looking at the item.
- `CoverLoader` holds only the wanted cover, in memory (`covers.py:135-219`). Wanting the same
  URL again is a no-op, so consecutive tracks of one album already reuse the cover, but
  returning to an album seen earlier downloads it again (S-04 `verification.md:60`).
- The token store is the house pattern for on-disk state: temp file in the same directory,
  `os.replace`, and an OSError logged and survived (`spotify/token_store.py:52-70`,
  `spotify/reader.py:228-238`). `var/` is gitignored.

## Desired End State

- A playing or paused episode with no images of its own shows its show's cover.
- An item with no artwork at all (ad, local file, withheld item) shows the vinyl-disc symbol,
  immediately and never marked; a cover that exists but is not on hand (first download,
  failed download) still shows the green placeholder.
- Every cover rendered from an `https://i.scdn.co/image/<id>` URL is written to
  `var/covers/<board size>/<id>.png`. Wanting that URL again, in this process or after a
  restart or reboot, makes the cover `READY` on the same display sample with no download, and
  the log says `cover ready from cache: <url>`.
- A URL without such an id is still shown, just not cached.
- `uv run pytest` passes; on the Pi the symbol is judged legible and distinct, and a cover
  comes back from the cache after `pm2 restart` and after a cold boot.

### Key Discoveries:

- `select_artwork` returns `None` exactly when `item.artwork` is empty (`covers.py:98-99`),
  so "no artwork" is a property of the item, decidable without the loader.
- The 16x16 design grid and whole-pixel scaling used by the idle note (`frames.py:62-101`)
  are the pattern for the new symbol.
- `with_stale_marker` draws on a copy (`frames.py:118-135`), so the frame the loader holds,
  and therefore the one it stores, is always the unmarked render.
- Spotify image ids encode the image size (the 640, 300 and 64 px variants have different
  ids), and 16, 32 and 64 px boards all select the same 64 px image, which is why the board
  size is part of the cache path.

## What We're NOT Doing

- No staleness marker on the symbol or the placeholder, and no answer to OQ2 (blind with no
  cover): both stay S-06's.
- No caching of originals, no eviction, no size limit, no expiry (FR-017 as decided).
- No `-processed` or other variant suffixes yet; the filename leaves room for them (S-08).
- No cache for URLs without an `i.scdn.co/image/<id>` shape, and no hash-based fallback key.
- No automatic cleanup of old board-size directories.
- No change to the changeover rule: while a new item's cover downloads, the previous
  presentation stays, the symbol included.
- No status-page reporting of cache state (S-07).

## Implementation Approach

Two code phases, then a hardware phase. Phase 1 is purely about what the board chooses to
show: the parser fallback, a new frame and a new presentation kind, all testable without disk
or network. Phase 2 adds a small `frame_cache` module and lets `CoverLoader` consult it
synchronously in `want` before starting a download, and write to it after a successful
render. Phase 3 proves both on the real board and brings the docs in line.

## Critical Implementation Details

- **Order in the display rule**: the no-artwork check must come before the no-loader check,
  or an ad with no loader would select the placeholder instead of the symbol.
- **Filename safety**: the id is taken only when the whole URL matches
  `https://i.scdn.co/image/<[0-9A-Za-z]+>`, so nothing from a URL can put a dot or slash into
  a path under `var/covers/`.
- **A cache hit is synchronous**: `want` reads the PNG (a few hundred bytes to a few KB) on
  the event loop and sets `READY` before returning, so the display picks the cover up on the
  same sample and the changeover wait disappears for a cover seen before.

## Phase 1: Every item type gets its artwork or the symbol

### Overview

Episodes fall back to their show's images, items with no artwork select a new `NO_ARTWORK`
presentation drawn as a vinyl disc, and the placeholder keeps only the "artwork exists but is
not on hand" cases.

### Changes Required:

#### 1. Episode artwork falls back to the show

**File**: `src/spotify_to_led/spotify/playback.py`

**Intent**: Honour FR-010's "symbol only when no artwork exists at all" for an episode that
carries no images of its own but whose show does.

**Contract**: In `parse_currently_playing`, for `kind == "episode"`, when the episode's own
images parse to no artwork, `artwork` comes from `show.images`. An episode with its own images
keeps them (the existing test stays true). Module docstring notes the fallback.

#### 2. The generic no-artwork symbol

**File**: `src/spotify_to_led/frames.py`

**Intent**: Add the FR-010 symbol, a vinyl disc, distinct in shape from the idle note and the
play triangle and in brightness from both.

**Contract**: `no_artwork(size: int) -> Image.Image` returns an RGB `(size, size)` frame: a
ring with a centre hole, designed on the 16x16 grid and scaled by whole pixels like `idle`,
centered, in one neutral colour `NO_ARTWORK_COLOUR` brighter than `IDLE_COLOUR` and not green.
Module docstring lists it among the frames and narrows `playing_placeholder`'s description to
"artwork exists but is not on hand" (no longer "replaced by S-05").

#### 3. A `NO_ARTWORK` presentation in the display rule

**File**: `src/spotify_to_led/display.py`

**Intent**: Show the symbol for an item with no artwork, at once and never marked, while a
missing loader, a pending first download and a failed download keep their current behaviour.

**Contract**: `PresentationKind.NO_ARTWORK` and a module constant `NO_ARTWORK`. In
`select_presentation`, after the idle check: a showing item (`PLAYING` or `PAUSED`) that is
`None` or has empty `artwork` returns `NO_ARTWORK`, before the `cover is None` →
`PLACEHOLDER` check. `NO_ARTWORK` is never stale. `_frame_for` draws it with
`frames.no_artwork(size)`. The rule list in the module docstring gains the case.

#### 4. Tests

**Files**: `tests/test_playback_parse.py`, `tests/test_frames.py`, `tests/test_display.py`

**Intent**: Pin the new cases and update the one that changes meaning.

**Contract**:
- Parse: an episode with empty own images takes `show.images`; an episode with neither has no
  artwork; an episode with its own images ignores the show's.
- Frames: `no_artwork` is RGB `(size, size)` for 16, 32 and 64 and passes `validate_frame`;
  it differs from `idle` and `playing_placeholder`; its brightest channel exceeds
  `IDLE_MAX_CHANNEL`.
- Display: an item without artwork selects `NO_ARTWORK` for `PLAYING` and `PAUSED`, whatever
  the previous presentation; it is not marked after more than 30 s of failing reads; a failed
  download and a no-loader item still select `PLACEHOLDER`; the loop pushes the symbol frame.
  `test_an_item_without_artwork_wants_no_cover_and_selects_the_placeholder` is updated to the
  symbol (renamed accordingly).

### Success Criteria:

#### Automated Verification:

- Full suite passes: `uv run pytest`
- New parse, frames and display tests pass: `uv run pytest tests/test_playback_parse.py tests/test_frames.py tests/test_display.py`

#### Manual Verification:

- The symbol rendered by the stub transport (`var/frames`) at 32 px reads as a disc with a hole, distinct from the idle note and the play triangle

**Implementation Note**: After completing this phase and all automated verification passes,
pause here for manual confirmation from the human that the manual testing was successful
before proceeding to the next phase.

---

## Phase 2: Rendered frames persist on disk

### Overview

A `frame_cache` module stores each rendered cover at `var/covers/<size>/<image-id>.png`, and
`CoverLoader` answers a wanted URL from it before downloading.

### Changes Required:

#### 1. The frame cache

**File**: `src/spotify_to_led/frame_cache.py` (new)

**Intent**: FR-017's store: one rendered frame per image id and board size, unbounded,
persistent, never fatal to the display.

**Contract**:
- `image_id(url: str) -> str | None`: the id when `url` fully matches
  `^https://i\.scdn\.co/image/([0-9A-Za-z]+)$`, else `None`.
- `FrameCache(root: Path, size: int)`; entries live at `root / str(size) / f"{id}.png"`.
- `load(url) -> Image.Image | None`: `None` for an id-less URL or a missing file; a file
  that fails to open or decode, is not RGB-convertible, or is not `(size, size)` logs a
  WARNING and is a miss (the next successful download overwrites it). An OSError reading it
  is a miss with a WARNING. The image is fully loaded before the file closes.
- `store(url, frame) -> None`: no-op for an id-less URL; otherwise creates the directory,
  writes a PNG to a temp file in the same directory and `os.replace`s it into place. An
  OSError is logged at ERROR with the path and the error type and swallowed. No fsync: a lost
  entry only costs one more download.
- Module docstring: the key, the layout, why the board size is in the path, the accepted
  consequence that a renderer change means a new directory or a wiped cache (FR-017), and the
  room left for variant suffixes.

#### 2. The loader consults the cache

**File**: `src/spotify_to_led/covers.py`

**Intent**: A cover seen before is ready without a download; a downloaded cover is kept.

**Contract**: `CoverLoader(client, size, *, cache: FrameCache | None = None, sleep=...)`.
In `want(url)`, for a new non-`None` URL, a cache hit sets the state to `READY` with the
cached frame, logs INFO `cover ready from cache: <url>`, and starts no task; a miss starts
`_load` as today. After a successful render, `_load` stores the frame in the cache. Without a
cache, behaviour is unchanged. Module docstring replaces "a cache across items is S-05's".

#### 3. Configuration and wiring

**Files**: `src/spotify_to_led/config.py`, `src/spotify_to_led/main.py`

**Intent**: Make the cache location a setting, like the token file, and give the loader its
cache.

**Contract**: `Settings.cover_cache_dir: Path = Path("var/covers")` (env `COVER_CACHE_DIR`).
`lifespan` builds `FrameCache(settings.cover_cache_dir, settings.board_size)` and passes it as
`cache=` to `CoverLoader`. The `main` module docstring mentions that covers are kept on disk.

#### 4. Tests

**Files**: `tests/test_frame_cache.py` (new), `tests/test_covers.py`, `tests/test_config.py`

**Intent**: Pin the key, the round trip, every way a cache file can be bad, and the loader's
hit and miss paths, all on `tmp_path`.

**Contract**:
- `image_id`: a real-shaped scdn URL yields its id; another host, an extra path segment, a
  query string, a trailing slash and non-alphanumeric ids yield `None`.
- Round trip: `store` then `load` returns identical pixels, from `root/32/<id>.png`; no temp
  files remain.
- Misses: missing file; corrupt bytes (WARNING logged); a valid PNG of the wrong size; an
  id-less URL (and `store` writes nothing for it). A corrupt file is overwritten by the next
  `store`.
- A 64 px cache does not see a 32 px cache's entries.
- `store` into an unusable root (e.g. a path that is a file) logs ERROR and does not raise.
- Loader: a hit is `READY` immediately after `want`, with zero requests to the image server;
  a miss downloads once and writes the entry; a second loader on the same cache (a restart)
  gets `READY` with no request; an id-less URL downloads every time it is wanted again.
- Config: `cover_cache_dir` defaults to `var/covers` and is read from `COVER_CACHE_DIR`.

### Success Criteria:

#### Automated Verification:

- Full suite passes: `uv run pytest`
- Cache and loader tests pass: `uv run pytest tests/test_frame_cache.py tests/test_covers.py tests/test_config.py`

#### Manual Verification:

- On the laptop with the stub transport, playing a cover writes `var/covers/32/<id>.png`, and restarting the service shows `cover ready from cache` for that cover with no download line

**Implementation Note**: After completing this phase and all automated verification passes,
pause here for manual confirmation from the human that the manual testing was successful
before proceeding to the next phase.

---

## Phase 3: Prove it on the Pi and update the docs

### Overview

Deploy to the Pi, judge the symbol on the real board (OQ4 for FR-010), prove cache reuse
across a restart and a cold boot, and bring the living docs in line. Record the evidence in
`context/changes/all-item-types-and-frame-reuse/verification.md`.

### Changes Required:

#### 1. Living docs

**Files**: `CLAUDE.md`, `context/foundation/prd.md`, `context/foundation/infrastructure.md`

**Intent**: Keep the repository's description and the decisions true.

**Contract**:
- `CLAUDE.md`: the overview mentions the vinyl-disc symbol for items without artwork, the
  placeholder's narrowed role, and the on-disk cover cache; the "not built yet" sentence drops
  the frame cache and the generic symbol.
- PRD FR-017 cache-shape note: records that the key is the image id from the URL, under a
  per-board-size directory, with id-less URLs not cached (dated 2026-11-09 or the
  implementation date).
- `infrastructure.md`: one sentence that `var/covers/` sits beside the token file, is
  gitignored, and that `git clean -fdx` wiping it only costs re-downloads.

#### 2. Verification record

**File**: `context/changes/all-item-types-and-frame-reuse/verification.md` (new)

**Intent**: The evidence that S-05's outcome holds on hardware, in the shape of the earlier
slices' verification files.

**Contract**: Dated observations with the relevant `pm2 logs` lines for each manual check
below, and the owner's legibility verdict on the symbol, including any tuning of
`NO_ARTWORK_COLOUR` or the disc's shape.

### Success Criteria:

#### Automated Verification:

- Full suite passes on the Pi: `uv run pytest`

#### Manual Verification:

- A podcast episode shows its own or its show's artwork on the board
- A local file (or an ad) shows the vinyl-disc symbol within about 5 s, and the owner judges it legible and distinct from idle, the placeholder and the stale marker from desk distance
- Going from album A to album B and back to A shows A's cover from the cache (log `cover ready from cache`) with no download line
- After `pm2 restart spotify-to-led`, the playing cover comes back from the cache with no download line
- After a cold boot of the Pi, the playing cover comes back from the cache with no download line
- `ls var/covers/32/` on the Pi lists one `<id>.png` per cover seen

**Implementation Note**: After completing this phase and all automated verification passes,
pause here for manual confirmation from the human that the manual testing was successful.

---

## Testing Strategy

### Unit Tests:

- Parser: the episode fallback and its boundaries.
- Frames: shape, size, validity and distinctness of `no_artwork`.
- Display rule: `NO_ARTWORK` against every status, previous presentation and staleness case
  that touches it; the placeholder's remaining cases.
- Frame cache: key extraction, round trip, every bad-file case, unwritable root, size
  isolation, no temp files left.
- Loader: hit without a request, miss then write, restart via a second loader, id-less URL.

### Integration Tests:

- The existing display-loop tests with a loader keep passing; one loop test pushes the symbol
  frame for an item without artwork.

### Manual Testing Steps:

1. Play a podcast episode; the board shows its art.
2. Play a local file in the Spotify desktop app; the board shows the disc within about 5 s.
3. Play album A, then album B, then A again; `pm2 logs` shows `cover ready from cache` for A.
4. `pm2 restart spotify-to-led` while A plays; the cover returns from the cache.
5. Cut the Pi's power and restore it while A plays; the cover returns from the cache.

## Performance Considerations

A cache hit is a synchronous read of a PNG of a few KB on the event loop, about a millisecond
on the Pi's SD card, and it removes a network download from the item-change path, so NFR-01
gets more margin, not less. Entries are unbounded by decision; at a few KB each, ten thousand
covers take tens of MB.

## Migration Notes

None. The cache starts empty, each cover is downloaded once as today, and from then on it is
reused. Deleting `var/covers/` at any time is safe.

## References

- Roadmap: S-05 in `context/foundation/roadmap.md`
- PRD: FR-010 (`context/foundation/prd.md:181`), FR-017 and its cache-shape note
  (`prd.md:189-192`), OQ4
- Previous slice: `context/archive/2026-11-09-artwork-on-the-board/plan.md`,
  `verification.md:60`
- Atomic write pattern: `src/spotify_to_led/spotify/token_store.py:52-70`
- Glyph pattern: `src/spotify_to_led/frames.py:62-101`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Every item type gets its artwork or the symbol

#### Automated

- [x] 1.1 Full suite passes: `uv run pytest` — 7cf6607
- [x] 1.2 New parse, frames and display tests pass: `uv run pytest tests/test_playback_parse.py tests/test_frames.py tests/test_display.py` — 7cf6607

#### Manual

- [x] 1.3 The symbol rendered by the stub transport (`var/frames`) at 32 px reads as a disc with a hole, distinct from the idle note and the play triangle — 7cf6607

### Phase 2: Rendered frames persist on disk

#### Automated

- [x] 2.1 Full suite passes: `uv run pytest`
- [x] 2.2 Cache and loader tests pass: `uv run pytest tests/test_frame_cache.py tests/test_covers.py tests/test_config.py`

#### Manual

- [ ] 2.3 On the laptop with the stub transport, playing a cover writes `var/covers/32/<id>.png`, and restarting the service shows `cover ready from cache` for that cover with no download line

### Phase 3: Prove it on the Pi and update the docs

#### Automated

- [ ] 3.1 Full suite passes on the Pi: `uv run pytest`

#### Manual

- [ ] 3.2 A podcast episode shows its own or its show's artwork on the board
- [ ] 3.3 A local file (or an ad) shows the vinyl-disc symbol within about 5 s, and the owner judges it legible and distinct from idle, the placeholder and the stale marker from desk distance
- [ ] 3.4 Going from album A to album B and back to A shows A's cover from the cache (log `cover ready from cache`) with no download line
- [ ] 3.5 After `pm2 restart spotify-to-led`, the playing cover comes back from the cache with no download line
- [ ] 3.6 After a cold boot of the Pi, the playing cover comes back from the cache with no download line
- [ ] 3.7 `ls var/covers/32/` on the Pi lists one `<id>.png` per cover seen
