import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from aicfo_spend.assign import Directory
from aicfo_spend.cli import main
from aicfo_spend.daily import daily_usage
from aicfo_spend.errors import InputError
from aicfo_spend.months import Month
from aicfo_spend.pace import pace_of
from aicfo_spend.raw_store import RAMP_API_KEYS, RAMP_PROVIDER_TOTALS, save_raw
from aicfo_spend.settings import load_settings
from aicfo_spend.spend_table import PRODUCT_API, UNASSIGNED
from tests.factories import ramp_key, ramp_keys_answer, ramp_totals_answer
from tests.workspaces import SAVED_AT, SEPTEMBER, new_workspace, save_month

OCTOBER = Month(2026, 10)
TODAY = date(2026, 10, 22)


def save_october_api(folder: Path, through_day: int, cents: int, saved_at: datetime = SAVED_AT) -> None:
    keys = ramp_keys_answer(ramp_key("backend", cents, "sam.cho@example.com"))
    save_raw(folder, RAMP_API_KEYS, OCTOBER.first_day, date(2026, 10, through_day), keys, saved_at=saved_at)


def save_october_totals(folder: Path, through_day: int, cents: int) -> None:
    totals = ramp_totals_answer(ANTHROPIC=f"{cents / 100}")
    save_raw(folder, RAMP_PROVIDER_TOTALS, OCTOBER.first_day, date(2026, 10, through_day), totals, saved_at=SAVED_AT)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A download every day: Engineering's API runs $10 a day Oct 1-14, then $40 a day Oct 15-21.

    September is one whole-month download of $300. October so far: seats $75 + API $420 =
    $495. On pace for: the last 14 days average $25 a day, times 10 days left, so API $670
    and $745 in all. Plan (as of Sep 30): seats $75 + September's API $300 = $375.
    """
    folder = new_workspace(tmp_path)
    save_month(folder, SEPTEMBER, backend_cents=30000)
    total = 0
    for day in range(1, 22):
        total += 1000 if day <= 14 else 4000
        save_october_api(folder, day, total)
    return folder


def test_spreads_the_change_between_downloads_over_the_days_between(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    save_october_api(folder, 10, 10000)
    save_october_api(folder, 12, 16000)

    series = daily_usage(folder, OCTOBER, Directory.build({}, []), [])[PRODUCT_API]

    # The empty directory knows no one, so the key's spend is Unassigned.
    assert series.days[date(2026, 10, 1)] == {UNASSIGNED: Decimal(10)}
    assert series.days[date(2026, 10, 11)] == {UNASSIGNED: Decimal(30)}
    assert series.observed == frozenset()
    assert series.latest == {UNASSIGNED: Decimal("160")}


def test_a_download_showing_less_than_the_one_before_counts_as_zero(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    save_october_api(folder, 9, 10000)
    save_october_api(folder, 10, 8000)
    notes: list[str] = []

    series = daily_usage(folder, OCTOBER, Directory.build({}, []), notes)[PRODUCT_API]

    assert series.days[date(2026, 10, 10)] == {UNASSIGNED: Decimal(0)}
    assert series.latest == {UNASSIGNED: Decimal("80")}
    assert any("shows less than the download before it for Unassigned" in note for note in notes)


def test_projects_the_month_from_the_last_14_days(workspace: Path) -> None:
    pace = pace_of(workspace, load_settings(workspace), OCTOBER, TODAY)

    assert pace.through == {PRODUCT_API: date(2026, 10, 21)}
    assert pace.so_far == {"Engineering": Decimal("445.00"), "Sales": Decimal("25.00"), UNASSIGNED: Decimal("25.00")}
    assert pace.projected["Engineering"] == Decimal("695.00")
    assert sum(pace.projected.values()) == Decimal("745.00")


def test_flags_a_run_of_spike_days_as_one_alert(workspace: Path) -> None:
    pace = pace_of(workspace, load_settings(workspace), OCTOBER, TODAY)

    assert (
        "Spike: Engineering spent up to $40.00 a day from 2026-10-15 to 2026-10-21, against a usual day of $10.00."
        in pace.alerts
    )


def test_days_spread_over_a_gap_neither_spike_nor_set_what_is_normal(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    # First download on Oct 8 spreads $800 over eight days; Oct 9's $240 is a real reading.
    save_october_api(folder, 8, 80000)
    save_october_api(folder, 9, 104000)

    pace = pace_of(folder, load_settings(folder), OCTOBER, TODAY)

    assert not any(alert.startswith("Spike") for alert in pace.alerts)
    assert any(note.startswith("8 of 9 days this month are spread evenly") for note in pace.notes)


def test_flags_a_month_on_pace_well_over_its_plan(workspace: Path) -> None:
    pace = pace_of(workspace, load_settings(workspace), OCTOBER, TODAY)

    assert pace.plan is not None and pace.plan.total == Decimal("375.00")
    assert (
        "Over pace: 2026-10 is on pace for $745.00, 99% above the $375.00 forecast for it made as of 2026-09-30."
        in pace.alerts
    )


def test_the_plan_is_saved_once_and_never_moves(workspace: Path) -> None:
    pace_of(workspace, load_settings(workspace), OCTOBER, TODAY)
    saved = json.loads((workspace / "out/2026-10/plan.json").read_text(encoding="utf-8"))
    # A revised September download would change a fresh forecast, but not the saved plan.
    revised = ramp_keys_answer(ramp_key("backend", 90000, "sam.cho@example.com"))
    later = SAVED_AT + timedelta(hours=1)
    save_raw(workspace, RAMP_API_KEYS, SEPTEMBER.first_day, SEPTEMBER.last_day, revised, saved_at=later)

    pace = pace_of(workspace, load_settings(workspace), OCTOBER, date(2026, 10, 25))

    assert (saved["made_as_of"], saved["saved_on"]) == ("2026-09-30", "2026-10-22")
    assert pace.plan is not None and pace.plan.total == Decimal(saved["total_usd"]) == Decimal("375.00")


def test_no_plan_is_saved_without_history_to_base_it_on(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    save_october_api(folder, 21, 42000)

    pace = pace_of(folder, load_settings(folder), OCTOBER, TODAY)

    assert pace.plan is None
    assert not any(alert.startswith("Over pace") for alert in pace.alerts)
    assert "no plan yet: no complete month of api is saved before 2026-09-30, so over pace isn't checked" in pace.notes
    assert not (folder / "out/2026-10/plan.json").exists()


def test_no_plan_is_saved_on_the_1st_while_a_saved_product_lacks_history(tmp_path: Path) -> None:
    """A workspace started late in October: on Nov 1 nothing is saved for November yet."""
    folder = new_workspace(tmp_path)
    save_october_api(folder, 26, 42000)

    pace = pace_of(folder, load_settings(folder), Month(2026, 11), date(2026, 11, 1))

    assert pace.plan is None
    assert "no plan yet: no complete month of api is saved before 2026-10-31, so over pace isn't checked" in pace.notes
    assert not (folder / "out/2026-11/plan.json").exists()


def test_no_plan_is_saved_for_a_month_that_has_not_started(workspace: Path) -> None:
    pace = pace_of(workspace, load_settings(workspace), Month(2026, 11), TODAY)

    assert pace.plan is None
    assert "no plan yet: 2026-11 hasn't started, so over pace isn't checked" in pace.notes


def test_a_damaged_plan_says_what_removing_it_does(workspace: Path) -> None:
    plan_path = workspace / "out/2026-10/plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text("", encoding="utf-8")

    with pytest.raises(InputError, match="next check save a new plan"):
        pace_of(workspace, load_settings(workspace), OCTOBER, TODAY)


def test_a_month_with_nothing_saved_projects_seats_only(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)

    pace = pace_of(folder, load_settings(folder), OCTOBER, TODAY)

    assert sum(pace.projected.values()) == Decimal("75.00")
    assert pace.alerts == []
    assert "no api is saved for 2026-10 yet, so the projection leaves it out" in pace.notes


def test_pace_command_rewrites_the_alert_drafts_every_time(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    drafts = workspace / "out/2026-10/alerts.txt"

    assert main(["pace", str(workspace), "2026-10"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("2026-10 through 2026-10-21: $495.00 so far, on pace for $745.00")
    assert drafts.read_text(encoding="utf-8").startswith("Spike: Engineering")

    assert main(["pace", str(workspace), "2026-12"]) == 0
    assert (workspace / "out/2026-12/alerts.txt").read_text(encoding="utf-8") == "No alerts.\n"


def test_spend_no_current_key_carries_counts_so_far_but_is_never_projected_or_a_spike(workspace: Path) -> None:
    """On the 21st a $150 key is deleted: its spend leaves the keys and shows only in Ramp's total."""
    save_october_api(workspace, 21, 42000 - 15000, saved_at=SAVED_AT.replace(hour=10))
    save_october_totals(workspace, 21, 42000)

    pace = pace_of(workspace, load_settings(workspace), OCTOBER, TODAY)

    assert pace.so_far[UNASSIGNED] == Decimal("175.00")  # the empty seat and the untied $150
    assert pace.projected[UNASSIGNED] == Decimal("175.00")
    assert not any(alert.startswith("Spike: Unassigned") for alert in pace.alerts)
    assert sum(pace.so_far.values()) == Decimal("75.00") + Decimal("420.00")  # seats and Ramp's API total


def test_pace_leaves_untied_spend_out_when_the_newest_download_has_no_totals_like_the_report(workspace: Path) -> None:
    save_october_totals(workspace, 10, 10000 + 2000)

    pace = pace_of(workspace, load_settings(workspace), OCTOBER, TODAY)

    assert pace.so_far[UNASSIGNED] == Decimal("25.00")  # the empty seat only
    assert (
        "no Ramp provider totals are saved for 2026-10-01 to 2026-10-21, so API spend Ramp can't tie to a key isn't counted"
        in pace.notes
    )


def test_saved_on_is_today_not_the_month(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    save_month(folder, SEPTEMBER, backend_cents=30000)
    save_october_api(folder, 3, 3000, saved_at=datetime(2026, 10, 4, tzinfo=timezone.utc))

    pace = pace_of(folder, load_settings(folder), OCTOBER, date(2026, 10, 4))

    assert pace.plan is not None and pace.plan.saved_on == date(2026, 10, 4)
