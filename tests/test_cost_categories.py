import json
from datetime import date
from pathlib import Path

from aicfo_spend.dashboard import build_dashboard
from aicfo_spend.raw_store import RAMP_API_KEYS, RAMP_PROVIDER_TOTALS, save_raw
from aicfo_spend.report import NOT_TIED_TO_A_KEY, UNCATEGORIZED, api_by_cost_category, build_month, summarize
from aicfo_spend.settings import SETTINGS_FILE_NAME, load_settings
from tests.factories import ramp_key, ramp_keys_answer, ramp_totals_answer
from tests.workspaces import SAVED_AT, SEPTEMBER, new_workspace


def save_keys(folder: Path, *keys: dict[str, object], ramp_total: str) -> None:
    """Save a month of per-key spend and Ramp's total for it, which is more than the keys when spend is untied."""
    save_raw(folder, RAMP_API_KEYS, SEPTEMBER.first_day, SEPTEMBER.last_day, ramp_keys_answer(*keys), saved_at=SAVED_AT)
    save_raw(folder, RAMP_PROVIDER_TOTALS, SEPTEMBER.first_day, SEPTEMBER.last_day,
             ramp_totals_answer(ANTHROPIC=ramp_total), saved_at=SAVED_AT)


def with_categories(folder: Path, categories: dict[str, str]) -> None:
    settings = json.loads((folder / SETTINGS_FILE_NAME).read_text(encoding="utf-8"))
    (folder / SETTINGS_FILE_NAME).write_text(json.dumps({**settings, "api_cost_categories": categories}), encoding="utf-8")


def test_a_cost_category_tag_in_ramp_says_what_a_keys_spend_is_for(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    save_keys(folder, ramp_key("checkout-bot", 30000, "sam.cho@example.com", cost_category="Cost of revenue"), ramp_total="300")

    spend = build_month(folder, SEPTEMBER, load_settings(folder))

    assert [row.cost_category for row in spend.rows if row.api_key] == ["Cost of revenue"]


def test_settings_categorize_keys_without_a_tag_and_a_tag_wins(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    with_categories(folder, {"Review-Bot": "R&D", "checkout-bot": "R&D"})
    save_keys(folder, ramp_key("review-bot", 10000, "sam.cho@example.com"),
              ramp_key("checkout-bot", 30000, "sam.cho@example.com", cost_category="Cost of revenue"),
              ramp_key("scratch", 500, "sam.cho@example.com"), ramp_total="405")

    spend = build_month(folder, SEPTEMBER, load_settings(folder))

    assert {row.api_key: row.cost_category for row in spend.rows if row.api_key} == {
        "review-bot": "R&D", "checkout-bot": "Cost of revenue", "scratch": UNCATEGORIZED,
    }


def test_the_report_gives_api_by_category_with_the_remainders_last(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    with_categories(folder, {"review-bot": "R&D"})
    save_keys(folder, ramp_key("review-bot", 10000, "sam.cho@example.com"),
              ramp_key("checkout-bot", 30000, "sam.cho@example.com", cost_category="Cost of revenue"),
              ramp_key("scratch", 500, "sam.cho@example.com"), ramp_total="425")
    spend = build_month(folder, SEPTEMBER, load_settings(folder))

    assert [category for category, _ in api_by_cost_category(spend.rows)] == [
        "Cost of revenue", "R&D", UNCATEGORIZED, NOT_TIED_TO_A_KEY,
    ]
    summary = summarize(spend)
    assert "API by cost category:\n  Cost of revenue: $300.00\n  R&D: $100.00\n  Uncategorized: $5.00\n  Not tied to a current key: $20.00" in summary
    assert 'Give a key a category with a "Cost category" tag in Ramp' in summary


def test_the_team_view_shows_categories_without_naming_keys(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    settings = json.loads((folder / SETTINGS_FILE_NAME).read_text(encoding="utf-8"))
    (folder / SETTINGS_FILE_NAME).write_text(json.dumps({**settings, "visibility": "team"}), encoding="utf-8")
    save_keys(folder, ramp_key("pat-personal-key", 10000, "sam.cho@example.com", cost_category="R&D"), ramp_total="100")

    spend = build_dashboard(folder, load_settings(folder), SEPTEMBER, date(2026, 10, 2))["spend"]

    assert isinstance(spend, dict)
    assert spend["api_by_cost_category"] == [{"category": "R&D", "total_cents": 10000}]
    assert "pat-personal-key" not in json.dumps(spend)
