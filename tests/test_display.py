"""The display rule: believed playback state in, presentation out."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from spotify_to_led import frames
from spotify_to_led.board.transport import frame_to_pixel_colors
from spotify_to_led.display import Presentation, frame_for, select_presentation
from spotify_to_led.spotify.playback import INITIAL_SNAPSHOT, PlaybackStatus, ReadFailure

SIZE = 32


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
