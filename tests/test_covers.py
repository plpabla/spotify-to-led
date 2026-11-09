"""Artwork selection, and the cover loader driven against a scripted image server.

Every download goes to an `httpx2.MockTransport` that answers from a script,
as in `test_spotify_reader.py`, so each attempt's outcome is chosen by the
test. The sleep is a fake that records each delay and then parks the loader
until the test lets it go on, which is what makes the `FAILED`-during-backoff
state observable. No real time passes and no network is used.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from io import BytesIO

import httpx2
import pytest
from PIL import Image

from spotify_to_led.board.transport import frame_to_pixel_colors, validate_frame
from spotify_to_led.covers import (
    CoverLoader,
    CoverStatus,
    select_artwork,
)
from spotify_to_led.rendering import render_cover
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
    """Let the loader's task run until `condition` holds."""
    for _ in range(200):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("the condition never held")


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
