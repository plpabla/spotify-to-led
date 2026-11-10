"""The display rule and the display loop that keeps the board in step with it.

The loop runs against a fake transport, a fake cover loader and a fake sleep.
The board records every frame pushed, and each is named by matching it
against the images the service should draw. The loader answers from states
the test sets, so no download happens. The sleep records each delay instead
of waiting, and the scripted last one ends `run_display` the way shutdown
does, by raising `CancelledError`. The clock stands still at `NOW`; how long
reads have been failing is set through each snapshot's `failure_since`.
"""

import asyncio
import logging
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from spotify_to_led import frames
from spotify_to_led.board.transport import LinkState
from spotify_to_led.covers import PENDING, CoverState, CoverStatus
from spotify_to_led.display import (
    IDLE,
    NO_ARTWORK,
    PLACEHOLDER,
    Presentation,
    PresentationKind,
    run_display,
    select_presentation,
    wanted_cover_url,
)
from spotify_to_led.spotify.playback import (
    INITIAL_SNAPSHOT,
    Artwork,
    PlaybackSnapshot,
    PlaybackStatus,
    PlayingItem,
    ReadFailure,
)

SIZE = 32
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
A_URL = "https://i.scdn.co/image/cover-a"
B_URL = "https://i.scdn.co/image/cover-b"
NOTHING = replace(INITIAL_SNAPSHOT, status=PlaybackStatus.NOTHING_PLAYING)

COVER_A = Image.new("RGB", (SIZE, SIZE), (200, 40, 90))
COVER_B = Image.new("RGB", (SIZE, SIZE), (30, 90, 200))
READY_A = CoverState(CoverStatus.READY, COVER_A)
READY_B = CoverState(CoverStatus.READY, COVER_B)
FAILED = CoverState(CoverStatus.FAILED)

# Every image the service may push in these tests, by the name assertions use.
DRAWN: dict[str, Image.Image] = {
    "idle": frames.idle(SIZE),
    "placeholder": frames.playing_placeholder(SIZE),
    "no artwork": frames.no_artwork(SIZE),
    "cover a": COVER_A,
    "cover b": COVER_B,
    "stale cover a": frames.with_stale_marker(COVER_A),
    "stale idle": frames.with_stale_marker(frames.idle(SIZE)),
}


def item(url: str | None) -> PlayingItem:
    """A track whose only image is at `url`, or one with no artwork at all."""
    artwork = (Artwork(url=url, width=64, height=64),) if url is not None else ()
    return PlayingItem(kind="track", id=url, name="A Track", subtitle=None, artwork=artwork)


def playing(
    url: str | None,
    *,
    status: PlaybackStatus = PlaybackStatus.PLAYING,
    failing_for: float | None = None,
) -> PlaybackSnapshot:
    """`url`'s item playing; with `failing_for`, reads failing for that many seconds."""
    snapshot = replace(INITIAL_SNAPSHOT, status=status, item=item(url))
    if failing_for is None:
        return snapshot
    return replace(
        snapshot,
        failure=ReadFailure.NETWORK,
        failure_since=NOW - timedelta(seconds=failing_for),
    )


# An item with artwork playing; the loop tests that use it run without a
# loader, so it shows the placeholder.
PLAYING = playing(A_URL)


def failing(snapshot: PlaybackSnapshot, seconds: float) -> PlaybackSnapshot:
    """`snapshot` with reads failing for `seconds`."""
    return replace(
        snapshot, failure=ReadFailure.NETWORK, failure_since=NOW - timedelta(seconds=seconds)
    )


def cover(url: str, stale: bool = False) -> Presentation:
    return Presentation(PresentationKind.COVER, url, stale)


def marked(presentation: Presentation) -> Presentation:
    return replace(presentation, stale=True)


class FakeBoard:
    """A `BoardTransport` whose first connects and pushes can be made to fail."""

    def __init__(self, *, failing_connects: int = 0, failing_pushes: int = 0) -> None:
        self.failing_connects = failing_connects
        self.failing_pushes = failing_pushes
        self.failing_reconnects = 0
        self.connects = 0
        self.connections = 0
        # Every frame pushed, including the ones whose push raised.
        self.pushed: list[Image.Image] = []
        self._link_state = LinkState.DOWN

    @property
    def link_state(self) -> LinkState:
        return self._link_state

    @property
    def drawn(self) -> list[str]:
        """The name in `DRAWN` of every frame pushed, in order."""
        return [_name_of(frame) for frame in self.pushed]

    async def connect(self) -> None:
        self.connects += 1
        if self.connects <= self.failing_connects:
            raise OSError("the board is not reachable")
        if self.failing_reconnects:
            self.failing_reconnects -= 1
            raise OSError("the board is not reachable")
        self._link_state = LinkState.UP
        self.connections += 1

    def reconnect(self) -> None:
        """Bring the link up again without it having been seen down."""
        self.connections += 1

    def drop(self, *, failing_reconnects: int = 0) -> None:
        """Lose the link; the next `failing_reconnects` connects then fail."""
        self._link_state = LinkState.DOWN
        self.failing_reconnects = failing_reconnects

    async def push_frame(self, frame: Image.Image) -> None:
        self.pushed.append(frame)
        if len(self.pushed) <= self.failing_pushes:
            raise OSError("the write was not acknowledged")

    async def close(self) -> None:
        self._link_state = LinkState.DOWN


def _name_of(frame: Image.Image) -> str:
    for name, expected in DRAWN.items():
        if frame.size == expected.size and frame.tobytes() == expected.tobytes():
            return name
    raise AssertionError("pushed a frame the service should never draw")


class FakeCovers:
    """A `CoverLoader` answering from `states`, which a test changes between samples."""

    def __init__(self, states: dict[str, CoverState] | None = None) -> None:
        self.states = dict(states or {})
        # Every `want` call, one per sample.
        self.wanted: list[str | None] = []

    def want(self, url: str | None) -> None:
        self.wanted.append(url)

    def state(self, url: str) -> CoverState:
        return self.states.get(url, PENDING)


class FakeSleep:
    """Records each delay instead of waiting; the `sleeps`-th one ends the loop."""

    def __init__(self, sleeps: int) -> None:
        self.sleeps = sleeps
        self.durations: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.durations.append(seconds)
        if len(self.durations) >= self.sleeps:
            raise asyncio.CancelledError


class ActingSleep(FakeSleep):
    """A `FakeSleep` that runs `actions[n]` at its `n`-th sleep, before the next sample."""

    def __init__(self, sleeps: int, actions: dict[int, Callable[[], None]]) -> None:
        super().__init__(sleeps)
        self.actions = actions

    async def __call__(self, seconds: float) -> None:
        action = self.actions.get(len(self.durations) + 1)
        if action is not None:
            action()
        await super().__call__(seconds)


def scripted(*snapshots: PlaybackSnapshot | None) -> Callable[[], PlaybackSnapshot | None]:
    """A snapshot source answering one sample at a time, in order."""
    answers: Iterator[PlaybackSnapshot | None] = iter(snapshots)
    return lambda: next(answers)


async def run_until_out_of_sleeps(
    board: FakeBoard,
    current: Callable[[], PlaybackSnapshot | None],
    sleep: FakeSleep,
    covers: FakeCovers | None = None,
) -> None:
    with pytest.raises(asyncio.CancelledError):
        await run_display(board, SIZE, current, covers=covers, clock=lambda: NOW, sleep=sleep)


# --- the rule -------------------------------------------------------------


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        # A reader that has neither failed nor read yet has nothing to be stale about.
        INITIAL_SNAPSHOT,
        NOTHING,
    ],
)
def test_no_reader_unknown_and_nothing_playing_select_idle(
    snapshot: PlaybackSnapshot | None,
) -> None:
    assert wanted_cover_url(snapshot, SIZE) is None
    assert select_presentation(snapshot, None, cover(A_URL), NOW) == IDLE


@pytest.mark.parametrize("previous", [marked(IDLE), None])
def test_no_reader_is_plain_idle_however_long_it_has_been(previous: Presentation | None) -> None:
    # Spotify not configured is a setup mode, not blindness.
    assert select_presentation(None, None, previous, NOW + timedelta(days=30)) == IDLE


@pytest.mark.parametrize("snapshot", [INITIAL_SNAPSHOT, NOTHING])
@pytest.mark.parametrize(("failing_for", "stale"), [(29, False), (31, True)])
def test_idle_is_stale_only_after_more_than_30_s_of_failing_reads(
    snapshot: PlaybackSnapshot, failing_for: float, stale: bool
) -> None:
    # Blind, idle no longer knows that nothing plays (OQ2).
    chosen = select_presentation(failing(snapshot, failing_for), None, None, NOW)

    assert chosen == replace(IDLE, stale=stale)


def test_nothing_playing_wants_no_cover_even_with_an_item() -> None:
    snapshot = replace(playing(A_URL), status=PlaybackStatus.NOTHING_PLAYING)

    assert wanted_cover_url(snapshot, SIZE) is None


@pytest.mark.parametrize("previous", [cover(A_URL), PLACEHOLDER, IDLE, NO_ARTWORK, None])
@pytest.mark.parametrize("status", [PlaybackStatus.PLAYING, PlaybackStatus.PAUSED])
def test_an_item_without_artwork_wants_no_cover_and_selects_the_symbol(
    status: PlaybackStatus, previous: Presentation | None
) -> None:
    # No cover is coming, so there is no changeover to keep the previous one for.
    snapshot = playing(None, status=status)
    no_item = replace(snapshot, item=None)

    assert wanted_cover_url(snapshot, SIZE) is None
    assert wanted_cover_url(no_item, SIZE) is None
    assert select_presentation(snapshot, None, previous, NOW) == NO_ARTWORK
    assert select_presentation(no_item, None, previous, NOW) == NO_ARTWORK


def test_an_ad_selects_the_symbol_rather_than_the_placeholder() -> None:
    # `cover` is `None` both here and without a loader; the item tells them apart.
    ad = PlayingItem(kind="ad", id=None, name=None, subtitle=None, artwork=())
    snapshot = replace(playing(None), item=ad)

    assert select_presentation(snapshot, None, None, NOW) == NO_ARTWORK


def test_without_a_loader_an_item_with_artwork_selects_the_placeholder() -> None:
    assert select_presentation(playing(A_URL), None, None, NOW) == PLACEHOLDER


@pytest.mark.parametrize("status", [PlaybackStatus.PLAYING, PlaybackStatus.PAUSED])
def test_a_ready_cover_is_selected_and_a_paused_item_keeps_it(status: PlaybackStatus) -> None:
    snapshot = playing(A_URL, status=status)

    assert wanted_cover_url(snapshot, SIZE) == A_URL
    assert select_presentation(snapshot, (A_URL, READY_A), PLACEHOLDER, NOW) == cover(A_URL)


@pytest.mark.parametrize(
    ("previous", "expected"),
    [
        # The changeover: the old cover stays until the new one is ready.
        (cover(A_URL), cover(A_URL)),
        (PLACEHOLDER, PLACEHOLDER),
        (IDLE, IDLE),
        # Nothing chosen yet.
        (None, PLACEHOLDER),
    ],
)
def test_a_pending_cover_keeps_the_previous_presentation(
    previous: Presentation | None, expected: Presentation
) -> None:
    assert select_presentation(playing(B_URL), (B_URL, PENDING), previous, NOW) == expected


@pytest.mark.parametrize("previous", [cover(A_URL), PLACEHOLDER, None])
def test_a_failed_cover_selects_the_placeholder(previous: Presentation | None) -> None:
    assert select_presentation(playing(B_URL), (B_URL, FAILED), previous, NOW) == PLACEHOLDER


@pytest.mark.parametrize(("failing_for", "stale"), [(None, False), (29, False), (31, True)])
def test_a_cover_is_stale_only_after_more_than_30_s_of_failing_reads(
    failing_for: float | None, stale: bool
) -> None:
    snapshot = playing(A_URL, failing_for=failing_for)

    assert select_presentation(snapshot, (A_URL, READY_A), None, NOW) == cover(A_URL, stale)


@pytest.mark.parametrize(("read_ago", "stale"), [(29, False), (31, True)])
def test_a_cover_is_stale_when_reads_stop_without_failing(read_ago: float, stale: bool) -> None:
    # A reader that has stopped never records a failure; only the age of its
    # last good read can tell.
    snapshot = replace(playing(A_URL), last_read_at=NOW - timedelta(seconds=read_ago))

    assert select_presentation(snapshot, (A_URL, READY_A), None, NOW) == cover(A_URL, stale)


@pytest.mark.parametrize("previous", [cover(A_URL), IDLE, PLACEHOLDER, NO_ARTWORK])
@pytest.mark.parametrize(("failing_for", "stale"), [(29, False), (31, True)])
def test_a_presentation_kept_through_a_changeover_is_marked_by_the_reads_now(
    previous: Presentation, failing_for: float, stale: bool
) -> None:
    # Playback starting from idle while blind keeps idle, and marks it.
    snapshot = playing(B_URL, failing_for=failing_for)

    chosen = select_presentation(snapshot, (B_URL, PENDING), previous, NOW)

    assert chosen == replace(previous, stale=stale)


@pytest.mark.parametrize("previous", [cover(A_URL), IDLE, PLACEHOLDER, NO_ARTWORK])
def test_the_marker_clears_when_reads_recover(previous: Presentation) -> None:
    # A kept presentation is judged afresh, never carried over marked.
    kept = select_presentation(playing(B_URL), (B_URL, PENDING), marked(previous), NOW)
    ready = select_presentation(playing(A_URL), (A_URL, READY_A), marked(previous), NOW)

    assert kept == previous
    assert ready == cover(A_URL)


@pytest.mark.parametrize(
    "cover_state",
    [
        pytest.param((A_URL, FAILED), id="failed download"),
        pytest.param(None, id="no loader"),
    ],
)
@pytest.mark.parametrize(("failing_for", "stale"), [(29, False), (31, True)])
def test_the_placeholder_is_stale_only_after_more_than_30_s_of_failing_reads(
    cover_state: tuple[str, CoverState] | None, failing_for: float, stale: bool
) -> None:
    snapshot = playing(A_URL, failing_for=failing_for)

    chosen = select_presentation(snapshot, cover_state, None, NOW)

    assert chosen == replace(PLACEHOLDER, stale=stale)


@pytest.mark.parametrize("how", ["failing", "stopped"])
@pytest.mark.parametrize(("seconds", "stale"), [(29, False), (31, True)])
def test_the_symbol_is_stale_only_after_more_than_30_s_without_a_read(
    how: str, seconds: float, stale: bool
) -> None:
    # An ad may long have ended by the time a blind service is still showing its disc.
    if how == "failing":
        snapshot = playing(None, failing_for=seconds)
    else:
        snapshot = replace(playing(None), last_read_at=NOW - timedelta(seconds=seconds))

    chosen = select_presentation(snapshot, None, cover(A_URL, stale=True), NOW)

    assert chosen == replace(NO_ARTWORK, stale=stale)


def test_presentations_read_plainly_in_the_log() -> None:
    assert str(IDLE) == "idle"
    assert str(PLACEHOLDER) == "placeholder"
    assert str(NO_ARTWORK) == "no_artwork"
    assert str(cover(A_URL)) == f"cover {A_URL}"


def test_every_stale_presentation_says_so_in_the_log() -> None:
    assert str(marked(IDLE)) == "idle (stale)"
    assert str(marked(PLACEHOLDER)) == "placeholder (stale)"
    assert str(marked(NO_ARTWORK)) == "no_artwork (stale)"
    assert str(cover(A_URL, stale=True)) == f"cover {A_URL} (stale)"


# --- the loop: connecting and redrawing -----------------------------------


async def test_a_board_that_fails_to_connect_is_retried_with_backoff_until_it_shows_idle() -> None:
    board = FakeBoard(failing_connects=6)
    sleep = FakeSleep(sleeps=7)

    await run_until_out_of_sleeps(board, lambda: None, sleep)

    assert board.connects == 7
    # Doubling from 5 s, capped at 60 s, then one sample interval.
    assert sleep.durations == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0, 1.0]
    assert board.link_state is LinkState.UP
    assert board.drawn == ["idle"]


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

    assert board.drawn == ["placeholder"]
    assert sleep.durations == [1.0] * 10


class ReconnectingSleep(FakeSleep):
    """A `FakeSleep` that brings the link up again at the given sleeps."""

    def __init__(self, board: FakeBoard, sleeps: int, reconnect_at: set[int]) -> None:
        super().__init__(sleeps)
        self.board = board
        self.reconnect_at = reconnect_at

    async def __call__(self, seconds: float) -> None:
        if len(self.durations) + 1 in self.reconnect_at:
            self.board.reconnect()
        await super().__call__(seconds)


async def test_a_reconnect_redraws_the_unchanged_presentation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A board switched off and on comes back blank, and nothing but this loop
    # redraws it; seen on the Pi after S-02's first rollout.
    board = FakeBoard()
    sleep = ReconnectingSleep(board, sleeps=6, reconnect_at={3})

    with caplog.at_level(logging.INFO):
        await run_until_out_of_sleeps(board, scripted(*[NOTHING] * 6), sleep)

    assert board.drawn == ["idle", "idle"]
    assert "board link came up again; redrawing idle" in caplog.text


async def test_a_push_that_failed_is_drawn_again_after_the_next_reconnect() -> None:
    # The first push fails; its retry lands on the next sample, and the
    # reconnect after that still draws again.
    board = FakeBoard(failing_pushes=1)
    sleep = ReconnectingSleep(board, sleeps=5, reconnect_at={2})

    await run_until_out_of_sleeps(board, scripted(*[NOTHING] * 5), sleep)

    assert board.drawn == ["idle", "idle", "idle"]


class DroppingSleep(FakeSleep):
    """A `FakeSleep` that drops the link at the `drop_at`-th sleep."""

    def __init__(
        self, board: FakeBoard, sleeps: int, drop_at: int, failing_reconnects: int
    ) -> None:
        super().__init__(sleeps)
        self.board = board
        self.drop_at = drop_at
        self.failing_reconnects = failing_reconnects

    async def __call__(self, seconds: float) -> None:
        if len(self.durations) + 1 == self.drop_at:
            self.board.drop(failing_reconnects=self.failing_reconnects)
        await super().__call__(seconds)


async def test_a_dropped_link_is_reconnected_with_a_fresh_backoff_and_redrawn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The cold boot already climbed the backoff to 40 s; the outage after the
    # drop starts over at 5 s.
    board = FakeBoard(failing_connects=4)
    sleep = DroppingSleep(board, sleeps=10, drop_at=6, failing_reconnects=3)

    with caplog.at_level(logging.INFO, logger="spotify_to_led.display"):
        await run_until_out_of_sleeps(board, scripted(*[NOTHING] * 10), sleep)

    assert sleep.durations == [5.0, 10.0, 20.0, 40.0, 1.0, 1.0, 5.0, 10.0, 20.0, 1.0]
    assert board.connects == 9
    assert board.link_state is LinkState.UP
    assert board.drawn == ["idle", "idle"]
    assert "board link lost; reconnecting" in caplog.text
    assert "board link came up again; redrawing idle" in caplog.text


async def test_a_presentation_that_changed_while_the_link_was_down_is_drawn_on_reconnect() -> None:
    board = FakeBoard()
    sleep = DroppingSleep(board, sleeps=3, drop_at=1, failing_reconnects=1)

    await run_until_out_of_sleeps(board, scripted(NOTHING, PLAYING, PLAYING), sleep)

    assert sleep.durations == [1.0, 5.0, 1.0]
    assert board.drawn == ["idle", "placeholder"]


async def test_playback_starting_and_stopping_pushes_idle_then_placeholder_then_idle(
    caplog: pytest.LogCaptureFixture,
) -> None:
    board = FakeBoard()
    current = scripted(NOTHING, NOTHING, PLAYING, PLAYING, NOTHING, NOTHING)

    with caplog.at_level(logging.INFO, logger="spotify_to_led.display"):
        await run_until_out_of_sleeps(board, current, FakeSleep(sleeps=6))

    assert board.drawn == ["idle", "placeholder", "idle"]
    changes = [r for r in caplog.records if "presentation" in r.getMessage()]
    assert len(changes) == 3, "every presentation change is logged"


async def test_a_failed_push_is_retried_with_a_doubling_delay_until_it_lands(
    caplog: pytest.LogCaptureFixture,
) -> None:
    board = FakeBoard(failing_pushes=2)

    with caplog.at_level(logging.INFO, logger="spotify_to_led.display"):
        await run_until_out_of_sleeps(board, scripted(*[NOTHING] * 6), FakeSleep(sleeps=6))

    # Fails at the first sample, again 1 s later, and lands 2 s after that;
    # nothing is pushed once it has.
    assert board.drawn == ["idle", "idle", "idle"]
    warnings = [r for r in caplog.records if "pushing the frame failed" in r.getMessage()]
    assert [w.getMessage().rsplit(" ", 2)[1] for w in warnings] == ["1.0", "2.0"]
    assert warnings[0].exc_info is not None, "the traceback must reach the log"
    assert not warnings[1].exc_info, "one traceback per presentation"


async def test_a_new_presentation_is_pushed_at_once_even_while_a_retry_waits() -> None:
    board = FakeBoard(failing_pushes=1)
    current = scripted(NOTHING, PLAYING, PLAYING)

    await run_until_out_of_sleeps(board, current, FakeSleep(sleeps=3))

    assert board.drawn == ["idle", "placeholder"]


async def test_a_stale_marker_whose_push_failed_is_retried_rather_than_left_unmarked() -> None:
    # The cover is drawn, then reads fail for over 30 s and the marked push
    # fails; the board must not be left with the unmarked cover.
    board = FakeBoard()
    covers = FakeCovers({A_URL: READY_A})
    sleep = ActingSleep(sleeps=4, actions={1: lambda: setattr(board, "failing_pushes", 2)})
    current = scripted(playing(A_URL), *[playing(A_URL, failing_for=31)] * 3)

    await run_until_out_of_sleeps(board, current, sleep, covers)

    assert board.drawn == ["cover a", "stale cover a", "stale cover a"]


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

    assert board.drawn == ["placeholder"]
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


class HangingConnectBoard(FakeBoard):
    """A board whose connect never finishes, like one still being scanned for."""

    def __init__(self) -> None:
        super().__init__()
        self.connecting = asyncio.Event()

    async def connect(self) -> None:
        self.connects += 1
        self.connecting.set()
        await asyncio.Event().wait()


async def test_cancelling_while_a_connect_is_in_flight_ends_the_task_without_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The pm2 stop that lands mid-scan: the cancellation must leave the loop,
    # not be caught as a failed connect and retried.
    board = HangingConnectBoard()
    task = asyncio.create_task(run_display(board, SIZE, lambda: None))
    await asyncio.wait_for(board.connecting.wait(), timeout=2)

    with caplog.at_level(logging.WARNING):
        task.cancel()
        # `asyncio.wait` neither cancels nor raises, so a loop that swallowed
        # the cancellation fails here instead of hanging the suite.
        await asyncio.wait({task}, timeout=2)

    assert task.cancelled(), "the cancellation was swallowed and the loop went on"
    assert board.connects == 1
    assert caplog.records == []


# --- the loop: covers -----------------------------------------------------


async def test_each_item_change_pushes_its_cover_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    board = FakeBoard()
    covers = FakeCovers({A_URL: READY_A, B_URL: READY_B})
    current = scripted(*[playing(A_URL)] * 3, *[playing(B_URL)] * 3)

    with caplog.at_level(logging.INFO, logger="spotify_to_led.display"):
        await run_until_out_of_sleeps(board, current, FakeSleep(sleeps=6), covers)

    assert board.drawn == ["cover a", "cover b"]
    # Told on every sample; the loader treats a repeat as a no-op.
    assert covers.wanted == [A_URL] * 3 + [B_URL] * 3
    assert f"board presentation: cover {B_URL}" in caplog.text


async def test_nothing_is_pushed_while_the_next_cover_downloads() -> None:
    board = FakeBoard()
    covers = FakeCovers({A_URL: READY_A})
    current = scripted(playing(A_URL), *[playing(B_URL)] * 4)
    sleep = ActingSleep(sleeps=5, actions={4: lambda: covers.states.update({B_URL: READY_B})})

    await run_until_out_of_sleeps(board, current, sleep, covers)

    # Three samples with B pending kept A on the board, unpushed.
    assert board.drawn == ["cover a", "cover b"]


async def test_a_failed_download_shows_the_placeholder_until_a_retry_succeeds() -> None:
    board = FakeBoard()
    covers = FakeCovers({A_URL: READY_A, B_URL: FAILED})
    current = scripted(playing(A_URL), *[playing(B_URL)] * 3)
    sleep = ActingSleep(sleeps=4, actions={3: lambda: covers.states.update({B_URL: READY_B})})

    await run_until_out_of_sleeps(board, current, sleep, covers)

    assert board.drawn == ["cover a", "placeholder", "cover b"]


async def test_a_reconnect_during_a_changeover_redraws_the_old_cover(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The loader has moved on to B, so A's frame must come from the loop.
    board = FakeBoard()
    covers = FakeCovers({A_URL: READY_A})
    current = scripted(playing(A_URL), *[playing(B_URL)] * 3)
    sleep = ActingSleep(sleeps=4, actions={2: board.reconnect})

    with caplog.at_level(logging.INFO, logger="spotify_to_led.display"):
        await run_until_out_of_sleeps(board, current, sleep, covers)

    assert board.drawn == ["cover a", "cover a"]
    assert f"board link came up again; redrawing cover {A_URL}" in caplog.text


async def test_the_marker_comes_with_failing_reads_and_goes_when_they_recover(
    caplog: pytest.LogCaptureFixture,
) -> None:
    board = FakeBoard()
    covers = FakeCovers({A_URL: READY_A})
    current = scripted(
        playing(A_URL),
        playing(A_URL, failing_for=29),
        playing(A_URL, failing_for=31),
        playing(A_URL, failing_for=40),
        playing(A_URL),
    )

    with caplog.at_level(logging.INFO, logger="spotify_to_led.display"):
        await run_until_out_of_sleeps(board, current, FakeSleep(sleeps=5), covers)

    assert board.drawn == ["cover a", "stale cover a", "cover a"]
    assert f"board presentation: cover {A_URL} (stale)" in caplog.text


async def test_idle_is_marked_while_blind_and_unmarked_once_reads_recover(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Reads failing after "nothing playing": idle can no longer vouch for it.
    board = FakeBoard()
    current = scripted(
        NOTHING,
        failing(NOTHING, 29),
        failing(NOTHING, 31),
        failing(NOTHING, 40),
        NOTHING,
    )

    with caplog.at_level(logging.INFO, logger="spotify_to_led.display"):
        await run_until_out_of_sleeps(board, current, FakeSleep(sleeps=5))

    assert board.drawn == ["idle", "stale idle", "idle"]
    assert "board presentation: idle (stale)" in caplog.text


async def test_pausing_keeps_the_cover_and_stopping_returns_to_idle() -> None:
    board = FakeBoard()
    covers = FakeCovers({A_URL: READY_A})
    current = scripted(
        playing(A_URL),
        playing(A_URL, status=PlaybackStatus.PAUSED),
        NOTHING,
    )

    await run_until_out_of_sleeps(board, current, FakeSleep(sleeps=3), covers)

    assert board.drawn == ["cover a", "idle"]
    # Nothing on show wants nothing, which lets the loader drop the cover.
    assert covers.wanted == [A_URL, A_URL, None]


async def test_an_item_without_artwork_shows_the_symbol_at_once_after_a_cover() -> None:
    board = FakeBoard()
    covers = FakeCovers({A_URL: READY_A})
    current = scripted(playing(A_URL), playing(None), playing(None))

    await run_until_out_of_sleeps(board, current, FakeSleep(sleeps=3), covers)

    assert board.drawn == ["cover a", "no artwork"]
    assert covers.wanted == [A_URL, None, None]


async def test_without_a_loader_an_item_without_artwork_shows_the_symbol() -> None:
    board = FakeBoard()

    await run_until_out_of_sleeps(board, scripted(playing(None)), FakeSleep(sleeps=1))

    assert board.drawn == ["no artwork"]


async def test_without_a_loader_an_item_with_artwork_shows_the_placeholder() -> None:
    board = FakeBoard()

    await run_until_out_of_sleeps(board, scripted(playing(A_URL)), FakeSleep(sleeps=1))

    assert board.drawn == ["placeholder"]
