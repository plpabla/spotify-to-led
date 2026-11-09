"""The cover rendering candidates, against square, non-square and odd-mode sources."""

import pytest
from PIL import Image

from spotify_to_led import rendering
from spotify_to_led.board.transport import validate_frame
from spotify_to_led.config import SUPPORTED_BOARD_SIZES

CANDIDATE_NAMES = tuple(rendering.CANDIDATES)


def _gradient(width: int, height: int) -> Image.Image:
    """Return an RGB image with no two neighbouring pixels alike."""
    image = Image.new("RGB", (width, height))
    for y in range(height):
        for x in range(width):
            image.putpixel((x, y), (x * 255 // width, y * 255 // height, 128))
    return image


def _sources() -> dict[str, Image.Image]:
    square = _gradient(100, 100)
    rgba = square.convert("RGBA")
    rgba.putalpha(128)
    transparent_palette = square.convert("P")
    transparent_palette.info["transparency"] = 0
    return {
        "square": square,
        "wide": _gradient(160, 90),
        "tall": _gradient(90, 160),
        "smaller than the board": _gradient(10, 10),
        "RGBA": rgba,
        "P": square.convert("P"),
        "P with transparency": transparent_palette,
        "L": square.convert("L"),
        "LA": square.convert("LA"),
        "CMYK": square.convert("CMYK"),
    }


SOURCES = _sources()


@pytest.mark.parametrize("size", SUPPORTED_BOARD_SIZES)
@pytest.mark.parametrize("candidate", CANDIDATE_NAMES)
@pytest.mark.parametrize("source_name", tuple(SOURCES))
def test_every_candidate_returns_a_valid_frame(
    size: int, candidate: str, source_name: str
) -> None:
    frame = rendering.CANDIDATES[candidate](SOURCES[source_name], size)

    validate_frame(frame, size)


@pytest.mark.parametrize("candidate", CANDIDATE_NAMES)
def test_a_non_square_source_is_center_cropped(candidate: str) -> None:
    # Red bars on either side of a green square: a center crop keeps only
    # green, while squashing the whole image would bring red in at the edges.
    source = Image.new("RGB", (300, 100), (255, 0, 0))
    source.paste((0, 255, 0), (100, 0, 200, 100))

    frame = rendering.CANDIDATES[candidate](source, 16)

    for x in (0, 15):
        red, green, _ = frame.getpixel((x, 8))
        assert green > red, f"pixel {x} should come from the green center"


@pytest.mark.parametrize("candidate", CANDIDATE_NAMES)
def test_transparency_is_composited_on_black(candidate: str) -> None:
    source = Image.new("RGBA", (64, 64), (255, 255, 255, 0))

    frame = rendering.CANDIDATES[candidate](source, 16)

    assert frame.getcolors() == [(256, (0, 0, 0))]


def test_downscale_averages_by_area() -> None:
    # A 1-pixel checkerboard of black and white averages to mid grey, where
    # point sampling would return pure black or pure white.
    source = Image.new("RGB", (64, 64))
    for y in range(64):
        for x in range(64):
            if (x + y) % 2:
                source.putpixel((x, y), (255, 255, 255))

    frame = rendering.render_downscale(source, 16)

    assert all(120 <= channel <= 135 for channel in frame.getpixel((8, 8)))


def test_palette_candidate_uses_at_most_the_palette_size() -> None:
    frame = rendering.render_palette(_gradient(200, 200), 64)

    colours = frame.getcolors(maxcolors=64 * 64)
    assert colours is not None
    assert len(colours) <= rendering.PALETTE_COLOURS


def test_boosted_candidate_is_more_saturated_than_the_plain_downscale() -> None:
    source = Image.new("RGB", (64, 64), (160, 100, 100))

    plain = rendering.render_downscale(source, 16).getpixel((8, 8))
    boosted = rendering.render_boosted(source, 16).getpixel((8, 8))

    assert max(boosted) - min(boosted) > max(plain) - min(plain)
