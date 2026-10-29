import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from aicfo_spend.cli import main
from aicfo_spend.forecast import describe_forecast, describe_score, forecast_after
from aicfo_spend.months import Month
from aicfo_spend.report import MonthSpend, build_month, total
from aicfo_spend.settings import SETTINGS_FILE_NAME, load_settings
from aicfo_spend.spend_table import PRODUCT_API, PRODUCT_EXTRA_USAGE, PRODUCT_SEATS, UNASSIGNED
from aicfo_spend.raw_store import CLAUDE_SPEND_EXPORT, RAMP_PEOPLE, save_raw
from tests.factories import ramp_people_answer, ramp_user, spend_export
from tests.workspaces import SAVED_AT, SEPTEMBER, fill_september, new_workspace, save_month

JUNE, JULY, AUGUST = Month(2026, 6), Month(2026, 7), Month(2026, 8)
AUG_31 = AUGUST.last_day


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """API for June to September ($90, $120, $150, then $300); extra usage only for August ($60) and September.

    Forecast for September as of Aug 31: Engineering $25 seat + $150 of August's API = $175,
    Sales $25 seat + $60 extra usage = $85, Unassigned $25 empty seat. Total $285.
    September's actual is $483.
    """
    folder = new_workspace(tmp_path)
    save_month(folder, JUNE, backend_cents=9000)
    save_month(folder, JULY, backend_cents=12000)
    save_month(folder, AUGUST, backend_cents=15000, extra_usage=(("pat.lee@example.com", "claude-opus", "60"),))
    fill_september(folder)
    return folder


def test_api_comes_from_the_last_complete_month_and_extra_usage_from_the_months_that_have_it(workspace: Path) -> None:
    forecast = forecast_after(workspace, load_settings(workspace), AUG_31)

    assert forecast.month == SEPTEMBER
    assert forecast.history == {PRODUCT_EXTRA_USAGE: [AUGUST], PRODUCT_API: [AUGUST]}
    assert forecast.by_team["Engineering"][PRODUCT_API] == Decimal("150")
    assert forecast.by_team["Sales"][PRODUCT_EXTRA_USAGE] == Decimal("60")
    assert forecast.by_team["Sales"][PRODUCT_SEATS] == Decimal("25")
    assert total(forecast.by_team[UNASSIGNED]) == Decimal("25")


def test_leaves_out_months_not_complete_by_the_as_of_date(workspace: Path) -> None:
    forecast = forecast_after(workspace, load_settings(workspace), date(2026, 8, 30))

    # August isn't complete on Aug 30, so July carries the API forecast, and no extra usage history exists.
    assert forecast.history == {PRODUCT_EXTRA_USAGE: [], PRODUCT_API: [JULY]}
    assert forecast.by_team["Engineering"][PRODUCT_API] == Decimal("120")
    assert "no complete month of extra usage is saved before 2026-08-30, so it's forecast at $0" in forecast.notes


def test_a_month_still_in_progress_is_left_out_of_the_averages(workspace: Path) -> None:
    forecast = forecast_after(workspace, load_settings(workspace), date(2026, 9, 15))

    assert forecast.month == Month(2026, 10)
    assert forecast.history[PRODUCT_API] == [AUGUST]


def test_score_says_how_far_off_the_forecast_was(workspace: Path) -> None:
    settings = load_settings(workspace)
    forecast = forecast_after(workspace, settings, AUG_31)

    score = describe_score(forecast, build_month(workspace, SEPTEMBER, settings))

    lines = score.splitlines()
    assert lines[0] == (
        "The forecast for 2026-09 was $285.00; actual spend was $483.00. It was off by -$198.00 (-41.0%)."
    )
    assert next(line for line in lines if line.startswith("Engineering")).split() == [
        "Engineering", "$175.00", "$325.00", "-$150.00", "-46.2%"
    ]
    assert "  - extra usage was averaged over 1 month of history, not 3; the score is a thin read" in lines


def test_forecast_command_prints_the_forecast_and_its_score(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["forecast", str(workspace), "--as-of", "2026-08-31", "--score", "2026-09"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("Forecast for 2026-09, as of 2026-08-31")
    assert "API: each team's spend on current keys in 2026-08." in out
    assert "The forecast for 2026-09 was $285.00" in out


def test_forecast_command_scores_only_the_month_it_forecasts(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["forecast", str(workspace), "--as-of", "2026-08-31", "--score", "2026-10"]) == 1

    assert "is for 2026-09; score it against that month" in capsys.readouterr().err


def test_rounds_each_teams_forecast_to_cents_so_rows_add_up(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    for month, dollars in ((JUNE, "100"), (JULY, "100"), (AUGUST, "100.01")):
        save_month(folder, month, extra_usage=(("pat.lee@example.com", "claude-opus", dollars),))

    forecast = forecast_after(folder, load_settings(folder), AUG_31)

    assert forecast.by_team["Sales"][PRODUCT_EXTRA_USAGE] == Decimal("100.00")
    assert all(amount.as_tuple().exponent == -2 for amounts in forecast.by_team.values() for amount in amounts.values())


def test_merges_a_team_spelled_differently_in_an_older_people_list(workspace: Path) -> None:
    # June's spend is placed with a list saved on Jul 1, which spells the team in lower case.
    july_first = date(2026, 7, 1)
    lower = ramp_people_answer(ramp_user("pat.lee@example.com", "sales"), ramp_user("sam.cho@example.com", "engineering"))
    save_raw(workspace, RAMP_PEOPLE, july_first, july_first, lower, saved_at=SAVED_AT)
    save_raw(workspace, CLAUDE_SPEND_EXPORT, JUNE.first_day, JUNE.last_day,
             spend_export(("pat.lee@example.com", "claude-opus", "30")), saved_at=SAVED_AT)

    forecast = forecast_after(workspace, load_settings(workspace), AUG_31)

    assert "sales" not in forecast.by_team
    assert forecast.by_team["Sales"][PRODUCT_EXTRA_USAGE] == Decimal("45.00")


def test_score_notes_an_actual_month_that_isnt_finished(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    save_month(folder, AUGUST, backend_cents=15000)
    forecast = forecast_after(folder, load_settings(folder), AUG_31)
    partial = MonthSpend(SEPTEMBER, rows=[], notes=[], coverage={PRODUCT_API: (SEPTEMBER.first_day, date(2026, 9, 17))})

    score = describe_score(forecast, partial)

    assert "  - 2026-09's api only runs through 2026-09-17, so its actual is partial" in score.splitlines()


def test_api_spend_no_current_key_carries_is_left_out_because_a_deleted_key_cannot_spend_again(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    save_month(folder, AUGUST, backend_cents=15000, untied_cents=5000)

    forecast = forecast_after(folder, load_settings(folder), AUG_31)

    assert forecast.by_team["Engineering"][PRODUCT_API] == Decimal("150.00")
    assert forecast.by_team[UNASSIGNED][PRODUCT_API] == Decimal("0")


def with_known_changes(folder: Path, *changes: dict[str, object]) -> None:
    settings = json.loads((folder / SETTINGS_FILE_NAME).read_text(encoding="utf-8"))
    (folder / SETTINGS_FILE_NAME).write_text(json.dumps({**settings, "known_changes": list(changes)}), encoding="utf-8")


def test_a_known_change_for_the_month_is_added_and_said(workspace: Path) -> None:
    with_known_changes(workspace,
                       {"month": "2026-09", "team": "Engineering", "product": "api", "amount_usd": 200, "note": "new review bot"},
                       {"month": "2026-10", "team": "Engineering", "product": "api", "amount_usd": 999})

    forecast = forecast_after(workspace, load_settings(workspace), AUG_31)

    assert forecast.by_team["Engineering"][PRODUCT_API] == Decimal("350.00")
    assert "includes a known change: +$200.00 of api for Engineering (new review bot)" in describe_forecast(forecast)


def test_a_known_change_can_be_a_cut(workspace: Path) -> None:
    with_known_changes(workspace, {"month": "2026-09", "team": "engineering", "product": "api", "amount_usd": -100})

    forecast = forecast_after(workspace, load_settings(workspace), AUG_31)

    assert forecast.by_team["Engineering"][PRODUCT_API] == Decimal("50.00")
