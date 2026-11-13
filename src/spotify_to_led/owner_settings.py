"""Where the owner's choices from the status page persist between restarts.

They change at runtime, from `PUT /settings`, so like the Spotify token set
they live in their own file rather than in the env file, which is hand-edited
configuration the service cannot write to. Every save goes through a temp file
and `os.replace`, so a crash mid-write leaves the old choice or the new one.

A missing or broken file means the defaults, which keep enhancement off. A
checkbox that forgets is a nuisance; a service that will not start over it is
an outage.
"""

import json
import logging
import math
import os
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# The most AI-enhanced covers may cost in one UTC day unless the owner says
# otherwise: about a hundred generations at the default model's price.
DEFAULT_DAILY_LIMIT_USD = 1.0


@dataclass(frozen=True)
class OwnerSettings:
    """The owner's runtime choices. Off by default: enhancement spends money."""

    ai_enhanced: bool = False
    # The spend in USD at which no new generation starts until 00:00 UTC.
    daily_limit_usd: float = DEFAULT_DAILY_LIMIT_USD


class OwnerSettingsStore:
    """Load and save one `OwnerSettings` at `path`."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> OwnerSettings:
        """The saved settings, or the defaults when there are none worth using.

        A missing file is the normal state until the owner first changes
        something, so only a file that cannot be read or parsed is warned about.
        """
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return _from_json(raw)
        except FileNotFoundError:
            return OwnerSettings()
        except (OSError, ValueError, TypeError, KeyError) as error:
            logger.warning(
                "ignoring unreadable owner settings file %s (%s); using the defaults",
                self.path,
                type(error).__name__,
            )
            return OwnerSettings()

    def save(self, settings: OwnerSettings) -> None:
        """Write `settings` atomically."""
        write_json_atomically(self.path, asdict(settings))


def write_json_atomically(path: Path, data: object) -> None:
    """Write `data` as JSON to `path` through a temp file and `os.replace`.

    A crash mid-write leaves the old file or the new one, never half of
    either. Shared by every runtime state file that is rewritten in place.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # A crash between creating a temp file and replacing the target leaves
    # one behind, and nothing else would ever remove it.
    for stale in path.parent.glob(f".{path.name}.*.tmp"):
        stale.unlink(missing_ok=True)
    # Same directory as the target, or `os.replace` would not be atomic.
    temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    text = json.dumps(data)
    try:
        with temp.open("x", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    _fsync_directory(path.parent)


def _fsync_directory(directory: Path) -> None:
    """Make the rename itself durable, as `TokenStore` does.

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


def _from_json(raw: object) -> OwnerSettings:
    if not isinstance(raw, dict):
        raise TypeError("owner settings file is not a JSON object")
    # Strictly a bool: a hand-edited "false" string is truthy, and reading it
    # as on would spend money the owner did not choose to.
    if not isinstance(raw["ai_enhanced"], bool):
        raise TypeError("ai_enhanced is not a boolean")
    # Optional: files saved before the limit existed hold only the switch, and
    # reading them as broken would switch enhancement off at the deploy.
    limit = raw.get("daily_limit_usd", DEFAULT_DAILY_LIMIT_USD)
    if isinstance(limit, bool) or not isinstance(limit, int | float):
        raise TypeError("daily_limit_usd is not a number")
    if not math.isfinite(limit) or limit <= 0:
        raise ValueError("daily_limit_usd is not a positive amount")
    return OwnerSettings(ai_enhanced=raw["ai_enhanced"], daily_limit_usd=float(limit))
