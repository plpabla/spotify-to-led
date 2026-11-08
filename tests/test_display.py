"""The display rule and the display loop that keeps the board in step with it.

The loop runs against a fake transport and a fake sleep. The sleep records
each delay instead of waiting, and the scripted last one ends `run_display`
the way shutdown does, by raising `CancelledError`.
"""

import asyncio
import logging
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from PIL import Image

from spotify_to_led import frames
from spotify_to_led.board.transport import LinkState, frame_to_pixel_colors
from spotify_to_led.display import (
    Presentation,
    frame_for,
    run_display,
    select_presentation,
)
from spotify_to_led.spotify.playback import (
    INITIAL_SNAPSHOT,
    PlaybackSnapshot,
    PlaybackStatus,
    ReadFailure,
)

SIZE = 32
NOTHING = replace(INITIAL_SNAPSHOT, status=PlaybackStatus.NOTHING_PLAYING)
PLAYING = replace(INITIAL_SNAPSHOT, status=PlaybackStatus.PLAYING)


class FakeBoard:
    """A `BoardTransport` whose first connects and pushes can be made to fail."""

    def __init__(self, *, failing_connects: int = 0, failing_pushes: int = 0) -> None:
        self.failing_connects = failing_connects
        self.failing_pushes = failing_pushes
        self.connects = 0
        # Every push attempted, including the ones that raised.
        self.pushed: list[Presentation] = []
        self._link_state = LinkState.DOWN

    @property
    def link_state(self) -> LinkState:
        return self._link_state

    async def connect(self) -> None:
        self.connects += 1
        if self.connects <= self.failing_connects:
            raise OSError("the board is not reachable")
        self._link_state = LinkState.UP

    async def push_frame(self, frame: Image.Image) -> None:
        self.pushed.append(_presentation_of(frame))
        if len(self.pushed) <= self.failing_pushes:
            raise OSError("the write was not acknowledged")

    async def close(self) -> None:
        self._link_state = LinkState.DOWN


def _presentation_of(frame: Image.Image) -> Presentation:
    pixels = frame_to_pixel_colors(frame)
    for presentation in Presentation:
        if pixels == frame_to_pixel_colors(frame_for(presentation, SIZE)):
            return presentation
    raise AssertionError("pushed a frame that is no presentation")


class FakeSleep:
    """Records each delay instead of waiting; the `sleeps`-th one ends the loop."""

    def __init__(self, sleeps: int) -> None:
        self.sleeps = sleeps
        self.durations: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.durations.append(seconds)
        if len(self.durations) >= self.sleeps:
            raise asyncio.CancelledError


def scripted(*snapshots: PlaybackSnapshot | None) -> Callable[[], PlaybackSnapshot | None]:
    """A snapshot source answering one sample at a time, in order."""
    answers: Iterator[PlaybackSnapshot | None] = iter(snapshots)
    return lambda: next(answers)


async def run_until_out_of_sleeps(
    board: FakeBoard, current: Callable[[], PlaybackSnapshot | None], sleep: FakeSleep
) -> None:
    with pytest.raises(asyncio.CancelledError):
        await run_display(board, SIZE, current, sleep=sleep)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (PlaybackStatus.UNKNOWN, Presentation.IDLE),
        (PlaybackStatus.PLAYING, Presentation.PLAYING),
        (PlaybackStatus.PAUSED, Presentation.PLAYING),
        (PlaybackStatus.NOTHING_PLAYING, Presentation.IDLE),
    ],
)
def test_each_status_selects_its_presentation(
    status: PlaybackStatus, expected: Presentation
) -> None:
    snapshot = replace(INITIAL_SNAPSHOT, status=status)

    assert select_presentation(snapshot) is expected


def test_no_reader_selects_idle() -> None:
    assert select_presentation(None) is Presentation.IDLE


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (PlaybackStatus.NOTHING_PLAYING, Presentation.IDLE),
        (PlaybackStatus.PLAYING, Presentation.PLAYING),
    ],
)
def test_a_read_failure_does_not_change_the_presentation(
    status: PlaybackStatus, expected: Presentation
) -> None:
    # Ignored on purpose until S-06 brings the staleness marker.
    snapshot = replace(
        INITIAL_SNAPSHOT,
        status=status,
        failure=ReadFailure.NETWORK,
        failure_since=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert select_presentation(snapshot) is expected


def test_frame_for_idle_is_the_idle_frame() -> None:
    frame = frame_for(Presentation.IDLE, SIZE)

    assert frame_to_pixel_colors(frame) == frame_to_pixel_colors(frames.idle(SIZE))


def test_frame_for_playing_is_the_placeholder() -> None:
    frame = frame_for(Presentation.PLAYING, SIZE)

    assert frame_to_pixel_colors(frame) == frame_to_pixel_colors(
        frames.playing_placeholder(SIZE)
    )


async def test_a_board_that_fails_to_connect_is_retried_with_backoff_until_it_shows_idle() -> None:
    board = FakeBoard(failing_connects=6)
    sleep = FakeSleep(sleeps=7)

    await run_until_out_of_sleeps(board, lambda: None, sleep)

    assert board.connects == 7
    # Doubling from 5 s, capped at 60 s, then one sample interval.
    assert sleep.durations == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0, 1.0]
    assert board.link_state is LinkState.UP
    assert board.pushed == [Presentation.IDLE]


async def test_only_the_first_failed_connect_logs_a_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    board = FakeBoard(failing_connects=3)

    with caplog.at_level(logging.INFO, logger="spotify_to_led.display"):
        await run_until_out_of_sleeps(board, lambda: None, FakeSleep(sleeps=4))

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 3, "one warning per failed attempt"
    assert warnings[0].exc_info, "the first failure must carry its traceback"
    assert not any(r.exc_info for r in warnings[1:]), "later failures are one line each"
    # Without a traceback the line itself still names the cause and the wait.
    assert "OSError" in warnings[1].getMessage()
    assert "10.0" in warnings[1].getMessage()
    assert any(r.getMessage() == "board connected" for r in caplog.records)


async def test_an_unchanged_presentation_is_not_pushed_again() -> None:
    board = FakeBoard()
    sleep = FakeSleep(sleeps=10)

    await run_until_out_of_sleeps(board, scripted(*[PLAYING] * 10), sleep)

    assert board.pushed == [Presentation.PLAYING]
    assert sleep.durations == [1.0] * 10


async def test_playback_starting_and_stopping_pushes_idle_then_placeholder_then_idle(
    caplog: pytest.LogCaptureFixture,
) -> None:
    board = FakeBoard()
    current = scripted(NOTHING, NOTHING, PLAYING, PLAYING, NOTHING, NOTHING)

    with caplog.at_level(logging.INFO, logger="spotify_to_led.display"):
        await run_until_out_of_sleeps(board, current, FakeSleep(sleeps=6))

    assert board.pushed == [Presentation.IDLE, Presentation.PLAYING, Presentation.IDLE]
    changes = [r for r in caplog.records if "presentation" in r.getMessage()]
    assert len(changes) == 3, "every presentation change is logged"


async def test_a_failed_push_is_logged_and_not_retried_until_the_presentation_changes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    board = FakeBoard(failing_pushes=1)
    current = scripted(NOTHING, NOTHING, NOTHING, PLAYING)

    with caplog.at_level(logging.INFO, logger="spotify_to_led.display"):
        await run_until_out_of_sleeps(board, current, FakeSleep(sleeps=4))

    # The failed idle push is not repeated; the change to playing still draws.
    assert board.pushed == [Presentation.IDLE, Presentation.PLAYING]
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert errors[0].exc_info is not None, "the traceback must reach the log"


async def test_an_unexpected_error_in_a_sample_is_logged_and_the_loop_keeps_going(
    caplog: pytest.LogCaptureFixture,
) -> None:
    answers: Iterator[Exception | PlaybackSnapshot] = iter([RuntimeError("a bug"), PLAYING])

    def current() -> PlaybackSnapshot | None:
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    board = FakeBoard()
    with caplog.at_level(logging.ERROR, logger="spotify_to_led.display"):
        await run_until_out_of_sleeps(board, current, FakeSleep(sleeps=2))

    assert board.pushed == [Presentation.PLAYING]
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert errors[0].exc_info is not None


async def test_cancelling_during_a_connect_retry_ends_the_task_cleanly() -> None:
    board = FakeBoard(failing_connects=1_000)
    # The real sleep, so the task is genuinely parked in the 5 s retry wait.
    task = asyncio.create_task(run_display(board, SIZE, lambda: None))
    for _ in range(20):
        await asyncio.sleep(0)
    assert board.connects == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled()
    assert board.pushed == []
