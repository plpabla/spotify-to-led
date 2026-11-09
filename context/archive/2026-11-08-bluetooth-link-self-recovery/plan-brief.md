# Bluetooth link self-recovery — Plan Brief

> Full plan: `context/changes/bluetooth-link-self-recovery/plan.md`
> Research: `context/changes/bluetooth-link-self-recovery/research.md`

## What & Why

Roadmap S-03 / FR-013: when the board is switched off and on, or carried out of range and back, the link and the correct display must return with no human action. S-02 already built most of this, but research found a gap. After a drop, the library's own reconnect loop reuses one Bluetooth client for every attempt after the first. A marginal-range failure can therefore strand the link until the service restarts.

## Starting Point

The display loop retries the first connect forever, with a fresh client per failure, and this is proven on cold boots. Drops after that are left to the library's auto-reconnect, which is fresh only on its first attempt. The board is redrawn whenever the link comes up again. No out-of-range test has ever been run.

## Desired End State

The display loop is the only thing that connects or reconnects. Any drop sends it back into the same proven retry, with a fresh client every time, and it redraws when the link returns. On the Pi, the board came back by itself after a power-cycle, about 2 min out of range, about 15 min out of range, and about 5 min at the edge of range. FR-013 is signed off in `verification.md`, and the 7-day run is restarted on this build.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Close the reconnect gap | Library auto-reconnect off; display loop reconnects on `link_state` DOWN | One retry path that is already tested and hardware-proven, with no new private-API patching | Plan |
| Silent link death | Keep the library heartbeat on (30 s) | Its dead-link close still notifies listeners with auto-reconnect off, so DOWN stays reliable | Research |
| Backoff after a drop | Restart at 5 s, up to 60 s, same as the cold boot | Same function, same log lines | Plan |
| Deploy timing | Deploy as soon as Phase 1 passes; restart the 7-day run | The run should cover the build that actually ships | Plan |
| Range test | Power-cycle + ~2 min + ~15 min + ~5 min at the edge; pass = correct frame within ~2 min of return, 0 restarts | Exercises the multi-attempt and found-but-timed-out paths where the gap lives | Plan |
| Under-voltage | Record `get_throttled` before and after; do not block | Keeps S-03 moving while making a power-caused failure recognisable | Plan |

## Scope

**In scope:**
- Adapter: auto-reconnect off
- Display loop: reconnect on DOWN
- Unit tests at both layers
- Docstrings and `CLAUDE.md` wording
- Pi deploy, four physical tests, `verification.md`, 7-day restart

**Out of scope:**
- Link state on a page (S-07)
- A display while disconnected
- Recovery-time bounds
- Library version change
- Power-supply fix
- Restarting a dead display task

## Architecture / Approach

`IDotMatrixBoardTransport` keeps the heartbeat on and the stale-client discard on every failed connect and every drop, but no longer lets the library retry. `run_display` checks `link_state` each sample. On DOWN it logs `board link lost; reconnecting`, awaits `_connect` (5 s → 60 s backoff, a fresh client each failure), then continues. The `connections` count rises, so the next sample redraws the current presentation.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Display loop owns reconnects | One retry path + unit tests + docs | Losing the `attempted` record across reconnect would skip the redraw |
| 2. Prove FR-013 on the Pi | Four physical tests, sign-off, 7-day restart | Edge-of-range is hard to reproduce exactly; under-voltage could confound a failure |

**Prerequisites:** S-02 merged (done). The Pi checkout is switched to `feat/bluetooth-link-self-recovery` for the deploy. The owner is available for about 30 min of physical testing.
**Estimated effort:** about 1 session for Phase 1, plus about 1 hour on the Pi for Phase 2.

## Open Risks & Assumptions

- The gap is traced in source but not observed. Test (d) may or may not trigger it, so a pass is evidence of recovery, not proof that the trap was hit.
- The Pi is under-powered (`0x50000` in S-02), and a brownout during a test could look like a code failure.
- Out of range, BlueZ may take up to the supervision timeout or one heartbeat (30 s) to report the drop, and this adds to the recovery time.

## Success Criteria (Summary)

- The board returns to the correct frame by itself after each of the four physical tests, with 0 pm2 restarts.
- `uv run pytest` is green, with new tests for drop → several failed reconnects → redraw.
- FR-013 is signed off and the 7-day run is restarted, both recorded in `verification.md`.
