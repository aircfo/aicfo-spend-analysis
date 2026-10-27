import stat
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from aicfo_spend.errors import InputError
from aicfo_spend.months import Month
from aicfo_spend.raw_store import (
    CLAUDE_SPEND_EXPORT,
    RAMP_API_KEYS,
    RAMP_PEOPLE,
    RAMP_PROVIDER_TOTALS,
    fullest_in_month,
    save_raw,
    saved_files,
    snapshot_for,
)
from tests.factories import ramp_key, ramp_keys_answer, ramp_people_answer, ramp_totals_answer, spend_export

SEPTEMBER = Month(2026, 9)
SEPT_1 = date(2026, 9, 1)


def at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 10, day, hour, 0, 0, tzinfo=timezone.utc)


def test_saves_a_read_only_file_whose_name_says_what_it_covers(tmp_path: Path) -> None:
    path = save_raw(tmp_path, RAMP_API_KEYS, SEPT_1, date(2026, 9, 30), ramp_keys_answer(), saved_at=at(1))

    assert path == tmp_path / "raw/2026-09/ramp-api-keys_2026-09-01_2026-09-30_saved-20261001T090000Z.json"
    assert not path.stat().st_mode & stat.S_IWUSR


def test_a_second_download_becomes_a_new_file(tmp_path: Path) -> None:
    end = date(2026, 9, 14)
    first = save_raw(tmp_path, RAMP_API_KEYS, SEPT_1, end, ramp_keys_answer(), saved_at=at(1, 9))
    second = save_raw(tmp_path, RAMP_API_KEYS, SEPT_1, end, ramp_keys_answer(), saved_at=at(1, 10))

    assert first.exists() and second.exists()
    with pytest.raises(InputError, match="already exists"):
        save_raw(tmp_path, RAMP_API_KEYS, SEPT_1, end, ramp_keys_answer(), saved_at=at(1, 10))


@pytest.mark.parametrize(
    ("source", "start", "end", "content", "message"),
    [
        (RAMP_API_KEYS, date(2026, 9, 14), date(2026, 9, 15), ramp_keys_answer(), "starts on the 1st"),
        (CLAUDE_SPEND_EXPORT, SEPT_1, date(2026, 10, 5), spend_export(), "within one month"),
        (CLAUDE_SPEND_EXPORT, date(2026, 9, 14), SEPT_1, spend_export(), "before the start date"),
        (RAMP_PEOPLE, SEPT_1, date(2026, 9, 2), ramp_people_answer(), "snapshot of one day"),
        (RAMP_API_KEYS, SEPT_1, SEPT_1, b'{"keys": []}', 'with a "data" list'),
        (RAMP_API_KEYS, SEPT_1, SEPT_1, b"not json", "isn't valid JSON"),
        (RAMP_API_KEYS, SEPT_1, SEPT_1, ramp_keys_answer(ramp_key("app", 100, currency="EUR")), "only USD"),
        (RAMP_API_KEYS, SEPT_1, SEPT_1, b'{"data": ["not a key"]}', "isn't a JSON object"),
        (RAMP_PROVIDER_TOTALS, date(2026, 9, 2), date(2026, 9, 3), ramp_totals_answer(), "starts on the 1st"),
        (RAMP_PROVIDER_TOTALS, SEPT_1, SEPT_1, ramp_keys_answer(), "AI spend summary"),
        (RAMP_PROVIDER_TOTALS, SEPT_1, SEPT_1, b'{"cost": {"data": [{"total_cost": 1.5}]}}', "no provider"),
        (RAMP_PROVIDER_TOTALS, SEPT_1, SEPT_1, b'{"cost": {"data": [{"provider": "OPENAI", "total_cost": "1.5"}]}}',
         "isn't a number of dollars"),
        (CLAUDE_SPEND_EXPORT, SEPT_1, SEPT_1, b"email,spend\n", "missing the columns"),
        (
            CLAUDE_SPEND_EXPORT,
            SEPT_1,
            SEPT_1,
            spend_export(("pat.lee@example.com", "claude-opus", "$12.50")),
            "line 2 has a spend that isn't a number",
        ),
        ("quickbooks", SEPT_1, SEPT_1, b"", "unknown source"),
    ],
)
def test_refuses_a_download_that_would_break_the_report(
    tmp_path: Path, source: str, start: date, end: date, content: bytes, message: str
) -> None:
    with pytest.raises(InputError, match=message):
        save_raw(tmp_path, source, start, end, content)

    assert not list(tmp_path.rglob("*.*"))


def test_uses_the_download_reaching_furthest_into_the_month(tmp_path: Path) -> None:
    save_raw(tmp_path, CLAUDE_SPEND_EXPORT, SEPT_1, date(2026, 9, 22), spend_export(), saved_at=at(2))
    full = save_raw(tmp_path, CLAUDE_SPEND_EXPORT, SEPT_1, date(2026, 9, 30), spend_export(), saved_at=at(1))
    october = date(2026, 10, 1)
    save_raw(tmp_path, CLAUDE_SPEND_EXPORT, october, october, spend_export(), saved_at=at(3))

    chosen = fullest_in_month(saved_files(tmp_path, CLAUDE_SPEND_EXPORT), SEPTEMBER)

    assert chosen is not None and chosen.path == full


def test_a_past_month_keeps_the_first_people_list_saved_after_it(tmp_path: Path) -> None:
    def snapshot(day: date) -> Path:
        return save_raw(tmp_path, RAMP_PEOPLE, day, day, ramp_people_answer(), saved_at=at(27))

    snapshot(date(2026, 9, 15))
    first_after = snapshot(date(2026, 10, 2))
    newest = snapshot(date(2026, 11, 3))
    files = saved_files(tmp_path, RAMP_PEOPLE)

    september = snapshot_for(files, SEPTEMBER)
    december = snapshot_for(files, Month(2026, 12))

    assert september is not None and september.path == first_after
    assert december is not None and december.path == newest


def test_a_correction_saved_the_same_day_replaces_that_days_people_list(tmp_path: Path) -> None:
    day = date(2026, 10, 2)
    save_raw(tmp_path, RAMP_PEOPLE, day, day, ramp_people_answer(), saved_at=at(2, 9))
    corrected = save_raw(tmp_path, RAMP_PEOPLE, day, day, ramp_people_answer(), saved_at=at(2, 15))

    chosen = snapshot_for(saved_files(tmp_path, RAMP_PEOPLE), SEPTEMBER)

    assert chosen is not None and chosen.path == corrected


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("ramp-api-keys_2026-09-31_2026-09-31_saved-20261001T090000Z.json", "date in its name that doesn't exist"),
        ("ramp-api-keys_2026-09-03_2026-09-07_saved-20261001T090000Z.json", "starts on the 1st"),
    ],
)
def test_a_hand_placed_file_that_breaks_the_rules_is_named(tmp_path: Path, name: str, message: str) -> None:
    folder = tmp_path / "raw" / "2026-09"
    folder.mkdir(parents=True)
    (folder / name).write_bytes(ramp_keys_answer())

    with pytest.raises(InputError, match=message) as error:
        saved_files(tmp_path, RAMP_API_KEYS)

    assert name in str(error.value)
