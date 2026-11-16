---
change_id: usage-progress-on-display
title: Show the AI spend bar as a health bar on the board and status page
status: impl_reviewed
created: 2026-11-16
updated: 2026-11-16
archived_at: null
---

## Notes

Modify the current usage bar to look like a health bar. It will start at 100% green and it will go down over time to reach below 50% and be red below 30%. Modify Webflow also to be inline with it.

Added after both phases (owner request, 2026-11-16): the Daily limit field on the status page steps by $0.01 instead of $0.001 (`min` and `step` both 0.01, since the browser counts steps from `min`), so limits are whole cents from $0.01.
