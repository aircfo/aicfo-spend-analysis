from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from aicfo_spend.cli import main
from aicfo_spend.forecast import describe_score, forecast_after
from aicfo_spend.months import Month
from aicfo_spend.report import build_month, total
from aicfo_spend.settings import load_settings
from aicfo_spend.spend_table import PRODUCT_API, PRODUCT_EXTRA_USAGE, PRODUCT_SEATS, UNASSIGNED
from tests.workspaces import SEPTEMBER, fill_september, new_workspace, save_month

JUNE, JULY, AUGUST = Month(2026, 6), Month(2026, 7), Month(2026, 8)
AUG_31 = AUGUST.last_day


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """API for June to September ($90, $120, $150, then $300); extra usage only for August ($60) and September.

    Forecast for September as of Aug 31: Engineering $25 seat + $120 average API = $145,
    Sales $25 seat + $60 extra usage = $85, Unassigned $25 empty seat. Total $255.
    September's actual is $483.
    """
    folder = new_workspace(tmp_path)
    save_month(folder, JUNE, backend_cents=9000)
    save_month(folder, JULY, backend_cents=12000)
    save_month(folder, AUGUST, backend_cents=15000, extra_usage=(("pat.lee@example.com", "claude-opus", "60"),))
    fill_september(folder)
    return folder


def test_averages_each_usage_product_over_the_months_that_have_it(workspace: Path) -> None:
    forecast = forecast_after(workspace, load_settings(workspace), AUG_31)

    assert forecast.month == SEPTEMBER
    assert forecast.history == {PRODUCT_EXTRA_USAGE: [AUGUST], PRODUCT_API: [JUNE, JULY, AUGUST]}
    assert forecast.by_team["Engineering"][PRODUCT_API] == Decimal("120")
    assert forecast.by_team["Sales"][PRODUCT_EXTRA_USAGE] == Decimal("60")
    assert forecast.by_team["Sales"][PRODUCT_SEATS] == Decimal("25")
    assert total(forecast.by_team[UNASSIGNED]) == Decimal("25")


def test_uses_nothing_after_the_as_of_date(workspace: Path) -> None:
    forecast = forecast_after(workspace, load_settings(workspace), date(2026, 8, 30))

    # August isn't complete on Aug 30, so June and July carry the API average, and no extra usage history exists.
    assert forecast.history == {PRODUCT_EXTRA_USAGE: [], PRODUCT_API: [JUNE, JULY]}
    assert forecast.by_team["Engineering"][PRODUCT_API] == Decimal("105")
    assert "no complete month of extra usage is saved before 2026-08-30, so it's forecast at $0" in forecast.notes


def test_a_month_still_in_progress_is_left_out_of_the_averages(workspace: Path) -> None:
    forecast = forecast_after(workspace, load_settings(workspace), date(2026, 9, 15))

    assert forecast.month == Month(2026, 10)
    assert forecast.history[PRODUCT_API] == [JUNE, JULY, AUGUST]


def test_score_says_how_far_off_the_forecast_was(workspace: Path) -> None:
    settings = load_settings(workspace)
    forecast = forecast_after(workspace, settings, AUG_31)

    score = describe_score(forecast, build_month(workspace, SEPTEMBER, settings))

    lines = score.splitlines()
    assert lines[0] == (
        "The forecast for 2026-09 was $255.00; actual spend was $483.00. It was off by -$228.00 (-47.2%)."
    )
    assert next(line for line in lines if line.startswith("Engineering")).split() == [
        "Engineering", "$145.00", "$325.00", "-$180.00", "-55.4%"
    ]
    assert "  - extra usage was averaged over 1 month of history, not 3; the score is a thin read" in lines


def test_forecast_command_prints_the_forecast_and_its_score(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["forecast", str(workspace), "--as-of", "2026-08-31", "--score", "2026-09"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("Forecast for 2026-09, as of 2026-08-31")
    assert "API: each team's average over 2026-06 to 2026-08 (3 months)." in out
    assert "The forecast for 2026-09 was $255.00" in out


def test_forecast_command_scores_only_the_month_it_forecasts(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["forecast", str(workspace), "--as-of", "2026-08-31", "--score", "2026-10"]) == 1

    assert "is for 2026-09; score it against that month" in capsys.readouterr().err
