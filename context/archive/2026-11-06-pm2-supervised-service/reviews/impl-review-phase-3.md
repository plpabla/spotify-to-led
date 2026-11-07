<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: pm2-Supervised Service and Persistent Logs

- **Plan**: context/changes/pm2-supervised-service/plan.md
- **Scope**: Phase 3 of 4
- **Reviewed phases**: 3
- **Date**: 2026-11-07
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical, 3 warnings, 2 observations

Repo scope of Phase 3: `fb98111` (scripts/deploy.sh, src/spotify_to_led/board/idotmatrix_transport.py, tests/test_idotmatrix_transport.py, plan.md) and `bca3bdb` (plan.md). The phase itself is on-device; its manual criteria 3.1-3.8 are all ticked, each against output pasted from the Pi. `uv run pytest`: 37 passed.

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | WARNING |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | WARNING |

## Findings

### F1 -- Foundation docs state facts the rollout disproved, and omit host steps it added

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: context/foundation/infrastructure.md:23, :80, :123, :148
- **Detail**: Four superseded notes say journald on the Pi is persistent by default ("`/var/log/journal` exists", "No journald change is made"). The Pi ships `/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf` (`Storage=volatile`); the first reboot left one boot in `journalctl --list-boots`. The rollout then added three host-side steps that live nowhere in the repo: `/etc/systemd/journald.conf.d/50-persistent.conf` (`Storage=persistent`, `SystemMaxUse=100M`), nvm loaded from `~/.profile` (without it `deploy.sh` exits 127 on `pm2: command not found`), and `BOARD_TRANSPORT`/`IDOT_MAC` added to the env file. Rebuilding the Pi from the docs would silently regress all three.
- **Fix**: In Phase 4, correct the four notes (journal was volatile; the persistent drop-in is now a host step) and add the nvm `~/.profile` line and the drop-in to Getting Started.
- **Decision**: FIXED (infrastructure.md :23, :80, :123, :148 corrected; drop-in and nvm prerequisite added to Getting Started steps 4-5)

### F2 -- deploy.sh can report success from a process pm2 did not start

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM -- real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: scripts/deploy.sh:25-30
- **Detail**: The health poll accepts any answer on 127.0.0.1:8000. Observed on 2026-11-06: the first deploy printed `{"status":"ok"}` while pm2's instance was crash-looping on `[Errno 98] address already in use` (exit code 3, 21:29-21:31), because a manually started copy held the port.
- **Fix A ⭐ Recommended**: Record it as a known limitation in verification.md and infrastructure.md
  - Strength: Zero code; the trigger (a hand-started copy on 8000) is an operator mistake that is now documented.
  - Tradeoff: A broken deploy can still exit 0 in that situation.
  - Confidence: HIGH -- only one way to hit it, and it was diagnosed.
  - Blind spot: None significant.
- **Fix B**: After the health poll, also require `pm2 jlist` to report `spotify-to-led` `online` with the same pid across two reads ~3 s apart
  - Strength: Catches the false positive and a crash loop that answers between restarts.
  - Tradeoff: Unplanned code in Phase 4, parsing pm2 JSON in bash (needs python3 or jq on the Pi), and a fixed wait.
  - Confidence: MED -- logic is simple, but untested against pm2's output format.
  - Blind spot: Behaviour when `startOrReload` swaps the pid mid-check.
- **Decision**: FIXED via Fix A (known limitation in infrastructure.md Getting Started step 4 and README Deploy; verification.md to repeat it in Phase 4)

### F3 -- Unplanned code changes in Phase 3 are not reflected in the plan's contract

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: src/spotify_to_led/board/idotmatrix_transport.py:33, :88-99; scripts/deploy.sh:7-18; plan.md:45, :147
- **Detail**: `fb98111` bounded the BLE disconnect to 5 s (with a test) and replaced the login shell with `. "$HOME/.profile"`. Both were approved mid-phase and are justified by Pi evidence (SIGKILL at 22:01:16; exit 1 from `~/.bash_logout`). The plan still says "runs its remote part in a login shell" (:45, :147) and lists no board-code change; "What We're NOT Doing" excludes in-app BLE reconnect, which the timeout is not, but a reader of the plan would not know the file changed.
- **Fix**: Record both as adaptations in verification.md and a dated line in change.md Notes (plan phase blocks stay read-only).
- **Decision**: FIXED (dated adaptations and host changes in change.md Notes; verification.md to repeat them in Phase 4)

### F4 -- Shutdown bound covers close() but not cancelling an in-flight first connect

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/main.py:58-62
- **Detail**: The lifespan `finally` does `task.cancel()` then `await task` with no bound. Test B covered a stop during the library's reconnect cycle after a successful connect. A stop within ~30 s of a start with the board off (the initial `connect()` still pending) was not exercised; if bleak delays cancellation, the SIGKILL could return there.
- **Fix**: One check on the Pi: board off, `pm2 restart spotify-to-led`, then restart again within 15 s and confirm `via signal [SIGINT]`. Bound `await task` only if it SIGKILLs.
- **Decision**: FIXED (verified on the Pi, no code change: board off, process started 20:12:42, stopped 20:12:48 with the first connect pending, exited via SIGINT in under 1 s)

### F5 -- Phase 3 evidence exists only in this conversation

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: context/changes/pm2-supervised-service/plan.md (Progress 3.1-3.8)
- **Detail**: Every 3.x tick rests on output pasted into the session (groups line with 103, logrotate conf, `23 waiting restart`, SIGINT lines at 19:36:38 and 19:37:18, 2 boots after the drop-in). 3.7 was briefly ticked on a general "works well" and reverted until the redeploy was confirmed. Clearing context before Phase 4 loses the record. Not observed and not required: whether the board lit after the reboots, and `cloudflared` restarting 1-3 times during boot.
- **Fix**: Write Phase 4's verification.md in this context, before any clear.
- **Decision**: FIXED (Phase 4 verification.md written in the same session as the rollout)
