<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Service Package and Swappable Board Transport

- **Plan**: context/changes/app-skeleton-and-board-transport/plan.md
- **Scope**: Full plan (5 of 5 phases)
- **Reviewed phases**: 1, 2, 3, 4, 5
- **Date**: 2026-11-04
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical, 4 warnings, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | WARNING |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Evidence gathered

Every automated criterion in the plan was re-run, not taken on trust:

- 1.1 `idotmatrix-api-client` version prints `0.3.0`
- 1.2 `ScreenSize.SIZE_32x32.value` prints `(32, 32)` on CPython 3.14.0
- 1.3 / 2.x / 3.x / 4.x `uv run pytest` collects 26 and passes 26 in 0.98s
- 3.5 (manual) live run with `BOARD_TRANSPORT=stub`: startup completed, `/healthz` returned `{"status":"ok"}`, `var/frames/latest.png` was written and read back as RGB 32x32 with the four documented quadrant colours, and shutdown produced zero tracebacks
- Degradation path, live under real uvicorn rather than TestClient: with `BOARD_TRANSPORT=idotmatrix` and an unreachable MAC, startup completed and `/healthz` kept answering 200. `configure_logging`'s format is confirmed effective in a real server (library log lines appear in the `%(asctime)s %(levelname)s %(name)s` format), so the error path's logging is not a test-harness artifact.
- Third-party claims made in the code were checked against the installed 0.3.0 source: `connection_manager.disconnect` is genuinely a no-op when never connected (guarded by `is_connected()`), and `show_image_pixeldata`'s `png` parameter does default to `True`.

Phase 5's hardware criteria (5.2, 5.3) cannot be re-verified from this machine. They are accepted on the recorded attestation in `infrastructure.md`, which is specific enough to be falsifiable: version, interpreter, architecture, quadrant colours and orientation, and the observed reconnect delay.

One deliberate deviation from the plan text was checked and is an improvement rather than drift: `frame_to_pixel_colors` uses `frame.get_flattened_data()` where the plan specified `list(Image.getdata())`. `Image.getdata` is deprecated in the installed Pillow with removal scheduled for Pillow 14 and `get_flattened_data` named as its replacement, and the method is present at the declared floor `pillow>=12.2.0` (verified against a throwaway 12.2.0 install). No finding.

## Findings

### F1 - GPL-3.0 acceptance recorded with a false premise

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW - quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: context/foundation/tech-stack.md (the "Recorded, 2026-11-04" GPL note)
- **Detail**: The note accepts the GPL-3.0-or-later dependency on the grounds that "the PRD's non-goals rule out distributing this software: it is a single-tenant service that runs only on the owner's own Pi, so the licence's distribution obligations are never triggered." That premise is false. `git remote -v` shows `origin` at `git@github.com:plpabla/spotify-to-led.git` and `gh repo view` reports `"visibility":"PUBLIC"`, so the source is already being distributed. The conclusion is nevertheless correct, for a reason the note does not mention: the repository already carries a GPLv3 `LICENSE` at its root, so the combined work is already copyleft-compatible. The risk is forward-looking rather than present. A future reader who wants to relicense, vendor, or publish a build will be told to "revisit this acceptance before distribution becomes a goal" when distribution is already happening and the actual binding constraint is the existing GPLv3 LICENSE file.
- **Fix**: Rewrite the note's rationale to rest on the real ground: the project is itself GPLv3 (`LICENSE`), the repository is public, and the combined work is therefore compatible. Keep the forward-looking caveat but repoint it at relicensing rather than at first distribution.
  - Strength: Makes the recorded decision survive the next reader, which is the whole purpose of writing it into `foundation/` rather than only into `pyproject.toml`.
  - Tradeoff: None beyond editing two sentences.
  - Confidence: HIGH - repository visibility and the GPLv3 LICENSE were both read directly.
  - Blind spot: Whether the owner intends the repo to stay public. If it is about to go private the note's premise becomes true again, though the LICENSE would still be the stronger reason.
- **Decision**: FIXED

### F2 - Two settings bypass the validation the MAC was given

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW - quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/config.py:29-32
- **Detail**: `Settings` validates `idot_mac` with a model validator whose stated purpose is that otherwise "the misconfiguration would surface much later as a Bluetooth fault, which is a far harder thing to read." `board_size` and `board_heartbeat_seconds` get no equivalent, and both accept values that pass `Settings` and then kill the process from inside the library. Verified by running the app: `BOARD_SIZE=17` with the hardware transport dies with `ValueError: (17, 17) is not a valid ScreenSize`, and `BOARD_HEARTBEAT_SECONDS=0` dies with `ValueError: The heartbeat interval must be greater than 0 seconds.` Both are raised during `create_transport` at `main.py:54`, which sits outside the background task's `except Exception`, so the process fails to start and `/healthz` never answers. Failing at startup is what the plan wants for a misconfiguration; failing with a traceback out of a third-party enum is not the readable error the MAC validator established as the pattern.
- **Fix**: Add two validators to `Settings`: reject a `board_size` that is not a supported screen size, and reject a `board_heartbeat_seconds` that is not positive. Validate the size without importing the hardware module at config level (for instance against a literal tuple of supported sizes) so `config.py` stays importable on a machine with no Bluetooth stack.
  - Strength: Keeps every configuration error arriving through one mechanism with one shape of message, which is the invariant `config.py`'s own docstring claims.
  - Tradeoff: A hardcoded list of valid sizes duplicates knowledge the fork's enum already holds, and would need updating if a new board size is supported.
  - Confidence: HIGH - both failure modes were reproduced end to end.
  - Blind spot: Whether the owner would rather tighten `board_size` to a `Literal[16, 32, 64]` and drop the plain `int`, which is stricter but changes the field's public type.
- **Decision**: FIXED

### F3 - Unguarded `close()` can put a traceback in the shutdown log

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW - quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/main.py:64
- **Detail**: The lifespan's `finally` guards the task (`task.cancel()` then `suppress(asyncio.CancelledError)`) but calls `await transport.close()` bare. The real adapter's `close` awaits `client.disconnect()`, which the fork's `connection_manager.disconnect` only short-circuits when `is_connected()` is false; on a live link it awaits `self._transport.disconnect()`, which can raise from bleak or D-Bus when the board has vanished mid-shutdown. The plan's contract for `close` is "tolerates never having connected", and the implementation satisfies exactly that, so this is not drift. It is a reliability gap on the path the plan cares most about: under the F-02 systemd unit with `Restart=always`, an exception escaping the lifespan writes a shutdown traceback into the journald log that NFR-02 and the 7-day observation run depend on being readable.
- **Fix**: Wrap the `await transport.close()` call in the lifespan's `finally` in a `try`/`except Exception` that logs at error level, matching how `push_test_pattern` already treats board failures.
  - Strength: Three lines, and it makes the module's stated promise ("a board fault is something to report, never something that takes the server down with it") hold on the shutdown path as well as the startup path.
  - Tradeoff: A genuinely broken `close` becomes a log line rather than a loud failure, which is the same tradeoff already accepted for `connect` and `push`.
  - Confidence: MEDIUM - the raising path is read from the fork's source and bleak's contract, not reproduced on hardware; a clean shutdown on a healthy link does not hit it.
  - Blind spot: Not reproduced against a real board, because the failure needs the link to die inside the shutdown window.
- **Decision**: FIXED

### F4 - Roadmap still shows F-01 as in-progress

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW - quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: context/foundation/roadmap.md (F-01 table row and F-01 detail block)
- **Detail**: `change.md` reads `status: implemented`, all 25 Progress rows in the plan are `[x]` with commit shas, and phase 5's documentation work is done. The roadmap still carries F-01 as `in-progress` in both the table row and the item's `**Status:**` line. Phase 5's contract covered question 6 and F-01's `Unknowns` entry but never named the status field, so this is a gap in the plan as much as in the implementation. It matters because the roadmap is what `/10x-status` and `/10x-archive` read to decide what is live.
- **Fix**: Set F-01's status to the roadmap's completed value in both the table row and the detail block, matching the spelling the roadmap's own legend uses.
  - Strength: Restores the roadmap as a truthful index before the next change is framed against it.
  - Tradeoff: If the project's convention is that the roadmap flips only at archive time, this should wait for `/10x-archive` instead.
  - Confidence: MEDIUM - the right trigger point for the flip is a project convention this review did not find written down anywhere.
  - Blind spot: Whether `/10x-archive` already performs this edit, in which case fixing it here is duplicated work rather than a fix.
- **Decision**: DEFERRED to /10x-archive, which closes the matching roadmap item on archive (verified in the skill: flips the table cell and the `### F-01:` body line, appends a `## Done` entry)

### F5 - Roadmap edits beyond the phase 5 contract

- **Severity**: 📋 OBSERVATION
- **Impact**: 🏃 LOW - quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: context/foundation/roadmap.md (frontmatter `top_blocker`, S-03 `Unknowns`)
- **Detail**: Phase 5 scoped the roadmap edit to resolving open question 6 and updating F-01's `Unknowns`, and said "Do not touch any other item's status in this edit." Two edits go past that. `top_blocker` moved from `external` to `decisions`, and S-03 gained a substantial new `Unknowns` block recording that reconnect restores the link but nothing re-pushes a frame. Both are defensible and the second is genuinely valuable: it is a hardware observation from criterion 5.3 that would otherwise have been lost, and it correctly flags that the FR-018 non-goal carve-out covers an unreachable board but not a recovered link still presenting an unvouched frame. The `top_blocker` change is arguably just the plan's own stated purpose ("it exists primarily to retire the project's `top_blocker: external` risk") being carried out.
- **Fix**: No change. Noted so the next plan touching `foundation/` writes the wider edit into its contract rather than discovering it during implementation.
- **Decision**: ACKNOWLEDGED, no change - both edits kept; noted so the next foundation-touching plan writes the wider edit into its contract

### F6 - pytest-asyncio deprecation warning on every run

- **Severity**: 📋 OBSERVATION
- **Impact**: 🏃 LOW - quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: pyproject.toml:26-28
- **Detail**: Every `uv run pytest` invocation prints a seven-line `PytestDeprecationWarning` before the session header because `asyncio_default_fixture_loop_scope` is unset. The suite passes and the warning is cosmetic, but it is the first thing anyone sees when running the project's brand new test command, and it will be the first thing a future CI log shows.
- **Fix**: Add `asyncio_default_fixture_loop_scope = "function"` to the existing `[tool.pytest.ini_options]` section, which also pins the behaviour the warning says will change in a future release.
- **Decision**: FIXED

### F7 - CLAUDE.md states no remote is configured (plus two sibling stale claims)

- **Severity**: 📋 OBSERVATION
- **Impact**: 🏃 LOW - quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: CLAUDE.md:37
- **Detail**: "No remote is configured" is false: `origin` points at `git@github.com:plpabla/spotify-to-led.git` and the repository is public. This predates the change and is outside its diff, so it is reported rather than fixed. It is listed here because it shares a root cause with F1: the same incorrect belief about this project not being published is what makes the GPL note's rationale wrong.
- **Fix**: Corrected during triage, and two sibling stale claims in the same file were found and corrected with it: `CLAUDE.md` also said "It is pre-implementation: `src/spotify_to_led/main.py` is still the uv placeholder" and "No test suite, framework or CI workflow exists yet." Both were made false by this very change, which is a stronger reason to fix them than the remote claim. The three edits are: the remote and its public visibility stated, the entrypoint described as the FastAPI app it now is, and the testing section pointing at `uv run pytest` and the `tech-stack.md` record while still noting that no CI workflow exists.
- **Decision**: FIXED
