"""Where spending is heading: next month's forecast, and a backtest that scores it.

Next month = seats x price + extra usage + API + the operator's known changes.

- Extra usage is each team's average over the last three complete months, or over as many
  as have a whole month of it, so a source that started recently isn't diluted.
- API is the last complete month's spend on current keys. On airCFO's twelve months of
  Ramp history, last month's API spend missed the next by 27% on average, against 43% for
  the three-month average: API spend moves in steps (a key starts, a project ends), and an
  average lags every step. Spend no current key carries is left out, because a deleted key
  doesn't spend again.
- Known changes, from settings, add what history can't show, like a new project's key.
- Once this month is three weeks in, it stands in as the newest month, scaled to a whole
  month: API spend moves in steps, and a step taken this month shouldn't wait a month to count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from aicfo_spend.assign import REASON_NOT_TIED_TO_A_KEY, seat_rows
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
from aicfo_spend.settings import KnownChange, Settings
from aicfo_spend.sources.people import read_people_file
from aicfo_spend.spend_table import PRODUCT_API, PRODUCT_EXTRA_USAGE, PRODUCTS

HISTORY_MONTHS = 3
# The API forecast is the newest complete month alone; see the module's note.
API_HISTORY_MONTHS = 1
# From this day of the month on, the month so far, scaled up, is a better guide than last month.
RUN_RATE_FROM_DAY = 21
USAGE_PRODUCTS = (PRODUCT_EXTRA_USAGE, PRODUCT_API)
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Forecast:
    month: Month
    as_of: date
    by_team: Amounts
    # The complete months each usage product was averaged over, oldest first.
    history: dict[str, list[Month]]
    notes: list[str]
    # The known changes added, each with the team it was added to, kept apart from the notes so a
    # shared view can say them without the operator's own words or an unfolded team.
    known_changes: list[tuple[str, KnownChange]] = field(default_factory=list)
    # For each usage product whose newest month is this month so far, scaled to a whole month:
    # the last day that month's downloads reach.
    run_rate: dict[str, date] = field(default_factory=dict)


def known_change_note(team: str, change: KnownChange, with_note: bool = True) -> str:
    sign = "+" if change.amount_usd >= 0 else "-"
    return (
        f"includes a known change: {sign}{dollars(abs(change.amount_usd))} of {change.product} for {team}"
        + (f" ({change.note})" if change.note and with_note else "")
    )


def forecast_after(workspace: Path, settings: Settings, as_of: date) -> Forecast:
    """Forecast the month after as_of's month, using only months complete by as_of."""
    target = Month.of(as_of).next_month()
    notes: list[str] = []
    people_file = read_people_file(workspace)
    directory = month_directory(workspace, target, people_file, notes)
    seats, seat_notes = seat_rows(target, people_file, settings, directory)
    notes.extend(seat_notes)
    by_team = totals_by(seats, lambda row: row.team)
    # Each month's people list may spell a team differently; merge them under one spelling,
    # preferring the target month's.
    spellings = {team.casefold(): team for team in [*directory.teams.values(), *by_team]}

    candidates = _recent_complete_months(as_of)
    spends = {month: build_month(workspace, month, settings) for month in candidates}
    current = Month.of(as_of)
    if current not in spends:
        spends[current] = build_month(workspace, current, settings)
    history: dict[str, list[Month]] = {}
    run_rate: dict[str, date] = {}
    scale: dict[Month, Decimal] = {}
    for product in USAGE_PRODUCTS:
        months = sorted(month for month in candidates if _has_whole_month(spends[month], product))
        covered = spends[current].coverage.get(product)
        # Only downloads from before as_of count, so a replay of a past date sees what that date saw.
        if (current not in candidates and covered and covered[0] == current.first_day
                and RUN_RATE_FROM_DAY <= covered[1].day and covered[1] < as_of):
            months.append(current)
            run_rate[product] = covered[1]
            scale[current] = Decimal(current.last_day.day) / Decimal(covered[1].day)
        months = months[-API_HISTORY_MONTHS:] if product == PRODUCT_API else months[-HISTORY_MONTHS:]
        history[product] = months
        if not months:
            notes.append(f"no complete month of {product} is saved before {as_of}, so it's forecast at $0")
            continue
        for month in months:
            product_rows = (
                row for row in spends[month].rows
                if row.product == product and row.reason != REASON_NOT_TIED_TO_A_KEY
            )
            for team, amounts in totals_by(product_rows, lambda row: row.team).items():
                name = spellings.setdefault(team.casefold(), team)
                team_amounts = by_team.setdefault(name, {product_name: ZERO for product_name in PRODUCTS})
                team_amounts[product] += amounts[product] * scale.get(month, Decimal(1)) / len(months)
    applied: list[tuple[str, KnownChange]] = []
    for change in settings.known_changes:
        if change.month != target:
            continue
        name = spellings.setdefault(change.team.casefold(), change.team)
        by_team.setdefault(name, {product_name: ZERO for product_name in PRODUCTS})[change.product] += change.amount_usd
        applied.append((name, change))
    # Rounded to cents per team, so the rows people see add up to the total they see.
    rounded = {team: {product: amount.quantize(CENT, ROUND_HALF_UP) for product, amount in amounts.items()}
               for team, amounts in by_team.items()}
    return Forecast(target, as_of, rounded, history, notes, applied, {
        product: through for product, through in run_rate.items() if current in history[product]
    })


def describe_forecast(forecast: Forecast) -> str:
    lines = [f"Forecast for {forecast.month.label}, as of {forecast.as_of}", ""]
    lines += breakdown("Team", team_order(forecast.by_team), forecast.by_team, with_total=True)
    lines += ["", f"Seats: today's people.csv and seat prices for {forecast.month.label}."]
    for product in USAGE_PRODUCTS:
        months = forecast.history[product]
        if months:
            span = months[0].label if len(months) == 1 else f"{months[0].label} to {months[-1].label}"
            if product in forecast.run_rate:
                span += f" ({months[-1].label} through {forecast.run_rate[product]}, scaled to a whole month)"
            if product == PRODUCT_API:
                lines.append(f"{PRODUCT_HEADINGS[product]}: each team's spend on current keys in {span}.")
            else:
                lines.append(f"{PRODUCT_HEADINGS[product]}: each team's average over {span} ({_months(len(months))}).")
    notes = [*forecast.notes, *(known_change_note(team, change) for team, change in forecast.known_changes)]
    if notes:
        lines += ["", "Notes:", *(f"  - {note}" for note in notes)]
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
        f"the months before {forecast.as_of} are read from today's downloads and people list, so this replays the "
        "forecast with today's view of those months; one made on the day could differ if they were revised since",
    ]
    for product in USAGE_PRODUCTS:
        used = len(forecast.history[product])
        if used < (API_HISTORY_MONTHS if product == PRODUCT_API else HISTORY_MONTHS):
            notes.append(f"{product} was averaged over {_months(used)} of history, not {HISTORY_MONTHS}; the score is a thin read")
        covered = actual.coverage.get(product)
        if covered is None:
            notes.append(f"no {product} is saved for {actual.month.label}, so its actual shows $0")
        elif covered[1] != actual.month.last_day:
            notes.append(f"{actual.month.label}'s {product} only runs through {covered[1]}, so its actual is partial")
    lines += ["", "Notes:", *(f"  - {note}" for note in notes)]
    return "\n".join(lines)


def _recent_complete_months(as_of: date) -> list[Month]:
    month = Month.of(as_of)
    latest = month if as_of == month.last_day else month.previous_month()
    months = [latest]
    while len(months) < HISTORY_MONTHS:
        months.append(months[-1].previous_month())
    return months


def _has_whole_month(spend: MonthSpend, product: str) -> bool:
    return spend.coverage.get(product) == (spend.month.first_day, spend.month.last_day)


def _score_row(team: str, forecast: Decimal, actual: Decimal) -> list[str]:
    error = forecast - actual
    return [team, dollars(forecast), dollars(actual), _signed(error), _percent(error, actual) if actual else "n/a"]


def _signed(amount: Decimal) -> str:
    return ("+" if amount >= 0 else "-") + dollars(abs(amount))


def _percent(part: Decimal, whole: Decimal) -> str:
    return f"{part / whole * 100:+.1f}%"


def _months(count: int) -> str:
    return f"{count} month" if count == 1 else f"{count} months"
