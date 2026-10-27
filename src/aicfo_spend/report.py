"""Build a month's plain table from the saved inputs, and summarize it by team."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
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
from aicfo_spend.sources.people import PeopleFileRow, read_people_file
from aicfo_spend.sources.ramp import read_key_spend, read_people
from aicfo_spend.spend_table import PRODUCT_API, PRODUCT_EXTRA_USAGE, PRODUCTS, UNASSIGNED, SpendRow
from aicfo_spend.vendors.anthropic.spend_export import read_spend_export

ZERO = Decimal(0)
PRODUCT_HEADINGS = {"seats": "Seats", "extra usage": "Extra usage", "api": "API"}


Amounts = dict[str, dict[str, Decimal]]  # name -> product -> dollars


@dataclass(frozen=True)
class MonthSpend:
    month: Month
    rows: list[SpendRow]
    # Gaps and mismatches in the inputs, said plainly; the totals are still computed.
    notes: list[str]
    # For each usage product, the dates its download covered; a product with no download is absent.
    coverage: dict[str, tuple[date, date]] = field(default_factory=dict)


def build_month(workspace: Path, month: Month, settings: Settings) -> MonthSpend:
    notes: list[str] = []
    people_file = read_people_file(workspace)
    directory = month_directory(workspace, month, people_file, notes)

    rows, seat_notes = seat_rows(month, people_file, settings, directory)
    notes.extend(seat_notes)
    coverage: dict[str, tuple[date, date]] = {}
    extra_usage, export = _extra_usage_rows(workspace, month, directory, notes)
    api, keys = _api_rows(workspace, month, directory, notes)
    for product, product_rows, used in ((PRODUCT_EXTRA_USAGE, extra_usage, export), (PRODUCT_API, api, keys)):
        rows.extend(product_rows)
        if used is not None:
            coverage[product] = (used.start, used.end)
    return MonthSpend(month, rows, notes, coverage)


def month_directory(workspace: Path, month: Month, people_file: list[PeopleFileRow], notes: list[str]) -> Directory:
    people_snapshot = snapshot_for(saved_files(workspace, RAMP_PEOPLE), month)
    if people_snapshot is None:
        notes.append("no Ramp people list is saved, so only people.csv places people on teams")
    ramp_people = read_people(people_snapshot.path) if people_snapshot else {}
    return Directory.build(ramp_people, people_file)


def totals_by(rows: Iterable[SpendRow], name_of: Callable[[SpendRow], str]) -> Amounts:
    totals: Amounts = defaultdict(lambda: {product: ZERO for product in PRODUCTS})
    for row in rows:
        totals[name_of(row)][row.product] += row.amount_usd
    return dict(totals)


def team_order(by_team: Amounts) -> list[str]:
    """Biggest teams first; Unassigned always last so it reads as the remainder."""
    teams = sorted((team for team in by_team if team != UNASSIGNED), key=lambda team: -total(by_team[team]))
    return teams + [UNASSIGNED] if UNASSIGNED in by_team else teams


def total(products: dict[str, Decimal]) -> Decimal:
    return sum(products.values(), ZERO)


def summarize(spend: MonthSpend, company_name: str = "") -> str:
    by_team = totals_by(spend.rows, lambda row: row.team)
    by_vendor = totals_by(spend.rows, lambda row: row.vendor)
    by_reason: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in spend.rows:
        if row.team == UNASSIGNED:
            by_reason[row.reason] += row.amount_usd
    vendors = sorted(by_vendor, key=lambda vendor: -total(by_vendor[vendor]))

    title = f"AI spend for {spend.month.label}" + (f" ({company_name})" if company_name else "")
    lines = [title, "", *breakdown("Team", team_order(by_team), by_team, with_total=True)]
    lines += ["", *breakdown("Vendor", vendors, by_vendor, with_total=False)]
    if by_reason:
        lines += ["", "Unassigned, by reason:"]
        lines += [f"  {reason}: {dollars(amount)}" for reason, amount in sorted(by_reason.items())]
    if spend.notes:
        lines += ["", "Notes:"]
        lines += [f"  - {note}" for note in spend.notes]
    return "\n".join(lines)


def _extra_usage_rows(
    workspace: Path, month: Month, directory: Directory, notes: list[str]
) -> tuple[list[SpendRow], SavedFile | None]:
    files = saved_files(workspace, CLAUDE_SPEND_EXPORT)
    export = fullest_in_month(files, month)
    if export is None:
        notes.append(f"no claude.ai spend export is saved for {month.label}, so extra usage shows $0")
        return [], None
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
    return rows, export


def _api_rows(
    workspace: Path, month: Month, directory: Directory, notes: list[str]
) -> tuple[list[SpendRow], SavedFile | None]:
    files = saved_files(workspace, RAMP_API_KEYS)
    saved = fullest_in_month(files, month)
    if saved is None:
        notes.append(f"no Ramp API spend is saved for {month.label}, so API shows $0")
        return [], None
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
    return rows, saved


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
            f"the newest {what} totals {dollars(chosen_total)}, less than an earlier download's "
            f"{dollars(largest)}; check that the latest download is complete"
        )


def describe_change(current: MonthSpend, previous: MonthSpend, show_people: bool) -> str:
    """How this month moved from last month: by team, by model, and by person when allowed."""
    if not previous.coverage:
        return f"No usage is saved for {previous.month.label}, so there's no change from last month to show."
    before, after = previous.month.label, current.month.label
    before_teams = totals_by(previous.rows, lambda row: row.team)
    after_teams = totals_by(current.rows, lambda row: row.team)
    zeros = {product: ZERO for product in PRODUCTS}
    teams = team_order({team: after_teams.get(team, zeros) for team in {*before_teams, *after_teams}})

    table = [["Team", before, after, "Change"]]
    for team in teams:
        old, new = total(before_teams.get(team, zeros)), total(after_teams.get(team, zeros))
        table.append([team, dollars(old), dollars(new), _change(old, new)])
    old_total, new_total = (sum((row.amount_usd for row in spend.rows), ZERO) for spend in (previous, current))
    table.append(["Total", dollars(old_total), dollars(new_total), _change(old_total, new_total)])
    lines = [f"Change from {before}", "", *aligned(table)]

    def extra_usage_model(row: SpendRow) -> str:
        return row.model if row.product == PRODUCT_EXTRA_USAGE else ""

    lines += ["", "By model (extra usage; Ramp reports API spend per key, not per model):"]
    lines += _biggest_moves(previous.rows, current.rows, extra_usage_model)
    if show_people:
        lines += ["", "By person (seats, extra usage, and API keys they own):"]
        lines += _biggest_moves(previous.rows, current.rows, lambda row: row.person)

    notes = []
    for product, (_, end) in current.coverage.items():
        if product not in previous.coverage:
            notes.append(f"{before} has no {product} saved, so the change overstates growth")
        elif end != current.month.last_day:
            notes.append(f"{after}'s {product} only runs through {end}, while {before} is a whole month")
    if notes:
        lines += ["", "Notes:", *(f"  - {note}" for note in notes)]
    return "\n".join(lines)


def _biggest_moves(
    before: list[SpendRow], after: list[SpendRow], name_of: Callable[[SpendRow], str], limit: int = 8
) -> list[str]:
    old: dict[str, Decimal] = defaultdict(lambda: ZERO)
    new: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for rows, amounts in ((before, old), (after, new)):
        for row in rows:
            name = name_of(row)
            if name:
                amounts[name] += row.amount_usd
    names = sorted({*old, *new}, key=lambda name: (-abs(new[name] - old[name]), name))
    lines = [f"  {name}: {dollars(old[name])} to {dollars(new[name])}, {_change(old[name], new[name])}" for name in names[:limit]]
    if len(names) > limit:
        lines.append(f"  ... and {len(names) - limit} more")
    return lines or ["  (none)"]


def _change(old: Decimal, new: Decimal) -> str:
    difference = new - old
    sign = "+" if difference >= 0 else "-"
    amount = f"{sign}{dollars(abs(difference))}"
    if old == 0:
        return amount if new == 0 else f"{amount} (new)"
    return f"{amount} ({sign}{abs(difference) / old * 100:.0f}%)"


def breakdown(heading: str, names: list[str], amounts: Amounts, with_total: bool) -> list[str]:
    table = [[heading, *(PRODUCT_HEADINGS[product] for product in PRODUCTS), "Total"]]
    for name in names:
        table.append([name, *(dollars(amounts[name][product]) for product in PRODUCTS), dollars(total(amounts[name]))])
    if with_total:
        column_totals = [sum((amounts[name][product] for name in names), ZERO) for product in PRODUCTS]
        table.append(["Total", *(dollars(amount) for amount in column_totals), dollars(sum(column_totals, ZERO))])
    return aligned(table)


def dollars(amount: Decimal) -> str:
    return f"${amount:,.2f}"


def aligned(table: list[list[str]]) -> list[str]:
    """Rows of text cells as columns: the first left-aligned, the rest right-aligned."""
    widths = [max(len(row[column]) for row in table) for column in range(len(table[0]))]
    lines = []
    for row in table:
        cells = [row[0].ljust(widths[0])] + [cell.rjust(width) for cell, width in zip(row[1:], widths[1:])]
        lines.append("  ".join(cells))
    return lines
