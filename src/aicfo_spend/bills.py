"""Check a month's numbers against what the vendors charged, from bills.csv.

A seat is charged for the month, or for the year paid up front, so the tool's seat count times
the price must match the bill. Usage is charged one of two ways. An invoice for the month's
use must match too. Prepaid credits, which is how Claude Team sells extra usage, are bought
ahead in bundles, often for less than the credit they carry ($200 for $250), so a month's
purchases don't match its use: those lines show what was paid and the credit it bought beside
what was used, and tie over time against the remaining balance the vendor shows.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from aicfo_spend.errors import InputError
from aicfo_spend.months import Month
from aicfo_spend.report import ZERO, MonthSpend, dollars
from aicfo_spend.settings import Settings
from aicfo_spend.spend_table import PRODUCT_SEATS, PRODUCTS

BILLS_FILE_NAME = "bills.csv"
BILLS_COLUMNS = ("month", "vendor", "product", "amount_usd", "months", "kind", "credit_usd", "charge_id", "note")
# The vendors the tool reads spend for; a bill for any other matches nothing.
TOOL_VENDORS = ("anthropic", "openai")
KIND_INVOICE = "invoice"
KIND_CREDITS = "credits"
KINDS = (KIND_INVOICE, KIND_CREDITS)
# A bill ties when it's within $1 or 0.5% of what the tool counted, whichever is more: vendors
# round per line and per seat, and a few cents of difference say nothing.
TOLERANCE_USD = Decimal(1)
TOLERANCE_SHARE = Decimal("0.005")
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Bill:
    month: Month
    vendor: str
    product: str
    amount_usd: Decimal
    # How many months the bill pays for, from its month on: 12 for a year's seats paid up front.
    months: int
    kind: str
    note: str
    # For prepaid credits, the credit bought, which a bundle sells for less; else the amount.
    credit_usd: Decimal = ZERO
    # The charge's id in Ramp, so the same charge is never entered twice.
    charge_id: str = ""

    def share_in(self, month: Month) -> Decimal:
        """The part of the bill that pays for the month, spread evenly over the months it covers."""
        covered = self.month
        for _ in range(self.months):
            if covered == month:
                return (self.amount_usd / self.months).quantize(CENT)
            covered = covered.next_month()
        return ZERO


@dataclass(frozen=True)
class BillCheck:
    vendor: str
    product: str
    kind: str
    billed: Decimal
    counted: Decimal
    # For prepaid credits, the credit the money bought; equal to billed otherwise.
    credit: Decimal
    # The last day the counted usage reaches; a month's invoice can't tie before its end.
    counted_through: date | None

    @property
    def difference(self) -> Decimal:
        return self.counted - self.billed

    @property
    def ties(self) -> bool | None:
        """Whether the bill matches, or None when it can't be judged: credits, or a month not yet counted in full."""
        if self.kind == KIND_CREDITS or self.counted_through is not None:
            return None
        return abs(self.difference) <= max(TOLERANCE_USD, abs(self.billed) * TOLERANCE_SHARE)


def read_bills(workspace: Path, settings: Settings) -> list[Bill]:
    path = workspace / BILLS_FILE_NAME
    if not path.exists():
        return []
    vendors = sorted({*TOOL_VENDORS, *settings.seats})
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in ("month", "vendor", "product", "amount_usd") if column not in (reader.fieldnames or [])]
        if missing:
            raise InputError(f"{BILLS_FILE_NAME} is missing the columns {missing}; its columns are {', '.join(BILLS_COLUMNS)}")
        bills: list[Bill] = []
        seen: dict[str, int] = {}
        for line_number, record in enumerate(reader, start=2):
            if not any((value or "").strip() for value in record.values() if isinstance(value, str)):
                continue
            where = f"{BILLS_FILE_NAME} line {line_number}"
            bill = _bill(record, where, vendors)
            if bill.charge_id and bill.charge_id in seen:
                raise InputError(
                    f"{where} repeats charge {bill.charge_id} from line {seen[bill.charge_id]}; each charge goes in once"
                )
            if bill.charge_id:
                seen[bill.charge_id] = line_number
            bills.append(bill)
    return bills


def _bill(record: dict[str, str | None], where: str, vendors: list[str]) -> Bill:
    def cell(column: str) -> str:
        return (record.get(column) or "").strip()

    try:
        month = Month.parse(cell("month"))
    except InputError:
        raise InputError(f"{where}: month must be the month the bill pays for, like 2026-09, not {cell('month')!r}") from None
    product = cell("product").lower()
    if product not in PRODUCTS:
        raise InputError(f"{where}: product must be one of {', '.join(PRODUCTS)}, not {cell('product')!r}")
    amount = _dollars(cell("amount_usd"), f"{where}: amount_usd")
    if amount is None:
        raise InputError(f"{where}: amount_usd must be a dollar amount like 1540.00, not {cell('amount_usd')!r}")
    months_text = cell("months") or "1"
    if not months_text.isdigit() or int(months_text) < 1:
        raise InputError(f"{where}: months must be a whole number of months the bill pays for, like 1 or 12, not {months_text!r}")
    kind = cell("kind").lower() or KIND_INVOICE
    if kind not in KINDS:
        raise InputError(f'{where}: kind must be "{KIND_INVOICE}" or "{KIND_CREDITS}", not {cell("kind")!r}')
    if kind == KIND_CREDITS and product == PRODUCT_SEATS:
        raise InputError(f"{where}: seats are charged by invoice, not bought as credits")
    credit = _dollars(cell("credit_usd"), f"{where}: credit_usd")
    if credit is not None and kind != KIND_CREDITS:
        raise InputError(f"{where}: credit_usd is only for prepaid credits; leave it blank for an invoice")
    vendor = cell("vendor").lower()
    if vendor not in vendors:
        raise InputError(f"{where}: vendor must be one of {', '.join(vendors)}, as the tool names them, not {cell('vendor')!r}")
    return Bill(month, vendor, product, amount, int(months_text), kind, cell("note"),
                amount if credit is None else credit, cell("charge_id"))


def _dollars(text: str, where: str) -> Decimal | None:
    """A dollar amount, with any $ sign and thousands commas; None for a blank cell."""
    if not text:
        return None
    try:
        return Decimal(text.replace("$", "").replace(",", ""))
    except InvalidOperation:
        raise InputError(f"{where} must be a dollar amount like 1540.00, not {text!r}") from None


def check_bills(spend: MonthSpend, bills: list[Bill]) -> list[BillCheck]:
    """One check per vendor, product and kind that has a bill paying for the month."""
    billed: dict[tuple[str, str, str], Decimal] = {}
    credit: dict[tuple[str, str, str], Decimal] = {}
    for bill in bills:
        share = bill.share_in(spend.month)
        if share or bill.month == spend.month:
            key = (bill.vendor, bill.product, bill.kind)
            billed[key] = billed.get(key, ZERO) + share
            credit[key] = credit.get(key, ZERO) + (bill.credit_usd if bill.kind == KIND_CREDITS else share)
    checks: list[BillCheck] = []
    for (vendor, product, kind), amount in sorted(billed.items()):
        counted = sum((row.amount_usd for row in spend.rows if row.vendor == vendor and row.product == product), ZERO)
        through = spend.coverage.get(product)
        short_of_month_end = through is not None and through[1] < spend.month.last_day
        checks.append(BillCheck(vendor, product, kind, amount, counted, credit[(vendor, product, kind)],
                                through[1] if through and short_of_month_end else None))
    return checks


def summarize_bills(month: Month, checks: list[BillCheck]) -> str:
    if not checks:
        return f"Against the bills: none entered for {month.label} in {BILLS_FILE_NAME}"
    lines = [f"Against the bills ({BILLS_FILE_NAME}):"]
    for check in checks:
        name = f"{check.vendor} {check.product}"
        if check.kind == KIND_CREDITS:
            lines.append(f"  {name}: {dollars(check.billed)} paid for {dollars(check.credit)} of credits, {dollars(check.counted)} used")
        elif check.counted_through is not None:
            lines.append(f"  {name}: billed {dollars(check.billed)}; counted {dollars(check.counted)} through {check.counted_through}, so it can't tie yet")
        elif check.ties:
            lines.append(f"  {name}: ties (billed {dollars(check.billed)}, counted {dollars(check.counted)})")
        else:
            direction = "more" if check.difference > 0 else "less"
            lines.append(
                f"  {name}: DOESN'T TIE. Billed {dollars(check.billed)}, counted {dollars(check.counted)}, "
                f"{dollars(abs(check.difference))} {direction} than the bill"
            )
    if any(check.kind == KIND_CREDITS for check in checks):
        lines.append("  Credits tie over time, not each month: the credit bought less what's used should match the balance the vendor shows.")
    if any(check.ties is False and check.product == PRODUCT_SEATS for check in checks):
        lines.append("  A seat line that doesn't tie usually means the seats bought or their prices in settings are out of date.")
    return "\n".join(lines)
