# AI-enhanced covers (S-08) Implementation Plan

## Overview

Let the owner switch on, from the status page, an AI step that regenerates each playing cover
through OpenRouter into a frame designed for the 32x32 panel. Each image is generated at most
once and cached next to its plain frame. With the switch off, no OpenRouter request is made at
all. A board bake-off and a PRD revision come first and gate the rest, because AI-enhanced covers
are still a v1 Non-Goal and a hard rule today.

## Current State Analysis

From the frame brief (`frame.md`), whose investigation this plan adopts:

- **The cover's identity is its URL alone.** `Presentation` is kind + url + stale
  (`display.py:115-124`), and the loop redraws only when that triple changes
  (`display.py:265-266`). `CoverLoader.want` is a no-op for the same URL (`covers.py:155-158`),
  and `fetch_cover` hard-wires `render_cover` (`covers.py:124`). A toggle flipped mid-track
  changes none of these.
- **The cache has one entry per image id** (`frame_cache.py:111-114`), and its docstring
  reserves a suffix after the id for a variant such as an AI frame (`frame_cache.py:16-17`).
- **There is no runtime-writable setting.** `Settings` is env-only (`config.py:39-58`), every
  route is GET (`main.py:182-266`), and the page only polls `/status` (`index.html:174`).
  `TokenStore` is the pattern for runtime state in `var/`: temp file + `os.replace`, and an
  unreadable file treated as absent (`token_store.py:1-60`).
- **The staleness marker is already safe for any frame.** It is drawn on whatever frame goes out
  (`display.py:318`), so FR-018 covers an enhanced frame with no further work.
- **The proof of concept** (`D:\sandbox\19_ai\openrouter`, essentials copied into
  `frame.md` § Proof of concept) calls `POST https://openrouter.ai/api/v1/images` with the cover
  inlined as a data URL, `black-forest-labs/flux-3-image-klein` (~$0.009/image) and
  `MINIATURE_PROMPT`. It returns 1024x1024 `b64_json`. Quality was judged on screen only, and
  latency was never measured.
- **OpenRouter errors** (Context7, OpenRouter docs): 402 means insufficient credits. 403
  "Key limit exceeded" means a per-key credit limit was hit, and a `daily` limit resets at
  midnight UTC. 408/429/502/503 are timeout, rate limit, model down and no provider. 401 is a
  bad key.
- **An existing bake-off tool** is `scripts/cover_bakeoff.py`. It pushes covers to the board
  through the normal transport and writes a contact sheet, and on the Pi it runs with the service
  stopped.

## Desired End State

The status page has an "AI-enhanced covers" checkbox, disabled when no `OPENROUTER_API_KEY` is
configured. Its state survives restarts in `var/settings.json`. When it is on, a cover never seen
enhanced shows the playing placeholder while it generates, then the enhanced frame. A cover
already enhanced before, including before a restart, appears straight from
`var/covers/<size>/<id>.ai.png` with no request. Flipping the checkbox redraws the current item
at once: off goes back to the plain frame, and on generates (or takes from the cache) the
enhanced one. A failed generation falls back to the plain cover after at most three attempts. A
credit-limit refusal pauses all generation until the next 00:00 UTC. The page names whichever of
these states applies. The staleness marker behaves exactly as for plain covers.

Verified by `uv run pytest` plus a manual run on the Pi against the real board (Phase 4).

### Key Discoveries:

- `select_artwork` picks the smallest image covering the board, which is 64 px for a 32 px board
  (`covers.py:93-110`). That is right for the plain path, but generation needs the widest one
  (`item.artwork[0]`, Spotify lists widest first).
- Retry convention: 5 s doubling to 60 s (`covers.py:66-67`, `display.py:56-58`). Enhancement
  uses a bounded variant of it.
- The board accepts one BLE connection, so the bake-off on the Pi runs with
  `pm2 stop spotify-to-led` (`scripts/cover_bakeoff.py` docstring).
- `main.py` names `/status` fields one by one so no secret can leak into it by accident. The new
  fields must follow suit, and the API key must never appear.

## What We're NOT Doing

- Animated covers. They stay a Non-Goal; the PoC's `generate_animation` is not ported.
- A spend cap or a cost display in the service. The owner's OpenRouter key has its own daily
  limit. The service only reacts to it (pause until 00:00 UTC) and logs each generation's cost.
- Regenerating a cover that is already cached, or a "regenerate" button. One enhanced frame per
  image id, forever, unless the cache file is deleted.
- Prompt or model editing from the page. The model comes from env and the prompt is a constant
  in code.
- Generating anything for idle, the placeholder or the no-artwork disc. Only covers are enhanced.
- Auth on the new write route. The hard rule stands, and Cloudflare Access is the only gate.
- Any change to the plain path's behaviour when the checkbox is off.

## Implementation Approach

Phase 1 settles whether the work is worth doing, on the board, and turns the answer into
requirements. Phase 2 adds the saved switch with no board effect yet, so the riskiest piece (the
first write route) lands small. Phase 3 widens the cover identity from *url* to
*(url, enhanced)* through the loader, the cache and the presentation, behind the `render_cover`
seam as the roadmap asked. Phase 4 makes the new states visible and proves it all on the Pi.

The loader keeps its single-wanted-cover model. The display tells it which URL it wants and,
when the switch is on and an enhancer exists, the source URL to enhance from. Loader states gain
`GENERATING`, and `READY` now says whether its frame is enhanced. The display rule maps
`GENERATING` to the placeholder and `READY` to a cover whose `Presentation` carries `enhanced`,
so a flip changes the presentation and redraws. An `Enhancer` owns the OpenRouter call, the
retry budget, the "gave up on this image" memory and the pause-until-midnight state, so the
loader stays about downloading and caching.

## Critical Implementation Details

- **State sequencing:** in enhanced mode, the plain frame is obtained and cached *before*
  generation starts. Turning the switch off is then always an immediate cache hit, and a failed
  generation always has a plain frame to fall back to. While that plain download is in flight
  the state is `PENDING` (previous frame kept, as today). Only once generation itself starts is
  it `GENERATING` (placeholder).
- **Timing & lifecycle:** a generation call can take far longer than a download. `want` with a
  different key must cancel it like a download. The stored `.ai.png` is written only on success,
  and only if the image is still wanted, matching `_load`'s `self._url != url` guards.
- **Security:** `PUT /settings` must accept only `application/json`. A cross-site page can send
  `text/plain` or form bodies without a CORS preflight, and this route spends money. Test that a
  `text/plain` body is rejected and changes nothing.

## Phase 1: Board bake-off and requirements gate

### Overview

Judge enhanced frames against plain ones on the real board, and time the calls. Then write the
verdict into the PRD, `CLAUDE.md`, the roadmap and the foundation docs. If enhanced frames do not
read better on the board, stop here and record that. No service code changes in this phase.

### Changes Required:

#### 1. OpenRouter image client (leaf module)

**File**: `src/spotify_to_led/enhance.py` (new), `tests/test_enhance.py` (new)

**Intent**: Port the PoC's single image-to-image call as a small async function on `httpx2`. The
bake-off uses it now, and Phase 3 builds the `Enhancer` around it. It carries the PoC prompt
verbatim as `ENHANCE_PROMPT` and turns OpenRouter's errors into typed outcomes the retry logic
can act on.

**Contract**: `async def generate(client: httpx2.AsyncClient, api_key: str, model: str, source:
bytes, mime: str, *, timeout: float) -> GeneratedImage`, where `GeneratedImage` holds the decoded
`Image.Image` and `cost: float | None` from `usage.cost`. Request body: `{model, prompt, n: 1,
aspect_ratio: "1:1", input_references: [{type: "image_url", image_url: {url:
"data:<mime>;base64,..."}}]}`, sent to `https://openrouter.ai/api/v1/images` with a Bearer key.
Raises `EnhanceRefused` for 401/402/403 (credit or key; the caller pauses), `EnhanceRetryable`
for transport errors, timeouts, 408, 429 and 5xx, and `EnhanceFailed` for any other status or
an undecodable body. The response body is size-checked before decoding, like `fetch_cover`. The
API key never appears in an exception message or a log line. Tests use `httpx2.MockTransport`.

#### 2. Bake-off script: enhanced column and timings

**File**: `scripts/cover_bakeoff.py`

**Intent**: Add `--enhance`. For each cover, it downloads the widest image, calls `generate`
(key and model from settings), and adds two columns to the contact sheet: the enhanced frame
through `render_cover` (BOX), and through a LANCZOS downscale. The PoC used LANCZOS and the
service uses BOX, so the board decides between them. On the board it shows plain, then enhanced
BOX, then enhanced LANCZOS for each cover, and prints each call's wall time and cost.

**Contract**: `uv run python scripts/cover_bakeoff.py --enhance [--hold S] <url>...`. The
existing behaviour without `--enhance` is unchanged. `Settings` (`config.py`) gains
`openrouter_api_key: SecretStr | None = None` (blank means `None`, like `spotify_client_id`) and
`openrouter_model: str = "black-forest-labs/flux-3-image-klein"` here, with tests in
`tests/test_config.py`, so the script and the service read one config.

#### 3. Requirements revision (after the owner's verdict)

**File**: `context/foundation/prd.md`, `CLAUDE.md`, `context/foundation/roadmap.md`,
`context/foundation/tech-stack.md`, `context/foundation/infrastructure.md`,
`context/changes/ai-enhanced-covers/verification.md` (new)

**Intent**: Record the bake-off (covers used, verdict, BOX vs LANCZOS choice, measured latency
and cost) in `verification.md`, and turn S-08 into requirements:
- PRD: a new FR-019 for owner-switched AI-enhanced covers (behaviour as in Desired End State)
  and its falsifiable bar from the bake-off. Remove AI covers from the Non-Goal, keeping
  animation. Add a note to NFR-01 that in enhanced mode the 5 s budget is met by the placeholder,
  which is the new item's state while it generates.
- `CLAUDE.md` hard rule: from "no animation, no AI-enhanced covers" to no animation, with AI
  enhancement only as FR-019 and off unless the owner enables it.
- Roadmap: close M-1 (Milestone History entry, `milestone_status`), open M-2, and set S-08 to
  `in-progress` with its unknowns resolved.
- `tech-stack.md`: OpenRouter Images API over `httpx2`, with no SDK.
- `infrastructure.md`: `OPENROUTER_API_KEY` in `/etc/spotify-to-led/env`, and outbound HTTPS to
  `openrouter.ai`. The cover now leaves the Pi; the Spotify credential never does.

**Contract**: FR-019 states the chosen downscale. Phase 3 implements whichever it names.

### Success Criteria:

#### Automated Verification:

- `uv run pytest tests/test_enhance.py` passes (request shape, data URL, error classification, key absent from errors)
- `uv run pytest` passes with no other test changed
- `uv run python scripts/cover_bakeoff.py --enhance <url>` writes a contact sheet with plain, enhanced-BOX and enhanced-LANCZOS columns against the stub

#### Manual Verification:

- On the Pi with the service stopped, the owner judges 6–10 varied covers plain vs enhanced from desk distance and records the verdict and the downscale choice
- Generation wall time and cost per call are recorded for at least 5 calls from the Pi
- PRD (FR-019, Non-Goal, NFR-01 note), `CLAUDE.md`, roadmap (M-1 closed, M-2 open), `tech-stack.md` and `infrastructure.md` are revised, or the change is stopped with the negative verdict recorded

**Implementation Note**: The owner's board verdict gates Phases 2–4. Pause here.

---

## Phase 2: Saved toggle and settings

### Overview

The owner can switch enhancement on and off from the page, and the choice survives restarts.
It has no effect on the board yet.

### Changes Required:

#### 1. Settings file location and availability

**File**: `src/spotify_to_led/config.py`, `.env.example`, `tests/test_config.py`

**Intent**: Say where the saved owner setting lives, and whether enhancement can be offered at
all (a key is configured). The key and model settings themselves landed in Phase 1.

**Contract**: `owner_settings_file: Path = Path("var/settings.json")` and an
`enhance_available` property. `.env.example` gets a commented `--- OpenRouter ---` block for
`OPENROUTER_API_KEY` and `OPENROUTER_MODEL`.

#### 2. Owner settings store

**File**: `src/spotify_to_led/owner_settings.py` (new), `tests/test_owner_settings.py` (new)

**Intent**: Persist the owner's runtime choices apart from the env file, the same way
`TokenStore` does. An unreadable or missing file means defaults, with a warning, never a crash.

**Contract**: `OwnerSettings` frozen dataclass `{ai_enhanced: bool = False}`, and
`OwnerSettingsStore(path)` with `load() -> OwnerSettings` and `save(OwnerSettings) -> None`
(atomic temp file + `os.replace`, leftover temp files removed). JSON shape `{"ai_enhanced": true}`.

#### 3. Route, status field and checkbox

**File**: `src/spotify_to_led/main.py`, `src/spotify_to_led/static/index.html`, `tests/test_app.py`

**Intent**: Load the store in `lifespan`, keep the current `OwnerSettings` on `app.state`, and
add `PUT /settings` to change it. `/status` reports whether enhancement is available and
enabled. The page gets an "AI-enhanced covers" section with a checkbox that PUTs on change and
re-renders from the next `/status` poll. It is disabled, with a one-line reason, when no key is
configured.

**Contract**: `PUT /settings` with JSON `{"ai_enhanced": bool}` answers 200 with the saved
settings. A non-JSON content type gets a 4xx and no change. With no key configured,
`ai_enhanced: true` gets a 409 and no change. A failed save gets a 500, with the in-memory value
unchanged. `/status` gains `"enhancement": {"available": bool, "enabled": bool}`, named field by
field.

### Success Criteria:

#### Automated Verification:

- `uv run pytest` passes, including new tests for: store round-trip, unreadable/missing file means defaults, PUT saves and `/status` reflects it, `text/plain` body rejected with no change, 409 without a key, the key never present in `/status`

#### Manual Verification:

- On the laptop, ticking the checkbox survives a service restart, and with no key the checkbox is disabled with its reason shown

**Implementation Note**: Pause for manual confirmation before Phase 3.

---

## Phase 3: Enhanced covers end to end

### Overview

The switch now changes what the board shows: generate once per image, cache, placeholder while
generating, plain fallback, pause until 00:00 UTC on a credit refusal, and a redraw on every
flip.

### Changes Required:

#### 1. Enhancer: retries, give-up and pause

**File**: `src/spotify_to_led/enhance.py`, `tests/test_enhance.py`

**Intent**: Wrap `generate` with the policy the owner chose. At most 3 attempts per image, with
5 s and then 10 s between them, on `EnhanceRetryable` only. `EnhanceFailed`, or running out of
attempts, means "gave up on this image": remembered in memory and cleared when the switch
changes or the service restarts. `EnhanceRefused` pauses all generation until the next 00:00 UTC.
Each outcome is logged once, with the image URL, the cost on success, and never the key.

**Contract**: `Enhancer(client, api_key, model, *, clock, sleep)`, with
`async def enhance(url: str, source: bytes, mime: str) -> Image.Image | None` (`None` means fall
back to plain), `paused_until: datetime | None`, `gave_up(url) -> bool` and `forget()`. The
returned image is already the board frame, downscaled as FR-019 states.

#### 2. Two frames per image in the cache

**File**: `src/spotify_to_led/frame_cache.py`, `tests/test_frame_cache.py`

**Intent**: Store and load the enhanced frame beside the plain one, and update the docstring to
match.

**Contract**: `load(url, *, enhanced: bool = False)` and `store(url, frame, *, enhanced: bool =
False)`. Enhanced entries are `<root>/<size>/<id>.ai.png`, and plain entries are unchanged, so the
existing cache stays valid.

#### 3. Loader: (url, enhanced) identity and GENERATING

**File**: `src/spotify_to_led/covers.py`, `tests/test_covers.py`

**Intent**: Split download from render so the loader can keep the raw bytes, and widen what is
wanted from a URL to a URL plus an optional source to enhance from. In enhanced mode: on an `.ai`
cache hit, `READY` enhanced at once. Otherwise obtain the plain frame (cache or download, stored
as today, state `PENDING`), then download the widest source and enhance (state `GENERATING`).
Success means `READY` enhanced, stored as `.ai`. A fallback, a give-up or a pause means `READY`
with the plain frame. A paused or given-up image goes straight to plain without a request.

**Contract**: `want(url: str | None, *, enhance_from: str | None = None)`, a no-op only when both
match. `CoverStatus.GENERATING` is added. `CoverState` gains `enhanced: bool = False`, true only
on a `READY` enhanced frame. `CoverLoader(..., enhancer: Enhancer | None = None)`. `state(url)`
is unchanged in shape. The plain path with `enhance_from=None` behaves exactly as today
(existing tests unchanged).

#### 4. Display: enhanced in the presentation

**File**: `src/spotify_to_led/display.py`, `tests/test_display.py`

**Intent**: Each sample reads the owner setting and, when it is on and an enhancer exists, wants
the cover with `enhance_from` set to the item's widest artwork. `GENERATING` selects the
placeholder. `READY` selects a cover whose presentation says whether it is enhanced, so a flip
either way redraws. Retained frames are keyed by URL plus enhanced.

**Contract**: `Presentation` gains `enhanced: bool = False` (in `__str__` too, for the log).
`run_display(..., owner_settings: Callable[[], OwnerSettings] | None = None)`.
`select_presentation` maps `GENERATING` to `PLACEHOLDER`. Staleness is still recomputed on every
kind, unchanged.

#### 5. Wiring

**File**: `src/spotify_to_led/main.py`, `tests/test_app.py`

**Intent**: Build an `Enhancer` on the shared client when a key is configured, hand it to the
loader, and pass `lambda: app.state.owner_settings` to the display. `PUT /settings` calls
`enhancer.forget()` when the value changes. Close order is unchanged: display, loader, client.

**Contract**: No route change beyond Phase 2.

### Success Criteria:

#### Automated Verification:

- `uv run pytest` passes, with new tests covering the enhanced cache hit, the PENDING/GENERATING/READY sequence, no request after restart, redraw on flip both ways, the retry budget, 400 with no retry, the 402/403 pause until 00:00 UTC, the marker on a stale enhanced cover, and no request with the switch off. In detail:
  - enhanced cache hit means no request
  - a never-seen cover goes PENDING, then GENERATING (placeholder), then READY enhanced, stored as `.ai.png`
  - the same image wanted again after a restart (new loader, same cache) makes no request
  - flip on mid-item redraws, and flip off redraws to plain from the cache with no download
  - 2 retryable failures then success; 3 retryable failures fall back to plain, and the image is not retried until `forget()`
  - 400 means plain with no retry
  - 402 and 403 pause until the next 00:00 UTC (fake clock), with no requests for any image until then
  - a stale enhanced cover carries the marker
  - with the switch off, no request reaches the OpenRouter mock in any test
- The existing plain-path tests in `test_covers.py`, `test_display.py` and `test_frame_cache.py` pass unmodified

#### Manual Verification:

- On the laptop with the stub and a real key, a new item shows the placeholder, then the enhanced frame in `var/frames`, and replaying it is instant with no new OpenRouter request in the log

**Implementation Note**: Pause for manual confirmation before Phase 4.

---

## Phase 4: Status reporting and Pi rollout

### Overview

The page names what enhancement is doing, and the whole flow is proven on the Pi and the board.

### Changes Required:

#### 1. Enhancement state on /status and the page

**File**: `src/spotify_to_led/main.py`, `src/spotify_to_led/static/index.html`, `tests/test_app.py`

**Intent**: Extend the enhancement section with what the current cover is: enhanced, plain,
generating, plain after giving up, or plain because generation is paused until a given time. Add
`enhanced` to the reported board presentation, so "Last sent: cover (AI-enhanced)" can be shown.

**Contract**: `"enhancement"` gains `"paused_until": datetime | null` and
`"cover": "enhanced" | "plain" | "generating" | "gave_up" | null` (null when no cover is
wanted). `"board.presentation"` gains `"enhanced": bool`. All fields are named one by one.

#### 2. Rollout

**File**: `/etc/spotify-to-led/env` on the Pi (owner-edited, not in git),
`context/changes/ai-enhanced-covers/verification.md`

**Intent**: The owner adds `OPENROUTER_API_KEY`, then deploys with `scripts/deploy.sh`. Record
the board observations below in `verification.md`.

**Contract**: No code.

### Success Criteria:

#### Automated Verification:

- `uv run pytest` passes, including `/status` tests for each `cover` value and for `paused_until`
- `scripts/deploy.sh` succeeds and `/healthz` answers on the Pi

#### Manual Verification:

- On the board, a never-seen album shows the placeholder then the enhanced frame, and a same-album track or replay shows it immediately with no OpenRouter request
- Flipping the checkbox off and on mid-track redraws the board each way within a few seconds
- The checkbox state survives `pm2 restart spotify-to-led`
- With a deliberately wrong key, the page shows paused until 00:00 UTC and the board shows the plain cover
- During a forced Spotify outage, an enhanced cover gets the staleness marker after 30 s

**Implementation Note**: After manual verification, the change is ready for review and archive.

---

## Testing Strategy

### Unit Tests:

- `enhance.py`: request body and headers, data URL, `b64_json` decode, error classification per status, size limit, key never in an exception or a log record.
- `Enhancer`: attempt budget and delays, give-up memory and `forget`, pause until the next UTC midnight (including a call right before and right after midnight), cost logging.
- `FrameCache`: `.ai` round-trip, plain and enhanced independent, an id with the suffix cannot escape the directory.
- `CoverLoader`: the state sequences in Phase 3, cancellation of a generation on a different `want`, no stale write after cancellation.
- `OwnerSettingsStore`: round-trip, defaults on a missing or corrupt file, atomic write.

### Integration Tests:

- App-level (`test_app.py`): PUT then `/status`, content-type rejection, 409 without a key, and the display redraw on a flip through `run_display` with the stub transport and a mocked OpenRouter.

### Manual Testing Steps:

1. Run the bake-off on the board (Phase 1).
2. Toggle on the laptop and restart the service (Phase 2).
3. Run the stub with a real key and play new and repeated albums (Phase 3).
4. Do the Pi checks listed in Phase 4.

## Performance Considerations

Generation runs in the loader's task, never in the display loop, so the board link and the
staleness marker are unaffected by a slow call. The per-call timeout is set from the Phase 1
measurement (90 s until then). Decoding a 1024x1024 PNG on the Pi is the one heavy step. It
stays bounded by the size check and happens once per image ever.

## Migration Notes

Existing `var/covers/<size>/<id>.png` entries stay valid as the plain frames. Enhanced entries are
new files beside them. With no `var/settings.json`, enhancement is off, so behaviour after deploy
is unchanged until the owner ticks the box. Rollback means turning the box off, or redeploying
the previous commit. Leftover `.ai.png` files are harmless.

## References

- Frame brief: `context/changes/ai-enhanced-covers/frame.md`
- Roadmap S-08: `context/foundation/roadmap.md`
- Similar implementation: `src/spotify_to_led/spotify/token_store.py` (persisted state), `src/spotify_to_led/covers.py:189-239` (retrying loader task), `scripts/cover_bakeoff.py` (board bake-off)
- Prior bake-off: `context/archive/2026-11-09-artwork-on-the-board/plan.md` Phase 1
- OpenRouter error codes and key limits: openrouter.ai/docs (errors-and-debugging; capping-shared-agent-keys)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Board bake-off and requirements gate

#### Automated

- [x] 1.1 `uv run pytest tests/test_enhance.py` passes (request shape, data URL, error classification, key absent from errors) — 1bca187
- [x] 1.2 `uv run pytest` passes with no other test changed — 1bca187
- [x] 1.3 `uv run python scripts/cover_bakeoff.py --enhance <url>` writes a contact sheet with plain, enhanced-BOX and enhanced-LANCZOS columns against the stub — 1bca187

#### Manual

- [x] 1.4 On the Pi with the service stopped, the owner judges 6–10 varied covers plain vs enhanced from desk distance and records the verdict and the downscale choice — 1bca187
- [x] 1.5 Generation wall time and cost per call are recorded for at least 5 calls from the Pi — 1bca187
- [x] 1.6 PRD (FR-019, Non-Goal, NFR-01 note), `CLAUDE.md`, roadmap (M-1 closed, M-2 open), `tech-stack.md` and `infrastructure.md` are revised, or the change is stopped with the negative verdict recorded — 1bca187

### Phase 2: Saved toggle and settings

#### Automated

- [x] 2.1 `uv run pytest` passes, including new tests for: store round-trip, unreadable/missing file means defaults, PUT saves and `/status` reflects it, `text/plain` body rejected with no change, 409 without a key, the key never present in `/status` — f2a0b88

#### Manual

- [x] 2.2 On the laptop, ticking the checkbox survives a service restart, and with no key the checkbox is disabled with its reason shown — f2a0b88

### Phase 3: Enhanced covers end to end

#### Automated

- [x] 3.1 `uv run pytest` passes, with new tests covering the enhanced cache hit, the PENDING/GENERATING/READY sequence, no request after restart, redraw on flip both ways, the retry budget, 400 with no retry, the 402/403 pause until 00:00 UTC, the marker on a stale enhanced cover, and no request with the switch off — e576b99
- [x] 3.2 The existing plain-path tests in `test_covers.py`, `test_display.py` and `test_frame_cache.py` pass unmodified — e576b99

#### Manual

- [x] 3.3 On the laptop with the stub and a real key, a new item shows the placeholder, then the enhanced frame in `var/frames`, and replaying it is instant with no new OpenRouter request in the log — e576b99

### Phase 4: Status reporting and Pi rollout

#### Automated

- [x] 4.1 `uv run pytest` passes, including `/status` tests for each `cover` value and for `paused_until`
- [ ] 4.2 `scripts/deploy.sh` succeeds and `/healthz` answers on the Pi

#### Manual

- [ ] 4.3 On the board, a never-seen album shows the placeholder then the enhanced frame, and a same-album track or replay shows it immediately with no OpenRouter request
- [ ] 4.4 Flipping the checkbox off and on mid-track redraws the board each way within a few seconds
- [ ] 4.5 The checkbox state survives `pm2 restart spotify-to-led`
- [ ] 4.6 With a deliberately wrong key, the page shows paused until 00:00 UTC and the board shows the plain cover
- [ ] 4.7 During a forced Spotify outage, an enhanced cover gets the staleness marker after 30 s
