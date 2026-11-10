"""What the board shows, decided in one place.

This is FR-009's display rule as far as S-05 takes it: the believed playback
state and the loader's state of the wanted cover go in, a presentation comes
out. Keeping the choice pure means the whole table is tested without a board,
a reader or a download. The rule as it stands:

- No reader (`None`, when Spotify is not configured), `UNKNOWN` or
  `NOTHING_PLAYING` show idle, whether or not reads are failing. Idle claims
  nothing about what is playing, so there is nothing on it to mark stale.
  What the board should show when it is blind and has no cover to mark is
  OQ2, and that case stays S-06's.
- `PLAYING` and `PAUSED` show the item's cover. A paused item keeps it (an
  owner decision, see `PlaybackStatus`).
- An item with no artwork at all, such as an ad or a local file, or no item,
  shows FR-010's no-artwork symbol at once, with or without a loader. There is
  no cover to wait for, and the symbol claims nothing a failed read could
  make untrue, so it is never marked stale.
- An item whose cover failed to download, or any item when there is no
  loader, shows the playing placeholder; the loader keeps retrying the
  download, and the cover replaces the placeholder once it succeeds.
- While the wanted cover downloads, the previous presentation stays, so an
  item change goes from one cover straight to the next without a flash in
  between. That includes idle: playback starting from idle keeps idle until
  the first cover is ready. Only before anything was chosen at all does a
  pending cover show the placeholder.
- A cover, newly chosen or kept through a changeover, is stale once reads
  have been failing, or no read has succeeded, for more than
  `STALE_AFTER_SECONDS`, and is drawn with FR-018's marker until a read
  succeeds again.

`run_display` is the task that owns the board for the process lifetime. Every
connect is retried here, forever with a doubling backoff: the first one, so a
cold boot that races BlueZ or a board switched on hours later both end
connected without a restart, and every one after the link drops, so a board
switched off and on, or briefly out of range, comes back the same way. The
backoff starts over at 5 s for each outage. Between connects it samples the
believed state about once a second and pushes a frame when the presentation
differs from the last one it attempted, or when the link has come up again
since then: a board that was switched off and on comes back blank.

Each sample tells the cover loader which cover it wants and only asks for its
state; the download runs in the loader's own task, so a slow image server
cannot delay link-down detection or a redraw. The loop keeps the frame of the
cover it last chose rather than asking the loader for it, because during a
changeover the loader has already moved on to the next cover while the board
still shows, and after a reconnect redraws, the previous one.

A failed push is retried while the same presentation stays wanted, 1 s
doubling to 60 s, restarted for each new presentation. Once the board can show
a stale marker, a push that failed silently would leave an unmarked cover up
for the whole outage, which the hard rules forbid.

The log lines are the evidence that the board recovers unattended, so they
have to stay readable in `pm2 logs`: INFO when the link is lost, one WARNING
per failed connect, with a traceback only on the first of each outage, INFO
on connect, and INFO on each presentation change, naming the cover's URL and
whether it is stale. Each attempt also gets the library's own INFO
"connecting to" line, and a scan for an absent board takes about 30 s, so at
the 60 s ceiling a board that is off all day costs about 2,000 lines, whether
it was off from boot or switched off later.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from PIL import Image

from spotify_to_led import frames
from spotify_to_led.board.transport import BoardTransport, LinkState
from spotify_to_led.covers import CoverLoader, CoverState, CoverStatus, select_artwork
from spotify_to_led.spotify.playback import PlaybackSnapshot, PlaybackStatus

logger = logging.getLogger(__name__)

# One retry path for the first connect and every reconnect: 5 s doubling to
# 60 s, restarted for each outage. Read at call time, not bound as defaults,
# so tests can shorten them.
CONNECT_RETRY_INITIAL_SECONDS = 5.0
CONNECT_RETRY_MAX_SECONDS = 60.0
# The reader polls every 3 s, so sampling every second puts a change on the
# board within about 4 s of Spotify reporting it.
SAMPLE_INTERVAL_SECONDS = 1.0
# FR-018's grace: a blip of failed reads leaves the cover unmarked, a cover
# the service has not been able to vouch for in longer than this is marked.
STALE_AFTER_SECONDS = 30
# A failed push is retried while its presentation stays wanted: the next
# sample, then doubling to 60 s. Restarted for each new presentation.
PUSH_RETRY_INITIAL_SECONDS = 1.0
PUSH_RETRY_MAX_SECONDS = 60.0

Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]

# The statuses with an item on show; a paused item keeps its cover.
_SHOWS_AN_ITEM = (PlaybackStatus.PLAYING, PlaybackStatus.PAUSED)


class PresentationKind(StrEnum):
    """Which kind of frame the board should show."""

    IDLE = "idle"
    PLACEHOLDER = "placeholder"
    NO_ARTWORK = "no_artwork"
    COVER = "cover"


@dataclass(frozen=True)
class Presentation:
    """What the board should show: a kind, the cover's URL for a cover, and staleness.

    Hashable and compared by value, so the loop can tell a change of cover,
    or of the marker alone, from the same presentation sampled again.
    """

    kind: PresentationKind
    url: str | None = None
    stale: bool = False

    def __str__(self) -> str:
        if self.kind is not PresentationKind.COVER:
            return str(self.kind)
        return f"cover {self.url} (stale)" if self.stale else f"cover {self.url}"


IDLE = Presentation(PresentationKind.IDLE)
PLACEHOLDER = Presentation(PresentationKind.PLACEHOLDER)
NO_ARTWORK = Presentation(PresentationKind.NO_ARTWORK)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def wanted_cover_url(snapshot: PlaybackSnapshot | None, size: int) -> str | None:
    """The URL of the cover `snapshot` calls for on a `size` px board, or `None`."""
    if snapshot is None or snapshot.status not in _SHOWS_AN_ITEM:
        return None
    artwork = select_artwork(snapshot.item, size)
    return artwork.url if artwork is not None else None


def select_presentation(
    snapshot: PlaybackSnapshot | None,
    cover: tuple[str, CoverState] | None,
    previous: Presentation | None,
    now: datetime,
) -> Presentation:
    """Choose the presentation for `snapshot`; `None` means there is no reader.

    `cover` is the wanted cover's URL and its loader state, or `None` when
    there is no cover to want (or no loader to want it from). `previous` is
    the presentation last chosen, `None` before the first.
    """
    if snapshot is None or snapshot.status not in _SHOWS_AN_ITEM:
        return IDLE
    # Decided from the item, before the loader: `cover` is also `None` when
    # there is no loader, and an ad without one must still get the symbol.
    if snapshot.item is None or not snapshot.item.artwork:
        return NO_ARTWORK
    if cover is None:
        return PLACEHOLDER
    url, state = cover
    if state.status is CoverStatus.READY:
        chosen = Presentation(PresentationKind.COVER, url)
    elif state.status is CoverStatus.PENDING:
        # The changeover: whatever was chosen last, idle included, stays until
        # the new cover is ready, rather than flashing the placeholder.
        chosen = previous if previous is not None else PLACEHOLDER
    else:
        return PLACEHOLDER
    if chosen.kind is not PresentationKind.COVER:
        return chosen
    # Recomputed for a kept cover too: staleness is about the reads now, not
    # about when the cover was chosen.
    return replace(chosen, stale=_is_stale(snapshot, now))


def _is_stale(snapshot: PlaybackSnapshot, now: datetime) -> bool:
    limit = timedelta(seconds=STALE_AFTER_SECONDS)
    if snapshot.failure_since is not None and now - snapshot.failure_since > limit:
        return True
    # Reads that stop without failing, such as a reader task that has ended,
    # never set `failure_since`; the age of the last good read still shows it.
    return snapshot.last_read_at is not None and now - snapshot.last_read_at > limit


async def run_display(
    transport: BoardTransport,
    size: int,
    current: Callable[[], PlaybackSnapshot | None],
    *,
    covers: CoverLoader | None = None,
    clock: Clock = _utc_now,
    sleep: Sleep = asyncio.sleep,
) -> None:
    """Connect, then keep the board in step with `current()` until cancelled.

    `current` returns the believed state, or `None` when there is no reader.
    `covers` downloads the covers; without one, an item with artwork shows the
    placeholder.
    Nothing but `CancelledError` leaves this coroutine: the task must not be
    able to prevent the application from serving.
    """
    await _connect(transport, sleep)
    # What was last drawn, and on which connection of the link; `None` until
    # the first push, so the first sample always draws.
    attempted: tuple[Presentation, int] | None = None
    # The presentation the rule last chose, which a changeover keeps.
    previous: Presentation | None = None
    # The URL and frame of the cover last chosen, kept here because the loader
    # forgets it as soon as the next cover is wanted.
    retained: tuple[str, Image.Image] | None = None
    # Whether the last push failed, and when and how soon to try it again.
    push_failed = False
    retry_in = 0.0
    retry_delay = PUSH_RETRY_INITIAL_SECONDS
    while True:
        try:
            if transport.link_state is LinkState.DOWN:
                # `attempted` survives the reconnect, so the grown connection
                # count makes this same sample redraw.
                logger.info("board link lost; reconnecting")
                await _connect(transport, sleep)
            snapshot = current()
            cover: tuple[str, CoverState] | None = None
            if covers is not None:
                url = wanted_cover_url(snapshot, size)
                # A no-op for the URL already wanted; never waits for the download.
                covers.want(url)
                if url is not None:
                    cover = (url, covers.state(url))
            previous = select_presentation(snapshot, cover, previous, clock())
            if cover is not None and cover[1].frame is not None:
                # Only a ready cover carries a frame, and a ready cover is
                # always the one chosen.
                retained = (cover[0], cover[1].frame)
            wanted = (previous, transport.connections)
            if wanted != attempted:
                presentation = wanted[0]
                if attempted is not None and presentation == attempted[0]:
                    logger.info("board link came up again; redrawing %s", presentation)
                else:
                    logger.info("board presentation: %s", presentation)
                # Recorded before the push, so a failed push is retried on its
                # own backoff below rather than on every sample.
                attempted = wanted
                retry_delay = PUSH_RETRY_INITIAL_SECONDS
                due = True
            elif push_failed:
                retry_in -= SAMPLE_INTERVAL_SECONDS
                due = retry_in <= 0
            else:
                due = False
            if due:
                first = retry_delay == PUSH_RETRY_INITIAL_SECONDS
                push_failed = not await _push(
                    transport, _frame_for(wanted[0], size, retained), retry_delay, first
                )
                if push_failed:
                    retry_in = retry_delay
                    retry_delay = min(retry_delay * 2, PUSH_RETRY_MAX_SECONDS)
        except Exception:
            # A bug must not end the display, as in `SpotifyReader.run`.
            logger.exception("unexpected error in the display loop; it keeps running")
        await sleep(SAMPLE_INTERVAL_SECONDS)


def _frame_for(
    presentation: Presentation, size: int, retained: tuple[str, Image.Image] | None
) -> Image.Image:
    """Return the RGB `(size, size)` frame that draws `presentation`."""
    if presentation.kind is PresentationKind.IDLE:
        return frames.idle(size)
    if presentation.kind is PresentationKind.PLACEHOLDER:
        return frames.playing_placeholder(size)
    if presentation.kind is PresentationKind.NO_ARTWORK:
        return frames.no_artwork(size)
    if retained is None or retained[0] != presentation.url:
        # Cannot happen while every ready cover is retained as it is chosen;
        # raised rather than drawing some other cover under this one's name.
        raise RuntimeError(f"no frame retained for {presentation}")
    frame = retained[1]
    return frames.with_stale_marker(frame) if presentation.stale else frame


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


async def _push(
    transport: BoardTransport, frame: Image.Image, retry_in: float, first: bool
) -> bool:
    """Push `frame`; on failure log it and return `False`, never raise."""
    try:
        await transport.push_frame(frame)
    except Exception as error:
        # The traceback once per presentation, as for a failed connect.
        logger.warning(
            "pushing the frame failed (%s: %s); retrying in %s s",
            type(error).__name__,
            error,
            retry_in,
            exc_info=first,
        )
        return False
    return True
