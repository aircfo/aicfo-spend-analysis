"""Read the Ramp answers the skill saves: per-key API spend and the people list."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
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
    day: date
    vendor: str
    key_name: str
    owner_email: str
    department_tag: str
    amount_usd: Decimal


def read_people(path: Path) -> dict[str, RampPerson]:
    """Ramp's user list, keyed by lowercase email."""
    people: dict[str, RampPerson] = {}
    for entry in _list(_load(path), "users", path):
        user = _object(entry, path)
        email = _text(user.get("email")).strip().lower()
        if not email:
            continue
        name = " ".join(part for part in (_text(user.get("first_name")), _text(user.get("last_name"))) if part)
        people[email] = RampPerson(
            email=email,
            name=name,
            team=_text(user.get("department_name")).strip(),
            active=user.get("is_inactive") is not True,
        )
    return people


def read_key_spend(path: Path, day: date) -> list[KeySpend]:
    """One day of Ramp's per-key API spend. Keys that spent nothing that day are left out."""
    spend: list[KeySpend] = []
    for entry in _list(_load(path), "data", path):
        key = _object(entry, path)
        cost = _object(key.get("estimated_cost"), path)
        if cost.get("currency") != "USD":
            raise InputError(f"{path.name} has a key priced in {cost.get('currency')!r}; only USD is supported")
        cents = cost.get("value")
        if isinstance(cents, bool) or not isinstance(cents, int):
            raise InputError(f"{path.name} has a key whose estimated_cost.value isn't a whole number of cents")
        if cents == 0:
            continue
        owner = key.get("owner")
        owner_email = _text(owner.get("email")).strip().lower() if isinstance(owner, dict) else ""
        spend.append(
            KeySpend(
                day=day,
                vendor=_text(key.get("provider")).lower(),
                key_name=_text(key.get("name")) or _text(key.get("external_id")),
                owner_email=owner_email,
                department_tag=_department_tag(key.get("tags")),
                amount_usd=Decimal(cents) / 100,
            )
        )
    return spend


def _department_tag(tags: object) -> str:
    if not isinstance(tags, list):
        return ""
    for tag in tags:
        if isinstance(tag, dict) and _text(tag.get("key")).strip().lower() == DEPARTMENT_TAG:
            return _text(tag.get("value")).strip()
    return ""


def _load(path: Path) -> object:
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise InputError(f"{path.name} isn't valid JSON: {error.msg}") from None
    return loaded


def _list(document: object, key: str, path: Path) -> list[object]:
    if isinstance(document, dict):
        entries = document.get(key)
        if isinstance(entries, list):
            return list(entries)
    raise InputError(f'{path.name} should hold Ramp\'s answer with a "{key}" list')


def _object(value: object, path: Path) -> dict[str, object]:
    if not isinstance(value, dict):
        raise InputError(f"{path.name} has an entry that isn't a JSON object")
    return {str(key): item for key, item in value.items()}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
