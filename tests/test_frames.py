"""The code-drawn frames and the pure image-to-wire conversion."""

from collections.abc import Callable

import pytest
from PIL import Image

from spotify_to_led import frames
from spotify_to_led.board.transport import frame_to_pixel_colors, validate_frame
from spotify_to_led.config import SUPPORTED_BOARD_SIZES

# Imported as a module, not as a symbol: a bare `test_pattern` name in this
# namespace would be collected as a test case.

SIZE = 32
SUPPORTED_SIZES = (16, 32, 64)
BLACK = (0, 0, 0)


def _lit_pixels(frame: Image.Image) -> list[tuple[int, int]]:
    width, height = frame.size
    return [
        (x, y) for y in range(height) for x in range(width) if frame.getpixel((x, y)) != BLACK
    ]


@pytest.mark.parametrize("size", SUPPORTED_SIZES)
def test_idle_and_placeholder_are_valid_frames_at_every_supported_size(size: int) -> None:
    for frame in (frames.idle(size), frames.playing_placeholder(size)):
        assert frame.mode == "RGB"
        assert frame.size == (size, size)
        validate_frame(frame, size)


@pytest.mark.parametrize("size", SUPPORTED_SIZES)
def test_idle_never_exceeds_the_brightness_ceiling(size: int) -> None:
    frame = frames.idle(size)

    brightest = max(max(pixel) for pixel in frame_to_pixel_colors(frame))

    assert 0 < brightest <= frames.IDLE_MAX_CHANNEL


@pytest.mark.parametrize("size", SUPPORTED_SIZES)
def test_idle_is_mostly_black(size: int) -> None:
    lit = _lit_pixels(frames.idle(size))

    assert 0 < len(lit) < size * size // 4


@pytest.mark.parametrize("size", SUPPORTED_SIZES)
def test_idle_glyph_is_centered_within_one_pixel(size: int) -> None:
    lit = _lit_pixels(frames.idle(size))
    xs = [x for x, _ in lit]
    ys = [y for _, y in lit]

    left, right = min(xs), size - 1 - max(xs)
    top, bottom = min(ys), size - 1 - max(ys)

    assert abs(left - right) <= 1, f"horizontal margins {left} and {right}"
    assert abs(top - bottom) <= 1, f"vertical margins {top} and {bottom}"


@pytest.mark.parametrize("size", SUPPORTED_SIZES)
def test_placeholder_lit_pixels_are_at_full_brightness(size: int) -> None:
    frame = frames.playing_placeholder(size)
    lit = _lit_pixels(frame)

    assert lit
    assert all(255 in frame.getpixel(point) for point in lit)


@pytest.mark.parametrize("size", SUPPORTED_SIZES)
def test_idle_differs_from_the_placeholder(size: int) -> None:
    idle = frame_to_pixel_colors(frames.idle(size))
    placeholder = frame_to_pixel_colors(frames.playing_placeholder(size))

    assert idle != placeholder


@pytest.mark.parametrize("size", SUPPORTED_SIZES)
def test_no_artwork_is_a_valid_frame_at_every_supported_size(size: int) -> None:
    frame = frames.no_artwork(size)

    assert frame.mode == "RGB"
    assert frame.size == (size, size)
    validate_frame(frame, size)


@pytest.mark.parametrize("size", SUPPORTED_SIZES)
def test_no_artwork_differs_from_idle_and_the_placeholder(size: int) -> None:
    symbol = frame_to_pixel_colors(frames.no_artwork(size))

    assert symbol != frame_to_pixel_colors(frames.idle(size))
    assert symbol != frame_to_pixel_colors(frames.playing_placeholder(size))


@pytest.mark.parametrize("size", SUPPORTED_SIZES)
def test_no_artwork_is_brighter_than_idle_and_not_green(size: int) -> None:
    # Brighter, so it does not read as nothing playing; neutral, so it does
    # not read as the placeholder's cover on its way.
    lit = {pixel for pixel in frame_to_pixel_colors(frames.no_artwork(size)) if pixel != BLACK}

    assert lit == {frames.NO_ARTWORK_COLOUR}
    assert max(frames.NO_ARTWORK_COLOUR) > frames.IDLE_MAX_CHANNEL
    assert len(set(frames.NO_ARTWORK_COLOUR)) == 1


@pytest.mark.parametrize("size", SUPPORTED_SIZES)
def test_no_artwork_is_a_centered_disc_with_a_hole(size: int) -> None:
    frame = frames.no_artwork(size)
    lit = _lit_pixels(frame)
    xs = [x for x, _ in lit]
    ys = [y for _, y in lit]

    assert min(xs) == size - 1 - max(xs), "horizontal margins differ"
    assert min(ys) == size - 1 - max(ys), "vertical margins differ"
    assert frame.getpixel((size // 2, size // 2)) == BLACK, "the centre hole is lit"


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


def _busy_frame(size: int) -> Image.Image:
    """Return a frame with no black and no marker-coloured pixel anywhere."""
    frame = Image.new("RGB", (size, size))
    for y in range(size):
        for x in range(size):
            frame.putpixel((x, y), (x * 255 // size, y * 255 // size, 200))
    return frame


def _marker_regions(size: int) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """Return the block's pixels and the ring's pixels, from the constants."""
    scale = size // 16
    ring = frames.STALE_MARKER_RING_PIXELS
    block = frames.STALE_MARKER_CELLS * scale
    outer = block + 2 * ring
    region = {(x, y) for x in range(size - outer, size) for y in range(outer)}
    block_pixels = {
        (x, y)
        for x in range(size - outer + ring, size - ring)
        for y in range(ring, ring + block)
    }
    return block_pixels, region - block_pixels


@pytest.mark.parametrize("size", SUPPORTED_BOARD_SIZES)
def test_stale_marker_draws_an_amber_block_inside_a_black_ring(size: int) -> None:
    marked = frames.with_stale_marker(_busy_frame(size))
    block, ring = _marker_regions(size)

    validate_frame(marked, size)
    assert block and ring
    assert all(marked.getpixel(point) == frames.STALE_MARKER_COLOUR for point in block)
    assert all(marked.getpixel(point) == BLACK for point in ring)


@pytest.mark.parametrize("size", SUPPORTED_BOARD_SIZES)
def test_stale_marker_sits_in_the_top_right_corner(size: int) -> None:
    block, ring = _marker_regions(size)

    assert (size - 1, 0) in ring
    assert all(x >= size // 2 and y < size // 2 for x, y in block | ring)


@pytest.mark.parametrize("size", SUPPORTED_BOARD_SIZES)
def test_stale_marker_leaves_the_rest_of_the_frame_unchanged(size: int) -> None:
    original = _busy_frame(size)
    marked = frames.with_stale_marker(original)
    block, ring = _marker_regions(size)

    changed = {
        (x, y)
        for y in range(size)
        for x in range(size)
        if marked.getpixel((x, y)) != original.getpixel((x, y))
    }

    assert changed == block | ring


@pytest.mark.parametrize("glyph", [frames.idle, frames.playing_placeholder, frames.no_artwork])
@pytest.mark.parametrize("size", SUPPORTED_BOARD_SIZES)
def test_stale_marker_clears_every_glyph(size: int, glyph: Callable[[int], Image.Image]) -> None:
    # Idle, the placeholder and the disc are marked too, and the marker must
    # not cut into them.
    block, ring = _marker_regions(size)

    assert not set(_lit_pixels(glyph(size))) & (block | ring)


@pytest.mark.parametrize("size", SUPPORTED_BOARD_SIZES)
def test_stale_marker_does_not_mutate_its_input(size: int) -> None:
    original = _busy_frame(size)
    before = frame_to_pixel_colors(original)

    frames.with_stale_marker(original)

    assert frame_to_pixel_colors(original) == before


def _generating_marker_dot(size: int) -> set[tuple[int, int]]:
    """Return the dot's pixels, from the constants."""
    inset = frames.GENERATING_MARKER_INSET_PIXELS
    dot = frames.GENERATING_MARKER_CELLS * (size // 16)
    return {(x, y) for x in range(inset, inset + dot) for y in range(inset, inset + dot)}


@pytest.mark.parametrize("size", SUPPORTED_BOARD_SIZES)
def test_generating_marker_draws_a_green_dot(size: int) -> None:
    marked = frames.with_generating_marker(_busy_frame(size))
    dot = _generating_marker_dot(size)

    validate_frame(marked, size)
    assert len(dot) == (size // 16) ** 2
    assert all(marked.getpixel(point) == frames.GENERATING_MARKER_COLOUR for point in dot)


def test_generating_marker_is_a_dimmer_green_than_the_placeholder() -> None:
    red, green, blue = frames.GENERATING_MARKER_COLOUR

    assert green > red and green > blue
    assert green < frames.PLAYING_COLOUR[1]


@pytest.mark.parametrize("size", SUPPORTED_BOARD_SIZES)
def test_generating_marker_sits_one_pixel_in_from_the_top_left_corner(size: int) -> None:
    dot = _generating_marker_dot(size)

    assert min(x for x, _ in dot) == 1 and min(y for _, y in dot) == 1
    assert all(x < size // 2 and y < size // 2 for x, y in dot)


@pytest.mark.parametrize("size", SUPPORTED_BOARD_SIZES)
def test_generating_marker_is_half_the_stale_markers_size(size: int) -> None:
    block, _ = _marker_regions(size)

    assert 4 * len(_generating_marker_dot(size)) == len(block)


@pytest.mark.parametrize("size", SUPPORTED_BOARD_SIZES)
def test_generating_marker_has_no_ring_and_leaves_the_rest_unchanged(size: int) -> None:
    original = _busy_frame(size)
    marked = frames.with_generating_marker(original)

    changed = {
        (x, y)
        for y in range(size)
        for x in range(size)
        if marked.getpixel((x, y)) != original.getpixel((x, y))
    }

    # Only the dot: the cover shows right up to it, corner pixel included.
    assert changed == _generating_marker_dot(size)


@pytest.mark.parametrize("size", SUPPORTED_BOARD_SIZES)
def test_generating_marker_does_not_mutate_its_input(size: int) -> None:
    original = _busy_frame(size)
    before = frame_to_pixel_colors(original)

    frames.with_generating_marker(original)

    assert frame_to_pixel_colors(original) == before


@pytest.mark.parametrize("size", SUPPORTED_BOARD_SIZES)
def test_generating_and_stale_markers_do_not_cover_each_other(size: int) -> None:
    dot = _generating_marker_dot(size)
    block, block_ring = _marker_regions(size)

    assert not dot & (block | block_ring)
    for both in (
        frames.with_stale_marker(frames.with_generating_marker(_busy_frame(size))),
        frames.with_generating_marker(frames.with_stale_marker(_busy_frame(size))),
    ):
        assert all(both.getpixel(point) == frames.GENERATING_MARKER_COLOUR for point in dot)
        assert all(both.getpixel(point) == frames.STALE_MARKER_COLOUR for point in block)
        assert all(both.getpixel(point) == BLACK for point in block_ring)
