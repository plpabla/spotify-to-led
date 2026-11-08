"""The service entrypoint: settings, the supervised background tasks, and the routes.

This single process is both the board driver and the observability surface, so
the two are deliberately kept apart: the board work and the Spotify reader run
in background tasks that swallow and log their own failures, and the routes
answer whether or not the board is reachable or Spotify is readable. A board or
Spotify fault is something to report, never something that takes the server
down with it.

Spotify is optional. Without a client ID no reader runs and the Spotify routes
say so instead of failing, so the laptop and the test suite start the same app
the Pi does.
"""

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from typing import Any

import httpx2
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from spotify_to_led import frames
from spotify_to_led.board.factory import create_transport
from spotify_to_led.board.transport import BoardTransport
from spotify_to_led.config import load_settings
from spotify_to_led.spotify.auth import AuthorizationRevoked, SpotifyAuthError
from spotify_to_led.spotify.playback import PlaybackSnapshot
from spotify_to_led.spotify.reader import AuthorizationStateMismatch, SpotifyReader
from spotify_to_led.spotify.token_store import TokenStore

logger = logging.getLogger(__name__)

NOT_CONFIGURED = "Spotify is not configured on this service (SPOTIFY_CLIENT_ID is empty)."
SNAPSHOT_FIELDS = ("status", "item", "failure", "failure_since", "last_read_at", "authorized_at")


def configure_logging(level: str) -> None:
    """Send the root logger to stdout at `level`, so pm2's log files capture it."""
    logging.basicConfig(
        level=level.upper(),
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def create_spotify_client() -> httpx2.AsyncClient:
    """The one HTTP client every Spotify call of the process goes through."""
    return httpx2.AsyncClient()


async def push_test_pattern(transport: BoardTransport, size: int) -> None:
    """Connect once and push the test pattern once.

    Every failure stops here. Nothing is re-raised, because this task must not
    be able to prevent the application from serving.
    """
    try:
        await transport.connect()
        await transport.push_frame(frames.test_pattern(size))
    except Exception:
        logger.exception("board task failed; the service keeps serving without the board")


async def _cancel(task: asyncio.Task[None]) -> None:
    # The task may have finished long ago, so cancellation has to tolerate an
    # already-completed task rather than assume a pending one.
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own the transport, the Spotify client and the background tasks for the process lifetime."""
    settings = load_settings()
    configure_logging(settings.log_level)
    transport = create_transport(settings)
    task = asyncio.create_task(push_test_pattern(transport, settings.board_size))

    spotify_client: httpx2.AsyncClient | None = None
    reader_task: asyncio.Task[None] | None = None
    app.state.spotify_reader = None
    if settings.spotify_enabled:
        spotify_client = create_spotify_client()
        reader = SpotifyReader(spotify_client, settings, TokenStore(settings.spotify_token_file))
        app.state.spotify_reader = reader
        # `run()` classifies and logs its own failures, so like the board task
        # it cannot take the server down.
        reader_task = asyncio.create_task(reader.run())
    else:
        logger.info("Spotify is not configured (no SPOTIFY_CLIENT_ID); no reader is running")

    try:
        yield
    finally:
        if reader_task is not None:
            await _cancel(reader_task)
        if spotify_client is not None:
            await spotify_client.aclose()
        await _cancel(task)
        try:
            await transport.close()
        except Exception:
            # Same reasoning as the task's own guard: a board fault is reported,
            # never raised. Escaping here would put a traceback in the shutdown
            # log on every unclean teardown, which is noise in the exact log the
            # unattended run is read from.
            logger.exception("closing the board transport failed")


app = FastAPI(title="spotify-to-led", lifespan=lifespan)


def _reader(request: Request) -> SpotifyReader | None:
    return request.app.state.spotify_reader


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Report that the process is serving, and nothing else.

    Link state, the playing item and re-authorization belong to FR-015, which
    is scheduled for S-07. Keeping them out of here is what lets this route
    stay truthful while the board is down.
    """
    return {"status": "ok"}


@app.get("/authorize")
async def authorize(request: Request) -> Response:
    """Send the owner to Spotify's consent screen.

    Ungated on purpose: the hard rules keep auth out of the application, and
    the only gate is the Cloudflare Access policy at the edge.
    """
    reader = _reader(request)
    if reader is None:
        return PlainTextResponse(NOT_CONFIGURED, status_code=503)
    return RedirectResponse(reader.begin_authorization(), status_code=302)


@app.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> PlainTextResponse:
    """Finish the authorization Spotify redirected back with.

    The code is never echoed: every answer here is a fixed sentence, plus
    Spotify's own error name when it refused.
    """
    reader = _reader(request)
    if reader is None:
        return PlainTextResponse(NOT_CONFIGURED, status_code=503)
    if error is not None:
        return PlainTextResponse(
            f"Spotify did not authorize the service ({error}). Start again at /authorize.",
            status_code=400,
        )
    if code is None or state is None:
        return PlainTextResponse(
            "The callback is missing its code or state. Start again at /authorize.",
            status_code=400,
        )
    try:
        await reader.complete_authorization(code, state)
    except AuthorizationStateMismatch:
        return PlainTextResponse(
            "This is not the authorization the service started. Start again at /authorize.",
            status_code=400,
        )
    except (AuthorizationRevoked, SpotifyAuthError, httpx2.TransportError) as failure:
        # The type name only: the request behind it carried the code and the
        # verifier, and nothing derived from it belongs in the log.
        logger.warning("Spotify code exchange failed (%s)", type(failure).__name__)
        return PlainTextResponse(
            "Spotify could not complete the authorization. Try again at /authorize.",
            status_code=502,
        )
    return PlainTextResponse("Spotify authorization succeeded. You can close this tab.")


@app.get("/playback")
async def playback(request: Request) -> dict[str, Any]:
    """The believed playback state, as JSON.

    Temporary: it lets S-01 be verified before the status page exists, and
    S-07 replaces it. The fields are named one by one rather than dumped from
    the reader, so no token can reach this response by a field added elsewhere.
    """
    reader = _reader(request)
    if reader is None:
        return {"configured": False, **dict.fromkeys(SNAPSHOT_FIELDS)}
    return {"configured": True, **_snapshot_json(reader.snapshot)}


def _snapshot_json(snapshot: PlaybackSnapshot) -> dict[str, Any]:
    return {
        "status": snapshot.status,
        "item": asdict(snapshot.item) if snapshot.item is not None else None,
        "failure": snapshot.failure,
        "failure_since": snapshot.failure_since,
        "last_read_at": snapshot.last_read_at,
        "authorized_at": snapshot.authorized_at,
    }


if __name__ == "__main__":
    # ecosystem.config.js runs `python -m spotify_to_led.main`, so this
    # invocation has to keep working.
    startup_settings = load_settings()
    uvicorn.run(app, host=startup_settings.host, port=startup_settings.port)
