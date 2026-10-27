from decimal import Decimal

import pytest

from aicfo_spend.assign import (
    REASON_EMPTY_SEAT,
    REASON_KEY_WITH_NO_TEAM,
    REASON_NO_TEAM_ON_PEOPLE_LIST,
    Directory,
    Placement,
    seat_rows,
)
from aicfo_spend.months import Month
from aicfo_spend.settings import SettingsError, parse_settings
from aicfo_spend.sources.people import PeopleFileRow
from aicfo_spend.sources.ramp import RampPerson
from aicfo_spend.spend_table import UNASSIGNED

SEPTEMBER = Month(2026, 9)
RAMP_PEOPLE = {
    "pat.lee@example.com": RampPerson("pat.lee@example.com", "Pat Lee", "Sales", True),
    "sam.cho@example.com": RampPerson("sam.cho@example.com", "Sam Cho", "Engineering", True),
}


def seat(email: str, vendor: str = "anthropic", seat_type: str = "standard", team: str = "") -> PeopleFileRow:
    return PeopleFileRow(email=email, name="", team=team, vendor=vendor, seat_type=seat_type)


def test_a_person_gets_their_ramp_department() -> None:
    assert Directory.build(RAMP_PEOPLE, []).place_person("Pat.Lee@example.com") == Placement("Sales")


def test_a_team_in_the_people_file_overrides_ramp() -> None:
    directory = Directory.build(RAMP_PEOPLE, [seat("pat.lee@example.com", team="Partnerships")])

    assert directory.place_person("pat.lee@example.com") == Placement("Partnerships")


def test_someone_no_list_places_on_a_team_is_unassigned() -> None:
    no_department = {"max.ito@example.com": RampPerson("max.ito@example.com", "Max Ito", "", True)}
    directory = Directory.build({**RAMP_PEOPLE, **no_department}, [])

    assert directory.place_person("new.hire@example.com") == Placement(UNASSIGNED, REASON_NO_TEAM_ON_PEOPLE_LIST)
    assert directory.place_person("max.ito@example.com") == Placement(UNASSIGNED, REASON_NO_TEAM_ON_PEOPLE_LIST)


def test_a_key_goes_to_its_department_tag_first() -> None:
    directory = Directory.build(RAMP_PEOPLE, [])

    assert directory.place_key("Operations", "sam.cho@example.com") == Placement("Operations")


def test_team_names_match_whatever_their_capitalization() -> None:
    directory = Directory.build(RAMP_PEOPLE, [seat("max.ito@example.com", team="SALES")])

    assert directory.place_key("sales", "") == Placement("Sales")
    assert directory.place_person("max.ito@example.com") == Placement("Sales")


def test_a_key_without_a_tag_goes_to_its_owners_team() -> None:
    assert Directory.build(RAMP_PEOPLE, []).place_key("", "sam.cho@example.com") == Placement("Engineering")


def test_a_key_with_no_tag_and_no_known_owner_is_unassigned() -> None:
    directory = Directory.build(RAMP_PEOPLE, [])

    assert directory.place_key("", "") == Placement(UNASSIGNED, REASON_KEY_WITH_NO_TEAM)
    assert directory.place_key("", "gone@example.com") == Placement(UNASSIGNED, REASON_KEY_WITH_NO_TEAM)


def test_each_listed_seat_costs_a_full_month_and_empty_seats_are_unassigned() -> None:
    settings = parse_settings({"seats": {"anthropic": {"standard": {"price_usd": 25, "bought": 3}}}})
    people = [seat("pat.lee@example.com"), seat("sam.cho@example.com")]

    rows, notes = seat_rows(SEPTEMBER, people, settings, Directory.build(RAMP_PEOPLE, people))

    assert [(row.person, row.team, row.amount_usd) for row in rows] == [
        ("pat.lee@example.com", "Sales", Decimal(25)),
        ("sam.cho@example.com", "Engineering", Decimal(25)),
        ("", UNASSIGNED, Decimal(25)),
    ]
    assert rows[-1].reason == REASON_EMPTY_SEAT
    assert all((row.start_date, row.end_date) == (SEPTEMBER.first_day, SEPTEMBER.last_day) for row in rows)
    assert notes == []


def test_notes_more_seats_held_than_bought() -> None:
    settings = parse_settings({"seats": {"anthropic": {"standard": {"price_usd": 25, "bought": 1}}}})
    people = [seat("pat.lee@example.com"), seat("sam.cho@example.com")]

    rows, notes = seat_rows(SEPTEMBER, people, settings, Directory.build(RAMP_PEOPLE, people))

    assert len(rows) == 2
    assert notes == [
        "people.csv lists 2 anthropic standard seats, but aicfo-spend.json says 1 were bought; check which is right"
    ]


def test_explains_a_seat_with_no_price_in_settings() -> None:
    settings = parse_settings({"seats": {"anthropic": {"standard": {"price_usd": 25, "bought": 1}}}})

    with pytest.raises(SettingsError, match="add seats.anthropic.premium"):
        seat_rows(SEPTEMBER, [seat("pat.lee@example.com", seat_type="premium")], settings, Directory.build({}, []))
