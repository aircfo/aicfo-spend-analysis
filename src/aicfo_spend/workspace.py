"""Set up a company's workspace folder, laid out the way every other command expects."""

from __future__ import annotations

import json
from pathlib import Path

from aicfo_spend.errors import UserError
from aicfo_spend.settings import SETTINGS_FILE_NAME

PEOPLE_FILE_NAME = "people.csv"
# The Claude skill keeps its published page's link here.
DASHBOARD_URL_FILE_NAME = "dashboard-url.txt"
# One row per seat a person holds. name and team are only needed for people Ramp doesn't know.
PEOPLE_COLUMNS = ("email", "name", "team", "vendor", "seat_type")

# Seats start empty because prices and counts differ by company. The alert numbers
# must match AlertThresholds' defaults; a test keeps the two in step.
DEFAULT_ALERTS = {"spike_multiple": 2, "spike_min_usd": 25, "over_pace_percent": 25}


class WorkspaceError(UserError):
    """A workspace can't be set up where asked."""


def git_repo_containing(folder: Path) -> Path | None:
    for candidate in (folder, *folder.parents):
        # .git is a folder in a normal clone and a file in a worktree or submodule.
        if (candidate / ".git").exists():
            return candidate
    return None


def create_workspace(folder: Path, company_name: str) -> list[Path]:
    """Create the workspace and return every folder and file it made, in order."""
    folder = folder.expanduser().resolve()
    repo = git_repo_containing(folder)
    if repo is not None:
        raise WorkspaceError(
            f"{folder} is inside the git repo at {repo}. A workspace holds a company's "
            "spend data, so it has to live outside any repo."
        )
    if folder.exists() and not folder.is_dir():
        raise WorkspaceError(f"{folder} is a file, not a folder")
    settings_path = folder / SETTINGS_FILE_NAME
    if settings_path.exists():
        raise WorkspaceError(f"{folder} is already in use, so nothing in it was changed. {_in_use(folder)}")

    created: list[Path] = []
    for path in (folder, folder / "raw", folder / "out"):
        if not path.exists():
            path.mkdir(parents=True)
            created.append(path)

    template = {"company_name": company_name, "visibility": "person", "seats": {}, "alerts": DEFAULT_ALERTS}
    settings_path.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
    created.append(settings_path)

    people_path = folder / PEOPLE_FILE_NAME
    if not people_path.exists():
        people_path.write_text(",".join(PEOPLE_COLUMNS) + "\n", encoding="utf-8")
        created.append(people_path)
    return created


def _in_use(folder: Path) -> str:
    """What an existing workspace already holds, so nobody mistakes it for an empty one."""
    held = [f"its {SETTINGS_FILE_NAME}"]
    downloads = [path for path in (folder / "raw").glob("*/*") if path.is_file()]
    if downloads:
        held.append(f"{len(downloads)} saved downloads")
    url_file = folder / DASHBOARD_URL_FILE_NAME
    if url_file.is_file() and url_file.read_text(encoding="utf-8").strip():
        held.append(f"a published dashboard at {url_file.read_text(encoding='utf-8').strip()}")
    listing = held[0] if len(held) == 1 else ", ".join(held[:-1]) + " and " + held[-1]
    return f"It has {listing}. To carry on with it, skip init; to start fresh, choose a new folder."
