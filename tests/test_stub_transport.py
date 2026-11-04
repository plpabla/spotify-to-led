"""The PNG stub transport, which is the FR-016 development loop."""

from pathlib import Path

import pytest
from PIL import Image

from spotify_to_led import frames
from spotify_to_led.board.stub import PngBoardTransport
from spotify_to_led.board.transport import LinkState

SIZE = 32


async def test_push_writes_a_readable_32x32_rgb_png_at_latest_png(tmp_path: Path) -> None:
    transport = PngBoardTransport(tmp_path / "frames", SIZE)
    await transport.connect()

    await transport.push_frame(frames.test_pattern(SIZE))

    written = tmp_path / "frames" / "latest.png"
    assert written.is_file()
    with Image.open(written) as image:
        image.load()
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert image.size == (SIZE, SIZE)
        assert image.getpixel((8, 8)) == (255, 0, 0)
        assert image.getpixel((24, 24)) == (255, 255, 255)


async def test_connect_creates_the_output_directory(tmp_path: Path) -> None:
    output_dir = tmp_path / "nested" / "frames"
    transport = PngBoardTransport(output_dir, SIZE)

    await transport.connect()

    assert output_dir.is_dir()


async def test_push_leaves_only_the_target_file_behind(tmp_path: Path) -> None:
    # The atomic write must replace the target, not accumulate files or leave
    # its temporary file in the directory a viewer is watching.
    transport = PngBoardTransport(tmp_path, SIZE)
    await transport.connect()

    await transport.push_frame(frames.test_pattern(SIZE))
    await transport.push_frame(frames.test_pattern(SIZE))

    assert [path.name for path in tmp_path.iterdir()] == ["latest.png"]


async def test_repeated_pushes_overwrite_the_same_file(tmp_path: Path) -> None:
    transport = PngBoardTransport(tmp_path, SIZE)
    await transport.connect()
    await transport.push_frame(frames.test_pattern(SIZE))

    await transport.push_frame(Image.new("RGB", (SIZE, SIZE), (7, 8, 9)))

    with Image.open(tmp_path / "latest.png") as image:
        assert image.getpixel((0, 0)) == (7, 8, 9)


async def test_push_rejects_a_wrong_size_frame(tmp_path: Path) -> None:
    transport = PngBoardTransport(tmp_path, SIZE)
    await transport.connect()

    with pytest.raises(ValueError):
        await transport.push_frame(Image.new("RGB", (16, 32)))

    assert not (tmp_path / "latest.png").exists()


async def test_push_rejects_a_wrong_mode_frame(tmp_path: Path) -> None:
    transport = PngBoardTransport(tmp_path, SIZE)
    await transport.connect()

    with pytest.raises(ValueError):
        await transport.push_frame(Image.new("RGBA", (SIZE, SIZE)))

    assert not (tmp_path / "latest.png").exists()


async def test_link_state_follows_connect_and_close(tmp_path: Path) -> None:
    transport = PngBoardTransport(tmp_path, SIZE)
    assert transport.link_state is LinkState.DOWN

    await transport.connect()
    assert transport.link_state is LinkState.UP

    await transport.close()
    assert transport.link_state is LinkState.DOWN


async def test_close_after_a_failed_connect_does_not_raise(tmp_path: Path) -> None:
    # A plain file where the output directory should be makes mkdir fail, which
    # is the shape the lifespan has to survive: connect raises, then it closes.
    blocked = tmp_path / "frames"
    blocked.write_bytes(b"")
    transport = PngBoardTransport(blocked, SIZE)
    with pytest.raises(OSError):
        await transport.connect()

    await transport.close()

    assert transport.link_state is LinkState.DOWN
