"""Where the Spotify token set persists between restarts.

The token set is mutable runtime state, rewritten on every rotation, so it
lives in its own file rather than in the env file: that one is hand-edited
configuration in a directory the service cannot write to. Every save goes
through a temp file and `os.replace`, so a crash mid-write leaves either the
old credential or the new one, never half of either.
"""

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from spotify_to_led.spotify.auth import TokenSet
from spotify_to_led.state_file import write_json_atomically

logger = logging.getLogger(__name__)

_DATETIME_FIELDS = ("expires_at", "authorized_at")
_STRING_FIELDS = ("access_token", "refresh_token", "scope")


class TokenStore:
    """Load, save and discard one token set at `path`."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> TokenSet | None:
        """The saved token set, or `None` when there is none worth using.

        A file that cannot be read or parsed is treated as absent, which leads
        to a re-authorization rather than a crash. The warning names the path
        and the error type only, never the contents, which hold the tokens.
        """
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return _from_json(raw)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, KeyError) as error:
            logger.warning(
                "ignoring unreadable Spotify token file %s (%s)",
                self.path,
                type(error).__name__,
            )
            return None

    def save(self, token_set: TokenSet) -> None:
        """Write `token_set` atomically, readable by the owner only."""
        # 0600 from the temp file's creation: the tokens are never briefly
        # readable by others.
        write_json_atomically(self.path, _to_json(token_set), mode=0o600)

    def discard(self) -> None:
        """Delete the saved token set; a missing file is already discarded."""
        self.path.unlink(missing_ok=True)


def _to_json(token_set: TokenSet) -> dict[str, str]:
    values = asdict(token_set)
    for name in _DATETIME_FIELDS:
        values[name] = values[name].isoformat()
    return values


def _from_json(raw: object) -> TokenSet:
    if not isinstance(raw, dict):
        raise TypeError("token file is not a JSON object")
    for name in _STRING_FIELDS:
        if not isinstance(raw[name], str):
            raise TypeError(f"{name} is not a string")
    datetimes: dict[str, datetime] = {}
    for name in _DATETIME_FIELDS:
        value = datetime.fromisoformat(raw[name])
        # A naive time cannot be compared with the UTC clock the reader uses.
        if value.tzinfo is None:
            raise ValueError(f"{name} has no timezone")
        datetimes[name] = value
    return TokenSet(
        access_token=raw["access_token"],
        refresh_token=raw["refresh_token"],
        scope=raw["scope"],
        **datetimes,
    )
