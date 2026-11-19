"""The real board adapter's shutdown path, without touching Bluetooth.

Constructing the adapter only builds the library's client; nothing connects
until `connect` is awaited. The client is then swapped for a fake, so these
tests run on a machine with no Bluetooth stack.
"""

import asyncio
import logging

import pytest
from idotmatrix.connection_manager import UploadError

from spotify_to_led import frames
from spotify_to_led.board import idotmatrix_transport
from spotify_to_led.board.idotmatrix_transport import IDotMatrixBoardTransport
from spotify_to_led.board.transport import BoardRejectedUpload, LinkState


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


class WorkingLink:
    """A library transport whose connect succeeds, until `drop` loses it."""

    def __init__(self) -> None:
        self.is_connected = False

    async def connect(self) -> None:
        self.is_connected = True

    async def start_notify(self, callback: object) -> None:
        pass

    async def disconnect(self) -> None:
        self.is_connected = False


type Link = FailingLink | WorkingLink


def use_links(transport: IDotMatrixBoardTransport, *links: Link) -> list[Link]:
    """Make the library build `links`, in order, whenever it needs a fresh link."""
    manager = transport._client._connection_manager
    pending = list(links)
    built: list[Link] = []

    def factory(target: object, on_disconnected: object) -> Link:
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


async def test_a_drop_makes_the_next_connect_use_a_fresh_link() -> None:
    # The library retries with whatever link the manager holds, so a drop has
    # to discard it just as a failed connect does.
    transport = make_transport()
    manager = transport._client._connection_manager
    manager._transport = FailingLink(TimeoutError())  # type: ignore[assignment]

    await transport._on_disconnected()

    assert manager._transport is None
    assert transport.link_state is LinkState.DOWN


async def test_a_disconnect_that_finds_the_link_back_up_keeps_it() -> None:
    # A connect can win the race to the listener; a live link must never be
    # thrown away.
    transport = make_transport()
    manager = transport._client._connection_manager
    live = FailingLink(TimeoutError(), connected=True)
    manager._transport = live  # type: ignore[assignment]

    await transport._on_disconnected()

    assert manager._transport is live


async def test_a_library_reported_drop_marks_the_link_down_and_starts_no_reconnect_loop() -> None:
    # The library's loop reuses the link its first attempt built, so it could
    # strand the board; the display loop is the only party that reconnects.
    transport = make_transport()
    manager = transport._client._connection_manager
    assert manager._auto_reconnect is False
    built = use_links(transport, WorkingLink())
    await transport.connect()

    built[0].is_connected = False
    manager._handle_disconnect()
    # The library schedules the listener; let it run.
    await asyncio.sleep(0)

    assert manager._reconnect_loop_task is None
    assert transport.link_state is LinkState.DOWN


async def test_each_connect_after_a_drop_uses_a_fresh_link() -> None:
    # The display loop's path after a drop: every connect, failed or not,
    # starts from a fresh link, so a stale BlueZ path cannot strand the board.
    # The library's own loop coming back is guarded by
    # `test_a_library_reported_drop_marks_the_link_down_and_starts_no_reconnect_loop`.
    transport = make_transport()
    built = use_links(
        transport,
        WorkingLink(),
        FailingLink(TimeoutError()),
        FailingLink(TimeoutError()),
        FailingLink(TimeoutError()),
        WorkingLink(),
    )
    await transport.connect()
    built[0].is_connected = False
    transport._client._connection_manager._handle_disconnect()
    await asyncio.sleep(0)

    for _ in range(3):
        with pytest.raises(TimeoutError):
            await transport.connect()
    await transport.connect()

    after_drop = built[1:]
    assert len(after_drop) == 4
    assert len({id(link) for link in after_drop}) == 4
    assert transport.connections == 2
    assert transport.link_state is LinkState.UP
    await transport.close()


async def test_close_disconnects_a_responsive_client() -> None:
    transport = make_transport()
    client = PromptClient()
    transport._client = client  # type: ignore[assignment]

    await transport.close()

    assert client.disconnected
    assert transport.link_state is LinkState.DOWN


async def test_each_time_the_link_comes_up_is_counted_once() -> None:
    # The first connection is reported twice, by our `connect` and by the
    # library's listener; counting it twice would draw the first frame twice.
    transport = make_transport()
    manager = transport._client._connection_manager
    manager._transport = FailingLink(TimeoutError())  # type: ignore[assignment]

    transport._mark_up()
    await transport._on_connected()
    assert transport.connections == 1

    await transport._on_disconnected()
    await transport._on_connected()

    assert transport.connections == 2
    assert transport.link_state is LinkState.UP


class RaisingImage:
    """An image module whose upload raises `error`."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def show_image_pixeldata(self, pixels: object) -> None:
        raise self.error


class UploadClient:
    def __init__(self, error: Exception) -> None:
        self.image = RaisingImage(error)


async def test_a_refused_upload_becomes_board_rejected_upload() -> None:
    # The message is the 0.3.0 library's; a refusal is the board answering no.
    transport = make_transport()
    refusal = UploadError("the device rejected the upload (notification 0500010003)")
    transport._client = UploadClient(refusal)  # type: ignore[assignment]

    with pytest.raises(BoardRejectedUpload) as caught:
        await transport.push_frame(frames.test_pattern(32))

    assert caught.value.__cause__ is refusal


@pytest.mark.parametrize(
    "error",
    [
        UploadError("the device did not acknowledge the upload within 5 seconds"),
        OSError("link lost"),
    ],
)
async def test_an_upload_that_was_not_refused_passes_through_unchanged(
    error: Exception,
) -> None:
    # A board that never answered, or a link that failed, is not a refusal.
    transport = make_transport()
    transport._client = UploadClient(error)  # type: ignore[assignment]

    with pytest.raises(type(error)) as caught:
        await transport.push_frame(frames.test_pattern(32))

    assert caught.value is error


class ResetCalls:
    """Records the order `clear` and `reset` are awaited in."""

    def __init__(self) -> None:
        self.calls: list[str] = []


class FakeMaterials:
    def __init__(self, record: ResetCalls, behaviour: str) -> None:
        self.record = record
        self.behaviour = behaviour

    async def clear(self) -> None:
        self.record.calls.append("clear")
        if self.behaviour == "raise":
            raise UploadError("the device rejected the upload (notification 05)")
        if self.behaviour == "hang":
            await asyncio.Event().wait()


class DroppedManager:
    """The private manager `_discard_stale_link` reads, for a link that is down."""

    def __init__(self) -> None:
        self._transport: object | None = object()

    def is_connected(self) -> bool:
        return False


class ResetClient:
    def __init__(
        self, *, clear: str = "ok", reset_error: Exception | None = None
    ) -> None:
        self.record = ResetCalls()
        self.materials = FakeMaterials(self.record, clear)
        self.reset_error = reset_error
        self._connection_manager = DroppedManager()

    async def reset(self) -> None:
        self.record.calls.append("reset")
        if self.reset_error is not None:
            raise self.reset_error


async def test_reset_clears_the_materials_then_resets_the_board() -> None:
    transport = make_transport()
    client = ResetClient()
    transport._client = client  # type: ignore[assignment]

    await transport.reset()

    assert client.record.calls == ["clear", "reset"]


async def test_reset_still_resets_when_clearing_the_materials_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = make_transport()
    client = ResetClient(clear="raise")
    transport._client = client  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING):
        await transport.reset()

    assert client.record.calls == ["clear", "reset"]
    assert "clearing the board's stored materials failed" in caplog.text


async def test_reset_still_resets_when_clearing_the_materials_never_returns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(idotmatrix_transport, "RESET_STEP_TIMEOUT_SECONDS", 0.05)
    transport = make_transport()
    client = ResetClient(clear="hang")
    transport._client = client  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING):
        async with asyncio.timeout(2):
            await transport.reset()

    assert client.record.calls == ["clear", "reset"]
    assert "clearing the board's stored materials failed" in caplog.text


async def test_a_failed_board_reset_reaches_the_caller() -> None:
    transport = make_transport()
    client = ResetClient(reset_error=OSError("link lost"))
    transport._client = client  # type: ignore[assignment]

    with pytest.raises(OSError, match="link lost"):
        await transport.reset()

    assert client.record.calls == ["clear", "reset"]
    # As after a failed connect, the next connect scans afresh.
    assert client._connection_manager._transport is None
