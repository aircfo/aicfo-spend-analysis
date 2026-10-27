"""Save each download once, untouched, and find the right saved files for a month.

Files land in raw/<YYYY-MM>/ as <source>_<start>_<end>_saved-<UTC time>.<ext> and are
made read-only. A re-download is a new file, never an edit; when two files cover the
same days from the same source, the one saved last counts.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from aicfo_spend.errors import InputError
from aicfo_spend.months import Month

RAMP_API_KEYS = "ramp-api-keys"
RAMP_PEOPLE = "ramp-people"
CLAUDE_SPEND_EXPORT = "claude-spend-export"

EXTENSIONS = {RAMP_API_KEYS: "json", RAMP_PEOPLE: "json", CLAUDE_SPEND_EXPORT: "csv"}
SOURCES = tuple(EXTENSIONS)

SPEND_EXPORT_REQUIRED_COLUMNS = ("user_email", "model", "total_net_spend_usd")

FILE_NAME = re.compile(
    r"^(?P<source>[a-z-]+)_(?P<start>\d{4}-\d{2}-\d{2})_(?P<end>\d{4}-\d{2}-\d{2})"
    r"_saved-(?P<saved>\d{8}T\d{6}Z)\.(?:json|csv)$"
)


@dataclass(frozen=True)
class SavedFile:
    path: Path
    source: str
    start: date
    end: date
    saved_at: str  # sortable: 20261027T150102Z


def save_raw(
    workspace: Path, source: str, start: date, end: date, content: bytes, saved_at: datetime | None = None
) -> Path:
    if source not in EXTENSIONS:
        raise InputError(f"unknown source {source!r}; it can be one of {list(SOURCES)}")
    if end < start:
        raise InputError(f"the end date {end} is before the start date {start}")
    if Month.of(start) != Month.of(end):
        raise InputError("a saved file has to cover days within one month; save each month separately")
    if source != CLAUDE_SPEND_EXPORT and start != end:
        # One day per file keeps re-downloads from overlapping and double counting.
        raise InputError(f"{source} is saved one day at a time; use the same start and end date")
    _check_content(source, content)

    stamp = (saved_at or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    folder = workspace / "raw" / Month.of(start).label
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{source}_{start}_{end}_saved-{stamp}.{EXTENSIONS[source]}"
    if path.exists():
        raise InputError(f"{path.name} already exists; wait a second and save again")
    path.write_bytes(content)
    path.chmod(0o444)
    return path


def saved_files(workspace: Path, source: str) -> list[SavedFile]:
    found: list[SavedFile] = []
    for path in sorted((workspace / "raw").glob(f"*/{source}_*")):
        match = FILE_NAME.match(path.name)
        if match is None or match.group("source") != source:
            continue
        found.append(
            SavedFile(
                path=path,
                source=source,
                start=date.fromisoformat(match.group("start")),
                end=date.fromisoformat(match.group("end")),
                saved_at=match.group("saved"),
            )
        )
    return found


def newest_per_day(files: list[SavedFile], month: Month) -> dict[date, SavedFile]:
    """For one-day sources: the last-saved file for each day of the month that has one."""
    chosen: dict[date, SavedFile] = {}
    for saved in files:
        if Month.of(saved.start) != month:
            continue
        current = chosen.get(saved.start)
        if current is None or saved.saved_at > current.saved_at:
            chosen[saved.start] = saved
    return dict(sorted(chosen.items()))


def fullest_in_month(files: list[SavedFile], month: Month) -> SavedFile | None:
    """For range sources: the file reaching latest into the month, the last-saved one on a tie."""
    in_month = [saved for saved in files if Month.of(saved.start) == month]
    return max(in_month, key=lambda saved: (saved.end, saved.saved_at), default=None)


def newest(files: list[SavedFile]) -> SavedFile | None:
    return max(files, key=lambda saved: saved.saved_at, default=None)


def _check_content(source: str, content: bytes) -> None:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InputError(f"the {source} data isn't UTF-8 text") from None
    if EXTENSIONS[source] == "json":
        try:
            parsed: object = json.loads(text)
        except json.JSONDecodeError as error:
            raise InputError(f"the {source} data isn't valid JSON: {error.msg}") from None
        expected = "users" if source == RAMP_PEOPLE else "data"
        if not isinstance(parsed, dict) or not isinstance(parsed.get(expected), list):
            raise InputError(
                f'the {source} data should be Ramp\'s answer with a "{expected}" list '
                "(join every page into that one list before saving)"
            )
        return
    header = next(csv.reader(io.StringIO(text)), [])
    missing = [column for column in SPEND_EXPORT_REQUIRED_COLUMNS if column not in header]
    if missing:
        raise InputError(f"the spend export is missing the columns {missing}; is it the claude.ai spend report?")
