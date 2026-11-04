# Service Package and Swappable Board Transport -- Plan Brief

> Full plan: `context/changes/app-skeleton-and-board-transport/plan.md`
> Research: `context/changes/app-skeleton-and-board-transport/research.md`

## What & Why

Replace the uv `print()` placeholder with a real `spotify_to_led` package that can push one 32x32 frame to the LED board through a single transport interface, with a real Bluetooth implementation and a PNG-writing stub behind it. This is roadmap item F-01 (FR-012, FR-016). Nothing downstream can be planned against a placeholder, and this is the cheapest point at which to discover that the board library does not work on the interpreter the stack mandates.

## Starting Point

`src/spotify_to_led/main.py` prints a hello message; `pyproject.toml` declares only `fastapi` and `uvicorn`; there are no routes, no transport code, no tests and no `[tool.*]` section. The Pi is already bootstrapped (uv, Python 3.14, repo cloned, `bluetooth` group, `bleak` scan proven) and `.env` already holds `IDOT_MAC`, so the board's address is known.

## Desired End State

`python -m spotify_to_led.main` starts a FastAPI app whose lifespan builds a transport from configuration, connects, pushes one test-pattern frame, and keeps serving a liveness endpoint whether or not the board was reachable. On the development machine that frame lands as `var/frames/latest.png`; on the Pi it appears on the physical 32x32 board.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Board library | `idotmatrix-api-client==0.3.0` (markusressel fork) | Declares `>=3.12,<3.15` with 3.14 in CI and is still maintained, while `idotmatrix` 0.0.9's upstream repos are both archived. | Research |
| Licence | GPL-3.0-or-later accepted | The PRD's non-goals rule out distribution, so a single-tenant private service is unaffected. | Plan |
| Skeleton scope | FastAPI app plus lifespan-supervised task, no real routes | F-02's unit file needs a stable `ExecStart` target, and the "board failure must not kill the observability surface" pattern is cheaper to establish once than to retrofit. | Plan |
| Frame type | Pillow `Image`, 32x32 RGB | Pillow is already a hard dependency of the board library and FR-007 rendering will produce an Image anyway, so no conversion layer exists purely for the interface. | Plan |
| Configuration | `pydantic-settings`, `BOARD_TRANSPORT` selects the transport | One mechanism serves both the systemd `EnvironmentFile` and the existing `.env`, and S-01 inherits a typed home for the Spotify config. | Plan |
| Startup failure | Log, stay up, link reported down | `infrastructure.md` warns that this process is both driver and observability surface, so a dead board must not take the server with it. | Plan |
| Recovery | The fork's auto-reconnect and 30s heartbeat switched on | Two lines for behaviour the library already implements with 5-to-60-second backoff. | Plan |
| Testing | pytest with pytest-asyncio, stub and pure code only | Covers exactly the layer FR-016 guarantees parity for and keeps CI runnable with no board attached. | Plan |
| Verification bar | Stub green locally plus one real frame on the Pi | Transport parity is explicitly not guaranteed off the Pi, so only hardware closes the risk. | Plan |

## Scope

**In scope:** the package and entrypoint; a `BoardTransport` protocol with frame validation and pixel conversion; a PNG stub; a `pydantic-settings` config and transport factory; the `IDotMatrixClient` adapter with link-state reporting, auto-reconnect and heartbeat; the first test suite; a single-frame smoke test on the real board; and superseding notes in the foundation docs that still describe 0.0.9 as the choice.

**Out of scope:** any Spotify code (S-01); cover rendering, the staleness marker and the frame cache (S-04 onward); the status page (S-07); a reconnect policy of our own or retry of a failed *initial* connect (S-03, F-02); the systemd unit, deploy script and CI workflow (F-02, F-03); any linter, formatter or type checker; and further evaluation of the remaining `idotmatrix` forks.

## Architecture / Approach

```
main.py (FastAPI app)
  lifespan -> config.Settings -> board.factory.create_transport()
           -> one supervised task: connect, push test_pattern(), log failures
                                    |
                          board.transport.BoardTransport (Protocol)
                           /                              \
         board.stub.PngBoardTransport        board.idotmatrix_transport
           var/frames/latest.png              IDotMatrixClient 0.3.0 -> BLE
```

The frame invariant and the pure `frame_to_pixel_colors` conversion live in `transport.py`, so the one piece of real-adapter logic worth testing is testable without Bluetooth. The concrete Bluetooth module is imported inside its factory branch, so a machine without a working BLE stack never imports `bleak`.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Pin the dependency and prove the interpreter | Locked dependencies, pytest configured, imports proven on 3.14 locally and on the Pi's aarch64, foundation docs corrected | A missing aarch64 wheel for `bleak`/`dbus-fast`/`cryptography`/`pillow` changes which library the rest of the plan targets, which is why this phase stands alone |
| 2. Frame contract, transport interface, PNG stub | The `BoardTransport` protocol, frame validation, pixel conversion, the quadrant test pattern, the stub, first tests | Getting the frame contract wrong here propagates into every later slice |
| 3. Settings and the service skeleton | `Settings`, the factory, the app with a lifespan-supervised task, `/healthz`, working `python -m` entrypoint | The degradation path (board dead, server alive) is easy to get subtly wrong and only a test catches it |
| 4. The real Bluetooth transport | The `IDotMatrixClient` adapter with link state, auto-reconnect and heartbeat | Three construction arguments carry all the risk and none are covered by the chosen test strategy |
| 5. Pi smoke test and recorded outcome | A frame on the real board, and the observed result written back into the foundation docs | 32x32 over BlueZ is the fork's least-verified combination; a transposed or wrong-channel push is the likely surprise |

**Prerequisites:** the Pi bootstrapped per `infrastructure.md` Getting Started steps 1 to 3 (confirmed done); `IDOT_MAC` known (in `.env`); the physical board available for phase 5.
**Estimated effort:** roughly 2 to 3 sessions. Phases 1 to 4 are one focused session on the development machine; phase 5 needs the Pi and the board in hand.

## Open Risks & Assumptions

- **32x32 over BlueZ is unproven upstream.** The fork's protocol notes verify a 64x64 on Linux and flag one third-party 32x32 ack format as a probable transcription error. Phase 5 is the first real test of this combination, and it is the most likely place to need a fork-side workaround.
- **A failed initial connect is not retried.** The library's reconnect loop starts only from a disconnect callback, so a board that is off at startup stays down until restart. In-process retry for the cold-boot case is left to F-02 and S-02, which is exactly where `infrastructure.md` says adapter readiness belongs.
- **Enabling auto-reconnect moves part of FR-013 forward.** S-03 changes from "build recovery" to "verify and bound recovery we inherited". Its acceptance criteria should be written with that in mind rather than assuming nothing exists.
- **The fork is pre-1.0 and its API stability is inferred.** Its `AGENTS.md` names the classes used here as the public surface for its Home Assistant integration, which is suggestive but not a guarantee; the exact pin and the thin adapter are the mitigation.
- **One `[tool.*]` section is added** to `pyproject.toml` for pytest, which is the first such section in a project whose guidelines say not to add tooling unasked. It is limited to the test runner.

## Success Criteria (Summary)

- The full suite passes on the development machine with no board attached, and the pushed frame is visible as `var/frames/latest.png`.
- The physical 32x32 board shows the test pattern, pushed from the Pi, with orientation and colours matching the stub's reference image.
- Starting the service with the board switched off leaves `/healthz` answering and the failure logged, instead of a dead process.
