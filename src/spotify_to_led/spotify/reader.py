"""The unattended poll loop that keeps the believed playback state current.

Once the owner has authorized, nobody is around to help: the loop refreshes
the access token before it expires, reads `currently-playing` every few
seconds, and classifies every way a read can fail instead of raising. Its
only output is the snapshot, which always says what was last known to play
and, separately, whether reading currently works.

`invalid_grant` is routine here, not exceptional: Spotify ends every refresh
token six months after authorization. The loop then discards the token file,
reports that authorization is needed and sleeps on the wake event until
`complete_authorization` hands it a new grant, with no restart.

No log line, return value or exception raised here carries a token, the
authorization code or the verifier.
"""

import asyncio
import logging
import math
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx2

from spotify_to_led.config import Settings
from spotify_to_led.spotify.auth import (
    AuthorizationRevoked,
    PendingAuthorization,
    SpotifyAuthError,
    TokenSet,
    authorize_url,
    exchange_code,
    refresh,
)
from spotify_to_led.spotify.playback import (
    INITIAL_SNAPSHOT,
    PlaybackSnapshot,
    PlaybackStatus,
    PlayingItem,
    ReadFailure,
    parse_currently_playing,
)
from spotify_to_led.spotify.token_store import TokenStore

logger = logging.getLogger(__name__)

CURRENTLY_PLAYING_URL = "https://api.spotify.com/v1/me/player/currently-playing"
# Episodes are only reported when asked for; without this a podcast reads as
# an item-less "unknown".
CURRENTLY_PLAYING_PARAMS = {"additional_types": "track,episode"}
READ_TIMEOUT_SECONDS = 5.0
# Refreshing this long before expiry means a read never goes out with a token
# that lapses in flight.
REFRESH_MARGIN = timedelta(seconds=60)
# Used when a 429 comes without a usable `Retry-After`: the length of
# Spotify's rolling rate-limit window.
DEFAULT_RETRY_AFTER_SECONDS = 30.0
# The longest a `Retry-After` may put the reader to sleep, so an absurd header
# cannot park it for days.
MAX_RETRY_AFTER_SECONDS = 3600.0

Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]


class AuthorizationStateMismatch(Exception):
    """The `state` returned to `/callback` is not the one this process issued.

    Either no authorization was started, a newer one replaced it, or the
    request is forged. The code is not exchanged in any of those cases.
    """


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SpotifyReader:
    """Owns the held token set and keeps the playback snapshot current."""

    def __init__(
        self,
        client: httpx2.AsyncClient,
        settings: Settings,
        store: TokenStore,
        *,
        interval: float = 3.0,
        clock: Clock = _utc_now,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if settings.spotify_client_id is None or settings.spotify_redirect_uri is None:
            raise ValueError("SpotifyReader needs a configured client ID and redirect URI")
        self._client = client
        self._client_id = settings.spotify_client_id
        self._redirect_uri = settings.spotify_redirect_uri
        self._store = store
        self._interval = interval
        self._clock = clock
        self._sleep = sleep
        self._wake = asyncio.Event()
        self._pending: PendingAuthorization | None = None
        self._token: TokenSet | None = store.load()
        self._state = INITIAL_SNAPSHOT
        # Set once a 401 has been answered with a refresh, so a token Spotify
        # keeps rejecting is refreshed once rather than on every tick.
        self._refreshed_after_401 = False

    @property
    def snapshot(self) -> PlaybackSnapshot:
        """The believed state, with the grant time of the token set held now."""
        authorized_at = self._token.authorized_at if self._token is not None else None
        return replace(self._state, authorized_at=authorized_at)

    def begin_authorization(self) -> str:
        """Start an authorization and return the URL that sends the owner to Spotify.

        Only the latest one can complete: starting another replaces it.
        """
        self._pending = PendingAuthorization.create()
        return authorize_url(self._client_id, self._redirect_uri, self._pending)

    async def complete_authorization(self, code: str, state: str) -> None:
        """Exchange the code from `/callback`, hold and save the grant, wake the loop.

        Raises `AuthorizationStateMismatch` for an unknown or wrong `state`;
        exchange failures surface as `AuthorizationRevoked`, `SpotifyAuthError`
        or an `httpx2.TransportError`. The pending authorization is used up
        either way, so a code cannot be replayed against it.
        """
        pending, self._pending = self._pending, None
        # Compared as bytes so a non-ASCII `state` is a mismatch, not a TypeError.
        if pending is None or not secrets.compare_digest(
            state.encode("utf-8"), pending.state.encode("utf-8")
        ):
            raise AuthorizationStateMismatch("authorization state does not match")
        token = await exchange_code(
            self._client, self._client_id, self._redirect_uri, code, pending.code_verifier
        )
        self._token = token
        self._refreshed_after_401 = False
        self._save(token)
        logger.info("Spotify authorization completed")
        self._wake.set()

    async def run(self) -> None:
        """Read until cancelled. A failing tick is logged and classified, never raised."""
        while True:
            try:
                wait = await self._tick()
            except Exception:
                # A bug must not end the reader. The read did not succeed, so
                # the snapshot says so rather than looking fresh.
                logger.exception("unexpected error in the Spotify reader; it keeps running")
                self._fail(ReadFailure.API_ERROR)
                wait = self._interval
            await self._pause(wait)

    async def _tick(self) -> float | None:
        """One read attempt; returns how long to wait, or `None` to wait for a wake."""
        token = self._token
        if token is None:
            self._fail(ReadFailure.AUTHORIZATION_NEEDED)
            return None
        try:
            if token.expires_at - self._clock() <= REFRESH_MARGIN:
                token = await self._refresh(token)
            response = await self._client.get(
                CURRENTLY_PLAYING_URL,
                params=CURRENTLY_PLAYING_PARAMS,
                headers={"Authorization": f"Bearer {token.access_token}"},
                timeout=READ_TIMEOUT_SECONDS,
            )
            if response.status_code == 401 and not self._refreshed_after_401:
                # The token was refused before its recorded expiry. One refresh
                # usually cures that; the read is retried on the next tick.
                self._refreshed_after_401 = True
                await self._refresh(token)
                return self._interval
        except AuthorizationRevoked:
            self._revoke(token)
            return None
        except SpotifyAuthError as error:
            # A refused refresh that is not `invalid_grant` is worth retrying:
            # the token set is kept.
            self._fail(ReadFailure.API_ERROR, f"token refresh: HTTP {error.status_code}")
            return self._interval
        except httpx2.TransportError as error:
            self._fail(ReadFailure.NETWORK, type(error).__name__)
            return self._interval
        except httpx2.DecodingError:
            # Spotify answered, but with a body that would not decode.
            self._fail(ReadFailure.API_ERROR, "undecodable body")
            return self._interval

        status_code = response.status_code
        if status_code in (200, 204):
            try:
                body = response.json() if status_code == 200 and response.content else None
            except ValueError:
                # A classified failure rather than the catch-all, which would
                # log a traceback on every tick for as long as it lasts.
                self._fail(ReadFailure.API_ERROR, "malformed body")
                return self._interval
            status, item = parse_currently_playing(status_code, body)
            self._refreshed_after_401 = False
            self._succeed(status, item)
            return self._interval
        if status_code == 429:
            self._fail(ReadFailure.RATE_LIMITED, f"HTTP {status_code}")
            return max(_retry_after(response), self._interval)
        self._fail(ReadFailure.API_ERROR, f"HTTP {status_code}")
        return self._interval

    async def _refresh(self, token: TokenSet) -> TokenSet:
        """Refresh `token`, persisting the new set before it is used."""
        new = await refresh(self._client, self._client_id, token)
        if self._token is not None and self._token is not token:
            # A new authorization landed while this refresh was in flight; it
            # wins, and the refreshed old grant is dropped unsaved.
            return self._token
        self._save(new)
        self._token = new
        return new

    def _save(self, token: TokenSet) -> None:
        try:
            self._store.save(token)
        except OSError as error:
            # Keep running on the in-memory set. A rotation lost this way ends
            # later as `invalid_grant`, which is already the routine recovery.
            logger.error(
                "could not save the Spotify token set to %s (%s); continuing in memory",
                self._store.path,
                type(error).__name__,
            )

    def _revoke(self, token: TokenSet) -> None:
        # Only the grant that was refused is dropped: a newer one that arrived
        # meanwhile is left alone.
        if self._token is token:
            self._token = None
            try:
                self._store.discard()
            except OSError as error:
                # The grant is already dropped in memory; a file left behind
                # is refused again at the next start and discarded then.
                logger.error(
                    "could not delete the Spotify token file %s (%s)",
                    self._store.path,
                    type(error).__name__,
                )
        self._fail(ReadFailure.AUTHORIZATION_NEEDED)

    async def _pause(self, seconds: float | None) -> None:
        """Sleep `seconds` (forever when `None`), cut short by the wake event."""
        waiters = [asyncio.ensure_future(self._wake.wait())]
        sleeper = None if seconds is None else asyncio.ensure_future(self._sleep(seconds))
        if sleeper is not None:
            waiters.append(sleeper)
        try:
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if sleeper in done:
                # `asyncio.wait` keeps a failed waiter's exception to itself;
                # re-raised here so a failing sleep is not mistaken for a nap.
                sleeper.result()
        finally:
            for waiter in waiters:
                waiter.cancel()
            self._wake.clear()

    def _succeed(self, status: PlaybackStatus, item: PlayingItem | None) -> None:
        self._publish(
            replace(
                self._state,
                status=status,
                item=item,
                failure=None,
                failure_since=None,
                last_read_at=self._clock(),
            )
        )

    def _fail(self, failure: ReadFailure, detail: str | None = None) -> None:
        # Status and item stay as last read: a failed read says nothing about
        # what is playing, only that the answer may be stale.
        since = self._state.failure_since or self._clock()
        self._publish(replace(self._state, failure=failure, failure_since=since), detail)

    def _publish(self, new: PlaybackSnapshot, detail: str | None = None) -> None:
        """Replace the snapshot, logging only what changed."""
        old, self._state = self._state, new
        if (old.status, _identity(old.item)) != (new.status, _identity(new.item)):
            logger.info("Spotify playback: %s %s", new.status, _describe(new.item))
        if old.failure == new.failure:
            return
        if new.failure is ReadFailure.AUTHORIZATION_NEEDED:
            logger.warning("Spotify authorization needed: open /authorize to grant access")
        elif new.failure is not None:
            logger.info("Spotify read failing: %s (%s)", new.failure, detail or "no detail")
        else:
            logger.info("Spotify read recovered")


def _identity(item: PlayingItem | None) -> tuple[str, str | None] | None:
    return (item.kind, item.id) if item is not None else None


def _describe(item: PlayingItem | None) -> str:
    if item is None:
        return "(no item)"
    return f"{item.kind} {item.name!r}" if item.name is not None else item.kind


def _retry_after(response: httpx2.Response) -> float:
    """Seconds from `Retry-After`, or the default when it is missing or unreadable."""
    try:
        seconds = float(response.headers["retry-after"])
    except (KeyError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS
    # `float` accepts "nan" and "inf", and `asyncio.sleep(nan)` raises, which
    # would end the reader.
    if not math.isfinite(seconds):
        return DEFAULT_RETRY_AFTER_SECONDS
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)
