"""Build a month's plain table from the saved inputs, and summarize it by team."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from aicfo_spend.assign import Directory, seat_rows
from aicfo_spend.months import Month
from aicfo_spend.raw_store import (
    CLAUDE_SPEND_EXPORT,
    RAMP_API_KEYS,
    RAMP_PEOPLE,
    fullest_in_month,
    newest,
    newest_per_day,
    saved_files,
)
from aicfo_spend.settings import Settings
from aicfo_spend.sources.people import read_people_file
from aicfo_spend.sources.ramp import read_key_spend, read_people
from aicfo_spend.spend_table import PRODUCT_API, PRODUCT_EXTRA_USAGE, PRODUCTS, UNASSIGNED, SpendRow
from aicfo_spend.vendors.anthropic.spend_export import read_spend_export

ZERO = Decimal(0)
PRODUCT_HEADINGS = {"seats": "Seats", "extra usage": "Extra usage", "api": "API"}


@dataclass(frozen=True)
class MonthSpend:
    month: Month
    rows: list[SpendRow]
    # Gaps and mismatches in the inputs, said plainly; the totals are still computed.
    notes: list[str]


def build_month(workspace: Path, month: Month, settings: Settings) -> MonthSpend:
    notes: list[str] = []

    people_snapshot = newest(saved_files(workspace, RAMP_PEOPLE))
    if people_snapshot is None:
        notes.append("no Ramp people list is saved, so only people.csv places people on teams")
    ramp_people = read_people(people_snapshot.path) if people_snapshot else {}
    people_file = read_people_file(workspace)
    directory = Directory.build(ramp_people, people_file)

    rows, seat_notes = seat_rows(month, people_file, settings, directory)
    notes.extend(seat_notes)
    rows.extend(_extra_usage_rows(workspace, month, directory, notes))
    rows.extend(_api_rows(workspace, month, directory, notes))
    return MonthSpend(month, rows, notes)


def summarize(spend: MonthSpend, company_name: str = "") -> str:
    by_team: dict[str, dict[str, Decimal]] = defaultdict(lambda: {product: ZERO for product in PRODUCTS})
    by_reason: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in spend.rows:
        by_team[row.team][row.product] += row.amount_usd
        if row.team == UNASSIGNED:
            by_reason[row.reason] += row.amount_usd

    def total(products: dict[str, Decimal]) -> Decimal:
        return sum(products.values(), ZERO)

    # Biggest teams first; Unassigned always last so it reads as the remainder.
    teams = sorted((team for team in by_team if team != UNASSIGNED), key=lambda team: -total(by_team[team]))
    if UNASSIGNED in by_team:
        teams.append(UNASSIGNED)

    title = f"AI spend for {spend.month.label}" + (f" ({company_name})" if company_name else "")
    headings = ["Team", *(PRODUCT_HEADINGS[product] for product in PRODUCTS), "Total"]
    table = [headings]
    for team in teams:
        table.append([team, *(_dollars(by_team[team][product]) for product in PRODUCTS), _dollars(total(by_team[team]))])
    column_totals = [sum((by_team[team][product] for team in teams), ZERO) for product in PRODUCTS]
    table.append(["Total", *(_dollars(amount) for amount in column_totals), _dollars(sum(column_totals, ZERO))])

    lines = [title, "", *_aligned(table)]
    if by_reason:
        lines += ["", "Unassigned, by reason:"]
        lines += [f"  {reason}: {_dollars(amount)}" for reason, amount in sorted(by_reason.items())]
    if spend.notes:
        lines += ["", "Notes:"]
        lines += [f"  - {note}" for note in spend.notes]
    return "\n".join(lines)


def _extra_usage_rows(workspace: Path, month: Month, directory: Directory, notes: list[str]) -> list[SpendRow]:
    export = fullest_in_month(saved_files(workspace, CLAUDE_SPEND_EXPORT), month)
    if export is None:
        notes.append(f"no claude.ai spend export is saved for {month.label}, so extra usage shows $0")
        return []
    if export.start != month.first_day or export.end != month.last_day:
        notes.append(f"the claude.ai spend export covers {export.start} to {export.end}, not the whole month")
    rows: list[SpendRow] = []
    for usage in read_spend_export(export.path):
        placement = directory.place_person(usage.email)
        rows.append(
            SpendRow(
                start_date=export.start,
                end_date=export.end,
                vendor="anthropic",
                product=PRODUCT_EXTRA_USAGE,
                amount_usd=usage.amount_usd,
                source_file=export.path.relative_to(workspace).as_posix(),
                person=usage.email,
                team=placement.team,
                reason=placement.reason,
                model=usage.model,
            )
        )
    return rows


def _api_rows(workspace: Path, month: Month, directory: Directory, notes: list[str]) -> list[SpendRow]:
    daily = newest_per_day(saved_files(workspace, RAMP_API_KEYS), month)
    if not daily:
        notes.append(f"no Ramp API spend is saved for {month.label}, so API shows $0")
        return []
    last_day = max(daily)
    missing = [day for day in month.days() if day <= last_day and day not in daily]
    if missing:
        listed = ", ".join(day.isoformat() for day in missing[:5]) + (" and more" if len(missing) > 5 else "")
        notes.append(f"Ramp API spend is missing for {len(missing)} day(s): {listed}")
    if last_day < month.last_day:
        notes.append(f"Ramp API spend runs through {last_day}")

    rows: list[SpendRow] = []
    for day, saved in daily.items():
        for key in read_key_spend(saved.path, day):
            placement = directory.place_key(key.department_tag, key.owner_email)
            rows.append(
                SpendRow(
                    start_date=day,
                    end_date=day,
                    vendor=key.vendor,
                    product=PRODUCT_API,
                    amount_usd=key.amount_usd,
                    source_file=saved.path.relative_to(workspace).as_posix(),
                    # For API rows, person is the key's owner.
                    person=key.owner_email,
                    team=placement.team,
                    reason=placement.reason,
                    api_key=key.key_name,
                )
            )
    return rows


def _dollars(amount: Decimal) -> str:
    return f"${amount:,.2f}"


def _aligned(table: list[list[str]]) -> list[str]:
    widths = [max(len(row[column]) for row in table) for column in range(len(table[0]))]
    lines = []
    for row in table:
        cells = [row[0].ljust(widths[0])] + [cell.rjust(width) for cell, width in zip(row[1:], widths[1:])]
        lines.append("  ".join(cells))
    return lines
