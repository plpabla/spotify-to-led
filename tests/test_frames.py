"""The test pattern and the pure image-to-wire conversion."""

from spotify_to_led import frames
from spotify_to_led.board.transport import frame_to_pixel_colors

# Imported as a module, not as a symbol: a bare `test_pattern` name in this
# namespace would be collected as a test case.

SIZE = 32


def test_pattern_is_a_32x32_rgb_image() -> None:
    frame = frames.test_pattern(SIZE)

    assert frame.mode == "RGB"
    assert frame.size == (SIZE, SIZE)


def test_pattern_has_four_solid_quadrants_in_the_documented_colours() -> None:
    frame = frames.test_pattern(SIZE)

    # One pixel inside each quadrant, well away from the seams.
    assert frame.getpixel((8, 8)) == (255, 0, 0), "top left should be red"
    assert frame.getpixel((24, 8)) == (0, 255, 0), "top right should be green"
    assert frame.getpixel((8, 24)) == (0, 0, 255), "bottom left should be blue"
    assert frame.getpixel((24, 24)) == (255, 255, 255), "bottom right should be white"

    colours = frame.getcolors()
    assert colours is not None
    assert sorted(count for count, _ in colours) == [256, 256, 256, 256], (
        "the four quadrants should be solid and equally sized"
    )


def test_pattern_corners_are_distinguishable_under_mirror_and_transpose() -> None:
    frame = frames.test_pattern(SIZE)
    corners = [
        frame.getpixel((0, 0)),
        frame.getpixel((SIZE - 1, 0)),
        frame.getpixel((0, SIZE - 1)),
        frame.getpixel((SIZE - 1, SIZE - 1)),
    ]

    assert len(set(corners)) == 4


def test_frame_to_pixel_colors_is_row_major_with_one_triple_per_pixel() -> None:
    frame = frames.test_pattern(SIZE)

    pixels = frame_to_pixel_colors(frame)

    assert len(pixels) == SIZE * SIZE == 1024
    assert pixels[0] == (255, 0, 0), "first pixel is the top left quadrant"
    assert pixels[SIZE - 1] == (0, 255, 0), "end of the first row is the top right"
    assert pixels[SIZE * (SIZE - 1)] == (0, 0, 255), "start of the last row is the bottom left"
    assert pixels[-1] == (255, 255, 255), "last pixel is the bottom right quadrant"


def test_frame_to_pixel_colors_walks_rows_not_columns() -> None:
    # A transposed conversion would return the same multiset of pixels, so
    # compare against an image whose rows and columns cannot be confused.
    frame = frames.test_pattern(SIZE)
    row_boundary = frame_to_pixel_colors(frame)[SIZE // 2 - 1 : SIZE // 2 + 1]

    assert row_boundary == [(255, 0, 0), (0, 255, 0)], (
        "the red-to-green step must fall mid-row, not mid-column"
    )
