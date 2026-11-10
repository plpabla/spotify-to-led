"""How a `currently-playing` answer becomes a status and an item.

The bodies are trimmed to the fields the parser reads, in the shapes Spotify
documents. Ads and withheld items arrive with `item: null`, and image lists
carry no size guarantee, so the absent cases matter as much as the full ones.
"""

from spotify_to_led.spotify.playback import (
    Artwork,
    PlaybackStatus,
    PlayingItem,
    parse_currently_playing,
)

ALBUM_IMAGES = [
    {"url": "https://i.scdn.co/image/album-640", "width": 640, "height": 640},
    {"url": "https://i.scdn.co/image/album-300", "width": 300, "height": 300},
    {"url": "https://i.scdn.co/image/album-64", "width": 64, "height": 64},
]
EPISODE_IMAGES = [
    {"url": "https://i.scdn.co/image/episode-640", "width": 640, "height": 640},
    {"url": "https://i.scdn.co/image/episode-64", "width": 64, "height": 64},
]


def a_track_body(*, is_playing: bool = True) -> dict[str, object]:
    return {
        "is_playing": is_playing,
        "currently_playing_type": "track",
        "item": {
            "type": "track",
            "id": "4uLU6hMCjMI75M1A2tKUQC",
            "name": "Never Gonna Give You Up",
            "artists": [{"name": "Rick Astley"}, {"name": "A Guest"}],
            "album": {"name": "Whenever You Need Somebody", "images": ALBUM_IMAGES},
        },
    }


def an_episode_body() -> dict[str, object]:
    return {
        "is_playing": True,
        "currently_playing_type": "episode",
        "item": {
            "type": "episode",
            "id": "512ojhOuo1ktJprKbVcKyQ",
            "name": "An Episode",
            "images": EPISODE_IMAGES,
            "show": {"name": "A Show", "images": [{"url": "https://i.scdn.co/image/show"}]},
        },
    }


def test_204_means_nothing_is_playing() -> None:
    assert parse_currently_playing(204, None) == (PlaybackStatus.NOTHING_PLAYING, None)


def test_an_empty_body_means_nothing_is_playing() -> None:
    assert parse_currently_playing(200, None) == (PlaybackStatus.NOTHING_PLAYING, None)
    assert parse_currently_playing(200, {}) == (PlaybackStatus.NOTHING_PLAYING, None)


def test_a_playing_track_takes_its_artwork_from_the_album_widest_first() -> None:
    status, item = parse_currently_playing(200, a_track_body())

    assert status is PlaybackStatus.PLAYING
    assert item == PlayingItem(
        kind="track",
        id="4uLU6hMCjMI75M1A2tKUQC",
        name="Never Gonna Give You Up",
        subtitle="Rick Astley, A Guest",
        artwork=(
            Artwork("https://i.scdn.co/image/album-640", 640, 640),
            Artwork("https://i.scdn.co/image/album-300", 300, 300),
            Artwork("https://i.scdn.co/image/album-64", 64, 64),
        ),
    )


def test_a_paused_track_stays_the_believed_item() -> None:
    # Owner decision: a pause keeps the cover, so the item is not dropped.
    status, item = parse_currently_playing(200, a_track_body(is_playing=False))

    assert status is PlaybackStatus.PAUSED
    assert item == parse_currently_playing(200, a_track_body())[1]


def test_a_playing_episode_takes_its_artwork_from_the_item_not_the_show() -> None:
    status, item = parse_currently_playing(200, an_episode_body())

    assert status is PlaybackStatus.PLAYING
    assert item == PlayingItem(
        kind="episode",
        id="512ojhOuo1ktJprKbVcKyQ",
        name="An Episode",
        subtitle="A Show",
        artwork=(
            Artwork("https://i.scdn.co/image/episode-640", 640, 640),
            Artwork("https://i.scdn.co/image/episode-64", 64, 64),
        ),
    )


def test_an_episode_without_images_of_its_own_takes_its_shows() -> None:
    # Many episodes carry only the show's art; that is artwork, not none.
    body = an_episode_body()
    body["item"]["images"] = []  # type: ignore[index]

    _, item = parse_currently_playing(200, body)

    assert item is not None
    assert item.artwork == (Artwork("https://i.scdn.co/image/show", None, None),)


def test_an_episode_with_no_images_and_no_show_images_has_no_artwork() -> None:
    body = an_episode_body()
    body["item"]["images"] = []  # type: ignore[index]
    body["item"]["show"]["images"] = []  # type: ignore[index]

    _, item = parse_currently_playing(200, body)

    assert item is not None and item.artwork == ()
    assert item.subtitle == "A Show"


def test_an_ad_with_no_item_is_an_ad_with_nothing_to_show() -> None:
    body = {"is_playing": True, "currently_playing_type": "ad", "item": None}

    assert parse_currently_playing(200, body) == (
        PlaybackStatus.PLAYING,
        PlayingItem(kind="ad", id=None, name=None, subtitle=None, artwork=()),
    )


def test_an_unknown_type_with_no_item_keeps_its_status_from_is_playing() -> None:
    body = {"is_playing": False, "currently_playing_type": "unknown", "item": None}

    assert parse_currently_playing(200, body) == (
        PlaybackStatus.PAUSED,
        PlayingItem(kind="unknown", id=None, name=None, subtitle=None, artwork=()),
    )


def test_a_type_spotify_has_not_documented_reads_as_unknown() -> None:
    body = {"is_playing": True, "currently_playing_type": "audiobook", "item": None}

    _, item = parse_currently_playing(200, body)

    assert item is not None
    assert item.kind == "unknown"


def test_missing_images_mean_no_artwork_rather_than_an_error() -> None:
    track = a_track_body()
    del track["item"]["album"]["images"]  # type: ignore[index]
    episode = an_episode_body()
    del episode["item"]["images"]  # type: ignore[index]
    # Otherwise the episode falls back to its show's images.
    del episode["item"]["show"]["images"]  # type: ignore[index]

    _, track_item = parse_currently_playing(200, track)
    _, episode_item = parse_currently_playing(200, episode)

    assert track_item is not None and track_item.artwork == ()
    assert episode_item is not None and episode_item.artwork == ()
    # The rest of the item is still worth knowing.
    assert track_item.name == "Never Gonna Give You Up"


def test_an_image_without_sizes_keeps_its_url() -> None:
    # Spotify guarantees no sizes; a URL alone is still a usable cover.
    body = a_track_body()
    body["item"]["album"]["images"] = [{"url": "https://i.scdn.co/image/x", "width": None}]  # type: ignore[index]

    _, item = parse_currently_playing(200, body)

    assert item is not None
    assert item.artwork == (Artwork("https://i.scdn.co/image/x", None, None),)
