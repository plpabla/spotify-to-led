# Service Package and Swappable Board Transport Implementation Plan

## Overview

Replace the uv `print()` placeholder with a real `spotify_to_led` service package that can push one 32x32 frame to the LED board through a single transport interface, backed by two implementations: an `idotmatrix-api-client` 0.3.0 adapter for the Raspberry Pi and a PNG-writing stub for the laptop development loop. This is roadmap item F-01, it satisfies FR-012 and FR-016, and it exists primarily to retire the project's `top_blocker: external` risk (an unmaintained board library on an untested interpreter) at the cheapest possible point.

## Current State Analysis

The repository is pre-implementation. `src/spotify_to_led/main.py` is the uv placeholder (`print("Hello from 20-led-board!")`), `pyproject.toml` declares only `fastapi` and `uvicorn` against `requires-python >=3.14`, and there is no ASGI app object, no routes, no transport code, no tests and no `[tool.*]` section anywhere.

What already exists and must not be rebuilt:

- `.env` carries `IDOT_MAC`, so the board's Bluetooth address has been obtained. `infrastructure.md` Getting Started steps 1 to 3 are complete on the Pi: uv is installed, Python 3.14 is pinned, the repo is cloned, and the service user is in the `bluetooth` group with a `bleak` scan already proven.
- `var/` is already in `.gitignore`, so a stub frame directory under `var/` needs no ignore-file change.
- `uv.lock` and `.python-version` are tracked deliberately, so the Pi deploys with `uv sync --frozen`.

Constraints discovered during research and verification:

- `idotmatrix` 0.0.9, the roadmap's original default, is a dead end: no release since 2025-04-13, no `requires-python`, a junk `asyncio` pin, and both upstream repositories archived. The maintained fork is published separately as `idotmatrix-api-client`.
- FR-013 (Bluetooth self-recovery) belongs to roadmap item S-03, and FR-015 (status page) belongs to S-07. Neither is in scope here, which bounds how much of the app shell this change builds.
- `tech-stack.md` records `typed: true` as a self-check answer, so new code is annotated, but no type checker is configured and none is to be added.

## Desired End State

`uv run python -m spotify_to_led.main` starts a FastAPI application whose lifespan builds a board transport from configuration, connects it, pushes one test-pattern frame, and keeps serving a liveness endpoint whether or not the board was reachable. With `BOARD_TRANSPORT=stub` on the development machine the frame lands as `var/frames/latest.png`. With `BOARD_TRANSPORT=idotmatrix` on the Pi the same frame appears on the physical 32x32 board.

Verification that this is true: the automated suite passes on the development machine with the stub, and a human has seen the test pattern on the real board driven from the Pi.

### Key Discoveries:

- `idotmatrix-api-client` 0.3.0 declares `requires_python <3.15,>=3.12` with dependencies `bleak>=0.22.3`, `cryptography>=46.0.7`, `pillow>=12.2.0`, `watchdog>=6.0.0`, and is licensed GPL-3.0-or-later (PyPI JSON API, queried 2026-11-04). Verified at the `0.3.0` git tag, not repo head.
- `ScreenSize.SIZE_32x32` is a first-class enum member (`idotmatrix/screensize.py`), so board size is configuration rather than a detection round trip. Passing `screen_size` to the client skips the `detect_screen_size` call that `connect()` otherwise makes.
- `IDotMatrixClient.__init__` takes `install_signal_handlers: bool = True` and installs its own SIGINT/SIGTERM handlers by default (`idotmatrix/client.py`). This must be `False` under uvicorn and systemd.
- `client.image.show_image_pixeldata(pixel_colors, png=True)` takes one colour per pixel, row by row (`idotmatrix/modules/image.py:208`), which is exactly what `list(Image.getdata())` produces for an RGB image.
- `ConnectionListener(on_connected=..., on_disconnected=...)` holds two optional async callbacks (`idotmatrix/connection_manager.py:20`), which is how link state reaches the application for FR-018 later.
- Auto-reconnect has bounded exponential backoff built in: `RECONNECT_INITIAL_DELAY_SECONDS = 5`, `RECONNECT_MAX_DELAY_SECONDS = 60` (`connection_manager.py`). The reconnect loop is started only from `_on_disconnected` (`connection_manager.py:609-613`), so it covers drops after a successful connection and does **not** retry a failed initial connect.
- `context/foundation/infrastructure.md:89` and the risk register describe 0.0.9's problems as the live situation. Those passages become stale with this change and need superseding notes, matching the existing `> Superseded, 2026-11-03:` convention in `tech-stack.md`.

## What We're NOT Doing

- **No reconnect policy of our own.** Auto-reconnect and the heartbeat are switched on because the library provides them for two lines, but bounding recovery, defining the display while disconnected, and verifying FR-013 properly remain S-03. Retrying a *failed initial* connect is specifically not implemented here, which matters for the cold-boot case `infrastructure.md` raises; that is F-02 and S-02 work.
- **No status page.** The liveness endpoint returns a fixed payload and deliberately does not report board link state, believed-playing item, or re-authorization. FR-015 is S-07.
- **No Spotify anything.** No OAuth, no API client, no scopes, no credential storage. That is S-01.
- **No cover rendering.** The frame pushed here is a synthetic test pattern, not artwork. FR-007 rendering, the staleness marker (FR-018) and the frame cache (FR-017) are later slices.
- **No systemd unit, no deploy script, no CI workflow.** F-02 and F-03.
- **No linter, formatter or type checker.** `CLAUDE.md` forbids adding one unasked. The single `[tool.*]` section introduced is pytest configuration, which the agreed testing decision requires.
- **No evaluation of the `brmk` or `pracucci/idotmatrix-overclocked` forks.** Research open question 4 is closed as not worth further work: the transport interface makes a later swap a one-file change, which was the entire point of building it.
- **No mocking of the fork's BLE client.** Automated tests cover the stub and the pure pixel conversion only, per the agreed testing scope.

## Implementation Approach

Four source concerns, deliberately separated so that only the last needs hardware:

1. A frame contract plus a `Protocol` describing the transport (push a 32x32 RGB image, report link state), with a pure `frame_to_pixel_colors` helper that both the real adapter and the tests use.
2. A stub transport that writes the frame to a PNG, which is the FR-016 development loop and the thing the test suite exercises.
3. A settings object and a service skeleton whose lifespan owns the transport and one supervised background task, so a board failure degrades to a logged link-down rather than killing the process that is also the observability surface.
4. The real adapter over `IDotMatrixClient`, which is thin because the fork already does connection management.

Phase ordering is driven by risk, not by layering: the dependency and interpreter check comes first and alone, because discovering an aarch64 wheel problem or a 3.14 incompatibility after writing code against the API wastes that code. Phases 2 and 3 are fully verifiable on the development machine. Hardware is needed only in phase 5.

## Critical Implementation Details

- **Signal handlers.** `IDotMatrixClient` installs SIGINT and SIGTERM handlers unless constructed with `install_signal_handlers=False`. Under uvicorn (which owns signals) and later under systemd (`Restart=always`), leaving the default on means two parties racing to handle shutdown. Pass `False` and let the lifespan's own teardown call `disconnect()`.
- **Initial connect is not retried by the library.** The reconnect loop starts from the disconnect callback only. A board that is off at startup leaves the link down until the process restarts. The lifespan must therefore catch the connect failure rather than assume the library will recover it, and must not pretend this is FR-013.
- **Atomic stub writes.** The development loop is "keep an image viewer open on `latest.png`". Write to a temporary file in the same directory and `os.replace` it, or a viewer polling the file will occasionally read a half-written PNG and report corruption that is not there.
- **Pixel order and channel order are the two things a smoke test catches.** The test pattern should use four distinct quadrant colours precisely so that a transposed, mirrored or BGR-ordered push is visible at a glance on the real board rather than being mistaken for a working display.

---

## Phase 1: Pin the dependency and prove the interpreter

### Overview

Add the dependencies and confirm they resolve and import on Python 3.14 on both the development machine and the Pi's aarch64 before any application code depends on the API. Record the pin, the licence acceptance and the testing choice in `context/foundation/`.

### Changes Required:

#### 1. Project dependencies

**File**: `pyproject.toml`, `uv.lock`

**Intent**: Add the board library, Pillow and the settings library as runtime dependencies, and pytest with its asyncio plugin as a development group. Pillow is declared directly even though the board library pulls it in transitively, because the frame contract and the stub depend on it directly.

**Contract**: Runtime dependencies gain `idotmatrix-api-client==0.3.0` (pinned exactly, not a floor, because the fork is pre-1.0 and its API is explicitly incompatible with the package it replaces), `pillow>=12.2.0` and `pydantic-settings>=2.0`. A `dev` dependency group gains `pytest` and `pytest-asyncio`. Added with `uv add` and `uv add --dev` so `uv.lock` stays in sync. Note that `watchdog` will appear in the lock as a transitive dependency of the board library.

#### 2. Test runner configuration

**File**: `pyproject.toml`

**Intent**: Configure pytest so async tests run without per-test decorators and so the suite has a fixed root.

**Contract**: A single new `[tool.pytest.ini_options]` section setting `asyncio_mode = "auto"` and `testpaths = ["tests"]`. This is the project's first `[tool.*]` section and is intentionally limited to the test runner; no linter, formatter or type checker configuration is added.

#### 3. Record the testing and licence decisions

**File**: `context/foundation/tech-stack.md`

**Intent**: `CLAUDE.md` requires the first test-framework choice to be recorded in `context/foundation/` rather than only in `pyproject.toml`. The GPL-3.0 dependency also needs a recorded acceptance.

**Contract**: A dated note appended in the existing `> Superseded, <date>:` style, stating that pytest with pytest-asyncio is the test runner as of this change, and that a GPL-3.0-or-later runtime dependency is accepted because the PRD's non-goals rule out distribution. Frontmatter keys are not changed.

#### 4. Supersede the 0.0.9 passages

**File**: `context/foundation/infrastructure.md`

**Intent**: The Unknown Unknowns entry and the risk register row describe `idotmatrix` 0.0.9 as the thing being used. Both are now wrong in their premise, and leaving them makes the foundation docs contradict the lock file.

**Contract**: A dated superseding note on the "`idotmatrix` 0.0.9 declares `asyncio`" bullet and an updated Mitigation cell on the "`idotmatrix` untested on Python 3.14" risk row, both naming `idotmatrix-api-client` 0.3.0, its declared `>=3.12,<3.15` support, and the fact that the 32x32-on-BlueZ combination is still the least-verified one. Do not delete the original text; the project convention is to supersede in place.

### Success Criteria:

#### Automated Verification:

- `uv sync` resolves with no conflict and the lock pins the expected version: `uv run python -c "import importlib.metadata as m; print(m.version('idotmatrix-api-client'))"` prints `0.3.0`
- The library imports on Python 3.14: `uv run python -c "from idotmatrix import IDotMatrixClient; from idotmatrix.screensize import ScreenSize; print(ScreenSize.SIZE_32x32.value)"` prints `(32, 32)`
- The test runner is configured and collects cleanly: `uv run pytest --collect-only`

#### Manual Verification:

- After `git pull && uv sync` on the Pi, the same import command succeeds on aarch64, meaning wheels resolve for `bleak`, `dbus-fast`, `cryptography` and `pillow` (this closes research open question 1)
- `tech-stack.md` and `infrastructure.md` read correctly against the lock file: the pin, the GPL-3.0 acceptance and the pytest choice are all recorded

**Implementation Note**: Do not start phase 2 until the Pi import has succeeded. The entire reason this phase stands alone is that a failure here changes which library the rest of the plan targets.

---

## Phase 2: Frame contract, transport interface, PNG stub

### Overview

Define what a frame is and what a transport must do, then implement the stub that satisfies it. No hardware, no FastAPI, no configuration. This is the layer FR-016 guarantees parity for.

### Changes Required:

#### 1. The transport contract

**File**: `src/spotify_to_led/board/transport.py` (new, with `src/spotify_to_led/board/__init__.py`)

**Intent**: Declare the single interface every board implementation satisfies, the link-state value the application reports, the frame invariant, and the pure conversion from an image to the wire representation the real board wants. Keeping the conversion here rather than in the adapter is what lets it be tested without any BLE involvement.

**Contract**: Four public names.

```python
class LinkState(StrEnum):
    UP = "up"
    DOWN = "down"

class BoardTransport(Protocol):
    @property
    def link_state(self) -> LinkState: ...
    async def connect(self) -> None: ...
    async def push_frame(self, frame: Image.Image) -> None: ...
    async def close(self) -> None: ...

def validate_frame(frame: Image.Image, size: int) -> None: ...        # raises ValueError
def frame_to_pixel_colors(frame: Image.Image) -> list[tuple[int, int, int]]: ...
```

`push_frame` accepts a Pillow `Image` in mode `RGB` whose size is exactly `(size, size)`; `validate_frame` raises `ValueError` otherwise, and every implementation calls it first so the invariant lives in one place. `frame_to_pixel_colors` returns row-major RGB triples, the order `show_image_pixeldata` documents. `connect` may raise; `close` must be safe to call after a failed `connect`.

#### 2. The test pattern

**File**: `src/spotify_to_led/frames.py` (new)

**Intent**: Provide the one frame this change pushes. Four distinct quadrant colours make a transposed, mirrored or wrong-channel-order push obvious on the real board instead of looking like success.

**Contract**: `def test_pattern(size: int) -> Image.Image` returning an `RGB` image of `(size, size)` with four solid quadrants in distinguishable colours. Not artwork rendering and not a stand-in for FR-007.

#### 3. The stub transport

**File**: `src/spotify_to_led/board/stub.py` (new)

**Intent**: Write the pushed frame to disk so the development loop has full fidelity on everything except transport, which is exactly the parity carve-out FR-016 states.

**Contract**: `PngBoardTransport(output_dir: Path, size: int)` satisfying `BoardTransport`. `connect` creates the directory and sets link state up; `push_frame` validates then writes `output_dir / "latest.png"` by writing a temporary file in the same directory and `os.replace`-ing it over the target, so a viewer never reads a partial file; `close` sets link state down. Default output directory is under `var/`, which is already gitignored.

#### 4. First tests

**File**: `tests/test_stub_transport.py`, `tests/test_frames.py` (new, with `tests/__init__.py` if needed)

**Intent**: Exercise the contract the rest of the plan builds on.

**Contract**: Cover a successful push producing a readable 32x32 RGB PNG at the expected path, rejection of a wrong-size and a wrong-mode image with `ValueError`, link state transitions across `connect`/`close`, `close` after a failed `connect` not raising, and `frame_to_pixel_colors` returning 1024 triples whose four corners match the test pattern's quadrant colours.

### Success Criteria:

#### Automated Verification:

- Stub and frame tests pass: `uv run pytest tests/test_stub_transport.py tests/test_frames.py`
- A push writes a readable 32x32 RGB PNG at the configured path (asserted in the suite)
- A wrong-size or wrong-mode image is rejected with `ValueError` (asserted in the suite)
- `frame_to_pixel_colors` returns 1024 row-major triples whose corners match the pattern (asserted in the suite)

#### Manual Verification:

- Open the written `latest.png` and confirm the quadrant colours and their orientation are what you expect to see on the physical board, since this image is the reference the phase 5 smoke test is judged against

---

## Phase 3: Settings and the service skeleton

### Overview

Replace the placeholder with the real entrypoint: a settings object, a factory that picks a transport, and a FastAPI application whose lifespan owns the transport and one supervised background task. Still no hardware.

### Changes Required:

#### 1. Settings

**File**: `src/spotify_to_led/config.py` (new)

**Intent**: One typed place where environment configuration enters the process, serving both the systemd `EnvironmentFile` on the Pi and the existing `.env` on the development machine.

**Contract**: A `pydantic-settings` `Settings` model with no environment prefix and case-insensitive matching, so the existing `IDOT_MAC` key in `.env` works unchanged. Fields: `board_transport: Literal["stub", "idotmatrix"] = "stub"` (stub is the safe default; the Pi's unit sets the other), `idot_mac: str | None = None`, `board_size: int = 32`, `board_heartbeat_seconds: float | None = 30.0`, `stub_frame_dir: Path = Path("var/frames")`, `host: str = "127.0.0.1"`, `port: int = 8000`, `log_level: str = "INFO"`. A validator rejects `board_transport="idotmatrix"` with `idot_mac` unset, so a misconfiguration fails at startup rather than presenting as a Bluetooth fault. The heartbeat default of 30 seconds is chosen to match FR-018's 30-second grace window, so the two cadences do not drift apart later.

#### 2. Transport factory

**File**: `src/spotify_to_led/board/factory.py` (new)

**Intent**: Map settings to a transport instance, keeping the choice in one function so the application never imports a concrete transport.

**Contract**: `def create_transport(settings: Settings) -> BoardTransport`. In this phase it returns only the stub and raises on `"idotmatrix"`; phase 4 fills that branch in. The real adapter's module is imported inside its branch, not at module top level, so a development machine without a working Bluetooth stack never imports `bleak`.

#### 3. The service skeleton

**File**: `src/spotify_to_led/main.py` (rewrite)

**Intent**: Give the project a real ASGI application and a supervised background task, so that F-02's unit file has a stable target and so the pattern that keeps a board failure from killing the observability surface is established once, here.

**Contract**: Module-level `app` created with a lifespan that builds settings, builds the transport, and starts exactly one background task; on shutdown it cancels the task and calls `close()`. The task connects and pushes `test_pattern(settings.board_size)` once. Any exception from connect or push is caught and logged at error level inside the task: it never propagates out of the lifespan and never prevents the application from serving. One route, `GET /healthz`, returning a fixed `{"status": "ok"}` payload and deliberately not reporting link state, which belongs to FR-015 in S-07. `if __name__ == "__main__":` calls `uvicorn.run` with the configured host and port, because `infrastructure.md`'s unit file specifies `ExecStart=<repo>/.venv/bin/python -m spotify_to_led.main` and that invocation must keep working. Logging is configured once at startup from `log_level`, writing to stdout so journald captures it.

#### 4. Skeleton tests

**File**: `tests/test_config.py`, `tests/test_app.py` (new)

**Intent**: Pin the config invariants and the degradation behaviour, which is the part of this phase most likely to regress silently.

**Contract**: Config tests cover defaults, that `IDOT_MAC` from the environment populates `idot_mac`, and that `board_transport="idotmatrix"` without a MAC raises at construction. Application tests use FastAPI's test client with `board_transport="stub"` to assert that running the lifespan pushes exactly one frame and that `/healthz` returns 200; and, with a transport whose `connect` raises, that `/healthz` still returns 200 and the error was logged.

### Success Criteria:

#### Automated Verification:

- Config tests pass, including that `idotmatrix` without `IDOT_MAC` fails at construction: `uv run pytest tests/test_config.py`
- Application tests pass: the stub lifespan pushes exactly one frame and `/healthz` returns 200: `uv run pytest tests/test_app.py`
- A transport whose `connect` raises leaves `/healthz` answering 200 (asserted in the suite)
- The whole suite is green: `uv run pytest`

#### Manual Verification:

- `uv run python -m spotify_to_led.main` with `BOARD_TRANSPORT=stub` starts, writes `var/frames/latest.png`, answers `/healthz`, and shuts down cleanly on Ctrl+C with no traceback

---

## Phase 4: The real Bluetooth transport

### Overview

Implement the adapter over `IDotMatrixClient`. It is thin by design: the fork owns connection management, chunking and acks, so this phase is mostly about passing the right three construction arguments and translating link events.

### Changes Required:

#### 1. The iDotMatrix adapter

**File**: `src/spotify_to_led/board/idotmatrix_transport.py` (new)

**Intent**: Satisfy `BoardTransport` by delegating to the fork, exposing its connection events as link state, and turning on the recovery the library already provides.

**Contract**: `IDotMatrixBoardTransport(mac_address: str, size: int, heartbeat_seconds: float | None)` satisfying `BoardTransport`. Construction detail that the rest of the plan depends on:

```python
self._client = IDotMatrixClient(
    screen_size=ScreenSize((size, size)),   # SIZE_32x32 for size=32
    mac_address=mac_address,
    install_signal_handlers=False,          # uvicorn and systemd own signals
)
self._client.add_connection_listener(
    ConnectionListener(on_connected=..., on_disconnected=...)   # both async
)
self._client.set_auto_reconnect(True)
self._client.set_heartbeat(heartbeat_seconds)
```

`connect` awaits `client.connect()` and lets failures propagate to the lifespan, which logs them; link state is set by the listener callbacks rather than inferred. `push_frame` validates, then awaits `client.image.show_image_pixeldata(frame_to_pixel_colors(frame))`, leaving `png=True` at its default. `close` awaits `client.disconnect()` and tolerates never having connected. Passing `screen_size` explicitly is deliberate: it skips the `detect_screen_size` round trip that `connect()` makes when the size is unknown.

Record in a short module docstring that auto-reconnect covers drops after a successful connection, with the library's own 5-to-60-second backoff, and does not retry a failed initial connect, so the cold-boot case stays an open item for S-02 and S-03.

#### 2. Wire the factory branch

**File**: `src/spotify_to_led/board/factory.py`

**Intent**: Return the real adapter for `board_transport="idotmatrix"`.

**Contract**: The previously raising branch now constructs `IDotMatrixBoardTransport` from `idot_mac`, `board_size` and `board_heartbeat_seconds`, importing the module inside the branch. Constructing the adapter must not touch Bluetooth, so the factory stays callable in a test.

### Success Criteria:

#### Automated Verification:

- The factory returns the real adapter for `BOARD_TRANSPORT=idotmatrix` without any Bluetooth activity (asserted in the suite)
- The whole suite is still green: `uv run pytest`

#### Manual Verification:

- Read the adapter and confirm the three fork settings the chosen test strategy does not cover are present and correct: `install_signal_handlers=False`, `set_auto_reconnect(True)`, and `set_heartbeat` receiving the configured value

---

## Phase 5: Pi smoke test and recorded outcome

### Overview

Drive the real 32x32 board from the Pi, which is the only verification that means anything for the transport layer, then write what was observed back into the documents that currently record it as unknown.

### Changes Required:

#### 1. Record the verified outcome

**File**: `context/foundation/infrastructure.md`

**Intent**: The risk register row for the board library currently describes a mitigation to perform. After this phase it describes something observed.

**Contract**: The Mitigation cell on that row gains the observed result: which version was proven, on which interpreter and architecture, and whether a 32x32 board accepted a frame over BlueZ.

#### 2. Close the roadmap question

**File**: `context/foundation/roadmap.md`

**Intent**: Open question 6 asked which implementation gets pinned. It now has an answer backed by hardware.

**Contract**: Question 6 is marked resolved, naming `idotmatrix-api-client` 0.3.0 and the evidence. F-01's `Unknowns` entry for the same question is updated. Do not touch any other item's status in this edit.

#### 3. Close the research open questions

**File**: `context/changes/app-skeleton-and-board-transport/research.md`

**Intent**: The doc is marked `status: partial` with four open questions. Three are now answerable and the fourth was closed by decision.

**Contract**: Open questions 1 and 2 record the observed answers, question 3 records the owner's decision, question 4 records that it was closed as not worth evaluating given the interface. Frontmatter `status` becomes `complete` and `last_updated` is bumped.

### Success Criteria:

#### Automated Verification:

- On the Pi, `uv sync --frozen` succeeds against the committed lock and the suite passes there: `uv run pytest`

#### Manual Verification:

- The physical 32x32 board displays the test pattern pushed from the Pi, with quadrant colours and orientation matching the `latest.png` reference from phase 2 (this closes research open question 2, the least-verified combination in the fork)
- With the service running, switch the board off and on and confirm from `journalctl` that the disconnect was seen, the reconnect loop ran, and the link returned. This is an observation of inherited behaviour that informs S-03, not an acceptance of FR-013
- `infrastructure.md`, `roadmap.md` and `research.md` record the observed outcome, and no document still describes the library choice as unknown

---

## Testing Strategy

### Unit Tests:

- Frame invariant: correct size and mode accepted; wrong size, wrong mode rejected with `ValueError`
- `frame_to_pixel_colors`: 1024 entries, row-major order, corner values matching the test pattern quadrants
- Stub transport: PNG written at the expected path and readable as 32x32 RGB; repeated pushes overwrite rather than accumulate; `close` after a failed `connect` does not raise
- Settings: defaults, `IDOT_MAC` picked up from the environment, and the `idotmatrix`-without-MAC rejection

### Integration Tests:

- Lifespan with the stub transport: exactly one frame pushed, `/healthz` returns 200, shutdown closes the transport
- Lifespan with a transport whose `connect` raises: the application still serves `/healthz` and the failure is logged rather than propagated
- Factory: returns the real adapter for `idotmatrix` without initiating any Bluetooth activity

The fork's BLE client is deliberately not mocked. Its call sequence is verified by the phase 5 hardware run, not by assertions about a third-party surface whose stability was inferred rather than guaranteed.

### Manual Testing Steps:

1. With `BOARD_TRANSPORT=stub`, run `uv run python -m spotify_to_led.main`, open `var/frames/latest.png`, and confirm the pattern and orientation.
2. On the Pi, `git pull && uv sync --frozen`, then run with `BOARD_TRANSPORT=idotmatrix` and `IDOT_MAC` set, and compare the board against that reference image.
3. With the service running on the Pi, power-cycle the board and read `journalctl` for the disconnect, the backoff and the recovery.
4. Stop the service with the board switched off and start it again: confirm the application still serves `/healthz` with the link down and logs the failure, which is the degradation this change promises.

## Performance Considerations

Nothing here is on the NFR-01 five-second path yet, since only one frame is pushed at startup. For later slices the relevant numbers were observed during research: uploads are chunked at 4096 bytes with per-chunk acks and a 20 ms inter-packet write interval, and a 32x32 RGB frame is roughly 3 KB raw and smaller as PNG, so one frame is a single chunk. PNG encoding of 1024 pixels happens inline on the event loop in the `show_image_pixeldata` path and is negligible at this size, but it is worth remembering when FR-007 starts pushing a frame every few seconds.

## Migration Notes

No database and no persisted state, so nothing to unwind. Two things change for anything already running: `main.py`'s `main()` placeholder is gone, replaced by `python -m spotify_to_led.main` starting a server, and `uv.lock` gains a new dependency tree, so the Pi needs `uv sync --frozen` and not just a `git pull`. No new secret is introduced: `IDOT_MAC` is already present in `.env` on the development machine and belongs in the systemd `EnvironmentFile` on the Pi, outside the working tree, when F-02 lands.

## References

- Research: `context/changes/app-skeleton-and-board-transport/research.md`
- Change record: `context/changes/app-skeleton-and-board-transport/change.md`
- Roadmap item F-01: `context/foundation/roadmap.md:123-152`; open question 6: `context/foundation/roadmap.md:411-415`
- FR-012 and FR-016: `context/foundation/prd.md:201,219`; FR-013 (S-03, out of scope): `context/foundation/prd.md:205`; FR-018 (link state consumer): `context/foundation/prd.md:193`
- Board library concerns superseded by this change: `context/foundation/infrastructure.md:89`
- Pi baseline and BLE prerequisites: `context/foundation/infrastructure.md:126-129`
- Upstream API verified at tag `0.3.0`: `idotmatrix/client.py`, `idotmatrix/connection_manager.py:20,609-613`, `idotmatrix/modules/image.py:208`, `idotmatrix/screensize.py`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append the short commit sha when a step lands, per `references/progress-format.md`. Do not rename step titles.

### Phase 1: Pin the dependency and prove the interpreter

#### Automated

- [x] 1.1 `uv sync` resolves and the lock pins `idotmatrix-api-client` 0.3.0 -- fdcd51f
- [x] 1.2 The library imports on Python 3.14 and `ScreenSize.SIZE_32x32` is `(32, 32)` -- fdcd51f
- [x] 1.3 The test runner is configured and collects cleanly -- fdcd51f

#### Manual

- [x] 1.4 The same import succeeds on the Pi's aarch64 after `uv sync` (closes research open question 1) -- fdcd51f
- [x] 1.5 `tech-stack.md` and `infrastructure.md` record the pin, the GPL-3.0 acceptance and the pytest choice -- fdcd51f

### Phase 2: Frame contract, transport interface, PNG stub

#### Automated

- [x] 2.1 Stub and frame tests pass -- 072979e
- [x] 2.2 A push writes a readable 32x32 RGB PNG at the configured path -- 072979e
- [x] 2.3 A wrong-size or wrong-mode image is rejected with `ValueError` -- 072979e
- [x] 2.4 `frame_to_pixel_colors` returns 1024 row-major triples whose corners match the pattern -- 072979e

#### Manual

- [x] 2.5 The written `latest.png` shows the expected quadrant colours and orientation, and becomes the phase 5 reference

### Phase 3: Settings and the service skeleton

#### Automated

- [x] 3.1 Config tests pass, including `idotmatrix` without `IDOT_MAC` failing at construction -- 0a1deb1
- [x] 3.2 Application tests pass: the stub lifespan pushes exactly one frame and `/healthz` returns 200 -- 0a1deb1
- [x] 3.3 A transport whose `connect` raises leaves `/healthz` answering 200 -- 0a1deb1
- [x] 3.4 The whole suite is green -- 0a1deb1

#### Manual

- [x] 3.5 `python -m spotify_to_led.main` with the stub starts, writes the PNG, answers `/healthz`, and shuts down with no traceback -- 0a1deb1

### Phase 4: The real Bluetooth transport

#### Automated

- [x] 4.1 The factory returns the real adapter for `BOARD_TRANSPORT=idotmatrix` with no Bluetooth activity -- bfe9a3b
- [x] 4.2 The whole suite is still green -- bfe9a3b

#### Manual

- [x] 4.3 The adapter sets `install_signal_handlers=False`, `set_auto_reconnect(True)` and the configured heartbeat -- bfe9a3b

### Phase 5: Pi smoke test and recorded outcome

#### Automated

- [x] 5.1 On the Pi, `uv sync --frozen` succeeds and the suite passes there

#### Manual

- [x] 5.2 The physical 32x32 board shows the test pattern pushed from the Pi, matching the phase 2 reference (closes research open question 2)
- [x] 5.3 A board power-cycle shows disconnect, backoff and recovery in `journalctl` (informs S-03, does not accept FR-013)
- [x] 5.4 `infrastructure.md`, `roadmap.md` and `research.md` record the observed outcome
