"""The real board adapter's shutdown path, without touching Bluetooth.

Constructing the adapter only builds the library's client; nothing connects
until `connect` is awaited. The client is then swapped for a fake, so these
tests run on a machine with no Bluetooth stack.
"""

import asyncio
import logging

import pytest

from spotify_to_led.board import idotmatrix_transport
from spotify_to_led.board.idotmatrix_transport import IDotMatrixBoardTransport
from spotify_to_led.board.transport import LinkState


class HangingClient:
    """A client whose disconnect never returns, like one stuck behind a reconnect."""

    async def disconnect(self) -> None:
        await asyncio.Event().wait()


class PromptClient:
    def __init__(self) -> None:
        self.disconnected = False

    async def disconnect(self) -> None:
        self.disconnected = True


def make_transport() -> IDotMatrixBoardTransport:
    return IDotMatrixBoardTransport("11:22:33:44:55:66", 32, 30.0)


async def test_close_gives_up_on_a_disconnect_that_never_returns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A hung disconnect used to hold shutdown until pm2's kill_timeout ran out
    # and the process was SIGKILLed.
    monkeypatch.setattr(idotmatrix_transport, "DISCONNECT_TIMEOUT_SECONDS", 0.05)
    transport = make_transport()
    transport._client = HangingClient()  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING):
        async with asyncio.timeout(2):
            await transport.close()

    assert transport.link_state is LinkState.DOWN
    assert "board disconnect did not finish" in caplog.text


class TimingOutLink:
    """A library transport whose connect fails, as a board still booting does."""

    is_connected = False

    async def connect(self) -> None:
        raise TimeoutError


async def test_a_failed_connect_makes_the_next_attempt_use_a_fresh_link() -> None:
    # Reusing the failed link is what left the board dark after a cold boot:
    # its remembered BlueZ device path was gone, so every retry failed at once.
    transport = make_transport()
    manager = transport._client._connection_manager
    stale = TimingOutLink()
    manager._transport = stale  # type: ignore[assignment]
    manager._transport_created_for = transport._client.mac_address

    with pytest.raises(TimeoutError):
        await transport.connect()

    assert manager._transport is not stale
    assert manager._create_transport() is not stale


async def test_close_disconnects_a_responsive_client() -> None:
    transport = make_transport()
    client = PromptClient()
    transport._client = client  # type: ignore[assignment]

    await transport.close()

    assert client.disconnected
    assert transport.link_state is LinkState.DOWN
