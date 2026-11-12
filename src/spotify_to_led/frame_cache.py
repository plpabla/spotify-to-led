"""Where rendered covers persist, so a cover seen before needs no download (FR-017).

Spotify serves every cover from `https://i.scdn.co/image/<id>`, and the id
names one image, whichever item it belongs to: the tracks of an album share
it. That id is the key, and only a URL of exactly that shape has one, with
the id made of letters and digits alone, so nothing taken from a URL can put
a dot or a slash into a path here. A cover from anywhere else is simply not
cached and is downloaded each time it is wanted.

An entry is the rendered frame, not the downloaded image, stored as
`<root>/<size>/<id>.png`. The board size is in the path because the frame is
drawn for one size: a 64 px board must not be handed a 32 px cache's frames,
and switching sizes leaves the other directory intact for switching back. The
renderer is not in the path, so a change to how covers are rendered means a
new directory or a wiped cache; FR-017 accepts that, since every entry can be
downloaded again.

An image can have a second entry: its AI-enhanced frame (FR-019), stored
beside the plain one as `<root>/<size>/<id>.ai.png`. The suffix comes after
the id, which the id's alphabet leaves room for, so the two can never name
the same file, and the plain entries written before enhancement existed stay
valid as they are. The two are independent: switching enhancement off finds
the plain entry, and switching it on again finds the enhanced one, so a cover
is generated at most once, even across restarts. Unlike a plain entry, an
enhanced one cannot be had again for the price of a download: losing it costs
a paid generation.

The cache is unbounded: one entry is a few kilobytes, so even years of
listening stay small next to the Pi's storage. It is never fatal to the
display. An entry that cannot be read or does not hold a frame of the right
size is a miss, and the next download overwrites it; a write that fails is
logged and the frame is still shown. Writes go through a temp file and
`os.replace`, so a crash leaves the old entry or the new one, but there is no
fsync: an entry lost to a power cut costs one more download, not a credential.
"""

import logging
import os
import re
import secrets
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# The whole URL must match, which `fullmatch` enforces: no other host, no
# further path segment, no query string.
SCDN_IMAGE_URL = re.compile(r"https://i\.scdn\.co/image/([0-9A-Za-z]+)")


def image_id(url: str) -> str | None:
    """The Spotify image id in `url`, or `None` when `url` is not a plain scdn image URL."""
    match = SCDN_IMAGE_URL.fullmatch(url)
    return match.group(1) if match is not None else None


class FrameCache:
    """Rendered `size` px frames on disk under `root`, plain and enhanced, per Spotify image id."""

    def __init__(self, root: Path, size: int) -> None:
        self._directory = root / str(size)
        self._size = size

    def load(self, url: str, *, enhanced: bool = False) -> Image.Image | None:
        """The cached plain or enhanced frame for `url`, or `None` on a miss of any kind."""
        path = self._path(url, enhanced)
        if path is None:
            return None
        try:
            with Image.open(path) as image:
                # Checked on the header, before the pixels are decoded.
                if image.size != (self._size, self._size):
                    logger.warning(
                        "ignoring cached cover %s of %sx%s pixels on a %s px board",
                        path,
                        image.width,
                        image.height,
                        self._size,
                    )
                    return None
                # Decoded and converted while the file is still open, so the
                # frame handed back owns its pixels.
                return image.convert("RGB")
        except FileNotFoundError:
            return None
        # DecompressionBombError is no OSError: a header claiming a huge
        # image raises it from `open`, and it must be a miss like the rest.
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
            logger.warning(
                "ignoring unreadable cached cover %s (%s: %s)",
                path,
                type(error).__name__,
                error,
            )
            return None

    def store(self, url: str, frame: Image.Image, *, enhanced: bool = False) -> None:
        """Keep `frame` as `url`'s plain or enhanced entry; a URL without an id is not kept."""
        path = self._path(url, enhanced)
        if path is None:
            return
        # Same directory as the entry, or `os.replace` would not be atomic.
        temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # A power cut between writing a temp file and replacing the entry
            # leaves one behind, and nothing else would ever remove it.
            for leftover in path.parent.glob(f".{path.name}.*.tmp"):
                leftover.unlink(missing_ok=True)
            try:
                frame.save(temp, format="PNG")
                os.replace(temp, path)
            except BaseException:
                temp.unlink(missing_ok=True)
                raise
        except OSError as error:
            # The frame is already on its way to the board; only the next
            # sighting of this cover pays for the failure, with a download.
            logger.error(
                "could not keep the rendered cover in %s (%s)",
                path,
                type(error).__name__,
            )

    def _path(self, url: str, enhanced: bool) -> Path | None:
        found = image_id(url)
        if found is None:
            return None
        return self._directory / (f"{found}.ai.png" if enhanced else f"{found}.png")
