import os
import subprocess
import sys
from pathlib import Path

import pytest

from aicfo_spend import __version__
from aicfo_spend.cli import main
from aicfo_spend.settings import SETTINGS_FILE_NAME, load_settings

SRC = Path(__file__).resolve().parents[1] / "src"


def test_version_flag_prints_the_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_init_lists_what_it_created_and_what_to_do_next(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["init", str(tmp_path / "acme"), "--company", "Acme"]) == 0

    out = capsys.readouterr().out
    assert SETTINGS_FILE_NAME in out
    assert "people.csv" in out
    assert "Next:" in out


def test_init_names_the_company_after_the_folder_by_default(tmp_path: Path) -> None:
    main(["init", str(tmp_path / "acme-co")])

    assert load_settings(tmp_path / "acme-co").company_name == "acme-co"


def test_settings_command_summarizes_seats_and_alerts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / SETTINGS_FILE_NAME).write_text(
        '{"company_name": "Acme", "seats": {"anthropic": {"standard": {"price_usd": 20, "bought": 10}}}}',
        encoding="utf-8",
    )

    assert main(["settings", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "Company: Acme" in out
    assert "anthropic standard: 10 bought at $20.00 a month" in out
    assert "over 2× its 28-day median" in out


def test_settings_command_exits_1_with_a_plain_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / SETTINGS_FILE_NAME).write_text('{"visibility": "everyone"}', encoding="utf-8")

    assert main(["settings", str(tmp_path)]) == 1

    assert capsys.readouterr().err.startswith('aicfo-spend: visibility must be "team" or "person"')


def a_workspace(tmp_path: Path) -> Path:
    main(["init", str(tmp_path / "acme"), "--company", "Acme"])
    return tmp_path / "acme"


def an_export(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("user_email,account_uuid,product,model,total_net_spend_usd,total_gross_spend_usd\n", encoding="utf-8")
    return path


def test_save_refuses_an_export_named_for_other_days(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    workspace = a_workspace(tmp_path)
    export = an_export(tmp_path / "Downloads", "spend-report-acme-2026-10-01-to-2026-10-27.csv")

    status = main(["save", str(workspace), "claude-spend-export", "--start", "2026-10-01", "--end", "2026-10-28",
                   "--file", str(export)])

    assert status == 1
    assert "is named for 2026-10-01 to 2026-10-27, but --start and --end say 2026-10-01 to 2026-10-28" in capsys.readouterr().err
    assert not list((workspace / "raw").glob("*/*"))


def test_save_can_remove_the_downloaded_copy_once_it_is_saved(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    workspace = a_workspace(tmp_path)
    export = an_export(tmp_path / "Downloads", "spend-report-acme-2026-10-01-to-2026-10-28.csv")

    status = main(["save", str(workspace), "claude-spend-export", "--start", "2026-10-01", "--end", "2026-10-28",
                   "--file", str(export), "--remove-original"])

    assert status == 0
    assert not export.exists()
    assert len(list((workspace / "raw" / "2026-10").glob("claude-spend-export_2026-10-01_2026-10-28_saved-*.csv"))) == 1
    assert f"Removed {export}" in capsys.readouterr().out


def test_save_keeps_the_original_when_the_save_is_refused(tmp_path: Path) -> None:
    workspace = a_workspace(tmp_path)
    export = an_export(tmp_path / "Downloads", "spend-report-acme-2026-10-01-to-2026-10-27.csv")

    main(["save", str(workspace), "claude-spend-export", "--start", "2026-10-01", "--end", "2026-10-28",
          "--file", str(export), "--remove-original"])

    assert export.exists()


def test_report_compare_points_to_the_skills_roster_script(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    workspace = a_workspace(tmp_path)

    status = main(["report", str(workspace), "2026-10", "--compare", "export.csv"])

    assert status == 1
    assert "roster_to_people.py" in capsys.readouterr().err


def test_runs_from_a_checkout_without_installing() -> None:
    # Cloud routines can't install packages, so they run the tool in place like this.
    result = subprocess.run(
        [sys.executable, "-m", "aicfo_spend", "--version"],
        env={**os.environ, "PYTHONPATH": str(SRC)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert __version__ in result.stdout
