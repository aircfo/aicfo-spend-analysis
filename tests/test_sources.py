from decimal import Decimal
from pathlib import Path

import pytest

from aicfo_spend.errors import InputError
from aicfo_spend.sources.people import read_people_file
from aicfo_spend.sources.ramp import read_key_spend, read_people
from aicfo_spend.vendors.anthropic.spend_export import read_spend_export
from aicfo_spend.workspace import PEOPLE_FILE_NAME
from tests.factories import ramp_key, ramp_keys_answer, ramp_people_answer, ramp_user, spend_export


def write(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


def test_reads_key_spend_in_dollars_from_cents(tmp_path: Path) -> None:
    path = write(tmp_path / "keys.json", ramp_keys_answer(ramp_key("app", 1340, "pat.lee@example.com", "Sales")))

    [spend] = read_key_spend(path)

    assert spend.amount_usd == Decimal("13.40")
    assert spend.vendor == "anthropic"
    assert spend.key_name == "app"
    assert spend.owner_email == "pat.lee@example.com"
    assert spend.department_tag == "Sales"


def test_keeps_fractions_of_a_cent_exact(tmp_path: Path) -> None:
    path = write(tmp_path / "keys.json", ramp_keys_answer(ramp_key("app", 1234.5)))

    [spend] = read_key_spend(path)

    assert spend.amount_usd == Decimal("12.345")


def test_leaves_out_keys_that_spent_nothing_or_have_no_estimate(tmp_path: Path) -> None:
    path = write(
        tmp_path / "keys.json", ramp_keys_answer(ramp_key("idle", 0), ramp_key("unpriced", None), ramp_key("busy", 5))
    )

    assert [spend.key_name for spend in read_key_spend(path)] == ["busy"]


def test_reads_a_key_with_no_owner_and_no_tags(tmp_path: Path) -> None:
    path = write(tmp_path / "keys.json", ramp_keys_answer(ramp_key("orphan", 99, provider="OPENAI")))

    [spend] = read_key_spend(path)

    assert (spend.vendor, spend.owner_email, spend.department_tag) == ("openai", "", "")


def test_names_the_key_whose_cost_it_cannot_read(tmp_path: Path) -> None:
    path = write(tmp_path / "keys.json", ramp_keys_answer(ramp_key("app", 100, currency="EUR")))

    with pytest.raises(InputError, match="key 'app' is priced in 'EUR'; only USD"):
        read_key_spend(path)


def test_reads_ramp_people_by_lowercase_email(tmp_path: Path) -> None:
    path = write(
        tmp_path / "people.json",
        ramp_people_answer(ramp_user("Pat.Lee@Example.com", "Sales"), ramp_user("sam.cho@example.com", "", True)),
    )

    people = read_people(path)

    assert people["pat.lee@example.com"].team == "Sales"
    assert people["pat.lee@example.com"].active
    assert not people["sam.cho@example.com"].active


def test_reads_seats_and_team_overrides_from_the_people_file(tmp_path: Path) -> None:
    (tmp_path / PEOPLE_FILE_NAME).write_text(
        "email,name,team,vendor,seat_type\n"
        "PAT.LEE@example.com,,,Anthropic,Premium\n"
        "max.ito@example.com,Max Ito,Finance,,\n",
        encoding="utf-8",
    )

    seat, outsider = read_people_file(tmp_path)

    assert (seat.email, seat.vendor, seat.seat_type, seat.team) == ("pat.lee@example.com", "anthropic", "premium", "")
    assert (outsider.team, outsider.vendor) == ("Finance", "")


def test_skips_blank_lines_a_spreadsheet_leaves_in_the_people_file(tmp_path: Path) -> None:
    (tmp_path / PEOPLE_FILE_NAME).write_text(
        "email,name,team,vendor,seat_type\npat.lee@example.com,,,anthropic,standard\n,,,,\n", encoding="utf-8"
    )

    assert [row.email for row in read_people_file(tmp_path)] == ["pat.lee@example.com"]


def test_people_file_needs_vendor_and_seat_type_together(tmp_path: Path) -> None:
    (tmp_path / PEOPLE_FILE_NAME).write_text(
        "email,name,team,vendor,seat_type\npat.lee@example.com,,,anthropic,\n", encoding="utf-8"
    )

    with pytest.raises(InputError, match="line 2 needs both vendor and seat_type"):
        read_people_file(tmp_path)


def test_spend_export_counts_net_spend_and_skips_zero_rows(tmp_path: Path) -> None:
    path = write(
        tmp_path / "export.csv",
        spend_export(("pat.lee@example.com", "claude-opus", "12.3456"), ("sam.cho@example.com", "claude-haiku", "0")),
    )

    [usage] = read_spend_export(path)

    assert (usage.email, usage.model, usage.amount_usd) == ("pat.lee@example.com", "claude-opus", Decimal("12.3456"))


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "twelve"])
def test_spend_export_refuses_a_spend_that_isnt_a_number(tmp_path: Path, amount: str) -> None:
    path = write(tmp_path / "export.csv", spend_export(("pat.lee@example.com", "claude-opus", amount)))

    with pytest.raises(InputError, match="line 2 has a spend that isn't a number"):
        read_spend_export(path)
