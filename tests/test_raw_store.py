import stat
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from aicfo_spend.errors import InputError
from aicfo_spend.months import Month
from aicfo_spend.raw_store import (
    CLAUDE_SPEND_EXPORT,
    RAMP_API_KEYS,
    fullest_in_month,
    newest_per_day,
    save_raw,
    saved_files,
)
from tests.factories import ramp_key, ramp_keys_answer, spend_export

SEPTEMBER = Month(2026, 9)
DAY = date(2026, 9, 14)


def at(hour: int) -> datetime:
    return datetime(2026, 10, 1, hour, 0, 0, tzinfo=timezone.utc)


def test_saves_a_read_only_file_whose_name_says_what_it_covers(tmp_path: Path) -> None:
    path = save_raw(tmp_path, RAMP_API_KEYS, DAY, DAY, ramp_keys_answer(), saved_at=at(9))

    assert path == tmp_path / "raw/2026-09/ramp-api-keys_2026-09-14_2026-09-14_saved-20261001T090000Z.json"
    assert not path.stat().st_mode & stat.S_IWUSR


def test_a_second_download_becomes_a_new_file(tmp_path: Path) -> None:
    first = save_raw(tmp_path, RAMP_API_KEYS, DAY, DAY, ramp_keys_answer(), saved_at=at(9))
    second = save_raw(tmp_path, RAMP_API_KEYS, DAY, DAY, ramp_keys_answer(), saved_at=at(10))

    assert first.exists() and second.exists()
    with pytest.raises(InputError, match="already exists"):
        save_raw(tmp_path, RAMP_API_KEYS, DAY, DAY, ramp_keys_answer(), saved_at=at(10))


@pytest.mark.parametrize(
    ("source", "start", "end", "content", "message"),
    [
        (RAMP_API_KEYS, DAY, date(2026, 9, 15), ramp_keys_answer(), "one day at a time"),
        (CLAUDE_SPEND_EXPORT, date(2026, 9, 20), date(2026, 10, 5), spend_export(), "within one month"),
        (CLAUDE_SPEND_EXPORT, DAY, date(2026, 9, 1), spend_export(), "before the start date"),
        (RAMP_API_KEYS, DAY, DAY, b'{"keys": []}', 'with a "data" list'),
        (RAMP_API_KEYS, DAY, DAY, b"not json", "isn't valid JSON"),
        (CLAUDE_SPEND_EXPORT, DAY, DAY, b"email,spend\n", "missing the columns"),
        ("quickbooks", DAY, DAY, b"", "unknown source"),
    ],
)
def test_refuses_a_download_it_could_misread(
    tmp_path: Path, source: str, start: date, end: date, content: bytes, message: str
) -> None:
    with pytest.raises(InputError, match=message):
        save_raw(tmp_path, source, start, end, content)

    assert not (tmp_path / "raw").exists() or not any((tmp_path / "raw").rglob("*.*"))


def test_uses_the_last_saved_file_for_each_day(tmp_path: Path) -> None:
    save_raw(tmp_path, RAMP_API_KEYS, DAY, DAY, ramp_keys_answer(ramp_key("app", 100)), saved_at=at(9))
    later = save_raw(tmp_path, RAMP_API_KEYS, DAY, DAY, ramp_keys_answer(ramp_key("app", 150)), saved_at=at(11))
    other_month = date(2026, 10, 1)
    save_raw(tmp_path, RAMP_API_KEYS, other_month, other_month, ramp_keys_answer(), saved_at=at(9))

    chosen = newest_per_day(saved_files(tmp_path, RAMP_API_KEYS), SEPTEMBER)

    assert list(chosen) == [DAY]
    assert chosen[DAY].path == later


def test_uses_the_export_reaching_furthest_into_the_month(tmp_path: Path) -> None:
    first = date(2026, 9, 1)
    save_raw(tmp_path, CLAUDE_SPEND_EXPORT, first, date(2026, 9, 22), spend_export(), saved_at=at(9))
    full = save_raw(tmp_path, CLAUDE_SPEND_EXPORT, first, date(2026, 9, 30), spend_export(), saved_at=at(8))

    chosen = fullest_in_month(saved_files(tmp_path, CLAUDE_SPEND_EXPORT), SEPTEMBER)

    assert chosen is not None and chosen.path == full
