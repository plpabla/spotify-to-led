"""Maps settings to a transport instance.

The choice lives in one function so the application never imports a concrete
transport, and so a development machine never pays for the hardware one.
"""

from spotify_to_led.board.stub import PngBoardTransport
from spotify_to_led.board.transport import BoardTransport
from spotify_to_led.config import Settings


def create_transport(settings: Settings) -> BoardTransport:
    """Return the transport `settings.board_transport` names."""
    if settings.board_transport == "stub":
        return PngBoardTransport(settings.stub_frame_dir, settings.board_size)

    # The real adapter gets constructed here, with its module imported inside
    # this branch rather than at module level, so a development machine with
    # no working Bluetooth stack never imports bleak.
    raise NotImplementedError("the idotmatrix transport is not implemented yet")
