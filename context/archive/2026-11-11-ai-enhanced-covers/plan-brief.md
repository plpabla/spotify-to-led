# AI-enhanced covers (S-08) — Plan Brief

> Full plan: `context/changes/ai-enhanced-covers/plan.md`
> Frame brief: `context/changes/ai-enhanced-covers/frame.md`

## What & Why

> **The actual problem to plan around is**: making the board's cover a function of
> *(image, rendering mode)*, where the mode is an owner setting persisted from the page, and
> reconciling a slow, paid, off-device render with the 5 s / never-wrong-but-confident rules.

The owner judged the plain 32x32 downscale not good enough. A proof of concept shows that
OpenRouter can redesign a cover as a 32 px pixel-art icon for about $0.009. This change brings
that onto the board behind a checkbox, and when the checkbox is off, no request is sent.

## Starting Point

The display, the cover loader and the frame cache all identify a cover by its URL alone, so a
toggle flipped mid-track would change nothing. There is no runtime-writable setting and no write
route. `render_cover` and a cache-key suffix were left as the S-08 seams. AI covers are still a
PRD Non-Goal and a `CLAUDE.md` hard rule.

## Desired End State

The status page has a saved "AI-enhanced covers" checkbox. When it's on, a never-seen cover shows
the playing placeholder while it generates, then the enhanced frame. A cover enhanced before
appears instantly from `var/covers/<size>/<id>.ai.png` with no request. Flipping the checkbox
redraws the current item either way. Failures fall back to the plain cover, a credit-limit
refusal pauses generation until 00:00 UTC, and the page says which of these applies.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Where the model runs | OpenRouter Images API, ported from the PoC (`flux-3-image-klein`, `MINIATURE_PROMPT`) | Already proven to give good-looking 32 px output | Frame |
| On/off | Checkbox on the status page; off means no OpenRouter request at all | The owner controls spend and look | Frame |
| Persistence | `var/settings.json`, atomic writes like `TokenStore` | Choice survives restarts, as the owner asked | Frame |
| Flip effect | Redraws the current cover at once, both ways | The owner wants an immediate change, not "from the next track" | Frame |
| Changeover | Previous frame during the plain download, then the placeholder while generating, then enhanced | Truthful within NFR-01, and no wrong album during a long generation | Plan |
| Generation count | Once per image id, cached as a second frame (`<id>.ai.png`) beside the plain one | Never regenerate, and same-album tracks share one generation | Plan (owner) |
| Failure | Plain cover stays; up to 3 attempts (5 s, then 10 s) on timeout/408/429/5xx; 400 not retried; give-up remembered until a flip or restart | Board never worse than today, with bounded calls | Plan |
| Credit limit | 401/402/403 pause all generation until the next 00:00 UTC | The owner's key has a daily cap, so retrying the same day is pointless | Plan (owner) |
| Spend guard | None in the service; log the cost per call | The key's own daily cap is the guard | Plan (owner) |
| Gate | Phase 1 = board bake-off + latency timing, then the PRD/CLAUDE.md/roadmap revision | FR-007's bar is judged on the board; the PoC was judged on screen only | Plan |
| Write route | `PUT /settings`, JSON only, no auth | Hard rule: no app auth; JSON forces a CORS preflight against cross-site posts | Plan |

## Scope

**In scope:** OpenRouter client and `Enhancer` policy; saved owner setting, `PUT /settings` and
the checkbox; two-frame cache; loader `GENERATING` state; `enhanced` in the presentation;
enhancement state on `/status` and the page; bake-off extension; PRD/CLAUDE.md/roadmap/foundation
revisions; Pi rollout.

**Out of scope:** animation; spend cap or cost display; regenerate button; prompt or model
editing from the page; enhancing idle, the placeholder or the no-artwork disc; any auth; any
change to the plain path when the checkbox is off.

## Architecture / Approach

Each sample, the display reads the owner setting and asks `CoverLoader` for *(url,
enhance_from)*. In enhanced mode the loader:

1. checks the `.ai` cache
2. otherwise gets the plain frame first (from cache or download) and stores it
3. then downloads the widest source and calls `Enhancer` (state `GENERATING`)

On success it stores `.ai.png` and returns `READY` enhanced. On failure, give-up or pause it
returns `READY` with the plain frame. `Presentation.enhanced` makes a flip redraw, and the
staleness marker applies to whatever frame goes out.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Bake-off and requirements gate | `enhance.generate`, `--enhance` bake-off, the owner's board verdict, measured latency, PRD FR-019 and the hard-rule revision | Enhanced frames may not beat plain on the board (stops the change) |
| 2. Saved toggle and settings | `var/settings.json`, `PUT /settings`, the checkbox, `/status.enhancement` | First write route; must reject non-JSON |
| 3. Enhanced covers end to end | `Enhancer`, `.ai` cache, `GENERATING`, redraw on flip, retries and pause | Cancel and race correctness in the loader |
| 4. Status reporting and Pi rollout | Enhancement state on the page; verified on the real board | Real latency and behaviour on the Pi |

**Prerequisites:** an OpenRouter key with a daily limit (the owner has one); M-1 closed in Phase 1.
**Estimated effort:** ~4 sessions, one per phase, plus a board session in Phase 1.

## Open Risks & Assumptions

- Frame confidence was MEDIUM. Board quality and latency are unmeasured until Phase 1.
- Generation latency is unknown. The 90 s per-call timeout is a placeholder until Phase 1 measures it.
- Assumes `/api/v1/images` keeps the PoC's request and response shape.
- Covers (public images) leave the Pi. The Spotify credential never does.

## Success Criteria (Summary)

- With the checkbox on, the board shows an enhanced cover for every item with artwork, and a repeat costs nothing.
- Flipping the checkbox redraws the board either way, and the choice survives a restart.
- A broken provider or exhausted credit never leaves the board worse than today's plain cover, and the page says why.
