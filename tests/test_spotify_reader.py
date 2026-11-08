"""The poll loop, driven tick by tick against a scripted Spotify.

Every request goes to an `httpx2.MockTransport` that answers token POSTs and
playback GETs from two scripts, so each branch of a tick is chosen by the
test. The clock and the sleep are fakes: a sleep advances the clock instead of
waiting, and the last scripted sleep ends `run()` the way shutdown does, by
cancellation. No real time passes and no account is needed.

The fake clock starts at the real time because `exchange_code` and `refresh`
stamp `expires_at` from the real clock; anchored together, "expires within
60 s" means the same thing to both.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from spotify_to_led.config import Settings
from spotify_to_led.spotify.auth import SCOPE, TOKEN_URL, TokenSet
from spotify_to_led.spotify.playback import PlaybackStatus, ReadFailure
from spotify_to_led.spotify.reader import (
    CURRENTLY_PLAYING_URL,
    AuthorizationStateMismatch,
    SpotifyReader,
)
from spotify_to_led.spotify.token_store import TokenStore

A_CLIENT_ID = "0123456789abcdef0123456789abcdef"
A_REDIRECT_URI = "https://led.example.com/callback"
A_CODE = "AQD-the-authorization-code"
AN_ACCESS_TOKEN = "BQ-the-access-token"
A_REFRESH_TOKEN = "AQ-the-refresh-token"
A_NEW_ACCESS_TOKEN = "BQ-the-new-access-token"
A_NEW_REFRESH_TOKEN = "AQ-the-new-refresh-token"
INTERVAL = 3.0

# A scripted answer: a response, or a function raising what the network would.
Answer = httpx2.Response | Callable[[httpx2.Request], httpx2.Response]


def a_track(track_id: str = "track-1", *, is_playing: bool = True) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "is_playing": is_playing,
            "currently_playing_type": "track",
            "item": {
                "id": track_id,
                "name": "A Track",
                "artists": [{"name": "An Artist"}],
                "album": {"images": [{"url": "https://i.scdn.co/image/a", "width": 640, "height": 640}]},
            },
        },
    )


def a_token_answer(access_token: str = A_NEW_ACCESS_TOKEN, expires_in: int = 3600) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "access_token": access_token,
            "token_type": "Bearer",
            "scope": SCOPE,
            "expires_in": expires_in,
            "refresh_token": A_NEW_REFRESH_TOKEN,
        },
    )


def unreachable(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("connection refused", request=request)


def timed_out(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ReadTimeout("timed out", request=request)


class FakeSpotify:
    """Answers token POSTs and playback GETs from their own scripts, in order."""

    def __init__(self, reads: list[Answer] | None = None, tokens: list[Answer] | None = None) -> None:
        self.reads = list(reads or [])
        self.tokens = list(tokens or [])
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        script = self.tokens if str(request.url) == TOKEN_URL else self.reads
        assert script, f"unscripted request to {request.url}"
        answer = script.pop(0)
        return answer if isinstance(answer, httpx2.Response) else answer(request)

    @property
    def reads_sent(self) -> list[httpx2.Request]:
        return [r for r in self.requests if r.method == "GET"]

    @property
    def token_posts(self) -> list[httpx2.Request]:
        return [r for r in self.requests if str(r.url) == TOKEN_URL]


class FakeClock:
    def __init__(self) -> None:
        self.start = datetime.now(UTC)
        self.now = self.start

    def __call__(self) -> datetime:
        return self.now


class FakeSleep:
    """Advances the clock instead of waiting; the `ticks`-th sleep ends `run()`."""

    def __init__(self, clock: FakeClock, ticks: int) -> None:
        self.clock = clock
        self.ticks = ticks
        self.durations: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.durations.append(seconds)
        self.clock.now += timedelta(seconds=seconds)
        if len(self.durations) >= self.ticks:
            raise asyncio.CancelledError


class Harness:
    def __init__(self, tmp_path: Path, spotify: FakeSpotify, *, ticks: int, token: TokenSet | None) -> None:
        self.spotify = spotify
        self.clock = FakeClock()
        self.sleep = FakeSleep(self.clock, ticks)
        self.store = TokenStore(tmp_path / "var" / "spotify-token.json")
        if token is not None:
            self.store.save(token)
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            spotify_client_id=A_CLIENT_ID,
            spotify_redirect_uri=A_REDIRECT_URI,
        )
        self.reader = SpotifyReader(
            httpx2.AsyncClient(transport=httpx2.MockTransport(spotify)),
            settings,
            self.store,
            interval=INTERVAL,
            clock=self.clock,
            sleep=self.sleep,
        )

    async def run(self) -> None:
        """Run until the scripted sleeps run out."""
        with pytest.raises(asyncio.CancelledError):
            await self.reader.run()


def a_token_set(*, expires_in: timedelta = timedelta(hours=1)) -> TokenSet:
    return TokenSet(
        access_token=AN_ACCESS_TOKEN,
        refresh_token=A_REFRESH_TOKEN,
        expires_at=datetime.now(UTC) + expires_in,
        authorized_at=datetime(2026, 5, 1, 9, 30, tzinfo=UTC),
        scope=SCOPE,
    )


def bearer_of(request: httpx2.Request) -> str:
    return request.headers["authorization"].removeprefix("Bearer ")


def form_of(request: httpx2.Request) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


async def settle() -> None:
    """Let the reader task run until it blocks on the wake event."""
    for _ in range(20):
        await asyncio.sleep(0)


async def stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_without_a_token_the_reader_asks_for_authorization_and_does_not_poll(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, FakeSpotify(), ticks=1, token=None)

    task = asyncio.create_task(harness.reader.run())
    await settle()

    assert harness.reader.snapshot.failure is ReadFailure.AUTHORIZATION_NEEDED
    assert harness.reader.snapshot.status is PlaybackStatus.UNKNOWN
    # Waiting on the wake event, not on a timer: nothing sent, no sleep taken.
    assert harness.spotify.requests == []
    assert harness.sleep.durations == []
    assert not task.done()
    await stop(task)


async def test_completing_authorization_wakes_the_loop_and_the_next_read_lands(
    tmp_path: Path,
) -> None:
    spotify = FakeSpotify(reads=[a_track()], tokens=[a_token_answer(AN_ACCESS_TOKEN)])
    harness = Harness(tmp_path, spotify, ticks=1, token=None)
    task = asyncio.create_task(harness.reader.run())
    await settle()

    url = harness.reader.begin_authorization()
    [state] = parse_qs(urlsplit(url).query)["state"]
    await harness.reader.complete_authorization(A_CODE, state)
    with pytest.raises(asyncio.CancelledError):
        await task

    snapshot = harness.reader.snapshot
    assert snapshot.status is PlaybackStatus.PLAYING
    assert snapshot.item is not None and snapshot.item.id == "track-1"
    assert snapshot.failure is None
    assert snapshot.authorized_at is not None
    assert form_of(spotify.token_posts[0])["code"] == A_CODE
    assert bearer_of(spotify.reads_sent[0]) == AN_ACCESS_TOKEN
    saved = harness.store.load()
    assert saved is not None and saved.access_token == AN_ACCESS_TOKEN


async def test_a_mismatched_state_is_rejected_and_nothing_is_saved(tmp_path: Path) -> None:
    spotify = FakeSpotify(tokens=[a_token_answer()])
    harness = Harness(tmp_path, spotify, ticks=1, token=None)
    url = harness.reader.begin_authorization()
    [state] = parse_qs(urlsplit(url).query)["state"]

    with pytest.raises(AuthorizationStateMismatch):
        await harness.reader.complete_authorization(A_CODE, "a-forged-state")
    # The pending authorization was used up by the failed attempt, so even
    # the right state no longer completes it.
    with pytest.raises(AuthorizationStateMismatch):
        await harness.reader.complete_authorization(A_CODE, state)

    assert spotify.requests == []
    assert harness.store.load() is None
    assert harness.reader.snapshot.authorized_at is None


async def test_completing_an_authorization_that_was_never_started_is_rejected(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, FakeSpotify(), ticks=1, token=None)

    with pytest.raises(AuthorizationStateMismatch):
        await harness.reader.complete_authorization(A_CODE, "any-state")

    assert harness.spotify.requests == []


async def test_only_the_latest_authorization_can_complete(tmp_path: Path) -> None:
    harness = Harness(tmp_path, FakeSpotify(), ticks=1, token=None)
    first = parse_qs(urlsplit(harness.reader.begin_authorization()).query)["state"][0]
    harness.reader.begin_authorization()

    with pytest.raises(AuthorizationStateMismatch):
        await harness.reader.complete_authorization(A_CODE, first)


async def test_a_token_near_expiry_is_refreshed_and_saved_before_the_read(
    tmp_path: Path,
) -> None:
    spotify = FakeSpotify(reads=[a_track()], tokens=[a_token_answer()])
    old = a_token_set(expires_in=timedelta(seconds=30))
    harness = Harness(tmp_path, spotify, ticks=1, token=old)

    await harness.run()

    assert [str(r.url) == TOKEN_URL for r in spotify.requests] == [True, False]
    assert form_of(spotify.token_posts[0])["refresh_token"] == A_REFRESH_TOKEN
    assert bearer_of(spotify.reads_sent[0]) == A_NEW_ACCESS_TOKEN
    saved = harness.store.load()
    assert saved is not None
    assert (saved.access_token, saved.refresh_token) == (A_NEW_ACCESS_TOKEN, A_NEW_REFRESH_TOKEN)
    # The grant age survives the refresh: it is what dates the six-month end.
    assert saved.authorized_at == old.authorized_at
    assert harness.reader.snapshot.authorized_at == old.authorized_at


async def test_a_token_far_from_expiry_is_used_as_is(tmp_path: Path) -> None:
    spotify = FakeSpotify(reads=[a_track()])
    harness = Harness(tmp_path, spotify, ticks=1, token=a_token_set())

    await harness.run()

    assert spotify.token_posts == []
    assert bearer_of(spotify.reads_sent[0]) == AN_ACCESS_TOKEN


async def test_a_401_triggers_one_refresh_and_the_read_is_retried_next_tick(
    tmp_path: Path,
) -> None:
    spotify = FakeSpotify(reads=[httpx2.Response(401), a_track()], tokens=[a_token_answer()])
    harness = Harness(tmp_path, spotify, ticks=2, token=a_token_set())

    await harness.run()

    assert len(spotify.token_posts) == 1
    assert [bearer_of(r) for r in spotify.reads_sent] == [AN_ACCESS_TOKEN, A_NEW_ACCESS_TOKEN]
    assert harness.reader.snapshot.status is PlaybackStatus.PLAYING
    assert harness.reader.snapshot.failure is None
    saved = harness.store.load()
    assert saved is not None and saved.access_token == A_NEW_ACCESS_TOKEN


async def test_a_401_that_survives_the_refresh_is_an_api_error_not_a_refresh_loop(
    tmp_path: Path,
) -> None:
    spotify = FakeSpotify(
        reads=[httpx2.Response(401), httpx2.Response(401), httpx2.Response(401)],
        tokens=[a_token_answer()],
    )
    harness = Harness(tmp_path, spotify, ticks=3, token=a_token_set())

    await harness.run()

    assert len(spotify.token_posts) == 1
    assert harness.reader.snapshot.failure is ReadFailure.API_ERROR


async def test_invalid_grant_discards_the_token_file_and_waits(tmp_path: Path) -> None:
    spotify = FakeSpotify(
        reads=[a_track()],
        tokens=[httpx2.Response(400, json={"error": "invalid_grant"})],
    )
    harness = Harness(tmp_path, spotify, ticks=1, token=a_token_set(expires_in=timedelta(seconds=10)))

    task = asyncio.create_task(harness.reader.run())
    await settle()

    assert not harness.store.path.exists()
    assert harness.reader.snapshot.failure is ReadFailure.AUTHORIZATION_NEEDED
    assert harness.reader.snapshot.authorized_at is None
    # No read with a dead credential, and no polling while it is missing.
    assert spotify.reads_sent == []
    assert harness.sleep.durations == []
    assert not task.done()
    await stop(task)


async def test_invalid_grant_after_a_401_also_discards_and_waits(tmp_path: Path) -> None:
    spotify = FakeSpotify(
        reads=[httpx2.Response(401)],
        tokens=[httpx2.Response(400, json={"error": "invalid_grant"})],
    )
    harness = Harness(tmp_path, spotify, ticks=1, token=a_token_set())

    task = asyncio.create_task(harness.reader.run())
    await settle()

    assert not harness.store.path.exists()
    assert harness.reader.snapshot.failure is ReadFailure.AUTHORIZATION_NEEDED
    await stop(task)


async def test_a_transient_refresh_failure_keeps_the_token(tmp_path: Path) -> None:
    # Only `invalid_grant` ends a grant; a refused refresh is retried later.
    spotify = FakeSpotify(tokens=[httpx2.Response(503, json={"error": "server_error"})])
    harness = Harness(tmp_path, spotify, ticks=1, token=a_token_set(expires_in=timedelta(seconds=10)))

    await harness.run()

    assert harness.reader.snapshot.failure is ReadFailure.API_ERROR
    assert harness.store.load() is not None
    assert spotify.reads_sent == []


async def test_a_network_failure_during_refresh_is_a_network_failure(tmp_path: Path) -> None:
    spotify = FakeSpotify(tokens=[unreachable])
    harness = Harness(tmp_path, spotify, ticks=1, token=a_token_set(expires_in=timedelta(seconds=10)))

    await harness.run()

    assert harness.reader.snapshot.failure is ReadFailure.NETWORK
    assert harness.store.load() is not None


@pytest.mark.parametrize(
    ("headers", "expected_sleep"),
    [
        ({"Retry-After": "12"}, 12.0),
        # Never faster than the normal cadence, whatever the header says.
        ({"Retry-After": "1"}, INTERVAL),
        ({}, 30.0),
    ],
)
async def test_429_is_rate_limited_and_honours_retry_after(
    tmp_path: Path, headers: dict[str, str], expected_sleep: float
) -> None:
    spotify = FakeSpotify(reads=[httpx2.Response(429, headers=headers)])
    harness = Harness(tmp_path, spotify, ticks=1, token=a_token_set())

    await harness.run()

    assert harness.reader.snapshot.failure is ReadFailure.RATE_LIMITED
    assert harness.sleep.durations == [expected_sleep]


@pytest.mark.parametrize("status_code", [403, 500, 502, 503])
async def test_other_error_statuses_are_api_errors(tmp_path: Path, status_code: int) -> None:
    spotify = FakeSpotify(reads=[httpx2.Response(status_code)])
    harness = Harness(tmp_path, spotify, ticks=1, token=a_token_set())

    await harness.run()

    assert harness.reader.snapshot.failure is ReadFailure.API_ERROR
    assert harness.sleep.durations == [INTERVAL]


@pytest.mark.parametrize("failing", [unreachable, timed_out])
async def test_transport_errors_and_timeouts_are_network_failures(
    tmp_path: Path, failing: Callable[[httpx2.Request], httpx2.Response]
) -> None:
    spotify = FakeSpotify(reads=[failing])
    harness = Harness(tmp_path, spotify, ticks=1, token=a_token_set())

    await harness.run()

    assert harness.reader.snapshot.failure is ReadFailure.NETWORK


async def test_the_read_asks_for_episodes_with_a_five_second_timeout(tmp_path: Path) -> None:
    spotify = FakeSpotify(reads=[httpx2.Response(204)])
    harness = Harness(tmp_path, spotify, ticks=1, token=a_token_set())

    await harness.run()

    [read] = spotify.reads_sent
    assert read.url.copy_with(query=None) == CURRENTLY_PLAYING_URL
    assert read.url.params["additional_types"] == "track,episode"
    assert read.extensions["timeout"]["read"] == 5.0
    assert harness.reader.snapshot.status is PlaybackStatus.NOTHING_PLAYING


async def test_failures_keep_the_item_and_the_time_the_failures_began(tmp_path: Path) -> None:
    spotify = FakeSpotify(reads=[a_track(), httpx2.Response(500), unreachable])
    harness = Harness(tmp_path, spotify, ticks=3, token=a_token_set())

    await harness.run()

    snapshot = harness.reader.snapshot
    # What was playing is still the best belief; the failure says how stale.
    assert snapshot.status is PlaybackStatus.PLAYING
    assert snapshot.item is not None and snapshot.item.id == "track-1"
    assert snapshot.failure is ReadFailure.NETWORK
    # Set by the first failure (one interval in) and not moved by the second.
    assert snapshot.failure_since == harness.clock.start + timedelta(seconds=INTERVAL)
    assert snapshot.last_read_at == harness.clock.start


async def test_a_successful_read_clears_the_failure(tmp_path: Path) -> None:
    spotify = FakeSpotify(reads=[a_track(), httpx2.Response(500), httpx2.Response(204)])
    harness = Harness(tmp_path, spotify, ticks=3, token=a_token_set())

    await harness.run()

    snapshot = harness.reader.snapshot
    assert snapshot.status is PlaybackStatus.NOTHING_PLAYING
    assert snapshot.item is None
    assert (snapshot.failure, snapshot.failure_since) == (None, None)
    assert snapshot.last_read_at == harness.clock.start + timedelta(seconds=2 * INTERVAL)


async def test_a_paused_item_stays_the_believed_item(tmp_path: Path) -> None:
    spotify = FakeSpotify(reads=[a_track(), a_track(is_playing=False)])
    harness = Harness(tmp_path, spotify, ticks=2, token=a_token_set())

    await harness.run()

    snapshot = harness.reader.snapshot
    assert snapshot.status is PlaybackStatus.PAUSED
    assert snapshot.item is not None and snapshot.item.id == "track-1"


async def test_an_unexpected_exception_does_not_end_the_loop(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(request: httpx2.Request) -> httpx2.Response:
        raise RuntimeError("a bug, not a network fault")

    spotify = FakeSpotify(reads=[broken, a_track()])
    harness = Harness(tmp_path, spotify, ticks=2, token=a_token_set())

    with caplog.at_level(logging.ERROR):
        await harness.run()

    assert "unexpected error" in caplog.text
    # The loop went on to the next tick and read successfully.
    assert len(spotify.reads_sent) == 2
    assert harness.reader.snapshot.status is PlaybackStatus.PLAYING
    assert harness.reader.snapshot.failure is None


async def test_cancellation_ends_the_loop(tmp_path: Path) -> None:
    # The lifespan stops the reader by cancelling it; the guard must not eat that.
    harness = Harness(tmp_path, FakeSpotify(), ticks=1, token=None)
    task = asyncio.create_task(harness.reader.run())
    await settle()

    await stop(task)

    assert task.cancelled()


async def test_logs_name_changes_once_rather_than_every_read(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    spotify = FakeSpotify(reads=[a_track(), a_track(), a_track(), a_track("track-2")])
    harness = Harness(tmp_path, spotify, ticks=4, token=a_token_set())

    with caplog.at_level(logging.INFO, logger="spotify_to_led.spotify.reader"):
        await harness.run()

    playback_lines = [r for r in caplog.records if r.getMessage().startswith("Spotify playback")]
    assert len(playback_lines) == 2


async def test_no_token_code_or_verifier_reaches_the_log_across_a_full_cycle(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Authorize, read, get a 401, refresh, read, fail, recover: every path
    # that logs, with every secret in play.
    spotify = FakeSpotify(
        reads=[a_track(), httpx2.Response(401), a_track("track-2"), unreachable, a_track()],
        tokens=[
            a_token_answer(AN_ACCESS_TOKEN),
            a_token_answer(A_NEW_ACCESS_TOKEN),
        ],
    )
    harness = Harness(tmp_path, spotify, ticks=5, token=None)

    with caplog.at_level(logging.DEBUG):
        task = asyncio.create_task(harness.reader.run())
        await settle()
        url = harness.reader.begin_authorization()
        [state] = parse_qs(urlsplit(url).query)["state"]
        await harness.reader.complete_authorization(A_CODE, state)
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(spotify.token_posts) == 2
    verifier = form_of(spotify.token_posts[0])["code_verifier"]
    assert caplog.records, "the cycle should have logged something to check"
    for secret in (
        A_CODE,
        verifier,
        AN_ACCESS_TOKEN,
        A_NEW_ACCESS_TOKEN,
        A_NEW_REFRESH_TOKEN,
    ):
        assert secret not in caplog.text
    assert harness.reader.snapshot.failure is None
