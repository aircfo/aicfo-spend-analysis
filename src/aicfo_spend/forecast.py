"""Where spending is heading: next month's forecast, and a backtest that scores it.

Next month = seats x price + each team's average monthly usage over the last three
complete months, or over as many of them as have data. Each usage product is averaged
over the months that have a whole month of it, so a source that started recently
isn't diluted by months it wasn't saved for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from aicfo_spend.assign import seat_rows
from aicfo_spend.months import Month
from aicfo_spend.report import (
    PRODUCT_HEADINGS,
    ZERO,
    Amounts,
    MonthSpend,
    aligned,
    breakdown,
    build_month,
    dollars,
    month_directory,
    team_order,
    total,
    totals_by,
)
from aicfo_spend.settings import Settings
from aicfo_spend.sources.people import read_people_file
from aicfo_spend.spend_table import PRODUCT_API, PRODUCT_EXTRA_USAGE, PRODUCTS

HISTORY_MONTHS = 3
USAGE_PRODUCTS = (PRODUCT_EXTRA_USAGE, PRODUCT_API)


@dataclass(frozen=True)
class Forecast:
    month: Month
    as_of: date
    by_team: Amounts
    # The complete months each usage product was averaged over, oldest first.
    history: dict[str, list[Month]]
    notes: list[str]


def forecast_after(workspace: Path, settings: Settings, as_of: date) -> Forecast:
    """Forecast the month after as_of's month, using only months complete by as_of."""
    target = Month.of(as_of).next_month()
    notes: list[str] = []
    people_file = read_people_file(workspace)
    directory = month_directory(workspace, target, people_file, notes)
    seats, seat_notes = seat_rows(target, people_file, settings, directory)
    notes.extend(seat_notes)
    by_team = totals_by(seats, lambda row: row.team)

    candidates = _recent_complete_months(as_of)
    spends = {month: build_month(workspace, month, settings) for month in candidates}
    history: dict[str, list[Month]] = {}
    for product in USAGE_PRODUCTS:
        months = sorted(month for month in candidates if _has_whole_month(spends[month], product, as_of))
        history[product] = months
        if not months:
            notes.append(f"no complete month of {product} is saved before {as_of}, so it's forecast at $0")
            continue
        for month in months:
            product_rows = (row for row in spends[month].rows if row.product == product)
            for team, amounts in totals_by(product_rows, lambda row: row.team).items():
                team_amounts = by_team.setdefault(team, {name: ZERO for name in PRODUCTS})
                team_amounts[product] += amounts[product] / len(months)
    return Forecast(target, as_of, by_team, history, notes)


def describe_forecast(forecast: Forecast) -> str:
    lines = [f"Forecast for {forecast.month.label}, as of {forecast.as_of}", ""]
    lines += breakdown("Team", team_order(forecast.by_team), forecast.by_team, with_total=True)
    lines += ["", f"Seats: today's people.csv and seat prices for {forecast.month.label}."]
    for product in USAGE_PRODUCTS:
        months = forecast.history[product]
        if months:
            span = months[0].label if len(months) == 1 else f"{months[0].label} to {months[-1].label}"
            lines.append(f"{PRODUCT_HEADINGS[product]}: each team's average over {span} ({_months(len(months))}).")
    if forecast.notes:
        lines += ["", "Notes:", *(f"  - {note}" for note in forecast.notes)]
    return "\n".join(lines)


def describe_score(forecast: Forecast, actual: MonthSpend) -> str:
    """How far the forecast was from what happened, by team and in total."""
    actual_by_team = totals_by(actual.rows, lambda row: row.team)
    zeros = {product: ZERO for product in PRODUCTS}
    teams = team_order({team: actual_by_team.get(team, zeros) for team in {*forecast.by_team, *actual_by_team}})

    table = [["Team", "Forecast", "Actual", "Off by", "Off by %"]]
    for team in teams:
        table.append(_score_row(team, total(forecast.by_team.get(team, zeros)), total(actual_by_team.get(team, zeros))))
    forecast_total = sum((total(amounts) for amounts in forecast.by_team.values()), ZERO)
    actual_total = sum((row.amount_usd for row in actual.rows), ZERO)
    table.append(_score_row("Total", forecast_total, actual_total))

    error = forecast_total - actual_total
    headline = f"The forecast for {forecast.month.label} was {dollars(forecast_total)}; actual spend was {dollars(actual_total)}."
    if actual_total:
        headline += f" It was off by {_signed(error)} ({_percent(error, actual_total)})."
    lines = [headline, "", *aligned(table)]

    notes = [
        "seats come from today's people.csv, so the seat part of the forecast can't miss; the error is in usage",
    ]
    for product in USAGE_PRODUCTS:
        used = len(forecast.history[product])
        if used < HISTORY_MONTHS:
            notes.append(f"{product} was averaged over {_months(used)} of history, not {HISTORY_MONTHS}; the score is a thin read")
        if product not in actual.coverage:
            notes.append(f"no {product} is saved for {actual.month.label}, so its actual shows $0")
    lines += ["", "Notes:", *(f"  - {note}" for note in notes)]
    return "\n".join(lines)


def _recent_complete_months(as_of: date) -> list[Month]:
    month = Month.of(as_of)
    latest = month if as_of == month.last_day else month.previous_month()
    months = [latest]
    while len(months) < HISTORY_MONTHS:
        months.append(months[-1].previous_month())
    return months


def _has_whole_month(spend: MonthSpend, product: str, as_of: date) -> bool:
    covered = spend.coverage.get(product)
    return covered is not None and covered == (spend.month.first_day, spend.month.last_day) and covered[1] <= as_of


def _score_row(team: str, forecast: Decimal, actual: Decimal) -> list[str]:
    error = forecast - actual
    return [team, dollars(forecast), dollars(actual), _signed(error), _percent(error, actual) if actual else "n/a"]


def _signed(amount: Decimal) -> str:
    return ("+" if amount >= 0 else "-") + dollars(abs(amount))


def _percent(part: Decimal, whole: Decimal) -> str:
    return f"{part / whole * 100:+.1f}%"


def _months(count: int) -> str:
    return f"{count} month" if count == 1 else f"{count} months"
