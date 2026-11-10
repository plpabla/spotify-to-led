<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Every item type and frame reuse

- **Plan**: context/changes/all-item-types-and-frame-reuse/plan.md
- **Scope**: Full plan (completed phases only; phase 3 has two open manual rows, 3.3 and 3.6, and its doc changes were read for drift but the phase is not counted as reviewed)
- **Reviewed phases**: 1, 2
- **Date**: 2026-11-10
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical, 1 warning, 5 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | WARNING |

Automated criteria re-run: `uv run pytest` 327 passed, 1 skipped; phase 1 scoped 120 passed; phase 2 scoped 71 passed. Every planned change is a MATCH, every test-contract bullet has a named test, and nothing in "What We're NOT Doing" was crossed. The extras (`tests/test_app.py` pointing `COVER_CACHE_DIR` at `tmp_path`, the `frames._glyph` helper, the roadmap status flips) are justified.

## Findings

### F1 — A cache entry with a huge header wedges the loader on that cover

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/frame_cache.py:59-83, src/spotify_to_led/covers.py:169-182
- **Detail**: `PIL.Image.DecompressionBombError` subclasses `Exception`, not `OSError`/`ValueError`/`SyntaxError`, and `Image.open` raises it from the header of a PNG claiming more than ~179M pixels. Reproduced: a 66-byte PNG with a 20000x20000 IHDR at `32/<id>.png` makes `load()` raise instead of missing. `want` has already set `self._url = url` with state `PENDING` and starts no task, so every later `want(url)` is the same-URL no-op: no download ever runs, the entry is never overwritten, and it recurs after every restart. While that item plays the board keeps the previous presentation, which may be the previous item's cover, unmarked while reads succeed. Needs a tampered or corrupt local file, so unlikely, but the module promises "an entry that cannot be read … is a miss".
- **Fix**: Catch `Image.DecompressionBombError` alongside the other classes in `FrameCache.load` (a miss with a WARNING), and add a test with a bomb-header entry that also checks a `CoverLoader` on that cache downloads and rewrites the entry.
- **Decision**: FIXED — `Image.DecompressionBombError` caught in `FrameCache.load`; tests `test_an_entry_whose_header_claims_a_huge_image_is_a_miss` and `test_a_bad_cache_entry_is_downloaded_again_and_rewritten[bomb header]` (break-checked red without the fix)

### F2 — Three prose descriptions of the placeholder are slightly off

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: src/spotify_to_led/display.py:19-21, src/spotify_to_led/main.py:12, CLAUDE.md:3
- **Detail**: The display.py rule list says "any item when there is no loader shows the playing placeholder", but an item without artwork shows the symbol even without a loader. main.py's docstring still says the display shows "the playing placeholder while there is no cover to show" and never mentions the symbol. CLAUDE.md says the placeholder stands in "while an item's cover is still downloading", but during a download the previous presentation stays; the placeholder shows only before anything was chosen, after a failed download, or with no loader.
- **Fix**: Reword the three sentences to match the code: "any item with artwork when there is no loader"; mention the symbol in main.py; "before its first cover is ready or after its download failed" in CLAUDE.md.
- **Decision**: FIXED — display.py rule list, main.py docstring and CLAUDE.md reworded to match the code

### F3 — After an art-less item, the disc stays up while the next item's cover downloads

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/display.py:166-173
- **Detail**: Ad (`previous = NO_ARTWORK`) → item B pending: the changeover rule keeps the disc, saying "no artwork" for an item that has some, for up to the 4 s fetch timeout (or until a download failure switches to the placeholder). The plan accepted exactly this ("No change to the changeover rule … the symbol included"; brief, Open Risks), and no path asks `_frame_for` for an unretained cover. The display.py docstring names idle as kept through a changeover but not the symbol.
- **Fix**: Add "and the no-artwork symbol" next to "That includes idle" in the display.py docstring, so the accepted behaviour is written where the rule lives.
- **Decision**: FIXED — the display.py rule docstring now says the no-artwork disc is kept through a changeover, as S-05 accepted

### F4 — No sweep of leftover temp files, unlike the token store

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/spotify_to_led/frame_cache.py:86-109 (vs src/spotify_to_led/spotify/token_store.py:56-59)
- **Detail**: `store` unlinks its temp on any exception, but a power cut between `save` and `os.replace` leaves `.<id>.png.<hex>.tmp` forever. `token_store.save` sweeps `.<name>.*.tmp` first. Each orphan is a few KB and holds nothing secret, and FR-014 makes power cuts routine, so they can accumulate slowly.
- **Fix**: Before writing in `store`, remove leftover `.<id>.png.*.tmp` files for that entry, as `token_store.save` does for its own name.
- **Decision**: FIXED — `FrameCache.store` removes the entry's leftover `.<id>.png.*.tmp` files before writing; test `test_a_store_sweeps_temp_files_a_power_cut_left_behind` (break-checked)

### F5 — No loader-level test for a bad cache entry

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: tests/test_covers.py (frame-cache section)
- **Detail**: "A corrupt entry is overwritten by the next store" is tested only on `FrameCache`. Nothing checks that a `CoverLoader` with a corrupt entry falls through to a download, ends `READY` and rewrites the entry, which is the path F1 broke for one error class.
- **Fix**: Add that loader test (with garbage bytes; the bomb case comes with F1).
- **Decision**: FIXED — with F1: `test_a_bad_cache_entry_is_downloaded_again_and_rewritten` runs on a garbage entry and on a bomb header

### F6 — Two manual checks open, and the show-art fallback was not seen on hardware

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: plan.md Progress 3.3, 3.6; verification.md
- **Detail**: 3.3 (the disc on the real board, OQ4 for FR-010) and 3.6 (cold boot) were not run, both recorded in verification.md. The disc was judged on a 10x preview only. 3.2 passed with episodes that carry their own images, so the show-art fallback is proven by unit tests only. No rubber-stamping: every ticked manual row has log evidence in verification.md.
- **Fix**: Run 3.3 and 3.6 before archiving, or accept them as not run when archiving (they carry to S-06's board session naturally).
- **Decision**: ACCEPTED — 3.3 and 3.6 stay recorded as not run in verification.md and open in Progress; owner's decision
