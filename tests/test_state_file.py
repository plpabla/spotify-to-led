"""The atomic JSON write every runtime state file under `var/` goes through."""

import json
import os
import stat
from pathlib import Path

import pytest

from spotify_to_led.state_file import write_json_atomically


def test_an_atomic_write_holds_the_json_and_leaves_no_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "var" / "state.json"

    write_json_atomically(path, {"a": 1, "b": [True, None]})

    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1, "b": [True, None]}
    assert [entry.name for entry in path.parent.iterdir()] == ["state.json"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_an_atomic_write_creates_the_file_with_the_given_mode(tmp_path: Path) -> None:
    path = tmp_path / "secret.json"

    write_json_atomically(path, {"token": "x"}, mode=0o600)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
