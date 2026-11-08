# Unattended idle board (S-02) Implementation Plan

## Overview

Make the board come back by itself after a cold boot of the Pi and show the right frame for what the Spotify reader believes: the idle display (FR-011) when nothing is playing or nothing has been read yet, and a temporary "playing" placeholder while something plays, until S-04 brings artwork. This is the roadmap's north star: landing it makes the PRD's primary success criterion true and starts the 7-day unattended clock (NFR-02).

## Current State Analysis

- The lifespan starts one board task, `push_test_pattern` (`src/spotify_to_led/main.py:80-90`), which calls `transport.connect()` once and pushes the four-quadrant test pattern once. Any failure is logged and the task ends for good.
- The library's reconnect loop is only started from the disconnect callback (`.venv/.../idotmatrix/connection_manager.py:609-613`), so it never retries a failed *initial* connect. The transport's own docstring records this as open for S-02/S-03 (`src/spotify_to_led/board/idotmatrix_transport.py:7-14`).
- Observed on the Pi during F-02 (`context/archive/2026-11-06-pm2-supervised-service/verification.md:76`): after a reboot the board connects when it is already on; a process started while the board is off fails its first connect once and never retries.
- The Spotify reader already publishes a `PlaybackSnapshot` with `status` (`UNKNOWN`, `PLAYING`, `PAUSED`, `NOTHING_PLAYING`) kept apart from `failure` (`src/spotify_to_led/spotify/playback.py:22-79`), read via `SpotifyReader.snapshot` (`src/spotify_to_led/spotify/reader.py:111-115`). It polls every 3 s. Without `SPOTIFY_CLIENT_ID` there is no reader (`main.py:112-120`).
- A recorded owner decision: a paused item stays the believed item, with its cover (`playback.py:28`), so paused is not idle.
- Supervision (pm2, exponential backoff, 10 s `kill_timeout`, on-disk rotated logs, persistent journald) is done by F-02 and needs no change. The app never exits on a BLE fault, so readiness after boot is purely an in-process concern (`context/foundation/infrastructure.md`, risk register row 1).
- `frames.py` holds only `test_pattern`, a diagnostic used by the transport and stub tests.

## Desired End State

Cutting power to the Pi and restoring it, with nobody touching anything, results in the board showing the idle display by itself whenever nothing is playing; if the board was off during boot, switching it on later is enough, with no restart. While something plays or is paused the board shows the temporary placeholder, and it returns to idle when playback stops. Each connect attempt and each frame change is in the pm2 log. The 7-day observation has a recorded start.

### Key Discoveries:

- `IDotMatrixClient.connect()` can be called again after a failure: it re-enters `ConnectionManager.connect`, which reuses or recreates the transport and re-arms auto-reconnect (`connection_manager.py:217-252`).
- Once a first connect succeeds, the library's own auto-reconnect (5 s doubling to 60 s) and 30 s heartbeat take over drops (`idotmatrix_transport.py:61-62`). Re-pushing after such a reconnect is S-03.
- `transport.close()` is already bounded to 5 s and safe after a failed connect (`idotmatrix_transport.py:84-97`), and a stop with the first connect pending exits in under a second (`pm2 verification.md:69`), so cancelling a retrying task at shutdown is already safe.
- Tests replace the transport through `main.create_transport` and Spotify through `main.create_spotify_client` (`tests/test_app.py:82-149`); the new loop should follow the reader's pattern of injectable `sleep` for fast tests (`reader.py:84-102`).

## What We're NOT Doing

- No artwork, rendering or cover cache (S-04, S-05). The playing placeholder is temporary and is deleted by S-04.
- No staleness marker and no answer to PRD Open Question 2 (S-06). Read failures do not change the frame in this slice: a failing reader keeps its last status, so the board keeps its last presentation.
- No re-push after a link drop and reconnect, and no retry of a failed push (S-03). After a drop, the board keeps whatever frame it already has until the presentation next changes.
- No status page, no link state in `/healthz`, no change to `/playback` (S-07).
- No change to `ecosystem.config.js`, `scripts/deploy.sh` or the Pi's host configuration.
- No new configuration keys: the retry and poll cadences are module constants.
- Not gating on the 7 days: S-02 is done after the two power cuts; the 7-day run starts and its start is recorded.

## Implementation Approach

Split the decision from the side effects. A pure rule maps the reader's snapshot (or no reader) to one of two presentations, and each presentation maps to one frame. A single supervised display task owns the board: it connects, retrying forever with backoff, then samples the snapshot about once a second and pushes only when the presentation changes. Sampling keeps the reader untouched; at a 3 s read cadence plus a 1 s sample the board follows a change well inside NFR-01's 5 s. Like the reader, the loop catches and logs every failure itself so it can neither die nor take the server with it.

## Critical Implementation Details

- **Timing & lifecycle**: The reader must be constructed before the display task starts, because the task's snapshot source closes over it; with no reader the source returns `None` and the rule shows idle. At shutdown cancel the display task before `transport.close()`, as today.
- **Debug & observability**: These log lines are the evidence for NFR-02, so they must be readable in `pm2 logs` without tracebacks on every retry: one WARNING per failed connect attempt (exception type and message, next delay), a traceback only on the first failure of a run, INFO on connect success, and INFO on every presentation change. With the board off all day that is at most about 1,500 lines a day at the 60 s ceiling, well inside pm2-logrotate's 10 MB.

## Phase 1: Frames and display rule

### Overview

The two new frames and the pure rule that chooses between them. No I/O and no change to the running app yet.

### Changes Required:

#### 1. Idle and placeholder frames

**File**: `src/spotify_to_led/frames.py`

**Intent**: Add the idle display (FR-011) as a dim, small music-note glyph centered on black: calm enough for the many idle hours and clearly "on" rather than dead. Add a temporary "playing" placeholder, a bright play triangle filling roughly half the panel, unmistakably different from idle at a glance. Update the module docstring: `test_pattern` stays as the hardware orientation diagnostic but is no longer what the service shows.

**Contract**: `idle(size: int) -> Image.Image` and `playing_placeholder(size: int) -> Image.Image`, both RGB `(size, size)` for every supported size (16, 32, 64), drawn in code (no asset files). Idle invariant: every channel of every pixel is at most a module constant `IDLE_MAX_CHANNEL` (start at 64, tuned on the board in Phase 3), most pixels are black, and the lit pixels' bounding box is centered within one pixel. Placeholder invariant: lit pixels at full brightness on at least one channel. The exact glyph pixels are the implementer's design.

#### 2. Display rule

**File**: `src/spotify_to_led/display.py` (new)

**Intent**: The single place that decides what the board shows, as the start of FR-009's rule. In this slice: `PLAYING` and `PAUSED` choose the placeholder; `UNKNOWN`, `NOTHING_PLAYING` and "no reader" (`None`) choose idle. `failure` is deliberately ignored until S-06.

**Contract**: `class Presentation(StrEnum)` with `IDLE = "idle"` and `PLAYING = "playing"`; `select_presentation(snapshot: PlaybackSnapshot | None) -> Presentation`; `frame_for(presentation: Presentation, size: int) -> Image.Image`. Module docstring names the interim choices (UNKNOWN shows idle; failure ignored; placeholder is temporary) and the slices that replace them.

#### 3. Tests

**File**: `tests/test_frames.py`, `tests/test_display.py` (new)

**Intent**: Pin the frame invariants and the full status-to-presentation table, including `None` and a snapshot that carries a failure.

**Contract**: frames: size and mode for 16/32/64, idle brightness ceiling and centering, idle differs from placeholder, both pass `validate_frame`. Rule: one case per `PlaybackStatus`, `None`, and `NOTHING_PLAYING` / `PLAYING` with `failure` set (presentation unchanged).

### Success Criteria:

#### Automated Verification:

- New frame and rule tests pass: `uv run pytest tests/test_frames.py tests/test_display.py`
- Full suite passes: `uv run pytest`

#### Manual Verification:

- The idle and placeholder frames written to PNG through the stub look like a dim centered note and a bright play triangle

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 2: Display task with connect retry, wired into the app

### Overview

Replace the one-shot test-pattern task with a supervised display loop that survives a board that is absent at boot and keeps the board in step with the reader.

### Changes Required:

#### 1. Display loop

**File**: `src/spotify_to_led/display.py`

**Intent**: Own the board for the process lifetime. Connect, retrying forever with backoff so a cold boot that races BlueZ or a board that is switched on later both end connected with no restart. Then sample the snapshot source about once a second and push the presentation's frame whenever it differs from the last one attempted. A failed push is logged with its traceback and is not retried until the presentation changes (re-push is S-03). An unexpected error in an iteration is logged and the loop continues, mirroring `SpotifyReader.run`.

**Contract**: `async def run_display(transport: BoardTransport, size: int, current: Callable[[], PlaybackSnapshot | None], *, sleep: Sleep = asyncio.sleep) -> None`, running until cancelled and never raising anything but `CancelledError`. Module constants `CONNECT_RETRY_INITIAL_SECONDS = 5.0`, `CONNECT_RETRY_MAX_SECONDS = 60.0` (doubling, matching the library's reconnect backoff) and `SAMPLE_INTERVAL_SECONDS = 1.0`. Logging as described under Critical Implementation Details.

#### 2. Lifespan wiring

**File**: `src/spotify_to_led/main.py`

**Intent**: Start the display task instead of `push_test_pattern`, after the reader exists, with a snapshot source that returns `reader.snapshot` or `None` when Spotify is not configured. Remove `push_test_pattern` and the now-unused `frames` import. Update the module docstring to describe the display task.

**Contract**: The lifespan's task ordering at shutdown is unchanged in effect: reader task cancelled, Spotify client closed, display task cancelled, then `transport.close()` under its existing guard. `/healthz`, `/authorize`, `/callback` and `/playback` are untouched.

#### 3. Transport docstring

**File**: `src/spotify_to_led/board/idotmatrix_transport.py`

**Intent**: The "Recovery boundary" note says the cold-boot case is open for S-02 and S-03. Record that the failed-initial-connect case is now retried by the display loop, while re-pushing after a reconnect remains S-03 and nothing here satisfies FR-013.

**Contract**: Module docstring only.

#### 4. Tests

**File**: `tests/test_display.py`, `tests/test_app.py`

**Intent**: Prove the loop's behaviour with fake transports and a fake sleep, and update the app tests whose premise was "one frame, once".

**Contract**: Display loop tests:
- a transport that fails N connects then succeeds is connected and pushes idle, with retry delays 5, 10, 20, 40, 60, 60 s;
- only the first failure carries a traceback;
- no re-push while the presentation is unchanged across many samples;
- `NOTHING_PLAYING` → `PLAYING` → `NOTHING_PLAYING` pushes idle, placeholder, idle;
- a failing push is logged at ERROR with traceback and the loop keeps sampling, pushing again on the next change;
- cancellation during a retry sleep ends the task cleanly.

App tests:
- `test_the_lifespan_pushes_exactly_one_frame` becomes "without Spotify the lifespan pushes the idle frame once";
- `test_a_failing_connect_leaves_healthz_answering_and_is_logged` asserts the connect is retried (patched short delays) while `/healthz` answers;
- a test that the shutdown still closes the transport while the loop is mid-retry.

#### 5. Repository guidelines

**File**: `CLAUDE.md`

**Intent**: The opening paragraph says the lifespan "pushes one frame in a supervised background task" and that drawing on the board from Spotify state is not built yet. Update it to say the display task retries the board connect and shows the idle display or a temporary playing placeholder from the reader's state.

**Contract**: The opening paragraph of `CLAUDE.md` only.

### Success Criteria:

#### Automated Verification:

- Display loop and app tests pass: `uv run pytest tests/test_display.py tests/test_app.py`
- Full suite passes: `uv run pytest`

#### Manual Verification:

- On the laptop with the stub, `uv run python -m spotify_to_led.main` writes the idle frame to `var/frames/latest.png`, and with Spotify configured and music started it switches to the placeholder within 5 s, then back to idle when playback stops

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 3: Pi rollout and cold-boot verification

### Overview

Deploy, judge the two frames on the real board, prove both cold-boot paths with real power cuts, and start the 7-day clock. The owner runs the physical steps; results are recorded in `context/changes/unattended-idle-board/verification.md`, with placeholders for the public hostname and board address as in earlier verification files.

### Changes Required:

#### 1. Deploy

**File**: none (runs `scripts/deploy.sh`)

**Intent**: Promote the branch to the Pi through the existing script, and confirm in `pm2 logs spotify-to-led` that the board connects and the idle presentation is pushed.

**Contract**: `scripts/deploy.sh` exits 0; `pm2 ls` shows `spotify-to-led` online.

#### 2. Legibility and brightness

**File**: `src/spotify_to_led/frames.py` (only if tuning is needed)

**Intent**: Resolve the roadmap's S-02 unknown by looking: from normal desk viewing distance, idle must read as deliberate and "on" and be distinct from the placeholder. If the note is too faint or too bright, tune `IDLE_MAX_CHANNEL` or the glyph, redeploy, and record the final value.

**Contract**: The judged result and any tuned value in `verification.md`.

#### 3. Cold boots

**File**: `context/changes/unattended-idle-board/verification.md` (new)

**Intent**: Prove US-05/FR-014 with genuine power cuts, not restarts, as the roadmap requires. Run (a): board on, pull the Pi's power, restore it, touch nothing; the board shows idle by itself. Run (b): board off, pull and restore the Pi's power, wait until `pm2 logs` shows repeated connect retries, switch the board on; idle appears with no restart. Then play music and stop it once, and confirm placeholder then idle within 5 s.

**Contract**: For each run, record: the power-cut time, `pm2 ls` (online, restart count), the relevant `pm2 logs spotify-to-led --lines 200 --nostream` lines (retries, connect, presentation), and what the board showed.

#### 4. Start the 7-day clock and record the outcome

**File**: `context/changes/unattended-idle-board/verification.md`, `context/foundation/roadmap.md`

**Intent**: Record the start of the NFR-02 observation (the time of the last deploy or power cut, whichever is later). Write the legibility outcome into S-02's Unknowns line in the roadmap, marked resolved with the date, as earlier items were.

**Contract**: A dated "7-day run started" line in `verification.md`; S-02's Unknowns bullet in `roadmap.md` struck through and annotated.

### Success Criteria:

#### Automated Verification:

- Deploy exits 0 and `/healthz` answers: `scripts/deploy.sh`

#### Manual Verification:

- Idle is legible from desk distance and distinct from the placeholder on the real board
- Cold boot with the board on: after a power cut and restore, the board shows idle with no human action
- Cold boot with the board off: switching the board on minutes after boot brings idle up with no restart, and the log shows the retries
- Playing and stopping music on the Pi switches placeholder and idle within 5 s
- The 7-day run start and the legibility result are recorded in `verification.md` and the roadmap

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Testing Strategy

### Unit Tests:

- Frame invariants for every supported size; idle brightness ceiling and centering; idle and placeholder distinct.
- The full status-to-presentation table, including `None` and snapshots carrying a failure.
- Display loop with fake transports and fake sleep: retry backoff sequence and cap, traceback only on first failure, push-on-change only, push failure survival, clean cancellation.

### Integration Tests:

- `TestClient` lifespan with a counting transport: idle pushed once without Spotify; a failing connect is retried while `/healthz` answers; shutdown closes the transport mid-retry.

### Manual Testing Steps:

1. Laptop with the stub: idle PNG on start, placeholder while playing, idle after stopping.
2. Pi: deploy, judge both frames from the desk.
3. Pi: power cut with the board on; idle by itself.
4. Pi: power cut with the board off; switch on later; idle with no restart.
5. Pi: play and stop music; placeholder and idle within 5 s.

## Performance Considerations

Sampling an in-memory snapshot once a second is negligible. Pushes happen only on a presentation change, so a playing session costs one push per start and stop, not one per track. Worst-case latency from a playback change to the board is about 3 s (read) + 1 s (sample) + push time, inside NFR-01's 5 s.

## Migration Notes

None. No data, no configuration keys and no host changes. Rollback is the standard `git checkout <sha> && uv sync --frozen --no-dev && pm2 reload ecosystem.config.js --update-env`.

## References

- Roadmap item: `context/foundation/roadmap.md` (S-02, North star)
- PRD: `context/foundation/prd.md` (US-03, US-05, FR-009, FR-011, FR-012, FR-014, NFR-02, Open Questions 2 and 4)
- Pre-mortem and cold-boot risk: `context/foundation/infrastructure.md`
- F-02 reboot evidence: `context/archive/2026-11-06-pm2-supervised-service/verification.md:71-84`
- Current board task: `src/spotify_to_led/main.py:80-90`
- Supervised-loop pattern to mirror: `src/spotify_to_led/spotify/reader.py:148-159`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` -- <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Frames and display rule

#### Automated

- [x] 1.1 New frame and rule tests pass: `uv run pytest tests/test_frames.py tests/test_display.py` -- a39d0f8
- [x] 1.2 Full suite passes: `uv run pytest` -- a39d0f8

#### Manual

- [x] 1.3 The idle and placeholder frames written to PNG through the stub look like a dim centered note and a bright play triangle -- a39d0f8

### Phase 2: Display task with connect retry, wired into the app

#### Automated

- [x] 2.1 Display loop and app tests pass: `uv run pytest tests/test_display.py tests/test_app.py`
- [x] 2.2 Full suite passes: `uv run pytest`

#### Manual

- [x] 2.3 On the laptop with the stub, `uv run python -m spotify_to_led.main` writes the idle frame to `var/frames/latest.png`, and with Spotify configured and music started it switches to the placeholder within 5 s, then back to idle when playback stops

### Phase 3: Pi rollout and cold-boot verification

#### Automated

- [ ] 3.1 Deploy exits 0 and `/healthz` answers: `scripts/deploy.sh`

#### Manual

- [ ] 3.2 Idle is legible from desk distance and distinct from the placeholder on the real board
- [ ] 3.3 Cold boot with the board on: after a power cut and restore, the board shows idle with no human action
- [ ] 3.4 Cold boot with the board off: switching the board on minutes after boot brings idle up with no restart, and the log shows the retries
- [ ] 3.5 Playing and stopping music on the Pi switches placeholder and idle within 5 s
- [ ] 3.6 The 7-day run start and the legibility result are recorded in `verification.md` and the roadmap
