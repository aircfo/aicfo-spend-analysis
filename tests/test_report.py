import csv
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from aicfo_spend.cli import main
from aicfo_spend.months import Month
from aicfo_spend.raw_store import CLAUDE_SPEND_EXPORT, RAMP_API_KEYS, RAMP_PEOPLE, save_raw
from aicfo_spend.report import build_month, summarize
from aicfo_spend.settings import SETTINGS_FILE_NAME, load_settings
from aicfo_spend.spend_table import UNASSIGNED
from aicfo_spend.workspace import PEOPLE_FILE_NAME, create_workspace
from tests.factories import (
    ramp_key,
    ramp_keys_answer,
    ramp_people_answer,
    ramp_user,
    spend_export,
)

SEPTEMBER = Month(2026, 9)
SAVED_AT = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)


def new_workspace(tmp_path: Path) -> Path:
    folder = tmp_path / "acme"
    create_workspace(folder, "Acme")
    (folder / SETTINGS_FILE_NAME).write_text(
        '{"company_name": "Acme", "seats": {"anthropic": {"standard": {"price_usd": 25, "bought": 3}}}}',
        encoding="utf-8",
    )
    (folder / PEOPLE_FILE_NAME).write_text(
        "email,name,team,vendor,seat_type\n"
        "pat.lee@example.com,,,anthropic,standard\n"
        "sam.cho@example.com,,,anthropic,standard\n",
        encoding="utf-8",
    )
    return folder


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Two seats held of three bought, one export, and a full month of two API keys.

    Expected: Engineering $325.00 (seat $25 + key $10 x 30 days), Sales $125.50 (seat $25 +
    extra usage $100.50), Unassigned $32.50 (empty seat $25 + unknown person $4.50 + orphan
    key $0.10 x 30 days). Total $483.00.
    """
    folder = new_workspace(tmp_path)
    october_first = date(2026, 10, 1)
    people = ramp_people_answer(ramp_user("pat.lee@example.com", "Sales"), ramp_user("sam.cho@example.com", "Engineering"))
    save_raw(folder, RAMP_PEOPLE, october_first, october_first, people, saved_at=SAVED_AT)
    export = spend_export(("pat.lee@example.com", "claude-opus", "100.50"), ("new.hire@example.com", "claude-sonnet", "4.50"))
    save_raw(folder, CLAUDE_SPEND_EXPORT, SEPTEMBER.first_day, SEPTEMBER.last_day, export, saved_at=SAVED_AT)
    for day in SEPTEMBER.days():
        keys = ramp_keys_answer(ramp_key("backend", 1000, "sam.cho@example.com"), ramp_key("orphan", 10))
        save_raw(folder, RAMP_API_KEYS, day, day, keys, saved_at=SAVED_AT)
    return folder


def team_totals(folder: Path) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for row in build_month(folder, SEPTEMBER, load_settings(folder)).rows:
        totals[row.team] = totals.get(row.team, Decimal(0)) + row.amount_usd
    return totals


def test_every_dollar_lands_on_a_team_or_on_unassigned(workspace: Path) -> None:
    totals = team_totals(workspace)

    assert totals == {"Engineering": Decimal("325.00"), "Sales": Decimal("125.50"), UNASSIGNED: Decimal("32.50")}
    assert sum(totals.values()) == Decimal("483.00")


def test_summary_lists_biggest_teams_first_and_unassigned_by_reason(workspace: Path) -> None:
    summary = summarize(build_month(workspace, SEPTEMBER, load_settings(workspace)), "Acme")

    assert summary.startswith("AI spend for 2026-09 (Acme)")
    assert summary.index("Engineering") < summary.index("Sales") < summary.index("Unassigned")
    assert "$483.00" in summary
    assert "  empty seat: $25.00" in summary
    assert "  key with no team: $3.00" in summary
    assert "  not on the people list: $4.50" in summary
    assert "Notes:" not in summary


def test_notes_say_which_inputs_are_missing_or_partial(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    for day in (date(2026, 9, 14), date(2026, 9, 16)):
        save_raw(folder, RAMP_API_KEYS, day, day, ramp_keys_answer(ramp_key("backend", 100)), saved_at=SAVED_AT)

    notes = build_month(folder, SEPTEMBER, load_settings(folder)).notes

    assert "no Ramp people list is saved, so only people.csv places people on teams" in notes
    assert "no claude.ai spend export is saved for 2026-09, so extra usage shows $0" in notes
    assert any(note.startswith("Ramp API spend is missing for 14 day(s): 2026-09-01") for note in notes)
    assert "Ramp API spend runs through 2026-09-16" in notes


def test_report_command_writes_a_spend_table_that_adds_up(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["report", str(workspace), "2026-09"]) == 0

    with (workspace / "out/2026-09/spend.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert sum(Decimal(row["amount_usd"]) for row in rows) == Decimal("483.00")
    assert {row["source_file"] for row in rows if row["product"] == "seats"} == {"people.csv", "aicfo-spend.json"}
    assert "Wrote out/2026-09/spend.csv (65 rows)" in capsys.readouterr().out


def test_save_command_files_a_download(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = new_workspace(tmp_path)
    download = tmp_path / "answer.json"
    download.write_bytes(ramp_keys_answer(ramp_key("backend", 100)))

    assert main(["save", str(folder), "ramp-api-keys", "--start", "2026-09-14", "--file", str(download)]) == 0

    assert capsys.readouterr().out.startswith("Saved raw/2026-09/ramp-api-keys_2026-09-14_2026-09-14_saved-")


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["ramp-api-keys", "--start", "2026-09-14", "--file", "missing.json"], "no file at"),
        (["ramp-api-keys", "--start", "14 Sept", "--file", "missing.json"], "isn't a date"),
    ],
)
def test_save_command_explains_bad_arguments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], arguments: list[str], message: str
) -> None:
    folder = new_workspace(tmp_path)

    assert main(["save", str(folder), *arguments]) == 1

    assert message in capsys.readouterr().err


def test_save_command_refuses_a_folder_that_isnt_a_workspace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["save", str(tmp_path), "ramp-api-keys", "--start", "2026-09-14", "--file", "x.json"]) == 1

    assert "isn't a workspace" in capsys.readouterr().err
