"""Building the Seats tab's rows from Ramp, the defaults and the member lists, in one pass."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import roster_to_people as roster
import seat_rows

PRICED = {"anthropic": {"standard", "premium"}, "openai": {"business"}}
DEFAULTS = {"anthropic": "standard", "openai": "none"}
PAT = roster.RampUser("pat.lee@example.com", "Pat Lee", "Sales", True)
SAM = roster.RampUser("sam.cho@example.com", "Sam Cho", "Engineering", True)
JO = roster.RampUser("jo.park@example.com", "Jo Park", "Engineering", False)


def by_email(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row["email"]: row for row in rows}


class BuildRowsTest(unittest.TestCase):
    def test_every_active_ramp_user_starts_at_the_defaults_without_member_lists(self) -> None:
        rows, notes = seat_rows.build_rows([PAT, SAM, JO], PRICED, DEFAULTS, {}, set())

        self.assertEqual(rows, [
            {"name": "Pat Lee", "email": "pat.lee@example.com", "team": "Sales", "anthropic": "standard", "openai": "none"},
            {"name": "Sam Cho", "email": "sam.cho@example.com", "team": "Engineering", "anthropic": "standard", "openai": "none"},
        ])
        self.assertEqual(notes, [])

    def test_a_vendors_member_list_decides_its_seats_and_others_keep_the_default(self) -> None:
        members = {"anthropic": {"pat.lee@example.com": "Premium"}}

        rows = by_email(seat_rows.build_rows([PAT, SAM], PRICED, DEFAULTS, members, set())[0])

        self.assertEqual(rows["pat.lee@example.com"]["anthropic"], "premium")
        self.assertEqual(rows["sam.cho@example.com"]["anthropic"], "none")
        self.assertEqual(rows["sam.cho@example.com"]["openai"], "none")

    def test_someone_on_a_member_list_but_not_in_ramp_gets_a_row_with_no_team(self) -> None:
        members = {"openai": {"new.hire@example.com": "business"}}

        rows, notes = seat_rows.build_rows([PAT], PRICED, DEFAULTS, members, set())

        self.assertEqual(by_email(rows)["new.hire@example.com"]["team"], "")
        self.assertEqual(notes, ["on a member list but not in Ramp, so ask which team they're on: new.hire@example.com"])

    def test_someone_inactive_in_ramp_who_still_holds_a_seat_stays(self) -> None:
        rows, notes = seat_rows.build_rows([PAT, JO], PRICED, DEFAULTS, {"anthropic": {"jo.park@example.com": "standard"}}, set())

        self.assertIn("jo.park@example.com", by_email(rows))
        self.assertEqual(notes, ["inactive in Ramp but still holding a seat: jo.park@example.com"])

    def test_people_already_on_the_tab_are_left_alone(self) -> None:
        rows, _ = seat_rows.build_rows([PAT, SAM], PRICED, DEFAULTS, {}, {"pat.lee@example.com"})

        self.assertEqual([row["email"] for row in rows], ["sam.cho@example.com"])

    def test_a_seat_type_settings_dont_price_is_refused_with_the_known_ones(self) -> None:
        with self.assertRaises(seat_rows.SeatRowsError) as refused:
            seat_rows.build_rows([PAT], PRICED, DEFAULTS, {"openai": {"pat.lee@example.com": "plus"}}, set())

        self.assertIn("openai seat type 'plus' isn't priced in settings, which price: business", str(refused.exception))


class WriteBatchesTest(unittest.TestCase):
    def test_writes_one_file_per_person_and_batches_of_at_most_fifty(self) -> None:
        rows = [{"name": "", "email": f"person{number}@example.com", "team": "Sales", "anthropic": "standard"} for number in range(51)]
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "rows"

            batches = seat_rows.write_batches(rows, out)

            first = json.loads(batches[0].read_text(encoding="utf-8"))
            second = json.loads(batches[1].read_text(encoding="utf-8"))
            self.assertEqual((len(batches), len(first), len(second)), (2, 50, 1))
            self.assertEqual({key: first[0][key] for key in ("op", "collection", "doc_id")},
                             {"op": "set", "collection": "seats", "doc_id": "person0@example.com"})
            self.assertEqual(json.loads(Path(first[0]["file_path"]).read_text(encoding="utf-8")), rows[0])


class RoundTripTest(unittest.TestCase):
    def test_the_rows_it_writes_read_back_as_a_roster(self) -> None:
        rows = [{"name": "Pat Lee", "email": "pat.lee@example.com", "team": "Sales", "anthropic": "standard"}]
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "rows"
            seat_rows.write_batches(rows, out)

            self.assertEqual(roster.read_roster(out), {"rows": [
                {"name": "Pat Lee", "email": "pat.lee@example.com", "team": "Sales", "seats": {"anthropic": "standard"}},
            ]})


class FolderTest(unittest.TestCase):
    def test_reads_ramp_settings_and_defaults_from_the_company_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "aicfo-spend.json").write_text(json.dumps(
                {"seats": {"anthropic": {"standard": {"price_usd": 25, "bought": 2}}}}), encoding="utf-8")
            (folder / "roster.json").write_text(json.dumps({"kept_in": "dashboard", "defaults": {"anthropic": "standard"}}))
            snapshot = folder / "raw/2026-10/ramp-people_2026-10-27_2026-10-27_saved-20261027T090000Z.json"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text(json.dumps({"users": [
                {"email": "Pat.Lee@example.com", "first_name": "Pat", "last_name": "Lee", "department_name": "Sales"},
            ]}), encoding="utf-8")

            seat_rows.main([str(folder), str(folder / "rows")])

            written = json.loads((folder / "rows" / "seats" / "pat.lee@example.com.json").read_text(encoding="utf-8"))
            self.assertEqual(written, {"name": "Pat Lee", "email": "pat.lee@example.com", "team": "Sales", "anthropic": "standard"})

    def test_says_to_ask_the_default_questions_first_when_there_are_no_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "aicfo-spend.json").write_text(json.dumps({"seats": {}}), encoding="utf-8")

            with self.assertRaises(SystemExit) as refused:
                seat_rows.main([str(folder), str(folder / "rows")])

            self.assertIn("ask the two default-seat questions first", str(refused.exception))


if __name__ == "__main__":
    unittest.main()
