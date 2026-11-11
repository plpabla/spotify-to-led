# Frame Brief: AI-enhanced covers (roadmap S-08)

> Framing step before /10x-plan. This document captures what is *actually*
> at issue, separated from what was initially assumed.

## Reported Observation

The plain area downscale (FR-007, chosen in the S-04 bake-off) is not good enough on the
32x32 panel: the owner judged none of the three bake-off treatments sufficient on its own.
Roadmap S-08 slots AI enhancement for M-2, but it is still a PRD v1 Non-Goal and a hard rule
in `CLAUDE.md` ("no AI-enhanced covers").

## Initial Framing (preserved)

- **User's stated cause or approach**: use OpenRouter for the AI step (answering S-08's blocking
  "where does the model run" unknown: hosted), with a checkbox on the web page to enable or
  disable enhancement. "Based on this checkbox the whole request should be sent", read as:
  when off, no OpenRouter request is made at all.
- **User's proposed direction**: reuse a proof of concept from another repository
  (`D:\sandbox\19_ai\openrouter`) and reference it here so it informs the plan.
- **Pre-dispatch narrowing**: the PoC proves good-looking 32x32 output; flipping the checkbox
  must change the cover currently on the board too, not only the next one.
- **Post-investigation narrowing (Step 4)**: on a never-seen cover with enhancement on, the board
  keeps the previous frame until the enhanced one is ready; the checkbox state survives restarts
  and reboots; static frames only (animation is a later change); the PoC output was judged on
  the notebook's 8x screen preview, not on the real board.

## Dimension Map

1. **Image generation** (the OpenRouter call, prompt, model, cost). The PoC proves it ←
   initial framing.
2. **Cover identity**: the display, loader and cache all identify a cover by its URL alone, so
   "enhanced or plain" is invisible to them.
3. **Runtime owner setting**: a value the page writes and the service persists. Nothing like
   that exists yet.
4. **Latency and truthfulness**: a generation call vs NFR-01 (5 s) and the "never present a
   cover as current when the service cannot vouch for it" rule.
5. **Requirements gate**: the PRD Non-Goal, the hard rule, the open milestone M-1, and the
   FR-007 path (judged on the real board).

## Hypothesis Investigation

| Hypothesis | Evidence | Verdict |
| --- | --- | --- |
| 1. Generation is the hard part | PoC `animate.py:generate_image` POSTs `https://openrouter.ai/api/v1/images` with `input_references` (cover inlined as a base64 data URL), `aspect_ratio: "1:1"`, `seed`; notebook cell 9 uses `black-forest-labs/flux-3-image-klein` at $0.009/image with `MINIATURE_PROMPT`; output is 1024x1024, downscaled to 32 px with LANCZOS (cell 10). Already solved. | WEAK as the problem (solved) |
| 2. Cover identity is URL-only | `Presentation` = kind + url + stale (`display.py:115-124`); redraw only when it changes (`display.py:265-266`). `CoverLoader.want` is a no-op for the same URL (`covers.py:155-158`); `fetch_cover` hard-wires `render_cover` (`covers.py:124`). `FrameCache` keys `<size>/<id>.png` (`frame_cache.py:111-114`); variants were anticipated as an id suffix (`frame_cache.py:16-17`). A toggle mid-track changes none of these, so nothing redraws, and both renderings collide in one cache entry. | STRONG |
| 3. No runtime setting exists | `Settings` is env-only (`config.py:39-58`); every route is GET (`main.py:182-266`); the page only `fetch`es `/status` (`index.html:174`). The checkbox is the first write route and the first owner-mutable persisted state (like `var/spotify-token.json`, but written by the page). Hard rule: no auth in service code, so the write route rests on the edge Access gate alone. | STRONG |
| 4. Latency vs truthfulness | NFR-01 (`prd.md:229`): the board shows the new item's state within 5 s. Today a PENDING cover keeps the previous presentation (`display.py:186-189`), bounded by the 4 s download timeout (`covers.py:62`). The PoC's request timeout is 300 s with backoff on 429/502 (`animate.py:_generate_image`); generation time was not measured. The chosen changeover ("previous frame until enhanced") shows the **previous item's** cover, unmarked, for as long as the generation takes, which breaks NFR-01 and is wrong-but-confident, not stale (FR-018's marker is about failed reads, `display.py:197-208`). | STRONG (direct conflict with a stated preference) |
| 5. Requirements gate | PRD Non-Goal `prd.md:286`; hard rule in `CLAUDE.md`; roadmap S-08 "needs a PRD revision" and "M-1 must close first" while `milestone_status: open`. FR-007's bar is judged on the real board (`prd.md:170`); the PoC was judged on screen only. | STRONG (procedural prerequisite) |

## Narrowing Signals

- PoC quality was judged on screen, not on the board: "better at 32x32" is still unproven by
  the method FR-007 itself prescribes.
- "Current cover changes too": the toggle must force a redraw within one item, which is what
  breaks the URL-only identity (dimension 2).
- "Whatever I last set": the setting is persistent state under `var/` (dimension 3).
- "Previous frame until enhanced" collides with NFR-01 and the truthfulness rule (dimension 4).
  The roadmap's own S-08 note had suggested the opposite (plain first, then swap).
- Static only: animation stays out, so the "one static frame per item" hard rule needs only
  its "no AI-enhanced covers" half revised.

## Cross-System Convention

The repo already anticipates this shape: `render_cover` is documented as the S-08 seam
(`rendering.py:10-12`), the cache docstring reserves an id suffix for an AI variant, and the
roadmap says to "make the cache key carry the renderer's identity if both renderings must
coexist". The toggle makes coexistence mandatory (switching off must restore plain at once). For
slow work, the codebase's convention is "never block the display loop, show something truthful
meanwhile" (`covers.py` module docstring). The "keep previous frame" preference breaks that
convention once the wait goes past a CDN download.

## Reframed Problem Statement

> **The actual problem to plan around is**: making the board's cover a function of
> *(image, rendering mode)*, where the mode is an owner setting persisted from the page, and
> reconciling a slow, paid, off-device render with the 5 s / never-wrong-but-confident rules.

The OpenRouter call is the part the PoC already proved and can be ported nearly as-is. The work
that remains is in this repo: a persisted toggle with a write route, cover identity (display
presentation, loader, cache key) that carries the mode so a flip redraws and both renderings
coexist, and an explicit changeover policy. "Keep the previous frame until enhanced" cannot
stand as stated: it needs a bound, a fallback (show plain after N s or on failure), or a PRD
amendment to NFR-01. That is a decision for the PRD revision, not something to settle in the plan.

## Confidence

**MEDIUM.** The code evidence for dimensions 2–4 is direct, but two facts are unmeasured:
(a) the real generation latency on flux-3-image-klein from the Pi, and (b) whether the enhanced
frame beats the plain downscale **on the board**. Before /10x-plan: push a few PoC 32 px outputs
(`outputs/cover_miniature_32px.png` and more from the bake-off set) through the stub/real
transport next to their plain renders, judge them from desk distance, and time a handful of
generation calls. Then revise the PRD (lift the Non-Goal, add the FR with its bar, and settle the
changeover vs NFR-01), and close M-1.

## What Changes for /10x-plan

The plan is about cover identity and a persisted owner setting first, with the OpenRouter client
as a ported leaf. It must take as inputs the revised PRD (including the changeover rule) and a
recorded board bake-off, not the screen preview.

## Proof of concept (external reference)

`D:\sandbox\19_ai\openrouter` is a local folder with no git history or remote, so a link to it
is not durable. These are the parts the plan needs, copied here rather than referenced:

- Endpoint `POST https://openrouter.ai/api/v1/images`, `Authorization: Bearer <OPENROUTER_API_KEY>`;
  body `{model, prompt, n: 1, input_references: [{type: "image_url", image_url: {url: <data URL>}}],
  aspect_ratio: "1:1", seed, resolution?}`; response `data[0].b64_json`, cost in `usage.cost`.
- Model `black-forest-labs/flux-3-image-klein` (~$0.009/image; `qwen/qwen-image-2.1-pro`
  ~$0.046 also tried). `resolution` is omitted for models that do not accept it.
- The cover is fetched locally and sent inline because some hosts block the provider's
  fetcher; here the scdn bytes are already downloaded by `fetch_cover`.
- Prompt (notebook cell 9, `MINIATURE_PROMPT`): redesign as a 32x32 pixel-art icon; crop tight
  on the single most recognizable element (~80% of the canvas); 6–8 saturated high-contrast colours
  from the original; thick dark outlines; pure black background; no text, logos, gradients or
  fine detail.
- Downscale 1024 -> 32 with LANCZOS in the PoC; this repo's `render_cover` uses BOX. Which one
  suits a pixel-art source is part of the board bake-off.
- The PoC `.env` holds a live API key: never copy it. Here the key belongs in the service env file.

## References

- Source files: `src/spotify_to_led/display.py:115-124,161-194,265-266,318`,
  `src/spotify_to_led/covers.py:62,124,155-170`, `src/spotify_to_led/frame_cache.py:16-17,111-114`,
  `src/spotify_to_led/rendering.py:10-12`, `src/spotify_to_led/config.py:39-58`,
  `src/spotify_to_led/main.py:182-266`, `src/spotify_to_led/static/index.html:174`
- Foundation: `context/foundation/prd.md:170,229,286`, `context/foundation/roadmap.md` (S-08)
- PoC: `D:\sandbox\19_ai\openrouter\animate.py`, `sandbox.ipynb` (cells 9–10)
- Related research: none yet
