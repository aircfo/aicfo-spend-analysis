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
from collections import Counter, defaultdict
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
from aicfo_spend.other_vendors import OtherAiSpend, other_ai_spend
from aicfo_spend.pace import Pace, pace_of
from aicfo_spend.raw_store import RAMP_PEOPLE, saved_files, snapshot_for
from aicfo_spend.recommendations import (
    BUNDLES,
    DECLINED,
    DONE,
    UPGRADE,
    PersonMonth,
    find_recommendations,
    months_read,
    outcomes,
    read_ledger,
)
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
from aicfo_spend.sources.people import PeopleFileRow, read_people_file
from aicfo_spend.sources.ramp import read_people
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
    usage = build_usage(workspace, month)
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
        "pace": _pace_section(pace, *_plan_split(workspace, settings, pace, team_name)),
        "forecast": _forecast_section(forecast, team_name, usage.seat_holders),
        "history": _history_section(workspace, settings, month, previous),
        "alerts": pace.alerts,
        "notes": notes if show_people else [without_emails(note) for note in notes],
        "seat_types": _seat_types(settings, read_people_file(workspace), usage),
        "savings": _savings_section(workspace, settings, month, today, show_people),
        "people_names": _people_names(workspace, month) if show_people else {},
        "usage": _usage_section(usage, team_name, show_people),
        "other_ai_vendors": _other_ai_section(other_ai_spend(workspace, month, settings)),
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


def _other_ai_section(spend: OtherAiSpend | None) -> Json | None:
    # Vendor names name no one, so every view shows them.
    if spend is None:
        return None
    return {
        "through": spend.through.isoformat() if spend.through else None,
        "vendors": [{"vendor": name, "total_cents": cents(amount)} for name, amount in spend.vendors],
    }


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
        # A person's team is the one their own seats and usage go to; an API key can belong to another.
        teams: dict[str, str] = {}
        for row in sorted(rows, key=lambda row: bool(row.api_key)):
            if row.person:
                teams.setdefault(row.person, row.team)
        section["by_person"] = [
            {**entry, "team": teams.get(str(entry["person"]), "")}
            for entry in _table("person", by_person, sorted(by_person, key=lambda person: -total(by_person[person])))
        ]
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


def _plan_split(
    workspace: Path, settings: Settings, pace: Pace, team_name: Callable[[str], str]
) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    """The plan by product and by team, teams named as the page shows them. A plan saved before
    plans kept the product split gets it from the same forecast replayed, which matches unless
    downloads were revised since."""
    if pace.plan is None:
        return {}, {}
    by_product = pace.plan.by_product
    if not by_product:
        replay = forecast_after(workspace, settings, pace.plan.made_as_of)
        by_product = {product: sum((amounts[product] for amounts in replay.by_team.values()), ZERO) for product in PRODUCTS}
    by_team: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for team, amount in pace.plan.by_team.items():
        by_team[team_name(team)] += amount
    return by_product, dict(by_team)


def _pace_section(pace: Pace, plan_by_product: dict[str, Decimal], plan_by_team: dict[str, Decimal]) -> Json:
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
        "by_product": [
            {"product": product, "so_far_cents": cents(pace.so_far_by_product.get(product, ZERO)),
             "on_pace_for_cents": cents(pace.projected_by_product.get(product, ZERO)),
             "plan_cents": cents(plan_by_product[product]) if product in plan_by_product else None}
            for product in PRODUCTS
        ],
        "by_team": [
            {"team": team, "so_far_cents": cents(pace.so_far.get(team, ZERO)),
             "on_pace_for_cents": cents(pace.projected[team]),
             "plan_cents": cents(plan_by_team[team]) if team in plan_by_team else (0 if plan_by_team else None)}
            for team in teams
        ],
        # Teams the plan expected that have no spend this month.
        "planned_only": [{"team": team, "plan_cents": cents(amount)} for team, amount in sorted(plan_by_team.items())
                         if team not in pace.projected],
    }


def _forecast_section(forecast: Forecast, team_name: Callable[[str], str], seat_holders: dict[str, str]) -> Json:
    by_team: Amounts = {}
    # Claude seat holders per team, which the page's scenario uses for each team's extra usage per seat.
    holders: dict[str, int] = defaultdict(int)
    for team in seat_holders.values():
        holders[team_name(team)] += 1
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
        "seat_holders": dict(sorted(holders.items())),
        "known_changes": _known_change_cents(forecast, team_name),
        # Products whose newest month is this month so far, scaled to a whole month, and the day it reaches.
        "run_rate": {product: through.isoformat() for product, through in forecast.run_rate.items()},
    }


def _known_change_cents(forecast: Forecast, team_name: Callable[[str], str]) -> dict[str, Json]:
    """Each team's known changes by product, which the page's scenario adds as they are instead of
    scaling them with the extra usage and API levers."""
    amounts: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for team, change in forecast.known_changes:
        amounts[team_name(team)][change.product] += change.amount_usd
    return {
        team: {f"{product.replace(' ', '_')}_cents": cents(amount) for product, amount in sorted(products.items())}
        for team, products in sorted(amounts.items())
    }


def _shared_names(rows: list[UsageRow], name_of: Callable[[UsageRow], str]) -> set[str]:
    """The products or models safe to name in the team view: those at least 3 people used.
    The rest are left out rather than grouped, since a group of 1 or 2 still points at them, and
    folding a well-used product in to make the group big enough would hide that product."""
    people: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.requests:
            people[name_of(row)].add(row.person)
    return {name for name, persons in people.items() if len(persons) >= MIN_PEOPLE_PER_TEAM}


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
    product_rows = model_rows = usage.rows
    if not show_people:
        products = _shared_names(usage.rows, lambda row: row.product)
        models = _shared_names(usage.rows, lambda row: row.model)
        product_rows = [row for row in usage.rows if row.product in products]
        model_rows = [row for row in usage.rows if row.model in models]
    by_product = usage_totals_by(product_rows, lambda row: row.product)
    by_model = usage_totals_by(model_rows, lambda row: row.model)
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
            for product, totals in sorted(by_product.items(), key=lambda item: -item[1].fresh_tokens)
        ],
        "by_model": [
            {"model": model, **_usage_cells(totals)}
            for model, totals in sorted(by_model.items(), key=lambda item: -item[1].fresh_tokens)
        ],
        # How many products and models the team view leaves out for having fewer than 3 people.
        "left_out": {
            "products": len({row.product for row in usage.rows if row.requests} - set(by_product)),
            "models": len({row.model for row in usage.rows if row.requests} - set(by_model)),
        },
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


def _seat_types(settings: Settings, people_file: list[PeopleFileRow], usage: MonthUsage) -> Json:
    """Each vendor's seat types with their price, how many were bought, held, and used this month:
    the Seats tab offers the types and shows how much of each is used, and the Forecast tab's
    scenario fills empty seats before buying more. Only Claude seats have usage to count."""
    held = Counter((row.vendor, row.seat_type) for row in people_file if row.vendor)
    using = {row.person for row in usage.rows if row.requests} if usage.covers else set()
    active = Counter((row.vendor, row.seat_type) for row in people_file if row.vendor and row.email in using)
    return {
        vendor: [
            {"seat_type": seat_type, "bought": plan.bought, "held": held[(vendor, seat_type)],
             "active": active[(vendor, seat_type)] if vendor == "anthropic" and usage.covers else None,
             "price_cents": cents(plan.price_usd)}
            for seat_type, plan in sorted(plans.items())
        ]
        for vendor, plans in sorted(settings.seats.items())
    }


def _history_section(workspace: Path, settings: Settings, month: Month, previous: MonthSpend) -> list[Json]:
    """The three months before this one, oldest first, for the Forecast tab's chart: each month's
    total by product, and whether every usage product was saved for the whole month."""
    spends = [previous]
    while len(spends) < 3:
        spends.append(build_month(workspace, spends[-1].month.previous_month(), settings))
    history: list[Json] = []
    for spend in reversed(spends):
        if not spend.coverage:
            continue
        by_product = {product: sum((row.amount_usd for row in spend.rows if row.product == product), ZERO) for product in PRODUCTS}
        history.append({
            "month": spend.month.label,
            "total_cents": sum(cents(amount) for amount in by_product.values()),
            **{f"{product.replace(' ', '_')}_cents": cents(amount) for product, amount in by_product.items()},
            "whole": all(end == spend.month.last_day for _, end in spend.coverage.values()),
        })
    return history


def _people_names(workspace: Path, month: Month) -> dict[str, str]:
    """Each person's name for the person view: from Ramp's people list, or people.csv where it names them."""
    snapshot = snapshot_for(saved_files(workspace, RAMP_PEOPLE), month)
    names = {email: person.name for email, person in (read_people(snapshot.path) if snapshot else {}).items() if person.name}
    names.update({row.email: row.name for row in read_people_file(workspace) if row.name})
    return dict(sorted(names.items()))


def _savings_section(workspace: Path, settings: Settings, month: Month, today: date, show_people: bool) -> Json:
    """What the seat rules recommend now, and what done recommendations have saved.

    The rules read the whole months before this one and this month so far. A recommendation
    the ledger already marks done or declined isn't offered again. The team view keeps the
    company-wide ones and says only how many name a person.
    """
    found, notes = find_recommendations(workspace, settings, month.previous_month(), today)
    # The months the rules read, as numbers, so the page can say why in a few words.
    months, _ = months_read(workspace, month.previous_month())

    def history(person: str) -> list[Json]:
        if person:
            return [{"month": read.label, "requests": by_person.get(person, PersonMonth()).requests,
                     "extra_cents": cents(by_person.get(person, PersonMonth()).extra_usd)} for read, by_person in months]
        return [{"month": read.label, "requests": sum(use.requests for use in by_person.values()),
                 "extra_cents": cents(sum((use.extra_usd for use in by_person.values()), ZERO))} for read, by_person in months]

    ledger = read_ledger(workspace)
    decided = {(item.person, item.action) for item in ledger if item.status in (DONE, DECLINED)}
    open_items = [item for item in found if (item.person, item.action) not in decided]
    shown = open_items if show_people else [item for item in open_items if not item.person]
    results = outcomes(workspace, settings, ledger, today)
    return {
        "open": [
            {"person": item.person, "action": item.action, "from_seat": item.from_seat, "to_seat": item.to_seat,
             "monthly_cents": cents(item.monthly_usd) if item.monthly_usd is not None else None,
             "at_most": item.action == UPGRADE, "why": item.why, "months": history(item.person)}
            for item in sorted(shown, key=lambda item: (-(item.monthly_usd or ZERO), item.action != BUNDLES))
        ],
        "about_people": len(open_items) - len(shown),
        "done": [
            {"person": result.recommendation.person if show_people else "", "action": result.recommendation.action,
             "saved_cents": cents(result.saved_usd), "months": result.months, "problem": result.problem}
            for result in results
        ] if show_people else [],
        "saved_cents": sum(cents(result.saved_usd) for result in results),
        "notes": notes,
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
