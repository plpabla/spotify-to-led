"""What the service believes is playing, and how a `currently-playing` answer becomes it.

The snapshot keeps *what is playing* apart from *whether reading works*: on a
failed read the last known item stays, and the failure cause and its start
time are recorded next to it. That is the shape the display needs to keep a
cover up while marking it stale, and the shape the status page needs to name
the cause.

Parsing is defensive on purpose. Spotify documents `item` and most of its
fields as nullable, leaves the ad and private-session bodies undocumented, and
guarantees no image sizes, so a missing or odd field yields an absent value
rather than an exception.

An episode's artwork is its own images, and its show's when it has none of
its own: many episodes carry only the show's art, and the generic no-artwork
symbol is for items with no artwork at all (FR-010).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

ITEM_KINDS = frozenset({"track", "episode", "ad", "unknown"})


class PlaybackStatus(StrEnum):
    """What the account is doing, as of the last successful read."""

    # No successful read since the process started.
    UNKNOWN = "unknown"
    PLAYING = "playing"
    # Owner decision: a paused item stays the believed item, with its cover.
    PAUSED = "paused"
    NOTHING_PLAYING = "nothing_playing"


class ReadFailure(StrEnum):
    """Why the most recent read did not produce a fresh answer."""

    AUTHORIZATION_NEEDED = "authorization_needed"
    NETWORK = "network"
    API_ERROR = "api_error"
    RATE_LIMITED = "rate_limited"


@dataclass(frozen=True)
class Artwork:
    """One image Spotify offers for an item; either dimension may be unknown."""

    url: str
    width: int | None
    height: int | None


@dataclass(frozen=True)
class PlayingItem:
    """The item a read reported, reduced to what the display and status need.

    `artwork` is in Spotify's order, widest first. An ad or an item Spotify
    withholds has a kind but no id, name or artwork.
    """

    kind: str
    id: str | None
    name: str | None
    subtitle: str | None
    artwork: tuple[Artwork, ...]


@dataclass(frozen=True)
class PlaybackSnapshot:
    """One immutable view of the believed state.

    `failure_since` is when the current run of failed reads began, so a
    consumer can tell a momentary blip from a cover that has gone stale.
    """

    status: PlaybackStatus
    item: PlayingItem | None
    failure: ReadFailure | None
    failure_since: datetime | None
    last_read_at: datetime | None
    authorized_at: datetime | None


INITIAL_SNAPSHOT = PlaybackSnapshot(
    status=PlaybackStatus.UNKNOWN,
    item=None,
    failure=None,
    failure_since=None,
    last_read_at=None,
    authorized_at=None,
)


def parse_currently_playing(
    status_code: int, body: object
) -> tuple[PlaybackStatus, PlayingItem | None]:
    """Turn a successful `currently-playing` answer into a status and an item.

    `body` is the decoded JSON, or `None` when the response had no body.
    """
    if status_code == 204 or not isinstance(body, dict) or not body:
        return PlaybackStatus.NOTHING_PLAYING, None

    status = PlaybackStatus.PLAYING if body.get("is_playing") is True else PlaybackStatus.PAUSED
    kind = body.get("currently_playing_type")
    if kind not in ITEM_KINDS:
        kind = "unknown"

    item = body.get("item")
    if not isinstance(item, dict):
        # Ads, and whatever Spotify chooses to withhold, arrive as `item: null`:
        # the kind is still worth knowing, the rest is not there to read.
        return status, PlayingItem(kind=kind, id=None, name=None, subtitle=None, artwork=())

    if kind == "track":
        subtitle = _joined_artists(item.get("artists"))
        album = item.get("album")
        images = album.get("images") if isinstance(album, dict) else None
    elif kind == "episode":
        show = item.get("show")
        subtitle = _string(show.get("name")) if isinstance(show, dict) else None
        images = item.get("images")
        if not _artwork(images) and isinstance(show, dict):
            # Only as a fallback: an episode's own art is the more specific.
            images = show.get("images")
    else:
        subtitle = None
        images = item.get("images")

    return status, PlayingItem(
        kind=kind,
        id=_string(item.get("id")),
        name=_string(item.get("name")),
        subtitle=subtitle,
        artwork=_artwork(images),
    )


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _integer(value: object) -> int | None:
    # `bool` is an `int` subclass, and `true` is not a pixel count.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _joined_artists(artists: object) -> str | None:
    if not isinstance(artists, list):
        return None
    names = [
        artist["name"]
        for artist in artists
        if isinstance(artist, dict) and isinstance(artist.get("name"), str)
    ]
    return ", ".join(names) or None


def _artwork(images: object) -> tuple[Artwork, ...]:
    if not isinstance(images, list):
        return ()
    return tuple(
        Artwork(
            url=image["url"],
            width=_integer(image.get("width")),
            height=_integer(image.get("height")),
        )
        for image in images
        if isinstance(image, dict) and isinstance(image.get("url"), str)
    )
