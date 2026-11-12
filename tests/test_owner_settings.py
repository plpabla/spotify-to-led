"""The one file the owner's runtime choices persist in.

What matters is that the choice survives a restart, that the file is never
left half-written, and that a missing or broken file reads as the defaults,
enhancement off, rather than taking the service down.
"""

import json
import logging
import os
from pathlib import Path

import pytest

from spotify_to_led.owner_settings import OwnerSettings, OwnerSettingsStore


def test_the_defaults_keep_enhancement_off() -> None:
    assert OwnerSettings().ai_enhanced is False


@pytest.mark.parametrize("ai_enhanced", [True, False])
def test_saved_settings_load_back_equal(tmp_path: Path, ai_enhanced: bool) -> None:
    # A parent that does not exist yet, as `var/` may not on a fresh checkout.
    store = OwnerSettingsStore(tmp_path / "var" / "settings.json")

    store.save(OwnerSettings(ai_enhanced=ai_enhanced))

    assert store.load() == OwnerSettings(ai_enhanced=ai_enhanced)


def test_the_file_holds_the_documented_json_shape(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"

    OwnerSettingsStore(path).save(OwnerSettings(ai_enhanced=True))

    assert json.loads(path.read_text(encoding="utf-8")) == {"ai_enhanced": True}


def test_a_missing_file_loads_as_the_defaults(tmp_path: Path) -> None:
    assert OwnerSettingsStore(tmp_path / "settings.json").load() == OwnerSettings()


@pytest.mark.parametrize(
    "contents",
    ["{not json", "[]", "{}", '{"ai_enhanced": "true"}', '{"ai_enhanced": 1}'],
)
def test_a_malformed_file_loads_as_the_defaults_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, contents: str
) -> None:
    path = tmp_path / "settings.json"
    path.write_text(contents, encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert OwnerSettingsStore(path).load() == OwnerSettings()

    assert caplog.records, "a broken settings file must be reported"


def test_a_save_removes_temp_files_a_crash_left_behind(tmp_path: Path) -> None:
    stale = tmp_path / ".settings.json.deadbeef.tmp"
    stale.write_text("{}", encoding="utf-8")

    OwnerSettingsStore(tmp_path / "settings.json").save(OwnerSettings(ai_enhanced=True))

    assert [entry.name for entry in tmp_path.iterdir()] == ["settings.json"]


def test_a_failed_write_leaves_the_previous_file_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failing fsync stands in for a crash between writing and replacing.
    store = OwnerSettingsStore(tmp_path / "settings.json")
    store.save(OwnerSettings(ai_enhanced=False))

    def failing_fsync(fd: int) -> None:
        raise OSError("disk went away")

    monkeypatch.setattr(os, "fsync", failing_fsync)

    with pytest.raises(OSError, match="disk went away"):
        store.save(OwnerSettings(ai_enhanced=True))

    assert store.load() == OwnerSettings(ai_enhanced=False)
    assert [entry.name for entry in tmp_path.iterdir()] == ["settings.json"]
