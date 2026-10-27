import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from aicfo_spend.assign import Directory
from aicfo_spend.cli import main
from aicfo_spend.daily import daily_usage
from aicfo_spend.months import Month
from aicfo_spend.pace import pace_of
from aicfo_spend.raw_store import RAMP_API_KEYS, save_raw
from aicfo_spend.settings import load_settings
from aicfo_spend.spend_table import PRODUCT_API, UNASSIGNED
from tests.factories import ramp_key, ramp_keys_answer
from tests.workspaces import SAVED_AT, SEPTEMBER, new_workspace, save_month

OCTOBER = Month(2026, 10)


def save_october_api(folder: Path, through_day: int, cents: int) -> None:
    keys = ramp_keys_answer(ramp_key("backend", cents, "sam.cho@example.com"))
    save_raw(folder, RAMP_API_KEYS, OCTOBER.first_day, date(2026, 10, through_day), keys, saved_at=SAVED_AT)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Engineering's API: $10 a day through September and Oct 1-14, then $40 a day Oct 15-21.

    October so far: seats $75 + API $420 = $495. On pace for: the last 14 days average
    $25 a day, times 10 days left, so API $670 and $745 in all. Plan (as of Sep 30):
    seats $75 + September's API $300 = $375.
    """
    folder = new_workspace(tmp_path)
    save_month(folder, SEPTEMBER, backend_cents=30000)
    save_october_api(folder, 14, 14000)
    save_october_api(folder, 21, 42000)
    return folder


def test_spreads_the_change_between_downloads_over_the_days_between(workspace: Path) -> None:
    daily = daily_usage(workspace, OCTOBER, Directory.build({}, []), [])[PRODUCT_API]

    assert sorted(daily) == [date(2026, 10, day) for day in range(1, 22)]
    # The empty directory knows no one, so the key's spend is Unassigned.
    assert daily[date(2026, 10, 1)] == {UNASSIGNED: Decimal(10)}
    assert daily[date(2026, 10, 15)] == {UNASSIGNED: Decimal(40)}


def test_projects_the_month_from_the_last_14_days(workspace: Path) -> None:
    pace = pace_of(workspace, load_settings(workspace), OCTOBER)

    assert pace.through == {PRODUCT_API: date(2026, 10, 21)}
    assert pace.so_far == {"Engineering": Decimal("445.00"), "Sales": Decimal("25.00"), UNASSIGNED: Decimal("25.00")}
    assert pace.projected["Engineering"] == Decimal("695.00")
    assert sum(pace.projected.values()) == Decimal("745.00")


def test_flags_a_run_of_spike_days_as_one_alert(workspace: Path) -> None:
    pace = pace_of(workspace, load_settings(workspace), OCTOBER)

    assert (
        "Spike: Engineering spent up to $40.00 a day from 2026-10-15 to 2026-10-21, against a usual day of $10.00."
        in pace.alerts
    )


def test_flags_a_month_on_pace_well_over_its_plan(workspace: Path) -> None:
    pace = pace_of(workspace, load_settings(workspace), OCTOBER)

    assert pace.plan_total == Decimal("375.00")
    assert (
        "Over pace: 2026-10 is on pace for $745.00, 99% above the $375.00 forecast for it made as of 2026-09-30."
        in pace.alerts
    )


def test_the_plan_is_saved_once_and_never_moves(workspace: Path) -> None:
    pace_of(workspace, load_settings(workspace), OCTOBER)
    plan_path = workspace / "out/2026-10/plan.json"
    saved = json.loads(plan_path.read_text(encoding="utf-8"))
    # A revised September download would change a fresh forecast, but not the saved plan.
    revised = ramp_keys_answer(ramp_key("backend", 90000, "sam.cho@example.com"))
    later = SAVED_AT + timedelta(hours=1)
    save_raw(workspace, RAMP_API_KEYS, SEPTEMBER.first_day, SEPTEMBER.last_day, revised, saved_at=later)

    pace = pace_of(workspace, load_settings(workspace), OCTOBER)

    assert saved["made_as_of"] == "2026-09-30"
    assert pace.plan_total == Decimal(saved["total_usd"]) == Decimal("375.00")


def test_no_spike_without_a_week_of_history(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    save_october_api(folder, 3, 3000)
    save_october_api(folder, 4, 13000)

    pace = pace_of(folder, load_settings(folder), OCTOBER)

    assert not any(alert.startswith("Spike") for alert in pace.alerts)


def test_a_month_with_nothing_saved_projects_seats_only(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)

    pace = pace_of(folder, load_settings(folder), OCTOBER)

    assert sum(pace.projected.values()) == Decimal("75.00")
    assert pace.alerts == []
    assert "no api is saved for 2026-10 yet, so the projection leaves it out" in pace.notes


def test_pace_command_prints_the_month_and_saves_alert_drafts(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["pace", str(workspace), "2026-10"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("2026-10 through 2026-10-21: $495.00 so far, on pace for $745.00")
    assert "Alerts (drafts, not sent):" in out
    drafts = (workspace / "out/2026-10/alerts.txt").read_text(encoding="utf-8")
    assert drafts.startswith("Spike: Engineering")
