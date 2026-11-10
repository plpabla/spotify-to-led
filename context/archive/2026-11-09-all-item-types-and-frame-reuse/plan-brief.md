# Every item type and frame reuse — Plan Brief

> Full plan: `context/changes/all-item-types-and-frame-reuse/plan.md`

## What & Why

Roadmap S-05 (FR-010, FR-017). The board should show artwork for any item type, including
podcast episodes, show a generic symbol only for items that genuinely have no artwork, and
never download or render the same artwork twice, even across reboots, which FR-014 makes a
normal event.

## Starting Point

S-04 shows covers through a `CoverLoader` that holds only the wanted cover in memory, so a
cover seen earlier is downloaded again. A green play-triangle placeholder stands for three
different cases: cover still downloading, download failed, and no artwork at all. An episode
with no images of its own gets no artwork, although its show has some.

## Desired End State

Episodes fall back to their show's art. Ads, local files and withheld items show a vinyl-disc
symbol at once; the placeholder remains only for artwork that exists but is not on hand. Every
rendered cover is kept at `var/covers/<board size>/<image-id>.png`, so a cover seen before,
in this run or before a restart or reboot, is on the board on the next sample with no
download, and the log says `cover ready from cache`.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) |
| --- | --- | --- |
| Placeholder vs symbol | Symbol only for no artwork; placeholder kept for first and failed downloads | A CDN failure must never claim "this item has no art", which FR-010 reserves for genuinely art-less items. |
| Symbol look | Vinyl disc (ring with centre hole), neutral colour brighter than idle | Says "music, no picture" and differs in shape and brightness from the idle note and the play triangle. |
| Episode without own images | Fall back to `show.images` | Honours "symbol only when no artwork exists at all", and it's what Spotify's apps show. |
| Cache key | Image id from `i.scdn.co/image/<id>`, in a per-board-size directory | The id is shared by every track of an album, leaves room for later `-processed` variants, and the size directory stops a 32 px frame being served to a 64 px board. |
| URL without an id | Shown but not cached | Rare, and one naming scheme beats a hash fallback. |
| Cache hit timing | Synchronous in `want`, before any download | A few-KB read is ~1 ms, and a known cover then skips the changeover wait entirely. |
| Disk failures | Corrupt file = miss with WARNING; write error = ERROR, carry on | The token store's pattern: the cache can cost reuse, never the cover. |

## Scope

**In scope:**
- Episode → show artwork fallback in the parser
- `frames.no_artwork` and a `NO_ARTWORK` presentation in the display rule
- `frame_cache` module, `CoverLoader` integration, `COVER_CACHE_DIR` setting
- Hardware verification on the Pi; CLAUDE.md, PRD FR-017 note, infrastructure.md

**Out of scope:**
- Staleness marking of the symbol or the placeholder, and OQ2's blind-with-no-cover case (S-06)
- Caching originals, eviction, size limits, variant suffixes (S-08), hash keys for id-less URLs
- Cleaning up old board-size directories; cache state on the status page (S-07)

## Architecture / Approach

The display rule decides `NO_ARTWORK` from the item alone (empty `artwork`), before the
existing loader-driven cases. `CoverLoader.want(url)` asks `FrameCache.load(url)` first; a hit
is `READY` at once, a miss downloads as today and `FrameCache.store`s the unmarked render
(temp file plus `os.replace`). The key is the URL's image id; nothing else from the URL
reaches a path.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Every item type gets its artwork or the symbol | Show-art fallback, vinyl-disc frame, `NO_ARTWORK` presentation | Disc may read as a plain "O" at 32 px; tuned on the board in phase 3 |
| 2. Rendered frames persist on disk | `var/covers/<size>/<id>.png`, loader hit/miss paths | A bad cache file must be a miss, never a crash or a wrong frame |
| 3. Prove it on the Pi and update the docs | Legibility verdict, cache reuse across restart and cold boot, docs | Producing a no-artwork item on demand (local file in the desktop app) |

**Prerequisites:** S-04 done (it is); the Pi and the board available for phase 3.
**Estimated effort:** ~2–3 sessions across 3 phases.

## Open Risks & Assumptions

- Assumes Spotify cover URLs keep the `https://i.scdn.co/image/<id>` shape; any that don't
  still show, just uncached.
- The symbol's legibility (OQ4 for FR-010) is decided only on the real board; its colour and
  shape may be tuned there.
- During a changeover from an art-less item to one whose cover is downloading, the symbol
  stays up for that few seconds (the existing changeover rule, unchanged).

## Success Criteria (Summary)

- Any item with artwork, podcasts included, shows it; only art-less items show the disc.
- A cover seen once is never downloaded again, including after `pm2 restart` and a cold boot.
- `uv run pytest` passes, and the owner signs off the symbol on the board.
