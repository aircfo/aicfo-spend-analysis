"""Read people.csv: the seats each person holds, plus anyone Ramp doesn't know."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from aicfo_spend.errors import InputError
from aicfo_spend.workspace import PEOPLE_COLUMNS, PEOPLE_FILE_NAME


@dataclass(frozen=True)
class PeopleFileRow:
    email: str
    name: str
    # When set, overrides Ramp's department for this person.
    team: str
    vendor: str
    seat_type: str


def read_people_file(workspace: Path) -> list[PeopleFileRow]:
    path = workspace / PEOPLE_FILE_NAME
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in PEOPLE_COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise InputError(f"{PEOPLE_FILE_NAME} is missing the columns {missing}")
        rows: list[PeopleFileRow] = []
        for line_number, record in enumerate(reader, start=2):
            # Spreadsheets often leave trailing lines of empty cells; they aren't mistakes.
            if not any((value or "").strip() for value in record.values() if isinstance(value, str)):
                continue
            row = PeopleFileRow(
                email=(record["email"] or "").strip().lower(),
                name=(record["name"] or "").strip(),
                team=(record["team"] or "").strip(),
                vendor=(record["vendor"] or "").strip().lower(),
                seat_type=(record["seat_type"] or "").strip().lower(),
            )
            if not row.email:
                raise InputError(f"{PEOPLE_FILE_NAME} line {line_number} has no email")
            if bool(row.vendor) != bool(row.seat_type):
                raise InputError(
                    f"{PEOPLE_FILE_NAME} line {line_number} needs both vendor and seat_type, or neither"
                )
            rows.append(row)
    return rows
