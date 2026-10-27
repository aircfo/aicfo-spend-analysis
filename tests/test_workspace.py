from pathlib import Path

import pytest

from aicfo_spend.settings import SETTINGS_FILE_NAME, AlertThresholds, load_settings
from aicfo_spend.workspace import PEOPLE_FILE_NAME, WorkspaceError, create_workspace


def test_creates_the_folders_and_an_empty_people_file(tmp_path: Path) -> None:
    workspace = tmp_path / "acme"

    create_workspace(workspace, "Acme")

    assert (workspace / "raw").is_dir()
    assert (workspace / "out").is_dir()
    assert (workspace / PEOPLE_FILE_NAME).read_text(encoding="utf-8") == "email,name,team,vendor,seat_type\n"


def test_writes_settings_that_load_with_the_default_alert_numbers(tmp_path: Path) -> None:
    workspace = tmp_path / "acme"

    create_workspace(workspace, "Acme")
    settings = load_settings(workspace)

    assert settings.company_name == "Acme"
    assert settings.visibility == "team"
    assert settings.seats == {}
    assert settings.alerts == AlertThresholds()


def test_refuses_to_overwrite_an_existing_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "acme"
    create_workspace(workspace, "Acme")
    (workspace / SETTINGS_FILE_NAME).write_text('{"company_name": "Edited by hand"}', encoding="utf-8")

    with pytest.raises(WorkspaceError, match="already a workspace"):
        create_workspace(workspace, "Acme")

    assert load_settings(workspace).company_name == "Edited by hand"


def test_keeps_a_people_file_that_is_already_there(tmp_path: Path) -> None:
    workspace = tmp_path / "acme"
    workspace.mkdir()
    (workspace / PEOPLE_FILE_NAME).write_text("email\nada@example.com\n", encoding="utf-8")

    create_workspace(workspace, "Acme")

    assert (workspace / PEOPLE_FILE_NAME).read_text(encoding="utf-8") == "email\nada@example.com\n"


def test_refuses_a_folder_inside_a_git_repo(tmp_path: Path) -> None:
    (tmp_path / "repo" / ".git").mkdir(parents=True)

    with pytest.raises(WorkspaceError, match="outside any repo"):
        create_workspace(tmp_path / "repo" / "acme", "Acme")

    assert not (tmp_path / "repo" / "acme").exists()


def test_refuses_a_path_that_is_a_file(tmp_path: Path) -> None:
    (tmp_path / "acme").write_text("not a folder", encoding="utf-8")

    with pytest.raises(WorkspaceError, match="is a file"):
        create_workspace(tmp_path / "acme", "Acme")
