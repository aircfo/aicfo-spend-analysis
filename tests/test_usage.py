import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from aicfo_spend.cli import main
from aicfo_spend.dashboard import OTHER_MODELS, OTHER_PRODUCTS, build_dashboard
from aicfo_spend.months import Month
from aicfo_spend.raw_store import CLAUDE_SPEND_EXPORT, save_raw
from aicfo_spend.settings import SETTINGS_FILE_NAME, load_settings
from aicfo_spend.usage import active_seat_holders, build_usage, top_share
from tests.factories import spend_export, usage_export
from tests.workspaces import SAVED_AT, SEPTEMBER, new_workspace

EARLY_OCTOBER = date(2026, 10, 2)


def save_usage(folder: Path, *rows: tuple[str, str, int, int, int, int, str]) -> None:
    save_raw(folder, CLAUDE_SPEND_EXPORT, SEPTEMBER.first_day, SEPTEMBER.last_day, usage_export(*rows), saved_at=SAVED_AT)


def test_use_is_fresh_tokens_with_cache_reads_kept_apart(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    save_usage(folder, ("pat.lee@example.com", "Chat", 40, 1_000, 90_000, 500, "0"))

    [row] = build_usage(folder, SEPTEMBER).rows

    assert (row.requests, row.fresh_tokens, row.cache_read_tokens) == (40, 1_500, 90_000)
    assert (row.team, row.extra_usd) == ("Sales", Decimal(0))


def test_a_seat_holder_who_never_used_claude_is_not_active(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    save_usage(folder, ("pat.lee@example.com", "Chat", 40, 1_000, 0, 500, "0"))

    usage = build_usage(folder, SEPTEMBER)

    assert set(usage.seat_holders) == {"pat.lee@example.com", "sam.cho@example.com"}
    assert active_seat_holders(usage) == {"pat.lee@example.com"}


def test_the_busiest_tenth_needs_ten_people_to_mean_anything(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    save_usage(folder, *((f"p{n}@example.com", "Chat", 10, 100, 0, 0, "0") for n in range(9)))
    assert top_share(build_usage(folder, SEPTEMBER)) is None

    save_raw(folder, CLAUDE_SPEND_EXPORT, SEPTEMBER.first_day, SEPTEMBER.last_day, usage_export(
        *((f"p{n}@example.com", "Chat", 10, 100, 0, 0, "0") for n in range(9)),
        ("busy@example.com", "Claude Code", 900, 9_100, 0, 0, "40")), saved_at=SAVED_AT.replace(hour=10))
    assert top_share(build_usage(folder, SEPTEMBER)) == Decimal("0.91")


def test_an_export_without_usage_columns_says_so_instead_of_showing_zeros(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    save_raw(folder, CLAUDE_SPEND_EXPORT, SEPTEMBER.first_day, SEPTEMBER.last_day,
             spend_export(("pat.lee@example.com", "claude-opus", "12")), saved_at=SAVED_AT)

    usage = build_usage(folder, SEPTEMBER)

    assert usage.covers is None
    assert "an older kind with no usage columns" in usage.notes[0]


def test_report_prints_usage_and_writes_usage_csv(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = new_workspace(tmp_path)
    save_usage(folder, ("pat.lee@example.com", "Claude Code", 40, 1_000, 0, 500, "3.50"))

    main(["report", str(folder), "2026-09"])

    out = capsys.readouterr().out
    assert "1 of 2 Claude seat holders used Claude" in out
    assert "Claude Code: 100% of fresh tokens, 1 person, $3.50 extra usage" in out
    assert (folder / "out/2026-09/usage.csv").read_text(encoding="utf-8").splitlines()[1] == (
        "pat.lee@example.com,Sales,Claude Code,claude-sonnet,40,1500,0,3.50"
    )


def test_the_team_view_folds_products_few_people_use_and_names_no_one(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    save_usage(folder, *((f"p{n}@example.com", "Cowork", 10, 100, 0, 0, "0") for n in range(3)),
               *((f"c{n}@example.com", "Claude Code", 50, 900, 0, 0, "5") for n in range(2)),
               ("pat.lee@example.com", "Chat", 5, 50, 0, 0, "0"))

    usage = build_dashboard(folder, load_settings(folder), SEPTEMBER, EARLY_OCTOBER)["usage"]

    assert isinstance(usage, dict)
    assert [(row["product"], row["people"]) for row in usage["by_product"]] == [("Cowork", 3), (OTHER_PRODUCTS, 3)]
    assert "by_person" not in usage
    assert "@" not in json.dumps(usage)


def test_the_team_views_other_products_never_holds_fewer_than_three_people(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    save_usage(folder, *((f"p{n}@example.com", "Cowork", 10, 100, 0, 0, "0") for n in range(3)),
               ("pat.lee@example.com", "Claude Code", 50, 900, 0, 0, "412"))

    usage = build_dashboard(folder, load_settings(folder), SEPTEMBER, EARLY_OCTOBER)["usage"]

    assert isinstance(usage, dict)
    # One person's Claude Code alone would point at them, so Cowork joins it.
    assert [(row["product"], row["people"]) for row in usage["by_product"]] == [(OTHER_PRODUCTS, 4)]
    assert [row["model"] for row in usage["by_model"]] == ["claude-sonnet"]


def test_the_team_view_folds_models_few_people_use(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    export = usage_export(*((f"p{n}@example.com", "Chat", 10, 100, 0, 0, "0") for n in range(4)))
    rare = export.decode().replace("p3@example.com,,Chat,claude-sonnet", "p3@example.com,,Chat,claude-opus")
    save_raw(folder, CLAUDE_SPEND_EXPORT, SEPTEMBER.first_day, SEPTEMBER.last_day, rare.encode(), saved_at=SAVED_AT)

    usage = build_dashboard(folder, load_settings(folder), SEPTEMBER, EARLY_OCTOBER)["usage"]

    assert isinstance(usage, dict)
    assert [row["model"] for row in usage["by_model"]] == [OTHER_MODELS]


def test_the_person_view_lists_seat_holders_who_never_used_claude(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)
    settings = json.loads((folder / SETTINGS_FILE_NAME).read_text(encoding="utf-8"))
    (folder / SETTINGS_FILE_NAME).write_text(json.dumps({**settings, "visibility": "person"}), encoding="utf-8")
    save_usage(folder, ("pat.lee@example.com", "Chat", 40, 1_000, 0, 500, "0"))

    usage = build_dashboard(folder, load_settings(folder), SEPTEMBER, EARLY_OCTOBER)["usage"]

    assert isinstance(usage, dict)
    people = {row["person"]: row for row in usage["by_person"]}
    assert people["sam.cho@example.com"]["requests"] == 0
    assert people["sam.cho@example.com"]["claude_seat_holder"] is True


def test_a_month_with_no_export_has_no_usage_section(tmp_path: Path) -> None:
    folder = new_workspace(tmp_path)

    assert build_dashboard(folder, load_settings(folder), Month(2026, 9), EARLY_OCTOBER)["usage"] is None
