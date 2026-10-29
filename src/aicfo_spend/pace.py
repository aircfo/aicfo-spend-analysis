"""Where this month is heading: spend so far, the month-end projection, and the two alerts.

This month = seats + usage so far + the last 14 days' average daily usage × days left, plus
any API spend no current key carries, counted as it stands and not projected.
It's compared with the month's plan: the forecast for it made as of the last day of the
month before, saved the first time the month's pace is checked so it never moves.
"""

from __future__ import annotations

import json
import os
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from aicfo_spend.assign import seat_rows
from aicfo_spend.daily import SOURCE_OF, DailySeries, daily_usage
from aicfo_spend.errors import InputError
from aicfo_spend.forecast import USAGE_PRODUCTS, forecast_after
from aicfo_spend.months import Month
from aicfo_spend.raw_store import saved_files
from aicfo_spend.report import ZERO, MonthSpend, aligned, build_month, dollars, month_directory, total, untied_so_far
from aicfo_spend.settings import AlertThresholds, Settings
from aicfo_spend.sources.people import read_people_file
from aicfo_spend.spend_table import PRODUCT_API, PRODUCT_SEATS, PRODUCTS, UNASSIGNED

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
    # Seats, extra usage and API, so the page can say which of them moved spend off plan.
    # Empty for a plan saved before plans kept it.
    by_product: dict[str, Decimal] = field(default_factory=dict)
    by_team: dict[str, Decimal] = field(default_factory=dict)


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
    # The whole company's spend so far and month-end projection for each product.
    so_far_by_product: dict[str, Decimal] = field(default_factory=dict)
    projected_by_product: dict[str, Decimal] = field(default_factory=dict)


def pace_of(
    workspace: Path,
    settings: Settings,
    month: Month,
    today: date | None = None,
    team_name: Callable[[str], str] | None = None,
) -> Pace:
    """team_name renames teams before anything is added up, so spikes are judged on the teams shown."""
    today = today or date.today()
    notes: list[str] = []
    people_file = read_people_file(workspace)
    directory = month_directory(workspace, month, people_file, notes)
    seats, seat_notes = seat_rows(month, people_file, settings, directory)
    notes.extend(seat_notes)
    spellings = {team.casefold(): team for team in [*directory.teams.values(), *(row.team for row in seats)]}

    def name_of(team: str) -> str:
        spelled = spellings.setdefault(team.casefold(), team)
        return team_name(spelled) if team_name else spelled

    so_far: dict[str, Decimal] = defaultdict(lambda: ZERO)
    projected: dict[str, Decimal] = defaultdict(lambda: ZERO)
    product_so_far: dict[str, Decimal] = defaultdict(lambda: ZERO)
    product_projected: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in seats:
        so_far[name_of(row.team)] += row.amount_usd
        projected[name_of(row.team)] += row.amount_usd
        product_so_far[PRODUCT_SEATS] += row.amount_usd
        product_projected[PRODUCT_SEATS] += row.amount_usd

    previous = month.previous_month()
    previous_directory = month_directory(workspace, previous, people_file, [])
    this_month = daily_usage(workspace, month, directory, notes, name_of)
    last_month = daily_usage(workspace, previous, previous_directory, [], name_of)

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
            product_so_far[product] += series.latest.get(team, ZERO)
            product_projected[product] += series.latest.get(team, ZERO) + average * days_left
    # Counted in full but never projected or checked for spikes: it's mostly spend on keys
    # deleted during the month, which moves out of a team's numbers rather than adding to them.
    untied, untied_notes = untied_so_far(workspace, month)
    notes.extend(untied_notes)
    if untied:
        so_far[name_of(UNASSIGNED)] += untied
        projected[name_of(UNASSIGNED)] += untied
        product_so_far[PRODUCT_API] += untied
        product_projected[PRODUCT_API] += untied
    if len(set(through.values())) > 1:
        reached = ", ".join(f"{product} through {end}" for product, end in through.items())
        notes.append(f"the downloads reach different days ({reached}), so spikes are checked up to the earlier one")

    rounded_projected = _cents(projected)
    alerts = _spikes(this_month, last_month, through, month, settings.alerts, notes)
    ever_saved = [product for product, source in SOURCE_OF.items() if saved_files(workspace, source)]
    plan, plan_note = plan_for(workspace, settings, month, ever_saved, today)
    if plan is None:
        notes.append(plan_note)
    else:
        alerts += _over_pace(month, sum(rounded_projected.values(), ZERO), plan, settings.alerts)
    return Pace(month, through, _cents(so_far), rounded_projected, plan, alerts, notes,
                _cents(product_so_far), _cents(product_projected))


def plan_for(
    workspace: Path, settings: Settings, month: Month, needed: Iterable[str], today: date
) -> tuple[Plan | None, str]:
    """The month's saved plan, or a new one saved now if it has the history it needs.

    Returns the plan, or None with the reason there isn't one yet. A plan is only saved
    once every usage product in needed (each one with any download saved) has a complete
    month of history, because a frozen plan missing a product would make every later
    check look over pace, even on the 1st, before the month has a download of its own.
    A workspace with no usage download at all has no plan yet either: one saved then would
    be seats alone, the case setup hits when it publishes the page before the first refresh.
    """
    path = workspace / "out" / month.label / PLAN_FILE_NAME
    if path.exists():
        return _read_plan(path), ""
    if month.first_day > today:
        return None, f"no plan yet: {month.label} hasn't started, so over pace isn't checked"
    needed = list(needed)
    if not needed:
        return None, "no plan yet: no usage is saved, so over pace isn't checked"
    made_as_of = month.previous_month().last_day
    forecast = forecast_after(workspace, settings, made_as_of)
    missing = [product for product in needed if not forecast.history[product]]
    if missing:
        return None, (
            f"no plan yet: no complete month of {' or '.join(missing)} is saved before {made_as_of}, "
            "so over pace isn't checked"
        )
    by_team = {team: total(amounts) for team, amounts in forecast.by_team.items()}
    by_product = {product: sum((amounts[product] for amounts in forecast.by_team.values()), ZERO) for product in PRODUCTS}
    plan = Plan(sum(by_team.values(), ZERO), made_as_of, today, by_product, by_team)
    document = {
        "month": month.label,
        "made_as_of": made_as_of.isoformat(),
        "saved_on": today.isoformat(),
        "total_usd": f"{plan.total:f}",
        "by_team": {team: f"{amount:f}" for team, amount in sorted(by_team.items())},
        "by_product": {product: f"{amount:f}" for product, amount in by_product.items()},
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
    products = document.get("by_product", {})
    teams = document.get("by_team", {})
    if not all(isinstance(part, dict) and all(isinstance(value, str) for value in part.values()) for part in (products, teams)):
        raise InputError(problem)
    try:
        return Plan(Decimal(total_text), date.fromisoformat(made_text), date.fromisoformat(saved_text),
                    {str(product): Decimal(value) for product, value in products.items()},
                    {str(team): Decimal(value) for team, value in teams.items()})
    except (InvalidOperation, ValueError):
        raise InputError(problem) from None


def _cents(amounts: dict[str, Decimal]) -> dict[str, Decimal]:
    return {team: amount.quantize(CENT, ROUND_HALF_UP) for team, amount in amounts.items()}


def describe_forecast_check(workspace: Path, settings: Settings, actual: MonthSpend) -> str | None:
    """For a month that has ended: how far its forecast was from what happened, beside how far
    doing nothing (calling it last month's total) would have been. None without a last month to
    compare with, since then neither has a footing.

    The forecast is the plan saved at the month's start when there is one, else a replay of it
    with today's downloads, which says so because downloads may have been revised since.
    """
    previous = build_month(workspace, actual.month.previous_month(), settings)
    if not previous.coverage:
        return None
    # A month saved only in part reads as a big miss that says nothing about the method.
    partial = [
        f"{product} for {spend.month.label} is saved only through {end}"
        for spend in (actual, previous)
        for product, (_, end) in sorted(spend.coverage.items())
        if end < spend.month.last_day
    ]
    if partial:
        return (
            f"How the forecast did for {actual.month.label}: not scored, because {'; '.join(partial)}. "
            "Save those months whole to score it."
        )
    path = workspace / "out" / actual.month.label / PLAN_FILE_NAME
    if path.exists():
        plan = _read_plan(path)
        forecast_total, source = plan.total, f"the plan saved on {plan.saved_on}"
    else:
        replay = forecast_after(workspace, settings, previous.month.last_day)
        forecast_total = sum((total(amounts) for amounts in replay.by_team.values()), ZERO)
        source = "replayed with today's downloads, as no plan was saved"
    actual_total = sum((row.amount_usd for row in actual.rows), ZERO)
    naive_total = sum((row.amount_usd for row in previous.rows), ZERO)
    if not actual_total:
        return None
    forecast_miss = abs(forecast_total - actual_total) / actual_total
    naive_miss = abs(naive_total - actual_total) / actual_total
    if abs(forecast_total - naive_total) < 1:
        verdict = "The forecast was the same as doing nothing, as it is while only one month of history is saved."
    elif forecast_miss < naive_miss:
        verdict = "The forecast did better than doing nothing."
    else:
        verdict = "Doing nothing did better, so the forecast method needs a look."
    return "\n".join([
        f"How the forecast did for {actual.month.label}:",
        f"  forecast {dollars(forecast_total)} ({source}); actual {dollars(actual_total)}, off by {forecast_miss:.0%}",
        f"  doing nothing, calling it {previous.month.label}'s {dollars(naive_total)}, would have been off by {naive_miss:.0%}",
        f"  {verdict}",
    ])
