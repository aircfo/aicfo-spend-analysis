"""Read the claude.ai spend export: each person's extra usage beyond their seat, and their usage."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from aicfo_spend.errors import InputError


@dataclass(frozen=True)
class ExtraUsage:
    email: str
    model: str
    amount_usd: Decimal


def read_spend_export(path: Path) -> list[ExtraUsage]:
    return parse_spend_export(path.read_text(encoding="utf-8-sig"), path.name)


def parse_spend_export(text: str, label: str) -> list[ExtraUsage]:
    """Net spend per person and model, as v0.3.0 counts it. Rows that spent nothing are left out."""
    usage: list[ExtraUsage] = []
    for line_number, record in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        raw_amount = (record.get("total_net_spend_usd") or "").strip() or "0"
        try:
            amount = Decimal(raw_amount)
        except InvalidOperation:
            amount = Decimal("NaN")
        if not amount.is_finite():
            raise InputError(f"{label} line {line_number} has a spend that isn't a number: {raw_amount!r}")
        if amount == 0:
            continue
        usage.append(
            ExtraUsage(
                email=(record.get("user_email") or "").strip().lower(),
                model=(record.get("model") or "").strip(),
                amount_usd=amount,
            )
        )
    return usage


# Older exports had only the spend columns; these came later.
USAGE_COLUMNS = ("total_requests", "total_uncached_input_tokens", "total_cache_read_tokens", "total_completion_tokens")


@dataclass(frozen=True)
class ExportUsage:
    """One person's use of one product and model over the export's days, $0 extra usage included."""

    email: str
    product: str
    model: str
    requests: int
    uncached_input_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    output_tokens: int
    extra_usd: Decimal


def read_export_usage(path: Path) -> tuple[list[ExportUsage], bool]:
    """Every row of the export, and whether it has the usage columns at all."""
    return parse_export_usage(path.read_text(encoding="utf-8-sig"), path.name)


def parse_export_usage(text: str, label: str) -> tuple[list[ExportUsage], bool]:
    reader = csv.DictReader(io.StringIO(text))
    has_usage = all(column in (reader.fieldnames or []) for column in USAGE_COLUMNS)
    rows: list[ExportUsage] = []
    for line_number, record in enumerate(reader, start=2):
        where = f"{label} line {line_number}"
        rows.append(
            ExportUsage(
                email=(record.get("user_email") or "").strip().lower(),
                product=(record.get("product") or "").strip(),
                model=(record.get("model") or "").strip(),
                requests=_count(record, "total_requests", where),
                uncached_input_tokens=_count(record, "total_uncached_input_tokens", where),
                cache_read_tokens=_count(record, "total_cache_read_tokens", where),
                cache_write_tokens=_count(record, "total_cache_write_5m_tokens", where)
                + _count(record, "total_cache_write_1h_tokens", where),
                output_tokens=_count(record, "total_completion_tokens", where),
                extra_usd=_spend(record, where),
            )
        )
    return rows, has_usage


def _count(record: dict[str, str], column: str, where: str) -> int:
    raw = (record.get(column) or "").strip() or "0"
    try:
        return int(Decimal(raw))
    except InvalidOperation:
        raise InputError(f"{where} has a {column} that isn't a number: {raw!r}") from None


def _spend(record: dict[str, str], where: str) -> Decimal:
    raw = (record.get("total_net_spend_usd") or "").strip() or "0"
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        amount = Decimal("NaN")
    if not amount.is_finite():
        raise InputError(f"{where} has a spend that isn't a number: {raw!r}")
    return amount
