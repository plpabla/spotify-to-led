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


class FailingLink:
    """A library transport whose connect raises `error`."""

    def __init__(self, error: Exception, *, connected: bool = False) -> None:
        self.error = error
        self.is_connected = connected

    async def connect(self) -> None:
        raise self.error


def use_links(transport: IDotMatrixBoardTransport, *links: FailingLink) -> list[FailingLink]:
    """Make the library build `links`, in order, whenever it needs a fresh link."""
    manager = transport._client._connection_manager
    pending = list(links)
    built: list[FailingLink] = []

    def factory(target: object, on_disconnected: object) -> FailingLink:
        built.append(pending.pop(0))
        return built[-1]

    manager._transport_factory = factory
    # Drop the real bleak link built at construction, so the next connect
    # asks the factory.
    manager._transport = None
    return built


async def test_a_failed_connect_makes_the_next_attempt_use_a_fresh_link() -> None:
    # Reusing the failed link is what left the board dark after a cold boot:
    # its remembered BlueZ device path was gone, so every retry failed at once.
    transport = make_transport()
    built = use_links(
        transport,
        FailingLink(TimeoutError()),
        FailingLink(ConnectionRefusedError()),
    )

    with pytest.raises(TimeoutError):
        await transport.connect()
    # The retry reaches the second link; reusing the first would time out again.
    with pytest.raises(ConnectionRefusedError):
        await transport.connect()

    assert len(built) == 2


async def test_a_drop_makes_the_librarys_reconnect_use_a_fresh_link() -> None:
    # The library's own reconnect loop retries with whatever link the manager
    # holds, so a drop has to discard it just as a failed connect does.
    transport = make_transport()
    manager = transport._client._connection_manager
    manager._transport = FailingLink(TimeoutError())  # type: ignore[assignment]

    await transport._on_disconnected()

    assert manager._transport is None
    assert transport.link_state is LinkState.DOWN


async def test_a_disconnect_that_finds_the_link_back_up_keeps_it() -> None:
    # The library's reconnect can win the race to the listener; a live link
    # must never be thrown away.
    transport = make_transport()
    manager = transport._client._connection_manager
    live = FailingLink(TimeoutError(), connected=True)
    manager._transport = live  # type: ignore[assignment]

    await transport._on_disconnected()

    assert manager._transport is live


async def test_close_disconnects_a_responsive_client() -> None:
    transport = make_transport()
    client = PromptClient()
    transport._client = client  # type: ignore[assignment]

    await transport.close()

    assert client.disconnected
    assert transport.link_state is LinkState.DOWN
