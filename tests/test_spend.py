"""The ledger of today's AI cover spend.

What matters is that the total survives a restart, that a new UTC day starts
from $0, and that a missing, broken or unwritable file never stops the
service, nor lets the limit be forgotten while it runs.
"""

import json
import logging
import os
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from spotify_to_led.spend import SpendLedger

AFTERNOON = datetime(2026, 11, 13, 15, 0, tzinfo=UTC)
MIDNIGHT = datetime(2026, 11, 14, 0, 0, tzinfo=UTC)


class FakeClock:
    """A clock that stands still at `now` until a test moves it."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def a_ledger(path: Path, clock: FakeClock | None = None) -> SpendLedger:
    return SpendLedger(path, clock=clock or FakeClock(AFTERNOON))


def test_a_missing_file_is_nothing_spent(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        ledger = a_ledger(tmp_path / "ai-spend.json")

    assert ledger.spent_today() == 0.0
    assert caplog.records == []


def test_records_add_up_and_survive_a_restart(tmp_path: Path) -> None:
    # A parent that does not exist yet, as `var/` may not on a fresh checkout.
    path = tmp_path / "var" / "ai-spend.json"
    ledger = a_ledger(path)

    ledger.record(0.009)
    ledger.record(0.011)

    assert ledger.spent_today() == pytest.approx(0.02)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "day": "2026-11-13",
        "spent_usd": pytest.approx(0.02),
    }
    assert a_ledger(path).spent_today() == pytest.approx(0.02)


def test_a_total_stored_for_an_earlier_day_reads_as_nothing_and_is_replaced(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ai-spend.json"
    path.write_text('{"day": "2026-11-12", "spent_usd": 0.5}', encoding="utf-8")
    ledger = a_ledger(path)

    assert ledger.spent_today() == 0.0

    ledger.record(0.01)

    assert ledger.spent_today() == pytest.approx(0.01)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "day": "2026-11-13",
        "spent_usd": pytest.approx(0.01),
    }


def test_the_day_rolls_over_exactly_at_utc_midnight(tmp_path: Path) -> None:
    clock = FakeClock(MIDNIGHT - timedelta(microseconds=1))
    ledger = a_ledger(tmp_path / "ai-spend.json", clock)
    ledger.record(0.3)
    assert ledger.spent_today() == pytest.approx(0.3)

    clock.now = MIDNIGHT

    assert ledger.spent_today() == 0.0
    ledger.record(0.1)
    assert ledger.spent_today() == pytest.approx(0.1)


def test_the_day_is_the_utc_date_not_the_local_one(tmp_path: Path) -> None:
    # An evening west of Greenwich is already the next day in UTC.
    evening = datetime(2026, 11, 13, 20, 0, tzinfo=timezone(timedelta(hours=-5)))
    path = tmp_path / "ai-spend.json"
    a_ledger(path, FakeClock(evening)).record(0.01)

    assert json.loads(path.read_text(encoding="utf-8"))["day"] == "2026-11-14"


@pytest.mark.parametrize(
    "contents",
    [
        "{not json",
        "[]",
        "{}",
        '{"day": "2026-11-13"}',
        '{"day": "yesterday", "spent_usd": 0.1}',
        '{"day": 20261113, "spent_usd": 0.1}',
        '{"day": "2026-11-13", "spent_usd": "x"}',
        '{"day": "2026-11-13", "spent_usd": true}',
        '{"day": "2026-11-13", "spent_usd": -0.1}',
        '{"day": "2026-11-13", "spent_usd": NaN}',
        '{"day": "2026-11-13", "spent_usd": Infinity}',
    ],
)
def test_a_malformed_file_is_nothing_spent_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, contents: str
) -> None:
    path = tmp_path / "ai-spend.json"
    path.write_text(contents, encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        ledger = a_ledger(path)

    assert ledger.spent_today() == 0.0
    assert [r.levelno for r in caplog.records] == [logging.WARNING]


def test_a_failed_write_warns_and_still_counts_in_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    ledger = a_ledger(tmp_path / "ai-spend.json")

    def failing_fsync(fd: int) -> None:
        raise OSError("disk went away")

    monkeypatch.setattr(os, "fsync", failing_fsync)

    with caplog.at_level(logging.WARNING):
        ledger.record(0.4)

    assert ledger.spent_today() == pytest.approx(0.4)
    assert [r.levelno for r in caplog.records] == [logging.WARNING]


def test_it_resets_at_the_next_utc_midnight(tmp_path: Path) -> None:
    clock = FakeClock(AFTERNOON)
    ledger = a_ledger(tmp_path / "ai-spend.json", clock)

    assert ledger.resets_at() == MIDNIGHT

    clock.now = MIDNIGHT
    assert ledger.resets_at() == MIDNIGHT + timedelta(days=1)
