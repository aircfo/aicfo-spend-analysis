"""Made-up workspaces with saved downloads, for tests that run whole months."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from aicfo_spend.months import Month
from aicfo_spend.raw_store import CLAUDE_SPEND_EXPORT, RAMP_API_KEYS, RAMP_PEOPLE, RAMP_PROVIDER_TOTALS, save_raw
from aicfo_spend.settings import SETTINGS_FILE_NAME
from aicfo_spend.workspace import PEOPLE_FILE_NAME, create_workspace
from tests.factories import ramp_key, ramp_keys_answer, ramp_people_answer, ramp_totals_answer, ramp_user, spend_export

SEPTEMBER = Month(2026, 9)
PEOPLE_CSV_HEADER = "email,name,team,vendor,seat_type\n"
TWO_SEATS = "pat.lee@example.com,,,anthropic,standard\nsam.cho@example.com,,,anthropic,standard\n"
SAVED_AT = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)


def new_workspace(tmp_path: Path, people_csv: str = TWO_SEATS, with_ramp_people: bool = True) -> Path:
    """Acme: three standard seats bought at $25, two held (Sales and Engineering)."""
    folder = tmp_path / "acme"
    create_workspace(folder, "Acme")
    (folder / SETTINGS_FILE_NAME).write_text(
        '{"company_name": "Acme", "seats": {"anthropic": {"standard": {"price_usd": 25, "bought": 3}}}}',
        encoding="utf-8",
    )
    (folder / PEOPLE_FILE_NAME).write_text(PEOPLE_CSV_HEADER + people_csv, encoding="utf-8")
    if not with_ramp_people:
        return folder
    october_first = date(2026, 10, 1)
    people = ramp_people_answer(ramp_user("pat.lee@example.com", "Sales"), ramp_user("sam.cho@example.com", "Engineering"))
    save_raw(folder, RAMP_PEOPLE, october_first, october_first, people, saved_at=SAVED_AT)
    return folder


def save_month(
    folder: Path,
    month: Month,
    backend_cents: int | None = None,
    orphan_cents: int | None = None,
    extra_usage: tuple[tuple[str, str, str], ...] | None = None,
    untied_cents: int = 0,
) -> None:
    """Save a whole month of Ramp API spend, with Ramp's total beside it, and, if given, a claude.ai export.

    Ramp's total is the keys' sum plus untied_cents, the spend no current key carries.
    """
    if backend_cents is not None or orphan_cents is not None:
        keys = []
        if backend_cents is not None:
            keys.append(ramp_key("backend", backend_cents, "sam.cho@example.com"))
        if orphan_cents is not None:
            keys.append(ramp_key("orphan", orphan_cents))
        save_raw(folder, RAMP_API_KEYS, month.first_day, month.last_day, ramp_keys_answer(*keys), saved_at=SAVED_AT)
        total_cents = (backend_cents or 0) + (orphan_cents or 0) + untied_cents
        totals = ramp_totals_answer(ANTHROPIC=f"{total_cents / 100}")
        save_raw(folder, RAMP_PROVIDER_TOTALS, month.first_day, month.last_day, totals, saved_at=SAVED_AT)
    if extra_usage is not None:
        export = spend_export(*extra_usage)
        save_raw(folder, CLAUDE_SPEND_EXPORT, month.first_day, month.last_day, export, saved_at=SAVED_AT)


def fill_september(folder: Path) -> None:
    """Engineering $325.00, Sales $125.50, Unassigned $32.50: $483.00 in all."""
    save_month(
        folder,
        SEPTEMBER,
        backend_cents=30000,
        orphan_cents=300,
        extra_usage=(("pat.lee@example.com", "claude-opus", "100.50"), ("new.hire@example.com", "claude-sonnet", "4.50")),
    )
