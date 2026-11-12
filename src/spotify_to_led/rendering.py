"""The one step that turns a cover image into a board frame.

A 640 px album cover shrunk to a 16, 32 or 64 px LED panel loses most of what
made it recognisable, and how much survives depends on the treatment. The PRD
left that choice (OQ3) to be made by looking at real covers on the real board,
and the bake-off settled it (FR-007): the plain area downscale, faithful to
the source, read better there than a boosted or a palette-reduced version.

`render_cover` is the seam the rest of the service calls for a cover as
Spotify serves it. `render_enhanced` is its counterpart for an AI-enhanced
cover (FR-019): the image model's answer is already pixel art drawn for the
panel, about 1024 px square, and the board bake-off found a LANCZOS downscale
of it slightly crisper on the board than the area average. Both return an RGB
`(size, size)` image that passes `validate_frame`. The source may be any size,
aspect ratio and mode: it is center-cropped to a square and converted to RGB
first, with any transparency composited on black, the colour of an unlit LED.
"""

from PIL import Image

BLACK: tuple[int, int, int] = (0, 0, 0)


def render_cover(source: Image.Image, size: int) -> Image.Image:
    """Return `source` as a board frame, averaged down by area."""
    # BOX averages every source pixel that falls in a board pixel, so a fine
    # pattern turns into its mean colour instead of aliasing into noise.
    return _square_rgb(source).resize((size, size), Image.Resampling.BOX)


def render_enhanced(source: Image.Image, size: int) -> Image.Image:
    """Return an image model's redrawn cover as a board frame, shrunk with LANCZOS."""
    # The answer is flat colour blocks on a coarse grid, not a photograph, so
    # there is no fine pattern to alias; LANCZOS keeps the blocks' edges
    # sharper than BOX's average does, which is what the board bake-off saw.
    return _square_rgb(source).resize((size, size), Image.Resampling.LANCZOS)


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
