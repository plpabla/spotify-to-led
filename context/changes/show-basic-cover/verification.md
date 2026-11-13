# Verification: show-basic-cover

## Board check (Pi, 2026-11-13)

The owner pulled `feat/show-basic-cover` on the Pi and watched it on the real board with
AI-enhanced covers on. Verdict: works well — a never-seen cover shows its plain frame with the
green top-left dot while the enhanced version generates, then the enhanced frame without it.

One change requested: no black ring around the dot. The dot keeps its place (one pixel in from
the top-left corner, 1x1 cell) and the cover's own pixels show around it. Applied in
`frames.with_generating_marker`; the colour stays at the starting value `(0, 160, 0)`.

Not checked separately: 2.4, the local stub-transport run (log lines and status-page text); the
owner went straight to the hardware.
