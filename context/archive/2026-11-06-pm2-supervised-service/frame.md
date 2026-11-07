# Frame Brief: Supervisor choice for F-02 (systemd vs the pm2 already on the Pi)

> Framing step before /10x-plan. This document captures what is *actually*
> at issue, separated from what was initially assumed.

## Reported Observation

F-02 is scoped as "a Trixie-correct systemd unit plus persistent journald"
(`roadmap.md:72`, `roadmap.md:156`). On the same Pi, pm2 already supervises a
live production process, `cloudflared tunnel run n8n`, which also carries this
project's public hostname (`archive/2026-11-05-edge-surface-and-spotify-app-registration/verification.md:13`).

## Initial Framing (preserved)

- **User's stated cause or approach**: "I'm not sure if systemd is a correct
  approach as I already have PM2 in place and it is running Cloudflare as well."
- **User's proposed direction**: "At the end maybe we can utilize this service"
  (supervise spotify-to-led with pm2 too).
- **Pre-dispatch narrowing**: leading concern is *one pane of glass* (every
  long-running thing on the Pi visible and managed in one place). pm2 survives
  reboot via `pm2 startup` (a systemd unit). The log need is *read history after
  a reboot* (evidence for the 7-day run), not co-location with other logs.

## Dimension Map

1. **Stop semantics**: pm2's SIGINT then SIGKILL after 1.6 s truncates GATT teardown (`infrastructure.md:54`).
2. **Cold-boot race with BlueZ**: systemd can order after `bluetooth.target` and pm2 cannot, so pm2 burns its restart budget at boot (`infrastructure.md:84` pre-mortem).
3. **Two-layer supervision / operability**: systemd->pm2->app is extra cost (`infrastructure.md:54,72`). <- initial framing (inverted: a second supervisor is the cost)
4. **Persistent logs**: journald on Pi images is RAM-backed and loses NFR-02 evidence on reboot (`infrastructure.md:79,112`).
5. **BLE permissions under pm2**: the pm2 daemon runs as `pi-n8n`, and group membership is read when the daemon starts (`infrastructure.md:92`).

## Hypothesis Investigation

| Hypothesis | Evidence | Verdict |
| --- | --- | --- |
| 1. pm2 kill window breaks clean teardown | `kill_timeout` is a documented per-app setting (ecosystem `kill_timeout`, CLI `--kill-timeout`; pm2 docs, application-declaration). The 1.6 s figure is only a default. Current teardown is one `transport.close()` (`main.py:58-72`). | WEAK. One config line, not a fit problem |
| 2. pm2 loses the BlueZ boot race | The app never exits on a board failure. `push_test_pattern` swallows every exception and the server keeps serving (`main.py:36-46`, module docstring). pm2 `max_restarts` (16) counts only *unstable* restarts under `min_uptime`, which a process that stays up never triggers. The pre-mortem's restart-limit burnout needs a process that crashes on BLE failure, and this one doesn't. Getting the board back after a cold boot is an in-app reconnect concern (S-02/S-03, FR-013) under either supervisor. | WEAK as a supervisor differentiator |
| 3. A second supervisor is the real cost (initial framing) | The systemd->pm2 layer already exists and already carries production traffic for this project. The status page and Spotify re-auth are reachable only while pm2's `cloudflared` is up (`verification.md:13,27`). So FR-014/US-05 already depend on `pm2-<user>.service` surviving reboot. A separate systemd unit adds a second command set (`systemctl`/`journalctl` vs `pm2 ls`/`pm2 logs`) and a second log location for a single-owner Pi. The "two-layer" objection was scored when `research.md:134` (spotify-redirect-uri-policy) assumed "no connector runs on the Pi". | STRONG |
| 4. Logs vanish on reboot | Verified on the Pi: journald is **already persistent** (`/var/log/journal` exists, no `Storage=` override, so the default `auto` persists). pm2 writes to `~/.pm2/logs` on disk. Both survive a reboot today. Neither has been seen surviving one yet, because the journal holds a single boot (since 2026-09-20). `pm2-logrotate` is not installed, and `cloudflared-error.log` has grown unrotated to 28 MB since Jul 29. | NONE as a differentiator. The "persistent logs" half of F-02 is already met under either supervisor |
| 5. BLE permission trap under pm2 | Verified on the Pi: `pi-n8n` is in `bluetooth` (103), but the running pm2 daemon (PID 1451, started Sep 27) lacks gid 103 in `/proc/<pid>/status`, and children inherit the daemon's groups. Under pm2, BLE needs one daemon restart (`systemctl restart pm2-pi-n8n` or a reboot), which bounces `cloudflared` and n8n (47 days up). A systemd `User=pi-n8n` unit reads groups fresh at start, so it needs no bounce. | WEAK. One-time cost under pm2, not a blocker |

## Narrowing Signals

- The user's concern is operability (one pane), not a technical defect in systemd. Nothing above makes systemd *wrong*. The evidence shows its advantages over pm2 were scored against a premise that is now false.
- `pm2 startup` is in place, so pm2 itself is already a boot-surviving system service. This meets the PRD's "starts as a system service and resumes after a reboot" (`prd.md:36`) as written.
- The log need is "read history after reboot". pm2's on-disk logs satisfy it by default, while journald needs host-wide config.

## Cross-System Convention

The convention is one supervisor per host for a single-owner box. `infrastructure.md:72` itself argues against "owning a two-layer supervision story for no gain". Applied to the host as it actually is, that same argument now points the other way. The 2026-11-06 supersede note (`infrastructure.md:89`) corrected only the forwarded-header consequence of F-03's finding. The supervisor scoring (`infrastructure.md:46-56`) and the tech-stack supersede note (`tech-stack.md:31`) were never revisited.

## Reframed (or Confirmed) Problem Statement

> **The actual problem to plan around is**: F-02's supervisor decision rests on a premise F-03 disproved (no production process under pm2 on this Pi), so F-02 must first re-decide the supervisor against the host as it is. The requirements stay fixed: boot survival with no human action (FR-014), a process that cannot be permanently given up on, and logs that survive a reboot for NFR-02.

The initial framing largely held. The doubt is justified, and the documented objections to pm2 are configurable (kill timeout) or don't apply to this app's failure model (boot race). The roadmap item's *outcome* is unchanged. Only its mechanism, "systemd unit + journald", is in question, along with the title that bakes the mechanism in.

## Verification on the Pi (2026-11-06)

- pm2 v7.0.3 daemon runs as `pi-n8n`, booted by `pm2-pi-n8n.service` (enabled, `User=pi-n8n`, `After=network.target`, `Restart=on-failure`). It supervises `cloudflared` (4 restarts) and `n8n`.
- `pi-n8n` is in `bluetooth`, but the live daemon lacks the group (see row 5).
- journald is persistent by default config. Only one boot is recorded, so persistence across a reboot is configured but not yet observed.
- `pm2-logrotate` is absent, and pm2 logs grow unbounded.

## Confidence

**HIGH.** The code and docs evidence is direct, and the two facts that could only be checked on the Pi are now verified. Neither one argues for systemd over pm2 in a way that outweighs the operability concern.

## What Changes for /10x-plan

The plan should open with the supervisor decision, settled for the whole host. It should record that decision by superseding `infrastructure.md` (Recommendation, scoring table, Agent Operability "Logs" line, the journald risk row at `:112`) and `tech-stack.md:31`, and rename the change if pm2 wins.

Success criteria are unchanged: cold-boot survival, no permanent give-up, and logs readable after a reboot. Under pm2, the plan must also cover a one-time daemon restart for the `bluetooth` group (bouncing cloudflared and n8n) and decide whether to install `pm2-logrotate`, which is host-wide and also affects cloudflared and n8n. Under either supervisor, "persistent logs" is a verification step (observe one real reboot) rather than a config change. Wording tied to journald (`main.py:28`, `main.py:69`, roadmap F-02) follows from whichever mechanism the plan picks.

## References

- Source: `src/spotify_to_led/main.py:1-46`, `:58-72`
- Decision being re-opened: `context/foundation/infrastructure.md:46-72`, `:79`, `:84`, `:89`, `:102`, `:112`, `:123`; `context/foundation/tech-stack.md:31`
- Premise-changing finding: `context/archive/2026-11-05-edge-surface-and-spotify-app-registration/verification.md:13,20,27`; `plan.md:101,123`
- Superseded assumption: `context/archive/spotify-redirect-uri-policy/research.md:134`
- Roadmap: `context/foundation/roadmap.md:72`, `:150-174`
- pm2 docs (Context7 `/websites/pm2_keymetrics_io`): application-declaration (`kill_timeout`, `max_restarts`, `min_uptime`), FAQ (log location, `pm2-logrotate`)
- Investigation done inline (small surface), no sub-agent tasks
