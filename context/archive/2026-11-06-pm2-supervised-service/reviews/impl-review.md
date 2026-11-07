<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: pm2-Supervised Service and Persistent Logs

- **Plan**: context/changes/pm2-supervised-service/plan.md
- **Scope**: Full plan
- **Reviewed phases**: 1, 2, 3, 4
- **Date**: 2026-11-07
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical, 3 warnings, 3 observations

Scope: `main..HEAD`, 18 files. Every planned file changed; the only unplanned code (`board/idotmatrix_transport.py`, `tests/test_idotmatrix_transport.py`) is the approved Phase 3 adaptation recorded in `change.md` Notes and already triaged in `impl-review-phase-3.md`. All automated criteria rerun and pass (1.1-1.3, 2.1-2.5 with 37 tests, 4.1-4.2); every manual row is ticked with evidence in `verification.md`.

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS |

## Findings

### F1 -- roadmap.md still says journald is persistent by default

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: context/foundation/roadmap.md:118-120
- **Detail**: Phase 1's snapshot note says journald "turned out to be persistent by default config anyway". `verification.md` Phase 3.5 shows it was volatile (`40-rpi-volatile-storage.conf`), and the Phase 3 triage corrected the same claim in four places in `infrastructure.md` but not here, leaving an unmarked contradiction across foundation docs.
- **Fix**: Add a `Corrected, 2026-11-07` clause pointing at the drop-in and `verification.md`, matching infrastructure.md.
- **Decision**: FIXED (dated correction added to roadmap.md snapshot note)

### F2 -- Lifespan tests are not isolated from SPOTIFY_TO_LED_ENV_FILE

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: tests/test_app.py:69-75
- **Detail**: The lifespan now calls `load_settings()`, but `use_transport` does not clear the new variable. A developer who exports it (pointing at a missing file) gets `FileNotFoundError` in every lifespan test; pointing at a real file leaks its `LOG_LEVEL`/`BOARD_SIZE` into the tests. `tests/test_config.py` cleans it; `test_app.py` does not.
- **Fix**: `monkeypatch.delenv(ENV_FILE_VARIABLE, raising=False)` in `use_transport`.
- **Decision**: FIXED (use_transport clears ENV_FILE_VARIABLE; with it exported to a missing file test_app.py went from 4 failed to 7 passed)

### F3 -- deploy.sh passes PI_REPO and PI_HOST through a second shell parse

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: scripts/deploy.sh:17
- **Detail**: ssh joins its remote arguments with spaces and the Pi's login shell re-parses them, so `"$PI_REPO"` is only quoted locally: a value with spaces splits, and `;` or `$(...)` would run on the Pi. A `PI_HOST` starting with `-` is read as an ssh option. Both are operator-set, so the risk is low, but the script is the one promotion path.
- **Fix**: `ssh -- "$PI_HOST" bash -s -- "$(printf %q "$PI_REPO")"`.
- **Decision**: FIXED (ssh -- and printf %q on PI_REPO; spaces and ; survive a second parse as one argument)

### F4 -- deploy.sh header and PATH failure could say more

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: scripts/deploy.sh:2, :18-19
- **Detail**: The header says "Promote the checked-out branch tip", but the script pulls whatever branch the Pi's checkout tracks and never compares with the local HEAD. When `~/.profile` does not put `uv`/`pm2` on PATH, the failure is a bare `command not found` (exit 127), as seen on 2026-11-06.
- **Fix**: Reword the header to "the branch the Pi's checkout tracks", and add `command -v uv pm2 >/dev/null || { echo "uv or pm2 not on PATH; see infrastructure.md Getting Started step 4" >&2; exit 1; }` after `set -euo pipefail`.
- **Decision**: FIXED (header reworded; uv/pm2 PATH check with a pointer to Getting Started step 4, checked against an empty ~/.profile)

### F5 -- Disconnect-timeout test relies on the default log level

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: tests/test_idotmatrix_transport.py:46-47
- **Detail**: The warning assertion passes only because the root level allows WARNING; `test_app.py` sets the capture level explicitly with `caplog.at_level(...)`.
- **Fix**: Wrap the `close()` call in `with caplog.at_level(logging.WARNING):`.
- **Decision**: FIXED (caplog.at_level(logging.WARNING) around close())

### F6 -- Leftover unmarked systemd wording and a stale link in foundation docs

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: context/foundation/roadmap.md:63; context/foundation/infrastructure.md:64, :149
- **Detail**: `roadmap.md:63` still says "a systemd unit with nothing on the board". `infrastructure.md:64` keeps "(Recommended)" on the systemd shortlist heading, offset only by notes elsewhere. `infrastructure.md:149` links `context/changes/edge-surface-and-spotify-app-registration/verification.md`, which was archived to `context/archive/2026-11-05-.../` (pre-existing on main).
- **Fix**: Reword :63 to "a supervised service", mark the :64 heading "(superseded, see Recommendation)", and repoint the :149 link to the archive path.
- **Decision**: FIXED (roadmap.md:63 reworded, infrastructure.md:64 heading marked superseded, :149 link repointed to the archive)
