"""Where this month is heading: spend so far, the month-end projection, and the two alerts.

This month = seats + usage so far + the last 14 days' average daily usage × days left.
It's compared with the month's plan: the forecast for it made as of the last day of the
month before, saved the first time the month's pace is checked so it never moves.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from aicfo_spend.assign import seat_rows
from aicfo_spend.daily import Daily, daily_usage, rename_teams
from aicfo_spend.errors import InputError
from aicfo_spend.forecast import USAGE_PRODUCTS, forecast_after
from aicfo_spend.months import Month
from aicfo_spend.report import ZERO, aligned, dollars, month_directory, total
from aicfo_spend.settings import AlertThresholds, Settings
from aicfo_spend.sources.people import read_people_file
from aicfo_spend.spend_table import UNASSIGNED

PROJECTION_WINDOW_DAYS = 14
SPIKE_HISTORY_DAYS = 28
# Fewer days than this make a median too jumpy to call anything a spike.
SPIKE_MIN_HISTORY_DAYS = 7
PLAN_FILE_NAME = "plan.json"
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Pace:
    month: Month
    # The last day each usage product's downloads reach.
    through: dict[str, date]
    so_far: dict[str, Decimal]
    projected: dict[str, Decimal]
    plan_total: Decimal
    plan_made_as_of: date
    alerts: list[str]
    notes: list[str]


def pace_of(workspace: Path, settings: Settings, month: Month) -> Pace:
    notes: list[str] = []
    people_file = read_people_file(workspace)
    directory = month_directory(workspace, month, people_file, notes)
    seats, seat_notes = seat_rows(month, people_file, settings, directory)
    notes.extend(seat_notes)
    spellings = {team.casefold(): team for team in [*directory.teams.values(), *(row.team for row in seats)]}

    def name_of(team: str) -> str:
        return spellings.setdefault(team.casefold(), team)

    so_far: dict[str, Decimal] = defaultdict(lambda: ZERO)
    projected: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in seats:
        so_far[name_of(row.team)] += row.amount_usd
        projected[name_of(row.team)] += row.amount_usd

    previous = month.previous_month()
    previous_directory = month_directory(workspace, previous, people_file, [])
    this_month = {product: rename_teams(daily, name_of) for product, daily in daily_usage(workspace, month, directory, notes).items()}
    last_month = {
        product: rename_teams(daily, name_of)
        for product, daily in daily_usage(workspace, previous, previous_directory, []).items()
    }

    through: dict[str, date] = {}
    for product in USAGE_PRODUCTS:
        days = this_month[product]
        if not days:
            notes.append(f"no {product} is saved for {month.label} yet, so the projection leaves it out")
            continue
        end = max(days)
        through[product] = end
        history = {**last_month[product], **days}
        window = [day for day in (end - timedelta(days=back) for back in range(PROJECTION_WINDOW_DAYS)) if day in history]
        days_left = (month.last_day - end).days
        teams = {team for day_teams in days.values() for team in day_teams} | {team for day in window for team in history[day]}
        for team in teams:
            spent = sum((teams_on_day.get(team, ZERO) for teams_on_day in days.values()), ZERO)
            average = sum((history[day].get(team, ZERO) for day in window), ZERO) / len(window)
            so_far[team] += spent
            projected[team] += spent + average * days_left
    if len(set(through.values())) > 1:
        reached = ", ".join(f"{product} through {end}" for product, end in through.items())
        notes.append(f"the downloads reach different days ({reached}), so spikes are checked up to the earlier one")

    plan_total, plan_made_as_of = plan_for(workspace, settings, month)
    rounded_projected = _cents(projected)
    alerts = _spikes(this_month, last_month, through, month, settings.alerts)
    alerts += _over_pace(month, sum(rounded_projected.values(), ZERO), plan_total, plan_made_as_of, settings.alerts)
    return Pace(month, through, _cents(so_far), rounded_projected, plan_total, plan_made_as_of, alerts, notes)


def plan_for(workspace: Path, settings: Settings, month: Month) -> tuple[Decimal, date]:
    """The month's plan: read it if saved, else forecast it as of the month before and save it."""
    path = workspace / "out" / month.label / PLAN_FILE_NAME
    if path.exists():
        return _read_plan(path)
    made_as_of = month.previous_month().last_day
    forecast = forecast_after(workspace, settings, made_as_of)
    by_team = {team: total(amounts) for team, amounts in forecast.by_team.items()}
    plan_total = sum(by_team.values(), ZERO)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "month": month.label,
        "made_as_of": made_as_of.isoformat(),
        "total_usd": f"{plan_total:f}",
        "by_team": {team: f"{amount:f}" for team, amount in sorted(by_team.items())},
    }
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return plan_total, made_as_of


def describe_pace(pace: Pace) -> str:
    so_far_total = sum(pace.so_far.values(), ZERO)
    projected_total = sum(pace.projected.values(), ZERO)
    reached = f" through {min(pace.through.values())}" if pace.through else ""
    lines = [
        f"{pace.month.label}{reached}: {dollars(so_far_total)} so far, on pace for {dollars(projected_total)}",
        f"Plan (the forecast made as of {pace.plan_made_as_of}): {dollars(pace.plan_total)}",
        "",
    ]
    teams = sorted((team for team in pace.projected if team != UNASSIGNED), key=lambda team: -pace.projected[team])
    if UNASSIGNED in pace.projected:
        teams.append(UNASSIGNED)
    table = [["Team", "So far", "On pace for"]]
    table += [[team, dollars(pace.so_far.get(team, ZERO)), dollars(pace.projected[team])] for team in teams]
    table.append(["Total", dollars(so_far_total), dollars(projected_total)])
    lines += aligned(table)
    lines += ["", "Alerts (drafts, not sent):" if pace.alerts else "No alerts."]
    lines += [f"  - {alert}" for alert in pace.alerts]
    if pace.notes:
        lines += ["", "Notes:", *(f"  - {note}" for note in pace.notes)]
    return "\n".join(lines)


def _spikes(
    this_month: dict[str, Daily],
    last_month: dict[str, Daily],
    through: dict[str, date],
    month: Month,
    thresholds: AlertThresholds,
) -> list[str]:
    """A team's day over the spike multiple of its 28-day median and at least the minimum above it."""
    if not through:
        return []
    checked_through = min(through.values())
    combined: Daily = {}
    for daily in (*last_month.values(), *this_month.values()):
        for day, teams in daily.items():
            combined_day = combined.setdefault(day, {})
            for team, amount in teams.items():
                combined_day[team] = combined_day.get(team, ZERO) + amount

    spike_days: dict[str, list[tuple[date, Decimal, Decimal]]] = defaultdict(list)
    for day in sorted(combined):
        if Month.of(day) != month or day > checked_through:
            continue
        for team, amount in combined[day].items():
            earlier = (day - timedelta(days=back) for back in range(1, SPIKE_HISTORY_DAYS + 1))
            history = [combined[earlier_day].get(team, ZERO) for earlier_day in earlier if earlier_day in combined]
            if len(history) < SPIKE_MIN_HISTORY_DAYS:
                continue
            median = statistics.median(history)
            if amount > thresholds.spike_multiple * median and amount - median >= thresholds.spike_min_usd:
                spike_days[team].append((day, amount, median))

    alerts: list[str] = []
    for team, days in sorted(spike_days.items()):
        for run in _consecutive(days):
            first_day, _, median = run[0]
            peak = max(amount for _, amount, _ in run)
            usual = f"against a usual day of {dollars(median)}"
            if len(run) == 1:
                alerts.append(f"Spike: {team} spent {dollars(peak)} on {first_day}, {usual}.")
            else:
                alerts.append(f"Spike: {team} spent up to {dollars(peak)} a day from {first_day} to {run[-1][0]}, {usual}.")
    return alerts


def _consecutive(days: list[tuple[date, Decimal, Decimal]]) -> list[list[tuple[date, Decimal, Decimal]]]:
    runs: list[list[tuple[date, Decimal, Decimal]]] = []
    for entry in days:
        if runs and entry[0] - runs[-1][-1][0] == timedelta(days=1):
            runs[-1].append(entry)
        else:
            runs.append([entry])
    return runs


def _over_pace(
    month: Month, projected_total: Decimal, plan_total: Decimal, made_as_of: date, thresholds: AlertThresholds
) -> list[str]:
    if plan_total <= 0:
        return []
    if projected_total < plan_total * (1 + thresholds.over_pace_percent / 100):
        return []
    over = (projected_total - plan_total) / plan_total * 100
    return [
        f"Over pace: {month.label} is on pace for {dollars(projected_total)}, {over:.0f}% above the "
        f"{dollars(plan_total)} forecast for it made as of {made_as_of}."
    ]


def _read_plan(path: Path) -> tuple[Decimal, date]:
    problem = f"{path} isn't a plan the tool wrote; remove it and check the pace again"
    try:
        document: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise InputError(problem) from None
    if not isinstance(document, dict):
        raise InputError(problem)
    total_text, made_text = document.get("total_usd"), document.get("made_as_of")
    if not isinstance(total_text, str) or not isinstance(made_text, str):
        raise InputError(problem)
    try:
        return Decimal(total_text), date.fromisoformat(made_text)
    except (InvalidOperation, ValueError):
        raise InputError(problem) from None


def _cents(amounts: dict[str, Decimal]) -> dict[str, Decimal]:
    return {team: amount.quantize(CENT, ROUND_HALF_UP) for team, amount in amounts.items()}
