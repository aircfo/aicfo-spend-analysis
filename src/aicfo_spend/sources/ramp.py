"""Read the Ramp answers the skill saves: per-key API spend, each vendor's total, and the people list."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from aicfo_spend.errors import InputError

DEPARTMENT_TAG = "department"


@dataclass(frozen=True)
class RampPerson:
    email: str
    name: str
    team: str
    active: bool


@dataclass(frozen=True)
class KeySpend:
    vendor: str
    key_name: str
    owner_email: str
    department_tag: str
    amount_usd: Decimal


def read_people(path: Path) -> dict[str, RampPerson]:
    return parse_people(load_json(path), path.name)


def read_key_spend(path: Path) -> list[KeySpend]:
    return parse_key_spend(load_json(path), path.name)


def read_provider_totals(path: Path) -> dict[str, Decimal]:
    return parse_provider_totals(load_json(path), path.name)


def load_json(path: Path) -> object:
    try:
        # Dollar amounts stay exact: 12.5 is read as Decimal("12.5"), never a float.
        loaded: object = json.loads(path.read_text(encoding="utf-8-sig"), parse_float=Decimal)
    except json.JSONDecodeError as error:
        raise InputError(f"{path.name} isn't valid JSON: {error.msg}") from None
    return loaded


def parse_people(document: object, label: str) -> dict[str, RampPerson]:
    """Ramp's user list, keyed by lowercase email."""
    people: dict[str, RampPerson] = {}
    for entry in _list(document, "users", label):
        user = _object(entry, label)
        email = _text(user.get("email")).strip().lower()
        if not email:
            continue
        name = " ".join(part.strip() for part in (_text(user.get("first_name")), _text(user.get("last_name"))) if part)
        people[email] = RampPerson(
            email=email,
            name=name,
            team=_text(user.get("department_name")).strip(),
            active=user.get("is_inactive") is not True,
        )
    return people


def parse_key_spend(document: object, label: str) -> list[KeySpend]:
    """Each key's spend over the file's dates. Keys that spent nothing are left out."""
    spend: list[KeySpend] = []
    for entry in _list(document, "data", label):
        key = _object(entry, label)
        key_name = _text(key.get("name")) or _text(key.get("external_id")) or "(unnamed key)"
        amount = _key_amount(key.get("estimated_cost"), f"{label}, key {key_name!r}")
        if amount == 0:
            continue
        owner = key.get("owner")
        owner_email = _text(owner.get("email")).strip().lower() if isinstance(owner, dict) else ""
        spend.append(
            KeySpend(
                vendor=_text(key.get("provider")).lower() or "(unknown vendor)",
                key_name=key_name,
                owner_email=owner_email,
                department_tag=_department_tag(key.get("tags")),
                amount_usd=amount,
            )
        )
    return spend


def parse_provider_totals(document: object, label: str) -> dict[str, Decimal]:
    """Ramp's own API spend total for each vendor over the file's dates, keyed by vendor.

    It comes from Ramp's AI spend summary (cost, grouped by provider). Unlike the per-key
    answer, it includes spend Ramp can't tie to a current key, such as a key deleted
    during the month.
    """
    cost = document.get("cost") if isinstance(document, dict) else None
    rows = cost.get("data") if isinstance(cost, dict) else None
    if not isinstance(rows, list):
        raise InputError(f'{label} should be Ramp\'s AI spend summary, with a "cost" object holding a "data" list')
    totals: dict[str, Decimal] = {}
    for entry in rows:
        row = _object(entry, label)
        vendor = _text(row.get("provider")).lower()
        if not vendor:
            raise InputError(f"{label} has a row with no provider; ask Ramp for the cost grouped by provider")
        amount = row.get("total_cost")
        if amount is None:
            amount = 0
        if isinstance(amount, bool) or not isinstance(amount, (int, Decimal)) or not Decimal(amount).is_finite():
            raise InputError(f"{label} has a total_cost for {vendor} that isn't a number of dollars")
        totals[vendor] = totals.get(vendor, Decimal(0)) + Decimal(amount)
    return totals


def _key_amount(cost: object, where: str) -> Decimal:
    # A key Ramp has no estimate for spent nothing as far as the tool can tell.
    if cost is None:
        return Decimal(0)
    if not isinstance(cost, dict):
        raise InputError(f"{where} has an estimated_cost that isn't a JSON object")
    cents = cost.get("value")
    if cents is None:
        return Decimal(0)
    if cost.get("currency") != "USD":
        raise InputError(f"{where} is priced in {cost.get('currency')!r}; only USD is supported")
    if isinstance(cents, bool) or not isinstance(cents, (int, Decimal)) or not Decimal(cents).is_finite():
        raise InputError(f"{where} has an estimated_cost.value that isn't a number of cents")
    return Decimal(cents) / 100


def _department_tag(tags: object) -> str:
    if not isinstance(tags, list):
        return ""
    for tag in tags:
        if isinstance(tag, dict) and _text(tag.get("key")).strip().lower() == DEPARTMENT_TAG:
            return _text(tag.get("value")).strip()
    return ""


def _list(document: object, key: str, label: str) -> list[object]:
    if isinstance(document, dict):
        entries = document.get(key)
        if isinstance(entries, list):
            return list(entries)
    raise InputError(
        f'{label} should be Ramp\'s answer with a "{key}" list (join every page into that one list before saving)'
    )


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise InputError(f"{label} has an entry that isn't a JSON object")
    return {str(key): item for key, item in value.items()}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
