"""Which cover image to fetch for an item, and the loader that turns it into a frame.

Spotify offers each item's artwork in a few sizes, widest first (640, 300 and
64 px for an album). `select_artwork` picks the smallest one that still
covers the board, so the download that sits inside NFR-01's 5 s budget is a
few kilobytes rather than a full-size JPEG. Widths are not guaranteed, so an
image of unknown width never qualifies, and when none does the widest is
taken: downscaling too much costs nothing, upscaling would blur the cover.

`CoverLoader` holds the one cover the display currently wants. Downloading,
decoding and rendering happen in the loader's own task, never in the display
loop, so a slow CDN cannot delay link-down detection or a redraw on
reconnect. The display tells it which URL it wants on every sample and asks
for that URL's state:

- `PENDING` while the download is in flight, and for any URL that is not the
  one wanted now, so a stale question gets no stale answer.
- `READY` once the cover is rendered; the state carries the frame.
- `FAILED` after a failed attempt. A failure is retried with the board
  connect's backoff for as long as the same URL stays wanted, and the state
  stays `FAILED` through each wait, turning `READY` on a later success.

Wanting a different URL, or none, cancels the old work at once. Only the
wanted cover is held in memory. Across items and restarts, a `FrameCache`
keeps every rendered cover on disk (FR-017): a newly wanted URL is looked up
there first, and a hit is `READY` before `want` returns, with no task and no
download, so the display shows it on the same sample. Every cover rendered
after a download is stored there.

With an `Enhancer`, the display can also name an image to enhance the cover
from (FR-019): the item's widest, since the model draws from it, while the
URL wanted stays the small one the plain frame comes from and the one the
cache entries are named by. What is wanted is then the pair, and the same URL
wanted with a source, or without one, is a change like any other. An
enhanced cover goes:

- `READY`, enhanced, before `want` returns, when the cache holds its
  enhanced frame. No request is made of anyone.
- Otherwise `PENDING` while the plain frame is fetched, from the cache or by
  a download retried as above, and stored as usual. The plain frame comes
  first so that there is always one to fall back on, and so that switching
  enhancement off is answered at once, from memory or the cache.
- Then `GENERATING`, while the source is downloaded and the enhancer runs,
  which takes seconds, or a minute or more through its retries.
- Then `READY`: enhanced on success, when the frame is also stored as the
  image's enhanced entry, or plain when the enhancer answers `None` or the
  source cannot be downloaded. An image the enhancer has given up on, or any
  image while generation is paused, is `READY` plain at once, with no source
  download and no request.

A generation is cancelled like a download when something else is wanted, and
its frame is stored only if its cover is still wanted, enhanced.

The log lines say what happened to a cover, and nothing but the URL
identifies it: INFO when a cover is ready, saying so when it came from the
cache, a WARNING per failed attempt with its cause and the wait, with a
traceback only on the first failure of a URL. The enhancer logs its own
outcomes. The shared client is the Spotify client, but a cover request
carries no headers of its own, so no token goes to the image CDN or into
these lines.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO

import httpx2
from PIL import Image

from spotify_to_led.enhance import Enhancer
from spotify_to_led.frame_cache import FrameCache
from spotify_to_led.rendering import render_cover
from spotify_to_led.spotify.playback import Artwork, PlayingItem

logger = logging.getLogger(__name__)

# Most of NFR-01's 5 s goes to the reader poll and the display sample; this is
# what is left for one download. Passed per request, because the shared
# client's defaults belong to the Spotify reader.
FETCH_TIMEOUT_SECONDS = 4.0
# Spotify's covers are a few kilobytes at 64 px and a few hundred at 640 px;
# anything far beyond that is not a cover, and decoding it would cost memory
# and event-loop time on the Pi. Checked on the body and on the image header,
# before the pixels are decoded.
MAX_COVER_BYTES = 2_000_000
MAX_COVER_PIXELS = 4096 * 4096
# The same shape as the board connect retry (`display.CONNECT_RETRY_*`): 5 s
# doubling to 60 s, restarted for each URL. Read at call time, not bound as
# defaults, so tests can shorten them.
COVER_RETRY_INITIAL_SECONDS = 5.0
COVER_RETRY_MAX_SECONDS = 60.0

Sleep = Callable[[float], Awaitable[None]]


class CoverStatus(StrEnum):
    """How far the wanted cover has got."""

    PENDING = "pending"
    # The plain frame is in hand and the enhanced one is being made.
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True)
class CoverState:
    """The state of one cover; `frame` is the rendered cover, set only when `READY`.

    `enhanced` says whether that frame is the AI-enhanced one, so it is only
    ever true when `READY`.
    """

    status: CoverStatus
    frame: Image.Image | None = None
    enhanced: bool = False


PENDING = CoverState(CoverStatus.PENDING)
GENERATING = CoverState(CoverStatus.GENERATING)
FAILED = CoverState(CoverStatus.FAILED)


class CoverFetchError(Exception):
    """The image server answered, but not with an image to use."""


@dataclass(frozen=True)
class DownloadedImage:
    """A downloaded image, decoded, with the bytes it came as and their type."""

    image: Image.Image
    data: bytes
    mime: str


def select_artwork(item: PlayingItem | None, size: int) -> Artwork | None:
    """Return the image to download for `item` on a `size` px board, or `None`.

    The smallest image whose known width is at least `size`; if none
    qualifies, the first, which Spotify lists widest.
    """
    if item is None or not item.artwork:
        return None
    covering = [
        (artwork.width, artwork)
        for artwork in item.artwork
        if artwork.width is not None and artwork.width >= size
    ]
    if covering:
        return min(covering, key=lambda pair: pair[0])[1]
    return item.artwork[0]


async def download_image(client: httpx2.AsyncClient, url: str) -> DownloadedImage:
    """Download and decode the image at `url`, within the limits set for a cover.

    Raises on a transport error or timeout, a non-2xx status, or a body that
    does not decode as an image.
    """
    # httpx2's timeout bounds each phase, and the read timeout restarts with
    # every chunk, so a trickling server could outlast it; this bounds the
    # whole download. The per-request value still keeps the shared client's
    # defaults out of it.
    async with asyncio.timeout(FETCH_TIMEOUT_SECONDS):
        response = await client.get(url, timeout=FETCH_TIMEOUT_SECONDS)
    if not response.is_success:
        raise CoverFetchError(f"HTTP {response.status_code}")
    if len(response.content) > MAX_COVER_BYTES:
        raise CoverFetchError(f"body of {len(response.content)} bytes")
    image = Image.open(BytesIO(response.content))
    if image.width * image.height > MAX_COVER_PIXELS:
        raise CoverFetchError(f"image of {image.width}x{image.height} pixels")
    # `open` reads only the header; `load` decodes the rest, so a truncated
    # body fails here rather than somewhere in the render.
    image.load()
    # The type the bytes really are, whatever the server's header says, since
    # it is what an enhancement declares them as.
    mime = Image.MIME.get(image.format or "", "application/octet-stream")
    return DownloadedImage(image, response.content, mime)


async def fetch_cover(client: httpx2.AsyncClient, url: str, size: int) -> Image.Image:
    """Download the image at `url` and render it as a `size` px board frame.

    Raises as `download_image` does.
    """
    return render_cover((await download_image(client, url)).image, size)


class CoverLoader:
    """Downloads and renders the one cover the display wants, retrying failures.

    With an `enhancer`, it also makes the enhanced frame of a cover wanted
    with a source to enhance from; without one, such a source is ignored.
    """

    def __init__(
        self,
        client: httpx2.AsyncClient,
        size: int,
        *,
        cache: FrameCache | None = None,
        enhancer: Enhancer | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._client = client
        self._size = size
        self._cache = cache
        self._enhancer = enhancer
        self._sleep = sleep
        self._url: str | None = None
        self._enhance_from: str | None = None
        # The wanted URL's plain frame once it is in hand, so switching
        # enhancement either way within one cover needs no download.
        self._plain: Image.Image | None = None
        self._state = PENDING
        self._task: asyncio.Task[None] | None = None

    def want(self, url: str | None, *, enhance_from: str | None = None) -> None:
        """Make `url` the wanted cover, or want none; the same wish again is a no-op.

        `enhance_from`, when given, is the URL of the image to make `url`'s
        enhanced frame from. Only the same URL with the same source is the
        no-op. Must be called from a running event loop, which runs the
        download.
        """
        if self._enhancer is None:
            enhance_from = None
        if url == self._url and enhance_from == self._enhance_from:
            # Called on every display sample: restarting here would throw away
            # a download in flight, or a backoff that is meant to grow.
            return
        plain = self._plain if url == self._url else None
        self._cancel()
        self._url = url
        self._enhance_from = enhance_from
        self._plain = plain
        self._state = PENDING
        if url is None:
            return
        # Read on the event loop: an entry is a few kilobytes, and answering
        # now is what lets the display show it on this very sample.
        if enhance_from is not None and self._cache is not None:
            enhanced = self._cache.load(url, enhanced=True)
            if enhanced is not None:
                self._state = CoverState(CoverStatus.READY, enhanced, enhanced=True)
                logger.info("enhanced cover ready from cache: %s", url)
                return
        if plain is None and self._cache is not None:
            plain = self._cache.load(url)
            if plain is not None:
                self._plain = plain
                logger.info("cover ready from cache: %s", url)
        if plain is not None:
            if not self._will_enhance(enhance_from):
                self._state = CoverState(CoverStatus.READY, plain)
                return
            # The plain frame is in hand, so generation is all that is left,
            # and the display shows the placeholder from this very sample.
            self._state = GENERATING
        self._task = asyncio.create_task(self._load(url, enhance_from, plain))

    def state(self, url: str) -> CoverState:
        """The state of `url`: pending unless it is the wanted cover."""
        return self._state if url == self._url else PENDING

    async def aclose(self) -> None:
        """Stop any work and drop the held cover; safe to call more than once."""
        task = self._task
        self._cancel()
        self._url = None
        self._enhance_from = None
        self._plain = None
        self._state = PENDING
        if task is not None:
            with suppress(asyncio.CancelledError):
                await task

    def _cancel(self) -> None:
        # Not awaited here: `want` is synchronous, and a cancelled task stops
        # at its next await without touching the state again.
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def _will_enhance(self, enhance_from: str | None) -> bool:
        """Whether enhancing from `enhance_from` would be attempted now."""
        return (
            enhance_from is not None
            and self._enhancer is not None
            and self._enhancer.paused_until is None
            and not self._enhancer.gave_up(enhance_from)
        )

    def _wanted(self, url: str, enhance_from: str | None) -> bool:
        return self._url == url and self._enhance_from == enhance_from

    async def _load(
        self, url: str, enhance_from: str | None = None, plain: Image.Image | None = None
    ) -> None:
        """Fetch `url` until it succeeds, unless its frame is given, then enhance if asked.

        Only cancellation ends it early.
        """
        if plain is None:
            delay = COVER_RETRY_INITIAL_SECONDS
            first_failure = True
            while True:
                try:
                    plain = await fetch_cover(self._client, url, self._size)
                except Exception as error:
                    if not self._wanted(url, enhance_from):
                        # Cancellation should have stopped this task already; a
                        # library that swallowed it must not leave a stale answer.
                        return
                    # The traceback once, for diagnosis; after that one line per
                    # attempt, as for the board connect.
                    logger.warning(
                        "cover download failed for %s (%s: %s); retrying in %s s",
                        url,
                        type(error).__name__,
                        error,
                        delay,
                        exc_info=first_failure,
                    )
                    first_failure = False
                    self._state = FAILED
                    await self._sleep(delay)
                    delay = min(delay * 2, COVER_RETRY_MAX_SECONDS)
                else:
                    break
            if not self._wanted(url, enhance_from):
                return
            self._plain = plain
            if self._cache is not None:
                # The unmarked render: the staleness marker is drawn on a
                # copy at display time, never into the stored frame.
                self._cache.store(url, plain)
            if not self._will_enhance(enhance_from):
                self._state = CoverState(CoverStatus.READY, plain)
                logger.info("cover ready: %s", url)
                return
            logger.info("cover ready: %s; enhancing it", url)
            self._state = GENERATING
        assert enhance_from is not None
        enhanced = await self._enhance(enhance_from)
        if not self._wanted(url, enhance_from):
            return
        if enhanced is None:
            self._state = CoverState(CoverStatus.READY, plain)
            return
        self._state = CoverState(CoverStatus.READY, enhanced, enhanced=True)
        if self._cache is not None:
            self._cache.store(url, enhanced, enhanced=True)

    async def _enhance(self, source_url: str) -> Image.Image | None:
        """The enhanced frame made from the image at `source_url`, or `None` for plain."""
        assert self._enhancer is not None
        try:
            source = await download_image(self._client, source_url)
        except Exception as error:
            # Not retried: the plain cover stands in, and the source is asked
            # for again the next time this cover is wanted enhanced.
            logger.warning(
                "could not download %s to enhance it (%s: %s); showing the plain cover",
                source_url,
                type(error).__name__,
                error,
            )
            return None
        return await self._enhancer.enhance(source_url, source.data, source.mime)
