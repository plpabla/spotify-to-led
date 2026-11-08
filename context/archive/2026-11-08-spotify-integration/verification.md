# Live verification: spotify-integration (S-01)

Observations from the Pi, against the Phase 3 manual checks in `plan.md`. No token, code or verifier value appears here.

## Baseline before deploy (2026-11-08)

- Pi checkout switched from `main` (`219098d`) to `feat/spotify-integration` (`6e1b7fa`) for verification; to be switched back once the change merges.
- `/etc/spotify-to-led/env`: sha256 `d156d4eb31a7ba0059df494f6e02c6a99bf7749e1a878afde7e861dcc9ad3b96`, mtime `2026-11-06 20:54:31 +0100`, `-rw------- pi-n8n pi-n8n`.

## 3.4 Deploy (2026-11-08)

`scripts/deploy.sh` pulled `6e1b7fa`, reloaded under pm2 and `/healthz` answered `{"status":"ok"}` (the first poll hit the app before it listened, the next one passed). `pm2 ls` shows `spotify-to-led` online as `pi-n8n`. The board was not reachable over Bluetooth at the time (`device ... not found`), unrelated to S-01.

## 3.5 Before authorization (2026-11-08)

`/playback`:

```json
{"configured":true,"status":"unknown","item":null,"failure":"authorization_needed","failure_since":"2026-11-08T14:18:56.013862Z","last_read_at":null,"authorized_at":null}
```

Log shows one `WARNING spotify_to_led.spotify.reader: Spotify authorization needed: open /authorize to grant access` and no further reader lines: no polling. `var/` did not exist yet, so no token file.

## 3.6 Consent and callback (2026-11-08)

The owner opened `/authorize` through the Access-gated hostname. The consent screen listed only the currently-playing permission (owner's observation) and `/callback` reported success. The saved grant's `scope` is exactly `user-read-currently-playing`. The owner went through `/authorize` twice; the second grant replaced the first, as designed (last call wins).

## 3.7 Token file and env file (2026-11-08)

- `var/spotify-token.json`: `-rw------- pi-n8n pi-n8n`, keys `access_token, authorized_at, expires_at, refresh_token, scope`; `expires_at` is `authorized_at` + 1 h.
- `/etc/spotify-to-led/env`: sha256 and mtime identical to the baseline.

First read after authorizing:

```json
{"configured":true,"status":"playing","item":{"kind":"track","id":"6Mf1De32Pilct2JjfgvqxB","name":"China Doll","subtitle":"Bert Weedon","artwork":[{"url":"https://i.scdn.co/image/ab67616d0000b273fdfe01b6278373664237361e","width":640,"height":640},{"url":"https://i.scdn.co/image/ab67616d00001e02fdfe01b6278373664237361e","width":300,"height":300},{"url":"https://i.scdn.co/image/ab67616d00004851fdfe01b6278373664237361e","width":64,"height":64}]},"failure":null,"failure_since":null,"last_read_at":"2026-11-08T14:21:49.436939Z","authorized_at":"2026-11-08T14:20:31.090824Z"}
```

## Finding: authorization code in the access log (fixed in `0a181ea`)

uvicorn's access log wrote the full `/callback?code=...&state=...` request line into the pm2 log, and `httpx2` logged every poll at INFO (about 1,200 lines an hour). Fixed by a filter that drops query strings from `uvicorn.access` lines and by raising the `httpx2` logger to WARNING, with tests. After redeploying, `pm2 flush spotify-to-led` removed the two spent codes; a scan of every current and rotated `spotify-to-led*` log found no `callback?code=`.

## 3.10 Restart (2026-11-08)

After the `0a181ea` redeploy and again after `pm2 restart spotify-to-led`, the reader resumed reading within one interval with the same `authorized_at` (`2026-11-08T14:20:31Z`): no re-authorization.

## 3.8 Play, pause, episode, stop (2026-11-08)

Reader log (Pi local time, CET), one line per change and none in between:

```
15:24:07 paused track "The Master's Call"
15:24:16 playing track "The Master's Call"
15:25:22 playing episode '103: Cloud Hopper'
15:25:48 playing track 'Monastery - lofi version (slowed + reverb)'
15:25:51 nothing_playing (no item)
15:27:05 playing track 'Seraph'      (played from the owner's phone)
15:27:12 nothing_playing (no item)
```

Paused keeps the same item, an episode reads as `episode`, and stopping playback reads as `nothing_playing` rather than `paused`. A different device on the same account is read the same way. The owner saw each change on `/playback` promptly. After stopping:

```json
{"configured":true,"status":"nothing_playing","item":null,"failure":null,"failure_since":null,"last_read_at":"2026-11-08T14:28:47.446179Z","authorized_at":"2026-11-08T14:20:31.090824Z"}
```

The board task logged `board task failed` at 15:24:55: the board was off, unrelated to S-01.

## 3.9 Private session (2026-11-08)

Skipped by owner decision; not observed. What a private session returns from `currently-playing` remains open. Related observation instead: playback started on the owner's phone is read like any other device on the account (see 3.8).

## 3.11 Refresh after an hour (2026-11-08)

The grant from 15:30:28 CET was refreshed by the reader at 16:29:30 CET, 60 s before expiry as designed. Checked at 16:34:

- `var/spotify-token.json` rewritten at 16:29:30, still `600 pi-n8n`; `expires_at` advanced from `15:30:28Z` to `16:29:30Z`; `authorized_at` unchanged at `14:30:28Z`; scope still `user-read-currently-playing`.
- `/playback`: `nothing_playing`, `failure: null`, `last_read_at` current.
- Log since the 15:24 flush: no `rate_limited` and no httpx2 error. One transient reader failure, `network (ReadTimeout)` at 16:21:45, recovered at 16:21:56 (a live run of the network path: item kept, failure cleared on the next good read). The only ERROR is the board task (board off). Corrected after the fact: the 16:34 check counted this line but reported it as the board traceback.

## 3.12 Revoke and re-authorize (2026-11-08)

First attempt (15:2x to 15:30): the owner removed access and re-authorized within a few minutes. The reader never noticed the revoke: the access token it held stayed valid, no refresh fell in that window, and the log shows no failure. Re-authorizing replaced the grant with no restart. Takeaway: a revoke is only detected at the next refresh, up to an hour later.

Second attempt: access removed at 16:35 CET (reader still reading normally at 16:35:32). At the next refresh:

```
17:28:32 WARNING spotify_to_led.spotify.reader: Spotify authorization needed: open /authorize to grant access
```

`var/` is empty (token file discarded at 17:28), nothing further logged by the reader after the warning (no polling), and `/playback` at 17:31:

```json
{"configured":true,"status":"nothing_playing","item":null,"failure":"authorization_needed","failure_since":"2026-11-08T16:28:32.800530Z","last_read_at":"2026-11-08T16:28:29.658440Z","authorized_at":null}
```

Re-authorized at 17:32:20 CET: `Spotify authorization completed`, then `Spotify read recovered` 0.1 s later. New `var/spotify-token.json` (`600 pi-n8n`), new `authorized_at` `16:32:20Z`, `/playback` back to `nothing_playing` with `failure: null`. pm2 shows 2 h uptime and no new restart: recovery needed no restart.

## 3.13 Logs free of secrets (2026-11-08, 17:32)

Every `spotify-to-led*` pm2 log on the Pi (current and both rotated days, out and error) scanned on the Pi without printing contents: the current access token and refresh token appear in none, and none contains `code=`, `verifier` or `Bearer`. Earlier tokens and codes were not scannable by value; the `/callback` code leak found at 3.7 was fixed in `0a181ea` and the affected log flushed.
