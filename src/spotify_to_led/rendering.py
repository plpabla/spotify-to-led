"""Candidate treatments that turn a cover image into a board frame.

A 640 px album cover shrunk to a 16, 32 or 64 px LED panel loses most of what
made it recognisable, and how much survives depends on the treatment. The PRD
leaves that choice (OQ3) to be made by looking at real covers on the real
board, so three candidates live here side by side for the bake-off script:

- `render_downscale` is the plain area average, the honest baseline.
- `render_boosted` is the same downscale with contrast and saturation raised,
  because an LED panel next to a lamp looks washed out compared with a screen.
- `render_palette` quantizes the downscale to a small palette, a stylized look
  that trades colour fidelity for flat, readable shapes.

Each one has the same shape, `(source, size) -> frame`, and returns an RGB
`(size, size)` image that passes `validate_frame`. The source may be any size,
aspect ratio and mode: it is center-cropped to a square and converted to RGB
first, with any transparency composited on black, the colour of an unlit LED.

This module is temporary in its present form: once the owner has picked a
treatment, the two that lost are deleted.
"""

from collections.abc import Callable

from PIL import Image, ImageEnhance

# Factors for `ImageEnhance`, where 1.0 leaves the image unchanged. Starting
# values, to be tuned on the real board alongside the bake-off.
CONTRAST_BOOST = 1.3
SATURATION_BOOST = 1.5
# Few enough colours that the result reads as flat areas on a small panel,
# enough that a photographic cover keeps its main hues.
PALETTE_COLOURS = 16

Renderer = Callable[[Image.Image, int], Image.Image]

BLACK: tuple[int, int, int] = (0, 0, 0)


def render_downscale(source: Image.Image, size: int) -> Image.Image:
    """Return `source` as a board frame, averaged down by area."""
    # BOX averages every source pixel that falls in a board pixel, so a fine
    # pattern turns into its mean colour instead of aliasing into noise.
    return _square_rgb(source).resize((size, size), Image.Resampling.BOX)


def render_boosted(source: Image.Image, size: int) -> Image.Image:
    """Return the area downscale with contrast and saturation raised."""
    frame = render_downscale(source, size)
    # Boosted after the downscale, so the averaged colours are what gets
    # stretched, not the source detail the averaging is about to discard.
    frame = ImageEnhance.Contrast(frame).enhance(CONTRAST_BOOST)
    return ImageEnhance.Color(frame).enhance(SATURATION_BOOST)


def render_palette(source: Image.Image, size: int) -> Image.Image:
    """Return the area downscale reduced to `PALETTE_COLOURS` flat colours."""
    frame = render_downscale(source, size)
    # No dithering: on a panel this small a dither pattern is not blended by
    # the eye, it is read as speckle.
    quantized = frame.quantize(
        colors=PALETTE_COLOURS,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    )
    return quantized.convert("RGB")


# The bake-off script iterates this, so its order is the column order of the
# contact sheet.
CANDIDATES: dict[str, Renderer] = {
    "downscale": render_downscale,
    "boosted": render_boosted,
    "palette": render_palette,
}


def _square_rgb(source: Image.Image) -> Image.Image:
    """Return the largest centered square of `source`, as RGB on black."""
    image = _to_rgb(source)
    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    return image.crop((left, top, left + side, top + side))


def _to_rgb(source: Image.Image) -> Image.Image:
    """Convert any mode to RGB, compositing transparency onto black.

    A plain `convert("RGB")` drops alpha and keeps whatever colour the
    transparent pixels happen to store, which is often white or garbage.
    """
    image = source
    # A palette image marks transparency in `info`, not in an alpha band, so
    # it has to be expanded to RGBA before the alpha can be used.
    if image.mode == "P" and "transparency" in image.info:
        image = image.convert("RGBA")
    if image.mode in ("RGBA", "LA", "PA", "RGBa", "La"):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (*BLACK, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")
