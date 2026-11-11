---
change_id: staleness-marker-and-display-rule
roadmap_item: S-06
date: 2026-11-11
---

## Conventions

As for S-04 and S-05: `pi` is the SSH alias the deploy script targets, log lines are read with
`pm2 logs spotify-to-led` over SSH, and times are the Pi's local time (CET). Cover URLs are
shortened to the distinguishing end of the image id.

The Pi's checkout was switched from `main` to `feat/staleness-marker-and-display-rule` for this
rollout and deployed with `scripts/deploy.sh` at `041bc76` (`/healthz` answered
`{"status":"ok"}`). Switch it back to `main` after the merge, or later deploys pull the feature
branch.

## Phase 1: Marker on every presentation (2026-11-10)

1.3, the laptop check with the stub transport and the token file moved aside, was **not run**:
the owner skipped it in favour of the hardware. Phase 2 proves the same behaviour on the board,
and the rule is covered by unit and loop tests that were break-checked (13 tests went red with
only covers markable; the loop test went red with the marker drawn on covers only).

## Phase 2: On the Pi (2026-11-11)

The outage is `sudo iptables -I OUTPUT -p tcp --dport 443 -j REJECT`, removed after 90 s by a
`nohup sh -c "sleep 90; sudo iptables -D …"` started alongside it, so the rule goes even if the
SSH session drops. It also cuts the reader's open keep-alive connection (`ReadTimeout`).

| Check | Result |
| --- | --- |
| 2.1 `uv run pytest` on the Pi | pass: 362 passed |
| 2.2 no REJECT rule left | pass: `sudo iptables -S OUTPUT` lists only `-P OUTPUT ACCEPT` after each window |
| 2.3 a playing cover gains and loses the marker | pass, window 1 below |
| 2.4 idle gains and loses the marker | pass, window 2 below |
| 2.5 the marker reads from across the room | pass (owner): legible over a cover in a lit room, and over idle in a lit room and a dark one, with no glare problem next to the dim note |

Window 1, a cover playing:

```
13:18:47  rule inserted
13:18:55  Spotify read failing: network (ReadTimeout)
13:19:18  board presentation: cover …aee1 (stale)
13:20:17  rule removed
13:20:19  Spotify read recovered (now 'We're Not In Love')
13:20:20  board presentation: cover …aee1
13:20:21  board presentation: cover …962b
```

Window 2, nothing playing. A first attempt at 13:23:45 tested a cover again: playback resumed on
another device at 13:23:38, seven seconds before the rule went in (stale at 13:24:15, cleared at
13:25:17). The retry checked the log showed idle, unchanged for 10 s, before inserting the rule:

```
13:33:08  board presentation: idle
13:33:26  rule inserted
13:33:33  Spotify read failing: network (ReadTimeout)
13:33:56  board presentation: idle (stale)
13:34:58  Spotify read recovered
13:34:58  board presentation: idle
```

The marker comes on about 30 s after the last **successful** read, not after the first failure:
`_is_stale` also ages `last_read_at`, so it showed 23 s after the first failed read in both
windows. That is the rule as specified, not a deviation.

Not exercised on hardware: the marker over the playing placeholder and over the no-artwork disc.
Both are covered by unit tests, and the marker's footprint is tested to clear both glyphs at 16,
32 and 64 px.
