# Review Fixes

Queued from `reviews/impl-review.md` (2026-11-06).

## F2 -- Prove the Access policy refuses non-owner emails

- **When:** before S-01 ships the `/callback` route.
- **Who:** the owner, in a browser (edge checks are human-only).
- **Steps:**
  1. In a private window, open `https://<sub>.<owner-domain>/healthz`, confirm the Access login appears, log in with the owner's Google account, and confirm `{"status":"ok"}` (plan row 2.4).
  2. In a fresh private window, log in with a different Google account and confirm Access refuses it (plan row 2.5).
- **Then:** tick 2.4 and 2.5 in the plan's `## Progress`, and add a dated line to the Phase 2 table in `verification.md`.
