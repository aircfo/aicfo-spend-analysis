import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from aicfo_spend.cli import main
from aicfo_spend.dashboard import build_dashboard
from aicfo_spend.errors import InputError
from aicfo_spend.months import Month
from aicfo_spend.raw_store import CLAUDE_SPEND_EXPORT, save_raw
from aicfo_spend.recommendations import (
    BUNDLES,
    CHECK_IN,
    DECLINED,
    DONE,
    DOWNGRADE,
    LEDGER_FILE_NAME,
    RECLAIM,
    UPGRADE,
    add_new,
    bundle_plan,
    decide,
    find_recommendations,
    outcomes,
    read_ledger,
    write_ledger,
)
from aicfo_spend.settings import SETTINGS_FILE_NAME, load_settings
from aicfo_spend.workspace import PEOPLE_FILE_NAME, create_workspace
from tests.factories import usage_export
from tests.workspaces import PEOPLE_CSV_HEADER, SAVED_AT

JULY, AUGUST, SEPTEMBER, OCTOBER = Month(2026, 7), Month(2026, 8), Month(2026, 9), Month(2026, 10)
TODAY = date(2026, 10, 5)
ROSTER = (
    "heavy@example.com,,,anthropic,standard\n"
    "steady@example.com,,,anthropic,standard\n"
    "light@example.com,,,anthropic,premium\n"
    "idle@example.com,,,anthropic,standard\n"
    "rare@example.com,,,anthropic,standard\n"
)
# Per month: (email, requests, fresh tokens, extra usage dollars).
NORMAL_MONTH = [
    ("steady@example.com", 900, 40000, "0"),
    ("light@example.com", 300, 9000, "0"),
    ("rare@example.com", 20, 500, "0"),
]


def company(tmp_path: Path) -> Path:
    """Standard seats at $25 and Premium at $125, so an upgrade costs $100 more a month."""
    folder = tmp_path / "acme"
    create_workspace(folder, "Acme")
    (folder / SETTINGS_FILE_NAME).write_text(
        '{"seats": {"anthropic": {"standard": {"price_usd": 25, "bought": 4}, "premium": {"price_usd": 125, "bought": 1}}}}',
        encoding="utf-8",
    )
    (folder / PEOPLE_FILE_NAME).write_text(PEOPLE_CSV_HEADER + ROSTER, encoding="utf-8")
    return folder


def save_usage(folder: Path, month: Month, rows: list[tuple[str, int, int, str]], through: date | None = None) -> None:
    export = usage_export(*((email, "Chat", requests, fresh, 0, 0, extra) for email, requests, fresh, extra in rows))
    save_raw(folder, CLAUDE_SPEND_EXPORT, month.first_day, through or month.last_day, export, saved_at=SAVED_AT)


def with_standard_seats_bought(folder: Path, bought: int) -> None:
    settings = json.loads((folder / SETTINGS_FILE_NAME).read_text(encoding="utf-8"))
    settings["seats"]["anthropic"]["standard"]["bought"] = bought
    (folder / SETTINGS_FILE_NAME).write_text(json.dumps(settings), encoding="utf-8")


def three_months(folder: Path) -> None:
    save_usage(folder, JULY, [*NORMAL_MONTH, ("heavy@example.com", 4000, 90000, "150"), ("idle@example.com", 40, 800, "0")])
    save_usage(folder, AUGUST, [*NORMAL_MONTH, ("heavy@example.com", 3000, 70000, "90")])
    save_usage(folder, SEPTEMBER, [*NORMAL_MONTH, ("heavy@example.com", 4500, 95000, "180")])


def found_in(folder: Path) -> dict[str, str]:
    found, _ = find_recommendations(folder, load_settings(folder), SEPTEMBER, TODAY)
    return {item.person or "company": item.action for item in found if item.action != CHECK_IN} | {
        item.person + " (check in)": item.action for item in found if item.action == CHECK_IN
    }


def test_each_rule_finds_its_seat_from_three_whole_months(tmp_path: Path) -> None:
    folder = company(tmp_path)
    three_months(folder)

    assert found_in(folder) == {
        "heavy@example.com": UPGRADE,
        "light@example.com": DOWNGRADE,
        "idle@example.com": RECLAIM,
        "rare@example.com (check in)": CHECK_IN,
        "company": BUNDLES,
    }


def test_an_upgrade_saves_at_most_its_average_extra_usage_less_the_price_gap(tmp_path: Path) -> None:
    folder = company(tmp_path)
    three_months(folder)

    found, _ = find_recommendations(folder, load_settings(folder), SEPTEMBER, TODAY)
    upgrade = next(item for item in found if item.action == UPGRADE)

    assert (upgrade.baseline_usd, upgrade.monthly_usd) == (Decimal("140.00"), Decimal("40.00"))
    assert "the most it could be" in upgrade.why


def test_one_heavy_month_isnt_enough_to_upgrade(tmp_path: Path) -> None:
    folder = company(tmp_path)
    save_usage(folder, JULY, [*NORMAL_MONTH, ("heavy@example.com", 4000, 90000, "150")])
    save_usage(folder, AUGUST, [*NORMAL_MONTH, ("heavy@example.com", 2000, 50000, "40")])
    save_usage(folder, SEPTEMBER, [*NORMAL_MONTH, ("heavy@example.com", 2000, 50000, "60")])

    assert "heavy@example.com" not in found_in(folder)


def test_a_premium_seat_that_pays_extra_isnt_a_downgrade(tmp_path: Path) -> None:
    folder = company(tmp_path)
    three_months(folder)
    save_usage(folder, OCTOBER, [*NORMAL_MONTH[:1], ("light@example.com", 300, 9000, "5")])

    found, _ = find_recommendations(folder, load_settings(folder), OCTOBER, TODAY)

    assert DOWNGRADE not in [item.action for item in found]


def test_needs_two_whole_months_and_a_month_saved_in_part_doesnt_count(tmp_path: Path) -> None:
    folder = company(tmp_path)
    save_usage(folder, AUGUST, NORMAL_MONTH)
    save_usage(folder, SEPTEMBER, NORMAL_MONTH, through=date(2026, 9, 20))

    found, notes = find_recommendations(folder, load_settings(folder), SEPTEMBER, TODAY)

    assert found == []
    assert notes == ["seat recommendations need at least 2 months of the claude.ai spend export "
                     "(whole months to 2026-09, or 2026-10 from the 14th on); 1 is saved"]


def test_bundles_cover_the_smallest_month_biggest_first() -> None:
    assert bundle_plan(Decimal("1340")) == (Decimal(355), "1 × $1,000.00 for $700.00 and 1 × $250.00 for $200.00 and 1 × $50.00 for $45.00")
    assert bundle_plan(Decimal("49")) == (Decimal(0), "")


def test_bundles_stop_at_three_thousand_dollars_of_credit_a_month() -> None:
    assert bundle_plan(Decimal("8000"))[0] == Decimal(900)


def test_an_open_recommendation_isnt_added_twice_and_a_declined_one_returns_after_ninety_days(tmp_path: Path) -> None:
    folder = company(tmp_path)
    three_months(folder)
    found, _ = find_recommendations(folder, load_settings(folder), SEPTEMBER, TODAY)
    ledger, added = add_new([], found)

    again, added_again = add_new(ledger, found)
    declined = decide(again, added[0].id, DECLINED, TODAY, "")
    in_a_month = [replace(item, made_on=date(2026, 11, 5)) for item in found]
    in_four_months = [replace(item, made_on=date(2027, 2, 5)) for item in found]

    assert [item.id for item in added] == [f"R{number}" for number in range(1, len(found) + 1)]
    assert added_again == []
    assert add_new(declined, in_a_month)[1] == []
    assert [item.person for item in add_new(declined, in_four_months)[1]] == [added[0].person]


def test_the_ledger_reads_back_what_it_wrote(tmp_path: Path) -> None:
    folder = company(tmp_path)
    three_months(folder)
    found, _ = find_recommendations(folder, load_settings(folder), SEPTEMBER, TODAY)
    ledger = decide(add_new([], found)[0], "r1", DONE, TODAY, "moved to Premium")

    write_ledger(folder, ledger)

    assert read_ledger(folder) == ledger


def test_marking_an_unknown_recommendation_says_what_ids_look_like() -> None:
    with pytest.raises(InputError, match="has no recommendation R9; its ids are like R1"):
        decide([], "R9", DONE, TODAY, "")


def test_a_reclaimed_seat_saves_its_price_each_whole_month_until_it_is_given_back(tmp_path: Path) -> None:
    folder = company(tmp_path)
    three_months(folder)
    found, _ = find_recommendations(folder, load_settings(folder), SEPTEMBER, TODAY)
    ledger, added = add_new([], found)
    reclaim = next(item for item in added if item.action == RECLAIM)
    ledger = decide(ledger, reclaim.id, DONE, date(2026, 10, 6), "")
    (folder / PEOPLE_FILE_NAME).write_text(PEOPLE_CSV_HEADER + ROSTER.replace("idle@example.com,,,anthropic,standard\n", ""), encoding="utf-8")
    with_standard_seats_bought(folder, 3)

    # Done in October, so November and December are the whole months since.
    in_january = outcomes(folder, load_settings(folder), ledger, date(2027, 1, 10))

    assert [(result.saved_usd, result.months, result.problem) for result in in_january] == [(Decimal(50), 2, "")]
    (folder / PEOPLE_FILE_NAME).write_text(PEOPLE_CSV_HEADER + ROSTER, encoding="utf-8")
    assert outcomes(folder, load_settings(folder), ledger, date(2027, 1, 10))[0].problem == "the roster shows the seat given back"


def test_a_reclaimed_seat_saves_nothing_while_the_seats_bought_are_unchanged(tmp_path: Path) -> None:
    folder = company(tmp_path)
    three_months(folder)
    found, _ = find_recommendations(folder, load_settings(folder), SEPTEMBER, TODAY)
    ledger, added = add_new([], found)
    reclaim = next(item for item in added if item.action == RECLAIM)
    ledger = decide(ledger, reclaim.id, DONE, date(2026, 10, 6), "")
    (folder / PEOPLE_FILE_NAME).write_text(PEOPLE_CSV_HEADER + ROSTER.replace("idle@example.com,,,anthropic,standard\n", ""), encoding="utf-8")

    result = outcomes(folder, load_settings(folder), ledger, date(2027, 1, 10))

    assert [(item.saved_usd, item.problem) for item in result] == [
        (Decimal(0), "not saving yet: settings still show 4 standard seats bought, 4 when it was made")
    ]


def test_an_upgrade_saves_how_far_extra_usage_fell_less_the_price_gap(tmp_path: Path) -> None:
    folder = company(tmp_path)
    three_months(folder)
    found, _ = find_recommendations(folder, load_settings(folder), SEPTEMBER, TODAY)
    ledger, added = add_new([], found)
    upgrade = next(item for item in added if item.action == UPGRADE)
    ledger = decide(ledger, upgrade.id, DONE, date(2026, 9, 30), "")
    save_usage(folder, OCTOBER, [("heavy@example.com", 4000, 90000, "10")])

    result = outcomes(folder, load_settings(folder), ledger, date(2026, 11, 2))

    # $140 a month before, $10 in October, less Premium's $100 more: $30 saved.
    assert [(item.saved_usd, item.months) for item in result] == [(Decimal(30), 1)]


def test_recommend_writes_the_ledger_and_says_its_for_the_operator(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = company(tmp_path)
    three_months(folder)

    main(["recommend", str(folder), "2026-09"])
    output = capsys.readouterr().out

    assert output.startswith("Seat recommendations, for you and not the shared page:")
    assert "R1 (new): upgrade heavy@example.com standard → premium, saves up to $40.00 a month" in output
    assert (folder / LEDGER_FILE_NAME).exists()
    main(["decide", str(folder), "R1", "done", "--on", "2026-10-06"])
    assert read_ledger(folder)[0].status == DONE


def test_a_seat_used_again_this_month_isnt_reclaimed(tmp_path: Path) -> None:
    folder = company(tmp_path)
    three_months(folder)
    save_usage(folder, OCTOBER, [("idle@example.com", 12, 300, "0")], through=date(2026, 10, 4))

    assert "idle@example.com" not in found_in(folder)


def test_an_upgrade_whose_average_saving_is_negative_isnt_recommended(tmp_path: Path) -> None:
    folder = company(tmp_path)
    save_usage(folder, JULY, [*NORMAL_MONTH, ("heavy@example.com", 100, 2000, "0")])
    save_usage(folder, AUGUST, [*NORMAL_MONTH, ("heavy@example.com", 4000, 90000, "110")])
    save_usage(folder, SEPTEMBER, [*NORMAL_MONTH, ("heavy@example.com", 4000, 90000, "110")])

    # Over the gap in two months, but $73.33 a month on average is less than Premium's $100 more.
    assert "heavy@example.com" not in found_in(folder)


def test_a_declined_recommendation_returns_ninety_days_after_it_was_declined(tmp_path: Path) -> None:
    folder = company(tmp_path)
    three_months(folder)
    found, _ = find_recommendations(folder, load_settings(folder), SEPTEMBER, TODAY)
    ledger, added = add_new([], found)
    declined = decide(ledger, added[0].id, DECLINED, date(2026, 12, 1), "")

    early = [replace(item, made_on=date(2027, 1, 10)) for item in found]
    late = [replace(item, made_on=date(2027, 3, 2)) for item in found]

    assert add_new(declined, early)[1] == []
    assert [item.person for item in add_new(declined, late)[1]] == [added[0].person]


def test_an_upgrade_is_measured_over_every_whole_month_since_it_was_done(tmp_path: Path) -> None:
    folder = company(tmp_path)
    three_months(folder)
    found, _ = find_recommendations(folder, load_settings(folder), SEPTEMBER, TODAY)
    ledger, added = add_new([], found)
    upgrade = next(item for item in added if item.action == UPGRADE)
    ledger = decide(ledger, upgrade.id, DONE, date(2026, 9, 30), "")
    for month in (OCTOBER, Month(2026, 11), Month(2026, 12), Month(2027, 1), Month(2027, 2)):
        save_usage(folder, month, [("heavy@example.com", 4000, 90000, "10")])

    result = outcomes(folder, load_settings(folder), ledger, date(2027, 3, 2))

    # $30 a month for October to February.
    assert [(item.saved_usd, item.months) for item in result] == [(Decimal(150), 5)]


def test_use_in_a_month_saved_only_in_part_keeps_a_seat_from_being_reclaimed(tmp_path: Path) -> None:
    folder = company(tmp_path)
    three_months(folder)
    save_usage(folder, OCTOBER, [("idle@example.com", 1000, 9000, "0")], through=date(2026, 10, 20))

    found, _ = find_recommendations(folder, load_settings(folder), OCTOBER, date(2026, 11, 3))

    assert RECLAIM not in [item.action for item in found if item.person == "idle@example.com"]


def test_someone_whose_seat_the_ledger_changed_isnt_judged_on_months_spent_on_the_old_seat(tmp_path: Path) -> None:
    folder = company(tmp_path)
    three_months(folder)
    found, _ = find_recommendations(folder, load_settings(folder), SEPTEMBER, TODAY)
    ledger, added = add_new([], found)
    upgrade = next(item for item in added if item.action == UPGRADE)
    write_ledger(folder, decide(ledger, upgrade.id, DONE, date(2026, 8, 15), ""))
    (folder / PEOPLE_FILE_NAME).write_text(
        PEOPLE_CSV_HEADER + ROSTER.replace("heavy@example.com,,,anthropic,standard", "heavy@example.com,,,anthropic,premium"),
        encoding="utf-8",
    )

    again, _ = find_recommendations(folder, load_settings(folder), SEPTEMBER, TODAY)

    # On Premium now, with extra usage from months on Standard: no downgrade, no new upgrade.
    assert [item.action for item in again if item.person == "heavy@example.com"] == []


def test_an_upgrade_isnt_recommended_once_the_latest_months_extra_usage_has_stopped(tmp_path: Path) -> None:
    folder = company(tmp_path)
    save_usage(folder, JULY, [*NORMAL_MONTH, ("heavy@example.com", 4000, 90000, "150")])
    save_usage(folder, AUGUST, [*NORMAL_MONTH, ("heavy@example.com", 4500, 95000, "375")])
    save_usage(folder, SEPTEMBER, [*NORMAL_MONTH, ("heavy@example.com", 3000, 70000, "0")])

    assert "heavy@example.com" not in found_in(folder)


def test_two_weeks_into_a_month_it_counts_scaled_to_a_whole_month(tmp_path: Path) -> None:
    folder = company(tmp_path)
    save_usage(folder, AUGUST, [*NORMAL_MONTH, ("heavy@example.com", 3000, 70000, "150")])
    save_usage(folder, SEPTEMBER, [*NORMAL_MONTH, ("heavy@example.com", 4500, 95000, "180")])
    # $75 through Oct 15 is $155 over October's 31 days, above the $100 Premium gap.
    save_usage(folder, OCTOBER, [*NORMAL_MONTH, ("heavy@example.com", 2000, 40000, "75")], through=date(2026, 10, 15))

    found, notes = find_recommendations(folder, load_settings(folder), SEPTEMBER, date(2026, 10, 16))
    upgrade = next(item for item in found if item.action == UPGRADE)

    assert upgrade.basis == "2026-08 to 2026-10"
    assert upgrade.baseline_usd == Decimal("161.67")
    assert "2026-10 counts through 2026-10-15, scaled to a whole month" in notes


def test_the_page_lists_what_the_rules_find_with_people_in_the_person_view(tmp_path: Path) -> None:
    folder = company(tmp_path)
    three_months(folder)

    savings = build_dashboard(folder, load_settings(folder), OCTOBER, date(2026, 10, 5))["savings"]

    assert isinstance(savings, dict)
    upgrade = next(item for item in savings["open"] if item["action"] == UPGRADE)
    assert (upgrade["person"], upgrade["monthly_cents"], upgrade["at_most"]) == ("heavy@example.com", 4000, True)
    assert savings["about_people"] == 0


def test_the_team_view_lists_only_company_wide_savings_and_counts_the_rest(tmp_path: Path) -> None:
    folder = company(tmp_path)
    three_months(folder)
    settings = json.loads((folder / SETTINGS_FILE_NAME).read_text(encoding="utf-8"))
    (folder / SETTINGS_FILE_NAME).write_text(json.dumps({**settings, "visibility": "team"}), encoding="utf-8")

    savings = build_dashboard(folder, load_settings(folder), OCTOBER, date(2026, 10, 5))["savings"]

    assert isinstance(savings, dict)
    assert [item["action"] for item in savings["open"]] == [BUNDLES]
    assert savings["about_people"] == 4
    assert "@" not in json.dumps(savings)


def test_the_page_doesnt_offer_a_recommendation_the_ledger_marks_declined(tmp_path: Path) -> None:
    folder = company(tmp_path)
    three_months(folder)
    found, _ = find_recommendations(folder, load_settings(folder), SEPTEMBER, TODAY)
    ledger, added = add_new([], found)
    upgrade = next(item for item in added if item.action == UPGRADE)
    write_ledger(folder, decide(ledger, upgrade.id, DECLINED, TODAY, ""))

    savings = build_dashboard(folder, load_settings(folder), OCTOBER, date(2026, 10, 5))["savings"]

    assert isinstance(savings, dict)
    assert UPGRADE not in [item["action"] for item in savings["open"]]
