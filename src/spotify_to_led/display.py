"""What the board shows, decided in one place.

This is the start of FR-009's display rule: the believed playback state goes
in, a presentation comes out, and the presentation names a frame. Keeping the
choice pure means the whole table is tested without a board or a reader.

The choices in this slice are interim and recorded here so they are replaced
on purpose rather than by accident:

- `PLAYING` and `PAUSED` show the playing placeholder. A paused item keeps its
  cover (an owner decision, see `PlaybackStatus`), so it keeps the stand-in
  for one too. Cover rendering (S-04) replaces the placeholder.
- `UNKNOWN` shows idle. Before the first successful read there is nothing to
  vouch for, and idle claims nothing about what is playing.
- No reader at all (`None`, when Spotify is not configured) shows idle.
- `failure` is ignored. Marking a stale cover (FR-018) arrives with S-06;
  until then the placeholder is not a cover, so there is nothing to mark.
"""

from enum import StrEnum

from PIL import Image

from spotify_to_led import frames
from spotify_to_led.spotify.playback import PlaybackSnapshot, PlaybackStatus


class Presentation(StrEnum):
    """Which kind of frame the board should show."""

    IDLE = "idle"
    PLAYING = "playing"


def select_presentation(snapshot: PlaybackSnapshot | None) -> Presentation:
    """Choose the presentation for `snapshot`; `None` means there is no reader."""
    if snapshot is None:
        return Presentation.IDLE
    if snapshot.status in (PlaybackStatus.PLAYING, PlaybackStatus.PAUSED):
        return Presentation.PLAYING
    return Presentation.IDLE


def frame_for(presentation: Presentation, size: int) -> Image.Image:
    """Return the RGB `(size, size)` frame that draws `presentation`."""
    if presentation is Presentation.PLAYING:
        return frames.playing_placeholder(size)
    return frames.idle(size)
