"""Read the claude.ai spend export: each person's extra usage beyond their seat."""

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
