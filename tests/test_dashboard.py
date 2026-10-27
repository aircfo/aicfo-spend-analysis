import json
from datetime import date
from pathlib import Path

import pytest

from aicfo_spend.cli import main
from aicfo_spend.dashboard import OTHER_SMALL_TEAMS, build_dashboard, without_emails
from aicfo_spend.months import Month
from aicfo_spend.raw_store import RAMP_API_KEYS, RAMP_PEOPLE, save_raw
from aicfo_spend.settings import SETTINGS_FILE_NAME, load_settings
from aicfo_spend.spend_table import UNASSIGNED
from tests.factories import ramp_key, ramp_keys_answer, ramp_people_answer, ramp_user
from tests.workspaces import SAVED_AT, SEPTEMBER, new_workspace, save_month

OCTOBER = Month(2026, 10)
EARLY_OCTOBER = date(2026, 10, 5)
ENGINEERS = ("sam.cho@example.com", "jo.park@example.com", "kai.wu@example.com")
SEAT_HOLDERS = "".join(f"{email},,,anthropic,standard\n" for email in (*ENGINEERS, "pat.lee@example.com"))


def as_list(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    return value


def strings(value: object) -> list[str]:
    assert isinstance(value, list)
    return [str(item) for item in value]


def section(dashboard: dict[str, object], name: str) -> dict[str, object]:
    value = dashboard[name]
    assert isinstance(value, dict)
    return value


def by_name(table: object, label: str) -> dict[object, dict[str, object]]:
    return {row[label]: row for row in as_list(table)}


def use_person_view(folder: Path) -> None:
    settings = json.loads((folder / SETTINGS_FILE_NAME).read_text(encoding="utf-8"))
    settings["visibility"] = "person"
    (folder / SETTINGS_FILE_NAME).write_text(json.dumps(settings), encoding="utf-8")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Acme in September: three engineers and one salesperson hold seats; five were bought at $25.

    Engineering $375.00 (3 seats, API $300), Sales $125.50 (1 seat, extra usage $100.50),
    Unassigned $29.50 (an empty seat, and $4.50 from someone with no team): $530.00 in all.
    """
    folder = new_workspace(tmp_path, SEAT_HOLDERS, with_ramp_people=False)
    (folder / SETTINGS_FILE_NAME).write_text(
        '{"company_name": "Acme", "seats": {"anthropic": {"standard": {"price_usd": 25, "bought": 5}}}}',
        encoding="utf-8",
    )
    people = ramp_people_answer(
        *(ramp_user(email, "Engineering") for email in ENGINEERS), ramp_user("pat.lee@example.com", "Sales")
    )
    save_raw(folder, RAMP_PEOPLE, date(2026, 10, 1), date(2026, 10, 1), people, saved_at=SAVED_AT)
    save_month(
        folder,
        SEPTEMBER,
        backend_cents=30000,
        extra_usage=(("pat.lee@example.com", "claude-opus", "100.50"), ("new.hire@example.com", "claude-sonnet", "4.50")),
    )
    return folder


def test_the_team_view_folds_teams_fewer_than_three_people_spent_in(workspace: Path) -> None:
    dashboard = build_dashboard(workspace, load_settings(workspace), SEPTEMBER, EARLY_OCTOBER)

    teams = by_name(section(dashboard, "spend")["by_team"], "team")
    assert list(teams) == ["Engineering", OTHER_SMALL_TEAMS, UNASSIGNED]
    assert teams[OTHER_SMALL_TEAMS]["total_cents"] == 12550
    assert list(by_name(section(dashboard, "forecast")["by_team"], "team")) == ["Engineering", OTHER_SMALL_TEAMS, UNASSIGNED]
    assert list(by_name(section(dashboard, "pace")["by_team"], "team")) == ["Engineering", OTHER_SMALL_TEAMS, UNASSIGNED]


def test_the_team_view_shows_no_person_and_no_key_name(workspace: Path) -> None:
    dashboard = build_dashboard(workspace, load_settings(workspace), SEPTEMBER, EARLY_OCTOBER)

    text = json.dumps(dashboard)
    assert "@" not in text
    assert "backend" not in text
    assert "Sales" not in text
    assert "by_person" not in section(dashboard, "spend")


def test_emails_in_a_note_are_replaced() -> None:
    note = "couldn't place pat.lee@example.com (or dee.o'hara+ai@sub.example.co.uk), so check people.csv"

    assert without_emails(note) == "couldn't place someone (or someone), so check people.csv"


def test_the_person_view_shows_every_team_person_and_key(workspace: Path) -> None:
    use_person_view(workspace)

    dashboard = build_dashboard(workspace, load_settings(workspace), SEPTEMBER, EARLY_OCTOBER)

    spend = section(dashboard, "spend")
    assert list(by_name(spend["by_team"], "team")) == ["Engineering", "Sales", UNASSIGNED]
    assert by_name(spend["by_person"], "person")["pat.lee@example.com"]["total_cents"] == 12550
    assert as_list(spend["api_keys"]) == [
        {"api_key": "backend", "vendor": "anthropic", "owner": "sam.cho@example.com", "team": "Engineering",
         "total_cents": 30000}
    ]


def test_amounts_are_whole_cents_that_add_up_to_the_total(workspace: Path) -> None:
    dashboard = build_dashboard(workspace, load_settings(workspace), SEPTEMBER, EARLY_OCTOBER)

    spend = section(dashboard, "spend")
    teams = as_list(spend["by_team"])
    assert spend["total_cents"] == 53000
    assert sum(int(str(team["total_cents"])) for team in teams) == 53000
    engineering = teams[0]
    assert engineering == {"team": "Engineering", "seats_cents": 7500, "extra_usage_cents": 0, "api_cents": 30000,
                           "total_cents": 37500}


def test_a_spike_in_a_small_team_is_reported_under_other(workspace: Path) -> None:
    """Sales' one person runs a key at $10 a day for two weeks, then $40 a day."""
    total = 0
    for day in range(1, 22):
        total += 1000 if day <= 14 else 4000
        keys = ramp_keys_answer(ramp_key("reports", total, "pat.lee@example.com"))
        save_raw(workspace, RAMP_API_KEYS, OCTOBER.first_day, date(2026, 10, day), keys, saved_at=SAVED_AT)

    dashboard = build_dashboard(workspace, load_settings(workspace), OCTOBER, date(2026, 10, 22))

    assert (
        f"Spike: {OTHER_SMALL_TEAMS} spent up to $40.00 a day from 2026-10-15 to 2026-10-21, "
        "against a usual day of $10.00."
    ) in strings(dashboard["alerts"])
    assert "Sales" not in json.dumps(dashboard)


def test_change_from_last_month_folds_both_months_the_same_way(workspace: Path) -> None:
    keys = ramp_keys_answer(ramp_key("backend", 5000, "sam.cho@example.com"))
    save_raw(workspace, RAMP_API_KEYS, OCTOBER.first_day, date(2026, 10, 4), keys, saved_at=SAVED_AT)

    dashboard = build_dashboard(workspace, load_settings(workspace), OCTOBER, EARLY_OCTOBER)

    change = section(dashboard, "change_from_last_month")
    teams = by_name(change["by_team"], "team")
    assert teams[OTHER_SMALL_TEAMS] == {"team": OTHER_SMALL_TEAMS, "previous_cents": 12550, "current_cents": 2500}
    assert teams["Engineering"] == {"team": "Engineering", "previous_cents": 37500, "current_cents": 12500}
    assert "2026-10's api only runs through 2026-10-04, so it's compared with more of 2026-09" in strings(change["notes"])


def test_the_dashboard_command_writes_the_file(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["dashboard", str(workspace), "2026-09"]) == 0

    written = json.loads((workspace / "out/2026-09/dashboard.json").read_text(encoding="utf-8"))
    assert written["about"]["view"] == "team"
    assert "Wrote out/2026-09/dashboard.json (teams only, small ones folded together)" in capsys.readouterr().out


def test_a_month_that_has_not_started_has_no_dashboard(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["dashboard", str(workspace), "2099-01"]) == 1

    assert "2099-01 hasn't started yet" in capsys.readouterr().err
