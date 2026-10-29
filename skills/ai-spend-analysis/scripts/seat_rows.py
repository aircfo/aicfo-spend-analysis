#!/usr/bin/env python3
"""Build the Seats tab's rows: everyone on the roster, each at the seat they really hold.

Usage:
  python3 seat_rows.py <folder> <out folder> [--members <member lists file>] [--existing <Seats tab rows>]

Reads Ramp's newest saved people list, the settings' seat types and the defaults in
<folder>/roster.json. Writes one file per person into <out folder>/seats/, laid out like the
Seats tab's own download, and beside it batch-1.json, batch-2.json and so on: lists of up to
50 writes, each ready to pass as `writes` to one ArtifactData `batch` call on the dashboard page.

--members is a JSON file of the member lists read from claude.ai's and ChatGPT's admin
pages, each person's email and seat type:

  {"anthropic": {"pat@acme.com": "premium", "sam@acme.com": "standard"}, "openai": {"sam@acme.com": "business"}}

A vendor's member list decides that vendor's seats: someone on it holds that seat, and
anyone not on it holds none. A vendor without a list uses the default for everyone.
Someone on a member list who isn't an active Ramp user still gets a row, with no team, and
is named so the operator can say which team they're on.

--existing is the Seats tab as ArtifactData saved it with `out_dir`. Anyone already on it is
left out, so a refresh adds only people new to Ramp and never overwrites a seat someone set.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import roster_to_people as roster

BATCH_SIZE = 50
NO_SEAT = "none"
# A row's id is its email, and the page's database allows only these characters in an id.
ROW_ID = re.compile(r"^[A-Za-z0-9_\-.~:@+]{1,200}$")

Row = dict[str, str]


class SeatRowsError(Exception):
    """Something to fix before the rows can be built."""


def build_rows(
    users: list[roster.RampUser],
    priced: dict[str, set[str]],
    defaults: dict[str, str],
    members: dict[str, dict[str, str]],
    existing: set[str],
) -> tuple[list[Row], list[str]]:
    """The rows to add, and lines telling the operator what to check."""
    vendors = sorted(vendor for vendor, seat_types in priced.items() if seat_types)
    problems = _unpriced(vendors, priced, defaults, members)
    if problems:
        raise SeatRowsError("; ".join(problems))
    in_ramp = {user.email: user for user in users}
    on_member_lists = {email for listed in members.values() for email in listed}
    active = [user for user in users if user.active]
    # Someone inactive in Ramp who still holds a seat is still paid for, so they stay.
    inactive_with_a_seat = [user for user in users if not user.active and user.email in on_member_lists]
    not_in_ramp = [roster.RampUser(email, "", "", False) for email in sorted(on_member_lists - set(in_ramp))]
    people = active + inactive_with_a_seat + not_in_ramp

    rows: list[Row] = []
    unusable: list[str] = []
    for person in people:
        if person.email in existing:
            continue
        if not ROW_ID.match(person.email):
            unusable.append(person.email)
            continue
        row = {"name": person.name, "email": person.email, "team": person.team}
        for vendor in vendors:
            listed = members.get(vendor)
            row[vendor] = (listed.get(person.email, NO_SEAT) if listed is not None else defaults.get(vendor, NO_SEAT)).lower()
        rows.append(row)

    notes: list[str] = []
    unknown_to_ramp = sorted(row["email"] for row in rows if row["email"] not in in_ramp)
    if unknown_to_ramp:
        notes.append(f"on a member list but not in Ramp, so ask which team they're on: {', '.join(unknown_to_ramp)}")
    inactive = sorted(row["email"] for row in rows if row["email"] in in_ramp and not in_ramp[row["email"]].active)
    if inactive:
        notes.append(f"inactive in Ramp but still holding a seat: {', '.join(inactive)}")
    if unusable:
        notes.append(f"left out, because an email with these characters can't be a row: {', '.join(unusable)}")
    return rows, notes


def write_batches(rows: list[Row], out: Path) -> list[Path]:
    """One file per person, and the batch files that write them, 50 to a batch."""
    (out / "seats").mkdir(parents=True, exist_ok=True)
    writes = []
    for row in rows:
        person = out / "seats" / f"{row['email']}.json"
        person.write_text(json.dumps(row), encoding="utf-8")
        writes.append({"op": "set", "collection": "seats", "doc_id": row["email"], "file_path": str(person.resolve())})
    batches = []
    for number, start in enumerate(range(0, len(writes), BATCH_SIZE), start=1):
        batch = out / f"batch-{number}.json"
        batch.write_text(json.dumps(writes[start:start + BATCH_SIZE], indent=1), encoding="utf-8")
        batches.append(batch)
    return batches


def existing_emails(path: Path) -> set[str]:
    document = roster.read_roster(path)
    rows = document.get("rows", []) if isinstance(document, dict) else []
    return {str(row.get("email", "")).strip().lower() for row in rows if isinstance(row, dict)}


def read_members(path: Path) -> dict[str, dict[str, str]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise SeatRowsError("the member lists file should hold {\"anthropic\": {email: seat type}, ...}")
    members: dict[str, dict[str, str]] = {}
    for vendor, listed in document.items():
        if not isinstance(listed, dict):
            raise SeatRowsError(f"the {vendor} member list should map each email to a seat type")
        members[str(vendor).lower()] = {str(email).strip().lower(): str(seat).strip() for email, seat in listed.items()}
    return members


def read_defaults(folder: Path) -> dict[str, str]:
    path = folder / "roster.json"
    document = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    defaults = document.get("defaults") if isinstance(document, dict) else None
    if not isinstance(defaults, dict):
        raise SeatRowsError(f"{path} has no defaults yet; ask the two default-seat questions first (Setup step 4.1)")
    return {str(vendor).lower(): str(seat).strip() for vendor, seat in defaults.items()}


def _unpriced(
    vendors: list[str], priced: dict[str, set[str]], defaults: dict[str, str], members: dict[str, dict[str, str]]
) -> list[str]:
    problems = []
    for vendor in vendors:
        allowed = priced[vendor] | {NO_SEAT}
        seats = Counter(seat.lower() for seat in members.get(vendor, {}).values())
        if vendor not in members:
            seats[defaults.get(vendor, NO_SEAT).lower()] += 1
        for seat in sorted(set(seats) - allowed):
            problems.append(f"{vendor} seat type {seat!r} isn't priced in settings, which price: {', '.join(sorted(priced[vendor]))}")
    for vendor in sorted(set(members) - set(vendors)):
        problems.append(f"there's a {vendor} member list, but settings price no {vendor} seats")
    return problems


def main(argv: Sequence[str]) -> int:
    options: dict[str, str | None] = {"--members": None, "--existing": None}
    positional: list[str] = []
    arguments = list(argv)
    while arguments:
        argument = arguments.pop(0)
        if argument in options and arguments:
            options[argument] = arguments.pop(0)
        else:
            positional.append(argument)
    if len(positional) != 2:
        raise SystemExit(__doc__)
    folder, out = Path(positional[0]).expanduser(), Path(positional[1]).expanduser()
    try:
        members_file, existing_rows = options["--members"], options["--existing"]
        members = read_members(Path(members_file).expanduser()) if members_file else {}
        existing = existing_emails(Path(existing_rows).expanduser()) if existing_rows else set()
        rows, notes = build_rows(
            roster.ramp_users(folder), roster.priced_seats(folder), read_defaults(folder), members, existing
        )
    except SeatRowsError as error:
        raise SystemExit(f"seat_rows: {error}") from None
    batches = write_batches(rows, out)
    print(f"Wrote {len(rows)} rows in {len(batches)} {'batch' if len(batches) == 1 else 'batches'} to {out}")
    held = Counter((vendor, row[vendor]) for row in rows for vendor in row if vendor not in roster.ROW_FIELDS)
    for (vendor, seat), count in sorted(held.items()):
        if seat != NO_SEAT:
            print(f"  {vendor} {seat}: {count}")
    for note in notes:
        print(f"  {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
