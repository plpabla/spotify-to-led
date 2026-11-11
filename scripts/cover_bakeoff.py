"""Show a set of covers on the board as the service renders them, with and without the marker.

The bake-off for OQ3 chose the cover treatment by looking at real covers on
the real board, from where the owner actually sits (FR-007). This script keeps
that setup for what still has to be judged the same way: tuning FR-018's stale
marker and S-05's no-artwork symbol, and re-checking the treatment on new
covers. It puts every cover in a set through `rendering.render_cover`, then
repeats the run with the stale marker over each one, so the marker is judged
against the same covers. Each frame is held on the board for `--hold` seconds
while a label on stdout says what is showing.

Usage:

    uv run python scripts/cover_bakeoff.py [--enhance] [--hold SECONDS] <image-url-or-path>...

Each argument that names an existing file is read from disk; anything else
must be an http(s) URL and is downloaded. Pick 6 to 10 covers for variety:
dark, bright, text-heavy, photographic, minimal. Spotify's own image URLs,
read from `/playback`, work as they are.

The transport comes from the usual settings (`load_settings`,
`create_transport`), so the same command drives the PNG stub on the laptop
and the real board on the Pi. On the Pi the board settings live in
`/etc/spotify-to-led/env`, which only pm2 points the service at, so the script
has to be pointed at it too; without that it falls back to `.env` and drives
the stub. The service has to be stopped first, because the board accepts only
one Bluetooth connection:

    pm2 stop spotify-to-led
    SPOTIFY_TO_LED_ENV_FILE=/etc/spotify-to-led/env \
        uv run python scripts/cover_bakeoff.py ...
    pm2 start spotify-to-led

Before anything is pushed, a contact sheet is written to
`<stub_frame_dir>/bakeoff.png` (`var/frames/bakeoff.png` by default, which is
gitignored) for review on a laptop. It has one row per cover, in argument
order, and two columns: the cover, then the cover with the marker, named in
the header. Each cell is the board frame enlarged
with nearest-neighbour scaling, so one LED stays one sharp square.

`--enhance` turns the run into the bake-off for S-08 instead: whether a cover
redrawn by an image model (`enhance.generate`) reads better on the board than
the plain downscale, and how long a generation takes. Each cover is sent as
it was read, so pass the widest image there is (Spotify's 640 px URL). The
answer is shrunk to a board frame two ways: by `render_cover`'s area average
(BOX), as the service does today, and by LANCZOS, as the proof of concept
did. The board shows each cover plain, then enhanced BOX, then enhanced
LANCZOS, before moving on to the next, and the contact sheet's three columns
are the same three frames. The marker pass is left out: the marker is drawn
the same way over any frame, and this run is about the cover.

Every generation is a paid OpenRouter call, about $0.009 with the default
model, so `--enhance` needs `OPENROUTER_API_KEY` in the settings
(`OPENROUTER_MODEL` picks another model), and without it the script stops
before anything is downloaded. Each call's wall time and reported cost are
printed as it returns, and a summary after the last one. A cover whose
generation fails is reported and the run goes on: its enhanced cells stay
empty, and the board shows it plain only.
"""

import argparse
import asyncio
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import httpx2
from PIL import Image, ImageDraw

from spotify_to_led.board.factory import create_transport
from spotify_to_led.config import Settings, load_settings
from spotify_to_led.enhance import EnhanceError, generate
from spotify_to_led.frames import with_stale_marker
from spotify_to_led.rendering import render_cover

DEFAULT_HOLD_SECONDS = 4.0
DOWNLOAD_TIMEOUT_SECONDS = 10.0
# Generous on purpose: how long a generation takes is one of the things this
# run measures, so a slow answer should be timed rather than cut off.
ENHANCE_TIMEOUT_SECONDS = 120.0
CONTACT_SHEET_FILENAME = "bakeoff.png"
# Every cell is enlarged to about this many pixels wide, whatever the board
# size, so the sheet reads the same for a 16 and a 64 pixel panel.
CELL_TARGET_PIXELS = 256
CELL_GAP = 8
HEADER_HEIGHT = 24
SHEET_BACKGROUND: tuple[int, int, int] = (40, 40, 40)
LABEL_COLOUR: tuple[int, int, int] = (230, 230, 230)
# What a shot is a rendering of; each names its column on the contact sheet.
PLAIN = "cover"
MARKED = "cover + marker"
ENHANCED_BOX = "enhanced (BOX)"
ENHANCED_LANCZOS = "enhanced (LANCZOS)"
MARKER_COLUMNS: tuple[str, ...] = (PLAIN, MARKED)
ENHANCE_COLUMNS: tuple[str, ...] = (PLAIN, ENHANCED_BOX, ENHANCED_LANCZOS)


@dataclass(frozen=True)
class Cover:
    """A source image, the bytes it was read from, and the short name it is announced by."""

    name: str
    image: Image.Image
    # What `--enhance` sends: the image exactly as read, and its type.
    data: bytes
    mime: str


@dataclass(frozen=True)
class Shot:
    """One rendered frame, and what it is a rendering of."""

    cover_index: int
    cover_name: str
    treatment: str
    frame: Image.Image


async def load_covers(client: httpx2.AsyncClient, sources: list[str]) -> list[Cover]:
    """Read every source, from disk if it is an existing file, else over HTTP."""
    covers: list[Cover] = []
    for source in sources:
        path = Path(source)
        if path.is_file():
            data = path.read_bytes()
            name = path.name
        else:
            if urlparse(source).scheme not in ("http", "https"):
                raise SystemExit(f"not an existing file or an http(s) URL: {source}")
            response = await client.get(source)
            response.raise_for_status()
            data = response.content
            # Spotify image URLs end in a long hash; its tail is enough to
            # tell covers apart in the labels.
            tail = urlparse(source).path.rstrip("/").rsplit("/", 1)[-1]
            name = f"...{tail[-8:]}"
        image = Image.open(BytesIO(data))
        image.load()
        # The type the bytes really are, whatever the file name or the
        # server's header says.
        mime = Image.MIME.get(image.format or "", "application/octet-stream")
        covers.append(Cover(name, image, data, mime))
    return covers


def render_shots(covers: list[Cover], size: int) -> list[Shot]:
    """Render every cover, unmarked first, then marked."""
    shots: list[Shot] = []
    for marked in (False, True):
        for index, cover in enumerate(covers, start=1):
            frame = render_cover(cover.image, size)
            if marked:
                frame = with_stale_marker(frame)
            shots.append(Shot(index, cover.name, MARKED if marked else PLAIN, frame))
    return shots


def lanczos_frame(image: Image.Image, size: int) -> Image.Image:
    """Shrink `image` to a board frame as the proof of concept did, with LANCZOS."""
    # `render_cover` at the image's own side is only its square crop and RGB
    # conversion, since a resize to the same size changes nothing; so both
    # downscales start from exactly the same square.
    square = render_cover(image, min(image.size))
    return square.resize((size, size), Image.Resampling.LANCZOS)


async def enhance_shots(
    client: httpx2.AsyncClient, covers: list[Cover], settings: Settings
) -> list[Shot]:
    """Generate each cover once: per cover the plain frame, then both enhanced ones.

    Prints each call's wall time and cost as it returns, then a summary.
    """
    assert settings.openrouter_api_key is not None
    api_key = settings.openrouter_api_key.get_secret_value()
    size = settings.board_size
    shots: list[Shot] = []
    durations: list[float] = []
    total_cost = 0.0
    unreported_costs = 0
    failures = 0
    print(f"generating {len(covers)} covers with {settings.openrouter_model}")
    for index, cover in enumerate(covers, start=1):
        label = f"cover {index}/{len(covers)} {cover.name}"
        shots.append(Shot(index, cover.name, PLAIN, render_cover(cover.image, size)))
        started = time.perf_counter()
        try:
            generated = await generate(
                client,
                api_key,
                settings.openrouter_model,
                cover.data,
                cover.mime,
                timeout=ENHANCE_TIMEOUT_SECONDS,
            )
        except EnhanceError as error:
            elapsed = time.perf_counter() - started
            failures += 1
            print(f"  {label}: failed after {elapsed:.1f} s ({type(error).__name__}: {error})")
            continue
        elapsed = time.perf_counter() - started
        durations.append(elapsed)
        if generated.cost is None:
            unreported_costs += 1
            cost = "cost not reported"
        else:
            total_cost += generated.cost
            cost = f"${generated.cost:.4f}"
        print(f"  {label}: {elapsed:.1f} s, {cost}")
        box = render_cover(generated.image, size)
        lanczos = lanczos_frame(generated.image, size)
        shots.append(Shot(index, cover.name, ENHANCED_BOX, box))
        shots.append(Shot(index, cover.name, ENHANCED_LANCZOS, lanczos))

    # Times are of the calls that returned an image: a failure's time says
    # more about the failure than about how long a generation takes.
    summary = f"{len(durations)} generated, {failures} failed"
    if durations:
        mean = sum(durations) / len(durations)
        summary += f"; time mean {mean:.1f} s, max {max(durations):.1f} s"
    summary += f"; total cost ${total_cost:.4f}"
    if unreported_costs:
        summary += f" (and {unreported_costs} not reported)"
    print(summary)
    return shots


def contact_sheet(
    shots: list[Shot], labels: tuple[str, ...], cover_count: int, size: int
) -> Image.Image:
    """Lay the shots out with one row per cover and one column per label, in order."""
    scale = max(1, CELL_TARGET_PIXELS // size)
    cell = size * scale
    width = len(labels) * cell + (len(labels) + 1) * CELL_GAP
    height = HEADER_HEIGHT + cover_count * cell + (cover_count + 1) * CELL_GAP
    sheet = Image.new("RGB", (width, height), SHEET_BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    for column, label in enumerate(labels):
        left = CELL_GAP + column * (cell + CELL_GAP)
        draw.text((left, HEADER_HEIGHT // 3), label, fill=LABEL_COLOUR)
    for shot in shots:
        column = labels.index(shot.treatment)
        left = CELL_GAP + column * (cell + CELL_GAP)
        top = HEADER_HEIGHT + CELL_GAP + (shot.cover_index - 1) * (cell + CELL_GAP)
        enlarged = shot.frame.resize((cell, cell), Image.Resampling.NEAREST)
        sheet.paste(enlarged, (left, top))
    return sheet


async def run(sources: list[str], hold: float, enhance: bool) -> None:
    settings = load_settings()
    size = settings.board_size
    if enhance and settings.openrouter_api_key is None:
        raise SystemExit("--enhance needs OPENROUTER_API_KEY in the settings")
    # One client for the downloads and the generations alike; a generation
    # passes its own, longer timeout.
    async with httpx2.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS) as client:
        covers = await load_covers(client, sources)
        if enhance:
            shots = await enhance_shots(client, covers, settings)
            labels = ENHANCE_COLUMNS
        else:
            shots = render_shots(covers, size)
            labels = MARKER_COLUMNS

    # Written before the board is touched, so a board that will not connect
    # still leaves something to review.
    settings.stub_frame_dir.mkdir(parents=True, exist_ok=True)
    sheet_path = settings.stub_frame_dir / CONTACT_SHEET_FILENAME
    contact_sheet(shots, labels, len(covers), size).save(sheet_path, format="PNG")
    print(f"contact sheet: {sheet_path}")
    for index, cover in enumerate(covers, start=1):
        print(f"  row {index}: {cover.name}")

    transport = create_transport(settings)
    try:
        await transport.connect()
        print(f"connected to the {settings.board_transport} board ({size}x{size})")
        for number, shot in enumerate(shots, start=1):
            print(
                f"[{number}/{len(shots)}] cover {shot.cover_index}/{len(covers)}"
                f" {shot.cover_name} | {shot.treatment}"
            )
            await transport.push_frame(shot.frame)
            await asyncio.sleep(hold)
    finally:
        await transport.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Show covers on the board as rendered, with and without the stale marker,"
            " or plain and AI-enhanced with --enhance."
        )
    )
    parser.add_argument(
        "sources", nargs="+", metavar="image", help="an image URL or a local file path"
    )
    parser.add_argument(
        "--hold",
        type=float,
        default=DEFAULT_HOLD_SECONDS,
        metavar="SECONDS",
        help=f"how long each frame stays on the board (default {DEFAULT_HOLD_SECONDS})",
    )
    parser.add_argument(
        "--enhance",
        action="store_true",
        help=(
            "show each cover plain and AI-enhanced instead of with the marker;"
            " needs OPENROUTER_API_KEY, and every cover is a paid OpenRouter call"
        ),
    )
    arguments = parser.parse_args()
    asyncio.run(run(arguments.sources, arguments.hold, arguments.enhance))


if __name__ == "__main__":
    main()
