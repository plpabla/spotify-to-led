"""The real board transport, over the iDotMatrix fork's BLE client.

The adapter is thin on purpose: the fork owns connection management, chunking
and acknowledgements, so this module only passes the right construction
arguments and translates the fork's connection events into `LinkState`.

Recovery boundary, recorded so the next reader does not have to re-derive it:
the library's heartbeat (`set_heartbeat`) detects a link that died silently
and reports it as a disconnect, which sets `LinkState.DOWN`. The library's
own auto-reconnect is off: its loop reuses the Bluetooth client built by its
first attempt for every later one, so a stale BlueZ device path could strand
the link for good. Every connect, the first one and every one after a drop,
is retried by the display loop (`display.run_display`) instead, and a failed
connect always discards the client (see `_discard_stale_link`), so each
retry scans afresh. A board that loses power comes back blank, so every time
the link comes up `connections` grows, and the display loop draws again on
that.
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
# `disconnect` can wait behind an in-flight connect attempt for
# about 30 s, and pm2 would SIGKILL the process before uvicorn finished.
DISCONNECT_TIMEOUT_SECONDS = 5.0


class IDotMatrixBoardTransport:
    """Pushes frames to an iDotMatrix board over Bluetooth."""

    def __init__(
        self, mac_address: str, size: int, heartbeat_seconds: float | None
    ) -> None:
        self._size = size
        self._link_state = LinkState.DOWN
        self._connections = 0
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
        # The display loop is the only party that reconnects; see the module
        # docstring for why the library's own loop cannot be trusted with it.
        self._client.set_auto_reconnect(False)
        self._client.set_heartbeat(heartbeat_seconds)

    @property
    def link_state(self) -> LinkState:
        return self._link_state

    @property
    def connections(self) -> int:
        return self._connections

    def _mark_up(self) -> None:
        # Counted on the DOWN-to-UP edge only. Our own `connect` and the
        # library's listener both report the same first connection, in either
        # order; counting it twice would make the display draw it twice.
        if self._link_state is LinkState.DOWN:
            self._link_state = LinkState.UP
            self._connections += 1

    async def _on_connected(self) -> None:
        self._mark_up()

    async def _on_disconnected(self) -> None:
        self._link_state = LinkState.DOWN
        # A drop the heartbeat found or bleak reported leaves the client
        # holding a BlueZ path that may be gone; the display loop's next
        # connect must scan afresh, not reuse it.
        self._discard_stale_link()

    async def connect(self) -> None:
        # Failures propagate to the display loop, which is the one place
        # that logs and retries them.
        try:
            await self._client.connect()
        except Exception:
            self._discard_stale_link()
            raise
        # The listener runs later, as a scheduled task; marking the link up
        # here means the count is current before the first frame is chosen.
        self._mark_up()

    def _discard_stale_link(self) -> None:
        """Make the next connect build a fresh Bluetooth client and scan again.

        bleak remembers the BlueZ device path the first time a scan finds the
        board, and the library reuses that client for every later connect.
        Once the link fails or drops, BlueZ can remove the device object, and
        every retry then fails at once with "device ... not found" without
        scanning again -- seen on the Pi as a board switched on after a cold
        boot that never came up. The library does the same reset for a link
        its heartbeat finds dead. Its manager has no public way to do it, so
        this sets a private field; the library is pinned to exactly 0.3.0,
        and a version bump must re-check it.
        """
        manager = self._client._connection_manager
        # Never discard a link that is up: a connect may have won the race to
        # this point.
        if not manager.is_connected():
            manager._transport = None

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
