"""The real board transport, over the iDotMatrix fork's BLE client.

The adapter is thin on purpose: the fork owns connection management, chunking
and acknowledgements, so this module only passes the right construction
arguments and translates the fork's connection events into `LinkState`.

Recovery boundary, recorded so the next reader does not have to re-derive it:
`set_auto_reconnect(True)` plus `set_heartbeat(...)` covers a link that drops
after a successful connection, retried by the library's own bounded backoff
(5 seconds, growing to at most 60). The library's reconnect loop starts from
the disconnect callback only, so it does *not* retry a failed initial connect.
The cold-boot case -- the service starting before the board is reachable --
therefore remains an open item for roadmap items S-02 and S-03, and nothing
here should be read as satisfying FR-013.
"""

import asyncio
import logging

from idotmatrix import IDotMatrixClient, ScreenSize
from idotmatrix.connection_manager import ConnectionListener
from PIL import Image

from spotify_to_led.board.transport import (
    LinkState,
    frame_to_pixel_colors,
    validate_frame,
)

logger = logging.getLogger(__name__)

# Well inside pm2's 10 s `kill_timeout`. With the board unreachable,
# `disconnect` can wait behind the library's in-flight reconnect attempt for
# about 30 s, and pm2 would SIGKILL the process before uvicorn finished.
DISCONNECT_TIMEOUT_SECONDS = 5.0


class IDotMatrixBoardTransport:
    """Pushes frames to an iDotMatrix board over Bluetooth."""

    def __init__(
        self, mac_address: str, size: int, heartbeat_seconds: float | None
    ) -> None:
        self._size = size
        self._link_state = LinkState.DOWN
        self._client = IDotMatrixClient(
            # Passing the size skips the `detect_screen_size` round trip that
            # `connect` would otherwise make.
            screen_size=ScreenSize((size, size)),
            mac_address=mac_address,
            # uvicorn and pm2 own SIGINT and SIGTERM; letting the library
            # install its own handlers would make two parties race for them.
            install_signal_handlers=False,
        )
        self._client.add_connection_listener(
            ConnectionListener(
                on_connected=self._on_connected,
                on_disconnected=self._on_disconnected,
            )
        )
        self._client.set_auto_reconnect(True)
        self._client.set_heartbeat(heartbeat_seconds)

    @property
    def link_state(self) -> LinkState:
        return self._link_state

    async def _on_connected(self) -> None:
        self._link_state = LinkState.UP

    async def _on_disconnected(self) -> None:
        self._link_state = LinkState.DOWN

    async def connect(self) -> None:
        # Failures propagate to the lifespan's task, which is the one place
        # that logs them. Link state is left to the listener callbacks, so it
        # stays right across the library's own reconnect cycles too.
        await self._client.connect()

    async def push_frame(self, frame: Image.Image) -> None:
        validate_frame(frame, self._size)
        await self._client.image.show_image_pixeldata(frame_to_pixel_colors(frame))

    async def close(self) -> None:
        # `disconnect` is safe when there was never a connection, so this
        # tolerates being called from the lifespan's `finally` after a failed
        # `connect`. The wait is bounded so an unreachable board cannot hold
        # up shutdown past the supervisor's kill window.
        try:
            async with asyncio.timeout(DISCONNECT_TIMEOUT_SECONDS):
                await self._client.disconnect()
        except TimeoutError:
            logger.warning(
                "board disconnect did not finish within %s s; shutting down anyway",
                DISCONNECT_TIMEOUT_SECONDS,
            )
        self._link_state = LinkState.DOWN
