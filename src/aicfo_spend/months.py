"""Calendar months, the unit every report covers."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

from aicfo_spend.errors import InputError

MONTH_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")


@dataclass(frozen=True, order=True)
class Month:
    year: int
    number: int

    @classmethod
    def parse(cls, text: str) -> Month:
        match = MONTH_PATTERN.match(text)
        if match is None or not 1 <= int(match.group(2)) <= 12:
            raise InputError(f"{text!r} isn't a month; write it like 2026-09")
        return cls(int(match.group(1)), int(match.group(2)))

    @classmethod
    def of(cls, day: date) -> Month:
        return cls(day.year, day.month)

    @property
    def label(self) -> str:
        return f"{self.year:04d}-{self.number:02d}"

    @property
    def first_day(self) -> date:
        return date(self.year, self.number, 1)

    @property
    def last_day(self) -> date:
        return date(self.year, self.number, calendar.monthrange(self.year, self.number)[1])

    def days(self) -> list[date]:
        return [self.first_day + timedelta(days=offset) for offset in range(self.last_day.day)]
