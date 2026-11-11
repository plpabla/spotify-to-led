"""The service entrypoint: settings, the supervised background tasks, and the routes.

This single process is both the board driver and the observability surface, so
the two are deliberately kept apart: the display task and the Spotify reader
run in background tasks that swallow and log their own failures, and the
routes answer whether or not the board is reachable or Spotify is readable. A
board or Spotify fault is something to report, never something that takes the
server down with it.

The display task (`display.run_display`) connects to the board, reconnecting
with backoff whenever the link is down, and shows the cover of what is playing,
the playing placeholder while that cover is not on hand, the no-artwork disc
for an item with no artwork at all, or the idle frame, from the reader's
snapshot. Covers are downloaded by a `CoverLoader` beside
it, on the Spotify client, and kept on disk under `COVER_CACHE_DIR`, so a
cover seen before, even before a restart, is shown without a download. A
cover is marked once reads have been failing for over 30 s. It pushes again
whenever that choice changes or the link comes back.

With an OpenRouter key and Spotify both configured, the loader also gets an
`Enhancer` on the same client, and the display reads the owner's switch on
every sample: switched on, covers are redrawn by an image model (FR-019),
each image once, kept beside its plain frame in the cache. Switched off, or
without a key, no OpenRouter request is made.

The status page at `/` is static and renders `/status`, which reports the
believed playback, the authorization and the board link, including the case
where the display task has ended, so a board fault is explained, not hidden.
Its one control, the AI-enhanced covers checkbox, writes through
`PUT /settings`, which keeps the owner's choice in `OWNER_SETTINGS_FILE` so it
survives a restart. A change of it also makes the enhancer forget the images
it gave up on, so switching off and on is how the owner asks for another try.

Spotify is optional. Without a client ID no reader runs, the display shows
idle, and the Spotify routes say so instead of failing, so the laptop and the
test suite start the same app the Pi does.
"""

import asyncio
import logging
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, StrictBool

from spotify_to_led.board.factory import create_transport
from spotify_to_led.board.transport import BoardTransport, LinkState
from spotify_to_led.config import load_settings
from spotify_to_led.covers import CoverLoader
from spotify_to_led.display import STALE_AFTER_SECONDS, DisplayStatus, is_stale, run_display
from spotify_to_led.enhance import Enhancer
from spotify_to_led.frame_cache import FrameCache
from spotify_to_led.owner_settings import OwnerSettings, OwnerSettingsStore
from spotify_to_led.spotify.auth import AuthorizationRevoked, SpotifyAuthError
from spotify_to_led.spotify.playback import PlaybackSnapshot
from spotify_to_led.spotify.reader import AuthorizationStateMismatch, SpotifyReader
from spotify_to_led.spotify.token_store import TokenStore

logger = logging.getLogger(__name__)

NOT_CONFIGURED = "Spotify is not configured on this service (SPOTIFY_CLIENT_ID is empty)."
# Spotify's error codes are short snake_case words; anything else on the query
# is not echoed, so a crafted link cannot put its own text on this page.
SPOTIFY_ERROR_CODE = re.compile(r"[a-z_]{1,64}")
# Spotify ends a refresh token about 6 months after the authorization that
# granted it, and refreshing does not extend that. Spotify reports no expiry,
# so this is an estimate for the status page, never a deadline the code acts on.
REFRESH_TOKEN_LIFETIME = timedelta(days=182)
# Beside the package rather than the working directory, so the page is found
# from the checkout under pm2 and from the tests alike.
STATUS_PAGE = Path(__file__).parent / "static" / "index.html"


def _drop_query_strings(record: logging.LogRecord) -> bool:
    """Cut the query string off the path in a uvicorn access-log line.

    `/callback` arrives with the authorization code in its query, and uvicorn
    logs the full request line, so without this every authorization would
    write a code into the pm2 log.
    """
    if isinstance(record.args, tuple) and len(record.args) >= 3:
        args = list(record.args)
        args[2] = str(args[2]).split("?", 1)[0]
        record.args = tuple(args)
    return True


def configure_logging(level: str) -> None:
    """Send the root logger to stdout at `level`, so pm2's log files capture it."""
    logging.basicConfig(
        level=level.upper(),
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Adding the same function twice is a no-op, so repeated lifespans are fine.
    logging.getLogger("uvicorn.access").addFilter(_drop_query_strings)
    # httpx2 logs every request at INFO, which is a line per poll every three
    # seconds; the reader logs its own state changes instead.
    logging.getLogger("httpx2").setLevel(logging.WARNING)


def create_spotify_client() -> httpx2.AsyncClient:
    """The one HTTP client every Spotify call of the process goes through."""
    return httpx2.AsyncClient()


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
    # A broken file loads as the defaults, so this cannot stop the service.
    owner_settings_store = OwnerSettingsStore(settings.owner_settings_file)
    app.state.owner_settings_store = owner_settings_store
    app.state.owner_settings = owner_settings_store.load()
    app.state.enhance_available = settings.enhance_available

    spotify_client: httpx2.AsyncClient | None = None
    reader: SpotifyReader | None = None
    reader_task: asyncio.Task[None] | None = None
    covers: CoverLoader | None = None
    enhancer: Enhancer | None = None
    app.state.spotify_reader = None
    if settings.spotify_enabled:
        spotify_client = create_spotify_client()
        reader = SpotifyReader(spotify_client, settings, TokenStore(settings.spotify_token_file))
        app.state.spotify_reader = reader
        if settings.openrouter_api_key is not None:
            # The shared client carries no headers of its own; the key goes
            # on each OpenRouter request and nowhere else.
            enhancer = Enhancer(
                spotify_client,
                settings.openrouter_api_key.get_secret_value(),
                settings.openrouter_model,
                settings.board_size,
            )
        covers = CoverLoader(
            spotify_client,
            settings.board_size,
            cache=FrameCache(settings.cover_cache_dir, settings.board_size),
            enhancer=enhancer,
        )
        # `run()` classifies and logs its own failures, so like the display
        # task it cannot take the server down.
        reader_task = asyncio.create_task(reader.run())
    else:
        logger.info("Spotify is not configured (no SPOTIFY_CLIENT_ID); no reader is running")

    def current() -> PlaybackSnapshot | None:
        # `None` without a reader, which the display rule shows as idle.
        return reader.snapshot if reader is not None else None

    def owner_settings() -> OwnerSettings:
        # Read on every sample, so a change from the page is acted on at once.
        return app.state.owner_settings

    # For `PUT /settings`, which tells it when the switch changes.
    app.state.enhancer = enhancer
    # Started after the reader exists, because `current` closes over it.
    display_status = DisplayStatus()
    task = asyncio.create_task(
        run_display(
            transport,
            settings.board_size,
            current,
            covers=covers,
            # Only with an enhancer: without one, the switch has nothing to switch.
            owner_settings=owner_settings if enhancer is not None else None,
            status=display_status,
        )
    )
    # For `/status`, which reports the board without reaching into the loop.
    app.state.transport = transport
    app.state.display_task = task
    app.state.display_status = display_status

    try:
        yield
    finally:
        if reader_task is not None:
            await _cancel(reader_task)
        # The display first, so it cannot want another cover once the loader
        # is closed, and the loader before the client its download uses.
        await _cancel(task)
        if covers is not None:
            await covers.aclose()
        if spotify_client is not None:
            await spotify_client.aclose()
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

    Link state, the playing item and re-authorization belong to FR-015's
    status page and `/status`. Keeping them out of here is what lets this
    route stay truthful while the board is down.
    """
    return {"status": "ok"}


@app.get("/")
async def status_page() -> HTMLResponse:
    """The status page, which renders `/status` in the browser.

    Static on purpose: everything it shows comes from `/status`, so nothing
    from Spotify is ever put into HTML on the server.
    """
    return HTMLResponse(STATUS_PAGE.read_text(encoding="utf-8"))


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
) -> Response:
    """Finish the authorization Spotify redirected back with.

    Success lands on the status page, which shows a fixed notice for it. The
    code is never echoed: every other answer is a fixed sentence, plus
    Spotify's own error name when it refused, pointing back to the page.
    """
    reader = _reader(request)
    if reader is None:
        return PlainTextResponse(NOT_CONFIGURED, status_code=503)
    if error is not None:
        named = error if SPOTIFY_ERROR_CODE.fullmatch(error) else "unknown error"
        return PlainTextResponse(
            f"Spotify did not authorize the service ({named}). Start again at /authorize. "
            "Status page: /",
            status_code=400,
        )
    if code is None or state is None:
        return PlainTextResponse(
            "The callback is missing its code or state. Start again at /authorize. "
            "Status page: /",
            status_code=400,
        )
    try:
        await reader.complete_authorization(code, state)
    except AuthorizationStateMismatch:
        return PlainTextResponse(
            "This is not the authorization the service started. Start again at /authorize. "
            "Status page: /",
            status_code=400,
        )
    except (AuthorizationRevoked, SpotifyAuthError, httpx2.RequestError) as failure:
        # The type name only: the request behind it carried the code and the
        # verifier, and nothing derived from it belongs in the log.
        logger.warning("Spotify code exchange failed (%s)", type(failure).__name__)
        return PlainTextResponse(
            "Spotify could not complete the authorization. Try again at /authorize. "
            "Status page: /",
            status_code=502,
        )
    return RedirectResponse("/?authorized=1", status_code=303)


@app.get("/status")
async def status(request: Request) -> dict[str, Any]:
    """The believed playback state, the authorization and the board, as JSON.

    The fields are named one by one rather than dumped from the reader or the
    loop, so no token can reach this response by a field added elsewhere.
    `playback` and `authorization` are `null` when Spotify is not configured;
    the board and enhancement are reported either way.
    """
    reader = _reader(request)
    snapshot = reader.snapshot if reader is not None else None
    # One instant for staleness and `now`, so the page's durations agree with it.
    now = datetime.now(UTC)
    return {
        "configured": reader is not None,
        "playback": _playback_json(snapshot, now) if snapshot is not None else None,
        "authorization": _authorization_json(snapshot) if snapshot is not None else None,
        "board": _board_json(
            request.app.state.transport,
            request.app.state.display_task,
            request.app.state.display_status,
        ),
        "enhancement": _enhancement_json(request),
        "stale_after_seconds": STALE_AFTER_SECONDS,
        "now": now,
    }


class SettingsChange(BaseModel):
    """The body of `PUT /settings`: exactly one strict boolean, nothing else."""

    # A misspelt field is refused rather than silently dropped, and
    # `StrictBool` refuses `"false"` or `0` rather than coercing them.
    model_config = ConfigDict(extra="forbid")

    ai_enhanced: StrictBool


@app.put("/settings", response_model=None)
async def put_settings(request: Request, change: SettingsChange) -> dict[str, bool] | Response:
    """Save the owner's choices and answer with what was saved.

    Ungated like every route: the only gate is Cloudflare Access at the edge.
    A cross-site page can still post here without a CORS preflight if it
    sends `text/plain` or a form, and this route will spend money, so only an
    `application/json` body is accepted. FastAPI's default strict content
    type already refuses any other body, or one with no content type, with a
    422 before this runs; `tests/test_app.py` holds it to that.
    """
    if change.ai_enhanced and not request.app.state.enhance_available:
        return PlainTextResponse(
            "AI-enhanced covers need an OpenRouter key (OPENROUTER_API_KEY), "
            "and none is configured.",
            status_code=409,
        )
    previous: OwnerSettings = request.app.state.owner_settings
    updated = OwnerSettings(ai_enhanced=change.ai_enhanced)
    try:
        request.app.state.owner_settings_store.save(updated)
    except OSError as failure:
        # The in-memory choice follows the file, never runs ahead of it, so a
        # restart cannot quietly undo what the page showed as saved.
        logger.warning("saving the owner settings failed (%s)", type(failure).__name__)
        return PlainTextResponse("The setting could not be saved.", status_code=500)
    request.app.state.owner_settings = updated
    enhancer: Enhancer | None = request.app.state.enhancer
    if enhancer is not None and updated != previous:
        # A flip is the owner's way to ask again for the covers given up on.
        enhancer.forget()
    return {"ai_enhanced": updated.ai_enhanced}


def _enhancement_json(request: Request) -> dict[str, Any]:
    available: bool = request.app.state.enhance_available
    saved: OwnerSettings = request.app.state.owner_settings
    return {
        "available": available,
        # A choice saved while a key was configured does not count once the
        # key is gone: without one the service never enhances.
        "enabled": saved.ai_enhanced and available,
    }


def _playback_json(snapshot: PlaybackSnapshot, now: datetime) -> dict[str, Any]:
    return {
        "status": snapshot.status,
        "item": asdict(snapshot.item) if snapshot.item is not None else None,
        "failure": snapshot.failure,
        "failure_since": snapshot.failure_since,
        "last_read_at": snapshot.last_read_at,
        # The display rule's own verdict, so the page cannot disagree with
        # the marker on the board.
        "stale": is_stale(snapshot, now),
    }


def _authorization_json(snapshot: PlaybackSnapshot) -> dict[str, Any]:
    authorized_at = snapshot.authorized_at
    return {
        "authorized_at": authorized_at,
        "expires_around": (
            authorized_at + REFRESH_TOKEN_LIFETIME if authorized_at is not None else None
        ),
    }


def _board_json(
    transport: BoardTransport, task: asyncio.Task[None], display: DisplayStatus
) -> dict[str, Any]:
    running = not task.done()
    # Once the task has ended nothing drives reconnects, so whatever the
    # transport last believed, the link is as good as down.
    link = transport.link_state if running else LinkState.DOWN
    presentation = display.presentation
    return {
        "link": link,
        "task_running": running,
        "presentation": (
            {"kind": presentation.kind, "url": presentation.url, "stale": presentation.stale}
            if presentation is not None
            else None
        ),
        "push_failed": display.push_failed,
    }


if __name__ == "__main__":
    # ecosystem.config.js runs `python -m spotify_to_led.main`, so this
    # invocation has to keep working.
    startup_settings = load_settings()
    uvicorn.run(app, host=startup_settings.host, port=startup_settings.port)
