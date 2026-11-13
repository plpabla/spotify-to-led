"""Artwork selection, and the cover loader driven against a scripted image server.

Every download goes to an `httpx2.MockTransport` that answers from a script,
as in `test_spotify_reader.py`, so each attempt's outcome is chosen by the
test. The sleep is a fake that records each delay and then parks the loader
until the test lets it go on, which is what makes the `FAILED`-during-backoff
state observable. No real time passes and no network is used.

For enhanced covers the same client also reaches a scripted OpenRouter,
routed by host, with a real `Enhancer` whose sleep returns at once and whose
clock stands still until a test moves it.
"""

import asyncio
import base64
import json
import logging
import struct
import zlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import httpx2
import pytest
from PIL import Image

from spotify_to_led import covers
from spotify_to_led.board.transport import frame_to_pixel_colors, validate_frame
from spotify_to_led.covers import (
    CoverLoader,
    CoverStatus,
    select_artwork,
)
from spotify_to_led.enhance import Enhancer
from spotify_to_led.frame_cache import FrameCache
from spotify_to_led.rendering import render_cover, render_enhanced
from spotify_to_led.spotify.playback import Artwork, PlayingItem

SIZE = 32
A_URL = "https://i.scdn.co/image/cover-a"
ANOTHER_URL = "https://i.scdn.co/image/cover-b"
COVER_COLOUR: tuple[int, int, int] = (200, 40, 90)


def _png(colour: tuple[int, int, int] = COVER_COLOUR) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), colour).save(buffer, format="PNG")
    return buffer.getvalue()


A_PNG = _png()

# A scripted answer: a response, or a coroutine function standing in for the
# network (raising, or never answering).
Answer = httpx2.Response | Callable[[httpx2.Request], Awaitable[httpx2.Response]]


def a_cover() -> httpx2.Response:
    return httpx2.Response(200, content=A_PNG, headers={"content-type": "image/png"})


async def unreachable(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("connection refused", request=request)


class ImageServer:
    """Answers every GET from one script, in order; `hang` never answers."""

    def __init__(self, answers: list[Answer]) -> None:
        self.answers = list(answers)
        self.requests: list[httpx2.Request] = []
        self.cancelled: list[str] = []

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        assert self.answers, f"unscripted request to {request.url}"
        answer = self.answers.pop(0)
        return answer if isinstance(answer, httpx2.Response) else await answer(request)

    async def hang(self, request: httpx2.Request) -> httpx2.Response:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.append(str(request.url))
            raise
        raise AssertionError("unreachable")

    @property
    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]


class FakeSleep:
    """Records each delay, then waits until the test calls `wake`."""

    def __init__(self) -> None:
        self.durations: list[float] = []
        self._wake = asyncio.Event()

    async def __call__(self, seconds: float) -> None:
        self.durations.append(seconds)
        await self._wake.wait()
        self._wake.clear()

    def wake(self) -> None:
        self._wake.set()


def a_loader(server: ImageServer, sleep: FakeSleep) -> CoverLoader:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(server))
    return CoverLoader(client, SIZE, sleep=sleep)


async def until(condition: Callable[[], bool]) -> None:
    """Let the loader's task run until `condition` holds.

    Bounded by time, not by turns of the loop: a generation decodes and
    renders in worker threads, which a fixed number of turns can outrun.
    """
    deadline = asyncio.get_running_loop().time() + 5.0
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("the condition never held")
        await asyncio.sleep(0.001)


def an_item(*widths: int | None) -> PlayingItem:
    return PlayingItem(
        kind="track",
        id="track-1",
        name="A Track",
        subtitle="An Artist",
        artwork=tuple(
            Artwork(url=f"https://i.scdn.co/image/{index}", width=width, height=width)
            for index, width in enumerate(widths)
        ),
    )


# --- selection ------------------------------------------------------------


@pytest.mark.parametrize(
    ("widths", "size", "expected_index"),
    [
        # Spotify's album sizes: the smallest that covers the board wins.
        ((640, 300, 64), 16, 2),
        ((640, 300, 64), 32, 2),
        ((640, 300, 64), 64, 2),
        ((640, 300, 64), 65, 1),
        # Nothing covers the board: the widest, which Spotify lists first.
        ((640, 300, 64), 1000, 0),
        # An unknown width never qualifies, whatever the image really is.
        ((None, 300, None), 64, 1),
        ((640, None, 64), 32, 2),
        ((None, None), 32, 0),
        # A single image is taken whatever its size.
        ((640,), 32, 0),
        ((16,), 64, 0),
        ((None,), 32, 0),
    ],
)
def test_select_artwork_takes_the_smallest_image_that_covers_the_board(
    widths: tuple[int | None, ...], size: int, expected_index: int
) -> None:
    item = an_item(*widths)

    assert select_artwork(item, size) == item.artwork[expected_index]


def test_select_artwork_without_artwork_is_none() -> None:
    assert select_artwork(an_item(), SIZE) is None
    assert select_artwork(None, SIZE) is None


async def test_a_download_that_trickles_past_the_limit_fails_as_a_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every chunk would arrive inside the per-phase timeout; only the total
    # bound stops it.
    monkeypatch.setattr(covers, "FETCH_TIMEOUT_SECONDS", 0.05)

    async def trickle(request: httpx2.Request) -> httpx2.Response:
        await asyncio.sleep(0.5)
        return a_cover()

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(trickle))

    with pytest.raises(TimeoutError):
        await covers.fetch_cover(client, A_URL, SIZE)
    await client.aclose()


@pytest.mark.parametrize("limit", ["MAX_COVER_BYTES", "MAX_COVER_PIXELS"])
async def test_a_download_too_large_to_be_a_cover_fails(
    monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    # The 64x64 test cover is a few hundred bytes and 4096 pixels.
    monkeypatch.setattr(covers, limit, 100)
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(ImageServer([a_cover()])))

    with pytest.raises(covers.CoverFetchError):
        await covers.fetch_cover(client, A_URL, SIZE)
    await client.aclose()


async def test_a_download_that_finishes_after_its_url_was_dropped_changes_nothing() -> None:
    # As if cancellation of A's download had been swallowed: it completes
    # while B is the wanted cover, and must not answer for B.
    server = ImageServer([])
    server.answers = [server.hang, a_cover()]
    loader = a_loader(server, FakeSleep())
    loader.want(ANOTHER_URL)
    await until(lambda: len(server.requests) == 1)

    await loader._load(A_URL)

    assert loader.state(ANOTHER_URL).status is CoverStatus.PENDING
    await loader.aclose()


# --- loader ---------------------------------------------------------------


async def test_a_wanted_cover_is_pending_then_ready_with_the_rendered_frame(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = ImageServer([a_cover()])
    loader = a_loader(server, FakeSleep())

    with caplog.at_level(logging.INFO, logger="spotify_to_led.covers"):
        loader.want(A_URL)
        assert loader.state(A_URL).status is CoverStatus.PENDING
        await until(lambda: loader.state(A_URL).status is CoverStatus.READY)

    frame = loader.state(A_URL).frame
    assert frame is not None
    validate_frame(frame, SIZE)
    expected = render_cover(Image.open(BytesIO(A_PNG)), SIZE)
    assert frame_to_pixel_colors(frame) == frame_to_pixel_colors(expected)
    [request] = server.requests
    assert str(request.url) == A_URL
    assert request.extensions["timeout"]["read"] == 4.0
    # The shared client is the Spotify client; no credential goes to the CDN.
    assert "authorization" not in request.headers
    assert [r.getMessage() for r in caplog.records] == [f"cover ready: {A_URL}"]
    await loader.aclose()


async def test_a_url_that_is_not_the_wanted_one_reads_as_pending() -> None:
    server = ImageServer([a_cover()])
    loader = a_loader(server, FakeSleep())
    loader.want(A_URL)
    await until(lambda: loader.state(A_URL).status is CoverStatus.READY)

    assert loader.state(ANOTHER_URL).status is CoverStatus.PENDING
    assert loader.state(ANOTHER_URL).frame is None
    await loader.aclose()


async def test_failures_stay_failed_through_a_doubling_backoff_and_recover(
    caplog: pytest.LogCaptureFixture,
) -> None:
    truncated = A_PNG[: len(A_PNG) // 2]
    server = ImageServer(
        [
            httpx2.Response(404),
            unreachable,
            httpx2.Response(200, content=b"not an image"),
            httpx2.Response(200, content=truncated),
            httpx2.Response(503),
            httpx2.Response(500),
            a_cover(),
        ]
    )
    sleep = FakeSleep()
    loader = a_loader(server, sleep)

    with caplog.at_level(logging.INFO, logger="spotify_to_led.covers"):
        loader.want(A_URL)
        for attempt in range(1, 7):
            await until(lambda: len(sleep.durations) == attempt)
            # Parked in the backoff: the failure is what the display sees.
            assert loader.state(A_URL).status is CoverStatus.FAILED
            assert loader.state(A_URL).frame is None
            sleep.wake()
        await until(lambda: loader.state(A_URL).status is CoverStatus.READY)

    # Doubling from 5 s, capped at 60 s, as for the board connect.
    assert sleep.durations == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0]
    assert server.urls == [A_URL] * 7
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 6, "one warning per failed attempt"
    assert warnings[0].exc_info, "the first failure carries its traceback"
    assert not any(r.exc_info for r in warnings[1:]), "later failures are one line each"
    assert "HTTP 404" in warnings[0].getMessage()
    assert "ConnectError" in warnings[1].getMessage()
    assert all(A_URL in r.getMessage() for r in warnings)
    assert caplog.records[-1].getMessage() == f"cover ready: {A_URL}"
    await loader.aclose()


async def test_wanting_the_same_url_again_does_not_restart_the_download() -> None:
    server = ImageServer([])
    server.answers = [server.hang]
    loader = a_loader(server, FakeSleep())

    loader.want(A_URL)
    await until(lambda: len(server.requests) == 1)
    for _ in range(5):
        loader.want(A_URL)
        await asyncio.sleep(0)

    assert len(server.requests) == 1
    assert server.cancelled == []
    await loader.aclose()


async def test_wanting_the_same_url_again_does_not_restart_the_backoff() -> None:
    server = ImageServer([httpx2.Response(500), httpx2.Response(500)])
    sleep = FakeSleep()
    loader = a_loader(server, sleep)

    loader.want(A_URL)
    await until(lambda: len(sleep.durations) == 1)
    loader.want(A_URL)
    sleep.wake()
    await until(lambda: len(sleep.durations) == 2)

    assert sleep.durations == [5.0, 10.0]
    await loader.aclose()


async def test_switching_the_url_cancels_the_old_download() -> None:
    server = ImageServer([])
    server.answers = [server.hang, a_cover()]
    loader = a_loader(server, FakeSleep())

    loader.want(A_URL)
    await until(lambda: len(server.requests) == 1)
    loader.want(ANOTHER_URL)
    await until(lambda: loader.state(ANOTHER_URL).status is CoverStatus.READY)

    assert server.cancelled == [A_URL]
    assert server.urls == [A_URL, ANOTHER_URL]
    assert loader.state(A_URL).status is CoverStatus.PENDING
    await loader.aclose()


async def test_switching_the_url_during_a_backoff_cancels_the_retry() -> None:
    server = ImageServer([httpx2.Response(500), a_cover()])
    sleep = FakeSleep()
    loader = a_loader(server, sleep)

    loader.want(A_URL)
    await until(lambda: len(sleep.durations) == 1)
    loader.want(ANOTHER_URL)
    await until(lambda: loader.state(ANOTHER_URL).status is CoverStatus.READY)
    # Waking the old backoff now must not lead to another attempt at A.
    sleep.wake()
    for _ in range(20):
        await asyncio.sleep(0)

    assert server.urls == [A_URL, ANOTHER_URL]
    assert sleep.durations == [5.0]
    await loader.aclose()


async def test_wanting_nothing_drops_the_held_cover() -> None:
    server = ImageServer([a_cover()])
    loader = a_loader(server, FakeSleep())
    loader.want(A_URL)
    await until(lambda: loader.state(A_URL).status is CoverStatus.READY)

    loader.want(None)

    assert loader.state(A_URL).status is CoverStatus.PENDING
    await loader.aclose()


async def test_aclose_cancels_the_download_and_leaves_no_task_running() -> None:
    server = ImageServer([])
    server.answers = [server.hang]
    loader = a_loader(server, FakeSleep())
    loader.want(A_URL)
    await until(lambda: len(server.requests) == 1)

    await loader.aclose()
    await loader.aclose()

    assert server.cancelled == [A_URL]
    others = asyncio.all_tasks() - {asyncio.current_task()}
    assert others == set(), f"tasks left running: {others}"
    assert loader.state(A_URL).status is CoverStatus.PENDING


async def test_aclose_without_any_work_is_harmless() -> None:
    loader = a_loader(ImageServer([]), FakeSleep())

    await loader.aclose()


# --- frame cache ----------------------------------------------------------

# A real-shaped scdn URL: the ids above carry a hyphen, so they have no cache key.
A_CACHEABLE_URL = "https://i.scdn.co/image/ab67616d00004851aaaa"


def a_cached_loader(server: ImageServer, cache: FrameCache) -> CoverLoader:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(server))
    return CoverLoader(client, SIZE, cache=cache, sleep=FakeSleep())


async def test_a_cache_hit_is_ready_at_once_without_a_download(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cache = FrameCache(tmp_path, SIZE)
    expected = render_cover(Image.open(BytesIO(A_PNG)), SIZE)
    cache.store(A_CACHEABLE_URL, expected)
    server = ImageServer([])
    loader = a_cached_loader(server, cache)

    with caplog.at_level(logging.INFO, logger="spotify_to_led.covers"):
        loader.want(A_CACHEABLE_URL)

    # Before any await: the display sees it on the sample that wanted it.
    state = loader.state(A_CACHEABLE_URL)
    assert state.status is CoverStatus.READY
    assert state.frame is not None
    assert frame_to_pixel_colors(state.frame) == frame_to_pixel_colors(expected)
    assert [r.getMessage() for r in caplog.records] == [
        f"cover ready from cache: {A_CACHEABLE_URL}"
    ]
    await asyncio.sleep(0)
    assert server.requests == []
    await loader.aclose()


async def test_a_miss_downloads_once_and_a_restart_finds_it_in_the_cache(
    tmp_path: Path,
) -> None:
    cache = FrameCache(tmp_path, SIZE)
    server = ImageServer([a_cover()])
    loader = a_cached_loader(server, cache)

    loader.want(A_CACHEABLE_URL)
    await until(lambda: loader.state(A_CACHEABLE_URL).status is CoverStatus.READY)
    await loader.aclose()

    assert server.urls == [A_CACHEABLE_URL]
    assert (tmp_path / "32" / "ab67616d00004851aaaa.png").is_file()

    # A new loader over the same directory, as after a restart.
    restarted_server = ImageServer([])
    restarted = a_cached_loader(restarted_server, FrameCache(tmp_path, SIZE))
    restarted.want(A_CACHEABLE_URL)

    state = restarted.state(A_CACHEABLE_URL)
    assert state.status is CoverStatus.READY
    assert state.frame is not None
    expected = render_cover(Image.open(BytesIO(A_PNG)), SIZE)
    assert frame_to_pixel_colors(state.frame) == frame_to_pixel_colors(expected)
    assert restarted_server.requests == []
    await restarted.aclose()


def a_bomb_header() -> bytes:
    """A PNG that is only a header claiming 20000x20000 pixels, over Pillow's bomb limit."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", 20000, 20000, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IEND", b"")


@pytest.mark.parametrize(
    "bad_entry", [b"\x89PNG not really", a_bomb_header()], ids=["garbage", "bomb header"]
)
async def test_a_bad_cache_entry_is_downloaded_again_and_rewritten(
    tmp_path: Path, bad_entry: bytes
) -> None:
    entry = tmp_path / "32" / "ab67616d00004851aaaa.png"
    entry.parent.mkdir()
    entry.write_bytes(bad_entry)
    server = ImageServer([a_cover()])
    loader = a_cached_loader(server, FrameCache(tmp_path, SIZE))

    loader.want(A_CACHEABLE_URL)
    await until(lambda: loader.state(A_CACHEABLE_URL).status is CoverStatus.READY)
    await loader.aclose()

    assert server.urls == [A_CACHEABLE_URL]
    rewritten = FrameCache(tmp_path, SIZE).load(A_CACHEABLE_URL)
    assert rewritten is not None
    expected = render_cover(Image.open(BytesIO(A_PNG)), SIZE)
    assert frame_to_pixel_colors(rewritten) == frame_to_pixel_colors(expected)


async def test_a_url_without_a_cache_key_is_downloaded_every_time_it_is_wanted(
    tmp_path: Path,
) -> None:
    server = ImageServer([a_cover(), a_cover()])
    loader = a_cached_loader(server, FrameCache(tmp_path, SIZE))

    for _ in range(2):
        loader.want(A_URL)
        await until(lambda: loader.state(A_URL).status is CoverStatus.READY)
        loader.want(None)

    assert server.urls == [A_URL, A_URL]
    assert list(tmp_path.iterdir()) == []
    await loader.aclose()


# --- enhanced covers ------------------------------------------------------

# The plain URL is the small image the display wants; the source is the
# item's widest, which the enhancement is made from. Both are cacheable.
A_PLAIN_URL = A_CACHEABLE_URL
A_SOURCE_URL = "https://i.scdn.co/image/ab67616d0000b273aaaa"
B_PLAIN_URL = "https://i.scdn.co/image/ab67616d00004851bbbb"
B_SOURCE_URL = "https://i.scdn.co/image/ab67616d0000b273bbbb"
A_PLAIN_ENTRY = "ab67616d00004851aaaa.png"
AN_ENHANCED_ENTRY = "ab67616d00004851aaaa.ai.png"
OPENROUTER_URL = "https://openrouter.ai/api/v1/images"
AFTERNOON = datetime(2026, 11, 11, 15, 30, tzinfo=UTC)
NEXT_MIDNIGHT = datetime(2026, 11, 12, 0, 0, tzinfo=UTC)
GENERATED_COLOUR: tuple[int, int, int] = (250, 250, 0)


def _generated_png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (128, 128), GENERATED_COLOUR).save(buffer, format="PNG")
    return buffer.getvalue()


PLAIN_FRAME = render_cover(Image.open(BytesIO(A_PNG)), SIZE)
ENHANCED_FRAME = render_enhanced(Image.open(BytesIO(_generated_png())), SIZE)


def a_generation() -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "data": [{"b64_json": base64.b64encode(_generated_png()).decode("ascii")}],
            "usage": {"cost": 0.009},
        },
    )


def gated(response: httpx2.Response) -> tuple[asyncio.Event, Answer]:
    """An answer that waits for the returned event before it is given."""
    gate = asyncio.Event()

    async def answer(request: httpx2.Request) -> httpx2.Response:
        await gate.wait()
        return response

    return gate, answer


class Network:
    """Routes OpenRouter's requests to `openrouter`, and every other to `images`."""

    def __init__(self, images: ImageServer, openrouter: ImageServer) -> None:
        self.images = images
        self.openrouter = openrouter

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "openrouter.ai":
            return await self.openrouter(request)
        return await self.images(request)


class InstantSleep:
    """The enhancer's sleep: records each delay and returns at once."""

    def __init__(self) -> None:
        self.durations: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.durations.append(seconds)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class MemoryLedger:
    """Today's spend held in memory, standing in for a `SpendLedger` and its file."""

    def __init__(self, spent: float = 0.0) -> None:
        self.spent = spent

    def spent_today(self) -> float:
        return self.spent

    def record(self, cost: float) -> None:
        self.spent += cost


def an_enhancing_loader(
    images: ImageServer,
    openrouter: ImageServer,
    cache: FrameCache,
    *,
    clock: Clock | None = None,
    enhancer_sleep: InstantSleep | None = None,
    ledger: MemoryLedger | None = None,
    daily_limit: float = 1.0,
) -> tuple[CoverLoader, Enhancer]:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(Network(images, openrouter)))
    enhancer = Enhancer(
        client,
        "sk-or-v1-test-key",
        "a/model",
        SIZE,
        ledger=ledger or MemoryLedger(),
        daily_limit=lambda: daily_limit,
        clock=clock or Clock(AFTERNOON),
        sleep=enhancer_sleep or InstantSleep(),
    )
    loader = CoverLoader(client, SIZE, cache=cache, enhancer=enhancer, sleep=FakeSleep())
    return loader, enhancer


def entries(tmp_path: Path) -> list[str]:
    directory = tmp_path / str(SIZE)
    return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []


def colours(frame: Image.Image | None) -> list[tuple[int, int, int]]:
    assert frame is not None
    return frame_to_pixel_colors(frame)


async def test_an_enhanced_cache_hit_is_ready_at_once_with_no_request(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cache = FrameCache(tmp_path, SIZE)
    cache.store(A_PLAIN_URL, ENHANCED_FRAME, enhanced=True)
    images, openrouter = ImageServer([]), ImageServer([])
    loader, _ = an_enhancing_loader(images, openrouter, cache)

    with caplog.at_level(logging.INFO, logger="spotify_to_led.covers"):
        loader.want(A_PLAIN_URL, enhance_from=A_SOURCE_URL)

    state = loader.state(A_PLAIN_URL)
    assert state.status is CoverStatus.READY
    assert state.enhanced is True
    assert colours(state.frame) == colours(ENHANCED_FRAME)
    assert [r.getMessage() for r in caplog.records] == [
        f"enhanced cover ready from cache: {A_PLAIN_URL}"
    ]
    await asyncio.sleep(0)
    assert images.requests == []
    assert openrouter.requests == []
    await loader.aclose()


async def test_a_never_seen_cover_goes_pending_then_generating_then_ready_enhanced(
    tmp_path: Path,
) -> None:
    plain_gate, plain = gated(a_cover())
    generation_gate, generation = gated(a_generation())
    images = ImageServer([plain, a_cover()])
    openrouter = ImageServer([generation])
    loader, _ = an_enhancing_loader(images, openrouter, FrameCache(tmp_path, SIZE))

    loader.want(A_PLAIN_URL, enhance_from=A_SOURCE_URL)
    await until(lambda: len(images.requests) == 1)
    # The plain download first, keeping whatever the board shows.
    assert loader.state(A_PLAIN_URL).status is CoverStatus.PENDING

    plain_gate.set()
    await until(lambda: len(openrouter.requests) == 1)
    # The plain frame is in hand and kept, and carried, so the board shows it
    # with the generating marker.
    assert loader.state(A_PLAIN_URL).status is CoverStatus.GENERATING
    assert colours(loader.state(A_PLAIN_URL).frame) == colours(PLAIN_FRAME)
    assert loader.state(A_PLAIN_URL).enhanced is False
    assert entries(tmp_path) == [A_PLAIN_ENTRY]

    generation_gate.set()
    await until(lambda: loader.state(A_PLAIN_URL).status is CoverStatus.READY)

    state = loader.state(A_PLAIN_URL)
    assert state.enhanced is True
    assert colours(state.frame) == colours(ENHANCED_FRAME)
    assert images.urls == [A_PLAIN_URL, A_SOURCE_URL]
    # The widest image goes to OpenRouter as it was downloaded, typed by its content.
    [request] = openrouter.requests
    assert str(request.url) == OPENROUTER_URL
    sent = json.loads(request.content)["input_references"][0]["image_url"]["url"]
    assert sent == "data:image/png;base64," + base64.b64encode(A_PNG).decode("ascii")
    assert entries(tmp_path) == [AN_ENHANCED_ENTRY, A_PLAIN_ENTRY]
    stored = FrameCache(tmp_path, SIZE).load(A_PLAIN_URL, enhanced=True)
    assert colours(stored) == colours(ENHANCED_FRAME)
    await loader.aclose()


async def test_a_cover_known_plain_goes_straight_to_generating(tmp_path: Path) -> None:
    cache = FrameCache(tmp_path, SIZE)
    cache.store(A_PLAIN_URL, PLAIN_FRAME)
    images = ImageServer([a_cover()])
    openrouter = ImageServer([a_generation()])
    loader, _ = an_enhancing_loader(images, openrouter, cache)

    loader.want(A_PLAIN_URL, enhance_from=A_SOURCE_URL)

    # Before any await: the display shows the plain cover, with the generating
    # marker, on this very sample.
    state = loader.state(A_PLAIN_URL)
    assert (state.status, state.enhanced) == (CoverStatus.GENERATING, False)
    assert colours(state.frame) == colours(PLAIN_FRAME)
    await until(lambda: loader.state(A_PLAIN_URL).status is CoverStatus.READY)
    assert loader.state(A_PLAIN_URL).enhanced is True
    # Only the source: the plain frame came from the cache.
    assert images.urls == [A_SOURCE_URL]
    await loader.aclose()


async def test_after_a_restart_an_enhanced_cover_needs_no_request(tmp_path: Path) -> None:
    images = ImageServer([a_cover(), a_cover()])
    openrouter = ImageServer([a_generation()])
    loader, _ = an_enhancing_loader(images, openrouter, FrameCache(tmp_path, SIZE))
    loader.want(A_PLAIN_URL, enhance_from=A_SOURCE_URL)
    await until(lambda: loader.state(A_PLAIN_URL).enhanced)
    await loader.aclose()

    # A new loader and enhancer over the same directory, as after a restart.
    restarted_images, restarted_openrouter = ImageServer([]), ImageServer([])
    restarted, _ = an_enhancing_loader(
        restarted_images, restarted_openrouter, FrameCache(tmp_path, SIZE)
    )
    restarted.want(A_PLAIN_URL, enhance_from=A_SOURCE_URL)

    state = restarted.state(A_PLAIN_URL)
    assert (state.status, state.enhanced) == (CoverStatus.READY, True)
    assert colours(state.frame) == colours(ENHANCED_FRAME)
    await asyncio.sleep(0)
    assert restarted_images.requests == []
    assert restarted_openrouter.requests == []
    await restarted.aclose()


async def test_switching_off_while_generating_is_the_plain_cover_at_once(tmp_path: Path) -> None:
    images = ImageServer([a_cover(), a_cover()])
    openrouter = ImageServer([])
    openrouter.answers = [openrouter.hang]
    loader, _ = an_enhancing_loader(images, openrouter, FrameCache(tmp_path, SIZE))
    loader.want(A_PLAIN_URL, enhance_from=A_SOURCE_URL)
    await until(lambda: len(openrouter.requests) == 1)

    loader.want(A_PLAIN_URL)

    # Before any await, and with no download: the plain cover, now without
    # the generating marker.
    state = loader.state(A_PLAIN_URL)
    assert (state.status, state.enhanced) == (CoverStatus.READY, False)
    assert colours(state.frame) == colours(PLAIN_FRAME)
    await until(lambda: openrouter.cancelled == [OPENROUTER_URL])
    assert images.urls == [A_PLAIN_URL, A_SOURCE_URL]
    assert entries(tmp_path) == [A_PLAIN_ENTRY]
    await loader.aclose()


async def test_switching_off_and_on_again_takes_both_frames_from_memory_and_cache(
    tmp_path: Path,
) -> None:
    images = ImageServer([a_cover(), a_cover()])
    openrouter = ImageServer([a_generation()])
    loader, _ = an_enhancing_loader(images, openrouter, FrameCache(tmp_path, SIZE))
    loader.want(A_PLAIN_URL, enhance_from=A_SOURCE_URL)
    await until(lambda: loader.state(A_PLAIN_URL).enhanced)

    loader.want(A_PLAIN_URL)
    off = loader.state(A_PLAIN_URL)
    loader.want(A_PLAIN_URL, enhance_from=A_SOURCE_URL)
    on = loader.state(A_PLAIN_URL)

    assert (off.status, off.enhanced) == (CoverStatus.READY, False)
    assert colours(off.frame) == colours(PLAIN_FRAME)
    assert (on.status, on.enhanced) == (CoverStatus.READY, True)
    assert colours(on.frame) == colours(ENHANCED_FRAME)
    await asyncio.sleep(0)
    assert len(images.requests) == 2
    assert len(openrouter.requests) == 1
    await loader.aclose()


async def test_wanting_another_cover_cancels_the_generation_and_stores_nothing(
    tmp_path: Path,
) -> None:
    images = ImageServer([a_cover(), a_cover(), a_cover()])
    openrouter = ImageServer([])
    openrouter.answers = [openrouter.hang]
    loader, _ = an_enhancing_loader(images, openrouter, FrameCache(tmp_path, SIZE))
    loader.want(A_PLAIN_URL, enhance_from=A_SOURCE_URL)
    await until(lambda: len(openrouter.requests) == 1)

    loader.want(B_PLAIN_URL)
    await until(lambda: loader.state(B_PLAIN_URL).status is CoverStatus.READY)

    assert openrouter.cancelled == [OPENROUTER_URL]
    assert loader.state(A_PLAIN_URL).status is CoverStatus.PENDING
    assert AN_ENHANCED_ENTRY not in entries(tmp_path)
    await loader.aclose()


async def test_a_generation_that_finishes_after_its_cover_was_dropped_changes_nothing(
    tmp_path: Path,
) -> None:
    # As if cancellation of A's generation had been swallowed: it completes
    # while B is wanted, and must neither answer for B nor be stored.
    images = ImageServer([a_cover(), a_cover()])
    openrouter = ImageServer([a_generation()])
    loader, _ = an_enhancing_loader(images, openrouter, FrameCache(tmp_path, SIZE))
    loader.want(B_PLAIN_URL)
    await until(lambda: loader.state(B_PLAIN_URL).status is CoverStatus.READY)

    await loader._load(A_PLAIN_URL, A_SOURCE_URL, PLAIN_FRAME)

    assert len(openrouter.requests) == 1
    state = loader.state(B_PLAIN_URL)
    assert (state.status, state.enhanced) == (CoverStatus.READY, False)
    assert AN_ENHANCED_ENTRY not in entries(tmp_path)
    await loader.aclose()


async def test_three_retryable_failures_show_the_plain_cover_and_it_is_not_retried_until_forget(
    tmp_path: Path,
) -> None:
    images = ImageServer([a_cover(), a_cover(), a_cover()])
    openrouter = ImageServer([httpx2.Response(503)] * 3 + [a_generation()])
    sleep = InstantSleep()
    loader, enhancer = an_enhancing_loader(
        images, openrouter, FrameCache(tmp_path, SIZE), enhancer_sleep=sleep
    )

    loader.want(A_PLAIN_URL, enhance_from=A_SOURCE_URL)
    await until(lambda: loader.state(A_PLAIN_URL).status is CoverStatus.READY)

    state = loader.state(A_PLAIN_URL)
    assert state.enhanced is False
    assert colours(state.frame) == colours(PLAIN_FRAME)
    assert len(openrouter.requests) == 3
    assert sleep.durations == [5.0, 10.0]

    # Wanted again, as on the next sighting of the same album: plain at once,
    # with neither a source download nor a request.
    loader.want(None)
    loader.want(A_PLAIN_URL, enhance_from=A_SOURCE_URL)
    state = loader.state(A_PLAIN_URL)
    assert (state.status, state.enhanced) == (CoverStatus.READY, False)
    await asyncio.sleep(0)
    assert len(images.requests) == 2
    assert len(openrouter.requests) == 3

    # The switch flipped: tried again.
    enhancer.forget()
    loader.want(None)
    loader.want(A_PLAIN_URL, enhance_from=A_SOURCE_URL)
    assert loader.state(A_PLAIN_URL).status is CoverStatus.GENERATING
    await until(lambda: loader.state(A_PLAIN_URL).enhanced)
    assert len(openrouter.requests) == 4
    await loader.aclose()


async def test_a_rejected_generation_shows_the_plain_cover_with_no_retry(tmp_path: Path) -> None:
    images = ImageServer([a_cover(), a_cover()])
    openrouter = ImageServer([httpx2.Response(400, json={"error": {"message": "bad image"}})])
    sleep = InstantSleep()
    loader, _ = an_enhancing_loader(
        images, openrouter, FrameCache(tmp_path, SIZE), enhancer_sleep=sleep
    )

    loader.want(A_PLAIN_URL, enhance_from=A_SOURCE_URL)
    await until(lambda: loader.state(A_PLAIN_URL).status is CoverStatus.READY)

    assert loader.state(A_PLAIN_URL).enhanced is False
    assert colours(loader.state(A_PLAIN_URL).frame) == colours(PLAIN_FRAME)
    assert len(openrouter.requests) == 1
    assert sleep.durations == []
    assert entries(tmp_path) == [A_PLAIN_ENTRY]
    await loader.aclose()


@pytest.mark.parametrize("status", [402, 403])
async def test_a_credit_refusal_shows_plain_covers_with_no_request_until_midnight(
    tmp_path: Path, status: int
) -> None:
    images = ImageServer([a_cover(), a_cover(), a_cover(), a_cover()])
    openrouter = ImageServer([httpx2.Response(status), a_generation()])
    clock = Clock(AFTERNOON)
    loader, enhancer = an_enhancing_loader(
        images, openrouter, FrameCache(tmp_path, SIZE), clock=clock
    )

    loader.want(A_PLAIN_URL, enhance_from=A_SOURCE_URL)
    await until(lambda: loader.state(A_PLAIN_URL).status is CoverStatus.READY)
    assert loader.state(A_PLAIN_URL).enhanced is False
    assert enhancer.paused_until == NEXT_MIDNIGHT

    # Another album while paused: its plain cover, and no source, no request.
    loader.want(B_PLAIN_URL, enhance_from=B_SOURCE_URL)
    await until(lambda: loader.state(B_PLAIN_URL).status is CoverStatus.READY)
    assert loader.state(B_PLAIN_URL).enhanced is False
    assert images.urls == [A_PLAIN_URL, A_SOURCE_URL, B_PLAIN_URL]
    assert len(openrouter.requests) == 1

    # Past midnight, the next time it is wanted, it is generated.
    clock.now = NEXT_MIDNIGHT
    loader.want(None)
    loader.want(B_PLAIN_URL, enhance_from=B_SOURCE_URL)
    assert loader.state(B_PLAIN_URL).status is CoverStatus.GENERATING
    await until(lambda: loader.state(B_PLAIN_URL).enhanced)
    assert len(openrouter.requests) == 2
    await loader.aclose()


async def test_with_the_limit_reached_a_cover_is_plain_at_once_with_no_source_or_request(
    tmp_path: Path,
) -> None:
    images = ImageServer([a_cover()])
    openrouter = ImageServer([])
    ledger = MemoryLedger(1.0)
    loader, enhancer = an_enhancing_loader(
        images, openrouter, FrameCache(tmp_path, SIZE), ledger=ledger, daily_limit=1.0
    )
    assert enhancer.limit_reached

    loader.want(A_PLAIN_URL, enhance_from=A_SOURCE_URL)
    # Inside `want`'s task, straight to plain: no generating marker on the way.
    assert loader.state(A_PLAIN_URL).status is CoverStatus.PENDING
    await until(lambda: loader.state(A_PLAIN_URL).status is CoverStatus.READY)

    state = loader.state(A_PLAIN_URL)
    assert state.enhanced is False
    assert colours(state.frame) == colours(PLAIN_FRAME)
    assert images.urls == [A_PLAIN_URL]
    assert openrouter.requests == []

    # Known plain, wanted again: plain at once, still nothing downloaded or asked.
    loader.want(None)
    loader.want(A_PLAIN_URL, enhance_from=A_SOURCE_URL)
    assert (loader.state(A_PLAIN_URL).status, loader.state(A_PLAIN_URL).enhanced) == (
        CoverStatus.READY,
        False,
    )
    await asyncio.sleep(0)
    assert images.urls == [A_PLAIN_URL]
    assert openrouter.requests == []
    await loader.aclose()


async def test_with_the_limit_reached_a_cached_enhanced_cover_still_shows(
    tmp_path: Path,
) -> None:
    cache = FrameCache(tmp_path, SIZE)
    cache.store(A_PLAIN_URL, ENHANCED_FRAME, enhanced=True)
    images, openrouter = ImageServer([]), ImageServer([])
    loader, _ = an_enhancing_loader(
        images, openrouter, cache, ledger=MemoryLedger(5.0), daily_limit=1.0
    )

    loader.want(A_PLAIN_URL, enhance_from=A_SOURCE_URL)

    state = loader.state(A_PLAIN_URL)
    assert (state.status, state.enhanced) == (CoverStatus.READY, True)
    assert colours(state.frame) == colours(ENHANCED_FRAME)
    await asyncio.sleep(0)
    assert images.requests == []
    assert openrouter.requests == []
    await loader.aclose()


async def test_a_source_that_cannot_be_downloaded_shows_the_plain_cover(tmp_path: Path) -> None:
    images = ImageServer([a_cover(), httpx2.Response(404)])
    openrouter = ImageServer([])
    loader, _ = an_enhancing_loader(images, openrouter, FrameCache(tmp_path, SIZE))

    loader.want(A_PLAIN_URL, enhance_from=A_SOURCE_URL)
    await until(lambda: len(images.requests) == 2)
    await until(lambda: loader.state(A_PLAIN_URL).status is CoverStatus.READY)

    assert loader.state(A_PLAIN_URL).enhanced is False
    assert openrouter.requests == []
    await loader.aclose()


async def test_with_enhancement_off_no_request_reaches_openrouter(tmp_path: Path) -> None:
    images = ImageServer([a_cover(), a_cover()])
    openrouter = ImageServer([])
    loader, _ = an_enhancing_loader(images, openrouter, FrameCache(tmp_path, SIZE))

    for url in (A_PLAIN_URL, B_PLAIN_URL):
        loader.want(url)
        await until(lambda: loader.state(url).status is CoverStatus.READY)
        assert loader.state(url).enhanced is False

    assert images.urls == [A_PLAIN_URL, B_PLAIN_URL]
    assert openrouter.requests == []
    assert entries(tmp_path) == [A_PLAIN_ENTRY, "ab67616d00004851bbbb.png"]
    await loader.aclose()


async def test_without_an_enhancer_a_source_to_enhance_from_is_ignored(tmp_path: Path) -> None:
    server = ImageServer([a_cover()])
    loader = a_cached_loader(server, FrameCache(tmp_path, SIZE))

    loader.want(A_PLAIN_URL, enhance_from=A_SOURCE_URL)
    await until(lambda: loader.state(A_PLAIN_URL).status is CoverStatus.READY)

    assert loader.state(A_PLAIN_URL).enhanced is False
    assert server.urls == [A_PLAIN_URL]
    await loader.aclose()
