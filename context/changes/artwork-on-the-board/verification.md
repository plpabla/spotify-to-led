---
change_id: artwork-on-the-board
roadmap_item: S-04
date: 2026-11-09
---

## Conventions

The repository is public, so every real value is a placeholder: `pi` is the SSH alias the deploy script targets. The Pi account `pi-n8n` is named because the foundation docs already name it. Board observations are the owner's; log lines are read from `~/.pm2/logs/spotify-to-led-out.log` over SSH. Times are the Pi's local time (CET). Cover URLs are Spotify's public CDN image URLs and are shortened here.

The Pi's checkout was switched from `main` to `feat/artwork-on-the-board` for this rollout. Switch it back to `main` after the merge, or later deploys pull the feature branch.

## Phase 1: Rendering bake-off and marker (2026-11-09)

The bake-off script (`scripts/cover_bakeoff.py`) was run on the Pi with the service stopped. Two first runs drove the PNG stub instead of the board: from a shell, `load_settings()` reads the checkout's `.env`, which sets no `BOARD_TRANSPORT`, and only pm2 points the service at `/etc/spotify-to-led/env`. The script printed `connected to the stub board (32x32)` and wrote `var/frames/latest.png` while the board kept the play triangle. The fix is the env-file line in the script's docstring (`1fdc905`):

```
SPOTIFY_TO_LED_ENV_FILE=/etc/spotify-to-led/env uv run python scripts/cover_bakeoff.py <urls>
```

| Check | Result |
| --- | --- |
| Treatments shown on the board | plain area downscale, contrast/saturation-boosted downscale, 16-colour palette; three Spotify covers |
| Owner's pick | first boosted, then palette, then settled on the **plain area downscale** "for now"; none judged sufficient on its own, hence roadmap `S-08` |
| FR-007 bar written | every cover in the fixed set distinguishable from every other from desk distance |
| Marker, first look | a 2x2-cell amber block with a 1-cell (2 px) ring was too big |
| Marker, second look | ring changed to 1 px at every size (`3a04a11`): 6x6 px in all at 32x32; owner: "looks good now" |

The set was three covers, fewer than the 6–10 the plan suggested.

## Phase 3: Covers on the board (2026-11-09)

The plan's 3.4 and 3.5 checks were written for the laptop and the PNG stub. The owner ran them on the Pi's real board instead, at `3f62df2`, after `git pull` and `pm2 reload`.

| Check | Result |
| --- | --- |
| 3.4 cover shows and changes on skip | owner: works |
| 3.5 marker after over 30 s offline, cleared on reconnect | **not run**: see 4.5 |

## Phase 4: Rollout (2026-11-09)

| Check | Result |
| --- | --- |
| `scripts/deploy.sh` at `d0aa716` | exit 0, `/healthz` answered `{"status":"ok"}` |
| `uv run pytest` at `d0aa716` | 270 passed, 1 skipped (the POSIX file-mode test, on Windows) |

### NFR-01 (item change to board within 5 s)

From the log, the time from the reader noticing a new item to the board drawing its cover is under 2 s:

```
17:48:42,008 spotify.reader: Spotify playback: playing track '<track 1>'
17:48:42,928 covers: cover ready: https://i.scdn.co/image/...1cdcd24e
17:48:43,814 display: board presentation: cover https://i.scdn.co/image/...1cdcd24e
17:48:57,690 spotify.reader: Spotify playback: playing track '<track 2>'
17:48:58,152 covers: cover ready: https://i.scdn.co/image/...3b09eb38bd
17:48:59,051 display: board presentation: cover https://i.scdn.co/image/...3b09eb38bd
```

The reader polls every 3 s, so the worst case from a skip is about 3 s plus this. During rapid skipping (17:49:07 and 17:49:10, two tracks 3 s apart) the board kept the previous cover and drew only the one that settled, as the changeover rule intends. A second track from an album already seen re-downloads its cover; that repeat is S-05's.

| Check | Result |
| --- | --- |
| 4.3 ten skips, each under 5 s | pass: 20 item changes 17:48–20:18, reader-to-board 0.9–2.0 s each (slowest 18:03:48 → 18:03:50.5); with the 3 s poll, every change is inside 5 s |
| 4.4 a podcast episode shows its own artwork | pass: 20:17:46 an episode drew its show image (`ab6765...`) |
| 4.5 internet cut for over 30 s marks the cover; clears on reconnect | **not run**: the owner unplugged the Pi's Ethernet at about 20:20, but the Pi stayed online over Wi-Fi; the log has no failed read and no `(stale)` presentation since 17:48. The owner chose to close S-04 without it. The marker is covered by unit and loop tests (break-checked) and its geometry was judged on the board in the bake-off, but it has not been seen triggered on hardware. Carried to S-06. |
| 4.6 pause keeps the cover; stop returns to idle | pass: pauses at 20:14:58 and 20:17:56 left the cover up (no presentation change); stops at 17:49:57 and 20:15:01 went to `idle` |
