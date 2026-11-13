"""The frames the service draws in code, without artwork.

Four frames, and two overlays, live here:

- `idle` is what the board shows when nothing is playing (FR-011): a small,
  dim music note centered on black. It is on for many hours a day, so it stays
  calm and dark, yet a lit glyph still says the service is running rather than
  dead.
- `playing_placeholder` stands in for the cover while something is playing
  and its artwork exists but is not on hand: not downloaded yet, or failed to
  download. A bright play triangle, unmistakable next to idle at a glance.
- `no_artwork` is FR-010's generic symbol, for an item that has no artwork at
  all, such as an ad or a local file: a vinyl disc with a centre hole, in a
  neutral grey. Round where the triangle is pointed and the note is narrow,
  brighter than idle so it does not read as "nothing playing", and not green
  so it does not read as "cover on its way".
- `test_pattern` is the hardware orientation diagnostic. The service no longer
  shows it; it stays for checking a board's wiring and colour order.
- `with_stale_marker` is FR-018's staleness marker, drawn over a frame the
  service can no longer vouch for, a cover or idle, the placeholder or the
  disc: a small amber block in the top-right corner, inside a black ring so
  it reads against any cover, dark or bright. At every supported size it
  clears the note, the triangle and the disc, so it never cuts into a glyph.
- `with_spend_bar` is drawn over the plain cover while its AI-enhanced
  version (FR-019) is being generated, and is the board's only sign that one
  is: today's AI spend against the owner's daily limit, as a bar along the
  bottom pixel row. The whole row is black and its left part filled in
  proportion to the spend, so an empty bar still reads as a bar. The fill is
  green, because green already means "on its way" on this board, from the
  placeholder's triangle, then the stale marker's amber from 75 % and red
  from 90 %, so a limit about to stop the enhancement shows before it does.
  It is one board pixel high at every size, and never meets the stale marker
  at the top.

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

import math

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
# A neutral grey: well above the idle ceiling, so the disc says something is
# playing, yet short of full white, so it does not glare like a cover's
# highlights. A starting value, to be judged on the real board (OQ4).
NO_ARTWORK_COLOUR: tuple[int, int, int] = (160, 160, 160)

# The stale marker: a 2x2-cell amber block on the 16x16 design grid, with a
# black ring around it. The ring is in board pixels, not cells, and does not
# scale: on the real 32x32 board a 1-cell (2 px) ring made the marker too big,
# and 1 px already separates the block from any cover. Amber rather than red,
# so it reads as a warning and not as an error.
STALE_MARKER_COLOUR: tuple[int, int, int] = (255, 176, 0)
STALE_MARKER_CELLS = 2
STALE_MARKER_RING_PIXELS = 1

# The spend bar: one board pixel high, not scaled by the design grid, so it
# takes as little of the cover as the board allows. Filled green below the
# first threshold, amber below the second, red from there; the fractions are
# of the owner's daily limit. The green is dimmer than the placeholder's, so
# the bar hints rather than shouts over the cover.
SPEND_BAR_GREEN: tuple[int, int, int] = (0, 160, 0)
SPEND_BAR_AMBER_FROM = 0.75
SPEND_BAR_RED_FROM = 0.90
SPEND_BAR_RED: tuple[int, int, int] = (255, 0, 0)

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
# The vinyl disc, on the same grid and by the same rules as the note: 12 cells
# across, so a 2-cell margin on every side, with a 2x2 hole in the middle.
_DISC = (
    "....####....",
    "..########..",
    ".##########.",
    ".##########.",
    "############",
    "#####..#####",
    "#####..#####",
    "############",
    ".##########.",
    ".##########.",
    "..########..",
    "....####....",
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
    return _glyph(_NOTE, IDLE_COLOUR, size)


def no_artwork(size: int) -> Image.Image:
    """Return the no-artwork symbol: a grey vinyl disc with a hole, centered on black."""
    return _glyph(_DISC, NO_ARTWORK_COLOUR, size)


def _glyph(cells: tuple[str, ...], colour: tuple[int, int, int], size: int) -> Image.Image:
    """Draw a 16x16-grid design, `#` lit, centered and scaled by whole pixels."""
    scale = size // _GRID
    left = (_GRID - len(cells[0])) // 2
    top = (_GRID - len(cells)) // 2
    frame = Image.new("RGB", (size, size))
    for row, line in enumerate(cells):
        for column, cell in enumerate(line):
            if cell == "#":
                x = (left + column) * scale
                y = (top + row) * scale
                frame.paste(colour, (x, y, x + scale, y + scale))
    return frame


def playing_placeholder(size: int) -> Image.Image:
    """Return a bright right-pointing play triangle about half the panel wide.

    The stand-in for a cover that exists but the service does not have yet.
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
    so the marker keeps the same shape whatever the frame has at the border.
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


def with_spend_bar(frame: Image.Image, fraction: float) -> Image.Image:
    """Return a copy of `frame` with the spend bar along its bottom pixel row.

    The row is black, and its leftmost `fraction` of pixels, clamped to 0..1
    and rounded up so any spend at all shows, is filled in the colour for that
    fraction. `frame` itself is left untouched.
    """
    width = frame.width
    bottom = frame.height - 1
    filled = math.ceil(min(max(fraction, 0.0), 1.0) * width)
    if fraction < SPEND_BAR_AMBER_FROM:
        colour = SPEND_BAR_GREEN
    elif fraction < SPEND_BAR_RED_FROM:
        colour = STALE_MARKER_COLOUR
    else:
        colour = SPEND_BAR_RED
    barred = frame.copy()
    barred.paste((0, 0, 0), (0, bottom, width, bottom + 1))
    if filled:
        barred.paste(colour, (0, bottom, filled, bottom + 1))
    return barred
