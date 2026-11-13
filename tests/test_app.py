"""The service skeleton: the factory, the supervised tasks, and the routes.

The point of this module is the degradation behaviour. A board failure has to
be reported and survived, because the same process serves the observability
surface, so a transport whose `connect` raises must leave the route answering
while the display task keeps retrying it.
Spotify degrades the same way: without a client ID the app still starts and
its Spotify routes say so. With one, every Spotify request goes to a
`MockTransport`, so no test reaches the network.
"""

import asyncio
import json
import logging
import threading
import time
from datetime import UTC, datetime, timedelta
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
from spotify_to_led.covers import CoverState, CoverStatus
from spotify_to_led.enhance import Enhancer
from spotify_to_led.owner_settings import OwnerSettings, OwnerSettingsStore
from spotify_to_led.spend import SpendLedger
from spotify_to_led.spotify.auth import AUTHORIZE_URL, SCOPE, TOKEN_URL
from spotify_to_led.spotify.playback import (
    Artwork,
    PlaybackSnapshot,
    PlaybackStatus,
    PlayingItem,
    ReadFailure,
)

# `frames` is imported as a module, not as a symbol: a bare `test_pattern`
# name in this namespace would be collected as a test case.

A_MAC = "11:22:33:44:55:66"
A_CLIENT_ID = "0123456789abcdef0123456789abcdef"
A_REDIRECT_URI = "https://led.example.com/callback"
A_CODE = "AQD-the-authorization-code"
AN_ACCESS_TOKEN = "BQ-the-access-token"
A_REFRESH_TOKEN = "AQ-the-refresh-token"
AN_OPENROUTER_KEY = "sk-or-v1-the-openrouter-key"


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
    monkeypatch.setenv("OWNER_SETTINGS_FILE", str(tmp_path / "var" / "settings.json"))
    monkeypatch.setenv("AI_SPEND_FILE", str(tmp_path / "var" / "ai-spend.json"))
    # Like the client ID: a developer's real key in `.env` must not make
    # enhancement available; `use_openrouter_key` does that explicitly.
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
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


def use_openrouter_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure an OpenRouter key, which makes enhancement available."""
    monkeypatch.setenv("OPENROUTER_API_KEY", AN_OPENROUTER_KEY)


def idle_enhancement(available: bool, enabled: bool) -> dict[str, object]:
    """`/status`'s enhancement with no pause and no cover, as it is without Spotify."""
    return {
        "available": available,
        "enabled": enabled,
        "paused_until": None,
        "cover": None,
        "spend": no_spend(),
    }


def no_spend(limit: float = 1.0) -> dict[str, object]:
    """`/status`'s spend without an enhancer: the limit only, nothing spent or reached."""
    return {"spent_usd": None, "limit_usd": limit, "limit_reached": False, "resets_at": None}


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


def test_the_status_page_is_served_and_renders_status_without_innerhtml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)

    with TestClient(main.app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    # The calls themselves, not a mention of the paths in a comment.
    assert 'fetch("/status"' in response.text
    assert 'link.href = "/authorize"' in response.text
    assert 'fetch("/settings"' in response.text
    # A cheap guard on the rule that Spotify's text goes in as text: item
    # names are attacker-influenceable, anyone can name a podcast episode.
    assert "innerHTML" not in response.text


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


@pytest.mark.parametrize(
    ("failure", "since_ago", "read_ago", "stale"),
    [
        # Failing for 25 s, last good read 33 s ago: the board is marked, by
        # the age of the read, before the failure itself is 30 s old.
        (ReadFailure.NETWORK, 25, 33, True),
        # Reads stopped without failing: still marked, never "healthy".
        (None, None, 31, True),
        (None, None, 2, False),
    ],
)
def test_status_reports_staleness_by_the_display_rule(
    failure: ReadFailure | None, since_ago: float | None, read_ago: float, stale: bool
) -> None:
    now = datetime.fromisoformat("2026-11-11T12:00:00+00:00")
    snapshot = PlaybackSnapshot(
        status=PlaybackStatus.NOTHING_PLAYING,
        item=None,
        failure=failure,
        failure_since=now - timedelta(seconds=since_ago) if since_ago is not None else None,
        last_read_at=now - timedelta(seconds=read_ago),
        authorized_at=None,
    )

    assert main._playback_json(snapshot, now)["stale"] is stale


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
    assert "Status page: /" in forged.text and "Status page: /" in missing.text
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
    assert "Status page: /" in response.text


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
    assert "Status page: /" in response.text


def test_a_refused_exchange_is_a_502(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_spotify(monkeypatch, FakeSpotify(refuse_grant=True))

    with TestClient(main.app, follow_redirects=False) as client:
        state = state_of(client.get("/authorize"))
        response = client.get("/callback", params={"code": A_CODE, "state": state})

    assert response.status_code == 502
    assert "Status page: /" in response.text
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

    assert callback.status_code == 303
    assert callback.headers["location"] == "/?authorized=1"
    assert A_CODE not in callback.text
    body = status.json()
    assert set(body) == {
        "configured",
        "playback",
        "authorization",
        "board",
        "enhancement",
        "stale_after_seconds",
        "now",
    }
    assert body["configured"] is True
    playback = body["playback"]
    assert set(playback) == {
        "status", "item", "failure", "failure_since", "last_read_at", "stale"
    }
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


def test_put_settings_saves_and_status_reports_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_openrouter_key(monkeypatch)

    with TestClient(main.app) as client:
        before = client.get("/status").json()["enhancement"]
        on = client.put("/settings", json={"ai_enhanced": True})
        enabled = client.get("/status").json()["enhancement"]
        off = client.put("/settings", json={"ai_enhanced": False})
        disabled = client.get("/status").json()["enhancement"]

    assert before == idle_enhancement(True, False)
    assert (on.status_code, on.json()) == (200, {"ai_enhanced": True, "daily_limit_usd": 1.0})
    assert enabled == idle_enhancement(True, True)
    assert (off.status_code, off.json()) == (
        200,
        {"ai_enhanced": False, "daily_limit_usd": 1.0},
    )
    assert disabled == idle_enhancement(True, False)
    saved = json.loads((tmp_path / "var" / "settings.json").read_text(encoding="utf-8"))
    assert saved == {"ai_enhanced": False, "daily_limit_usd": 1.0, "model": None}


def test_the_saved_choice_survives_a_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_openrouter_key(monkeypatch)

    with TestClient(main.app) as client:
        assert client.put("/settings", json={"ai_enhanced": True}).status_code == 200

    # A second lifespan on the same file, as a pm2 restart would be.
    with TestClient(main.app) as client:
        enhancement = client.get("/status").json()["enhancement"]

    assert enhancement == idle_enhancement(True, True)


@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        # Valid JSON under a type a cross-site page may send with no preflight.
        ("text/plain", '{"ai_enhanced": true}'),
        ("text/plain;charset=UTF-8", '{"ai_enhanced": true}'),
        ("application/x-www-form-urlencoded", "ai_enhanced=true"),
        # A body with no type at all, as a cross-site `fetch` of a Blob sends.
        (None, '{"ai_enhanced": true}'),
    ],
)
def test_put_settings_refuses_a_body_that_is_not_json_and_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content_type: str | None, body: str
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_openrouter_key(monkeypatch)
    headers = {"content-type": content_type} if content_type is not None else {}

    with TestClient(main.app) as client:
        response = client.put("/settings", content=body.encode(), headers=headers)
        enhancement = client.get("/status").json()["enhancement"]

    assert 400 <= response.status_code < 500
    assert enhancement["enabled"] is False
    assert not (tmp_path / "var" / "settings.json").exists()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"ai_enhanced": "true"},
        {"ai_enhanced": 1},
        {"ai_enhanced": None},
        {"ai_enhanced": True, "something_else": 1},
        [True],
        {"daily_limit_usd": 0},
        {"daily_limit_usd": 0.0},
        {"daily_limit_usd": -0.5},
        {"daily_limit_usd": "1"},
        {"daily_limit_usd": True},
        {"ai_enhanced": True, "daily_limit_usd": 0},
        {"ai_enhanced": None, "daily_limit_usd": None},
        {"daily_limit_usd": 0.5, "something_else": 1},
    ],
)
def test_put_settings_refuses_a_body_of_the_wrong_shape_and_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: object
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_openrouter_key(monkeypatch)

    with TestClient(main.app) as client:
        response = client.put("/settings", json=body)
        enhancement = client.get("/status").json()["enhancement"]

    assert 400 <= response.status_code < 500
    assert enhancement["enabled"] is False
    assert not (tmp_path / "var" / "settings.json").exists()


def test_without_a_key_turning_enhancement_on_is_a_409_and_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)

    with TestClient(main.app) as client:
        on = client.put("/settings", json={"ai_enhanced": True})
        enhancement = client.get("/status").json()["enhancement"]
        # Off is always allowed: it is the state without a key anyway.
        off = client.put("/settings", json={"ai_enhanced": False})

    assert on.status_code == 409
    assert enhancement == idle_enhancement(False, False)
    assert off.status_code == 200
    saved = json.loads((tmp_path / "var" / "settings.json").read_text(encoding="utf-8"))
    assert saved == {"ai_enhanced": False, "daily_limit_usd": 1.0, "model": None}


def test_put_settings_takes_a_limit_alone_and_keeps_the_switch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_openrouter_key(monkeypatch)

    with TestClient(main.app) as client:
        assert client.put("/settings", json={"ai_enhanced": True}).status_code == 200
        limit = client.put("/settings", json={"daily_limit_usd": 0.5})
        enhancement = client.get("/status").json()["enhancement"]
        in_memory = main.app.state.owner_settings

    assert (limit.status_code, limit.json()) == (
        200,
        {"ai_enhanced": True, "daily_limit_usd": 0.5},
    )
    assert enhancement["enabled"] is True
    assert in_memory == OwnerSettings(ai_enhanced=True, daily_limit_usd=0.5)
    saved = json.loads((tmp_path / "var" / "settings.json").read_text(encoding="utf-8"))
    assert saved == {"ai_enhanced": True, "daily_limit_usd": 0.5, "model": None}


def test_put_settings_keeps_the_limit_when_only_the_switch_is_sent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_openrouter_key(monkeypatch)

    with TestClient(main.app) as client:
        assert client.put("/settings", json={"daily_limit_usd": 2}).status_code == 200
        on = client.put("/settings", json={"ai_enhanced": True})

    assert (on.status_code, on.json()) == (200, {"ai_enhanced": True, "daily_limit_usd": 2.0})


def test_without_a_key_a_limit_may_still_be_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The 409 is about switching on with nothing to switch, not about the limit.
    use_transport(monkeypatch, CountingTransport(), tmp_path)

    with TestClient(main.app) as client:
        limit = client.put("/settings", json={"daily_limit_usd": 0.25})
        both = client.put("/settings", json={"ai_enhanced": True, "daily_limit_usd": 0.5})

    assert (limit.status_code, limit.json()) == (
        200,
        {"ai_enhanced": False, "daily_limit_usd": 0.25},
    )
    assert both.status_code == 409
    assert main.app.state.owner_settings == OwnerSettings(daily_limit_usd=0.25)


def test_a_settings_file_from_before_the_limit_keeps_enhancement_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The file on the Pi at the deploy that brings the limit in.
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_openrouter_key(monkeypatch)
    (tmp_path / "var").mkdir()
    (tmp_path / "var" / "settings.json").write_text('{"ai_enhanced": true}', encoding="utf-8")

    with TestClient(main.app) as client:
        enhancement = client.get("/status").json()["enhancement"]
        settings = main.app.state.owner_settings

    assert enhancement == idle_enhancement(True, True)
    assert settings == OwnerSettings(ai_enhanced=True, daily_limit_usd=1.0)


def test_a_choice_saved_with_a_key_is_not_reported_enabled_once_the_key_is_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Without a key the service never enhances, whatever the file says.
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    (tmp_path / "var").mkdir()
    (tmp_path / "var" / "settings.json").write_text('{"ai_enhanced": true}', encoding="utf-8")

    with TestClient(main.app) as client:
        enhancement = client.get("/status").json()["enhancement"]

    assert enhancement == idle_enhancement(False, False)


def test_an_unreadable_settings_file_starts_with_enhancement_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_openrouter_key(monkeypatch)
    (tmp_path / "var").mkdir()
    (tmp_path / "var" / "settings.json").write_text("{not json", encoding="utf-8")

    with TestClient(main.app) as client:
        assert client.get("/healthz").status_code == 200
        enhancement = client.get("/status").json()["enhancement"]

    assert enhancement == idle_enhancement(True, False)


def test_a_failed_save_is_a_500_and_keeps_the_previous_choice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_openrouter_key(monkeypatch)

    def failing_save(self: OwnerSettingsStore, settings: OwnerSettings) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(OwnerSettingsStore, "save", failing_save)

    with TestClient(main.app) as client:
        response = client.put("/settings", json={"ai_enhanced": True})
        enhancement = client.get("/status").json()["enhancement"]

    assert response.status_code == 500
    assert enhancement == idle_enhancement(True, False)


def test_the_openrouter_key_is_in_no_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_openrouter_key(monkeypatch)

    with TestClient(main.app) as client:
        put = client.put("/settings", json={"ai_enhanced": True})
        status = client.get("/status")

    assert put.status_code == 200
    assert AN_OPENROUTER_KEY not in put.text
    assert AN_OPENROUTER_KEY not in status.text
    assert set(status.json()["enhancement"]) == {
        "available",
        "enabled",
        "paused_until",
        "cover",
        "spend",
    }


def test_with_spotify_and_a_key_flipping_the_switch_makes_the_enhancer_forget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_spotify(monkeypatch, FakeSpotify())
    use_openrouter_key(monkeypatch)
    forgotten: list[bool] = []
    monkeypatch.setattr(Enhancer, "forget", lambda self: forgotten.append(True))

    with TestClient(main.app) as client:
        assert isinstance(main.app.state.enhancer, Enhancer)
        client.put("/settings", json={"ai_enhanced": True})
        after_on = len(forgotten)
        # The same value again is no change, so nothing to forget.
        client.put("/settings", json={"ai_enhanced": True})
        after_repeat = len(forgotten)
        client.put("/settings", json={"ai_enhanced": False})

    assert (after_on, after_repeat, len(forgotten)) == (1, 1, 2)


def test_changing_only_the_limit_does_not_make_the_enhancer_forget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_spotify(monkeypatch, FakeSpotify())
    use_openrouter_key(monkeypatch)
    forgotten: list[bool] = []
    monkeypatch.setattr(Enhancer, "forget", lambda self: forgotten.append(True))

    with TestClient(main.app) as client:
        client.put("/settings", json={"daily_limit_usd": 0.5})
        client.put("/settings", json={"ai_enhanced": False, "daily_limit_usd": 2.0})

    assert forgotten == []


def test_the_lifespan_gives_the_enhancer_the_ledger_file_and_the_live_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_spotify(monkeypatch, FakeSpotify())
    use_openrouter_key(monkeypatch)
    today = datetime.now(UTC).date().isoformat()
    (tmp_path / "var").mkdir()
    (tmp_path / "var" / "ai-spend.json").write_text(
        json.dumps({"day": today, "spent_usd": 0.6}), encoding="utf-8"
    )

    with TestClient(main.app) as client:
        enhancer = main.app.state.enhancer
        assert isinstance(enhancer, Enhancer)
        spent = enhancer.spent_today
        before = enhancer.limit_reached
        assert client.put("/settings", json={"daily_limit_usd": 0.5}).status_code == 200
        after = enhancer.limit_reached

    assert spent == pytest.approx(0.6)
    assert (before, after) == (False, True)



def test_the_lifespan_gives_the_display_todays_spend_over_the_live_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # What the board's spend bar is drawn from: without this wiring every
    # display test would stay green while the bar never appears.
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_spotify(monkeypatch, FakeSpotify())
    use_openrouter_key(monkeypatch)
    today = datetime.now(UTC).date().isoformat()
    (tmp_path / "var").mkdir()
    (tmp_path / "var" / "ai-spend.json").write_text(
        json.dumps({"day": today, "spent_usd": 0.3}), encoding="utf-8"
    )
    passed: dict[str, object] = {}

    async def record(*args: object, **kwargs: object) -> None:
        passed.update(kwargs)

    monkeypatch.setattr(main, "run_display", record)

    with TestClient(main.app) as client:
        spend_fraction = passed["spend_fraction"]
        assert callable(spend_fraction)
        before = spend_fraction()
        assert client.put("/settings", json={"daily_limit_usd": 0.6}).status_code == 200
        after = spend_fraction()

    # The default $1.00 limit, then the one saved from the page, read live.
    assert (before, after) == (pytest.approx(0.3), pytest.approx(0.5))

def test_without_a_key_status_reports_the_limit_and_no_spend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The limit is the owner's setting and exists without a key; the spend does not.
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_spotify(monkeypatch, FakeSpotify())

    with TestClient(main.app) as client:
        assert client.put("/settings", json={"daily_limit_usd": 0.25}).status_code == 200
        spend = client.get("/status").json()["enhancement"]["spend"]

    assert spend == no_spend(0.25)


@pytest.mark.parametrize(
    ("spent", "reached"),
    [(0.4, False), (0.5, True), (0.6, True)],
    ids=["under", "at", "over"],
)
def test_with_a_key_status_reports_todays_spend_against_the_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, spent: float, reached: bool
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_spotify(monkeypatch, FakeSpotify())
    use_openrouter_key(monkeypatch)
    now = datetime.now(UTC)
    (tmp_path / "var").mkdir()
    (tmp_path / "var" / "settings.json").write_text(
        json.dumps({"ai_enhanced": True, "daily_limit_usd": 0.5}), encoding="utf-8"
    )
    (tmp_path / "var" / "ai-spend.json").write_text(
        json.dumps({"day": now.date().isoformat(), "spent_usd": spent}), encoding="utf-8"
    )

    with TestClient(main.app) as client:
        response = client.get("/status")

    spend = response.json()["enhancement"]["spend"]
    assert spend["spent_usd"] == pytest.approx(spent)
    assert spend["limit_usd"] == 0.5
    assert spend["limit_reached"] is reached
    # The next 00:00 UTC, when the ledger's day ends.
    next_midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), UTC)
    assert datetime.fromisoformat(spend["resets_at"]) == next_midnight
    assert AN_OPENROUTER_KEY not in response.text


def test_the_status_page_saves_the_limit_through_put_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)

    with TestClient(main.app) as client:
        page = client.get("/").text

    assert 'id="enhance-limit" min="0.001" step="0.001"' in page
    # The checkbox's call and the limit's, each a PUT to /settings.
    assert page.count('fetch("/settings"') == 2
    assert page.count('method: "PUT"') == 2
    assert "JSON.stringify({ daily_limit_usd: Number(limit.value) })" in page
    assert "The limit could not be saved." in page


@pytest.mark.parametrize("spotify", [False, True], ids=["key only", "spotify only"])
def test_without_both_spotify_and_a_key_there_is_no_enhancer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, spotify: bool
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    if spotify:
        use_spotify(monkeypatch, FakeSpotify())
    else:
        use_openrouter_key(monkeypatch)
    passed: dict[str, object] = {}

    async def record(*args: object, **kwargs: object) -> None:
        passed.update(kwargs)

    monkeypatch.setattr(main, "run_display", record)

    with TestClient(main.app) as client:
        enhancer = main.app.state.enhancer
        if not spotify:
            # Switching on is allowed with a key, and must not fail without a loader.
            assert client.put("/settings", json={"ai_enhanced": True}).status_code == 200

    assert enhancer is None
    # Without an enhancer the display is not even given the switch, nor a spend.
    assert passed["owner_settings"] is None
    assert passed["spend_fraction"] is None


# What `/status` says enhancement made of the current cover. The loader, the
# reader and the enhancer the route reads are swapped on `app.state` after
# startup, so each state is set exactly; the display loop keeps its own and
# never sees them. On the default 32 px board the cover wanted is the smallest
# image covering it, and the one enhanced from is the widest.
WIDE_COVER = "https://i.scdn.co/image/wide"
SMALL_COVER = "https://i.scdn.co/image/small"
A_FRAME = Image.new("RGB", (32, 32))
AN_INSTANT = datetime.fromisoformat("2026-11-11T12:00:00+00:00")


def playing(
    *, artwork: bool = True, status: PlaybackStatus = PlaybackStatus.PLAYING
) -> PlaybackSnapshot:
    images = (
        (Artwork(WIDE_COVER, 640, 640), Artwork(SMALL_COVER, 64, 64)) if artwork else ()
    )
    return PlaybackSnapshot(
        status=status,
        item=PlayingItem(
            kind="track", id="track-1", name="A Track", subtitle="An Artist", artwork=images
        ),
        failure=None,
        failure_since=None,
        last_read_at=AN_INSTANT,
        authorized_at=AN_INSTANT,
    )


class FakeReader:
    """Just the believed state, which is all `/status` reads of a reader."""

    def __init__(self, snapshot: PlaybackSnapshot) -> None:
        self.snapshot = snapshot


class FakeCoverLoader:
    """A loader in a fixed state for one URL, pending for any other, as the real one is."""

    def __init__(self, url: str, state: CoverState) -> None:
        self._url = url
        self._state = state

    def state(self, url: str) -> CoverState:
        return self._state if url == self._url else CoverState(CoverStatus.PENDING)

    def want(self, url: str | None, *, enhance_from: str | None = None) -> None:
        raise AssertionError("/status must never make the loader want anything")


def enhancer_after(answer: int | None, tmp_path: Path) -> Enhancer:
    """A real enhancer at `AN_INSTANT`, after one enhancement of `WIDE_COVER` answered `answer`.

    `None` asks for nothing, so the enhancer is fresh: not paused, given up on nothing.
    """

    async def run() -> Enhancer:
        def openrouter(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(answer or 500, json={"error": {"message": "refused"}})

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(openrouter)) as client:
            enhancer = Enhancer(
                client,
                AN_OPENROUTER_KEY,
                lambda: "a-model",
                32,
                ledger=SpendLedger(tmp_path / "ai-spend.json", clock=lambda: AN_INSTANT),
                daily_limit=lambda: 1.0,
                clock=lambda: AN_INSTANT,
            )
            if answer is not None:
                assert await enhancer.enhance(WIDE_COVER, b"source", "image/png") is None
        return enhancer

    return asyncio.run(run())


def status_with(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    snapshot: PlaybackSnapshot,
    cover: CoverState,
    enhancer: Enhancer,
    switched_on: bool = True,
) -> httpx2.Response:
    use_transport(monkeypatch, CountingTransport(), tmp_path)
    use_openrouter_key(monkeypatch)

    with TestClient(main.app) as client:
        assert client.put("/settings", json={"ai_enhanced": switched_on}).status_code == 200
        main.app.state.spotify_reader = FakeReader(snapshot)
        main.app.state.covers = FakeCoverLoader(SMALL_COVER, cover)
        main.app.state.enhancer = enhancer
        return client.get("/status")


@pytest.mark.parametrize(
    ("cover", "answer", "expected"),
    [
        # The plain frame is not on hand yet: enhancement has not begun.
        (CoverState(CoverStatus.PENDING), None, None),
        (CoverState(CoverStatus.FAILED), None, None),
        (CoverState(CoverStatus.GENERATING, A_FRAME), None, "generating"),
        (CoverState(CoverStatus.READY, A_FRAME, enhanced=True), None, "enhanced"),
        (CoverState(CoverStatus.READY, A_FRAME), None, "plain"),
        # A 400 is given up on at once, and the give-up is the source image's.
        (CoverState(CoverStatus.READY, A_FRAME), 400, "gave_up"),
    ],
    ids=["pending", "failed", "generating", "enhanced", "plain", "gave up"],
)
def test_status_reports_what_enhancement_made_of_the_current_cover(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cover: CoverState,
    answer: int | None,
    expected: str | None,
) -> None:
    response = status_with(
        monkeypatch,
        tmp_path,
        snapshot=playing(),
        cover=cover,
        enhancer=enhancer_after(answer, tmp_path),
    )

    enhancement = response.json()["enhancement"]
    resets_at = enhancement["spend"].pop("resets_at")
    assert enhancement == {
        "available": True,
        "enabled": True,
        "paused_until": None,
        "cover": expected,
        "spend": {"spent_usd": 0.0, "limit_usd": 1.0, "limit_reached": False},
    }
    assert datetime.fromisoformat(resets_at) == datetime.fromisoformat("2026-11-12T00:00:00+00:00")


@pytest.mark.parametrize(
    "snapshot",
    [
        playing(status=PlaybackStatus.NOTHING_PLAYING),
        # An ad or a local file: the no-artwork disc, nothing to enhance.
        playing(artwork=False),
    ],
    ids=["nothing playing", "no artwork"],
)
def test_status_reports_no_cover_when_none_is_wanted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, snapshot: PlaybackSnapshot
) -> None:
    response = status_with(
        monkeypatch,
        tmp_path,
        snapshot=snapshot,
        cover=CoverState(CoverStatus.READY, A_FRAME, enhanced=True),
        enhancer=enhancer_after(None, tmp_path),
    )

    assert response.json()["enhancement"]["cover"] is None


def test_status_reports_no_cover_with_the_switch_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    response = status_with(
        monkeypatch,
        tmp_path,
        snapshot=playing(),
        cover=CoverState(CoverStatus.READY, A_FRAME),
        enhancer=enhancer_after(None, tmp_path),
        switched_on=False,
    )

    assert response.json()["enhancement"]["cover"] is None


def test_a_refusal_is_reported_as_a_pause_until_midnight_utc_and_the_cover_as_plain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    response = status_with(
        monkeypatch,
        tmp_path,
        snapshot=playing(),
        cover=CoverState(CoverStatus.READY, A_FRAME),
        enhancer=enhancer_after(402, tmp_path),
    )

    enhancement = response.json()["enhancement"]
    assert datetime.fromisoformat(enhancement["paused_until"]) == datetime.fromisoformat(
        "2026-11-12T00:00:00+00:00"
    )
    # Not given up on: the page says plain because of the pause.
    assert enhancement["cover"] == "plain"
    assert AN_OPENROUTER_KEY not in response.text


def test_the_pause_is_reported_with_the_switch_off_and_nothing_playing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    response = status_with(
        monkeypatch,
        tmp_path,
        snapshot=playing(status=PlaybackStatus.NOTHING_PLAYING),
        cover=CoverState(CoverStatus.PENDING),
        enhancer=enhancer_after(403, tmp_path),
        switched_on=False,
    )

    enhancement = response.json()["enhancement"]
    assert datetime.fromisoformat(enhancement["paused_until"]) == datetime.fromisoformat(
        "2026-11-12T00:00:00+00:00"
    )
    assert enhancement["cover"] is None


@pytest.mark.parametrize(
    ("enhanced", "generating"),
    [(True, False), (False, False), (False, True)],
    ids=["enhanced", "plain", "generating"],
)
def test_status_reports_whether_the_cover_last_sent_was_enhanced_or_generating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, enhanced: bool, generating: bool
) -> None:
    use_transport(monkeypatch, CountingTransport(), tmp_path)

    with TestClient(main.app) as client:
        # Its own status object: the running loop keeps writing to the real one.
        main.app.state.display_status = display.DisplayStatus(
            presentation=display.Presentation(
                display.PresentationKind.COVER,
                SMALL_COVER,
                enhanced=enhanced,
                generating=generating,
            )
        )
        presentation = client.get("/status").json()["board"]["presentation"]

    assert presentation == {
        "kind": "cover",
        "url": SMALL_COVER,
        "stale": False,
        "enhanced": enhanced,
        "generating": generating,
    }
