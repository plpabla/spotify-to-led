# Select the AI enhancement model Implementation Plan

## Overview

Let the owner pick, from the status page, which OpenRouter image model redraws covers (FR-019). The choices come from a tracked text file the owner edits; the pick is saved with the owner's other settings. Because enhanced frames stay one per cover, the page also gets a button that regenerates the current cover with the selected model.

## Current State Analysis

- The model is env configuration: `Settings.openrouter_model` (`src/spotify_to_led/config.py:64`, default `black-forest-labs/flux-3-image-klein`), handed once to `Enhancer(...)` in the lifespan (`src/spotify_to_led/main.py:173-182`) and stored as `self._model` (`src/spotify_to_led/enhance.py:309`). Changing it needs an env edit and a restart.
- The owner's runtime choices are `OwnerSettings(ai_enhanced, daily_limit_usd)` in `var/settings.json` (`src/spotify_to_led/owner_settings.py`), loaded fail-soft, with optional fields for files saved before a field existed (`owner_settings.py:76`). `PUT /settings` takes a strict, partial, `extra="forbid"` JSON body (`main.py:373-449`); `/status.enhancement` reports the switch, cover state and spend (`main.py:452-484`).
- The enhancer already reads one owner choice live: `daily_limit` is a callable (`main.py:181`, `enhance.py:314`). The model can follow the same pattern.
- Enhanced frames are cached as `var/covers/<size>/<id>.ai.png`, with no model in the key (`src/spotify_to_led/frame_cache.py:130`). A cover enhanced once is never generated again.
- The give-up memory is per source URL (`enhance.py:_gave_up`), cleared wholesale by `forget()`, which `PUT /settings` calls on a switch flip (`main.py:443-447`).
- `CoverLoader.want` answers an enhanced cache hit at once, without loading the plain frame (`src/spotify_to_led/covers.py:want`); `_load` downloads/loads the plain frame, then enhances and stores the result only if the same pair is still wanted.
- The status page keeps its controls across polls with change counters and a focus guard (`src/spotify_to_led/static/index.html`, `enhanceChanges`/`limitChanges`).
- `scripts/cover_bakeoff.py:177,186` reads `settings.openrouter_model`; `tests/test_config.py:30,269-278` test it; `.env.example:49`, `context/foundation/tech-stack.md:43`, `context/foundation/infrastructure.md:110` and PRD FR-019 (`context/foundation/prd.md:205`) mention it.

## Desired End State

- `models.txt` at the repo root lists OpenRouter model ids, one per line (`#` comments and blank lines ignored). Its first id is the default.
- The status page's AI section has a model drop-down listing exactly those ids, showing the one in use; choosing another saves it to `var/settings.json` and the next generation uses it, without a restart. Editing `models.txt` changes the drop-down on the next poll.
- A saved model no longer in the list falls back to the first id. A missing or id-less list is warned about and acts as `["black-forest-labs/flux-3-image-klein"]`. `OPENROUTER_MODEL` no longer exists.
- Changing the model makes the enhancer forget the covers it gave up on.
- A "Regenerate" button redraws the cover on the board with the selected model, overwriting its `.ai.png`. It is unavailable when enhancement is off or unavailable, generation is paused, today's limit is reached, or no finished cover is on show.
- Verify: `uv run pytest` passes; on the Pi, switching model and pressing Regenerate shows a new enhanced cover and an INFO "cover enhanced" line naming the new model.

### Key Discoveries:

- Live-read callable pattern for owner choices: `main.py:181`, `enhance.py:314-316`.
- Optional-field compatibility in the settings file: `owner_settings.py:74-81`.
- `PUT /settings` must keep refusing non-JSON bodies (cross-site protection, `main.py:405-412`); a new money-spending route needs the same protection.
- `FrameCache.store(url, frame, enhanced=True)` is the single place `.ai.png` is written; regeneration reuses it to overwrite.

## What We're NOT Doing

- No per-model cache: enhanced frames stay one per cover (`<id>.ai.png`); a model switch alone does not redraw covers already enhanced. Regeneration is explicit, one cover at a time.
- No friendly labels or prices in the list file; the drop-down shows raw ids.
- No editing of the list from the web; the file is edited by hand and deployed with the checkout.
- No per-model daily limits or price estimates; the limit keeps counting OpenRouter's reported cost whatever the model.
- No bulk "regenerate all" and no regeneration of a cover that is not the one now wanted.
- No change to the prompt, the retry/pause policy or the plain-cover path.

## Implementation Approach

A small `models` module owns reading the list and resolving "the model in use" from the list and the saved choice; everything else asks it. `OwnerSettings` gains an optional `model`. The enhancer takes the model as a callable, like the daily limit, so routes only change saved state and the next generation follows. Regeneration is a loader operation on the currently wanted cover, triggered by a JSON-only route, and reuses the existing GENERATING → READY path, so the board shows the plain cover with the spend bar while it runs, exactly as a first generation does.

## Critical Implementation Details

**State sequencing (regenerate).** An enhanced cache hit leaves the loader without a plain frame, so regeneration must load the plain frame from the cache (or download it, `PENDING` meanwhile) before entering `GENERATING`. The previous enhanced frame is kept in memory: if the enhancer answers `None` (failure, give-up, refusal), the state returns to `READY` with that previous enhanced frame rather than plain, and the cache entry is untouched. The new frame is stored only if the same pair is still wanted, as today.

**Give-up memory and regeneration.** A forced regeneration must not be short-circuited by an earlier give-up on the same source image, so it drops that one image from the memory first; the pause and the daily limit still apply and are checked by the route before anything starts.

## Phase 1: Model list and live model

### Overview

Introduce the list file and the resolution rule, store the choice in owner settings, replace the env setting, and make the enhancer read the model live.

### Changes Required:

#### 1. The model list

**File**: `models.txt` (new, repo root)

**Intent**: The owner's list of selectable models, shipped with today's default first so behaviour is unchanged until the owner picks.

**Contract**: One OpenRouter model id per line; leading/trailing whitespace stripped; empty lines and lines starting with `#` ignored. Initial content: a header comment explaining the format and that the first id is the default, then `black-forest-labs/flux-3-image-klein`.

**File**: `src/spotify_to_led/models.py` (new)

**Intent**: Read the list on demand and resolve the model in use, failing soft like the other runtime files.

**Contract**:
- `FALLBACK_MODEL = "black-forest-labs/flux-3-image-klein"`.
- `load_models(path: Path) -> tuple[str, ...]`: ids in file order, duplicates dropped (first kept). Missing, unreadable or id-less file → `(FALLBACK_MODEL,)` with a WARNING (missing file warned too: unlike settings, the list is expected to exist). Never raises.
- `model_in_use(models: tuple[str, ...], saved: str | None) -> str`: `saved` if it is in `models`, else `models[0]`.

#### 2. Settings

**File**: `src/spotify_to_led/config.py`

**Intent**: Remove the env-configured model; add the list path.

**Contract**: delete `openrouter_model`; add `model_list_file: Path = Path("models.txt")` (env `MODEL_LIST_FILE`). `extra="ignore"` already makes a leftover `OPENROUTER_MODEL` in an env file harmless.

**File**: `src/spotify_to_led/owner_settings.py`

**Intent**: Persist the owner's model choice.

**Contract**: `OwnerSettings.model: str | None = None` (`None` = no choice yet). `_from_json`: `model` optional; absent or `null` → `None`; a non-string or blank string → `TypeError`/`ValueError` (whole file falls back to defaults, as for other bad fields). Saved via `asdict` as today.

#### 3. Enhancer reads the model live

**File**: `src/spotify_to_led/enhance.py`

**Intent**: Use whichever model is in use at the moment a generation starts, and say which in the log.

**Contract**: `Enhancer.__init__(..., model: Callable[[], str], ...)` replaces `model: str`; read once per `enhance()` call before the first attempt and used for all its attempts. The success INFO line and the give-up/refusal WARNING lines include the model id. Add `forget_image(url: str) -> None`, dropping one image from the give-up memory (used in Phase 4).

**File**: `src/spotify_to_led/main.py`

**Intent**: Wire the live model.

**Contract**: lifespan stores `settings.model_list_file` on `app.state` and passes `model=lambda: model_in_use(load_models(path), app.state.owner_settings.model)` to `Enhancer`.

#### 4. Bake-off script

**File**: `scripts/cover_bakeoff.py`

**Intent**: Keep the script working without `openrouter_model`.

**Contract**: uses the first id of `load_models(settings.model_list_file)`; its docstring's mention of `OPENROUTER_MODEL` becomes "the first model in `models.txt`".

#### 5. Tests

**Files**: `tests/test_models.py` (new), `tests/test_owner_settings.py`, `tests/test_enhance.py`, `tests/test_config.py`

**Intent**: Cover parsing, resolution, persistence and the live read.

**Contract**:
- `test_models.py`: comments/blanks/whitespace ignored, order kept, duplicates dropped; missing / empty / comment-only file → fallback with a warning; `model_in_use` returns saved when listed, first id otherwise and when `None`.
- `test_owner_settings.py`: round-trip with a model; a file without `model` loads with `model=None` and other fields kept; a non-string `model` → defaults.
- `test_enhance.py`: the request body's `model` is the callable's current value; changing it between two `enhance()` calls changes the second request; `forget_image` re-allows only that image.
- `test_config.py`: remove the two `openrouter_model` tests and the `OPENROUTER_MODEL` entry in the clean-env list; add a `model_list_file` default/env test.

### Success Criteria:

#### Automated Verification:

- Full test suite passes: `uv run pytest`
- No `openrouter_model` left in code or tests: `grep -rn openrouter_model src tests scripts` returns nothing

#### Manual Verification:

- `uv run python -m spotify_to_led.main` starts on the laptop with the shipped `models.txt`, and with `MODEL_LIST_FILE` pointing at a missing file (one WARNING, service up)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 2: Routes

### Overview

Let `PUT /settings` save a model from the list, and let `/status` report the list and the model in use.

### Changes Required:

#### 1. `PUT /settings`

**File**: `src/spotify_to_led/main.py`

**Intent**: Accept a model choice, only from the current list, and forget give-ups when the model in use changes.

**Contract**:
- `SettingsChange.model: StrictStr | None = None`; `_changes_something` counts it.
- A `model` not in `load_models(app.state.model_list_file)` → `409` plain text "That model is not in the model list." with nothing saved.
- `updated` carries `model` (kept from `previous` when omitted). Response adds `"model": updated.model`.
- If `model_in_use` before and after differs and an enhancer exists → `enhancer.forget()` (in addition to the existing switch-flip rule; one call is enough when both change).

#### 2. `/status`

**File**: `src/spotify_to_led/main.py`

**Intent**: Give the page what the drop-down needs.

**Contract**: `enhancement.models: list[str]` (the list as read now) and `enhancement.model: str` (the model in use), reported whether or not a key is configured.

#### 3. Tests

**File**: `tests/test_app.py`

**Intent**: Hold the route contract.

**Contract**: fixture points `MODEL_LIST_FILE` at a temp file with two ids. Tests: `/status` reports both ids and the first as in use; `PUT {"model": second}` saves (file + memory) and `/status` reports it; an unlisted model → 409 and nothing saved; a non-string model → 422; a model-only body keeps switch and limit; changing the model calls `forget()` (give-up cleared), re-sending the same model does not; removing the saved id from the file makes `/status` report the first id.

### Success Criteria:

#### Automated Verification:

- Full test suite passes: `uv run pytest`

#### Manual Verification:

- With the service running, `curl -X PUT -H 'Content-Type: application/json' -d '{"model":"<listed id>"}' localhost:8000/settings` saves it, and `/status` shows it as `enhancement.model`

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 3: Page and docs

### Overview

Put the drop-down on the status page and update the docs that describe the model setting.

### Changes Required:

#### 1. Drop-down

**File**: `src/spotify_to_led/static/index.html`

**Intent**: Let the owner pick the model where the other AI controls live.

**Contract**: a labelled `<select id="enhance-model">` in the AI section, hidden when `enhancement.available` is false. Options rebuilt from `enhancement.models` only when the list changed (text via `textContent`/`Option`), value set to `enhancement.model`. Same guards as the other controls: a change counter, a saving flag, and no update while the select has focus. `change` → `PUT /settings {"model": value}`; a failure shows "The model could not be saved." in `enhance-note`, and the next poll restores the saved value.

#### 2. Docs

**Files**: `context/foundation/prd.md` (FR-019 note), `context/foundation/tech-stack.md:43`, `context/foundation/infrastructure.md:110`, `CLAUDE.md` (overview paragraph), `.env.example:49`, `README.md` if it lists env keys

**Intent**: Replace `OPENROUTER_MODEL` with `models.txt` + the page choice everywhere it is described.

**Contract**: dated "Updated 2026-11-13 (select-model)" notes in the foundation docs, edited in place; `.env.example` drops `OPENROUTER_MODEL` and documents `MODEL_LIST_FILE` (commented, default `models.txt`); CLAUDE.md's overview mentions the model choice is saved in `var/settings.json` and listed in `models.txt`.

#### 3. Tests

**File**: `tests/test_app.py`

**Intent**: Keep the existing "page wires its controls" style of check.

**Contract**: the served page contains the `enhance-model` select and a `PUT` to `/settings` carrying `model`.

### Success Criteria:

#### Automated Verification:

- Full test suite passes: `uv run pytest`
- No `OPENROUTER_MODEL` left outside archives: `grep -rn OPENROUTER_MODEL --exclude-dir=archive --exclude-dir=.venv .` returns only dated historical notes

#### Manual Verification:

- On the page, the drop-down lists the ids in `models.txt` and shows the one in use; choosing another survives a reload and a restart
- Editing `models.txt` (add a line) shows the new option within one poll, without a restart
- The drop-down is hidden without an OpenRouter key

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 4: Regenerate the current cover

### Overview

Add a button that redraws the cover now on show with the selected model and replaces its cached enhanced frame.

### Changes Required:

#### 1. Loader

**File**: `src/spotify_to_led/covers.py`

**Intent**: Regenerate the wanted cover's enhanced frame on request, through the existing generation path.

**Contract**: `CoverLoader.regenerate(url: str, enhance_from: str) -> bool`. Returns `False` (and does nothing) unless `(url, enhance_from)` is the wanted pair, an enhancer exists, and the state is `READY`. Otherwise: drops the source from the enhancer's give-up memory (`forget_image`), remembers the current enhanced frame (if the state is enhanced) as the fallback, cancels nothing else (no task runs in `READY`), and starts `_load` with the plain frame from memory or the cache (download if neither, `PENDING` meanwhile), so the state is `GENERATING` while the enhancer runs. On success the frame is `READY` enhanced and overwrites `<id>.ai.png`; on `None` the state is `READY` with the fallback enhanced frame if there was one, else plain. Module docstring gains a paragraph for this path.

#### 2. Route

**File**: `src/spotify_to_led/main.py`

**Intent**: Let the page ask for regeneration safely.

**Contract**: `POST /regenerate` with a JSON body that must be an empty object (`RegenerateRequest` model, `extra="forbid"`), so like `PUT /settings` a cross-site form or `text/plain` post is refused with 422 before it can spend money. Refusals as `409` plain text, nothing started: enhancement not enabled (switch off or no key/enhancer); generation paused; today's limit reached; `covers.regenerate(wanted_cover_url(snapshot, size), enhancement_source_url(snapshot))` returned `False` or either URL is `None` ("No finished cover to regenerate right now."). Success → `202 {"regenerating": true}`. Module docstring mentions the route.

#### 3. Button

**File**: `src/spotify_to_led/static/index.html`

**Intent**: One click to redraw the current cover.

**Contract**: a "Regenerate cover" button in the AI section, hidden when enhancement is unavailable, disabled unless `enhancement.enabled` and `enhancement.cover` is `enhanced`, `plain` or `gave_up` and neither `paused_until` nor `spend.limit_reached`. Click → `POST /regenerate` with body `{}`; a non-2xx shows the response's text in `enhance-note`. The existing cover line already reports "generating" while it runs.

#### 4. Tests

**Files**: `tests/test_covers.py`, `tests/test_app.py`

**Intent**: Hold the regeneration contract.

**Contract**:
- `test_covers.py`: from an enhanced cache hit, `regenerate` goes `GENERATING` (plain frame) → `READY` enhanced with the new frame, and the cache's `.ai.png` is the new one; a failing enhancer returns to the previous enhanced frame and leaves the cache file unchanged; a given-up source is tried again; `regenerate` for a pair not wanted, or while `PENDING`/`GENERATING`, returns `False` and makes no request; wanting another cover mid-regeneration stores nothing.
- `test_app.py`: 409 when switched off, paused, limit reached, nothing playing; 422 for a non-JSON body and for a non-empty object; 202 when a cover is ready, and the enhancer is called with the model in use; the page contains the button and a `POST` to `/regenerate`.

#### 5. Docs

**Files**: `context/foundation/prd.md` (FR-019 note), `CLAUDE.md` (route list)

**Intent**: Record the regenerate action.

**Contract**: FR-019 note says the owner can regenerate the current cover with the selected model, replacing its kept enhanced frame; CLAUDE.md's route list includes `POST /regenerate`.

### Success Criteria:

#### Automated Verification:

- Full test suite passes: `uv run pytest`

#### Manual Verification:

- On the Pi with enhancement on: pick a different model, press Regenerate; the board shows the plain cover with the spend bar, then the new enhanced cover; the log's "cover enhanced" line names the new model; the next time the item plays, the new frame is shown from the cache
- The button is disabled with enhancement off, while generating, and once today's limit is reached
- Today's spend on the page goes up by the regeneration's cost

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful.

---

## Testing Strategy

### Unit Tests:

- List parsing and fallback (`tests/test_models.py`), settings persistence (`tests/test_owner_settings.py`), live model read and `forget_image` (`tests/test_enhance.py`), loader regeneration (`tests/test_covers.py`).

### Integration Tests:

- `tests/test_app.py` through the FastAPI test client: `/status` fields, `PUT /settings` model validation and give-up clearing, `POST /regenerate` refusals and success, and the page wiring checks.

### Manual Testing Steps:

1. Add a second id to `models.txt`; see it appear in the drop-down within 3 s.
2. Select it; reload the page and restart the service; it stays selected.
3. Press Regenerate on a cover already enhanced; watch the board and the log.
4. Remove the selected id from `models.txt`; the drop-down falls back to the first id.

## Performance Considerations

`models.txt` is read on every `/status` poll (every 3 s) and every enhancement; it is a few lines, so the cost is negligible.

## Migration Notes

- Existing `var/settings.json` files have no `model` and load as "no choice" → first id of `models.txt`, which ships as today's default: no behaviour change at deploy.
- A leftover `OPENROUTER_MODEL` in `/etc/spotify-to-led/env` is ignored; remove it at leisure. A Pi that had set a non-default model there must add that id as the first line of `models.txt` (or pick it on the page) to keep using it.
- Existing `.ai.png` files stay valid and are shown as before.

## References

- Change notes: `context/changes/select-model/change.md`
- Owner settings pattern: `src/spotify_to_led/owner_settings.py`, `src/spotify_to_led/main.py:373-449`
- Live-read callable: `src/spotify_to_led/main.py:181`, `src/spotify_to_led/enhance.py:314`
- Enhanced cache key: `src/spotify_to_led/frame_cache.py:130`
- Prior change: `context/archive/2026-11-13-display-usage/`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Model list and live model

#### Automated

- [x] 1.1 Full test suite passes: `uv run pytest` — 7fecc6f
- [x] 1.2 No `openrouter_model` left in code or tests: `grep -rn openrouter_model src tests scripts` returns nothing — 7fecc6f

#### Manual

- [x] 1.3 `uv run python -m spotify_to_led.main` starts on the laptop with the shipped `models.txt`, and with `MODEL_LIST_FILE` pointing at a missing file (one WARNING, service up) — 7fecc6f

### Phase 2: Routes

#### Automated

- [x] 2.1 Full test suite passes: `uv run pytest` — 263bef2

#### Manual

- [ ] 2.2 With the service running, `curl -X PUT -H 'Content-Type: application/json' -d '{"model":"<listed id>"}' localhost:8000/settings` saves it, and `/status` shows it as `enhancement.model`

### Phase 3: Page and docs

#### Automated

- [x] 3.1 Full test suite passes: `uv run pytest`
- [x] 3.2 No `OPENROUTER_MODEL` left outside archives: `grep -rn OPENROUTER_MODEL --exclude-dir=archive --exclude-dir=.venv .` returns only dated historical notes

#### Manual

- [x] 3.3 On the page, the drop-down lists the ids in `models.txt` and shows the one in use; choosing another survives a reload and a restart
- [x] 3.4 Editing `models.txt` (add a line) shows the new option within one poll, without a restart
- [x] 3.5 The drop-down is hidden without an OpenRouter key

### Phase 4: Regenerate the current cover

#### Automated

- [ ] 4.1 Full test suite passes: `uv run pytest`

#### Manual

- [ ] 4.2 On the Pi with enhancement on: pick a different model, press Regenerate; the board shows the plain cover with the spend bar, then the new enhanced cover; the log's "cover enhanced" line names the new model; the next time the item plays, the new frame is shown from the cache
- [ ] 4.3 The button is disabled with enhancement off, while generating, and once today's limit is reached
- [ ] 4.4 Today's spend on the page goes up by the regeneration's cost
