"""A board transport that writes frames to a PNG instead of to hardware.

This is the FR-016 development loop: full fidelity on everything except the
transport itself. The application points it at `var/frames`, which is already
gitignored.
"""

import os
from pathlib import Path

from PIL import Image

from spotify_to_led.board.transport import LinkState, validate_frame

FRAME_FILENAME = "latest.png"
TEMP_FILENAME = ".latest.png.tmp"


class PngBoardTransport:
    """Writes each pushed frame to `output_dir / "latest.png"`."""

    def __init__(self, output_dir: Path, size: int) -> None:
        self._output_dir = output_dir
        self._size = size
        self._link_state = LinkState.DOWN
        self._connections = 0

    @property
    def link_state(self) -> LinkState:
        return self._link_state

    @property
    def connections(self) -> int:
        return self._connections

    async def connect(self) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._link_state = LinkState.UP
        self._connections += 1

    async def push_frame(self, frame: Image.Image) -> None:
        validate_frame(frame, self._size)
        # Write beside the target and replace it, so the image viewer the
        # development loop keeps open never reads a half-written PNG. The
        # temporary file sits in the same directory because os.replace is only
        # atomic within one filesystem.
        temp_path = self._output_dir / TEMP_FILENAME
        frame.save(temp_path, format="PNG")
        os.replace(temp_path, self._output_dir / FRAME_FILENAME)

    async def close(self) -> None:
        self._link_state = LinkState.DOWN
