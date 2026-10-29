"""Made-up Ramp answers and spend exports, shaped like the real ones."""

from __future__ import annotations

import csv
import io
import json


def ramp_key(
    name: str,
    cents: int | float | None,
    owner_email: str | None = None,
    department: str | None = None,
    provider: str = "ANTHROPIC",
    currency: str = "USD",
    cost_category: str | None = None,
) -> dict[str, object]:
    """cents=None stands for a key Ramp has no cost estimate for."""
    tags = [{"key": "Department", "value": department}] if department else []
    if cost_category:
        tags.append({"key": "Cost category", "value": cost_category})
    return {
        "name": name,
        "external_id": f"apikey_{name}",
        "provider": provider,
        "status": "active",
        "tags": tags,
        "owner": {"email": owner_email, "first_name": "Test", "last_name": "Owner"} if owner_email else None,
        "estimated_cost": None if cents is None else {"currency": currency, "value": cents},
    }


def ramp_keys_answer(*keys: dict[str, object]) -> bytes:
    return json.dumps({"page": {"next": None}, "data": list(keys)}).encode()


def ramp_totals_answer(**dollars_by_provider: str) -> bytes:
    """Ramp's AI spend summary, cost grouped by provider: ramp_totals_answer(ANTHROPIC="303.0049")."""
    rows = [
        {"period_start_at": None, "period_end_at": None, "provider": provider, "model": None, "total_cost": float(amount)}
        for provider, amount in dollars_by_provider.items()
    ]
    return json.dumps({"cost": {"effective_start_at": None, "effective_end_at": None, "data": rows}, "usage": None}).encode()


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


USAGE_EXPORT_COLUMNS = [
    "user_email", "account_uuid", "product", "model", "total_requests", "total_prompt_tokens", "total_completion_tokens",
    "total_net_spend_usd", "total_gross_spend_usd", "user_id", "total_uncached_input_tokens", "total_cache_read_tokens",
    "total_cache_write_5m_tokens", "total_cache_write_1h_tokens", "total_web_search_count",
]


def usage_export(*rows: tuple[str, str, int, int, int, int, str]) -> bytes:
    """Rows of (email, product, requests, uncached input, cache reads, output tokens, net spend in dollars),
    shaped like the export with usage columns. Prompt tokens are uncached input plus cache reads, as in the real one."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(USAGE_EXPORT_COLUMNS)
    for email, product, requests, uncached, cache_read, output, net in rows:
        writer.writerow([email, "", product, "claude-sonnet", requests, uncached + cache_read, output, net, net, "",
                         uncached, cache_read, 0, 0, 0])
    return buffer.getvalue().encode()
