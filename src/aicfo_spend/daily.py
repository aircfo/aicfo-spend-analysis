"""Daily usage from month-to-date downloads.

A day's usage is the difference between that day's download and the one before it.
When downloads are several days apart, the difference is spread evenly across those
days; the first download of a month is spread from the 1st. Only days that are a
one-day difference count as observed, since spread days are an average, not a reading.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from aicfo_spend.assign import Directory
from aicfo_spend.errors import UserError
from aicfo_spend.months import Month
from aicfo_spend.raw_store import CLAUDE_SPEND_EXPORT, RAMP_API_KEYS, SavedFile, saved_files
from aicfo_spend.sources.ramp import read_key_spend
from aicfo_spend.spend_table import PRODUCT_API, PRODUCT_EXTRA_USAGE
from aicfo_spend.vendors.anthropic.spend_export import read_spend_export

ZERO = Decimal(0)

Daily = dict[date, dict[str, Decimal]]  # day -> team -> dollars

SOURCE_OF = {PRODUCT_EXTRA_USAGE: CLAUDE_SPEND_EXPORT, PRODUCT_API: RAMP_API_KEYS}


@dataclass(frozen=True)
class DailySeries:
    days: Daily
    # Days whose amount is a one-day difference between downloads, not spread over a gap.
    observed: frozenset[date]
    # Each team's month-to-date total in the newest download.
    latest: dict[str, Decimal]


def daily_usage(
    workspace: Path,
    month: Month,
    directory: Directory,
    notes: list[str],
    team_name: Callable[[str], str] | None = None,
) -> dict[str, DailySeries]:
    """Each usage product's spend per day and team, for the days the month's downloads cover.

    team_name renames each team as it's placed, so totals, and any note about them, use the
    names the caller shows.
    """
    name_of = team_name or _unchanged
    return {
        product: _daily(workspace, month, source, directory, notes, name_of) for product, source in SOURCE_OF.items()
    }


def _daily(
    workspace: Path, month: Month, source: str, directory: Directory, notes: list[str], name_of: Callable[[str], str]
) -> DailySeries:
    # For each day a download reaches, the one saved last counts.
    latest: dict[date, SavedFile] = {}
    for saved in saved_files(workspace, source, month):
        current = latest.get(saved.end)
        if current is None or saved.saved_at > current.saved_at:
            latest[saved.end] = saved

    daily: Daily = {}
    observed: set[date] = set()
    previous_end = month.first_day - timedelta(days=1)
    previous_totals: dict[str, Decimal] = {}
    for end in sorted(latest):
        try:
            totals = _team_totals(latest[end], source, directory, name_of)
        except UserError:
            notes.append(f"couldn't read {latest[end].path.name}, so its days are spread over the next download")
            continue
        days = [previous_end + timedelta(days=offset) for offset in range(1, (end - previous_end).days + 1)]
        if len(days) == 1:
            observed.add(end)
        for day in days:
            daily[day] = {}
        shrunk: list[str] = []
        for team in {*totals, *previous_totals}:
            change = totals.get(team, ZERO) - previous_totals.get(team, ZERO)
            # Month-to-date totals only grow; a drop means a revision or downloads out of order.
            if change < 0:
                shrunk.append(team)
                change = ZERO
            for day in days:
                daily[day][team] = change / len(days)
        if shrunk:
            notes.append(
                f"{latest[end].path.name} shows less than the download before it for {', '.join(sorted(shrunk))}, "
                "so those days count as $0"
            )
        previous_end, previous_totals = end, totals
    return DailySeries(daily, frozenset(observed), previous_totals)


def _team_totals(
    saved: SavedFile, source: str, directory: Directory, name_of: Callable[[str], str]
) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}

    def add(team: str, amount: Decimal) -> None:
        name = name_of(team)
        totals[name] = totals.get(name, ZERO) + amount

    if source == RAMP_API_KEYS:
        for key in read_key_spend(saved.path):
            add(directory.place_key(key.department_tag, key.owner_email).team, key.amount_usd)
    else:
        for usage in read_spend_export(saved.path):
            add(directory.place_person(usage.email).team, usage.amount_usd)
    return totals


def _unchanged(team: str) -> str:
    return team
