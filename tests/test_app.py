"""The service skeleton: the factory, the supervised task, and `/healthz`.

The point of this module is the degradation behaviour. A board failure has to
be reported and survived, because the same process serves the observability
surface, so a transport whose `connect` raises must leave the route answering.
"""

import logging
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from spotify_to_led import frames, main
from spotify_to_led.board.factory import create_transport
from spotify_to_led.board.stub import PngBoardTransport
from spotify_to_led.board.transport import LinkState
from spotify_to_led.config import Settings

# `frames` is imported as a module, not as a symbol: a bare `test_pattern`
# name in this namespace would be collected as a test case.

A_MAC = "11:22:33:44:55:66"


class CountingTransport:
    """A `BoardTransport` that records calls instead of driving anything."""

    def __init__(self) -> None:
        self.pushes = 0
        self.closed = False
        self.connect_attempted = threading.Event()
        self.pushed = threading.Event()
        self._link_state = LinkState.DOWN

    @property
    def link_state(self) -> LinkState:
        return self._link_state

    async def connect(self) -> None:
        self.connect_attempted.set()
        self._link_state = LinkState.UP

    async def push_frame(self, frame: Image.Image) -> None:
        self.pushes += 1
        self.pushed.set()

    async def close(self) -> None:
        self.closed = True
        self._link_state = LinkState.DOWN


class FailingTransport(CountingTransport):
    """A board that cannot be reached, which is the case that matters."""

    async def connect(self) -> None:
        self.connect_attempted.set()
        raise OSError("the board is not reachable")


class FailingCloseTransport(CountingTransport):
    """A board whose teardown raises, as a link dying mid-shutdown would."""

    async def close(self) -> None:
        raise OSError("the link went away during shutdown")


def use_transport(
    monkeypatch: pytest.MonkeyPatch, transport: CountingTransport, tmp_path: Path
) -> None:
    """Make the lifespan build `transport`, under settings that touch no repo path."""
    monkeypatch.setenv("BOARD_TRANSPORT", "stub")
    monkeypatch.setenv("STUB_FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setattr(main, "create_transport", lambda settings: transport)


def test_the_lifespan_pushes_exactly_one_frame(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transport = CountingTransport()
    use_transport(monkeypatch, transport, tmp_path)

    with TestClient(main.app):
        assert transport.pushed.wait(timeout=5), "the background task never pushed"

    # Asserted after shutdown, which awaits the task: one frame, not one or more.
    assert transport.pushes == 1
    assert transport.closed, "shutdown must close the transport"


def test_healthz_returns_a_fixed_ok_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transport = CountingTransport()
    use_transport(monkeypatch, transport, tmp_path)

    with TestClient(main.app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    # Deliberately not link state, the playing item or re-authorization: that
    # is FR-015, in S-07.
    assert response.json() == {"status": "ok"}


def test_a_failing_connect_leaves_healthz_answering_and_is_logged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    transport = FailingTransport()
    use_transport(monkeypatch, transport, tmp_path)

    with caplog.at_level(logging.ERROR):
        with TestClient(main.app) as client:
            assert transport.connect_attempted.wait(timeout=5)
            response = client.get("/healthz")

    assert response.status_code == 200
    assert transport.pushes == 0
    # A silently swallowed exception would pass the assertion above, which is a
    # worse bug than the crash it replaces.
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert errors, "the board failure must be logged at error level"
    assert errors[0].exc_info is not None, "the traceback must reach the log"


def test_a_failing_close_does_not_escape_the_lifespan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Escaping here would mean a shutdown traceback in the journal on every
    # unclean teardown, in the exact log the unattended run is read from.
    transport = FailingCloseTransport()
    use_transport(monkeypatch, transport, tmp_path)

    with caplog.at_level(logging.ERROR):
        with TestClient(main.app) as client:
            assert client.get("/healthz").status_code == 200

    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert errors, "the teardown failure must be logged at error level"
    assert errors[0].exc_info is not None, "the traceback must reach the log"


def test_the_factory_wires_the_stub_to_the_configured_directory_and_size(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        board_transport="stub",
        stub_frame_dir=tmp_path / "frames",
        board_size=16,
    )

    transport = create_transport(settings)

    assert isinstance(transport, PngBoardTransport)
    assert not (tmp_path / "frames").exists(), (
        "construction alone must not touch the filesystem"
    )


async def test_the_factory_builds_a_stub_that_writes_where_settings_say(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        board_transport="stub",
        stub_frame_dir=tmp_path / "frames",
        board_size=16,
    )
    transport = create_transport(settings)

    await transport.connect()
    await transport.push_frame(frames.test_pattern(settings.board_size))

    with Image.open(tmp_path / "frames" / "latest.png") as image:
        assert image.size == (16, 16)


def test_the_factory_wires_the_idotmatrix_adapter_without_touching_bluetooth() -> None:
    # Imported here for the same reason the factory does it: the hardware
    # adapter's module is only reachable when its branch is exercised.
    from spotify_to_led.board.idotmatrix_transport import IDotMatrixBoardTransport

    settings = Settings(
        _env_file=None,
        board_transport="idotmatrix",
        idot_mac=A_MAC,
        board_size=16,
    )

    transport = create_transport(settings)

    assert isinstance(transport, IDotMatrixBoardTransport)
    assert transport._client.mac_address == A_MAC
    assert transport._client.screen_size.value == (16, 16)
    # Nothing connected, scanned or awaited: the link is only reported up once
    # the library's own connection listener fires.
    assert transport.link_state is LinkState.DOWN
