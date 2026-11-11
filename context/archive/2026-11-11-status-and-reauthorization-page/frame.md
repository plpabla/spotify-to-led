# Frame Brief: Status and re-authorization page (S-07)

> Framing step before /10x-plan. This document captures what is *actually*
> at issue, separated from what was initially assumed.

## Reported Observation

The owner wants roadmap S-07 (`context/foundation/roadmap.md`, "S-07: One page reports what the
service believes, and re-authorizes Spotify"), plus cover image-formatting customization on the
same page (more contrast, a reduced palette), and later an AI-enhancement option there too.

## Initial Framing (preserved)

- **User's stated cause or approach**: the status page is the natural control surface, so cover
  formatting options belong on it, in the same change.
- **User's proposed direction**: one change delivering S-07 plus selectable image treatments,
  with AI enhancement added to the page later.
- **Pre-dispatch narrowing**: the owner dropped the formatting controls once the cache
  interaction was raised ("I didn't think about caching ... let's skip it"); S-07 closing M-1 is
  the outcome that matters; for AI, "just leave room", and a placeholder is a later slice.

## Dimension Map

1. **Scope: S-07 as specified** -- status, link health, failure cause, re-authorization
   (US-06, FR-004, FR-005, FR-015). The milestone's last open item.
2. **Scope: selectable render treatments** -- assumes the treatments are wanted and cheap to
   add behind a page control.  <- initial framing
3. **Scope: AI enhancement on the page** -- assumes it is in reach now.

## Hypothesis Investigation

Investigated inline: the evidence sat in three documents and two source files, so sub-agents
would have been padding.

| Hypothesis | Evidence | Verdict |
| --- | --- | --- |
| S-07 is the work that closes M-1 | `roadmap.md` M-1 "Done when": every F/S item done except S-08; S-07 is the only one still `proposed` | STRONG |
| Formatting controls are cheap and wanted | Already tried: the S-04 bake-off showed boosted and 16-colour on the board; the owner kept the plain downscale (`prd.md:170`, `context/archive/2026-11-09-artwork-on-the-board/verification.md:23-24`). Cost: the cache key has no renderer identity (`frame_cache.py:13-18`), so a treatment needs a key suffix and a re-download per cover per treatment, or a wiped cache. Not in any FR. | WEAK |
| AI enhancement belongs here | PRD v1 Non-Goal (`prd.md:286`); roadmap S-08 is deferred to M-2 and needs a PRD revision first | NONE for this change |

## Narrowing Signals

- The owner ranked "S-07 closes M-1" as the outcome that matters.
- Once the cache cost was visible, the owner dropped the formatting controls.
- AI: leave room only; any page placeholder is a later slice.

## Cross-System Convention

The project's own convention, applied to every earlier slice, is one roadmap slice per change,
with work outside the PRD going through a PRD revision first (S-08 is the precedent). Cutting
back to plain S-07 follows it.

## Reframed (or Confirmed) Problem Statement

> **The actual problem to plan around is**: deliver S-07 as the roadmap specifies it: one page,
> independent of the board task, that reports the believed-playing item (or that nothing is),
> board link health, which cause is behind a failed read, and offers Spotify re-authorization,
> never exposing the refresh credential.

The formatting controls are deferred, not ruled out. Correction to the reason recorded in the
answers: caching does not block them. `frame_cache.py` already reserves a suffix after the id
for variants, so the cost is one cached set and one re-download per cover per treatment. They
also need an FR and a fresh look on the board, since the bake-off judged both treatments and kept
neither. AI enhancement stays in S-08.

## Confidence

**HIGH** -- the roadmap names S-07 as the last open M-1 item, the owner chose it as the point of
the change, and the owner removed the extras.

## What Changes for /10x-plan

Plan plain S-07 and nothing more: no render options, no AI toggle or placeholder. The temporary
JSON `/playback` (`src/spotify_to_led/main.py:232`) is what S-07 replaces. Carry forward the
roadmap's constraints: no credential exposure, the page stays independent of the board task, and
no auth in service code.

Possible follow-ups, recorded here and not planned:
- Selectable cover treatments (contrast, palette) with a renderer suffix in the cache key; needs
  an FR first.
- An AI-enhancement placeholder on the page, which belongs with S-08.

## References

- `context/foundation/roadmap.md` -- S-07, S-08, M-1
- `context/foundation/prd.md:152-170, 219, 286`
- `src/spotify_to_led/frame_cache.py:13-18`, `src/spotify_to_led/main.py:164-253`
- `context/archive/2026-11-09-artwork-on-the-board/verification.md:23-24`
