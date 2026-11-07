---
change_id: pm2-supervised-service
title: pm2-supervised service and persistent logs
status: implemented
created: 2026-11-06
updated: 2026-11-07
archived_at: null
---

## Notes

<!-- Free-form notes for this change: links, ad-hoc context, decisions that don't belong in research/frame/plan. -->

- 2026-11-07, adaptations approved during the Phase 3 rollout (`fb98111`), not in the plan's contract:
  - `scripts/deploy.sh` sources `~/.profile` instead of running a login shell (plan.md:45, :147 say "login shell"). On the Pi, Debian's `~/.bash_logout` ran `clear_console` without a tty under `set -e` and turned a successful deploy into exit 1.
  - `IDotMatrixBoardTransport.close()` gives up on `disconnect()` after 5 s and logs a warning, with `tests/test_idotmatrix_transport.py`. With the board unreachable the disconnect waited behind the library's reconnect attempt past pm2's 10 s `kill_timeout`, and the app was SIGKILLed (2026-11-06 22:01:16). Not an in-app reconnect, so "What We're NOT Doing" still holds.
- 2026-11-07, host changes made during the rollout, not in the repo: nvm loaded from `~/.profile` (pm2 on PATH for `deploy.sh`), and `/etc/systemd/journald.conf.d/50-persistent.conf` (`Storage=persistent`, `SystemMaxUse=100M`), because the plan's "journald is already persistent" premise was wrong. Both are in `infrastructure.md` Getting Started.
