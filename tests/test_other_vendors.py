import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from aicfo_spend.cli import main
from aicfo_spend.dashboard import build_dashboard
from aicfo_spend.errors import InputError
from aicfo_spend.other_vendors import other_ai_spend
from aicfo_spend.raw_store import RAMP_VENDOR_SPEND, save_raw
from aicfo_spend.settings import SETTINGS_FILE_NAME, SettingsError, load_settings, parse_settings
from aicfo_spend.sources.ramp import parse_vendor_spend
from tests.factories import ramp_vendor_spend_answer
from tests.workspaces import SAVED_AT, SEPTEMBER, new_workspace

ANSWER = ramp_vendor_spend_answer(("Cursor", "480"), ("Office Lunches", "1200.50"), (None, "3.10"), ("perplexity", "40"))


def tracking(folder: Path, *vendors: str) -> None:
    settings = json.loads((folder / SETTINGS_FILE_NAME).read_text(encoding="utf-8"))
    (folder / SETTINGS_FILE_NAME).write_text(json.dumps({**settings, "other_ai_vendors": list(vendors)}), encoding="utf-8")


def save_september(folder: Path, answer: bytes = ANSWER) -> None:
    save_raw(folder, RAMP_VENDOR_SPEND, SEPTEMBER.first_day, date(2026, 9, 28), answer, saved_at=SAVED_AT)


def test_reads_ramps_answer_as_it_comes_or_cut_down_to_payee_and_amount() -> None:
    as_it_comes = parse_vendor_spend(json.loads(ANSWER, parse_float=Decimal), "answer")
    cut_down = parse_vendor_spend(
        json.loads('{"data": [{"payee": "Cursor", "amount": 480}, {"payee": "Office Lunches", "amount": 1200.50},'
                   ' {"payee": "Perplexity", "amount": 40}]}', parse_float=Decimal),
        "answer",
    )

    assert as_it_comes == cut_down == {"cursor": Decimal(480), "office lunches": Decimal("1200.5"), "perplexity": Decimal(40)}


def test_refuses_a_vendor_amount_that_isnt_dollars() -> None:
    with pytest.raises(InputError, match="has an amount for Cursor that isn't a number of dollars"):
        parse_vendor_spend({"data": [{"payee": "Cursor", "amount": "480"}]}, "answer")


def test_shows_only_the_vendors_settings_name_biggest_first_and_zero_for_one_not_paid(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    tracking(folder, "Perplexity", "Cursor", "Granola")
    save_september(folder)

    spend = other_ai_spend(folder, SEPTEMBER, load_settings(folder))

    assert spend is not None
    assert spend.vendors == [("Cursor", Decimal(480)), ("Perplexity", Decimal(40)), ("Granola", Decimal(0))]
    assert (spend.through, spend.total) == (date(2026, 9, 28), Decimal(520))


def test_shows_nothing_until_settings_name_a_vendor(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    save_september(folder)

    assert other_ai_spend(folder, SEPTEMBER, load_settings(folder)) is None


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"other_ai_vendors": "Cursor"}, "other_ai_vendors must be a list of vendor names"),
        ({"other_ai_vendors": [7]}, "other_ai_vendors item 1 must be text in quotes"),
        ({"other_ai_vendors": ["Anthropic"]}, "other_ai_vendors is for vendors besides Anthropic and OpenAI"),
        ({"other_ai_vendors": ["Cursor", "cursor"]}, "other_ai_vendors lists 'cursor' twice; list each vendor once"),
    ],
)
def test_rejects_an_other_ai_vendors_setting_with_a_message_that_says_what_to_fix(raw: object, message: str) -> None:
    with pytest.raises(SettingsError, match=re.escape(message)):
        parse_settings(raw)


def test_report_lists_other_ai_vendors_apart_from_the_totals(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = new_workspace(tmp_path)
    tracking(folder, "Cursor")
    save_september(folder)

    main(["report", str(folder), "2026-09"])

    assert ("Other AI vendors paid through Ramp, through 2026-09-28 (not in the teams, totals or forecast above):\n"
            "  Cursor: $480.00\n  Total: $480.00") in capsys.readouterr().out


def test_the_dashboard_shows_other_ai_vendors_in_the_team_view(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    tracking(folder, "Cursor")
    save_september(folder)

    dashboard = build_dashboard(folder, load_settings(folder), SEPTEMBER, date(2026, 10, 5))

    assert dashboard["other_ai_vendors"] == {"through": "2026-09-28", "vendors": [{"vendor": "Cursor", "total_cents": 48000}]}


def test_refuses_one_page_of_an_answer_that_has_more() -> None:
    answer = json.loads(ANSWER, parse_float=Decimal)
    answer["response"]["table"]["page"] = {"next": "page-2"}

    with pytest.raises(InputError, match="one page of Ramp's answer and more follow"):
        parse_vendor_spend(answer, "answer")
