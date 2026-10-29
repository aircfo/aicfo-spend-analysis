"""The numbers a shared dashboard shows: a month's spend, its pace, next month's forecast and the alerts.

Shared outputs show teams, not people, unless the person view is on. In the team view a team
that fewer than 3 people spent in folds into "Other (small teams)", which itself always holds
3 or more, because a one-person team's total is that person's spend; API keys are left out,
because a key's name often names its owner; and emails in notes are replaced. Amounts are whole cents, so money never
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
from aicfo_spend.bills import Bill, check_bills, read_bills
from aicfo_spend.errors import InputError
from aicfo_spend.forecast import USAGE_PRODUCTS, Forecast, forecast_after, known_change_note
from aicfo_spend.months import Month
from aicfo_spend.pace import Pace, pace_of
from aicfo_spend.report import (
    ZERO,
    Amounts,
    MonthSpend,
    api_by_cost_category,
    build_month,
    change_notes,
    team_order,
    team_spellings,
    total,
    totals_by,
)
from aicfo_spend.settings import Settings
from aicfo_spend.spend_table import PRODUCT_EXTRA_USAGE, PRODUCTS, UNASSIGNED, SpendRow
from aicfo_spend.usage import (
    MonthUsage,
    Totals,
    UsageRow,
    active_seat_holders,
    build_usage,
    top_share,
)
from aicfo_spend.usage import totals_by as usage_totals_by

DASHBOARD_FILE_NAME = "dashboard.json"
OTHER_SMALL_TEAMS = "Other (small teams)"
# A product only a person or two used would point at them, so the team view folds it too.
OTHER_PRODUCTS = "Other products"
OTHER_MODELS = "Other models"
MIN_PEOPLE_PER_TEAM = 3
EMAIL = re.compile(r"[^\s@,;:()]+@[^\s@,;:()]+\.[A-Za-z]+")

Json = dict[str, object]


def build_dashboard(workspace: Path, settings: Settings, month: Month, today: date | None = None) -> Json:
    """Everything the dashboard shows for a month, already rolled up for sharing."""
    today = today or date.today()
    if month.first_day > today:
        raise InputError(f"{month.label} hasn't started yet, so there's nothing to show")
    spend = build_month(workspace, month, settings)
    # The forecast and the pace read last month too, so a bad file there stops the dashboard,
    # with an error naming the file to fix, rather than leaving a gap nobody sees.
    previous = build_month(workspace, month.previous_month(), settings)

    show_people = settings.visibility == "person"
    spelled = team_spellings([spend, previous])
    team_name = spelled if show_people else fold_small_teams([spend, previous], spelled)
    rows = renamed(spend.rows, team_name)
    pace = pace_of(workspace, settings, month, today, team_name)
    forecast = forecast_after(workspace, settings, min(today, month.last_day))
    notes = list(spend.notes)
    change: Json | None = None
    if previous.coverage:
        change = _change_section(spend, previous, rows, renamed(previous.rows, team_name), show_people)
    else:
        notes.append(f"no usage is saved for {previous.month.label}, so there's no change from last month to show")
    # The same gap can surface in the month, its pace and the forecast; say it once.
    # A known change's note is the operator's own words and its team may be one that folds, so
    # the team view says only the folded team and the amount.
    change_notes = [
        known_change_note(team, change) if show_people else known_change_note(team_name(team), change, with_note=False)
        for team, change in forecast.known_changes
    ]
    notes = list(dict.fromkeys([*notes, *pace.notes, *forecast.notes, *change_notes]))

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
        "seat_types": _seat_types(settings),
        "usage": _usage_section(build_usage(workspace, month), team_name, show_people),
        "bills": _bills_section([previous, spend], read_bills(workspace, settings)),
    }


def _bills_section(months: list[MonthSpend], bills: list[Bill]) -> list[Json]:
    """Each month's check against its bills, last month first: early in a month the one just
    closed is the one that can tie. Company-wide lines name no one, so every view shows them."""
    section: list[Json] = []
    for spend in months:
        checks = check_bills(spend, bills)
        if checks:
            section.append({"month": spend.month.label, "lines": [
                {"vendor": check.vendor, "product": check.product, "kind": check.kind,
                 "billed_cents": cents(check.billed), "credit_cents": cents(check.credit),
                 "counted_cents": cents(check.counted),
                 "counted_through": check.counted_through.isoformat() if check.counted_through else None,
                 "ties": check.ties}
                for check in checks
            ]})
    return section


def write_dashboard(workspace: Path, month: Month, dashboard: Json) -> Path:
    path = workspace / "out" / month.label / DASHBOARD_FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written beside the final name, then renamed, so whatever reads it never sees half a file.
    partial = path.with_suffix(".json.partial")
    partial.write_text(json.dumps(dashboard, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, path)
    return path


def fold_small_teams(months: list[MonthSpend], spelled: Callable[[str], str]) -> Callable[[str], str]:
    """Team names that are safe to share, for the months shown, the current one first.

    A team keeps its name only if at least 3 people spent in it in each month it has spend;
    the rest fold into Other. Other has to hold 3 people too, so while it holds 1 or 2 in
    a month, the smallest named team joins it.
    """
    people: dict[str, dict[Month, set[str]]] = defaultdict(dict)
    for spend in months:
        for row in spend.rows:
            if row.team == UNASSIGNED:
                continue
            persons = people[spelled(row.team)].setdefault(spend.month, set())
            # A key with a department tag and no owner adds spend but no person.
            if row.person:
                persons.add(row.person)
    named = {team for team, by_month in people.items() if all(len(p) >= MIN_PEOPLE_PER_TEAM for p in by_month.values())}
    folded = set(people) - named

    def other_is_small() -> bool:
        for spend in months:
            in_other: set[str] = set()
            for team in folded:
                in_other |= people[team].get(spend.month, set())
            if 0 < len(in_other) < MIN_PEOPLE_PER_TEAM:
                return True
        return False

    current = months[0].month
    while named and other_is_small():
        smallest = min(named, key=lambda team: (len(people[team].get(current, set())), team))
        named.remove(smallest)
        folded.add(smallest)

    def team_name(team: str) -> str:
        name = spelled(team)
        # A team seen in neither month, such as one only in the forecast's older history, folds too.
        return name if name == UNASSIGNED or name in named else OTHER_SMALL_TEAMS

    return team_name


def without_emails(text: str) -> str:
    """No note names anyone today; this keeps a future one from putting an email on a shared page."""
    return EMAIL.sub("someone", text)


def renamed(rows: Iterable[SpendRow], team_name: Callable[[str], str]) -> list[SpendRow]:
    return [replace(row, team=team_name(row.team)) for row in rows]


def _spend_section(spend: MonthSpend, rows: list[SpendRow], show_people: bool) -> Json:
    by_team = totals_by(rows, lambda row: row.team)
    by_vendor = totals_by(rows, lambda row: row.vendor)
    unassigned: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in rows:
        if row.team == UNASSIGNED:
            unassigned[row.reason] += row.amount_usd
    section: Json = {
        "through": {product: end.isoformat() for product, (_, end) in spend.coverage.items()},
        "total_cents": _total_cents(by_team),
        "by_team": _table("team", by_team, team_order(by_team)),
        "by_vendor": _table("vendor", by_vendor, sorted(by_vendor, key=lambda vendor: -total(by_vendor[vendor]))),
        "unassigned_by_reason": [
            {"reason": reason, "total_cents": cents(amount)} for reason, amount in sorted(unassigned.items())
        ],
        # What API spend was for. A category names no one, so the team view shows it too.
        "api_by_cost_category": [
            {"category": category, "total_cents": cents(amount)} for category, amount in api_by_cost_category(rows)
        ],
    }
    if show_people:
        by_person = totals_by((row for row in rows if row.person), lambda row: row.person)
        section["by_person"] = _table("person", by_person, sorted(by_person, key=lambda person: -total(by_person[person])))
        # Spend no current key carries is in Unassigned by reason; this table is the keys themselves.
        api_rows = sorted((row for row in rows if row.api_key), key=lambda row: -row.amount_usd)
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
        "so_far_cents": sum(cents(amount) for amount in pace.so_far.values()),
        "on_pace_for_cents": sum(cents(amount) for amount in pace.projected.values()),
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
        "total_cents": _total_cents(by_team),
        "by_team": _table("team", by_team, team_order(by_team)),
        "averaged_over": {product: [month.label for month in forecast.history[product]] for product in USAGE_PRODUCTS},
    }


def _fold_few_people(rows: list[UsageRow], name_of: Callable[[UsageRow], str], other: str) -> Callable[[str], str]:
    """Names safe to share in the team view: a product or model fewer than 3 people used folds into
    `other`, and while `other` holds 1 or 2 people the least used name joins it, as small teams do."""
    people: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.requests:
            people[name_of(row)].add(row.person)
    named = {name for name, persons in people.items() if len(persons) >= MIN_PEOPLE_PER_TEAM}

    def in_other() -> set[str]:
        return set().union(*(persons for name, persons in people.items() if name not in named))

    while named and 0 < len(in_other()) < MIN_PEOPLE_PER_TEAM:
        named.remove(min(named, key=lambda name: (len(people[name]), name)))
    return lambda name: name if name in named else other


def _usage_section(usage: MonthUsage, team_name: Callable[[str], str], show_people: bool) -> Json | None:
    """Who used Claude and how much: by team, product and model, and by person in the person view."""
    if usage.covers is None:
        return None
    active = active_seat_holders(usage)
    holders_by_team: dict[str, list[str]] = defaultdict(list)
    for person, team in usage.seat_holders.items():
        holders_by_team[team_name(team)].append(person)
    by_team = usage_totals_by(usage.rows, lambda row: team_name(row.team))
    teams = sorted({*by_team, *holders_by_team}, key=lambda team: (team == UNASSIGNED, -by_team.get(team, Totals()).fresh_tokens))
    product_name: Callable[[str], str] = str
    model_name: Callable[[str], str] = str
    if not show_people:
        product_name = _fold_few_people(usage.rows, lambda row: row.product, OTHER_PRODUCTS)
        model_name = _fold_few_people(usage.rows, lambda row: row.model, OTHER_MODELS)
    by_product = usage_totals_by(usage.rows, lambda row: product_name(row.product))
    by_model = usage_totals_by(usage.rows, lambda row: model_name(row.model))
    share = top_share(usage)
    section: Json = {
        "covers": {"start": usage.covers[0].isoformat(), "end": usage.covers[1].isoformat()},
        "seat_holders": len(usage.seat_holders),
        "active_seat_holders": len(active),
        "busiest_tenth_share_percent": None if share is None else int((share * 100).quantize(Decimal(1), ROUND_HALF_UP)),
        "by_team": [
            {"team": team, "seat_holders": len(holders_by_team.get(team, [])),
             "active_seat_holders": sum(person in active for person in holders_by_team.get(team, [])),
             **_usage_cells(by_team.get(team, Totals()))}
            for team in teams
        ],
        "by_product": [
            {"product": product, "people": len(totals.people), **_usage_cells(totals)}
            for product, totals in sorted(by_product.items(), key=lambda item: (item[0] == OTHER_PRODUCTS, -item[1].fresh_tokens))
        ],
        "by_model": [
            {"model": model, **_usage_cells(totals)}
            for model, totals in sorted(by_model.items(), key=lambda item: (item[0] == OTHER_MODELS, -item[1].fresh_tokens))
        ],
    }
    if show_people:
        by_person = usage_totals_by(usage.rows, lambda row: row.person)
        # Seat holders who never used Claude are listed too, from the roster.
        team_of = {row.person: row.team for row in usage.rows} | usage.seat_holders
        section["by_person"] = [
            {"person": person, "team": team_name(team_of[person]), "claude_seat_holder": person in usage.seat_holders,
             **_usage_cells(by_person.get(person, Totals()))}
            for person in sorted(team_of, key=lambda person: -by_person.get(person, Totals()).fresh_tokens)
        ]
    return section


def _usage_cells(totals: Totals) -> Json:
    return {"requests": totals.requests, "fresh_tokens": totals.fresh_tokens,
            "cache_read_tokens": totals.cache_read_tokens, "extra_usage_cents": cents(totals.extra_usd)}


def _seat_types(settings: Settings) -> Json:
    """Each vendor's seat types and how many were bought, for the page's seats roster to offer."""
    return {
        vendor: [{"seat_type": seat_type, "bought": plan.bought} for seat_type, plan in sorted(plans.items())]
        for vendor, plans in sorted(settings.seats.items())
    }


def _total_cents(amounts: Amounts) -> int:
    """The sum of the rounded cells, so the rows on the page add up to the total on the page."""
    return sum(cents(amount) for products in amounts.values() for amount in products.values())


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
