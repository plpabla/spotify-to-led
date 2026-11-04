"""The service entrypoint: settings, one supervised board task, and `/healthz`.

This single process is both the board driver and the observability surface, so
the two are deliberately kept apart: the board work runs in a background task
that swallows and logs its own failures, and the route answers whether or not
the board is reachable. A board fault is something to report, never something
that takes the server down with it.
"""

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import uvicorn
from fastapi import FastAPI

from spotify_to_led import frames
from spotify_to_led.board.factory import create_transport
from spotify_to_led.board.transport import BoardTransport
from spotify_to_led.config import Settings

logger = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    """Send the root logger to stdout at `level`, so journald captures it."""
    logging.basicConfig(
        level=level.upper(),
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


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


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Own the transport and the one background task for the process lifetime."""
    settings = Settings()
    configure_logging(settings.log_level)
    transport = create_transport(settings)
    task = asyncio.create_task(push_test_pattern(transport, settings.board_size))
    try:
        yield
    finally:
        # The task usually finished long ago, so cancellation has to tolerate
        # an already-completed task rather than assume a pending one.
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await transport.close()


app = FastAPI(title="spotify-to-led", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Report that the process is serving, and nothing else.

    Link state, the playing item and re-authorization belong to FR-015, which
    is scheduled for S-07. Keeping them out of here is what lets this route
    stay truthful while the board is down.
    """
    return {"status": "ok"}


if __name__ == "__main__":
    # infrastructure.md's unit file runs `python -m spotify_to_led.main`, so
    # this invocation has to keep working.
    startup_settings = Settings()
    uvicorn.run(app, host=startup_settings.host, port=startup_settings.port)
