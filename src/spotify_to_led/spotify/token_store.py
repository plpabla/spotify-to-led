"""Where the Spotify token set persists between restarts.

The token set is mutable runtime state, rewritten on every rotation, so it
lives in its own file rather than in the env file: that one is hand-edited
configuration in a directory the service cannot write to. Every save goes
through a temp file and `os.replace`, so a crash mid-write leaves either the
old credential or the new one, never half of either.
"""

import json
import logging
import os
import secrets
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from spotify_to_led.spotify.auth import TokenSet

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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A crash between creating a temp file and replacing the target leaves
        # one behind, holding live tokens; nothing else would ever remove it.
        for stale in self.path.parent.glob(f".{self.path.name}.*.tmp"):
            stale.unlink(missing_ok=True)
        # Same directory as the target, or `os.replace` would not be atomic.
        temp = self.path.with_name(f".{self.path.name}.{secrets.token_hex(4)}.tmp")
        data = json.dumps(_to_json(token_set))
        # Created 0600 from the start, so the tokens are never briefly readable
        # by others between creation and a later chmod.
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, self.path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        _fsync_directory(self.path.parent)

    def discard(self) -> None:
        """Delete the saved token set; a missing file is already discarded."""
        self.path.unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    """Make the rename itself durable, so a power cut cannot undo a rotation.

    POSIX only: Windows cannot open a directory as a file, and the service
    only ever runs on the Pi.
    """
    if os.name != "posix":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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
