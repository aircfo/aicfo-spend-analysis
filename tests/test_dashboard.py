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
SUPPORT = ("lu.chen@example.com", "ray.diaz@example.com")
TEAMS = {**{email: "Engineering" for email in ENGINEERS}, "pat.lee@example.com": "Sales", **{email: "Support" for email in SUPPORT}}


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


def acme(tmp_path: Path, teams: dict[str, str]) -> Path:
    """Acme in September: each person in teams holds a $25 seat, and one more seat was bought.

    Engineering's key spends $300 of API and Sales' one person $100.50 of extra usage. Someone
    with no team spends $4.50, so Unassigned is $29.50 with the empty seat.
    """
    seat_holders = "".join(f"{email},,,anthropic,standard\n" for email in teams)
    folder = new_workspace(tmp_path, seat_holders, with_ramp_people=False)
    (folder / SETTINGS_FILE_NAME).write_text(
        '{"company_name": "Acme", "seats": {"anthropic": {"standard": {"price_usd": 25, "bought": %d}}}}'
        % (len(teams) + 1),
        encoding="utf-8",
    )
    people = ramp_people_answer(*(ramp_user(email, team) for email, team in teams.items()))
    save_raw(folder, RAMP_PEOPLE, date(2026, 10, 1), date(2026, 10, 1), people, saved_at=SAVED_AT)
    save_month(
        folder,
        SEPTEMBER,
        backend_cents=30000,
        extra_usage=(("pat.lee@example.com", "claude-opus", "100.50"), ("new.hire@example.com", "claude-sonnet", "4.50")),
    )
    return folder


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Engineering $375.00 (3 seats and the API), Sales $125.50 (1 seat and extra usage),
    Support $50.00 (2 seats), Unassigned $29.50: $580.00 in all. Sales and Support fold
    into Other, which then holds 3 people.
    """
    return acme(tmp_path, TEAMS)


def test_the_team_view_folds_teams_fewer_than_three_people_spent_in(workspace: Path) -> None:
    dashboard = build_dashboard(workspace, load_settings(workspace), SEPTEMBER, EARLY_OCTOBER)

    teams = by_name(section(dashboard, "spend")["by_team"], "team")
    assert list(teams) == ["Engineering", OTHER_SMALL_TEAMS, UNASSIGNED]
    assert teams[OTHER_SMALL_TEAMS]["total_cents"] == 17550
    assert list(by_name(section(dashboard, "forecast")["by_team"], "team")) == ["Engineering", OTHER_SMALL_TEAMS, UNASSIGNED]
    assert list(by_name(section(dashboard, "pace")["by_team"], "team")) == ["Engineering", OTHER_SMALL_TEAMS, UNASSIGNED]


def test_other_always_holds_at_least_three_people(tmp_path: Path) -> None:
    """With Sales' one person as the only small team, Other would be that person, so Engineering joins it."""
    folder = acme(tmp_path, {email: team for email, team in TEAMS.items() if team != "Support"})

    dashboard = build_dashboard(folder, load_settings(folder), SEPTEMBER, EARLY_OCTOBER)

    assert list(by_name(section(dashboard, "spend")["by_team"], "team")) == [OTHER_SMALL_TEAMS, UNASSIGNED]


def test_the_team_view_shows_no_person_and_no_key_name(workspace: Path) -> None:
    dashboard = build_dashboard(workspace, load_settings(workspace), SEPTEMBER, EARLY_OCTOBER)

    text = json.dumps(dashboard)
    assert "@" not in text
    assert "backend" not in text
    assert "Sales" not in text
    assert "Support" not in text
    assert "by_person" not in section(dashboard, "spend")


def test_the_seat_types_settings_price_are_listed_for_the_seats_roster(workspace: Path) -> None:
    dashboard = build_dashboard(workspace, load_settings(workspace), SEPTEMBER, EARLY_OCTOBER)

    assert dashboard["seat_types"] == {"anthropic": [{"seat_type": "standard", "bought": len(TEAMS) + 1}]}


def test_emails_in_a_note_are_replaced() -> None:
    note = "couldn't place pat.lee@example.com (or dee.o'hara+ai@sub.example.co.uk), so check people.csv"

    assert without_emails(note) == "couldn't place someone (or someone), so check people.csv"


def test_the_person_view_shows_every_team_person_and_key(workspace: Path) -> None:
    use_person_view(workspace)

    dashboard = build_dashboard(workspace, load_settings(workspace), SEPTEMBER, EARLY_OCTOBER)

    spend = section(dashboard, "spend")
    assert list(by_name(spend["by_team"], "team")) == ["Engineering", "Sales", "Support", UNASSIGNED]
    assert by_name(spend["by_person"], "person")["pat.lee@example.com"]["total_cents"] == 12550
    assert as_list(spend["api_keys"]) == [
        {"api_key": "backend", "vendor": "anthropic", "owner": "sam.cho@example.com", "team": "Engineering",
         "total_cents": 30000}
    ]


def test_amounts_are_whole_cents_that_add_up_to_the_total(workspace: Path) -> None:
    dashboard = build_dashboard(workspace, load_settings(workspace), SEPTEMBER, EARLY_OCTOBER)

    spend = section(dashboard, "spend")
    teams = as_list(spend["by_team"])
    assert spend["total_cents"] == 58000
    assert sum(int(str(team["total_cents"])) for team in teams) == 58000
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


def test_a_note_about_a_shrinking_download_names_no_small_team(workspace: Path) -> None:
    for day, cents in ((3, 5000), (4, 3000)):
        keys = ramp_keys_answer(ramp_key("reports", cents, "pat.lee@example.com"))
        save_raw(workspace, RAMP_API_KEYS, OCTOBER.first_day, date(2026, 10, day), keys, saved_at=SAVED_AT)

    dashboard = build_dashboard(workspace, load_settings(workspace), OCTOBER, EARLY_OCTOBER)

    assert any(f"shows less than the download before it for {OTHER_SMALL_TEAMS}" in note
               for note in strings(dashboard["notes"]))
    assert "Sales" not in json.dumps(dashboard)


def test_change_from_last_month_folds_both_months_the_same_way(workspace: Path) -> None:
    keys = ramp_keys_answer(ramp_key("backend", 5000, "sam.cho@example.com"))
    save_raw(workspace, RAMP_API_KEYS, OCTOBER.first_day, date(2026, 10, 4), keys, saved_at=SAVED_AT)

    dashboard = build_dashboard(workspace, load_settings(workspace), OCTOBER, EARLY_OCTOBER)

    change = section(dashboard, "change_from_last_month")
    teams = by_name(change["by_team"], "team")
    assert teams[OTHER_SMALL_TEAMS] == {"team": OTHER_SMALL_TEAMS, "previous_cents": 17550, "current_cents": 7500}
    assert teams["Engineering"] == {"team": "Engineering", "previous_cents": 37500, "current_cents": 12500}
    assert "2026-10's api only runs through 2026-10-04, so it's compared with more of 2026-09" in strings(change["notes"])


def test_a_team_spelled_differently_last_month_is_one_team(workspace: Path) -> None:
    respelled = ramp_people_answer(*(ramp_user(email, team.lower()) for email, team in TEAMS.items()))
    save_raw(workspace, RAMP_PEOPLE, date(2026, 10, 1), date(2026, 10, 1), respelled,
             saved_at=SAVED_AT.replace(hour=10))
    october = ramp_people_answer(*(ramp_user(email, team) for email, team in TEAMS.items()))
    save_raw(workspace, RAMP_PEOPLE, EARLY_OCTOBER, EARLY_OCTOBER, october, saved_at=SAVED_AT.replace(day=5))

    dashboard = build_dashboard(workspace, load_settings(workspace), OCTOBER, EARLY_OCTOBER)

    teams = by_name(section(dashboard, "change_from_last_month")["by_team"], "team")
    assert teams["Engineering"] == {"team": "Engineering", "previous_cents": 37500, "current_cents": 7500}
    assert "engineering" not in teams


def test_the_dashboard_command_writes_the_file(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["dashboard", str(workspace), "2026-09"]) == 0

    written = json.loads((workspace / "out/2026-09/dashboard.json").read_text(encoding="utf-8"))
    assert written["about"]["view"] == "team"
    assert "Wrote out/2026-09/dashboard.json (teams only, small ones folded together)" in capsys.readouterr().out


def test_a_month_that_has_not_started_has_no_dashboard(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["dashboard", str(workspace), "2099-01"]) == 1

    assert "2099-01 hasn't started yet" in capsys.readouterr().err
