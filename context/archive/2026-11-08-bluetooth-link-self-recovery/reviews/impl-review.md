<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Bluetooth link self-recovery

- **Plan**: context/changes/bluetooth-link-self-recovery/plan.md
- **Scope**: Full plan (completed phases only; Phase 2 has row 2.6 pending by owner decision)
- **Reviewed phases**: 1
- **Date**: 2026-11-09
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

The code diff is 6 files (`git diff --name-only bcc3a25..HEAD`), all named in the plan. They were read in full in the reviewing session, so the drift and safety passes were run inline rather than by sub-agents.

| Planned change | Verdict |
| --- | --- |
| `idotmatrix_transport.py`: `set_auto_reconnect(False)`, docstring and `_on_disconnected` comment rewritten, discard kept | MATCH; it also reworded three comments that only made sense with the library's own loop |
| `display.py`: DOWN check inside the `try`, INFO line, `_connect` reused, `attempted` kept, docstring and constants comment | MATCH |
| `main.py` and `CLAUDE.md` wording | MATCH |
| Tests at both layers | MATCH; adds the `FakeBoard.drop`, `DroppingSleep` and `WorkingLink` helpers |

Automated criteria were re-run on `ab2a9d4`:
- `uv run pytest`: 180 passed, 1 skipped
- `grep -rn "set_auto_reconnect(True)" src/`: no match

Manual criterion 1.5 was confirmed by the owner.

Phase 2 was not reviewed because Progress row 2.6 is pending. For whoever reviews it later: rows 2.4 and 2.5 are titled "out of range" but were met by board switch-offs. The deviation is recorded in `verification.md`, "(c) and (d): not run as out-of-range tests".

## Findings

### F1 — A late disconnect for a replaced link could leave the new link unwatched

- **Severity**: 💡 OBSERVATION
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/board/idotmatrix_transport.py:89-95, src/spotify_to_led/display.py:109-113
- **Detail**: This raises the Phase 1 subagent's uncertainty and is traced in the library source. It has not been observed on hardware.
  1. Suppose bleak delivers a disconnect for an old `BleakTransport` after a new one is connected. `ConnectionManager._on_disconnected` sees `is_connected()` true (from the new transport) and runs `_handle_disconnect`. That sets `_connected = False` and stops the heartbeat (`connection_manager.py:586-607`).
  2. Our listener sets DOWN but keeps the live transport. The display loop then calls `connect`, and the manager takes its "already connected" branch (`connection_manager.py:248-249`), which neither restarts the heartbeat nor sets `_connected`. `_mark_up` then counts a new connection and the board is redrawn.
  3. From then on, the heartbeat is off. A real drop of the new link would also be ignored, because `_on_disconnected` returns early once both `transport.is_connected` and `_connected` are false (`connection_manager.py:591-592`). `link_state` would stay UP on a dead link until a restart.

  How likely is the trigger? Low on the paths inspected:
  - The heartbeat path disconnects the old client before replacing it (`connection_manager.py:673-683`).
  - `_discard_stale_link` only drops clients that are already disconnected or never connected.

  The same sequence was reachable before this change through the library's own reconnect, so it is not a regression.
- **Fix**: Record it as a follow-up in `verification.md` with its log signature, rather than patching more private library state now. The signature is a `board link lost; reconnecting` line immediately followed by `already connected to <board address>`.
  - Strength: no code change, so no redeploy and the 7-day run keeps counting. The signature makes the case diagnosable if it ever happens.
  - Tradeoff: if it does happen, the board stays on its last frame until a restart.
  - Confidence: MED — the sequence follows from the library's source. The trigger, a late BlueZ signal for a replaced client, is speculative.
  - Blind spot: whether BlueZ can deliver `PropertiesChanged(Connected=false)` for a removed device object after a new client has connected.
- **Decision**: FIXED (follow-up and log signature recorded in verification.md, Open follow-ups)

### F2 — The "research gap" test does not depend on this change

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: tests/test_idotmatrix_transport.py:165-192
- **Detail**: `test_each_connect_after_a_drop_uses_a_fresh_link` says it covers "the research gap". It drives `transport.connect()` directly, so it passes with `set_auto_reconnect(True)` too: the fresh links come from `_discard_stale_link`, which predates the change. The deliberate-break check in Phase 1 showed that the gap is guarded by `test_a_library_reported_drop_marks_the_link_down_and_starts_no_reconnect_loop`. The plan's own wording ("the regression test for the research gap") carried the same overstatement.
- **Fix**: Reword the comment to say it pins the display-loop path (every connect after a drop scans fresh), and that the no-reconnect-loop test is the one that guards against the library's loop coming back.
- **Decision**: FIXED (test comment reworded)

### F3 — Display-task traceback now once per outage, not once per process

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/spotify_to_led/display.py:126-145
- **Detail**: `_connect` keeps `first_failure` local, so every outage now logs one traceback. Before this change that happened only on the cold boot. This is intended and documented ("traceback only on the first of each outage", module docstring), and the overnight run shows the cost is negligible: one traceback per outage against about 700 lines over 8.8 h. Noted only so the change in log shape is not mistaken for a regression when the 7-day logs are read.
- **Fix**: None needed; keep as documented.
- **Decision**: SKIPPED (intended, documented)
