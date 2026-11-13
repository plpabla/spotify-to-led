"""What the board shows, decided in one place.

This is FR-009's display rule, completed by S-06: the believed playback
state and the loader's state of the wanted cover go in, a presentation comes
out. Keeping the choice pure means the whole table is tested without a board,
a reader or a download. The rule:

- No reader (`None`, when Spotify is not configured) shows plain idle, never
  stale: that is a setup mode, not a service that has lost sight of Spotify.
- `UNKNOWN` and `NOTHING_PLAYING` show idle.
- `PLAYING` and `PAUSED` show the item's cover. A paused item keeps it (an
  owner decision, see `PlaybackStatus`).
- An item with no artwork at all, such as an ad or a local file, or no item,
  shows FR-010's no-artwork symbol at once, with or without a loader. There is
  no cover to wait for.
- An item whose cover failed to download, or an item with artwork when
  there is no loader, shows the playing placeholder; the loader keeps retrying the
  download, and the cover replaces the placeholder once it succeeds.
- While the wanted cover downloads, the previous presentation stays, so an
  item change goes from one cover straight to the next without a flash in
  between. That includes idle: playback starting from idle keeps idle until
  the first cover is ready. It includes the no-artwork symbol too: after an
  ad, the disc stays until the next item's cover is ready, briefly saying "no
  artwork" for an item that has some, which S-05 accepted over a flash of the
  placeholder. Only before anything was chosen at all does a pending cover
  show the placeholder.
- With AI enhancement switched on (FR-019), a cover whose enhanced frame is
  being generated shows its plain frame with the spend bar along its bottom
  row: today's AI spend as a fraction of the owner's daily limit, as it stood
  before this generation. Generation takes seconds, far longer than a
  changeover should keep the previous item's cover up. It then redraws as
  the enhanced frame, or as the plain one without the bar when the
  generation fails or is paused. A cover's presentation says whether it is
  the enhanced frame and whether it is generating, so switching enhancement
  either way redraws the current item. The fraction is part of the
  presentation too, so a change of it while generating redraws; no other
  presentation, the enhanced frame included, has the bar.
- Whatever a reader's snapshot chose, cover, idle, placeholder or symbol,
  newly chosen or kept through a changeover, is stale once reads have been
  failing, or no read has succeeded, for more than `STALE_AFTER_SECONDS`,
  and is drawn with FR-018's marker until a read succeeds again. Idle after
  "nothing playing" and the disc after an ad are claims a blind service can
  no longer vouch for either (OQ2).

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
cannot delay link-down detection or a redraw. When the owner's switch is on,
read afresh on every sample, the loader is also told the item's widest image
to enhance the cover from, and a generation runs in that same task. The loop
keeps the frame of the cover it last chose rather than asking the loader for
it, because during a changeover the loader has already moved on to the next
cover while the board still shows, and after a reconnect redraws, the
previous one.

A failed push is retried while the same presentation stays wanted, 1 s
doubling to 60 s, restarted for each new presentation. Once the board can show
a stale marker, a push that failed silently would leave an unmarked cover up
for the whole outage, which the hard rules forbid.

The log lines are the evidence that the board recovers unattended, so they
have to stay readable in `pm2 logs`: INFO when the link is lost, one WARNING
per failed connect, with a traceback only on the first of each outage, INFO
on connect, and INFO on each presentation change, naming the cover's URL and
whether it is enhanced, generating or stale. Each attempt also gets the library's own INFO
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
from spotify_to_led.owner_settings import OwnerSettings
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
# FR-018's grace: a blip of failed reads leaves the board unmarked, whatever
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

    `enhanced` says whether a cover is its AI-enhanced frame, and `generating`
    whether it is the plain frame shown while the enhanced one is made, drawn
    with the generating marker. `spend_fraction` is set only on a generating
    cover, and draws the spend bar. Hashable and compared by value, so the
    loop can tell a change of cover, of its frame or of a marker alone, from
    the same presentation sampled again.
    """

    kind: PresentationKind
    url: str | None = None
    stale: bool = False
    enhanced: bool = False
    generating: bool = False
    spend_fraction: float | None = None

    def __str__(self) -> str:
        name = f"cover {self.url}" if self.kind is PresentationKind.COVER else str(self.kind)
        if self.enhanced:
            name = f"{name} (enhanced)"
        if self.generating:
            name = f"{name} (generating)"
        if self.spend_fraction is not None:
            name = f"{name} (spend {self.spend_fraction:.0%})"
        return f"{name} (stale)" if self.stale else name


IDLE = Presentation(PresentationKind.IDLE)
PLACEHOLDER = Presentation(PresentationKind.PLACEHOLDER)
NO_ARTWORK = Presentation(PresentationKind.NO_ARTWORK)


@dataclass
class DisplayStatus:
    """What `run_display` last sent the board, for the status page to report.

    `presentation` is the one last attempted, `None` before the first push;
    `push_failed` is whether its latest push failed. Written only by the loop
    and read by the routes, both on the one event loop, so no lock is needed.
    """

    presentation: Presentation | None = None
    push_failed: bool = False


def _utc_now() -> datetime:
    return datetime.now(UTC)


def wanted_cover_url(snapshot: PlaybackSnapshot | None, size: int) -> str | None:
    """The URL of the cover `snapshot` calls for on a `size` px board, or `None`."""
    if snapshot is None or snapshot.status not in _SHOWS_AN_ITEM:
        return None
    artwork = select_artwork(snapshot.item, size)
    return artwork.url if artwork is not None else None


def enhancement_source_url(snapshot: PlaybackSnapshot | None) -> str | None:
    """The URL of the image to enhance the cover `snapshot` calls for from, or `None`.

    The widest image, which Spotify lists first: the model redraws the cover,
    and the more of it it sees, the better.
    """
    if snapshot is None or snapshot.status not in _SHOWS_AN_ITEM:
        return None
    if snapshot.item is None or not snapshot.item.artwork:
        return None
    return snapshot.item.artwork[0].url


def select_presentation(
    snapshot: PlaybackSnapshot | None,
    cover: tuple[str, CoverState] | None,
    previous: Presentation | None,
    now: datetime,
    spend_fraction: float | None = None,
) -> Presentation:
    """Choose the presentation for `snapshot`; `None` means there is no reader.

    `cover` is the wanted cover's URL and its loader state, or `None` when
    there is no cover to want (or no loader to want it from). `previous` is
    the presentation last chosen, `None` before the first. `spend_fraction`,
    today's AI spend over the daily limit, goes on a generating cover only.
    """
    if snapshot is None:
        # Spotify is not configured: a setup mode, not blindness, so plain idle.
        return IDLE
    if snapshot.status not in _SHOWS_AN_ITEM:
        chosen = IDLE
    # Decided from the item, before the loader: `cover` is also `None` when
    # there is no loader, and an ad without one must still get the symbol.
    elif snapshot.item is None or not snapshot.item.artwork:
        chosen = NO_ARTWORK
    elif cover is None:
        chosen = PLACEHOLDER
    elif cover[1].status is CoverStatus.READY:
        chosen = Presentation(PresentationKind.COVER, cover[0], enhanced=cover[1].enhanced)
    elif cover[1].status is CoverStatus.PENDING:
        # The changeover: whatever was chosen last, idle included, stays until
        # the new cover is ready, rather than flashing the placeholder.
        chosen = previous if previous is not None else PLACEHOLDER
    elif cover[1].status is CoverStatus.GENERATING:
        # The plain frame is in hand: shown, with the spend bar, while the
        # enhanced one is made.
        chosen = Presentation(
            PresentationKind.COVER, cover[0], generating=True, spend_fraction=spend_fraction
        )
    else:
        # A failed download.
        chosen = PLACEHOLDER
    # Whatever the kind, and recomputed for a kept presentation too: staleness
    # is about the reads now, not about when the presentation was chosen.
    return replace(chosen, stale=is_stale(snapshot, now))


def is_stale(snapshot: PlaybackSnapshot, now: datetime) -> bool:
    """Whether the service can no longer vouch for `snapshot` at `now` (FR-018).

    Public because the status page must say what the board's marker says, by
    this very rule rather than a copy of it.
    """
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
    owner_settings: Callable[[], OwnerSettings] | None = None,
    spend_fraction: Callable[[], float] | None = None,
    status: DisplayStatus | None = None,
    clock: Clock = _utc_now,
    sleep: Sleep = asyncio.sleep,
) -> None:
    """Connect, then keep the board in step with `current()` until cancelled.

    `current` returns the believed state, or `None` when there is no reader.
    `covers` downloads the covers; without one, an item with artwork shows the
    placeholder. `owner_settings` returns the owner's current choices, and is
    given only when `covers` has an enhancer: with it, and enhancement
    switched on, covers are wanted enhanced. `spend_fraction` returns today's
    AI spend over the daily limit, read on every sample for the spend bar of
    a generating cover; it means something only with `covers`. `status`, when
    given, is kept up to date with the presentation last attempted and
    whether its push failed.
    Nothing but `CancelledError` leaves this coroutine: the task must not be
    able to prevent the application from serving.
    """
    if status is None:
        status = DisplayStatus()
    await _connect(transport, sleep)
    # What was last drawn, and on which connection of the link; `None` until
    # the first push, so the first sample always draws.
    attempted: tuple[Presentation, int] | None = None
    # The presentation the rule last chose, which a changeover keeps.
    previous: Presentation | None = None
    # The URL, enhancement and frame of the cover last chosen, kept here
    # because the loader forgets it as soon as the next cover is wanted.
    retained: tuple[str, bool, Image.Image] | None = None
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
            fraction: float | None = None
            if covers is not None:
                url = wanted_cover_url(snapshot, size)
                enhance = owner_settings is not None and owner_settings().ai_enhanced
                source = enhancement_source_url(snapshot) if enhance else None
                # A no-op for the cover already wanted; never waits for the
                # download. The plain wish is made exactly as without enhancement.
                if source is None:
                    covers.want(url)
                else:
                    covers.want(url, enhance_from=source)
                if url is not None:
                    cover = (url, covers.state(url))
                if spend_fraction is not None:
                    fraction = spend_fraction()
            previous = select_presentation(snapshot, cover, previous, clock(), fraction)
            if cover is not None and cover[1].frame is not None:
                # Only a ready or generating cover carries a frame, and such
                # a cover is always the one chosen.
                retained = (cover[0], cover[1].enhanced, cover[1].frame)
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
                status.presentation = presentation
                # The last failure belongs to the presentation before this one.
                status.push_failed = False
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
                status.push_failed = push_failed
                if push_failed:
                    retry_in = retry_delay
                    retry_delay = min(retry_delay * 2, PUSH_RETRY_MAX_SECONDS)
        except Exception:
            # A bug must not end the display, as in `SpotifyReader.run`.
            logger.exception("unexpected error in the display loop; it keeps running")
            # Whatever was being sent did not reach the board as reported.
            status.push_failed = True
        await sleep(SAMPLE_INTERVAL_SECONDS)


def _frame_for(
    presentation: Presentation, size: int, retained: tuple[str, bool, Image.Image] | None
) -> Image.Image:
    """Return the RGB `(size, size)` frame that draws `presentation`, with its markers.

    The spend bar goes on first and the staleness marker last, each on a
    copy, so the retained frame is never drawn into and FR-018's marker is
    never covered.
    """
    if presentation.kind is PresentationKind.IDLE:
        frame = frames.idle(size)
    elif presentation.kind is PresentationKind.PLACEHOLDER:
        frame = frames.playing_placeholder(size)
    elif presentation.kind is PresentationKind.NO_ARTWORK:
        frame = frames.no_artwork(size)
    elif retained is None or retained[:2] != (presentation.url, presentation.enhanced):
        # Cannot happen while every cover with a frame is retained as it is
        # chosen; raised rather than drawing some other cover under this one's name.
        raise RuntimeError(f"no frame retained for {presentation}")
    elif presentation.generating:
        frame = retained[2]
        if presentation.spend_fraction is not None:
            frame = frames.with_spend_bar(frame, presentation.spend_fraction)
    else:
        frame = retained[2]
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
