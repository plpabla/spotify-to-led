---
change_id: all-item-types-and-frame-reuse
roadmap_item: S-05
date: 2026-11-10
---

## Conventions

As for S-04: `pi` is the SSH alias the deploy script targets, log lines are read from
`~/.pm2/logs/spotify-to-led-out.log` over SSH, and times are the Pi's local time (CET). Cover
URLs are Spotify's public CDN image URLs, shortened here to the distinguishing part of the id.

The Pi's checkout was switched from `main` to `feat/all-item-types-and-frame-reuse` for this
rollout and deployed by the owner at `b6aa9d0` (the service came up at 19:47:03). Switch it back
to `main` after the merge, or later deploys pull the feature branch.

## Phase 1: The no-artwork disc (2026-11-10)

The disc was judged from a 10x preview of the 32 px frames next to idle and the placeholder,
not on the board: a solid grey (160) disc 24 px across with a 4 px hole. Owner: "looks good".

## Phase 2: Frame cache (2026-11-10)

2.3 was written for the laptop with the stub transport, but the laptop has no Spotify
configured. A stand-in run drove the real `CoverLoader` and `FrameCache` twice against a real
cover URL with one cache directory: the first start downloaded (`GET … 200 OK`, `cover ready`)
and wrote `32/<id>.png`; the second start made no request and logged `cover ready from cache`.
The owner deferred the check to the hardware, where 3.5 proves it on the real service.

## Phase 3: On the Pi (2026-11-10)

| Check | Result |
| --- | --- |
| 3.1 `uv run pytest` on the Pi | pass (owner) |
| 3.2 a podcast episode shows its artwork | pass: 19:47:57 and 19:48:16, two episodes drew their own images (`ab6765…`, `ab6766…`) |
| 3.3 a no-artwork item shows the disc, legible from the desk | **not run**: the owner skipped it. The disc is covered by unit and loop tests (break-checked) and was judged on a preview, not on the board. |
| 3.4 A → B → A shows A from the cache, no download | pass, see below |
| 3.5 `pm2 restart` brings the cover back from the cache | pass, see below; also proves 2.3 |
| 3.6 a cold boot brings the cover back from the cache | **not run** |
| 3.7 one `<id>.png` per cover seen | pass: four files in `var/covers/32/` after four covers (two tracks, two episodes) |

### 3.4: back to an item already seen

```
19:47:56,903 reader:  Spotify playback: playing episode '<episode A>'
19:47:57,548 covers:  cover ready: …/ab6765630000f68d…
19:48:16,247 reader:  Spotify playback: playing episode '<episode B>'
19:48:16,964 covers:  cover ready: …/ab6766630000703b…
19:48:58,210 reader:  Spotify playback: paused episode '<episode A>'
19:48:59,067 covers:  cover ready from cache: …/ab6765630000f68d…
19:48:59,067 display: board presentation: cover …/ab6765630000f68d…
```

The return to A made no request and drew on the same display sample as the cache hit, under
1 s from the reader noticing the change; a download took 1.5–2 s in the same session.

### 3.5: across a restart

With episode A's cover cached and A paused, `pm2 restart spotify-to-led` at 19:50:

```
19:50:15,383 reader:  Spotify playback: playing episode '<episode A>'
19:50:15,620 display: board connected
19:50:15,623 covers:  cover ready from cache: …/ab6765630000f68d…
19:50:15,624 display: board presentation: cover …/ab6765630000f68d…
```

The first presentation after the restart is the cover, from disk, 4 ms after the link came up,
with no placeholder in between. For contrast, the deploy at 19:47:03 started with an empty
cache and showed the placeholder for about a second before the first download landed.
