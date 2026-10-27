"""Read the claude.ai spend export: each person's extra usage beyond their seat."""

from __future__ import annotations

import csv
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
    """Net spend per person and model, as v0.3.0 counts it. Rows that spent nothing are left out."""
    usage: list[ExtraUsage] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for line_number, record in enumerate(csv.DictReader(handle), start=2):
            raw_amount = (record.get("total_net_spend_usd") or "").strip() or "0"
            try:
                amount = Decimal(raw_amount)
            except InvalidOperation:
                amount = Decimal("NaN")
            if not amount.is_finite():
                raise InputError(f"{path.name} line {line_number} has a spend that isn't a number: {raw_amount!r}")
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
