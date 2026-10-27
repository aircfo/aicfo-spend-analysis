"""Made-up Ramp answers and spend exports, shaped like the real ones."""

from __future__ import annotations

import csv
import io
import json


def ramp_key(
    name: str,
    cents: int,
    owner_email: str | None = None,
    department: str | None = None,
    provider: str = "ANTHROPIC",
    currency: str = "USD",
) -> dict[str, object]:
    return {
        "name": name,
        "external_id": f"apikey_{name}",
        "provider": provider,
        "status": "active",
        "tags": [{"key": "Department", "value": department}] if department else [],
        "owner": {"email": owner_email, "first_name": "Test", "last_name": "Owner"} if owner_email else None,
        "estimated_cost": {"currency": currency, "value": cents},
    }


def ramp_keys_answer(*keys: dict[str, object]) -> bytes:
    return json.dumps({"page": {"next": None}, "data": list(keys)}).encode()


def ramp_user(email: str, department: str, inactive: bool = False) -> dict[str, object]:
    first, _, last = email.partition("@")[0].partition(".")
    return {
        "email": email,
        "first_name": first.title(),
        "last_name": last.title(),
        "department_name": department,
        "is_inactive": inactive,
    }


def ramp_people_answer(*users: dict[str, object]) -> bytes:
    return json.dumps({"users": list(users), "next_page": None}).encode()


def spend_export(*rows: tuple[str, str, str]) -> bytes:
    """Rows of (email, model, net spend in dollars), with the export's other columns blank."""
    columns = ["user_email", "account_uuid", "product", "model", "total_net_spend_usd", "total_gross_spend_usd"]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for email, model, net in rows:
        writer.writerow([email, "", "Chat", model, net, net])
    return buffer.getvalue().encode()
