"""Where this month is heading: spend so far, the month-end projection, and the two alerts.

This month = seats + usage so far + the last 14 days' average daily usage × days left.
It's compared with the month's plan: the forecast for it made as of the last day of the
month before, saved the first time the month's pace is checked so it never moves.
"""

from __future__ import annotations

import json
import os
import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from aicfo_spend.assign import seat_rows
from aicfo_spend.daily import DailySeries, daily_usage, rename_teams
from aicfo_spend.errors import InputError
from aicfo_spend.forecast import USAGE_PRODUCTS, forecast_after
from aicfo_spend.months import Month
from aicfo_spend.report import ZERO, aligned, dollars, month_directory, total
from aicfo_spend.settings import AlertThresholds, Settings
from aicfo_spend.sources.people import read_people_file
from aicfo_spend.spend_table import UNASSIGNED

PROJECTION_WINDOW_DAYS = 14
SPIKE_HISTORY_DAYS = 28
# Fewer observed days than this make a median too jumpy to call anything a spike.
SPIKE_MIN_HISTORY_DAYS = 7
PLAN_FILE_NAME = "plan.json"
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Plan:
    total: Decimal
    made_as_of: date
    saved_on: date


@dataclass(frozen=True)
class Pace:
    month: Month
    # The last day each usage product's downloads reach.
    through: dict[str, date]
    so_far: dict[str, Decimal]
    projected: dict[str, Decimal]
    plan: Plan | None
    alerts: list[str]
    notes: list[str]


def pace_of(workspace: Path, settings: Settings, month: Month, today: date | None = None) -> Pace:
    today = today or date.today()
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
    this_month = {p: rename_teams(s, name_of) for p, s in daily_usage(workspace, month, directory, notes).items()}
    last_month = {p: rename_teams(s, name_of) for p, s in daily_usage(workspace, previous, previous_directory, []).items()}

    through: dict[str, date] = {}
    for product in USAGE_PRODUCTS:
        series = this_month[product]
        if not series.days:
            notes.append(f"no {product} is saved for {month.label} yet, so the projection leaves it out")
            continue
        end = max(series.days)
        through[product] = end
        history = {**last_month[product].days, **series.days}
        window = [day for day in (end - timedelta(days=back) for back in range(PROJECTION_WINDOW_DAYS)) if day in history]
        days_left = (month.last_day - end).days
        for team in {*series.latest, *(team for day in window for team in history[day])}:
            average = sum((history[day].get(team, ZERO) for day in window), ZERO) / len(window)
            so_far[team] += series.latest.get(team, ZERO)
            projected[team] += series.latest.get(team, ZERO) + average * days_left
    if len(set(through.values())) > 1:
        reached = ", ".join(f"{product} through {end}" for product, end in through.items())
        notes.append(f"the downloads reach different days ({reached}), so spikes are checked up to the earlier one")

    rounded_projected = _cents(projected)
    alerts = _spikes(this_month, last_month, through, month, settings.alerts, notes)
    plan, plan_note = plan_for(workspace, settings, month, through, today)
    if plan is None:
        notes.append(plan_note)
    else:
        alerts += _over_pace(month, sum(rounded_projected.values(), ZERO), plan, settings.alerts)
    return Pace(month, through, _cents(so_far), rounded_projected, plan, alerts, notes)


def plan_for(
    workspace: Path, settings: Settings, month: Month, needed: Iterable[str], today: date
) -> tuple[Plan | None, str]:
    """The month's saved plan, or a new one saved now if it has the history it needs.

    Returns the plan, or None with the reason there isn't one yet. A plan is only saved
    once every usage product the month has data for has a complete month of history,
    because a frozen plan missing a product would make every later check look over pace.
    """
    path = workspace / "out" / month.label / PLAN_FILE_NAME
    if path.exists():
        return _read_plan(path), ""
    if month.first_day > today:
        return None, f"no plan yet: {month.label} hasn't started, so over pace isn't checked"
    made_as_of = month.previous_month().last_day
    forecast = forecast_after(workspace, settings, made_as_of)
    missing = [product for product in needed if not forecast.history[product]]
    if missing:
        return None, (
            f"no plan yet: no complete month of {' or '.join(missing)} is saved before {made_as_of}, "
            "so over pace isn't checked"
        )
    by_team = {team: total(amounts) for team, amounts in forecast.by_team.items()}
    plan = Plan(sum(by_team.values(), ZERO), made_as_of, today)
    document = {
        "month": month.label,
        "made_as_of": made_as_of.isoformat(),
        "saved_on": today.isoformat(),
        "total_usd": f"{plan.total:f}",
        "by_team": {team: f"{amount:f}" for team, amount in sorted(by_team.items())},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written beside the final name, then renamed, so a crash can't leave half a plan.
    partial = path.with_suffix(".json.partial")
    partial.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, path)
    return plan, ""


def describe_pace(pace: Pace) -> str:
    so_far_total = sum(pace.so_far.values(), ZERO)
    projected_total = sum(pace.projected.values(), ZERO)
    reached = f" through {min(pace.through.values())}" if pace.through else ""
    lines = [f"{pace.month.label}{reached}: {dollars(so_far_total)} so far, on pace for {dollars(projected_total)}"]
    if pace.plan is not None:
        lines.append(
            f"Plan: {dollars(pace.plan.total)} (the forecast made as of {pace.plan.made_as_of}, saved {pace.plan.saved_on})"
        )
    lines.append("")
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
    this_month: dict[str, DailySeries],
    last_month: dict[str, DailySeries],
    through: dict[str, date],
    month: Month,
    thresholds: AlertThresholds,
    notes: list[str],
) -> list[str]:
    """A team's day over the spike multiple of its 28-day median and at least the minimum above it.

    Only observed days count, on both sides: a day spread evenly over a gap between
    downloads is an average, so it can neither be a spike nor set what normal looks like.
    """
    if not through:
        return []
    checked_through = min(through.values())
    combined: dict[date, dict[str, Decimal]] = {}
    observed: set[date] = set()
    for series_by_product in (last_month, this_month):
        days = {day for series in series_by_product.values() for day in series.days}
        for day in days:
            covering = [series for series in series_by_product.values() if day in series.days]
            if all(day in series.observed for series in covering):
                observed.add(day)
            combined_day = combined.setdefault(day, {})
            for series in covering:
                for team, amount in series.days[day].items():
                    combined_day[team] = combined_day.get(team, ZERO) + amount

    month_days = [day for day in combined if Month.of(day) == month and day <= checked_through]
    spread = sum(1 for day in month_days if day not in observed)
    if spread:
        notes.append(
            f"{spread} of {len(month_days)} days this month are spread evenly between downloads saved days apart; "
            "spikes are only checked on days with a download the day before"
        )

    spike_days: dict[str, list[tuple[date, Decimal, Decimal]]] = defaultdict(list)
    for day in sorted(month_days):
        if day not in observed:
            continue
        for team, amount in combined[day].items():
            earlier = (day - timedelta(days=back) for back in range(1, SPIKE_HISTORY_DAYS + 1))
            history = [combined[earlier_day].get(team, ZERO) for earlier_day in earlier if earlier_day in observed]
            if len(history) < SPIKE_MIN_HISTORY_DAYS:
                continue
            median = statistics.median(history)
            if amount > thresholds.spike_multiple * median and amount - median >= thresholds.spike_min_usd:
                spike_days[team].append((day, amount, median))

    alerts: list[str] = []
    for team, team_days in sorted(spike_days.items()):
        for run in _consecutive(team_days):
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


def _over_pace(month: Month, projected_total: Decimal, plan: Plan, thresholds: AlertThresholds) -> list[str]:
    if plan.total <= 0 or projected_total < plan.total * (1 + thresholds.over_pace_percent / 100):
        return []
    over = (projected_total - plan.total) / plan.total * 100
    return [
        f"Over pace: {month.label} is on pace for {dollars(projected_total)}, {over:.0f}% above the "
        f"{dollars(plan.total)} forecast for it made as of {plan.made_as_of}."
    ]


def _read_plan(path: Path) -> Plan:
    problem = (
        f"{path} isn't a plan the tool wrote. Removing it lets the next check save a new plan, "
        "built from the downloads saved by then"
    )
    try:
        document: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise InputError(problem) from None
    if not isinstance(document, dict):
        raise InputError(problem)
    fields = [document.get(key) for key in ("total_usd", "made_as_of", "saved_on")]
    if not all(isinstance(value, str) for value in fields):
        raise InputError(problem)
    total_text, made_text, saved_text = (str(value) for value in fields)
    try:
        return Plan(Decimal(total_text), date.fromisoformat(made_text), date.fromisoformat(saved_text))
    except (InvalidOperation, ValueError):
        raise InputError(problem) from None


def _cents(amounts: dict[str, Decimal]) -> dict[str, Decimal]:
    return {team: amount.quantize(CENT, ROUND_HALF_UP) for team, amount in amounts.items()}
