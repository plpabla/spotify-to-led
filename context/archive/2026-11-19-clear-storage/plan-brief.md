# Board reset on repeated upload rejections — Plan Brief

> Full plan: `context/changes/clear-storage/plan.md`
> Research: `context/changes/clear-storage/research.md`

## What & Why

On 2026-11-18 the board started rejecting every image upload (`notification 0500020000`) while the BLE link stayed up; the service retried for over 24 h and the board stayed wrong until a one-off script sent the board's RESET command. The service should recover such a board on its own, and let the owner trigger the same recovery from the status page.

## Starting Point

The display loop retries a failed push on a 1–60 s backoff per presentation and never escalates (`display.py:359-372`). The library raises the same `UploadError` for a rejection and an ack timeout, and offers `client.reset()` and `materials.clear()`. `/status` reports `push_failed`; `POST /regenerate` is the existing JSON-only owner action.

## Desired End State

Three rejected uploads in a row make the service clear the board's storage and reset it (at most once an hour), then redraw at once. The status page shows "Board reset at …" and has a "Reset board" button that does the same on demand. No SSH needed.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| What counts | Only the device's explicit rejection (`"rejected the upload"`), not ack timeouts, GATT errors or link drops | Those are different failures; only rejection was seen persisting | Research |
| Trigger | 3 rejections in a row, counted across presentation changes; any success clears the count | Track changes during the incident would defeat a per-frame count; no transient rejection ever observed | Plan |
| Reset failing | Automatic reset at most once per 60 min | Bounds reset storms while retrying hourly, unattended | Plan |
| Recovery steps | Clear stored materials (best effort), then RESET | Owner's choice; only RESET is proven, so a failed clear never blocks it | Plan |
| After reset | Retry the push immediately | The link may stay up, so redraw-on-reconnect would not fire | Research |
| Status surface | `last_reset_at` in `/status` (in memory) + a line on the page | Owner sees it happened without SSH | Plan |
| Manual tool | "Reset board" button → `POST /board/reset` (JSON only), executed by the display loop, ignores the cooldown | Owner wants a web trigger; one writer to the board | Plan |
| `scripts/clear_board.py` | Deleted | The button replaces it | Plan |
| Where code lives | Transport classifies + resets; display decides when | Keeps idotmatrix out of `display.py` and the fakes | Research |

## Scope

**In scope:**
- `BoardRejectedUpload` and `reset()` on the transport (adapter, stub)
- Rejection counter, cooldown, manual request and immediate retry in `run_display`
- `last_reset_at` in `/status`, `POST /board/reset`, page line and button
- Tests, CLAUDE.md and PRD notes, script removal

**Out of scope:**
- Resetting on any other failure kind
- Persisted reset history or counts
- Changing the staleness rule, backoff values or the library version

## Architecture / Approach

The adapter turns the library's rejection into `BoardRejectedUpload` and implements `reset()` as a bounded `materials.clear()` (errors swallowed) followed by a bounded `client.reset()`. `run_display` keeps a consecutive-rejection count and, on 3 (outside the hourly cooldown) or on the route's `reset_requested` flag, calls `transport.reset()`, stamps `DisplayStatus.last_reset_at` and makes the push due now. `/status` and the page read `last_reset_at`; the route only sets the flag.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Transport | Typed rejection + bounded clear-and-reset | Message match breaks on a library bump (pinned) |
| 2. Display loop | Automatic/manual reset with cooldown and immediate retry | Interaction with the per-presentation retry state |
| 3. Status & trigger | `/status` field, route, page button, docs, Pi check | The stuck state can't be reproduced; manual check uses the button |

**Prerequisites:** branch `feat/clear-storage`; SSH to the Pi for the manual check.
**Estimated effort:** ~2 sessions across 3 phases.

## Open Risks & Assumptions

- Whether RESET can hang or drop the link mid-write is unobserved; the 10 s bounds and the existing reconnect path cover both.
- Clearing storage erases anything stored with the phone app (accepted).
- The real rejection path is verified only by fakes until the board misbehaves again.

## Success Criteria (Summary)

- A board stuck rejecting uploads recovers within seconds, with no SSH or restart.
- No more than one automatic reset per hour.
- The owner can see the last reset and trigger one from the status page.
