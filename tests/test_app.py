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


def test_the_factory_refuses_the_idotmatrix_transport_for_now() -> None:
    settings = Settings(_env_file=None, board_transport="idotmatrix", idot_mac=A_MAC)

    with pytest.raises(NotImplementedError):
        create_transport(settings)
