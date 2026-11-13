# Select the AI enhancement model — Plan Brief

> Full plan: `context/changes/select-model/plan.md`

## What & Why

The owner wants to choose which OpenRouter image model redraws covers (FR-019), from a drop-down on the status page, with the choices defined in a text file they edit. Since enhanced frames stay one per cover, the page also gets a button to regenerate the current cover with the selected model.

## Starting Point

The model is the env setting `OPENROUTER_MODEL` (default `black-forest-labs/flux-3-image-klein`), fixed at startup in the `Enhancer`. Owner choices (AI switch, daily limit) already live in `var/settings.json` via `PUT /settings`, and the enhancer already reads the daily limit live. Enhanced frames are cached as `<id>.ai.png`, with no model in the key.

## Desired End State

`models.txt` in the repo lists model ids, first one the default. The status page shows a drop-down of exactly those ids; a pick is saved, survives restarts and applies to the next generation without a restart; editing the file updates the drop-down on the next poll. A "Regenerate cover" button redraws the cover on show with the selected model and replaces its cached enhanced frame.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) |
| --- | --- | --- |
| List file | Tracked `models.txt`, one id per line, `#` comments | Versioned, deployed with the checkout, trivial to parse |
| Default / stale choice | First line of the list; a saved id no longer listed falls back to it | The file is the single source of truth |
| Missing / empty list | Warn, act as `[flux-3-image-klein]`; `OPENROUTER_MODEL` removed | One place names models; a broken file never stops the service |
| Cache across a switch | Shared `<id>.ai.png`, one per cover | No re-spend on switching; regeneration is explicit |
| On model switch | Enhancer forgets give-ups (pause stays) | A new model is the natural retry for covers the old one failed on |
| List reload | Read on every request | File edits show without a restart |
| Regenerate | `POST /regenerate` (JSON `{}`), current cover only, overwrites `.ai.png`; refused when off, paused, at limit, or no finished cover | Lets the owner see a new model on known covers, with spend still guarded |
| Regenerate failure | Board returns to the previous enhanced frame; cache untouched | A failed retry should not downgrade what was shown |

## Scope

**In scope:** `models.txt` + reader; `OwnerSettings.model`; live model in `Enhancer`; `PUT /settings` `model` (validated against the list); `/status` `models`/`model`; page drop-down; `POST /regenerate` + button; bake-off script; docs.

**Out of scope:** per-model cache, labels/prices in the list, editing the list from the web, per-model limits, bulk regeneration, prompt or retry-policy changes.

## Architecture / Approach

A new `models.py` reads the list and resolves the model in use (saved if listed, else first). The enhancer gets the model as a callable, read at each generation, like the daily limit. Routes only change saved state. Regeneration is a `CoverLoader.regenerate(url, source)` call on the wanted cover that reuses the existing GENERATING → READY path, so the board shows the plain cover with the spend bar meanwhile.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Model list and live model | `models.txt`, `models.py`, `OwnerSettings.model`, live model in the enhancer, env setting removed | Breaking the bake-off script or old settings files |
| 2. Routes | `PUT /settings` model + forget; `/status` list and model in use | Accepting an unlisted model |
| 3. Page and docs | Drop-down with poll guards; foundation docs, CLAUDE.md, `.env.example` | A poll overwriting the select mid-choice |
| 4. Regenerate the current cover | Loader regenerate, `POST /regenerate`, button | Plain frame missing after an enhanced cache hit; cross-site POST spending money |

**Prerequisites:** none beyond the current `main`; an OpenRouter key on the Pi for manual checks.
**Estimated effort:** ~2–3 sessions across 4 phases.

## Open Risks & Assumptions

- A Pi that set a non-default `OPENROUTER_MODEL` silently returns to the list's first id until the owner lists/picks that model (noted in Migration Notes).
- Models differ in price; the daily limit counts reported cost, so a pricier model just reaches it sooner.
- Assumes every listed model accepts the same Images API request and prompt; a model that rejects it is given up on per cover, as today.

## Success Criteria (Summary)

- The owner switches model from the page, and the next new cover is drawn by it, with no restart.
- Pressing Regenerate shows the current cover redrawn by the selected model, and it stays that way on later plays.
- Nothing changes at deploy until the owner picks something.
