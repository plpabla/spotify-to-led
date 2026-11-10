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

The log lines say what happened to a cover, and nothing but the URL
identifies it: INFO when a cover is ready, saying so when it came from the
cache, a WARNING per failed attempt with its cause and the wait, with a
traceback only on the first failure of a URL.
The shared client is the Spotify client, but a cover request carries no
headers of its own, so no token goes to the image CDN or into these lines.
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
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True)
class CoverState:
    """The state of one cover; `frame` is the rendered cover, set only when `READY`."""

    status: CoverStatus
    frame: Image.Image | None = None


PENDING = CoverState(CoverStatus.PENDING)
FAILED = CoverState(CoverStatus.FAILED)


class CoverFetchError(Exception):
    """The image server answered, but not with an image to use."""


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


async def fetch_cover(client: httpx2.AsyncClient, url: str, size: int) -> Image.Image:
    """Download the image at `url` and render it as a `size` px board frame.

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
    with Image.open(BytesIO(response.content)) as image:
        if image.width * image.height > MAX_COVER_PIXELS:
            raise CoverFetchError(f"image of {image.width}x{image.height} pixels")
        # `open` reads only the header; `load` decodes the rest, so a
        # truncated body fails here rather than somewhere in the render.
        image.load()
        return render_cover(image, size)


class CoverLoader:
    """Downloads and renders the one cover the display wants, retrying failures."""

    def __init__(
        self,
        client: httpx2.AsyncClient,
        size: int,
        *,
        cache: FrameCache | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._client = client
        self._size = size
        self._cache = cache
        self._sleep = sleep
        self._url: str | None = None
        self._state = PENDING
        self._task: asyncio.Task[None] | None = None

    def want(self, url: str | None) -> None:
        """Make `url` the wanted cover, or want none; the same URL again is a no-op.

        Must be called from a running event loop, which runs the download.
        """
        if url == self._url:
            # Called on every display sample: restarting here would throw away
            # a download in flight, or a backoff that is meant to grow.
            return
        self._cancel()
        self._url = url
        self._state = PENDING
        if url is None:
            return
        # Read on the event loop: an entry is a few kilobytes, and answering
        # now is what lets the display show it on this very sample.
        cached = self._cache.load(url) if self._cache is not None else None
        if cached is not None:
            self._state = CoverState(CoverStatus.READY, cached)
            logger.info("cover ready from cache: %s", url)
            return
        self._task = asyncio.create_task(self._load(url))

    def state(self, url: str) -> CoverState:
        """The state of `url`: pending unless it is the wanted cover."""
        return self._state if url == self._url else PENDING

    async def aclose(self) -> None:
        """Stop any work and drop the held cover; safe to call more than once."""
        task = self._task
        self._cancel()
        self._url = None
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

    async def _load(self, url: str) -> None:
        """Fetch `url` until it succeeds; only cancellation ends it early."""
        delay = COVER_RETRY_INITIAL_SECONDS
        first_failure = True
        while True:
            try:
                frame = await fetch_cover(self._client, url, self._size)
            except Exception as error:
                if self._url != url:
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
                if self._url != url:
                    return
                self._state = CoverState(CoverStatus.READY, frame)
                logger.info("cover ready: %s", url)
                if self._cache is not None:
                    # The unmarked render: the staleness marker is drawn on a
                    # copy at display time, never into the stored frame.
                    self._cache.store(url, frame)
                return
