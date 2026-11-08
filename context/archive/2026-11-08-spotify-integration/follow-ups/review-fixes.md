# Review follow-ups: spotify-integration

## For S-07 (status and re-authorization page): persistent 401 is reported as api_error

From `reviews/impl-review.md` F4. When a read gets 401, the reader refreshes once and retries on the next tick. If the refreshed token is refused again, the loop reports `failure: api_error` ("HTTP 401") until a read succeeds, never `authorization_needed`, so a status page keyed on `authorization_needed` would not offer re-authorization in that case. A revoked grant observed live surfaced correctly as `invalid_grant` at the next refresh (`verification.md` 3.12), so this is a corner case. S-07 owns how failure causes map to owner actions; decide there whether a 401 that survives a refresh should count as `authorization_needed`. Location: `src/spotify_to_led/spotify/reader.py`, the 401 branch of `_tick`.
