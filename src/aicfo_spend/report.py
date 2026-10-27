"""Build a month's plain table from the saved inputs, and summarize it by team."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from aicfo_spend.assign import Directory, seat_rows
from aicfo_spend.errors import UserError
from aicfo_spend.months import Month
from aicfo_spend.raw_store import (
    CLAUDE_SPEND_EXPORT,
    RAMP_API_KEYS,
    RAMP_PEOPLE,
    SavedFile,
    fullest_in_month,
    saved_files,
    snapshot_for,
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

    people_snapshot = snapshot_for(saved_files(workspace, RAMP_PEOPLE), month)
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
    by_vendor: dict[str, dict[str, Decimal]] = defaultdict(lambda: {product: ZERO for product in PRODUCTS})
    by_reason: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in spend.rows:
        by_team[row.team][row.product] += row.amount_usd
        by_vendor[row.vendor][row.product] += row.amount_usd
        if row.team == UNASSIGNED:
            by_reason[row.reason] += row.amount_usd

    # Biggest teams first; Unassigned always last so it reads as the remainder.
    teams = sorted((team for team in by_team if team != UNASSIGNED), key=lambda team: -_total(by_team[team]))
    if UNASSIGNED in by_team:
        teams.append(UNASSIGNED)
    vendors = sorted(by_vendor, key=lambda vendor: -_total(by_vendor[vendor]))

    title = f"AI spend for {spend.month.label}" + (f" ({company_name})" if company_name else "")
    lines = [title, "", *_breakdown("Team", teams, by_team, with_total=True)]
    lines += ["", *_breakdown("Vendor", vendors, by_vendor, with_total=False)]
    if by_reason:
        lines += ["", "Unassigned, by reason:"]
        lines += [f"  {reason}: {_dollars(amount)}" for reason, amount in sorted(by_reason.items())]
    if spend.notes:
        lines += ["", "Notes:"]
        lines += [f"  - {note}" for note in spend.notes]
    return "\n".join(lines)


def _extra_usage_rows(workspace: Path, month: Month, directory: Directory, notes: list[str]) -> list[SpendRow]:
    files = saved_files(workspace, CLAUDE_SPEND_EXPORT)
    export = fullest_in_month(files, month)
    if export is None:
        notes.append(f"no claude.ai spend export is saved for {month.label}, so extra usage shows $0")
        return []
    if export.end != month.last_day:
        notes.append(f"the claude.ai spend export covers {export.start} to {export.end}, not the whole month")

    def export_total(saved: SavedFile) -> Decimal:
        return sum((usage.amount_usd for usage in read_spend_export(saved.path)), ZERO)

    _note_if_shrunk("claude.ai spend export", export, files, month, export_total, notes)
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
    files = saved_files(workspace, RAMP_API_KEYS)
    saved = fullest_in_month(files, month)
    if saved is None:
        notes.append(f"no Ramp API spend is saved for {month.label}, so API shows $0")
        return []
    if saved.end != month.last_day:
        notes.append(f"Ramp API spend covers {saved.start} to {saved.end}, not the whole month")

    def keys_total(other: SavedFile) -> Decimal:
        return sum((key.amount_usd for key in read_key_spend(other.path)), ZERO)

    _note_if_shrunk("Ramp API spend", saved, files, month, keys_total, notes)
    rows: list[SpendRow] = []
    for key in read_key_spend(saved.path):
        placement = directory.place_key(key.department_tag, key.owner_email)
        rows.append(
            SpendRow(
                start_date=saved.start,
                end_date=saved.end,
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


def _note_if_shrunk(
    what: str,
    chosen: SavedFile,
    files: list[SavedFile],
    month: Month,
    total_of: Callable[[SavedFile], Decimal],
    notes: list[str],
) -> None:
    """Month-to-date totals only grow, so a newer download showing less was probably cut short."""
    chosen_total = total_of(chosen)
    largest = ZERO
    for saved in files:
        if Month.of(saved.start) != month or saved.path == chosen.path:
            continue
        # An older download isn't used for the totals, so one that can't be read only earns a note.
        try:
            largest = max(largest, total_of(saved))
        except UserError:
            notes.append(f"couldn't read the older download {saved.path.name}, so it wasn't compared")
    if largest > chosen_total:
        notes.append(
            f"the newest {what} totals {_dollars(chosen_total)}, less than an earlier download's "
            f"{_dollars(largest)}; check that the latest download is complete"
        )


def _breakdown(
    heading: str, names: list[str], amounts: dict[str, dict[str, Decimal]], with_total: bool
) -> list[str]:
    table = [[heading, *(PRODUCT_HEADINGS[product] for product in PRODUCTS), "Total"]]
    for name in names:
        table.append([name, *(_dollars(amounts[name][product]) for product in PRODUCTS), _dollars(_total(amounts[name]))])
    if with_total:
        column_totals = [sum((amounts[name][product] for name in names), ZERO) for product in PRODUCTS]
        table.append(["Total", *(_dollars(amount) for amount in column_totals), _dollars(sum(column_totals, ZERO))])
    return _aligned(table)


def _total(products: dict[str, Decimal]) -> Decimal:
    return sum(products.values(), ZERO)


def _dollars(amount: Decimal) -> str:
    return f"${amount:,.2f}"


def _aligned(table: list[list[str]]) -> list[str]:
    widths = [max(len(row[column]) for row in table) for column in range(len(table[0]))]
    lines = []
    for row in table:
        cells = [row[0].ljust(widths[0])] + [cell.rjust(width) for cell, width in zip(row[1:], widths[1:])]
        lines.append("  ".join(cells))
    return lines
