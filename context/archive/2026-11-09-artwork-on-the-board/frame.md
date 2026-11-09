# Frame Brief: Artwork on the board (S-04), open points and room for animation/LLM later

> Framing step before /10x-plan. This document captures what is *actually*
> at issue, separated from what was initially assumed.

## Reported Observation

S-04 (`artwork-on-the-board`) is next on the roadmap and carries open points: the 32x32
rendering treatment (Open Question 3, gates FR-007's acceptance bar), track vs album artwork
(Open Question 5), and the roadmap's note that real covers behind today's display rule become
an unmarked stale cover, which the hard rules forbid (`roadmap.md` S-04 Risk).

## Initial Framing (preserved)

- **User's stated cause or approach**: the artwork functionality should be built "open to
  changes", because animations / `.gif` files and an LLM step that regenerates the picture are
  intended later.
- **User's proposed direction**: check the open points, and verify how to structure "the whole
  functionality" so those additions do not force a rework.
- **Pre-dispatch narrowing**: both concerns weigh equally. The future picture is an *animated
  cover of the playing item*, still one presentation per item, not arbitrary content. On
  staleness: covers may land "dev-only until S-06", not relied on unattended before then.

## Dimension Map

1. **Rendering treatment (OQ3)**: FR-007 cannot be failed until a treatment and a bar exist.
2. **Track vs album artwork (OQ5)**: assumes Spotify offers both and a choice must be made.
3. **Stale-cover coupling**: `display.py` ignores `failure`, so a cover outlives failing reads
   unmarked. Assumes S-06 (blocked on OQ2) is the only way out.
4. **Display-rule seam**: `Presentation` is a payload-free enum and `frame_for` is pure and
   synchronous; a cover needs data (artwork identity) and I/O (download).
5. **Transport payload seam**: `push_frame(Image)` accepts one static RGB frame only; animation
   may need a different payload. ← initial framing ("open to changes") lands on 4–7 as one blob
6. **Render latency on the display loop**: an LLM step is slow; the loop awaits inline.
7. **Cache identity (FR-017, owned by S-05)**: renders only, originals discarded; keyed on
   artwork identity alone.

## Hypothesis Investigation

| Hypothesis | Evidence | Verdict |
| --- | --- | --- |
| 1. OQ3 is a real open point S-04 must resolve | `prd.md` OQ3: "not answerable on paper"; resolution path is rendering real covers both ways on the board. No code can settle it. | STRONG (owner, on hardware) |
| 2. OQ5 needs an owner decision | Spotify `TrackObject` has **no** `images` field; cover art exists only as `album.images` (Spotify Web API reference, via Context7). `playback.py:117-119` already reads `album.images`; episodes carry their own `images` (`playback.py:122`). There is nothing to choose for tracks. | NONE: dissolved by the API |
| 3. Stale covers block S-04 entirely | `display.py:15-16` ignores `failure`; `PlaybackSnapshot` already carries `failure` + `failure_since` (`playback.py:67-79`). FR-018 fully specifies the marker *when a cover is in hand* (corner, 30 s grace); OQ2 only blocks the *no-cover* case. The Pi runs `main` continuously under pm2 and is mid-way through the NFR-02 7-day observation, so "dev-only" means S-04 must not be promoted to the Pi before staleness is handled. | STRONG: real, but narrower than "needs S-06" |
| 4. Display rule must change shape for covers | `display.py:68-87`: `Presentation` has two payload-free members; `frame_for(presentation, size)` cannot name *which* cover. Tests pin this shape (`tests/test_display.py:119-165`). Animated-cover-per-item (narrowing answer) keeps the "one presentation per item" model; only the payload grows. | STRONG: S-04 reshapes it regardless of the future |
| 5. Animation needs a different transport payload | Pinned `idotmatrix` 0.3.0 has `gif.upload_gif_file(...)` (`.venv/.../idotmatrix/modules/gif.py:50`): the board stores and loops the GIF *itself*, max 64 frames, with a per-frame `overlay` hook. Animation is therefore one upload, not a frame stream from the loop. `BoardTransport.push_frame` + `validate_frame` (`board/transport.py:41,45-56`) admit only static RGB; the PNG stub writes only `latest.png`. | STRONG: the one seam animation actually crosses |
| 6. Slow rendering would stall the loop | `run_display` awaits `_push(... frame_for(...))` inline (`display.py:110-122`); the same loop owns link-down detection and redraw-on-reconnect. Today's frames are instant. A download (S-04) and an LLM call (later) are not; NFR-01 gives 5 s from item change to board. | STRONG: S-04 hits it first with the download |
| 7. Cache identity blocks the LLM later | FR-017 note (`prd.md:191`) and `shape-notes.md` Forward: only rendered frames kept; a pipeline change means discard everything, accepted knowingly. An LLM "regenerate the picture" step needs the original as input. Owned by S-05, not S-04. | WEAK for S-04: real, but S-05's to decide |

## Narrowing Signals

- "Animated cover of the track" keeps one presentation per playing item: the display rule's
  shape (state in, one thing to show out) survives; the reader, the connect/retry loop and the
  redraw-on-reconnect logic are untouched by either future feature.
- No prior decision about animation, GIF upload or renderer identity exists in
  `context/archive/` (searched), so nothing here contradicts an earlier ruling.
- "Dev-only until S-06" turns OQ2 into the critical path for anything reaching the Pi, unless
  the cover-in-hand marker (fully specified) is separated from the no-cover case (blocked).

## Cross-System Convention

The project's own rule (user `CLAUDE.md`, "Simplicity First": no speculative flexibility) and
F-01's precedent (one narrow `BoardTransport` protocol that made a library swap a one-file
change) both point the same way: name the seams future work will cross; don't build
generic machinery for features that aren't scoped. FR-007 / the PRD Non-Goals keep v1 to one
static frame per item.

## Reframed (or Confirmed) Problem Statement

> **The actual problem to plan around is**: S-04 introduces the first board content that
> carries data and costs time (a downloaded, rendered cover) into a display rule and transport
> built for payload-free, instant, static frames. The future animation and LLM work stress
> exactly those two points (*what kind of payload* the board receives, and *how long producing
> it takes*), so S-04 only needs to get those right, not make "the whole functionality" open.

The initial framing was half right: extensibility is a real concern, but it is concentrated, not
global. Animation crosses the transport payload seam (dimension 5); the LLM step occupies the
artwork-to-payload step and inherits its latency (dimension 6) and the cache-identity question
(dimension 7, S-05's). Everything else (reader, connect/retry, redraw on reconnect) is already
indifferent to both. Of the open points, OQ5 dissolves (tracks only have album art), OQ3 stays
an on-board owner judgement, and the staleness coupling is narrower than "wait for S-06": only
the no-cover case is blocked by OQ2.

## Confidence

**HIGH** for the reframe and the open-point findings: each rests on file:line or API-reference
evidence, and the narrowing answer removed the one ambiguity (per-item cover vs arbitrary
content). The one item no investigation can close is OQ3, which by the PRD's own terms is
decided by looking at the real board; it shapes acceptance, not structure.

## What Changes for /10x-plan

Plan S-04 around three things. First, the cover path with a named artwork-to-payload step,
kept off the display loop's critical path so the 5 s budget and link handling survive a slow
step. Second, a display rule whose presentation can carry *which* cover. Third, an explicit
decision on staleness at ship: either bring in FR-018's cover-in-hand marker or keep the Pi on
the current build. Close OQ5 in the PRD/roadmap as resolved by the API. Make an OQ3 on-board
comparison a plan step with a written bar. Leave GIF transport and cache identity out, but
record them as the seams S-05 and the post-v1 work will extend.

## References

- Source files: `src/spotify_to_led/display.py:15-16,68-87,110-122`,
  `src/spotify_to_led/board/transport.py:41-56`, `src/spotify_to_led/board/stub.py`,
  `src/spotify_to_led/spotify/playback.py:67-79,117-122`, `src/spotify_to_led/frames.py`,
  `tests/test_display.py:119-165`, `.venv/Lib/site-packages/idotmatrix/modules/gif.py:23-80`
- Docs: `context/foundation/prd.md` FR-007, FR-017 (line 191), FR-018, OQ2, OQ3, OQ5;
  `context/foundation/roadmap.md` S-04, S-05, S-06; `context/foundation/shape-notes.md`
  `## Forward: technical-roadmap`
- External: Spotify Web API reference, `TrackObject.album.images` (no track-level images)
- Related research: none (`research.md` absent)
- Investigation tasks: done inline against the ~1.7k-line source rather than via sub-agents
