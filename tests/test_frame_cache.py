"""The on-disk store of rendered covers.

What matters is that only a plain scdn image URL ever names a file, that an
entry comes back pixel for pixel, and that nothing the disk can do, a broken
entry or a directory that cannot be written, is more than a miss.
"""

import logging
import struct
import zlib
from pathlib import Path

import pytest
from PIL import Image

from spotify_to_led.board.transport import frame_to_pixel_colors
from spotify_to_led.frame_cache import FrameCache, image_id

SIZE = 32
AN_ID = "ab67616d00004851aaaa"
A_URL = f"https://i.scdn.co/image/{AN_ID}"


def a_frame(size: int = SIZE) -> Image.Image:
    # Not a flat colour, so a mangled round trip would show.
    frame = Image.new("RGB", (size, size), (200, 40, 90))
    for x in range(size):
        frame.putpixel((x, x), (x * 7 % 256, 255 - x, 30))
    return frame


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (A_URL, AN_ID),
        ("https://i.scdn.co/image/ab67616d0000b273AbC123", "ab67616d0000b273AbC123"),
        ("https://example.com/image/ab67616d00004851aaaa", None),
        ("http://i.scdn.co/image/ab67616d00004851aaaa", None),
        ("https://i.scdn.co/image/ab67616d/00004851aaaa", None),
        ("https://i.scdn.co/image/ab67616d00004851aaaa?size=64", None),
        ("https://i.scdn.co/image/ab67616d00004851aaaa/", None),
        ("https://i.scdn.co/image/cover-a", None),
        ("https://i.scdn.co/image/..", None),
        ("https://i.scdn.co/image/", None),
    ],
)
def test_image_id_is_taken_only_from_a_plain_scdn_image_url(
    url: str, expected: str | None
) -> None:
    assert image_id(url) == expected


def test_a_stored_frame_loads_back_with_identical_pixels(tmp_path: Path) -> None:
    # `root` does not exist yet, as `var/covers` may not on a fresh checkout.
    cache = FrameCache(tmp_path / "covers", SIZE)

    cache.store(A_URL, a_frame())
    loaded = cache.load(A_URL)

    assert loaded is not None
    assert loaded.mode == "RGB"
    assert frame_to_pixel_colors(loaded) == frame_to_pixel_colors(a_frame())
    assert [p.name for p in (tmp_path / "covers" / "32").iterdir()] == [f"{AN_ID}.png"]


def test_a_store_sweeps_temp_files_a_power_cut_left_behind(tmp_path: Path) -> None:
    directory = tmp_path / "32"
    directory.mkdir()
    (directory / f".{AN_ID}.png.dead.tmp").write_bytes(b"half a png")
    other = directory / ".ab67616d00004851bbbb.png.dead.tmp"
    other.write_bytes(b"another entry's")

    FrameCache(tmp_path, SIZE).store(A_URL, a_frame())

    # Only this entry's leftovers, as the token store sweeps only its own.
    assert sorted(p.name for p in directory.iterdir()) == sorted([f"{AN_ID}.png", other.name])


def test_a_missing_entry_is_a_quiet_miss(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        assert FrameCache(tmp_path, SIZE).load(A_URL) is None

    assert caplog.records == []


def test_a_corrupt_entry_is_a_miss_and_the_next_store_overwrites_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cache = FrameCache(tmp_path, SIZE)
    entry = tmp_path / "32" / f"{AN_ID}.png"
    entry.parent.mkdir()
    entry.write_bytes(b"\x89PNG not really")

    with caplog.at_level(logging.WARNING, logger="spotify_to_led.frame_cache"):
        assert cache.load(A_URL) is None

    [warning] = caplog.records
    assert warning.levelno == logging.WARNING
    assert str(entry) in warning.getMessage()

    cache.store(A_URL, a_frame())
    loaded = cache.load(A_URL)
    assert loaded is not None
    assert frame_to_pixel_colors(loaded) == frame_to_pixel_colors(a_frame())


def a_bomb_header() -> bytes:
    """A PNG that is only a header claiming 20000x20000 pixels, over Pillow's bomb limit."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", 20000, 20000, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IEND", b"")


def test_an_entry_whose_header_claims_a_huge_image_is_a_miss(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Pillow raises DecompressionBombError for it, which is not an OSError.
    entry = tmp_path / "32" / f"{AN_ID}.png"
    entry.parent.mkdir()
    entry.write_bytes(a_bomb_header())

    with caplog.at_level(logging.WARNING, logger="spotify_to_led.frame_cache"):
        assert FrameCache(tmp_path, SIZE).load(A_URL) is None

    assert [r.levelno for r in caplog.records] == [logging.WARNING]


def test_an_entry_of_the_wrong_size_is_a_miss(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    entry = tmp_path / "32" / f"{AN_ID}.png"
    entry.parent.mkdir()
    a_frame(16).save(entry, format="PNG")

    with caplog.at_level(logging.WARNING, logger="spotify_to_led.frame_cache"):
        assert FrameCache(tmp_path, SIZE).load(A_URL) is None

    assert [r.levelno for r in caplog.records] == [logging.WARNING]


def test_a_url_without_an_id_is_never_cached(tmp_path: Path) -> None:
    cache = FrameCache(tmp_path, SIZE)
    url = "https://i.scdn.co/image/cover-a"

    cache.store(url, a_frame())

    assert cache.load(url) is None
    assert list(tmp_path.iterdir()) == []


def test_a_cache_for_one_board_size_does_not_see_another_sizes_entries(
    tmp_path: Path,
) -> None:
    FrameCache(tmp_path, 32).store(A_URL, a_frame(32))

    assert FrameCache(tmp_path, 64).load(A_URL) is None
    assert (tmp_path / "32" / f"{AN_ID}.png").is_file()


def test_a_store_into_an_unusable_root_is_logged_and_survived(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "covers"
    root.write_text("a file where the directory should be", encoding="utf-8")
    cache = FrameCache(root, SIZE)

    with caplog.at_level(logging.ERROR, logger="spotify_to_led.frame_cache"):
        cache.store(A_URL, a_frame())

    [error] = caplog.records
    assert error.levelno == logging.ERROR
    assert str(root) in error.getMessage()
    assert cache.load(A_URL) is None
