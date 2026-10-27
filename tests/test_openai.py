"""Done criterion 3: OpenAI's API spend and ChatGPT seats show up beside Anthropic's with no core change."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from aicfo_spend.forecast import forecast_after
from aicfo_spend.months import Month
from aicfo_spend.pace import pace_of
from aicfo_spend.raw_store import RAMP_API_KEYS, save_raw
from aicfo_spend.report import build_month, summarize, totals_by
from aicfo_spend.settings import SETTINGS_FILE_NAME, load_settings, parse_settings
from aicfo_spend.spend_table import PRODUCT_API, PRODUCT_SEATS
from tests.factories import ramp_key, ramp_keys_answer
from tests.workspaces import SAVED_AT, SEPTEMBER, TWO_SEATS, new_workspace, save_month

REPO = Path(__file__).resolve().parents[1]
OCTOBER = Month(2026, 10)
CHATGPT_SEAT = "pat.lee@example.com,,,openai,business\n"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Acme adds one ChatGPT Business seat (Sales, $30) and an OpenAI API key (Engineering, $9.08)."""
    folder = new_workspace(tmp_path, TWO_SEATS + CHATGPT_SEAT)
    (folder / SETTINGS_FILE_NAME).write_text(
        '{"company_name": "Acme", "seats": {'
        '"anthropic": {"standard": {"price_usd": 25, "bought": 3}},'
        '"openai": {"business": {"price_usd": 30, "bought": 1}}}}',
        encoding="utf-8",
    )
    keys = ramp_keys_answer(
        ramp_key("backend", 30000, "sam.cho@example.com"),
        ramp_key("reviewer", 908, "sam.cho@example.com", provider="OPENAI"),
    )
    save_raw(folder, RAMP_API_KEYS, SEPTEMBER.first_day, SEPTEMBER.last_day, keys, saved_at=SAVED_AT)
    save_month(folder, SEPTEMBER, extra_usage=(("pat.lee@example.com", "claude-opus", "100.50"),))
    return folder


def test_openai_shows_up_beside_anthropic_by_vendor(workspace: Path) -> None:
    summary = summarize(build_month(workspace, SEPTEMBER, load_settings(workspace)))

    lines = summary.splitlines()
    assert next(line for line in lines if line.startswith("openai")).split() == [
        "openai", "$30.00", "$0.00", "$9.08", "$39.08"
    ]
    assert next(line for line in lines if line.startswith("anthropic")).split() == [
        "anthropic", "$75.00", "$100.50", "$300.00", "$475.50"
    ]


def test_a_chatgpt_seat_and_an_openai_key_land_on_their_teams(workspace: Path) -> None:
    by_team = totals_by(build_month(workspace, SEPTEMBER, load_settings(workspace)).rows, lambda row: row.team)

    assert by_team["Sales"][PRODUCT_SEATS] == Decimal("55")  # a Claude seat and a ChatGPT seat
    assert by_team["Engineering"][PRODUCT_API] == Decimal("309.08")


def test_chatgpt_seats_are_in_the_forecast_and_the_pace(workspace: Path) -> None:
    settings = load_settings(workspace)

    forecast = forecast_after(workspace, settings, SEPTEMBER.last_day)
    pace = pace_of(workspace, settings, OCTOBER, date(2026, 10, 5))

    assert forecast.by_team["Sales"][PRODUCT_SEATS] == Decimal("55.00")
    assert forecast.by_team["Engineering"][PRODUCT_API] == Decimal("309.08")
    assert pace.so_far["Sales"] == Decimal("55.00")


def test_the_openai_vendor_folder_holds_only_its_note() -> None:
    folder = REPO / "src/aicfo_spend/vendors/openai"

    assert [path.name for path in folder.iterdir() if path.suffix == ".py"] == ["__init__.py"]
    assert "def " not in (folder / "__init__.py").read_text(encoding="utf-8")


def test_the_settings_example_in_the_guide_is_valid() -> None:
    guide = (REPO / "docs/adding-a-vendor.md").read_text(encoding="utf-8")
    example = guide.split("```json\n", 1)[1].split("```", 1)[0]

    settings = parse_settings(json.loads("{" + example + "}", parse_float=Decimal))

    assert settings.seats["openai"]["business"].price_usd == Decimal(30)
