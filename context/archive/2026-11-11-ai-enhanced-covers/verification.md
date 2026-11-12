# Verification: ai-enhanced-covers

## Phase 1: Board bake-off and requirements gate (2026-11-11)

### Setup

`scripts/cover_bakeoff.py --enhance` on the Pi, with the service stopped and
`SPOTIFY_TO_LED_ENV_FILE=/etc/spotify-to-led/env`. Model `black-forest-labs/flux-3-image-klein`,
prompt `enhance.ENHANCE_PROMPT` (the proof of concept's `MINIATURE_PROMPT`, verbatim). Each cover
was shown on the board as the plain downscale, the enhanced frame shrunk by BOX, and the enhanced
frame shrunk by LANCZOS. The covers were the four from the proof-of-concept notebook:

- `https://i.scdn.co/image/ab67616d0000b2737686c2cddf018f249e844344`
- `https://i.scdn.co/image/ab67616d0000b273e71db33f91c87243449d80f4`
- `https://i.scdn.co/image/ab67616d0000b27361172bdc4a7fb1c99e2f2170`
- `https://i.scdn.co/image/ab67616d0000b2730298ed8239cfc6c92d29fab8`

The plan asked for 6 to 10 varied covers. Four were judged; the owner judged the verdict clear
enough on those.

### Verdict (owner, from desk distance)

- Enhanced is better than the plain downscale on every cover.
- LANCZOS is slightly better than BOX for shrinking the model's answer, so FR-019 names LANCZOS.
- The model drew text on one cover despite the prompt ("Polly" on the second). Accepted: there,
  the text was the cover's most important part. The prompt stays verbatim.
- The model stays as it is for now. The owner may try other models later, but found none as
  cheap.

### Latency and cost

Every call cost $0.0090. Wall times per generation, from the script's own timer:

| Where  | Run | Times (s)                  |
| ------ | --- | -------------------------- |
| Laptop | 1   | 10.4, 9.3, 9.3             |
| Laptop | 2   | 9.5                        |
| Pi     | 1   | 8.9, 8.9, 9.2, 8.3         |
| Pi     | 2   | 9.9, 9.2, 11.4, 20.1       |

On the Pi: 8 calls, typically about 9 s, one outlier at 20.1 s. The Phase 3 per-call timeout
(90 s in the plan) has ample headroom over that.

### Bluetooth push failure, and the fix (`734d744`)

The first two `--enhance` runs on the Pi generated every cover and connected to the board, then
failed on the very first push with `bleak.exc.BleakGATTProtocolError: (UNLIKELY_ERROR: 14)`. The
service was stopped for the second run. The same frames pushed fine in a plain run, and so did an
`--enhance` run with a single cover. What failed was a connect made only after 40 to 60 s of
generation; a short wait did not fail. The root cause is not known. The script now connects
before generating and holds the link through the calls, as the service will in Phase 3, and the
run then worked. Phase 3 relies on the same pattern, a link held through a generation, which
this run exercised.

### Requirements revision

- `context/foundation/prd.md`: v2. FR-019 added (should-have) with its behaviour and acceptance
  bar, an NFR-01 note for FR-019, and the Non-Goal narrowed to animated covers only.
- `CLAUDE.md`: the hard rule now forbids animation, and allows AI-enhanced covers only as FR-019,
  off unless switched on.
- `context/foundation/roadmap.md`: M-1 closed (Milestone History), M-2 opened, and S-08's
  unknowns resolved.
- `context/foundation/tech-stack.md`, `context/foundation/infrastructure.md`: the OpenRouter call,
  `OPENROUTER_API_KEY` in `/etc/spotify-to-led/env`, and the cover now leaving the Pi.

## Phase 4: Pi rollout (2026-11-11)

`3be6a3f` deployed to the Pi with `scripts/deploy.sh` from the branch `feat/ai-enhanced-covers`;
`/healthz` answered and `/status` carried the new fields at once
(`enhancement: {available: true, enabled: true, paused_until: null, cover: "generating"}`, the
board on the placeholder while the playing cover was enhanced). `OPENROUTER_API_KEY` was already
in `/etc/spotify-to-led/env` from the Phase 1 bake-off.

The owner then ran every board check, and reported that all behaved as expected:

| Check | Result |
| --- | --- |
| 4.3 a never-seen album shows the placeholder, then the enhanced cover; a same-album track or a replay shows it at once, with no OpenRouter request | as expected |
| 4.4 flipping the checkbox off and on mid-track redraws the board each way within a few seconds | as expected |
| 4.5 the checkbox stays ticked across `pm2 restart spotify-to-led` | as expected |
| 4.6 with a wrong key, the page shows generation paused until 00:00 UTC and the board shows the plain cover | as expected |
| 4.7 during a forced Spotify outage, an enhanced cover gets the staleness marker after 30 s | as expected |

Phase 3's own board check (3.3) had already passed on the Pi before this phase: a new album went
from the placeholder to the enhanced cover, and a replay came from the cache with no new request.

## Deviations from the plan

Decided during implementation or found by the implementation review (`reviews/impl-review.md`).
Each is tested or documented in the code:

- **The enhanced frame is shrunk with LANCZOS** (`rendering.render_enhanced`), not BOX, as the
  Phase 1 bake-off chose. `Enhancer` takes the board size so it can return the finished frame.
- **An enhancer is built only when Spotify is configured as well as `OPENROUTER_API_KEY`**,
  because it shares the Spotify HTTP client; without Spotify there are no covers to enhance.
- **The give-up memory is keyed by the source URL** (the item's widest artwork, the image
  actually sent), while cache entries are keyed by the plain URL. `forget()` (a flip) clears the
  give-up memory but not the midnight pause.
- **`/status` `enhancement.cover` is `null` in more cases than the plan said.** It is null when no
  cover is wanted, but also when the switch is off (or no key), and while the plain cover is
  still downloading or failing. Otherwise a new item would read "plain", then "generating", as if
  it had fallen back first.
- **A failed download of the 640 px source falls back to the plain cover without a retry.** It
  is tried again the next time the cover is wanted enhanced.
- **Each retry logs its own INFO line.** The outcome (success, give-up, pause) is still one line.
- **The flip-redraw integration test is in `tests/test_display.py`**, with a real `CoverLoader`
  and a scripted network, not in `tests/test_app.py` as the Testing Strategy said.
- **Review fixes, after Phase 4:** an unexpected error in a generation now gives up on the image
  instead of killing the loader task. A damaged PNG answer and an undecodable body count as
  failures. Decoding and shrinking run in a worker thread.
