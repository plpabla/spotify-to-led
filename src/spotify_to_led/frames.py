"""The frames the service draws in code, without artwork.

Three frames, and one overlay, live here:

- `idle` is what the board shows when nothing is playing (FR-011): a small,
  dim music note centered on black. It is on for many hours a day, so it stays
  calm and dark, yet a lit glyph still says the service is running rather than
  dead.
- `playing_placeholder` is a temporary stand-in for the cover while
  something is playing: a bright play triangle, unmistakable next to idle at a
  glance. Cover rendering (S-04) replaces it.
- `test_pattern` is the hardware orientation diagnostic. The service no longer
  shows it; it stays for checking a board's wiring and colour order.
- `with_stale_marker` is FR-018's staleness marker, drawn over a frame the
  service can no longer vouch for: a small amber block in the top-right
  corner, inside a black ring so it reads against any cover, dark or bright.

The test pattern has four solid quadrants in colours that stay apart on a
32x32 LED matrix, so a transposed, mirrored or BGR-ordered push looks wrong
instead of looking like success:

- top left: red
- top right: green
- bottom left: blue
- bottom right: white

A channel swap turns the red quadrant blue and the blue quadrant red; a
transpose swaps green and blue; a horizontal mirror swaps red and green. The
written reference image is what the hardware smoke test is judged against.

None of these renders artwork; that is FR-007, in `rendering`.
"""

from PIL import Image, ImageDraw

TOP_LEFT: tuple[int, int, int] = (255, 0, 0)
TOP_RIGHT: tuple[int, int, int] = (0, 255, 0)
BOTTOM_LEFT: tuple[int, int, int] = (0, 0, 255)
BOTTOM_RIGHT: tuple[int, int, int] = (255, 255, 255)

# The brightest any channel of the idle frame may be. A starting value, to be
# tuned on the real board: dim enough for a dark room, bright enough to read.
IDLE_MAX_CHANNEL = 64
# A warm white derived from the ceiling, so tuning one number keeps the hue.
IDLE_COLOUR: tuple[int, int, int] = (
    IDLE_MAX_CHANNEL,
    IDLE_MAX_CHANNEL * 3 // 4,
    IDLE_MAX_CHANNEL // 2,
)
PLAYING_COLOUR: tuple[int, int, int] = (0, 255, 0)

# The stale marker: a 2x2-cell amber block on the 16x16 design grid, with a
# black ring around it. The ring is in board pixels, not cells, and does not
# scale: on the real 32x32 board a 1-cell (2 px) ring made the marker too big,
# and 1 px already separates the block from any cover. Amber rather than red,
# so it reads as a warning and not as an error.
STALE_MARKER_COLOUR: tuple[int, int, int] = (255, 176, 0)
STALE_MARKER_CELLS = 2
STALE_MARKER_RING_PIXELS = 1

# The note is designed on the 16x16 grid of the smallest board and scaled up
# by whole pixels, so every supported size (16, 32, 64) shows the same shape
# with no blurred edges. Its bounding box is exactly the glyph, and the
# margins around it are even on both axes, which keeps it centered after
# scaling.
_GRID = 16
_NOTE = (
    ".#..",
    ".##.",
    ".#.#",
    ".#..",
    "##..",
    "##..",
)


def test_pattern(size: int) -> Image.Image:
    """Return an RGB `(size, size)` image with four solid quadrant colours."""
    half = size // 2
    frame = Image.new("RGB", (size, size))
    frame.paste(TOP_LEFT, (0, 0, half, half))
    frame.paste(TOP_RIGHT, (half, 0, size, half))
    frame.paste(BOTTOM_LEFT, (0, half, half, size))
    frame.paste(BOTTOM_RIGHT, (half, half, size, size))
    return frame


def idle(size: int) -> Image.Image:
    """Return the idle frame: a dim eighth note centered on black."""
    scale = size // _GRID
    left = (_GRID - len(_NOTE[0])) // 2
    top = (_GRID - len(_NOTE)) // 2
    frame = Image.new("RGB", (size, size))
    for row, line in enumerate(_NOTE):
        for column, cell in enumerate(line):
            if cell == "#":
                x = (left + column) * scale
                y = (top + row) * scale
                frame.paste(IDLE_COLOUR, (x, y, x + scale, y + scale))
    return frame


def playing_placeholder(size: int) -> Image.Image:
    """Return a bright right-pointing play triangle about half the panel wide.

    Temporary: cover rendering (S-04) deletes it.
    """
    low = size // 4
    high = size - size // 4 - 1
    frame = Image.new("RGB", (size, size))
    ImageDraw.Draw(frame).polygon(
        [(low, low), (low, high), (high, size // 2)], fill=PLAYING_COLOUR
    )
    return frame


def with_stale_marker(frame: Image.Image) -> Image.Image:
    """Return a copy of `frame` with the stale marker in its top-right corner.

    The ring is black on every side, including the two along the panel edge,
    so the marker keeps the same shape whatever the cover has at the border.
    `frame` itself is left untouched.
    """
    scale = frame.width // _GRID
    ring = STALE_MARKER_RING_PIXELS
    block = STALE_MARKER_CELLS * scale
    outer = block + 2 * ring
    right = frame.width
    marked = frame.copy()
    marked.paste((0, 0, 0), (right - outer, 0, right, outer))
    marked.paste(
        STALE_MARKER_COLOUR, (right - outer + ring, ring, right - ring, ring + block)
    )
    return marked
