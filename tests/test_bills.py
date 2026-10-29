import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from aicfo_spend.bills import BILLS_FILE_NAME, KIND_CREDITS, Bill, check_bills, read_bills, summarize_bills
from aicfo_spend.cli import main
from aicfo_spend.dashboard import build_dashboard
from aicfo_spend.errors import InputError
from aicfo_spend.months import Month
from aicfo_spend.raw_store import RAMP_API_KEYS, RAMP_PROVIDER_TOTALS, save_raw
from aicfo_spend.report import build_month
from aicfo_spend.settings import load_settings
from tests.factories import ramp_key, ramp_keys_answer, ramp_totals_answer
from tests.workspaces import SAVED_AT, SEPTEMBER, fill_september, new_workspace

HEADER = "month,vendor,product,amount_usd,months,kind,note\n"


def write_bills(folder: Path, lines: str) -> None:
    (folder / BILLS_FILE_NAME).write_text(HEADER + lines, encoding="utf-8")


def september_checks(folder: Path) -> str:
    spend = build_month(folder, SEPTEMBER, load_settings(folder))
    return summarize_bills(SEPTEMBER, check_bills(spend, read_bills(folder)))


def test_a_seat_bill_within_a_dollar_ties(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    write_bills(folder, "2026-09,anthropic,seats,75.60,,,\n")

    assert "anthropic seats: ties (billed $75.60, counted $75.00)" in september_checks(folder)


def test_a_seat_bill_off_by_more_than_the_tolerance_says_by_how_much_and_why(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    write_bills(folder, "2026-09,anthropic,seats,100,,,\n")

    summary = september_checks(folder)

    assert "anthropic seats: DOESN'T TIE. Billed $100.00, counted $75.00, $25.00 less than the bill" in summary
    assert "seats bought or their prices in settings are out of date" in summary


def test_a_years_seats_paid_up_front_count_a_twelfth_in_each_month(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    write_bills(folder, "2026-01,anthropic,seats,900,12,,annual plan\n")

    assert "anthropic seats: ties (billed $75.00, counted $75.00)" in september_checks(folder)


def test_a_years_bill_pays_for_nothing_after_its_last_month() -> None:
    bill = Bill(Month(2025, 10), "anthropic", "seats", Decimal(1200), 12, "invoice", "")

    assert bill.share_in(Month(2026, 9)) == Decimal(100)
    assert bill.share_in(Month(2026, 10)) == Decimal(0)


def test_prepaid_credits_show_bought_beside_used_without_a_verdict(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    fill_september(folder)
    write_bills(folder, "2026-09,anthropic,extra usage,200,,credits,$250 bundle\n")

    summary = september_checks(folder)

    assert "anthropic extra usage: $200.00 of credits bought, $105.00 used" in summary
    assert "Credits tie over time" in summary


def test_a_usage_invoice_for_a_month_counted_only_in_part_cant_tie_yet(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    october = Month(2026, 10)
    save_raw(folder, RAMP_API_KEYS, october.first_day, date(2026, 10, 20),
             ramp_keys_answer(ramp_key("backend", 30000, "sam.cho@example.com")), saved_at=SAVED_AT)
    save_raw(folder, RAMP_PROVIDER_TOTALS, october.first_day, date(2026, 10, 20),
             ramp_totals_answer(ANTHROPIC="300"), saved_at=SAVED_AT)
    write_bills(folder, "2026-10,anthropic,api,500,,,\n")
    spend = build_month(folder, october, load_settings(folder))

    checks = check_bills(spend, read_bills(folder))

    assert [check.ties for check in checks] == [None]
    assert "counted $300.00 through 2026-10-20, so it can't tie yet" in summarize_bills(october, checks)


def test_says_when_no_bill_is_entered_for_the_month(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)

    assert september_checks(folder) == "Against the bills: none entered for 2026-09 in bills.csv"


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("September,anthropic,seats,75,,,\n", "line 2: month must be the month the bill pays for, like 2026-09"),
        ("2026-09,anthropic,tokens,75,,,\n", "line 2: product must be one of seats, extra usage, api"),
        ("2026-09,anthropic,seats,about 75,,,\n", "line 2: amount_usd must be a dollar amount"),
        ("2026-09,anthropic,seats,75,1.5,,\n", "line 2: months must be a whole number"),
        ("2026-09,anthropic,api,75,,prepaid,\n", 'line 2: kind must be "invoice" or "credits"'),
        ("2026-09,anthropic,seats,75,,credits,\n", "line 2: seats are charged by invoice, not bought as credits"),
    ],
)
def test_rejects_a_bill_line_with_a_message_that_says_what_to_fix(tmp_path: Path, line: str, message: str) -> None:
    write_bills(tmp_path, line)

    with pytest.raises(InputError, match=re.escape(message)):
        read_bills(tmp_path)


def test_reads_dollar_signs_and_thousands_commas_in_amounts(tmp_path: Path) -> None:
    write_bills(tmp_path, '2026-09,anthropic,extra usage,"$1,000.00",,credits,\n')

    assert [(bill.amount_usd, bill.kind) for bill in read_bills(tmp_path)] == [(Decimal("1000.00"), KIND_CREDITS)]


def test_the_dashboard_shows_the_tie_out_in_the_team_view(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    write_bills(folder, "2026-09,anthropic,seats,75,,,\n")

    dashboard = build_dashboard(folder, load_settings(folder), SEPTEMBER, date(2026, 10, 5))

    assert dashboard["bills"] == [
        {"vendor": "anthropic", "product": "seats", "kind": "invoice", "billed_cents": 7500, "counted_cents": 7500,
         "counted_through": None, "ties": True}
    ]


def test_report_checks_the_bills_only_once_a_bills_file_exists(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = new_workspace(tmp_path)
    main(["report", str(folder), "2026-09"])
    assert "Against the bills" not in capsys.readouterr().out

    write_bills(folder, "2026-09,anthropic,seats,75,,,\n")
    main(["report", str(folder), "2026-09"])

    assert "anthropic seats: ties" in capsys.readouterr().out
