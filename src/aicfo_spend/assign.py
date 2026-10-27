"""Who spent it: two lookups put each dollar on a team, or on Unassigned with the reason."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from aicfo_spend.months import Month
from aicfo_spend.settings import SETTINGS_FILE_NAME, Settings, SettingsError
from aicfo_spend.sources.people import PeopleFileRow
from aicfo_spend.sources.ramp import KeySpend, RampPerson
from aicfo_spend.spend_table import PRODUCT_SEATS, UNASSIGNED, SpendRow
from aicfo_spend.workspace import PEOPLE_FILE_NAME

# Covers someone missing from both lists and someone listed with no team: either way the
# fix is the same, give them a team on the people list.
REASON_NO_TEAM_ON_PEOPLE_LIST = "no team on the people list"
REASON_KEY_WITH_NO_TEAM = "key with no team"
REASON_EMPTY_SEAT = "empty seat"
# Ramp's total for a vendor is more than its keys add up to. The usual cause is a key deleted
# or rotated during the month: Ramp lists only the keys the vendor's console still has.
REASON_NOT_TIED_TO_A_KEY = "API spend not tied to a current key"

CENT = Decimal("0.01")
# Below this, a vendor's keys adding up to more than Ramp's total is rounding, not a mistake.
KEYS_OVER_TOTAL_TOLERANCE = Decimal("1.00")


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


def untied_api_spend(
    keys: Sequence[KeySpend], totals: Mapping[str, Decimal], label: str
) -> tuple[dict[str, Decimal], list[str]]:
    """Each vendor's spend Ramp counts but can't tie to a current key, in cents, and notes on any mismatch.

    Keys adding up to clearly more than Ramp's own total point to a copying mistake in the
    saved per-key answer, so that earns a note rather than a negative amount.
    """
    keys_by_vendor: dict[str, Decimal] = {}
    for key in keys:
        keys_by_vendor[key.vendor] = keys_by_vendor.get(key.vendor, Decimal(0)) + key.amount_usd
    untied: dict[str, Decimal] = {}
    notes: list[str] = []
    for vendor in sorted({*keys_by_vendor, *totals}):
        difference = (totals.get(vendor, Decimal(0)) - keys_by_vendor.get(vendor, Decimal(0))).quantize(
            CENT, ROUND_HALF_UP
        )
        if difference > 0:
            untied[vendor] = difference
            if vendor not in keys_by_vendor:
                notes.append(
                    f"Ramp's total in {label} has ${difference:,.2f} of {vendor} spend and none of its keys spent; "
                    "all of it counts as not tied to a current key"
                )
        elif -difference > KEYS_OVER_TOTAL_TOLERANCE:
            notes.append(
                f"{vendor}'s keys add up to ${-difference:,.2f} more than Ramp's total in {label}; "
                "check the saved per-key answer for a copying mistake"
            )
    return untied, notes


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
