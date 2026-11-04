"""The test pattern this change pushes to the board.

Four solid quadrants in colours that stay apart on a 32x32 LED matrix, so a
transposed, mirrored or BGR-ordered push looks wrong instead of looking like
success:

- top left: red
- top right: green
- bottom left: blue
- bottom right: white

A channel swap turns the red quadrant blue and the blue quadrant red; a
transpose swaps green and blue; a horizontal mirror swaps red and green. The
written reference image is what the hardware smoke test is judged against.

This is a diagnostic pattern, not artwork rendering, and not a stand-in for
FR-007.
"""

from PIL import Image

TOP_LEFT: tuple[int, int, int] = (255, 0, 0)
TOP_RIGHT: tuple[int, int, int] = (0, 255, 0)
BOTTOM_LEFT: tuple[int, int, int] = (0, 0, 255)
BOTTOM_RIGHT: tuple[int, int, int] = (255, 255, 255)


def test_pattern(size: int) -> Image.Image:
    """Return an RGB `(size, size)` image with four solid quadrant colours."""
    half = size // 2
    frame = Image.new("RGB", (size, size))
    frame.paste(TOP_LEFT, (0, 0, half, half))
    frame.paste(TOP_RIGHT, (half, 0, size, half))
    frame.paste(BOTTOM_LEFT, (0, half, half, size))
    frame.paste(BOTTOM_RIGHT, (half, half, size, size))
    return frame
