---
date: 2026-11-03
researcher: pawel (via Claude)
git_commit: bd7d4690935c8ab63cad608b7a768f679794b877
branch: cez/f82e738d
repository: spotify-to-led
topic: "Which idotmatrix implementation should F-01 pin, and what does it mean for the board transport interface?"
tags: [research, board-transport, idotmatrix, bleak, python-3.14]
status: partial
last_updated: 2026-11-03
last_updated_by: pawel (via Claude)
---

# Research: Which idotmatrix implementation should F-01 pin?

## Research Question

Roadmap open question 6 (`context/foundation/roadmap.md:411`): PyPI `idotmatrix` 0.0.9 or one of the three named forks, for F-01 (`app-skeleton-and-board-transport`, FR-012, FR-016). The question was taken from `change.md:12`; none was supplied in the message.

## Summary

- The roadmap premise is out of date. A maintained fork, `markusressel/idotmatrix-api-client`, is published on PyPI as `idotmatrix-api-client` 0.3.0 (uploaded 2026-10-23). It declares `requires-python >=3.12,<3.15` and its CI matrix includes Python 3.14. The roadmap and `infrastructure.md:89` describe only 0.0.9 as the PyPI option and say "nothing has tested this against 3.14"; that is true of 0.0.9 but not of this fork.
- Recommendation (evidence-based, owner decides): pin `idotmatrix-api-client` 0.3.0 rather than `idotmatrix` 0.0.9. The upstream `derkalle4` library and client repos are both archived, and the fork's README says the original is no longer maintained.
- The fork already offers what FR-012/FR-013 need at library level: connect by MAC address, auto-reconnect, optional heartbeat, connection listeners, and a pixel-data push (`show_image_pixeldata`). F-01's transport interface can stay thin and leave reconnect policy to the fork, while still isolating it.
- Not verified here: no install or import was run on Python 3.14 or on the Pi, and no frame was sent to hardware. The "3.14 works" claim rests on the fork's declared metadata and CI matrix only.

## Detailed Findings

### PyPI `idotmatrix` 0.0.9 (the roadmap default)

- Latest of 9 releases (0.0.1 to 0.0.9); 0.0.9 uploaded 2025-04-13. `requires_python` is empty; dependencies are `asyncio`, `bleak`, `cryptography`, `pillow` (PyPI JSON API, queried 2026-11-03). Matches `infrastructure.md:89`.
- Homepage points at `derkalle4/python3-idotmatrix-library`, last pushed 2025-06-05 and flagged `archived: true`; the sibling `python3-idotmatrix-client` repo is also archived (GitHub API).

### `markusressel/idotmatrix-api-client` (fork, PyPI `idotmatrix-api-client`)

- PyPI: 5 releases, 0.2.0 to 0.3.0, all uploaded 2026-10-23; latest 0.3.0, `requires_python <3.15,>=3.12`. Repo last pushed 2026-10-26, not archived, 2 open issues, GPL-3.0 (GitHub API). `pyproject.toml` deps: `bleak>=0.22.3`, `pillow>=12.2.0`, `cryptography>=46.0.7`, `watchdog>=6.0.0` (no `asyncio` junk pin).
- CI workflow `pythonpackage.yml:15` runs a matrix of 3.12, 3.13, 3.14. I did not inspect whether the workflow is currently green.
- Supports 16x16, 32x32 and 64x64 (README). Caveat from `docs/DEVICE-PROTOCOL.md`: the verified hardware is a 64x64 on Linux/BlueZ; a 32x32 was reported only from macOS Catalina via an issue, and one third-party 32x32 ack format is flagged "claim, probably a transcription error, to be checked on a 32x32". The owner's board is 32x32 (`shape-notes.md:41`), so the 32x32 path on BlueZ is the least-verified combination.
- API relevant to F-01 (`idotmatrix/client.py`, `connection_manager.py`, `modules/image.py`, as fetched at repo head ce23d6c):
  - `IDotMatrixClient(screen_size=..., mac_address=...)`, `connect()` (by address if given, otherwise first discovered), `set_auto_reconnect`, `set_heartbeat`, `add_connection_listener`, `disconnect`.
  - `client.image.show_image_pixeldata(pixel_colors, png=True)` takes one colour per pixel, row by row; `show_image_file(path, resize_mode=...)` also exists. This fits a transport interface whose input is a 32x32 RGB frame.
  - Uploads are chunked at 4096 bytes with per-chunk acks; the same data re-sent may be short-circuited by the device (`DEVICE-PROTOCOL.md` lines 51 to 67). Relevant to FR-008/FR-017 later, not to F-01.
- The fork's `AGENTS.md` names `IDotMatrixClient`, `BleakTransport`, `transport_factory`, `set_ble_device` as public API used by its Home Assistant integration, which suggests those names are intended to be stable. Inference, not a guarantee.
- The fork's API is stated to be incompatible with the original, so switching between 0.0.9 and the fork later is a real rewrite of the adapter, not a version bump. This is exactly what the transport interface contains.

### Other forks/candidates seen

- `8none1/idotmatrix` (last push 2024-10-20), `Kezii/idotmatrix` (2024-01-22), `pracucci/idotmatrix-overclocked` (2026-02-27, not inspected), `tukies/iDotMatrix-HomeAssistant` and `EarMaster/home-assistant-idotmatrix` (HA integrations, not libraries). `brmk` fork from the roadmap was not located by this search and was not inspected. Source: `gh search repos idotmatrix`, a single query.

### Repository state

- `src/spotify_to_led/main.py` is the placeholder `print`; `pyproject.toml` depends only on `fastapi` and `uvicorn`, `requires-python >=3.14` (`pyproject.toml:6-10`). No transport code, tests or tooling exist, consistent with `CLAUDE.md`.
- `pillow` is needed anyway for FR-007/FR-016 stub PNG output; the fork requires `pillow>=12.2.0`, so check it resolves alongside the project's own pin when added.

## Code References

- `context/foundation/roadmap.md:123-152` - F-01 definition, unknown on which implementation to pin.
- `context/foundation/roadmap.md:411-415` - open question 6.
- `context/foundation/infrastructure.md:89,109` - 0.0.9 maintenance concerns and the "swap behind interface" mitigation.
- `context/foundation/infrastructure.md:126-129` - Pi baseline and `bleak` scan steps.
- `pyproject.toml:6-10` - current dependencies.

## Architecture Insights

- Keep the transport interface at "push one 32x32 RGB frame, report link state". The fork already handles reconnect and heartbeat, but FR-018 needs the service to know whether the link is alive, so expose link state through the interface (via the fork's connection listeners) rather than hiding it.
- The stub writes a PNG; `show_image_pixeldata` accepting a flat pixel list means one frame representation serves both implementations.
- GPL-3.0 on the fork and the original: acceptable for a private, single-tenant service (PRD non-goal: no distribution), but worth recording in `context/foundation/`.

## Historical Context (from prior changes)

- `context/changes/bootstrap-verification/verification.md` and `spotify-redirect-uri-policy/research.md` were listed by the roadmap as inputs; not read for this question (not applicable to transport choice).
- Roadmap claim "0.0.9 is the PyPI option; forks have divergent APIs" (`roadmap.md:139-141`): partial. Divergent APIs is supported; "PyPI means 0.0.9" is contradicted, since the fork is on PyPI as a different distribution name.

## Related Research

None for board transport under `context/changes/` or `context/archive/`.

## Open Questions

1. Does `uv add idotmatrix-api-client` resolve and import on Python 3.14 on the dev laptop, and on aarch64 Trixie (wheels for `cryptography`, `pillow`, `bleak` dependencies such as `dbus-fast`)? Not tested. First step of the F-01 smoke test.
2. Does a 32x32 board work end to end on BlueZ with this fork (only 64x64 is verified there)? Needs the real board; roadmap open question 4 also needs it.
3. Is the owner fine with GPL-3.0 dependency and with moving off the roadmap's "start with 0.0.9" default? Owner decision.
4. `brmk` fork and `pracucci/idotmatrix-overclocked` were not evaluated; unlikely to beat a maintained, 3.14-CI fork but not ruled out.
