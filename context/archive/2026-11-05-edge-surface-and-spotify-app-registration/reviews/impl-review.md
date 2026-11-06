<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Edge Surface and Spotify App Registration

- **Plan**: context/changes/edge-surface-and-spotify-app-registration/plan.md
- **Scope**: Full plan
- **Reviewed phases**: 1, 3, 4
- **Date**: 2026-11-06
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical, 3 warnings, 1 observation

Phase 2 is not in the reviewed set because its manual rows 2.4 and 2.5 are still open (owner decision). Its evidence was read only through `verification.md` and is covered by F2.

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | WARNING |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | WARNING |

## Findings

### F1 -- Stale "connector on a different LAN host" text survives in two foundation spots

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: context/foundation/roadmap.md:229-233, context/foundation/infrastructure.md:134
- **Detail**: Phase 4 corrected the Unknown Unknowns bullet and the risk row, but its "touch nothing else" contract left two copies of the old assumption. The S-01 Risk paragraph in the roadmap still says the connector is on a different LAN host and that a derived URI becomes `http://192.168.x.x:8000/callback`. Getting Started step 6 in infrastructure.md still routes to "the Pi's reserved LAN address" via the dashboard. S-01's planner reads both. This is a gap in the plan, not drift from it.
- **Fix**: Append a one-line `Superseded, 2026-11-06:` note to each, pointing at the corrected Unknown Unknowns bullet (connector on the Pi, `localhost:8000`, `config.yml` ingress plus `tunnel route dns`).
- **Decision**: FIXED

### F2 -- The Access policy's refusal of non-owner emails is unproven (2.5 open)

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: context/changes/edge-surface-and-spotify-app-registration/plan.md:324-325
- **Detail**: 2.1 and 2.2 prove anonymous requests are redirected to Access. Only 2.5 proves the Allow rule is email-only rather than "any Google account". Per the PRD, the Access policy is the page's only gate, and S-01 will put the re-authorization flow behind it. 2.4 and 2.5 are left open by owner decision and recorded as pending in `verification.md`, so nothing is hidden, but the policy is untested for the case it exists for.
- **Fix**: Before S-01 ships the `/callback` route, try a second Google account at the Access login, confirm it is refused, and tick 2.5 (and 2.4) with a one-line addendum in `verification.md`.
- **Decision**: FIXED (queued as owner follow-up in `follow-ups/review-fixes.md`; needs a browser)

### F3 -- `.env.example` added at the repo root, outside the plan

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: .env.example
- **Detail**: Phase 3's Changes Required lists only the Spotify dashboard and `/etc/spotify-to-led/env`. `.env.example` was added in `ad5c518` and made gate 4.1 pass only as an exception. The content is benign: placeholders only (`led.example.com`), `.env` is gitignored, unknown keys are ignored by `Settings`, and its HOST comment reinforces the loopback rule. Recorded in `verification.md` and the p4 commit, but not in the plan.
- **Fix**: Keep the file, and add a dated addendum under Phase 3 in the plan naming it, so the plan matches the history before archive.
- **Decision**: FIXED

### F4 -- Edge and Pi checks are owner-attested, with no captured output

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: context/changes/edge-surface-and-spotify-app-registration/verification.md:12
- **Detail**: Every Automated row in Phases 1-3 runs on the Pi or against the live edge, so none can be re-run from this repo, and `verification.md` says literal transcripts were not kept. Also, criterion 1.3's probe (`systemctl is-active` or `docker inspect`) does not fit what was found (pm2); `verification.md` explains this. The plan's policy that edge work is human-only makes this expected, not a defect. Re-ran here: 4.1 (with the `.env.example` exception) and 4.2 pass, and `uv run pytest` passes (31).
- **Fix**: None needed. Optionally paste the `curl -sI` header lines (hostname redacted) into `verification.md` the next time 2.4/2.5 are run.
- **Decision**: SKIPPED
