---
date: 2026-11-19T11:26:16+01:00
researcher: Claude (Opus 5.5)
git_commit: 20bc43e63e01afd1fddcab09251edbb18d1a7828
branch: feat/usage-progress-on-display
repository: spotify-to-led
topic: "Automatic board reset when the iDotMatrix board keeps rejecting uploads"
tags: [research, display, board-transport, idotmatrix, status]
status: complete
last_updated: 2026-11-19
last_updated_by: Claude (Opus 5.5)
---

# Research: Automatic board reset when the board keeps rejecting uploads

**Date**: 2026-11-19T11:26:16+01:00
**Researcher**: Claude (Opus 5.5)
**Git Commit**: 20bc43e (local); the Pi runs `main` at f8c249e, the merge of this branch
**Branch**: feat/usage-progress-on-display
**Repository**: spotify-to-led

## Research Question

From `change.md`: what does the codebase and the pinned idotmatrix library need, and constrain, for the service to reset a board that is stuck rejecting every image upload (`UploadError ... notification 0500020000`) while the BLE link stays up — where the decision, the reset call, the observability and the tests would go, and which facts the plan still has to settle.

## Summary

- **The rejection is distinguishable only by message text.** The library raises the same `UploadError` class for "the device rejected the upload" (`.venv/Lib/site-packages/idotmatrix/connection_manager.py:424`) and for "did not acknowledge the upload within 5 seconds" (`connection_manager.py:420`); the class docstring covers both (`connection_manager.py:52-53`). No status attribute is carried. Classification must match the message (safe while the library is pinned to exactly 0.3.0), and belongs in the transport adapter, not in `display.py`.
- **The reset command exists on the client.** `IDotMatrixClient.reset()` delegates to `common.reset()` (`.venv/Lib/site-packages/idotmatrix/client.py:258-262`), which sends `RESET = Command(3, 0x80)` (`protocol.py:15`) via `_send_packets(..., response=True)` with `wait_for_ack` left False (`modules/common.py:162-176`): one write-with-response, no wait for a device notification. In the 2026-11-19 experiment (change.md), the link was still up about 2 s after the reset and the next push succeeded on the same connection; the service's first connect after it timed out, the second succeeded.
- **The display loop has no notion of "rejected repeatedly".** `_push` swallows every exception and returns a bool (`src/spotify_to_led/display.py:434-450`); the push backoff (1 s doubling to 60 s, `display.py:110-111`) restarts for each new presentation (`display.py:357`), so a rejection counter would have to live outside that per-presentation state to span the item changes that happened during the incident.
- **The reset must not rely on a link drop to redraw.** Redraw-on-reconnect fires when `transport.connections` grows (`display.py:335`, `transport.py:27-29`). In the one observed reset the link did not drop, so the loop has to make the retry due itself after a reset.
- **Surfacing**: `DisplayStatus` (`display.py:164-174`) → `_board_json` (`src/spotify_to_led/main.py:634-657`) → `renderBoard` (`src/spotify_to_led/static/index.html:135-154`) is the existing path; `push_failed` is already shown to the owner (`index.html:151`).
- **Field evidence for a threshold**: in the Pi's retained logs (2026-11-06 21:29 to 2026-11-19), 1,143 push-failure warnings were rejections, all on 2026-11-18/19; other push failures were 7 `BleakGATTProtocolError` and 1 pre-retry ERROR line; 0 were ack timeouts. No transient (self-clearing) rejection is on record.
- **No prior decision conflicts.** Nothing in `context/archive/**` or `context/foundation/**` mentions upload rejection, device storage or the RESET command. The artwork change's review explicitly left "a failed push with the link genuinely up" unobserved on hardware; this incident is that case.

## Detailed Findings

### The library's error and reset paths (idotmatrix-api-client 0.3.0, local `.venv`)

- `UploadError` ("The device rejected an upload or did not acknowledge it.") — `connection_manager.py:52-53`. Raised on ack timeout at `:420` and on an `UploadStatus.ERROR` notification at `:424`. `CommandRejectedError` (`:56`) is a separate class used for command acks (`:382`), not uploads.
- `UploadStatus.ERROR` for image uploads is notification status 0 or 2 (`util/upload_ack.py:40`); the comment "f.e. not enough space" (`upload_ack.py:15`) is an example, and the 2026-11-19 experiment showed clearing storage did not fix this instance.
- `UPLOAD_ACK_TIMEOUT_SECONDS = 5` (`connection_manager.py:36`).
- `send_packets` and `send_bytes` connect first if the manager is not connected (`connection_manager.py:453`, `:302`) and hold `_io_lock`; a `reset()` therefore serialises with pushes and may itself trigger a connect.
- `common.reset()` → `_send_packets(packets=[[RESET]], response=True)`; with `response=True` and the default `sleep_after`, no post-send sleep (`modules/__init__.py`, `_send_packets`). It does not wait for a device ack. Whether the write can hang if the board reboots mid-write is **not observed**; bleak's write timeout is not inspected here. A bound (`asyncio.timeout`) in the adapter is the safe assumption, as the adapter already does for `close` (`src/spotify_to_led/board/idotmatrix_transport.py:36-38`).
- Storage-clearing calls exist (`materials.clear`, `gif.clear_storage`, `system.delete_device_data`) but none fixed the incident (change.md experiment).

### Board transport boundary

- `BoardTransport` protocol: `link_state`, `connections`, `connect`, `push_frame`, `close` (`src/spotify_to_led/board/transport.py:20-42`); no reset. Structural, implementations do not inherit.
- Real adapter `IDotMatrixBoardTransport` (`board/idotmatrix_transport.py`): `push_frame` → `client.image.show_image_pixeldata` (`:127-129`); bounded `close` (`DISCONNECT_TIMEOUT_SECONDS`, `:38`). It is the natural place to (a) translate the library's rejection into a project-level exception so `display.py` keeps not importing idotmatrix, and (b) implement `reset()` via `self._client.reset()` with a timeout.
- Stub `PngBoardTransport` (`board/stub.py:19-52`) never fails a push; needs a no-op `reset()` to satisfy the protocol.
- Pin: `idotmatrix-api-client==0.3.0` exactly (`pyproject.toml:10`; decision in `context/archive/2026-11-03-app-skeleton-and-board-transport/plan.md:84`). Matching on the message text adds a second pin-dependent detail next to `_discard_stale_link`'s private field access (`idotmatrix_transport.py`, docstring of `_discard_stale_link`).

### Display loop

- Per-presentation push retry: on a new `wanted` (presentation, connection count) or frame, `retry_delay` resets to 1 s and the push is due (`display.py:341-358`); otherwise a failed push is retried when `retry_in` runs out (`:359-361`), doubling to 60 s (`:370-372`). The change.md note "1, 2, 4 ... 32 s" lists the steps seen in one log excerpt; the cap is 60 s (`display.py:111`).
- `_push` logs `type(error).__name__` and returns False (`display.py:434-450`); the caller never sees the exception. A reset trigger needs `_push` to return or classify the error.
- The catch-all sets `status.push_failed = True` and keeps the loop alive (`display.py:373-377`); a reset raising must not escape either.
- Redraw after reconnect: `attempted` survives a reconnect and the grown `connections` makes the same sample redraw (`display.py:308-312`, `:335`, `:343-345`). If a reset leaves the link up (as observed once), `connections` does not grow, so only the existing failed-push retry would redraw — after up to 60 s unless the loop makes it due immediately.
- `clock` and `sleep` are injected (`display.py:270-271`), so threshold-by-duration and cooldown are testable without real time.

### Status and status page

- `DisplayStatus{presentation, push_failed}` (`display.py:164-174`), created in the lifespan and passed to `run_display` (`main.py:224`, `:240`, `:246`).
- `_board_json` names each field explicitly (`main.py:634-657`); `tests/test_app.py:366` asserts the exact key set `{"link","task_running","presentation","push_failed"}`, so a new key (e.g. `last_reset_at`) changes that test.
- `renderBoard` shows "That push failed; it is being retried." when `push_failed` and a presentation exist (`index.html:151`); nothing else about the board beyond link and last presentation (`index.html:135-154`).

### Tests and fakes

- `FakeBoard` (`tests/test_display.py:137-184`): first N pushes raise `OSError("the write was not acknowledged")` (`:178-181`); `drop()`/`reconnect()` helpers. No fake raises a library error or has `reset()`; a rejection fake (raising the new project-level exception) and a reset counter are needed.
- `CountingTransport` / `FailingTransport` / `FailingCloseTransport` (`tests/test_app.py:63-126`) need `reset()` only if the protocol gains it and type-checking is enforced structurally at runtime (it is not; `Protocol` is unchecked at runtime).
- `tests/test_idotmatrix_transport.py` swaps `transport._client` for duck-typed fakes (`:44`, `:199`) or pokes the connection manager (`:84-98`); `reset()` and rejection translation are testable the same way with a fake client exposing `reset()` and `image.show_image_pixeldata`.

### Field evidence (Pi, `~/.pm2/logs/spotify-to-led-out*.log`, 2026-11-06 21:29 to 2026-11-19)

- 1,143 "pushing the frame failed" warnings with "rejected the upload", from 2026-11-18 07:08 to the reset on 2026-11-19 (~11:19).
- Non-rejection push failures in the same files: `BleakGATTProtocolError` ×7 (2026-11-10 ×1, 11-13 ×5, 11-18 ×1) and one 2026-11-08 ERROR line from the pre-retry code. `UploadError` ack timeouts ("did not acknowledge"): 0.
- So within this inspected window every rejection was part of the one persistent episode; there is no observed rate of transient rejections to tune against.

## Code References

- `src/spotify_to_led/display.py:110-111` — push retry 1 s → 60 s
- `src/spotify_to_led/display.py:164-174` — `DisplayStatus`
- `src/spotify_to_led/display.py:335-372` — wanted/attempted, retry scheduling, `status.push_failed`
- `src/spotify_to_led/display.py:373-377` — catch-all keeps the loop alive
- `src/spotify_to_led/display.py:434-450` — `_push` swallows the error
- `src/spotify_to_led/board/transport.py:20-42` — `BoardTransport`
- `src/spotify_to_led/board/idotmatrix_transport.py:36-38,127-129` — close timeout, push
- `src/spotify_to_led/board/stub.py:19-52` — stub transport
- `src/spotify_to_led/main.py:634-657` — `/status` board block
- `src/spotify_to_led/static/index.html:135-154` — board rendering
- `tests/test_display.py:137-184` — `FakeBoard`
- `tests/test_app.py:366` — exact board key-set assertion
- `.venv/Lib/site-packages/idotmatrix/connection_manager.py:52-56,420,424,453` — error classes, raises, auto-connect
- `.venv/Lib/site-packages/idotmatrix/client.py:258-262` — `client.reset()`
- `.venv/Lib/site-packages/idotmatrix/modules/common.py:162-176` — `common.reset()`
- `.venv/Lib/site-packages/idotmatrix/util/upload_ack.py:15,40` — ERROR status meaning
- `scripts/clear_board.py` — uncommitted manual diagnostic used in the experiment

## Architecture Insights

- Display decides *when*, transport decides *how*: the existing split (connect retry in `display._connect`, link mechanics in the adapter) extends naturally to "display counts rejections and calls `transport.reset()`; adapter classifies the library error and sends RESET with a timeout".
- Library-specific knowledge stays in `board/idotmatrix_transport.py`; `display.py` and the fakes should see a project-level exception (e.g. in `board/transport.py`), keeping `FakeBoard` free of idotmatrix imports.
- Hard rules unaffected: a reset is a board-side command, not a Spotify write; no animation; no auth. FR-018's marker cannot be drawn on a board that rejects frames — the PRD already accepts "no staleness guarantee while the board is unreachable" (`context/foundation/prd.md:308`), so the owner-facing signal is `/status`.

## Historical Context (from prior changes)

- `context/archive/2026-11-09-artwork-on-the-board/reviews/impl-review.md:24-41` — F1: chose retrying failed pushes (1 s → 60 s) over treating a failed push as a lost link (would cost a ~30 s scan per transient write error). Recorded tradeoff "repeated writes to a board in a bad state; the backoff bounds them" and blind spot "whether a failed push ... ever leaves the link genuinely up — not observed on hardware yet". Supported: this incident is that case; the backoff bounded writes but nothing recovered the board.
- `context/archive/2026-11-11-status-and-reauthorization-page/plan-brief.md:37`, `plan.md:112-116,138-143` — `push_failed` added to `/status` to catch "link up, pushes failing". Supported and still the right surface.
- `context/archive/2026-11-08-unattended-idle-board/plan.md:106` — S-02 originally did not retry failed pushes; superseded by the artwork change's F1.
- `context/archive/2026-11-11-ai-enhanced-covers/verification.md:44-53` — earlier `BleakGATTProtocolError (UNLIKELY_ERROR)` push failures after idle generation, worked around by connecting first; a different failure class from this one.
- `context/foundation/prd.md:36-38,222-232` — unattended operation is the primary success criterion; FR-013 covers link drops only. A reset serves FR-014's intent; no FR conflicts.
- No archived or foundation document mentions `UploadError`, upload rejection, device storage, materials or RESET.

## Related Research

- `context/archive/2026-11-08-bluetooth-link-self-recovery/research.md` — reconnect path and `_discard_stale_link`
- `context/archive/2026-11-03-app-skeleton-and-board-transport/research.md` — library fork choice and upload/ack protocol notes

## Open Questions

For `/10x-plan` (product/design choices, not missing evidence unless marked):

1. **Trigger**: N consecutive rejections, or rejections spanning T seconds, counted across presentation changes? Field data shows no transient rejections, so a small N (e.g. 3) or short T (e.g. 1–2 min) would have fixed the incident within minutes.
2. **Cooldown** after a reset that does not help (change.md suggests ≤1 per hour), and whether the counter resets on the first successful push.
3. **Immediate retry after reset**: make the push due at once (and/or re-scan) rather than waiting out the 60 s backoff, since the link may stay up.
4. **Reset timeout and failure handling**: value of the bound; whether a reset that raises or times out should mark the link DOWN to force a fresh connect. *Evidence gap*: behaviour of the write if the board reboots mid-write is unobserved.
5. **Status surface**: `last_reset_at` (and maybe a reset count) in `/status` and a line in `renderBoard`; `tests/test_app.py:366` changes.
6. **Storage clear**: drop it (experiment shows it did not help) or run it alongside the reset — no evidence it helps.
7. **`scripts/clear_board.py`**: keep as a documented manual diagnostic (renamed, e.g. `reset_board.py`) or delete.
8. **Hardware verification**: the stuck state cannot be induced on demand; plan verification likely rests on fakes plus a manual `reset()` on the Pi and watching logs.
