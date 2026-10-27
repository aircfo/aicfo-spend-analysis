"""The numbers a shared dashboard shows: a month's spend, its pace, next month's forecast and the alerts.

Shared outputs show teams, not people, unless the person view is on. In the team view a team
that fewer than 3 people spent in folds into "Other (small teams)", because a one-person
team's total is that person's spend; API keys are left out, because a key's name often
names its owner; and emails in notes are replaced. Amounts are whole cents, so money never
passes through a float on its way to a chart.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from aicfo_spend import __version__
from aicfo_spend.errors import InputError, UserError
from aicfo_spend.forecast import USAGE_PRODUCTS, Forecast, forecast_after
from aicfo_spend.months import Month
from aicfo_spend.pace import Pace, pace_of
from aicfo_spend.report import ZERO, Amounts, MonthSpend, build_month, change_notes, team_order, total, totals_by
from aicfo_spend.settings import Settings
from aicfo_spend.spend_table import PRODUCT_API, PRODUCT_EXTRA_USAGE, PRODUCTS, UNASSIGNED, SpendRow

DASHBOARD_FILE_NAME = "dashboard.json"
OTHER_SMALL_TEAMS = "Other (small teams)"
MIN_PEOPLE_PER_TEAM = 3
EMAIL = re.compile(r"[^\s@,;:()]+@[^\s@,;:()]+\.[A-Za-z]+")

Json = dict[str, object]


def build_dashboard(workspace: Path, settings: Settings, month: Month, today: date | None = None) -> Json:
    """Everything the dashboard shows for a month, already rolled up for sharing."""
    today = today or date.today()
    if month.first_day > today:
        raise InputError(f"{month.label} hasn't started yet, so there's nothing to show")
    spend = build_month(workspace, month, settings)
    notes = list(spend.notes)
    previous: MonthSpend | None = None
    try:
        previous = build_month(workspace, month.previous_month(), settings)
    except UserError as error:
        notes.append(f"change from last month isn't available: {error}")

    show_people = settings.visibility == "person"
    shown_months = [spend] if previous is None else [spend, previous]
    team_name = _unchanged if show_people else fold_small_teams(shown_months)
    rows = renamed(spend.rows, team_name)
    pace = pace_of(workspace, settings, month, today, team_name)
    forecast = forecast_after(workspace, settings, min(today, month.last_day))
    change: Json | None = None
    if previous is not None and previous.coverage:
        change = _change_section(spend, previous, rows, renamed(previous.rows, team_name), show_people)
    elif previous is not None:
        notes.append(f"no usage is saved for {previous.month.label}, so there's no change from last month to show")
    # The same gap can surface in the month, its pace and the forecast; say it once.
    notes = list(dict.fromkeys([*notes, *pace.notes, *forecast.notes]))

    return {
        "about": {
            "tool": f"aicfo-spend {__version__}",
            "company": settings.company_name,
            "month": month.label,
            "made_on": today.isoformat(),
            "view": "person" if show_people else "team",
            "amounts": "whole US cents",
        },
        "spend": _spend_section(spend, rows, show_people),
        "change_from_last_month": change,
        "pace": _pace_section(pace),
        "forecast": _forecast_section(forecast, team_name),
        "alerts": pace.alerts,
        "notes": notes if show_people else [without_emails(note) for note in notes],
    }


def write_dashboard(workspace: Path, month: Month, dashboard: Json) -> Path:
    path = workspace / "out" / month.label / DASHBOARD_FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written beside the final name, then renamed, so whatever reads it never sees half a file.
    partial = path.with_suffix(".json.partial")
    partial.write_text(json.dumps(dashboard, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, path)
    return path


def fold_small_teams(months: Iterable[MonthSpend]) -> Callable[[str], str]:
    """A team keeps its name only if at least 3 people spent in it in every month it has spend."""
    people: dict[str, dict[Month, set[str]]] = defaultdict(lambda: defaultdict(set))
    for spend in months:
        for row in spend.rows:
            people[row.team.casefold()][spend.month].add(row.person)
    named = {
        team
        for team, by_month in people.items()
        if all(len(persons - {""}) >= MIN_PEOPLE_PER_TEAM for persons in by_month.values())
    }

    def team_name(team: str) -> str:
        # A team seen in neither month, such as one only in the forecast's older history, folds too.
        return team if team == UNASSIGNED or team.casefold() in named else OTHER_SMALL_TEAMS

    return team_name


def without_emails(text: str) -> str:
    """No note names anyone today; this keeps a future one from putting an email on a shared page."""
    return EMAIL.sub("someone", text)


def renamed(rows: Iterable[SpendRow], team_name: Callable[[str], str]) -> list[SpendRow]:
    return [replace(row, team=team_name(row.team)) for row in rows]


def _unchanged(team: str) -> str:
    return team


def _spend_section(spend: MonthSpend, rows: list[SpendRow], show_people: bool) -> Json:
    by_team = totals_by(rows, lambda row: row.team)
    by_vendor = totals_by(rows, lambda row: row.vendor)
    unassigned: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in rows:
        if row.team == UNASSIGNED:
            unassigned[row.reason] += row.amount_usd
    section: Json = {
        "through": {product: end.isoformat() for product, (_, end) in spend.coverage.items()},
        "total_cents": cents(sum((row.amount_usd for row in rows), ZERO)),
        "by_team": _table("team", by_team, team_order(by_team)),
        "by_vendor": _table("vendor", by_vendor, sorted(by_vendor, key=lambda vendor: -total(by_vendor[vendor]))),
        "unassigned_by_reason": [
            {"reason": reason, "total_cents": cents(amount)} for reason, amount in sorted(unassigned.items())
        ],
    }
    if show_people:
        by_person = totals_by((row for row in rows if row.person), lambda row: row.person)
        section["by_person"] = _table("person", by_person, sorted(by_person, key=lambda person: -total(by_person[person])))
        api_rows = sorted((row for row in rows if row.product == PRODUCT_API), key=lambda row: -row.amount_usd)
        section["api_keys"] = [
            {"api_key": row.api_key, "vendor": row.vendor, "owner": row.person, "team": row.team,
             "total_cents": cents(row.amount_usd)}
            for row in api_rows
        ]
    return section


def _change_section(
    spend: MonthSpend, previous: MonthSpend, rows: list[SpendRow], previous_rows: list[SpendRow], show_people: bool
) -> Json:
    section: Json = {
        "previous_month": previous.month.label,
        "by_team": _moves("team", previous_rows, rows, lambda row: row.team),
        "by_model": _moves("model", previous_rows, rows, lambda row: row.model if row.product == PRODUCT_EXTRA_USAGE else ""),
        "notes": change_notes(spend, previous),
    }
    if show_people:
        section["by_person"] = _moves("person", previous_rows, rows, lambda row: row.person)
    return section


def _pace_section(pace: Pace) -> Json:
    teams = sorted((team for team in pace.projected if team != UNASSIGNED), key=lambda team: -pace.projected[team])
    if UNASSIGNED in pace.projected:
        teams.append(UNASSIGNED)
    plan: Json | None = None
    if pace.plan is not None:
        plan = {
            "total_cents": cents(pace.plan.total),
            "made_as_of": pace.plan.made_as_of.isoformat(),
            "saved_on": pace.plan.saved_on.isoformat(),
        }
    return {
        "through": min(pace.through.values()).isoformat() if pace.through else None,
        "so_far_cents": cents(sum(pace.so_far.values(), ZERO)),
        "on_pace_for_cents": cents(sum(pace.projected.values(), ZERO)),
        "plan": plan,
        "by_team": [
            {"team": team, "so_far_cents": cents(pace.so_far.get(team, ZERO)),
             "on_pace_for_cents": cents(pace.projected[team])}
            for team in teams
        ],
    }


def _forecast_section(forecast: Forecast, team_name: Callable[[str], str]) -> Json:
    by_team: Amounts = {}
    for team, amounts in forecast.by_team.items():
        merged = by_team.setdefault(team_name(team), {product: ZERO for product in PRODUCTS})
        for product, amount in amounts.items():
            merged[product] += amount
    return {
        "month": forecast.month.label,
        "as_of": forecast.as_of.isoformat(),
        "total_cents": cents(sum((total(amounts) for amounts in by_team.values()), ZERO)),
        "by_team": _table("team", by_team, team_order(by_team)),
        "averaged_over": {product: [month.label for month in forecast.history[product]] for product in USAGE_PRODUCTS},
    }


def _table(label: str, amounts: Amounts, names: list[str]) -> list[Json]:
    table: list[Json] = []
    for name in names:
        cells = {f"{product.replace(' ', '_')}_cents": cents(amounts[name][product]) for product in PRODUCTS}
        table.append({label: name, **cells, "total_cents": sum(cells.values())})
    return table


def _moves(
    label: str, before: list[SpendRow], after: list[SpendRow], name_of: Callable[[SpendRow], str]
) -> list[Json]:
    """Each name's total in both months, the biggest move first; rows with no name are skipped."""
    old: dict[str, Decimal] = defaultdict(lambda: ZERO)
    new: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for rows, amounts in ((before, old), (after, new)):
        for row in rows:
            name = name_of(row)
            if name:
                amounts[name] += row.amount_usd
    names = sorted({*old, *new}, key=lambda name: (-abs(new[name] - old[name]), name))
    return [{label: name, "previous_cents": cents(old[name]), "current_cents": cents(new[name])} for name in names]


def cents(amount: Decimal) -> int:
    return int((amount * 100).quantize(Decimal(1), ROUND_HALF_UP))
