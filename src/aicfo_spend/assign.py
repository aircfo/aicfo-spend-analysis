"""Who spent it: two lookups put each dollar on a team, or on Unassigned with the reason."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from aicfo_spend.months import Month
from aicfo_spend.settings import SETTINGS_FILE_NAME, Settings, SettingsError
from aicfo_spend.sources.people import PeopleFileRow
from aicfo_spend.sources.ramp import RampPerson
from aicfo_spend.spend_table import PRODUCT_SEATS, UNASSIGNED, SpendRow
from aicfo_spend.workspace import PEOPLE_FILE_NAME

# Covers someone missing from both lists and someone listed with no team: either way the
# fix is the same, give them a team on the people list.
REASON_NO_TEAM_ON_PEOPLE_LIST = "no team on the people list"
REASON_KEY_WITH_NO_TEAM = "key with no team"
REASON_EMPTY_SEAT = "empty seat"


@dataclass(frozen=True)
class Placement:
    team: str
    reason: str = ""


@dataclass(frozen=True)
class Directory:
    """Each known person's team: Ramp's department, unless people.csv names a team for them."""

    teams: Mapping[str, str]
    # Team names are matched without regard to case; the first spelling seen is the one shown.
    spellings: Mapping[str, str]

    @classmethod
    def build(cls, ramp_people: Mapping[str, RampPerson], people_file: Sequence[PeopleFileRow]) -> Directory:
        spellings: dict[str, str] = {}
        teams: dict[str, str] = {}
        named = [(email, person.team) for email, person in ramp_people.items()]
        named += [(row.email, row.team) for row in people_file]
        for email, team in named:
            if team:
                teams[email] = spellings.setdefault(team.casefold(), team)
        return cls(teams, spellings)

    def place_person(self, email: str) -> Placement:
        team = self.teams.get(email.lower(), "")
        return Placement(team) if team else Placement(UNASSIGNED, REASON_NO_TEAM_ON_PEOPLE_LIST)

    def place_key(self, department_tag: str, owner_email: str) -> Placement:
        if department_tag:
            return Placement(self.spellings.get(department_tag.casefold(), department_tag))
        team = self.teams.get(owner_email.lower(), "") if owner_email else ""
        return Placement(team) if team else Placement(UNASSIGNED, REASON_KEY_WITH_NO_TEAM)


def seat_rows(
    month: Month, people_file: Sequence[PeopleFileRow], settings: Settings, directory: Directory
) -> tuple[list[SpendRow], list[str]]:
    """A full month's price for every seat people.csv lists, plus Unassigned rows for empty seats."""
    rows: list[SpendRow] = []
    held: Counter[tuple[str, str]] = Counter()
    for person in people_file:
        if not person.vendor:
            continue
        plan = settings.seats.get(person.vendor, {}).get(person.seat_type)
        if plan is None:
            raise SettingsError(
                f"{PEOPLE_FILE_NAME} gives {person.email} a {person.vendor} {person.seat_type} seat, but "
                f"{SETTINGS_FILE_NAME} has no price for it; add seats.{person.vendor}.{person.seat_type}"
            )
        placement = directory.place_person(person.email)
        rows.append(
            SpendRow(
                start_date=month.first_day,
                end_date=month.last_day,
                vendor=person.vendor,
                product=PRODUCT_SEATS,
                amount_usd=plan.price_usd,
                source_file=PEOPLE_FILE_NAME,
                person=person.email,
                team=placement.team,
                reason=placement.reason,
            )
        )
        held[(person.vendor, person.seat_type)] += 1

    notes: list[str] = []
    for vendor, plans in settings.seats.items():
        for seat_type, plan in plans.items():
            holders = held[(vendor, seat_type)]
            if plan.bought > holders:
                rows.append(
                    SpendRow(
                        start_date=month.first_day,
                        end_date=month.last_day,
                        vendor=vendor,
                        product=PRODUCT_SEATS,
                        amount_usd=plan.price_usd * (plan.bought - holders),
                        source_file=SETTINGS_FILE_NAME,
                        team=UNASSIGNED,
                        reason=REASON_EMPTY_SEAT,
                    )
                )
            elif holders > plan.bought:
                notes.append(
                    f"{PEOPLE_FILE_NAME} lists {holders} {vendor} {seat_type} seats, but {SETTINGS_FILE_NAME} "
                    f"says {plan.bought} were bought; check which is right"
                )
    return rows, notes
