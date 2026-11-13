# Show the plain cover while the AI-enhanced version is generated — Implementation Plan

## Overview

With AI-enhanced covers switched on (FR-019), a cover whose enhanced frame is being generated
shows the playing placeholder today, for about 9 s on the Pi and a minute or more through the
enhancer's retries. This change shows the item's plain cover instead, with a small green dot in
the top-left corner saying that an enhanced version is on its way. When the enhanced frame is
ready it replaces the plain one; when generation fails, gives up or is paused, the plain cover
stays and the dot goes.

## Current State Analysis

- The loader already has the plain frame in hand whenever it reports `GENERATING`: the state
  is entered only after the plain frame is loaded from the cache or downloaded and stored
  (`src/spotify_to_led/covers.py:259-266`, `:339-349`), and it is held in `self._plain`. But the
  shared `GENERATING` constant carries no frame (`covers.py:124`), so the display cannot use it.
- The display rule maps every non-`READY`, non-`PENDING` state, `GENERATING` included, to
  `PLACEHOLDER` (`src/spotify_to_led/display.py:217-219`), as the module docstring decided
  (`display.py:27-32`).
- The loop retains a frame only from a ready cover ("Only a ready cover carries a frame",
  `display.py:298-301`), keyed by `(url, enhanced)`, and `_frame_for` refuses to draw a cover
  whose retained key does not match (`display.py:349-352`).
- `Presentation` is compared by value to decide whether to push (`display.py:124-136`), so a
  new field is enough to make the board redraw when it changes.
- The staleness marker is a 2x2-cell amber block in a 1 px black ring, top-right, drawn on a
  copy at display time (`src/spotify_to_led/frames.py:154-171`). Green already means "cover on
  its way" on this board (the play-triangle placeholder, `frames.py:57`; the disc is kept
  non-green for that reason, `frames.py:14-16`).
- `/status` reports the last presentation as `kind`, `url`, `stale`, `enhanced`
  (`src/spotify_to_led/main.py:499-506`), and the page's enhancement line says "the board shows
  the placeholder meanwhile" for `generating` (`src/spotify_to_led/static/index.html:182-183`).
- PRD FR-019 and the NFR-01 note say the placeholder shows while a never-seen cover generates
  (`context/foundation/prd.md:206`, `:238`).

## Desired End State

With enhancement on, an item whose enhanced frame is not cached goes on the board as:

1. the previous presentation while its plain frame downloads (unchanged), or the placeholder if
   nothing was chosen before (unchanged);
2. its plain cover with the green top-left dot as soon as the plain frame is in hand — at once
   when the plain frame is cached;
3. the enhanced cover, without the dot, when generation succeeds; or the plain cover without the
   dot when it fails, gives up or is paused.

Switching enhancement on while a plain cover is up adds the dot to the same cover instead of
flashing the placeholder. The staleness marker still goes on whatever is shown, dot or not.
`/status` and the page say that the plain cover is up with the dot while the enhanced version is
made. Verified by the unit tests below and by watching a never-seen cover on the real board.

### Key Discoveries:

- `covers.py:265` and `covers.py:349` are the two places that enter `GENERATING`; both have the
  plain frame in a local at that point.
- `display.py:298-301` already retains whatever frame the state carries; only its comment and
  the "ready cover" assumption change.
- `main.py:452-453` derives `"generating"` from `CoverStatus.GENERATING`, independent of the frame,
  so the enhancement field needs no logic change.
- Tests that encode the old behaviour: `tests/test_display.py:868-872`, the frame sequence at
  `tests/test_display.py:1056-1091`, and the comments at `tests/test_covers.py:667`, `:699`,
  `:742`.

## What We're NOT Doing

- No change to the plain path with enhancement off, or to the `PENDING` changeover rule (the
  previous presentation is still kept while the plain frame downloads).
- No change to the placeholder after a failed plain download, with or without enhancement.
- No change to the enhancer, its retries, its pause or the frame cache.
- No progress indication beyond the dot (no animation, no countdown); the board still draws one
  static frame per state.
- No dot on idle, the placeholder or the no-artwork disc — only over a plain cover that is
  being enhanced.
- No new status-page control.

## Implementation Approach

Make the frame travel with the state rather than invent a new path: `GENERATING` carries the
plain frame, the rule turns it into a cover presentation flagged `generating`, the loop retains
the frame exactly as for a ready cover, and `_frame_for` draws the dot before the staleness
marker. The glyph comes first as a pure function so its tests stand alone.

## Critical Implementation Details

- **State sequencing** — draw the generating dot first and the staleness marker second, each on
  a copy; the two sit in opposite top corners and never overlap, but the order keeps the stored
  frame and the retained frame unmarked.
- **Equality** — `CoverState` is a frozen dataclass compared by value; with a frame inside, two
  `GENERATING` states are no longer equal across covers. Nothing compares states today except
  tests, which should check `.status` and `.frame` rather than identity with a shared constant.

## Phase 1: Generating marker glyph

### Overview

Add the "enhanced version on its way" dot as a pure overlay beside the staleness marker.

### Changes Required:

#### 1. The overlay

**File**: `src/spotify_to_led/frames.py`

**Intent**: Add `with_generating_marker(frame)`, returning a copy of the frame with a 1x1-cell
green dot (one 16x16-grid cell: 1 px on 16, 2 px on 32, 4 px on 64) inside a 1 px black ring in
the top-left corner. Document it in the module docstring next to the staleness marker, including
why it is green (the placeholder's "on its way" meaning) and half the marker's size (so the two
never read alike).

**Contract**: `with_generating_marker(frame: Image.Image) -> Image.Image`, input left untouched.
New constants `GENERATING_MARKER_COLOUR` (a green dimmer than `PLAYING_COLOUR`, a starting value
tuned in Phase 3), `GENERATING_MARKER_CELLS = 1`, ring reusing `STALE_MARKER_RING_PIXELS` or its
own 1 px constant.

#### 2. Tests

**File**: `tests/test_frames.py`

**Intent**: Mirror the staleness-marker tests for the new overlay at every supported size: the
dot and ring are where and what they should be, the rest of the frame is unchanged, the input is
not mutated, and with both overlays applied neither covers the other.

**Contract**: New tests alongside `test_stale_marker_*`.

### Success Criteria:

#### Automated Verification:

- Frame tests pass: `uv run pytest tests/test_frames.py`
- Full suite passes: `uv run pytest`

**Implementation Note**: No manual gate; the dot is judged on the board in Phase 3. Continue to
Phase 2 once automated verification passes.

---

## Phase 2: Plain cover while generating

### Overview

Carry the plain frame through `GENERATING`, choose it with a `generating` flag, draw the dot,
and report it on `/status` and the page.

### Changes Required:

#### 1. Loader state

**File**: `src/spotify_to_led/covers.py`

**Intent**: Make `GENERATING` carry the plain frame, so the display can show it. Replace the
shared frameless `GENERATING` constant with states built from the plain frame at both entry
points, and update the docstrings (`CoverState`, the module docstring's enhanced-cover list) to
say the frame is set when `READY` or `GENERATING`.

**Contract**: `CoverState(CoverStatus.GENERATING, plain_frame)`; `enhanced` stays true only when
`READY`. Drop the `GENERATING` constant if nothing else uses it, and update the import in
`tests/test_display.py`.

#### 2. Presentation and rule

**File**: `src/spotify_to_led/display.py`

**Intent**: Add `generating: bool = False` to `Presentation` and map `GENERATING` to
`Presentation(COVER, url, generating=True)` (not enhanced), keeping `FAILED` on the placeholder.
`__str__` names it, e.g. `cover <url> (generating)`, so the presentation log line says so. Update
the module docstring's FR-019 bullet: a cover being enhanced shows its plain frame with the dot,
then redraws as the enhanced frame or as the plain one without the dot.

**Contract**: `Presentation.generating`; `select_presentation` branch for
`CoverStatus.GENERATING`.

#### 3. Loop and frame

**File**: `src/spotify_to_led/display.py`

**Intent**: Retain the frame whenever the state carries one (ready or generating) — fix the
comment at `display.py:298-301`. In `_frame_for`, draw `with_generating_marker` on a cover
presentation with `generating` set, then the staleness marker if stale.

**Contract**: `retained` stays `(url, enhanced, frame)`; a generating presentation matches the
plain retained entry `(url, False, frame)`.

#### 4. Status

**File**: `src/spotify_to_led/main.py`, `src/spotify_to_led/static/index.html`

**Intent**: Add `generating` to the `/status` presentation object, and change the page: the
board line says the cover is up with the "enhanced version on its way" dot, and the
`generating` enhancement line says the board shows the plain cover with a green dot meanwhile.
Update the `_enhanced_cover` docstring (`main.py:433-434`) and the module docstring at
`main.py:12`.

**Contract**: `/status` → `board.presentation.generating: bool`.

#### 5. Tests

**File**: `tests/test_display.py`, `tests/test_covers.py`, `tests/test_app.py`

**Intent**: Replace the placeholder expectations with the new behaviour and cover the cases the
decision fixes:
- the rule: `GENERATING` with a frame selects the plain cover flagged `generating`, stale or not;
- the loop sequence for a never-seen cover with the plain cached: `plain+dot`, then `enhanced`;
  and with a plain download first: previous, then `plain+dot`, then `enhanced`;
- a generation that fails or is paused: `plain+dot`, then `plain` (a redraw without the dot);
- switching on while the plain cover is up: `plain`, then `plain+dot` (no placeholder);
- a stale generating cover carries both overlays;
- loader: `GENERATING` states carry the plain frame at both entry points;
- `/status` reports `generating`.

**Contract**: Existing tests at `test_display.py:868`, `:1056-1091`, `:1126-1127` and the
comments at `test_covers.py:667`, `:699`, `:742` updated rather than duplicated.

### Success Criteria:

#### Automated Verification:

- Display, loader and app tests pass: `uv run pytest tests/test_display.py tests/test_covers.py tests/test_app.py`
- Full suite passes: `uv run pytest`
- No placeholder expectation remains for a generating cover: `grep -n "GENERATING" tests/test_display.py` shows only the new expectations

#### Manual Verification:

- Run locally with the stub transport and enhancement on; the log shows `cover <url> (generating)` then `cover <url> (enhanced)` for a new cover, and the status page's board and enhancement lines read correctly while generating

**Implementation Note**: After completing this phase and all automated verification passes,
pause for manual confirmation before Phase 3.

---

## Phase 3: Docs and Pi check

### Overview

Bring the PRD and the repo description in line, then watch it on the real board and tune the
dot's colour.

### Changes Required:

#### 1. PRD

**File**: `context/foundation/prd.md`

**Intent**: In FR-019's note (`prd.md:206`), replace "the board shows the playing placeholder,
then the enhanced frame" with the plain cover carrying a small green top-left dot until the
enhanced frame is ready, and the plain cover without the dot on failure. In the NFR-01 note
(`prd.md:238`), say the plain cover with the dot is the state within 5 s while a never-seen
cover generates, when its plain frame is on hand. Add a dated "Changed 2026-11-13
(show-basic-cover)" line as the earlier notes do.

**Contract**: FR-019 and NFR-01 notes only.

#### 2. Repository description

**File**: `CLAUDE.md`

**Intent**: Add one clause to the overview paragraph that, with AI-enhanced covers on, the
plain cover shows with a small green top-left dot while the enhanced one is made. Nothing else
in the paragraph changes.

**Contract**: The overview paragraph.

#### 3. Board check and colour

**File**: `src/spotify_to_led/frames.py` (only if the colour needs tuning)

**Intent**: Deploy to the Pi, play a cover never enhanced before with enhancement on, and judge
the dot from normal desk viewing distance; adjust `GENERATING_MARKER_COLOUR` if it is invisible
or glaring, and record the outcome in `context/changes/show-basic-cover/verification.md`.

**Contract**: `GENERATING_MARKER_COLOUR` value.

### Success Criteria:

#### Automated Verification:

- Full suite passes after any colour change: `uv run pytest`

#### Manual Verification:

- On the real board, a never-seen cover shows plain with the green dot within about 5 s, then the enhanced cover without the dot
- The dot is visible from desk distance over a dark and a bright cover, and does not read as the staleness marker
- Switching enhancement off while generating shows the plain cover without the dot; switching on over a plain cover adds the dot without a placeholder flash
- Outcome and the final colour recorded in `context/changes/show-basic-cover/verification.md`

**Implementation Note**: This phase closes the change; after the manual checks pass, the change
is ready for `/10x-impl-review`.

---

## Testing Strategy

### Unit Tests:

- `frames.with_generating_marker`: position, colours, ring, untouched rest and input, coexistence with the staleness marker, at 16, 32 and 64.
- `select_presentation`: `GENERATING` → plain cover flagged `generating`, recomputed staleness.
- `CoverLoader`: both `GENERATING` entry points carry the plain frame.

### Integration Tests:

- `run_display` with a fake board and loader: the frame sequences listed in Phase 2, item 5.
- `/status` includes `presentation.generating`.

### Manual Testing Steps:

1. With enhancement on, play a track whose album was never enhanced; watch plain+dot, then enhanced.
2. Play it again; the enhanced cover appears at once, no dot.
3. Switch off during a generation; plain without dot. Switch on over a plain cover; dot appears, no placeholder.
4. Stop the network long enough for the staleness marker; both overlays show together during a generation.

## Performance Considerations

One extra BLE push per newly enhanced cover (plain+dot before the enhanced frame), well within
the board's per-item push rate; a cached enhanced cover still goes straight to the board.

## Migration Notes

None. No stored data changes; `/status` gains a field.

## References

- Prior change: `context/archive/2026-11-11-ai-enhanced-covers/plan-brief.md`
- Loader `GENERATING` entry points: `src/spotify_to_led/covers.py:265`, `:349`
- Display rule: `src/spotify_to_led/display.py:211-219`
- Staleness marker to mirror: `src/spotify_to_led/frames.py:154-171`
- PRD: `context/foundation/prd.md:203-209`, `:237-238`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Generating marker glyph

#### Automated

- [x] 1.1 Frame tests pass: `uv run pytest tests/test_frames.py` — a18d1d2
- [x] 1.2 Full suite passes: `uv run pytest` — a18d1d2

### Phase 2: Plain cover while generating

#### Automated

- [x] 2.1 Display, loader and app tests pass — e483147
- [x] 2.2 Full suite passes: `uv run pytest` — e483147
- [x] 2.3 No placeholder expectation remains for a generating cover — e483147

#### Manual

- [ ] 2.4 Local run with the stub transport shows the generating then enhanced log lines and correct status-page text

### Phase 3: Docs and Pi check

#### Automated

- [ ] 3.1 Full suite passes after any colour change: `uv run pytest`

#### Manual

- [ ] 3.2 On the real board, a never-seen cover shows plain with the green dot, then the enhanced cover
- [ ] 3.3 The dot is visible over dark and bright covers and does not read as the staleness marker
- [ ] 3.4 Switching off while generating and on over a plain cover behave as specified
- [ ] 3.5 Outcome and final colour recorded in verification.md
