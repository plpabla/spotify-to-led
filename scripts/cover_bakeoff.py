"""Show a set of covers on the board with each rendering candidate, for OQ3.

The PRD settles the cover treatment by looking at real covers on the real
board, from where the owner actually sits. This script puts every cover in a
set through every candidate in `spotify_to_led.rendering`, then repeats the
whole run with FR-018's stale marker over each one, so the marker can be
judged against the same covers. Each frame is held on the board for `--hold`
seconds while a label on stdout says what is showing.

Usage:

    uv run python scripts/cover_bakeoff.py [--hold SECONDS] <image-url-or-path>...

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
order, and one column per candidate without the marker followed by one per
candidate with it, named in the header. Each cell is the board frame enlarged
with nearest-neighbour scaling, so one LED stays one sharp square.
"""

import argparse
import asyncio
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import httpx2
from PIL import Image, ImageDraw

from spotify_to_led.board.factory import create_transport
from spotify_to_led.config import load_settings
from spotify_to_led.frames import with_stale_marker
from spotify_to_led.rendering import CANDIDATES

DEFAULT_HOLD_SECONDS = 4.0
DOWNLOAD_TIMEOUT_SECONDS = 10.0
CONTACT_SHEET_FILENAME = "bakeoff.png"
# Every cell is enlarged to about this many pixels wide, whatever the board
# size, so the sheet reads the same for a 16 and a 64 pixel panel.
CELL_TARGET_PIXELS = 256
CELL_GAP = 8
HEADER_HEIGHT = 24
SHEET_BACKGROUND: tuple[int, int, int] = (40, 40, 40)
LABEL_COLOUR: tuple[int, int, int] = (230, 230, 230)


@dataclass(frozen=True)
class Cover:
    """A source image and the short name it is announced by."""

    name: str
    image: Image.Image


@dataclass(frozen=True)
class Shot:
    """One rendered frame, and what it is a rendering of."""

    cover_index: int
    cover_name: str
    candidate: str
    marked: bool
    frame: Image.Image


async def load_covers(sources: list[str]) -> list[Cover]:
    """Read every source, from disk if it is an existing file, else over HTTP."""
    covers: list[Cover] = []
    async with httpx2.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS) as client:
        for source in sources:
            path = Path(source)
            if path.is_file():
                with Image.open(path) as image:
                    image.load()
                    covers.append(Cover(path.name, image.copy()))
                continue
            if urlparse(source).scheme not in ("http", "https"):
                raise SystemExit(f"not an existing file or an http(s) URL: {source}")
            response = await client.get(source)
            response.raise_for_status()
            image = Image.open(BytesIO(response.content))
            image.load()
            # Spotify image URLs end in a long hash; its tail is enough to
            # tell covers apart in the labels.
            tail = urlparse(source).path.rstrip("/").rsplit("/", 1)[-1]
            covers.append(Cover(f"...{tail[-8:]}", image))
    return covers


def render_shots(covers: list[Cover], size: int) -> list[Shot]:
    """Render every cover with every candidate, unmarked first, then marked."""
    shots: list[Shot] = []
    for marked in (False, True):
        for index, cover in enumerate(covers, start=1):
            for candidate, render in CANDIDATES.items():
                frame = render(cover.image, size)
                if marked:
                    frame = with_stale_marker(frame)
                shots.append(Shot(index, cover.name, candidate, marked, frame))
    return shots


def contact_sheet(shots: list[Shot], cover_count: int, size: int) -> Image.Image:
    """Lay the shots out with one row per cover and one column per variant."""
    scale = max(1, CELL_TARGET_PIXELS // size)
    cell = size * scale
    columns = [
        (candidate, marked) for marked in (False, True) for candidate in CANDIDATES
    ]
    width = len(columns) * cell + (len(columns) + 1) * CELL_GAP
    height = HEADER_HEIGHT + cover_count * cell + (cover_count + 1) * CELL_GAP
    sheet = Image.new("RGB", (width, height), SHEET_BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    for column, (candidate, marked) in enumerate(columns):
        left = CELL_GAP + column * (cell + CELL_GAP)
        label = f"{candidate} + marker" if marked else candidate
        draw.text((left, HEADER_HEIGHT // 3), label, fill=LABEL_COLOUR)
    for shot in shots:
        column = columns.index((shot.candidate, shot.marked))
        left = CELL_GAP + column * (cell + CELL_GAP)
        top = HEADER_HEIGHT + CELL_GAP + (shot.cover_index - 1) * (cell + CELL_GAP)
        enlarged = shot.frame.resize((cell, cell), Image.Resampling.NEAREST)
        sheet.paste(enlarged, (left, top))
    return sheet


async def run(sources: list[str], hold: float) -> None:
    settings = load_settings()
    size = settings.board_size
    covers = await load_covers(sources)
    shots = render_shots(covers, size)

    # Written before the board is touched, so a board that will not connect
    # still leaves something to review.
    settings.stub_frame_dir.mkdir(parents=True, exist_ok=True)
    sheet_path = settings.stub_frame_dir / CONTACT_SHEET_FILENAME
    contact_sheet(shots, len(covers), size).save(sheet_path, format="PNG")
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
                f" {shot.cover_name} | {shot.candidate}"
                f" | marker: {'yes' if shot.marked else 'no'}"
            )
            await transport.push_frame(shot.frame)
            await asyncio.sleep(hold)
    finally:
        await transport.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show covers on the board with each rendering candidate."
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
    arguments = parser.parse_args()
    asyncio.run(run(arguments.sources, arguments.hold))


if __name__ == "__main__":
    main()
