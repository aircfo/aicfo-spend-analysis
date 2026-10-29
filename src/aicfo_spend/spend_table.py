"""The plain table every input becomes: one row per charge, written as spend.csv."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

PRODUCT_SEATS = "seats"
PRODUCT_EXTRA_USAGE = "extra usage"
PRODUCT_API = "api"
PRODUCTS = (PRODUCT_SEATS, PRODUCT_EXTRA_USAGE, PRODUCT_API)

UNASSIGNED = "Unassigned"

COLUMNS = (
    "start_date",
    "end_date",
    "vendor",
    "product",
    "person",
    "team",
    "reason",
    "api_key",
    "model",
    "amount_usd",
    "source_file",
    "cost_category",
)


@dataclass(frozen=True)
class SpendRow:
    # Daily data has start_date == end_date; a whole-month export spans the month.
    start_date: date
    end_date: date
    vendor: str
    product: str
    amount_usd: Decimal
    source_file: str
    person: str = ""
    team: str = ""
    # Set only when team is Unassigned, saying why.
    reason: str = ""
    api_key: str = ""
    model: str = ""
    # For API rows: what the key's spend is for, from its Ramp tag or the settings.
    cost_category: str = ""


def write_spend_csv(rows: list[SpendRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for row in rows:
            writer.writerow(
                [
                    row.start_date.isoformat(),
                    row.end_date.isoformat(),
                    row.vendor,
                    row.product,
                    row.person,
                    row.team,
                    row.reason,
                    row.api_key,
                    row.model,
                    # Exact, not rounded to cents, so the file adds up to the report's totals.
                    f"{row.amount_usd:f}",
                    row.source_file,
                    row.cost_category,
                ]
            )
