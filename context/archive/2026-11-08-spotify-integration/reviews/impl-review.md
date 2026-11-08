<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Spotify authorization and playback read (S-01)

- **Plan**: context/changes/spotify-integration/plan.md
- **Scope**: Full plan
- **Reviewed phases**: 1, 2, 3
- **Date**: 2026-11-08
- **Verdict**: APPROVED
- **Findings**: 0 critical, 2 warnings, 5 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | WARNING |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Findings

### F1 -- Non-finite Retry-After kills the reader task and breaks shutdown

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/spotify/reader.py:294
- **Detail**: `_retry_after` uses `float()`, which accepts `nan`, `inf` and `1e400`. `max(nan, 3.0)` is `nan`, and `asyncio.sleep(nan)` raises `ValueError` (verified). `_pause` re-raises it outside `run()`'s guard, so the reader task dies with the snapshot stuck on `rate_limited`. At shutdown `_cancel` only suppresses `CancelledError`, so the HTTP client and board transport are not closed. `inf` or huge values make the reader sleep until the next authorization. Spotify is unlikely to send these, but it is the one path that breaks the module's "never raised" contract.
- **Fix**: In `_retry_after`, fall back to the default unless `math.isfinite(value)`, and clamp to a ceiling (for example 3600 s); add a test for `nan` and `inf`.
- **Decision**: FIXED -- non-finite Retry-After falls back to 30 s, capped at 3600 s; tests for nan, inf, 1e400, 86400

### F2 -- Unplanned defensive additions (documented, benign)

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: src/spotify_to_led/main.py:43, src/spotify_to_led/spotify/auth.py:128
- **Detail**: Beyond the plan: `/callback` 503 when unconfigured and 400 on missing code; the access-log query filter and `httpx2` at WARNING (0a181ea, from live verification); `repr=False` on secrets; `missing_refresh_token` and malformed-token-response errors; refresh-versus-new-grant race guard; `create_spotify_client` test seam; `O_EXCL` temp files. All were reported during implementation, are tested, and the log filter closes a real leak found on the Pi.
- **Fix**: Accept as is; they are recorded in verification.md and the implementation reports.
- **Decision**: ACCEPTED -- defensive additions kept; all reported during implementation and tested

### F3 -- Malformed responses go to the catch-all or escape as 500

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/spotify/reader.py:192, src/spotify_to_led/main.py:199
- **Detail**: A 200 with a non-JSON body raises in `response.json()` outside the tick's try, so `run()` logs a full traceback every 3 s while it lasts (no secrets in it, checked). Separately, `httpx2.DecodingError` is a `RequestError` but not a `TransportError`, so a corrupt token response makes `/callback` a bare 500 instead of 502, and the reader treats it via the catch-all.
- **Fix**: Catch `ValueError` around `response.json()` as `_fail(API_ERROR, "malformed body")`, and catch `httpx2.RequestError` instead of `TransportError` in reader.py:186 and main.py:199.
- **Decision**: FIXED -- malformed 200 body and httpx2.DecodingError classified as api_error without a traceback; /callback catches httpx2.RequestError (502)

### F4 -- Persistent 401 reports api_error, and one 401 tick is unclassified

- **Severity**: 💡 OBSERVATION
- **Impact**: 🔎 MEDIUM -- real tradeoff; pause to reason through it
- **Dimension**: Plan Adherence
- **Location**: src/spotify_to_led/spotify/reader.py:172
- **Detail**: The tick that gets a 401 refreshes and records no failure (minor drift from "retry on the next tick" wording; intent kept). If the refreshed token is also refused, the loop reports `api_error "HTTP 401"` until a read succeeds, never `authorization_needed`, so S-07 could not tell the owner to re-authorize in that case. Live, a revoke surfaced correctly as `invalid_grant` at refresh, so this is a corner case.
- **Fix**: Leave for S-07, which owns cause wording; note it in that change's frame.
  - Strength: S-07 decides how causes map to owner actions; no code churn now.
  - Tradeoff: The corner case stays mislabelled until then.
  - Confidence: MED -- unclear how often Spotify returns 401 for a still-refreshable grant.
  - Blind spot: Not observed live.
- **Decision**: DEFERRED -- queued for S-07 in follow-ups/review-fixes.md

### F5 -- Token discard on invalid_grant is unguarded

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/spotify/reader.py:231
- **Detail**: If `unlink` raises (for example a permission error on `var/`), the error reaches the catch-all: one traceback and one `api_error` tick before the next tick reports `authorization_needed` (the in-memory token is already cleared).
- **Fix**: Guard `discard()` with `except OSError` and log the type name, as `_save` does.
- **Decision**: FIXED -- discard() guarded with except OSError, logged by type name

### F6 -- Crash-orphaned temp files and no directory fsync

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/spotify/token_store.py:66
- **Detail**: A SIGKILL or power cut between create and replace leaves `.spotify-token.json.<hex>.tmp` (0600, live tokens) in `var/`; nothing sweeps them. The parent directory is not fsynced after `os.replace`, so a power cut can lose a rotation; that ends as `invalid_grant`, which is the accepted recovery path. Save also runs blocking fsync on the event loop about once an hour, acceptable at that cadence.
- **Fix**: Optional: fsync the directory after replace and remove stale `.<name>.*.tmp` files on save.
- **Decision**: FIXED -- stale temp files swept on save; parent directory fsynced after replace (POSIX)

### F7 -- Spotify error text reflected in /callback

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW -- quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/spotify_to_led/main.py:184
- **Detail**: The `error` query value is echoed verbatim in a `text/plain` 400. No XSS, and the route sits behind Cloudflare Access, but a crafted link can put arbitrary text under the service's sentence.
- **Fix**: Echo it only when it matches `[a-z_]{1,64}`, otherwise say "unknown error".
- **Decision**: FIXED -- only [a-z_]{1,64} error codes are echoed, otherwise 'unknown error'
