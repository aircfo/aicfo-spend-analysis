import re
from decimal import Decimal
from pathlib import Path

import pytest

from aicfo_spend.settings import (
    SETTINGS_FILE_NAME,
    AlertThresholds,
    SettingsError,
    load_settings,
    parse_settings,
)


def write_settings(folder: Path, text: str) -> None:
    (folder / SETTINGS_FILE_NAME).write_text(text, encoding="utf-8")


def test_reads_seat_prices_as_exact_decimals(tmp_path: Path) -> None:
    write_settings(tmp_path, '{"seats": {"anthropic": {"premium": {"price_usd": 100.10, "bought": 2}}}}')

    plan = load_settings(tmp_path).seats["anthropic"]["premium"]

    assert plan.price_usd == Decimal("100.10")
    assert plan.bought == 2


def test_fills_in_defaults_when_the_file_is_empty() -> None:
    settings = parse_settings({})

    assert settings.company_name == ""
    assert settings.visibility == "team"
    assert settings.seats == {}
    assert settings.alerts == AlertThresholds()


def test_keeps_default_alert_numbers_the_file_leaves_out() -> None:
    alerts = parse_settings({"alerts": {"spike_min_usd": 50}}).alerts

    assert alerts.spike_min_usd == Decimal(50)
    assert alerts.spike_multiple == AlertThresholds().spike_multiple
    assert alerts.over_pace_percent == AlertThresholds().over_pace_percent


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([], "the settings file must be a JSON object"),
        ({"visibility": "everyone"}, 'visibility must be "team" or "person"'),
        ({"visiblity": "team"}, "unexpected keys ['visiblity']"),
        ({"company_name": 7}, "company_name must be text in quotes"),
        (
            {"seats": {"anthropic": {"standard": {"price_usd": -1, "bought": 3}}}},
            "seats.anthropic.standard.price_usd can't be negative",
        ),
        (
            {"seats": {"anthropic": {"standard": {"price_usd": True, "bought": 3}}}},
            "seats.anthropic.standard.price_usd must be a dollar amount",
        ),
        (
            {"seats": {"anthropic": {"standard": {"price_usd": 20, "bought": 2.5}}}},
            "seats.anthropic.standard.bought must be a whole number of seats",
        ),
        ({"seats": {"anthropic": {"standard": {"price_usd": 20}}}}, "needs both price_usd and bought"),
        ({"alerts": {"spike_multiple": 0}}, "alerts.spike_multiple must be more than 0"),
        ({"alerts": {"spike_after": 3}}, "alerts has unexpected keys ['spike_after']"),
    ],
)
def test_rejects_settings_with_a_message_that_says_what_to_fix(raw: object, message: str) -> None:
    with pytest.raises(SettingsError, match=re.escape(message)):
        parse_settings(raw)


def test_points_to_the_line_of_a_json_mistake(tmp_path: Path) -> None:
    # A missing comma, which every supported Python reports on the line after it.
    write_settings(tmp_path, '{\n  "visibility": "team"\n  "seats": {}\n}\n')

    with pytest.raises(SettingsError, match="line 3"):
        load_settings(tmp_path)


def test_says_how_to_create_a_missing_settings_file(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="aicfo-spend init"):
        load_settings(tmp_path)
