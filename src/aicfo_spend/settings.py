"""Load and check a workspace's settings file, aicfo-spend.json.

The file is JSON rather than TOML because Python 3.10, the oldest version the
tool supports, can't read TOML without an add-on package.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal

from aicfo_spend.errors import UserError
from aicfo_spend.months import Month

SETTINGS_FILE_NAME = "aicfo-spend.json"

Visibility = Literal["team", "person"]
VISIBILITIES: tuple[Visibility, ...] = ("team", "person")

TOP_LEVEL_KEYS = (
    "company_name", "visibility", "seats", "alerts", "known_changes", "api_cost_categories", "other_ai_vendors",
)
KNOWN_CHANGE_KEYS = ("month", "team", "product", "amount_usd", "note")
# The usage products a known change can move; seats follow the people file instead.
KNOWN_CHANGE_PRODUCTS = ("extra usage", "api")
SEAT_PLAN_KEYS = ("price_usd", "bought")
ALERT_KEYS = ("spike_multiple", "spike_min_usd", "over_pace_percent")


class SettingsError(UserError):
    """A problem with the settings file."""


@dataclass(frozen=True)
class SeatPlan:
    price_usd: Decimal
    bought: int


@dataclass(frozen=True)
class AlertThresholds:
    spike_multiple: Decimal = Decimal("2")
    spike_min_usd: Decimal = Decimal("25")
    over_pace_percent: Decimal = Decimal("25")


@dataclass(frozen=True)
class KnownChange:
    """Something the operator expects that history can't show, like a new project's API key."""

    month: Month
    team: str
    product: str
    # Negative for a cut, such as a key being retired.
    amount_usd: Decimal
    note: str


@dataclass(frozen=True)
class Settings:
    company_name: str = ""
    visibility: Visibility = "person"
    # vendor -> seat type -> plan, e.g. seats["anthropic"]["premium"]
    seats: Mapping[str, Mapping[str, SeatPlan]] = field(default_factory=dict)
    alerts: AlertThresholds = field(default_factory=AlertThresholds)
    known_changes: tuple[KnownChange, ...] = ()
    # API key name, in lower case -> what its spend is for. A "Cost category" tag on the key in Ramp wins.
    api_cost_categories: Mapping[str, str] = field(default_factory=dict)
    # Other AI vendors paid through Ramp, named as Ramp names the payee, such as "Cursor".
    other_ai_vendors: tuple[str, ...] = ()


def load_settings(workspace: Path) -> Settings:
    path = workspace / SETTINGS_FILE_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SettingsError(
            f"no settings file at {path}. Run `aicfo-spend init {workspace}` to create one."
        ) from None
    try:
        # Dollar amounts stay exact: 25.10 is read as Decimal("25.10"), never a float.
        raw: object = json.loads(text, parse_float=Decimal)
    except json.JSONDecodeError as error:
        raise SettingsError(
            f"{path} isn't valid JSON (line {error.lineno}, column {error.colno}): {error.msg}"
        ) from None
    return parse_settings(raw)


def parse_settings(raw: object) -> Settings:
    top = _object(raw, "the settings file")
    _reject_unknown_keys(top, TOP_LEVEL_KEYS, "the settings file")
    return Settings(
        company_name=_text(top.get("company_name", ""), "company_name"),
        visibility=_visibility(top.get("visibility", "person")),
        seats=_seats(top.get("seats", {})),
        alerts=_alerts(top.get("alerts", {})),
        known_changes=_known_changes(top.get("known_changes", [])),
        api_cost_categories={
            key.strip().lower(): _text(category, f"api_cost_categories.{key}").strip()
            for key, category in _object(top.get("api_cost_categories", {}), "api_cost_categories").items()
        },
        other_ai_vendors=_other_ai_vendors(top.get("other_ai_vendors", [])),
    )


def _other_ai_vendors(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise SettingsError('other_ai_vendors must be a list of vendor names, like ["Cursor", "Perplexity"]')
    names = tuple(_text(name, f"other_ai_vendors item {number}").strip() for number, name in enumerate(value, start=1))
    if any(name.lower() in ("anthropic", "openai") for name in names):
        raise SettingsError("other_ai_vendors is for vendors besides Anthropic and OpenAI, which the tool already reads")
    seen: set[str] = set()
    for name in names:
        # Ramp's payees are matched whatever their case, so two spellings would count one vendor twice.
        if name.casefold() in seen:
            raise SettingsError(f"other_ai_vendors lists {name!r} twice; list each vendor once")
        seen.add(name.casefold())
    return names


def _object(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SettingsError(f"{where} must be a JSON object, like {{ ... }}")
    return {str(key): item for key, item in value.items()}


def _reject_unknown_keys(values: Mapping[str, object], allowed: tuple[str, ...], where: str) -> None:
    unknown = sorted(set(values) - set(allowed))
    if unknown:
        raise SettingsError(
            f"{where} has unexpected keys {unknown}; the keys it can have are {list(allowed)}"
        )


def _text(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise SettingsError(f"{where} must be text in quotes")
    return value


def _visibility(value: object) -> Visibility:
    for visibility in VISIBILITIES:
        if value == visibility:
            return visibility
    raise SettingsError(f'visibility must be "team" or "person", not {value!r}')


def _money(value: object, where: str) -> Decimal:
    # bool is a subclass of int in Python, so true/false must be ruled out first.
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise SettingsError(f"{where} must be a dollar amount, like 25 or 25.00")
    amount = Decimal(value)
    if amount < 0:
        raise SettingsError(f"{where} can't be negative")
    return amount


def _positive(value: object, where: str) -> Decimal:
    amount = _money(value, where)
    if amount == 0:
        raise SettingsError(f"{where} must be more than 0")
    return amount


def _seat_count(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SettingsError(f"{where} must be a whole number of seats, 0 or more")
    return value


def _seats(value: object) -> dict[str, dict[str, SeatPlan]]:
    # Names are matched without regard to case, so "Anthropic" in settings meets "anthropic" in people.csv.
    seats: dict[str, dict[str, SeatPlan]] = {}
    for vendor, plans in _object(value, "seats").items():
        where_vendor = f"seats.{vendor}"
        vendor_plans = seats.setdefault(vendor.strip().lower(), {})
        for seat_type, plan in _object(plans, where_vendor).items():
            where = f"{where_vendor}.{seat_type}"
            fields = _object(plan, where)
            _reject_unknown_keys(fields, SEAT_PLAN_KEYS, where)
            if "price_usd" not in fields or "bought" not in fields:
                raise SettingsError(f"{where} needs both price_usd and bought")
            vendor_plans[seat_type.strip().lower()] = SeatPlan(
                price_usd=_money(fields["price_usd"], f"{where}.price_usd"),
                bought=_seat_count(fields["bought"], f"{where}.bought"),
            )
    return seats


def _alerts(value: object) -> AlertThresholds:
    fields = _object(value, "alerts")
    _reject_unknown_keys(fields, ALERT_KEYS, "alerts")
    defaults = AlertThresholds()
    return AlertThresholds(
        spike_multiple=_positive(fields.get("spike_multiple", defaults.spike_multiple), "alerts.spike_multiple"),
        spike_min_usd=_money(fields.get("spike_min_usd", defaults.spike_min_usd), "alerts.spike_min_usd"),
        over_pace_percent=_positive(
            fields.get("over_pace_percent", defaults.over_pace_percent), "alerts.over_pace_percent"
        ),
    )


def _known_changes(value: object) -> tuple[KnownChange, ...]:
    if not isinstance(value, list):
        raise SettingsError("known_changes must be a list, like [ { ... } ]")
    changes = []
    for number, item in enumerate(value, start=1):
        where = f"known_changes item {number}"
        fields = _object(item, where)
        _reject_unknown_keys(fields, KNOWN_CHANGE_KEYS, where)
        month_text = _text(fields.get("month"), f"{where}.month")
        try:
            month = Month.parse(month_text)
        except UserError:
            raise SettingsError(f"{where}.month must be a month like 2026-11, not {month_text!r}") from None
        product = fields.get("product")
        if product not in KNOWN_CHANGE_PRODUCTS:
            raise SettingsError(f'{where}.product must be "api" or "extra usage", not {product!r}')
        amount = fields.get("amount_usd")
        # bool is a subclass of int in Python, so true/false must be ruled out first.
        if isinstance(amount, bool) or not isinstance(amount, (int, Decimal)):
            raise SettingsError(f"{where}.amount_usd must be a dollar amount, like 300 or -120.50")
        changes.append(KnownChange(
            month=month,
            team=_text(fields.get("team"), f"{where}.team").strip(),
            product=str(product),
            amount_usd=Decimal(amount),
            note=_text(fields.get("note", ""), f"{where}.note"),
        ))
    return tuple(changes)
