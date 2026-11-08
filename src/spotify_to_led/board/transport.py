"""The contract every LED board implementation satisfies.

The frame invariant and the image-to-wire conversion live here rather than in
an adapter, so both can be exercised without any Bluetooth involvement.
"""

from enum import StrEnum
from typing import Protocol

from PIL import Image


class LinkState(StrEnum):
    """Whether the application currently believes the board is reachable."""

    UP = "up"
    DOWN = "down"


class BoardTransport(Protocol):
    """A board the service can push frames to.

    Implementations satisfy this structurally; they do not inherit it.
    `connect` may raise, and `close` must be safe to call after a failed or
    skipped `connect`.

    `connections` counts how many times the link has come up, including
    reconnects the implementation makes on its own. A board that loses power
    forgets its frame, so a new count is the signal to draw again.
    """

    @property
    def link_state(self) -> LinkState: ...

    @property
    def connections(self) -> int: ...

    async def connect(self) -> None: ...

    async def push_frame(self, frame: Image.Image) -> None: ...

    async def close(self) -> None: ...


def validate_frame(frame: Image.Image, size: int) -> None:
    """Raise `ValueError` unless `frame` is an RGB image of `(size, size)`.

    Every implementation calls this first, so the frame invariant has exactly
    one definition.
    """
    if frame.mode != "RGB":
        raise ValueError(f"frame mode must be RGB, got {frame.mode!r}")
    if frame.size != (size, size):
        raise ValueError(f"frame size must be ({size}, {size}), got {frame.size}")


def frame_to_pixel_colors(frame: Image.Image) -> list[tuple[int, int, int]]:
    """Return the frame's pixels as row-major RGB triples.

    This is the order `show_image_pixeldata` expects: one colour per pixel,
    left to right, top row first. `get_flattened_data` is the non-deprecated
    spelling of `getdata`, available since Pillow 12.1.
    """
    return list(frame.get_flattened_data())
