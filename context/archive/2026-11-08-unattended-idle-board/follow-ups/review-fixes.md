# Review follow-ups: unattended-idle-board

## For S-04 and S-06: never put a real cover behind a rule that ignores read failures

From `reviews/impl-review.md` F4. `display.select_presentation` ignores `snapshot.failure` by design in S-02, so while Spotify reads fail with the status stuck at PLAYING or PAUSED, the board keeps showing its last presentation indefinitely. With S-02's temporary placeholder that claims nothing about which item is playing, so it does not breach the hard rule. Once S-04 shows real covers through the same rule, the same path shows an unmarked stale cover, which the hard rules forbid (FR-018). S-04 must either ship together with S-06's staleness marker or keep covers off the board until S-06 lands. Recorded in the roadmap under S-04's Risk. Location: `src/spotify_to_led/display.py`, `select_presentation`.
