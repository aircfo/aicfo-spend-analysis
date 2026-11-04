"""Who uses AI and how much: requests and tokens per person, product and model, beside spend.

The claude.ai spend export lists everyone who used Claude in its days, $0 of extra usage
included, with their requests and tokens, so it measures use within seats as well as beyond
them. Usage isn't money, so it stays out of the spend table and has a table of its own.

Most prompt tokens are usually cache reads, which are cheap and
say little about how much work was done. So use is measured in requests and in fresh tokens:
uncached input, cache writes and output. Cache reads are kept, but apart.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from aicfo_spend.months import Month
from aicfo_spend.raw_store import CLAUDE_SPEND_EXPORT, fullest_in_month, saved_files
from aicfo_spend.report import month_directory
from aicfo_spend.sources.people import read_people_file
from aicfo_spend.vendors.anthropic.spend_export import read_export_usage

USAGE_FILE_NAME = "usage.csv"
# The share of fresh tokens the busiest tenth of people use; below 10 people a tenth is no one.
TOP_FRACTION = Decimal("0.1")
MIN_PEOPLE_FOR_TOP_SHARE = 10
COLUMNS = ("person", "team", "product", "model", "requests", "fresh_tokens", "cache_read_tokens", "extra_usd")


@dataclass(frozen=True)
class UsageRow:
    person: str
    team: str
    product: str
    model: str
    requests: int
    fresh_tokens: int
    cache_read_tokens: int
    extra_usd: Decimal


@dataclass
class Totals:
    requests: int = 0
    fresh_tokens: int = 0
    cache_read_tokens: int = 0
    extra_usd: Decimal = Decimal(0)
    people: set[str] = field(default_factory=set)

    def add(self, row: UsageRow) -> None:
        self.requests += row.requests
        self.fresh_tokens += row.fresh_tokens
        self.cache_read_tokens += row.cache_read_tokens
        self.extra_usd += row.extra_usd
        if row.requests:
            self.people.add(row.person)


@dataclass(frozen=True)
class MonthUsage:
    month: Month
    rows: list[UsageRow]
    # Each Claude seat holder's team, so people who hold a seat and never used it still count.
    seat_holders: dict[str, str]
    # The days the export covered; None when no export with usage columns is saved.
    covers: tuple[date, date] | None
    notes: list[str]


def build_usage(workspace: Path, month: Month) -> MonthUsage:
    notes: list[str] = []
    people_file = read_people_file(workspace)
    directory = month_directory(workspace, month, people_file, [])
    seat_holders = {
        row.email: directory.place_person(row.email).team for row in people_file if row.vendor == "anthropic"
    }
    export = fullest_in_month(saved_files(workspace, CLAUDE_SPEND_EXPORT, month), month)
    if export is None:
        notes.append(f"no claude.ai spend export is saved for {month.label}, so there's no usage to show")
        return MonthUsage(month, [], seat_holders, None, notes)
    found, has_usage = read_export_usage(export.path)
    if not has_usage:
        notes.append(f"the claude.ai spend export for {month.label} is an older kind with no usage columns")
        return MonthUsage(month, [], seat_holders, None, notes)
    rows = [
        UsageRow(
            person=usage.email,
            team=directory.place_person(usage.email).team,
            product=usage.product,
            model=usage.model,
            requests=usage.requests,
            fresh_tokens=usage.uncached_input_tokens + usage.cache_write_tokens + usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            extra_usd=usage.extra_usd,
        )
        for usage in found
    ]
    return MonthUsage(month, rows, seat_holders, (export.start, export.end), notes)


def totals_by(rows: Iterable[UsageRow], name_of: Callable[[UsageRow], str]) -> dict[str, Totals]:
    totals: dict[str, Totals] = defaultdict(Totals)
    for row in rows:
        totals[name_of(row)].add(row)
    return dict(totals)


def active_seat_holders(usage: MonthUsage) -> set[str]:
    active = {row.person for row in usage.rows if row.requests}
    return {person for person in usage.seat_holders if person in active}


def top_share(usage: MonthUsage) -> Decimal | None:
    """The share of fresh tokens the busiest tenth of people used, or None for too few people."""
    by_person = sorted((totals.fresh_tokens for totals in totals_by(usage.rows, lambda row: row.person).values()), reverse=True)
    if len(by_person) < MIN_PEOPLE_FOR_TOP_SHARE or not sum(by_person):
        return None
    top = int(len(by_person) * TOP_FRACTION)
    return Decimal(sum(by_person[:top])) / Decimal(sum(by_person))


def people(count: int) -> str:
    return f"{count} {'person' if count == 1 else 'people'}"


def write_usage_csv(usage: MonthUsage, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for row in usage.rows:
            writer.writerow([row.person, row.team, row.product, row.model, row.requests, row.fresh_tokens,
                             row.cache_read_tokens, f"{row.extra_usd:f}"])


def summarize_usage(usage: MonthUsage) -> str:
    if usage.covers is None:
        return "Claude usage: " + "; ".join(usage.notes)
    start, end = usage.covers
    lines = [f"Claude usage, {start} to {end} (requests, and fresh tokens: uncached input, cache writes and output)"]
    holders = len(usage.seat_holders)
    active = len(active_seat_holders(usage))
    share = top_share(usage)
    lines.append(
        f"  {active} of {holders} Claude seat holders used Claude"
        + (f"; the busiest tenth of people used {share:.0%} of fresh tokens" if share is not None else "")
    )
    lines += ["", "  By product:"]
    by_product = totals_by(usage.rows, lambda row: row.product)
    fresh = sum(totals.fresh_tokens for totals in by_product.values()) or 1
    for product, totals in sorted(by_product.items(), key=lambda item: -item[1].fresh_tokens):
        lines.append(
            f"    {product}: {totals.fresh_tokens / fresh:.0%} of fresh tokens, {people(len(totals.people))}, "
            f"${totals.extra_usd:,.2f} extra usage"
        )
    lines += ["", "  By model:"]
    for model, totals in sorted(totals_by(usage.rows, lambda row: row.model).items(), key=lambda item: -item[1].fresh_tokens):
        lines.append(f"    {model}: {totals.fresh_tokens / fresh:.0%} of fresh tokens, ${totals.extra_usd:,.2f} extra usage")
    return "\n".join(lines)
