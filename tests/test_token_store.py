"""The one file the Spotify credential persists in.

What matters is that the file can never be left half-written and never holds
a credential readable by anyone but the owner, and that a broken file reads as
"no credential" rather than taking the reader down.
"""

import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from spotify_to_led.spotify.auth import SCOPE, TokenSet
from spotify_to_led.spotify.token_store import TokenStore

AN_ACCESS_TOKEN = "BQ-the-access-token"
A_REFRESH_TOKEN = "AQ-the-refresh-token"


def a_token_set(access_token: str = AN_ACCESS_TOKEN) -> TokenSet:
    return TokenSet(
        access_token=access_token,
        refresh_token=A_REFRESH_TOKEN,
        expires_at=datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
        authorized_at=datetime(2026, 5, 1, 9, 30, tzinfo=UTC),
        scope=SCOPE,
    )


def test_a_saved_token_set_loads_back_equal(tmp_path: Path) -> None:
    # A parent that does not exist yet, as `var/` may not on a fresh checkout.
    store = TokenStore(tmp_path / "var" / "spotify-token.json")

    store.save(a_token_set())

    assert store.load() == a_token_set()


def test_the_file_mirrors_the_token_set_with_iso_utc_times(tmp_path: Path) -> None:
    path = tmp_path / "spotify-token.json"

    TokenStore(path).save(a_token_set())

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "access_token": AN_ACCESS_TOKEN,
        "refresh_token": A_REFRESH_TOKEN,
        "expires_at": "2026-11-08T12:00:00+00:00",
        "authorized_at": "2026-05-01T09:30:00+00:00",
        "scope": SCOPE,
    }


def test_a_missing_file_loads_as_none(tmp_path: Path) -> None:
    assert TokenStore(tmp_path / "spotify-token.json").load() is None


@pytest.mark.parametrize(
    "contents",
    [
        "{not json",
        "[]",
        '{"access_token": "BQ-the-access-token"}',
        json.dumps(
            {
                "access_token": AN_ACCESS_TOKEN,
                "refresh_token": A_REFRESH_TOKEN,
                "expires_at": "yesterday",
                "authorized_at": "2026-05-01T09:30:00+00:00",
                "scope": SCOPE,
            }
        ),
    ],
)
def test_a_malformed_file_loads_as_none_and_warns_without_its_contents(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, contents: str
) -> None:
    # Treated as absent, which leads to re-authorization instead of a crash.
    path = tmp_path / "spotify-token.json"
    path.write_text(contents, encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert TokenStore(path).load() is None

    assert caplog.records, "a broken token file must be reported"
    assert AN_ACCESS_TOKEN not in caplog.text


def test_discard_removes_the_file(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "spotify-token.json")
    store.save(a_token_set())

    store.discard()

    assert not store.path.exists()
    assert store.load() is None


def test_discarding_a_missing_file_is_a_no_op(tmp_path: Path) -> None:
    TokenStore(tmp_path / "spotify-token.json").discard()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_the_saved_file_is_readable_by_the_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "spotify-token.json"

    TokenStore(path).save(a_token_set())

    assert path.stat().st_mode & 0o777 == 0o600


def test_a_failed_write_leaves_the_previous_file_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The crash this guards against happens between writing and replacing; a
    # failing fsync stands in for it.
    store = TokenStore(tmp_path / "spotify-token.json")
    store.save(a_token_set())

    def failing_fsync(fd: int) -> None:
        raise OSError("disk went away")

    monkeypatch.setattr(os, "fsync", failing_fsync)

    with pytest.raises(OSError, match="disk went away"):
        store.save(a_token_set(access_token="BQ-a-newer-access-token"))

    assert store.load() == a_token_set()
    # No stray temp file left behind next to the credential.
    assert [entry.name for entry in tmp_path.iterdir()] == ["spotify-token.json"]
