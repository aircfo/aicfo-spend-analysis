import csv
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from aicfo_spend.cli import main
from aicfo_spend.raw_store import RAMP_API_KEYS, RAMP_PEOPLE, save_raw
from aicfo_spend.report import build_month, describe_change, summarize
from aicfo_spend.settings import load_settings
from aicfo_spend.spend_table import UNASSIGNED
from tests.factories import ramp_key, ramp_keys_answer, ramp_people_answer, ramp_user, spend_export
from tests.workspaces import SEPTEMBER, fill_september, new_workspace, save_month

SEPT_1 = SEPTEMBER.first_day
SEPT_30 = SEPTEMBER.last_day
AUGUST = SEPTEMBER.previous_month()


def saved_at(hour: int) -> datetime:
    return datetime(2026, 10, 1, hour, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Two seats held of three bought, one export, and a month of two API keys.

    Expected: Engineering $325.00 (seat $25 + key $300), Sales $125.50 (seat $25 + extra
    usage $100.50), Unassigned $32.50 (empty seat $25 + unknown person $4.50 + orphan key
    $3). Total $483.00.
    """
    folder = new_workspace(tmp_path)
    fill_september(folder)
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


def test_a_team_in_the_people_file_moves_that_persons_seat_and_usage(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path, "pat.lee@example.com,,Partnerships,anthropic,standard\n")
    fill_september(folder)

    totals = team_totals(folder)

    assert totals["Partnerships"] == Decimal("125.50")
    assert "Sales" not in totals


def test_summary_lists_biggest_teams_first_and_unassigned_by_reason(workspace: Path) -> None:
    summary = summarize(build_month(workspace, SEPTEMBER, load_settings(workspace)), "Acme")

    assert summary.startswith("AI spend for 2026-09 (Acme)")
    assert summary.index("Engineering") < summary.index("Sales") < summary.index("Unassigned")
    assert "$483.00" in summary
    vendor_line = next(line for line in summary.splitlines() if line.startswith("anthropic"))
    assert vendor_line.split() == ["anthropic", "$75.00", "$105.00", "$303.00", "$483.00"]
    assert "  empty seat: $25.00" in summary
    assert "  key with no team: $3.00" in summary
    assert "  no team on the people list: $4.50" in summary
    assert "Notes:" not in summary


def test_notes_say_which_inputs_are_missing_or_partial(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path, with_ramp_people=False)
    keys = ramp_keys_answer(ramp_key("backend", 100))
    save_raw(folder, RAMP_API_KEYS, SEPT_1, date(2026, 9, 16), keys, saved_at=saved_at(9))

    notes = build_month(folder, SEPTEMBER, load_settings(folder)).notes

    assert "no Ramp people list is saved, so only people.csv places people on teams" in notes
    assert "no claude.ai spend export is saved for 2026-09, so extra usage shows $0" in notes
    assert "Ramp API spend covers 2026-09-01 to 2026-09-16, not the whole month" in notes


def test_notes_when_a_newer_download_shows_less_than_an_earlier_one(workspace: Path) -> None:
    cut_short = ramp_keys_answer(ramp_key("backend", 1000, "sam.cho@example.com"))
    save_raw(workspace, RAMP_API_KEYS, SEPT_1, SEPT_30, cut_short, saved_at=saved_at(12))

    spend = build_month(workspace, SEPTEMBER, load_settings(workspace))

    assert (
        "the newest Ramp API spend totals $10.00, less than an earlier download's $303.00; "
        "check that the latest download is complete"
    ) in spend.notes
    assert team_totals(workspace)["Engineering"] == Decimal("35.00")


def test_an_older_download_it_cannot_read_only_earns_a_note(workspace: Path) -> None:
    # Placed by hand, so it skipped the checks save would have made.
    bad = workspace / "raw/2026-09/claude-spend-export_2026-09-01_2026-09-15_saved-20260916T090000Z.csv"
    bad.write_bytes(spend_export(("pat.lee@example.com", "claude-opus", "$12.50")))

    spend = build_month(workspace, SEPTEMBER, load_settings(workspace))

    assert f"couldn't read the older download {bad.name}, so it wasn't compared" in spend.notes
    assert sum(row.amount_usd for row in spend.rows) == Decimal("483.00")


def test_report_command_writes_a_spend_table_that_adds_up(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["report", str(workspace), "2026-09"]) == 0

    with (workspace / "out/2026-09/spend.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert sum(Decimal(row["amount_usd"]) for row in rows) == Decimal("483.00")
    assert {row["source_file"] for row in rows if row["product"] == "seats"} == {"people.csv", "aicfo-spend.json"}
    assert "Wrote out/2026-09/spend.csv (7 rows)" in capsys.readouterr().out


def test_save_command_files_a_download(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = new_workspace(tmp_path)
    download = tmp_path / "answer.json"
    download.write_bytes(ramp_keys_answer(ramp_key("backend", 100)))

    arguments = ["ramp-api-keys", "--start", "2026-09-01", "--end", "2026-09-14", "--file", str(download)]
    assert main(["save", str(folder), *arguments]) == 0

    assert capsys.readouterr().out.startswith("Saved raw/2026-09/ramp-api-keys_2026-09-01_2026-09-14_saved-")


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["ramp-api-keys", "--start", "2026-09-01", "--file", "missing.json"], "no file at"),
        (["ramp-api-keys", "--start", "1 Sept", "--file", "missing.json"], "isn't a date"),
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
    assert main(["save", str(tmp_path), "ramp-api-keys", "--start", "2026-09-01", "--file", "x.json"]) == 1

    assert "isn't a workspace" in capsys.readouterr().err


def fill_august(folder: Path) -> None:
    """Engineering $175 (seat + $150 of API), Sales $85 (seat + $60 extra usage), Unassigned $25."""
    save_month(folder, AUGUST, backend_cents=15000, extra_usage=(("pat.lee@example.com", "claude-opus", "60"),))


def test_api_spend_no_current_key_carries_is_unassigned_with_its_reason(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    save_month(folder, SEPTEMBER, backend_cents=30000, untied_cents=19236)

    spend = build_month(folder, SEPTEMBER, load_settings(folder))

    [untied] = [row for row in spend.rows if row.reason == "API spend not tied to a current key"]
    assert (untied.team, untied.vendor, untied.product, untied.amount_usd) == (UNASSIGNED, "anthropic", "api", Decimal("192.36"))
    assert untied.source_file.startswith("raw/2026-09/ramp-provider-totals_2026-09-01_2026-09-30_saved-")
    assert "  API spend not tied to a current key: $192.36" in summarize(spend)


def test_keys_adding_up_to_clearly_more_than_ramps_total_earn_a_note(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    save_month(folder, SEPTEMBER, backend_cents=30000, untied_cents=-500)

    spend = build_month(folder, SEPTEMBER, load_settings(folder))

    assert any("anthropic's keys add up to $5.00 more than Ramp's total" in note for note in spend.notes)
    assert not any(row.reason == "API spend not tied to a current key" for row in spend.rows)


def test_a_cent_of_rounding_between_keys_and_ramps_total_is_ignored(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    save_month(folder, SEPTEMBER, backend_cents=30000, untied_cents=-1)

    spend = build_month(folder, SEPTEMBER, load_settings(folder))

    assert not any("Ramp's total" in note for note in spend.notes)
    assert not any(row.reason == "API spend not tied to a current key" for row in spend.rows)


def test_says_so_when_no_provider_totals_are_saved(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    keys = ramp_keys_answer(ramp_key("backend", 30000, "sam.cho@example.com"))
    save_raw(folder, RAMP_API_KEYS, SEPTEMBER.first_day, SEPTEMBER.last_day, keys)

    spend = build_month(folder, SEPTEMBER, load_settings(folder))

    assert (
        "no Ramp provider totals are saved for 2026-09-01 to 2026-09-30, so API spend Ramp can't tie to a key isn't counted"
        in spend.notes
    )


def test_change_from_last_month_shows_each_teams_move(workspace: Path) -> None:
    fill_august(workspace)
    settings = load_settings(workspace)

    change = describe_change(build_month(workspace, SEPTEMBER, settings), build_month(workspace, AUGUST, settings), False)

    lines = change.splitlines()
    assert lines[0] == "Change from 2026-08"
    assert next(line for line in lines if line.startswith("Engineering")).split() == [
        "Engineering", "$175.00", "$325.00", "+$150.00", "(+86%)"
    ]
    assert next(line for line in lines if line.startswith("Total")).split() == [
        "Total", "$285.00", "$483.00", "+$198.00", "(+69%)"
    ]
    assert "  claude-opus: $60.00 to $100.50, +$40.50 (+68%)" in lines
    assert "  claude-sonnet: $0.00 to $4.50, +$4.50 (new)" in lines
    assert "By person" not in change


def test_change_lines_up_a_team_spelled_differently_last_month(workspace: Path) -> None:
    fill_august(workspace)
    lowercase = ramp_people_answer(ramp_user("pat.lee@example.com", "sales"), ramp_user("sam.cho@example.com", "engineering"))
    save_raw(workspace, RAMP_PEOPLE, date(2026, 9, 1), date(2026, 9, 1), lowercase, saved_at=saved_at(9))
    settings = load_settings(workspace)

    change = describe_change(build_month(workspace, SEPTEMBER, settings), build_month(workspace, AUGUST, settings), False)

    team_lines = [line.split()[0] for line in change.splitlines()[3:6]]
    assert team_lines == ["Engineering", "Sales", "Unassigned"]


def test_change_by_person_appears_only_in_the_person_view(workspace: Path) -> None:
    fill_august(workspace)
    settings = load_settings(workspace)

    change = describe_change(build_month(workspace, SEPTEMBER, settings), build_month(workspace, AUGUST, settings), True)

    assert "  sam.cho@example.com: $175.00 to $325.00, +$150.00 (+86%)" in change.splitlines()


def test_change_warns_when_last_month_is_missing_a_download(workspace: Path) -> None:
    save_month(workspace, AUGUST, backend_cents=15000)
    settings = load_settings(workspace)

    change = describe_change(build_month(workspace, SEPTEMBER, settings), build_month(workspace, AUGUST, settings), False)

    assert "  - 2026-08 has no extra usage saved, so the change overstates growth" in change.splitlines()


def test_change_says_so_when_last_month_has_no_usage(workspace: Path) -> None:
    settings = load_settings(workspace)

    change = describe_change(build_month(workspace, SEPTEMBER, settings), build_month(workspace, AUGUST, settings), False)

    assert change == "No usage is saved for 2026-08, so there's no change from last month to show."


def test_change_warns_when_last_month_is_partial(workspace: Path) -> None:
    keys = ramp_keys_answer(ramp_key("backend", 15000, "sam.cho@example.com"))
    save_raw(workspace, RAMP_API_KEYS, AUGUST.first_day, date(2026, 8, 20), keys, saved_at=saved_at(9))
    settings = load_settings(workspace)

    change = describe_change(build_month(workspace, SEPTEMBER, settings), build_month(workspace, AUGUST, settings), False)

    assert "  - 2026-08's api only runs through 2026-08-20, so the change overstates growth" in change.splitlines()


def test_a_team_that_spent_the_same_shows_no_change(workspace: Path) -> None:
    september = build_month(workspace, SEPTEMBER, load_settings(workspace))

    change = describe_change(september, september, False)

    assert next(line for line in change.splitlines() if line.startswith("Engineering")).endswith("no change")


def test_report_still_succeeds_when_last_months_files_cant_be_read(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = workspace / "raw/2026-08/ramp-api-keys_2026-08-31_2026-08-31_saved-20260901T090000Z.json"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_bytes(ramp_keys_answer())

    assert main(["report", str(workspace), "2026-09"]) == 0

    assert "Change from last month isn't available:" in capsys.readouterr().out
