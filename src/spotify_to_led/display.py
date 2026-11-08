"""What the board shows, decided in one place.

This is the start of FR-009's display rule: the believed playback state goes
in, a presentation comes out, and the presentation names a frame. Keeping the
choice pure means the whole table is tested without a board or a reader.

The choices in this slice are interim and recorded here so they are replaced
on purpose rather than by accident:

- `PLAYING` and `PAUSED` show the playing placeholder. A paused item keeps its
  cover (an owner decision, see `PlaybackStatus`), so it keeps the stand-in
  for one too. Cover rendering (S-04) replaces the placeholder.
- `UNKNOWN` shows idle. Before the first successful read there is nothing to
  vouch for, and idle claims nothing about what is playing.
- No reader at all (`None`, when Spotify is not configured) shows idle.
- `failure` is ignored. Marking a stale cover (FR-018) arrives with S-06;
  until then the placeholder is not a cover, so there is nothing to mark.

`run_display` is the task that owns the board for the process lifetime. It
connects first, retrying forever with a doubling backoff, so a cold boot that
races BlueZ or a board switched on hours later both end connected without a
restart. It then samples the believed state about once a second and pushes a
frame only when the presentation differs from the last one it attempted.

Its boundaries in this slice, so they are not mistaken for oversights:

- A failed push is logged and not retried until the presentation changes.
  Re-pushing, including after the library reconnects a dropped link, is S-03.
- Only the first connect is retried here. A link that drops later is the
  library's own auto-reconnect, and nothing redraws the board after it.

The log lines are the evidence that the board recovers unattended, so they
have to stay readable in `pm2 logs`: one WARNING per failed connect, with a
traceback only on the first, INFO on connect, and INFO on each presentation
change. Each attempt also gets the library's own INFO "connecting to" line,
and a scan for an absent board takes about 30 s, so at the 60 s ceiling a
board that is off all day costs about 2,000 lines.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum

from PIL import Image

from spotify_to_led import frames
from spotify_to_led.board.transport import BoardTransport
from spotify_to_led.spotify.playback import PlaybackSnapshot, PlaybackStatus

logger = logging.getLogger(__name__)

# The same 5 s doubling to 60 s backoff the library uses for its own reconnect,
# so the two retry paths read alike in the log. Read at call time, not bound
# as defaults, so tests can shorten them.
CONNECT_RETRY_INITIAL_SECONDS = 5.0
CONNECT_RETRY_MAX_SECONDS = 60.0
# The reader polls every 3 s, so sampling every second puts a change on the
# board within about 4 s of Spotify reporting it.
SAMPLE_INTERVAL_SECONDS = 1.0

Sleep = Callable[[float], Awaitable[None]]


class Presentation(StrEnum):
    """Which kind of frame the board should show."""

    IDLE = "idle"
    PLAYING = "playing"


def select_presentation(snapshot: PlaybackSnapshot | None) -> Presentation:
    """Choose the presentation for `snapshot`; `None` means there is no reader."""
    if snapshot is None:
        return Presentation.IDLE
    if snapshot.status in (PlaybackStatus.PLAYING, PlaybackStatus.PAUSED):
        return Presentation.PLAYING
    return Presentation.IDLE


def frame_for(presentation: Presentation, size: int) -> Image.Image:
    """Return the RGB `(size, size)` frame that draws `presentation`."""
    if presentation is Presentation.PLAYING:
        return frames.playing_placeholder(size)
    return frames.idle(size)


async def run_display(
    transport: BoardTransport,
    size: int,
    current: Callable[[], PlaybackSnapshot | None],
    *,
    sleep: Sleep = asyncio.sleep,
) -> None:
    """Connect, then keep the board in step with `current()` until cancelled.

    `current` returns the believed state, or `None` when there is no reader.
    Nothing but `CancelledError` leaves this coroutine: the task must not be
    able to prevent the application from serving.
    """
    await _connect(transport, sleep)
    # `None` until the first push, so the first sample always draws.
    attempted: Presentation | None = None
    while True:
        try:
            presentation = select_presentation(current())
            if presentation is not attempted:
                # Recorded before the push, so a failed push is not retried on
                # every sample; the next change is what draws again.
                attempted = presentation
                logger.info("board presentation: %s", presentation)
                await _push(transport, frame_for(presentation, size))
        except Exception:
            # A bug must not end the display, as in `SpotifyReader.run`.
            logger.exception("unexpected error in the display loop; it keeps running")
        await sleep(SAMPLE_INTERVAL_SECONDS)


async def _connect(transport: BoardTransport, sleep: Sleep) -> None:
    """Connect, retrying with a doubling, capped delay until it succeeds."""
    delay = CONNECT_RETRY_INITIAL_SECONDS
    first_failure = True
    while True:
        try:
            await transport.connect()
        except Exception as error:
            # The traceback once, for diagnosis; after that one line per
            # attempt, so a board left off all day does not flood the log.
            logger.warning(
                "board connect failed (%s: %s); retrying in %s s",
                type(error).__name__,
                error,
                delay,
                exc_info=first_failure,
            )
            first_failure = False
            await sleep(delay)
            delay = min(delay * 2, CONNECT_RETRY_MAX_SECONDS)
        else:
            logger.info("board connected")
            return


async def _push(transport: BoardTransport, frame: Image.Image) -> None:
    try:
        await transport.push_frame(frame)
    except Exception:
        logger.exception(
            "pushing the frame failed; it is not retried until the presentation changes"
        )
