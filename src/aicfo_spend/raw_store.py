"""Save each download once, untouched, and find the right saved files for a month.

Files land in raw/<YYYY-MM>/ as <source>_<start>_<end>_saved-<UTC time>.<ext> and are
made read-only. Spend downloads cover the month to date, so they start on the 1st and
the one reaching furthest into the month counts; a re-download is a new file, never an
edit. The Ramp people list is a snapshot of one day.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from aicfo_spend.errors import InputError
from aicfo_spend.months import Month
from aicfo_spend.sources.ramp import parse_key_spend, parse_people, parse_provider_totals
from aicfo_spend.vendors.anthropic.spend_export import parse_spend_export

RAMP_API_KEYS = "ramp-api-keys"
RAMP_PROVIDER_TOTALS = "ramp-provider-totals"
RAMP_PEOPLE = "ramp-people"
CLAUDE_SPEND_EXPORT = "claude-spend-export"

EXTENSIONS = {RAMP_API_KEYS: "json", RAMP_PROVIDER_TOTALS: "json", RAMP_PEOPLE: "json", CLAUDE_SPEND_EXPORT: "csv"}
SOURCES = tuple(EXTENSIONS)
MONTH_TO_DATE_SOURCES = (RAMP_API_KEYS, RAMP_PROVIDER_TOTALS, CLAUDE_SPEND_EXPORT)

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
    problem = _range_problem(source, start, end)
    if problem:
        raise InputError(problem)
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


def saved_files(workspace: Path, source: str, month: Month | None = None) -> list[SavedFile]:
    """Every saved file from a source, or only one month's, so a bad file elsewhere doesn't get in the way."""
    found: list[SavedFile] = []
    folders = month.label if month else "*"
    for path in sorted((workspace / "raw").glob(f"{folders}/{source}_*")):
        match = FILE_NAME.match(path.name)
        if match is None or match.group("source") != source:
            continue
        where = f"raw/{path.parent.name}/{path.name}"
        try:
            start = date.fromisoformat(match.group("start"))
            end = date.fromisoformat(match.group("end"))
        except ValueError:
            raise InputError(f"{where} has a date in its name that doesn't exist; rename or remove it") from None
        problem = _range_problem(source, start, end)
        if problem:
            raise InputError(f"{where} doesn't follow the saving rules ({problem}); remove it and save it again")
        found.append(SavedFile(path, source, start, end, match.group("saved")))
    return found


def partner_for(chosen: SavedFile, files: list[SavedFile]) -> SavedFile | None:
    """The last-saved file from another source that covers exactly the same days as chosen."""
    same_days = [saved for saved in files if (saved.start, saved.end) == (chosen.start, chosen.end)]
    return max(same_days, key=lambda saved: saved.saved_at, default=None)


def fullest_in_month(files: list[SavedFile], month: Month) -> SavedFile | None:
    """For month-to-date sources: the file reaching furthest into the month, the last-saved on a tie."""
    in_month = [saved for saved in files if Month.of(saved.start) == month]
    return max(in_month, key=lambda saved: (saved.end, saved.saved_at), default=None)


def snapshot_for(files: list[SavedFile], month: Month) -> SavedFile | None:
    """The people list to use for a month: the first day's one saved once it ended, else the newest.

    Using the first day after the month keeps a past month's teams the same no matter how
    many later snapshots are saved; within that day the last save wins, so a correction
    made the same day counts.
    """
    after = [saved for saved in files if saved.start >= month.last_day]
    if after:
        first_day = min(saved.start for saved in after)
        return max((saved for saved in after if saved.start == first_day), key=lambda saved: saved.saved_at)
    return max(files, key=lambda saved: (saved.start, saved.saved_at), default=None)


def _range_problem(source: str, start: date, end: date) -> str:
    if end < start:
        return f"the end date {end} is before the start date {start}"
    if Month.of(start) != Month.of(end):
        return "a saved file has to cover days within one month; save each month separately"
    if source in MONTH_TO_DATE_SOURCES and start.day != 1:
        return f"{source} covers the month to date, so it starts on the 1st ({Month.of(start).first_day})"
    if source == RAMP_PEOPLE and start != end:
        return f"{source} is a snapshot of one day; use the same start and end date"
    return ""


def _check_content(source: str, content: bytes) -> None:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InputError(f"the {source} data isn't UTF-8 text") from None
    label = f"the {source} data"
    if EXTENSIONS[source] == "json":
        try:
            document: object = json.loads(text, parse_float=Decimal)
        except json.JSONDecodeError as error:
            raise InputError(f"{label} isn't valid JSON: {error.msg}") from None
        # Read it the way the report will, so a file that would break the report is refused now.
        if source == RAMP_PEOPLE:
            parse_people(document, label)
        elif source == RAMP_PROVIDER_TOTALS:
            parse_provider_totals(document, label)
        else:
            parse_key_spend(document, label)
        return
    header = next(csv.reader(io.StringIO(text)), [])
    missing = [column for column in SPEND_EXPORT_REQUIRED_COLUMNS if column not in header]
    if missing:
        raise InputError(f"the spend export is missing the columns {missing}; is it the claude.ai spend report?")
    parse_spend_export(text, label)
