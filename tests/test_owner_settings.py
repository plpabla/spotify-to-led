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

from spotify_to_led.owner_settings import (
    DEFAULT_DAILY_LIMIT_USD,
    OwnerSettings,
    OwnerSettingsStore,
    write_json_atomically,
)


def test_the_defaults_keep_enhancement_off() -> None:
    assert OwnerSettings().ai_enhanced is False


def test_the_default_daily_limit_is_one_dollar() -> None:
    assert OwnerSettings().daily_limit_usd == DEFAULT_DAILY_LIMIT_USD == 1.0


def test_a_saved_limit_loads_back_equal(tmp_path: Path) -> None:
    store = OwnerSettingsStore(tmp_path / "settings.json")

    store.save(OwnerSettings(ai_enhanced=True, daily_limit_usd=0.25))

    assert store.load() == OwnerSettings(ai_enhanced=True, daily_limit_usd=0.25)


def test_a_file_without_a_limit_keeps_its_switch_and_takes_the_default_limit(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The file every Pi has from before the limit existed.
    path = tmp_path / "settings.json"
    path.write_text('{"ai_enhanced": true}', encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        settings = OwnerSettingsStore(path).load()

    assert settings == OwnerSettings(ai_enhanced=True, daily_limit_usd=1.0)
    assert caplog.records == []


def test_a_whole_number_limit_loads_as_a_float(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"ai_enhanced": false, "daily_limit_usd": 2}', encoding="utf-8")

    settings = OwnerSettingsStore(path).load()

    assert settings.daily_limit_usd == 2.0
    assert isinstance(settings.daily_limit_usd, float)


@pytest.mark.parametrize("limit", ["0", "0.0", "-1", "true", '"1"', "Infinity", "NaN", "null"])
def test_a_limit_that_is_not_a_positive_amount_loads_as_the_defaults_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, limit: str
) -> None:
    path = tmp_path / "settings.json"
    path.write_text(f'{{"ai_enhanced": true, "daily_limit_usd": {limit}}}', encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert OwnerSettingsStore(path).load() == OwnerSettings()

    assert caplog.records, "a broken settings file must be reported"


@pytest.mark.parametrize("ai_enhanced", [True, False])
def test_saved_settings_load_back_equal(tmp_path: Path, ai_enhanced: bool) -> None:
    # A parent that does not exist yet, as `var/` may not on a fresh checkout.
    store = OwnerSettingsStore(tmp_path / "var" / "settings.json")

    store.save(OwnerSettings(ai_enhanced=ai_enhanced))

    assert store.load() == OwnerSettings(ai_enhanced=ai_enhanced)


def test_the_file_holds_the_documented_json_shape(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"

    OwnerSettingsStore(path).save(OwnerSettings(ai_enhanced=True))

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "ai_enhanced": True,
        "daily_limit_usd": 1.0,
    }


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


def test_an_atomic_write_holds_the_json_and_leaves_no_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "var" / "state.json"

    write_json_atomically(path, {"a": 1, "b": [True, None]})

    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1, "b": [True, None]}
    assert [entry.name for entry in path.parent.iterdir()] == ["state.json"]
