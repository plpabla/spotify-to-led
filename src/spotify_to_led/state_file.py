"""The one way runtime state files are rewritten in place.

The token set, the owner's settings and today's AI spend each live in a small
JSON file under `var/` that the service rewrites while it runs. Every write
goes through a temp file in the same directory and `os.replace`, so a crash
mid-write leaves the old file or the new one, never half of either, and the
directory is fsynced afterwards so the rename itself survives a power cut.
"""

import json
import os
import secrets
from pathlib import Path


def write_json_atomically(path: Path, data: object, *, mode: int = 0o666) -> None:
    """Write `data` as JSON to `path` through a temp file and `os.replace`.

    The temp file is created with `mode` (less the umask) from the start, so a
    file holding secrets is never briefly readable by others.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # A crash between creating a temp file and replacing the target leaves
    # one behind, possibly holding live tokens; nothing else would remove it.
    for stale in path.parent.glob(f".{path.name}.*.tmp"):
        stale.unlink(missing_ok=True)
    # Same directory as the target, or `os.replace` would not be atomic.
    temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    text = json.dumps(data)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    _fsync_directory(path.parent)


def _fsync_directory(directory: Path) -> None:
    """Make the rename itself durable.

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
