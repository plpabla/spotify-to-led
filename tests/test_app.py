"""The service skeleton: the factory, the supervised tasks, and the routes.

The point of this module is the degradation behaviour. A board failure has to
be reported and survived, because the same process serves the observability
surface, so a transport whose `connect` raises must leave the route answering
while the display task keeps retrying it.
Spotify degrades the same way: without a client ID the app still starts and
its Spotify routes say so. With one, every Spotify request goes to a
`MockTransport`, so no test reaches the network.
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from spotify_to_led import display, frames, main
from spotify_to_led.board.factory import create_transport
from spotify_to_led.board.stub import PngBoardTransport
from spotify_to_led.board.transport import LinkState, frame_to_pixel_colors
from spotify_to_led.config import ENV_FILE_VARIABLE, Settings
from spotify_to_led.spotify.auth import AUTHORIZE_URL, SCOPE, TOKEN_URL

# `frames` is imported as a module, not as a symbol: a bare `test_pattern`
# name in this namespace would be collected as a test case.

A_MAC = "11:22:33:44:55:66"
A_CLIENT_ID = "0123456789abcdef0123456789abcdef"
A_REDIRECT_URI = "https://led.example.com/callback"
A_CODE = "AQD-the-authorization-code"
AN_ACCESS_TOKEN = "BQ-the-access-token"
A_REFRESH_TOKEN = "AQ-the-refresh-token"


class CountingTransport:
    """A `BoardTransport` that records calls instead of driving anything."""

    def __init__(self) -> None:
        self.frames: list[Image.Image] = []
        self.closed = False
        self.connects = 0
        # Connects that brought the link up, as `BoardTransport` counts them.
        self.connections = 0
        self.connect_attempted = threading.Event()
        self.pushed = threading.Event()
        self._link_state = LinkState.DOWN

    @property
    def pushes(self) -> int:
        return len(self.frames)

    @property
    def link_state(self) -> LinkState:
        return self._link_state

    async def connect(self) -> None:
        self.connects += 1
        self.connect_attempted.set()
        self._link_state = LinkState.UP
        self.connections += 1

    async def push_frame(self, frame: Image.Image) -> None:
        self.frames.append(frame)
        self.pushed.set()

    async def close(self) -> None:
        self.closed = True
        self._link_state = LinkState.DOWN


class FailingTransport(CountingTransport):
    """A board that cannot be reached, which is the case that matters.

    It fails its first `failing_connects` connects, or every one when that is
    `None`, and sets `retried` once a second attempt has been made.
    """

    def __init__(self, failing_connects: int | None = None) -> None:
        super().__init__()
        self.failing_connects = failing_connects
        self.retried = threading.Event()

    async def connect(self) -> None:
        self.connects += 1
        self.connect_attempted.set()
        if self.connects >= 2:
            self.retried.set()
        if self.failing_connects is None or self.connects <= self.failing_connects:
            raise OSError("the board is not reachable")
        self._link_state = LinkState.UP
        self.connections += 1


class FailingCloseTransport(CountingTransport):
    """A board whose teardown raises, as a link dying mid-shutdown would."""

    async def close(self) -> None:
        raise OSError("the link went away during shutdown")


def use_transport(
    monkeypatch: pytest.MonkeyPatch, transport: CountingTransport, tmp_path: Path
) -> None:
    """Make the lifespan build `transport`, under settings that touch no repo path."""
    # The lifespan reads the file this names; a developer's export of it must
    # not fail or reconfigure these tests.
    monkeypatch.delenv(ENV_FILE_VARIABLE, raising=False)
    monkeypatch.setenv("BOARD_TRANSPORT", "stub")
    monkeypatch.setenv("STUB_FRAME_DIR", str(tmp_path / "frames"))
    # The process environment beats `.env`, so a developer's real client ID
    # there cannot switch Spotify on; `use_spotify` switches it on explicitly.
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "")
    monkeypatch.setenv("SPOTIFY_TOKEN_FILE", str(tmp_path / "var" / "spotify-token.json"))
    monkeypatch.setenv("COVER_CACHE_DIR", str(tmp_path / "var" / "covers"))
    monkeypatch.setattr(main, "create_transport", lambda settings: transport)


class FakeSpotify:
    """Answers the token POST with a grant and every playback read with a track.

    It never runs out of answers, because the reader keeps reading every few
    seconds for as long as the app is up.
    """

    def __init__(self, *, refuse_grant: bool = False) -> None:
        self.refuse_grant = refuse_grant
        self.read = threading.Event()

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == TOKEN_URL:
            if self.refuse_grant:
                return httpx2.Response(503, json={"error": "server_error"})
            return httpx2.Response(
                200,
                json={
                    "access_token": AN_ACCESS_TOKEN,
                    "token_type": "Bearer",
                    "scope": SCOPE,
                    "expires_in": 3600,
                    "refresh_token": A_REFRESH_TOKEN,
                },
            )
        self.read.set()
        return httpx2.Response(
            200,
            json={
                "is_playing": True,
                "currently_playing_type": "track",
                "item": {
                    "id": "track-1",
                    "name": "A Track",
                    "artists": [{"name": "An Artist"}],
                    "album": {
                        "images": [{"url": "https://i.scdn.co/image/a", "width": 640, "height": 640}]
                    },
                },
            },
        )


def use_spotify(monkeypatch: pytest.MonkeyPatch, spotify: FakeSpotify) -> None:
    """Configure Spotify, with every request answered by `spotify`, never the network."""
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", A_CLIENT_ID)
    monkeypatch.setenv("SPOTIFY_REDIRECT_URI", A_REDIRECT_URI)
    monkeypatch.setattr(
        main,
        "create_spotify_client",
        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(spotify)),
    )


def state_of(authorize_response: httpx2.Response) -> str:
    return parse_qs(urlsplit(authorize_response.headers["location"]).query)["state"][0]


def use_short_display_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the display loop's waits, so retries and samples happen within a test."""
    monkeypatch.setattr(display, "CONNECT_RETRY_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(display, "CONNECT_RETRY_MAX_SECONDS", 0.02)
    monkeypatch.setattr(display, "SAMPLE_INTERVAL_SECONDS", 0.01)


def test_without_spotify_the_lifespan_pushes_the_idle_frame_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transport = CountingTransport()
    use_transport(monkeypatch, transport, tmp_path)
    use_short_display_delays(monkeypatch)
    monkeypatch.setenv("BOARD_SIZE", "16")

    with TestClient(main.app):
        assert transport.pushed.wait(timeout=5), "the display task never pushed"
        # Many sample intervals, so a re-push of the same frame would show.
        time.sleep(0.2)

    # Asserted after shutdown, which awaits the task: one frame, not one or more.
    assert transport.pushes == 1
    assert frame_to_pixel_colors(transport.frames[0]) == frame_to_pixel_colors(
        frames.idle(16)
    )
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


def test_a_failing_connect_is_retried_and_logged_while_healthz_answers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    transport = FailingTransport()
    use_transport(monkeypatch, transport, tmp_path)
    use_short_display_delays(monkeypatch)

    with caplog.at_level(logging.WARNING):
        with TestClient(main.app) as client:
            assert transport.retried.wait(timeout=5), "the failed connect was never retried"
            response = client.get("/healthz")

    assert response.status_code == 200
    assert transport.pushes == 0
    # A silently swallowed exception would pass the assertions above, which is
    # a worse bug than the crash it replaces.
    failures = [r for r in caplog.records if "board connect failed" in r.getMessage()]
    assert len(failures) >= 2, "every failed attempt must be logged"
    assert all(r.levelno == logging.WARNING for r in failures)
    assert failures[0].exc_info, "the first failure's traceback must reach the log"


def test_a_board_that_comes_up_later_is_connected_and_shows_idle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transport = FailingTransport(failing_connects=3)
    use_transport(monkeypatch, transport, tmp_path)
    use_short_display_delays(monkeypatch)

    with TestClient(main.app):
        assert transport.pushed.wait(timeout=5), "the display task never pushed"

    assert transport.connects == 4
    assert transport.pushes == 1


def test_shutdown_closes_the_transport_while_the_connect_is_being_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The real 5 s delay: shutdown arrives while the loop waits to retry.
    transport = FailingTransport()
    use_transport(monkeypatch, transport, tmp_path)

    with TestClient(main.app) as client:
        assert transport.connect_attempted.wait(timeout=5)
        assert client.get("/healthz").status_code == 200

    assert transport.connects == 1
    assert transport.closed, "shutdown must close the transport mid-retry"


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


def test_without_a_client_id_the_app_serves_and_the_spotify_routes_say_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)

    with TestClient(main.app, follow_redirects=False) as client:
        healthz = client.get("/healthz")
        authorize = client.get("/authorize")
        callback = client.get("/callback", params={"code": A_CODE, "state": "any-state"})
        status = client.get("/status")

    assert healthz.status_code == 200
    assert authorize.status_code == 503
    assert callback.status_code == 503
    assert status.status_code == 200
    body = status.json()
    assert body["configured"] is False
    assert body["playback"] is None
    assert body["authorization"] is None
    # The board is reported whether or not Spotify is configured.
    assert set(body["board"]) == {"link", "task_running", "presentation", "push_failed"}
    assert body["board"]["task_running"] is True
    assert body["stale_after_seconds"] == display.STALE_AFTER_SECONDS
    assert body["now"] is not None


def test_status_reports_the_link_down_once_the_display_task_has_ended(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The transport still believes the link is up, but with the task gone
    # nothing would reconnect it, so the page must not say "up".
    transport = CountingTransport()
    use_transport(monkeypatch, transport, tmp_path)

    async def connect_and_end(board: CountingTransport, *args: object, **kwargs: object) -> None:
        await board.connect()

    monkeypatch.setattr(main, "run_display", connect_and_end)

    with TestClient(main.app) as client:
        deadline = time.monotonic() + 5
        board = client.get("/status").json()["board"]
        while board["task_running"] and time.monotonic() < deadline:
            time.sleep(0.05)
            board = client.get("/status").json()["board"]
        # Checked before shutdown, which closes the transport.
        assert transport.link_state is LinkState.UP

    assert board["task_running"] is False
    assert board["link"] == "down"


def test_authorize_redirects_to_spotify_with_the_configured_redirect_uri(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_spotify(monkeypatch, FakeSpotify())

    with TestClient(main.app, follow_redirects=False) as client:
        response = client.get("/authorize")

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(f"{AUTHORIZE_URL}?")
    query = parse_qs(urlsplit(location).query)
    # Verbatim from settings, never derived from the request's own host.
    assert query["redirect_uri"] == [A_REDIRECT_URI]
    assert query["client_id"] == [A_CLIENT_ID]


def test_a_callback_with_a_bad_state_is_rejected_without_an_exchange(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spotify = FakeSpotify()
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_spotify(monkeypatch, spotify)

    with TestClient(main.app, follow_redirects=False) as client:
        client.get("/authorize")
        forged = client.get("/callback", params={"code": A_CODE, "state": "a-forged-state"})
        missing = client.get("/callback", params={"code": A_CODE})
        authorization = client.get("/status").json()["authorization"]

    assert (forged.status_code, missing.status_code) == (400, 400)
    assert A_CODE not in forged.text
    assert authorization == {"authorized_at": None, "expires_around": None}
    assert not (tmp_path / "var" / "spotify-token.json").exists()


def test_a_callback_carrying_spotifys_error_is_a_400_that_names_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_spotify(monkeypatch, FakeSpotify())

    with TestClient(main.app, follow_redirects=False) as client:
        state = state_of(client.get("/authorize"))
        response = client.get("/callback", params={"error": "access_denied", "state": state})

    assert response.status_code == 400
    assert "access_denied" in response.text


def test_a_callback_error_that_is_not_a_spotify_code_is_not_echoed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_spotify(monkeypatch, FakeSpotify())
    crafted = "Your account is suspended, call 555-0100"

    with TestClient(main.app, follow_redirects=False) as client:
        response = client.get("/callback", params={"error": crafted})

    assert response.status_code == 400
    assert crafted not in response.text
    assert "unknown error" in response.text


def test_an_undecodable_token_answer_is_a_502_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def undecodable(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.DecodingError("bad gzip stream", request=request)

    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_spotify(monkeypatch, FakeSpotify())
    monkeypatch.setattr(
        main,
        "create_spotify_client",
        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(undecodable)),
    )

    with TestClient(main.app, follow_redirects=False) as client:
        state = state_of(client.get("/authorize"))
        response = client.get("/callback", params={"code": A_CODE, "state": state})

    assert response.status_code == 502


def test_a_refused_exchange_is_a_502(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_spotify(monkeypatch, FakeSpotify(refuse_grant=True))

    with TestClient(main.app, follow_redirects=False) as client:
        state = state_of(client.get("/authorize"))
        response = client.get("/callback", params={"code": A_CODE, "state": state})

    assert response.status_code == 502
    assert A_CODE not in response.text


def test_authorize_callback_and_status_end_to_end_with_no_token_in_any_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spotify = FakeSpotify()
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_spotify(monkeypatch, spotify)

    with TestClient(main.app, follow_redirects=False) as client:
        state = state_of(client.get("/authorize"))
        callback = client.get("/callback", params={"code": A_CODE, "state": state})
        assert spotify.read.wait(timeout=5), "the reader never read after authorizing"
        # The read is answered before the reader records it, so wait for the
        # snapshot rather than for the request.
        deadline = time.monotonic() + 5
        status = client.get("/status")
        while status.json()["playback"]["status"] != "playing" and time.monotonic() < deadline:
            time.sleep(0.05)
            status = client.get("/status")

    assert callback.status_code == 200
    assert "succeeded" in callback.text
    assert A_CODE not in callback.text
    body = status.json()
    assert set(body) == {
        "configured",
        "playback",
        "authorization",
        "board",
        "stale_after_seconds",
        "now",
    }
    assert body["configured"] is True
    playback = body["playback"]
    assert set(playback) == {"status", "item", "failure", "failure_since", "last_read_at"}
    assert playback["status"] == "playing"
    assert playback["failure"] is None
    assert playback["item"]["id"] == "track-1"
    assert playback["item"]["artwork"] == [
        {"url": "https://i.scdn.co/image/a", "width": 640, "height": 640}
    ]
    authorized_at = datetime.fromisoformat(body["authorization"]["authorized_at"])
    expires_around = datetime.fromisoformat(body["authorization"]["expires_around"])
    assert expires_around - authorized_at == timedelta(days=182)
    assert body["board"]["link"] == "up"
    assert body["board"]["task_running"] is True
    for secret in (AN_ACCESS_TOKEN, A_REFRESH_TOKEN, A_CODE):
        assert secret not in status.text
    assert (tmp_path / "var" / "spotify-token.json").exists(), "the grant must be saved"


def test_the_access_log_drops_the_callback_query_and_its_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # TestClient bypasses uvicorn, so the line is logged the way uvicorn's
    # h11 protocol logs it: the path with its query is the third argument.
    main.configure_logging("INFO")
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:5000",
            "GET",
            f"/callback?code={A_CODE}&state=s",
            "1.1",
            200,
        )

    assert A_CODE not in caplog.text
    assert '"GET /callback HTTP/1.1" 200' in caplog.text


def test_httpx2_does_not_log_every_poll() -> None:
    main.configure_logging("INFO")

    assert not logging.getLogger("httpx2").isEnabledFor(logging.INFO)


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
