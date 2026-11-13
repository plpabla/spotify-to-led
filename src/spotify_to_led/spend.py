"""Today's spend on AI-enhanced covers, kept across restarts.

The day is the UTC date, because a daily OpenRouter key limit resets at
00:00 UTC and the enhancer's refusal pause ends then too: one day boundary
for the whole service. Only today is kept. The file holds
`{"day": "YYYY-MM-DD", "spent_usd": <float>}`, is read once at start, and is
rewritten after every generation that reported a cost, the same atomic way as
the owner's settings. A total stored for any other day reads as $0.

The total counts what OpenRouter reported for each successful generation and
nothing else: a call abandoned midway reports no cost to us, so it is not in
here.

A missing file is $0, the normal state before the first generation. A broken
one is warned about and also read as $0, and a failed write is warned about
while the total in memory still counts, so the limit holds until a restart.
Neither stops the service.
"""

import json
import logging
import math
from datetime import UTC, date, datetime
from pathlib import Path

from spotify_to_led.enhance import Clock, next_utc_midnight
from spotify_to_led.state_file import write_json_atomically

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SpendLedger:
    """The USD spent on generations on one UTC day, persisted at `path`."""

    def __init__(self, path: Path, *, clock: Clock = _utc_now) -> None:
        self.path = path
        self._clock = clock
        self._day, self._spent = self._load()

    def spent_today(self) -> float:
        """What today's generations have cost so far, in USD."""
        if self._day != self._today():
            return 0.0
        return self._spent

    def record(self, cost: float) -> None:
        """Add `cost` to today's total and save it, starting a new day if one began."""
        today = self._today()
        if self._day != today:
            self._day, self._spent = today, 0.0
        self._spent += cost
        try:
            write_json_atomically(
                self.path, {"day": today.isoformat(), "spent_usd": self._spent}
            )
        except OSError as error:
            logger.warning(
                "saving today's AI spend to %s failed (%s); it is counted until a restart",
                self.path,
                type(error).__name__,
            )

    def resets_at(self) -> datetime:
        """When today's total goes back to $0: the next 00:00 UTC."""
        return next_utc_midnight(self._clock())

    def _today(self) -> date:
        return self._clock().astimezone(UTC).date()

    def _load(self) -> tuple[date | None, float]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return _from_json(raw)
        except FileNotFoundError:
            return None, 0.0
        except (OSError, ValueError, TypeError, KeyError) as error:
            logger.warning(
                "ignoring unreadable AI spend file %s (%s); counting today from $0",
                self.path,
                type(error).__name__,
            )
            return None, 0.0


def _from_json(raw: object) -> tuple[date, float]:
    if not isinstance(raw, dict):
        raise TypeError("AI spend file is not a JSON object")
    day = raw["day"]
    if not isinstance(day, str):
        raise TypeError("day is not a string")
    spent = raw["spent_usd"]
    if isinstance(spent, bool) or not isinstance(spent, int | float):
        raise TypeError("spent_usd is not a number")
    if not math.isfinite(spent) or spent < 0:
        raise ValueError("spent_usd is not an amount")
    # `fromisoformat` raises ValueError for a string that is not a date.
    return date.fromisoformat(day), float(spent)
