"""From the seats roster people edit to the engine's people.csv: one row per seat held."""
from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import roster_to_people as roster

PRICED = {"anthropic": {"standard", "premium"}, "openai": {"standard", "premium"}}
RAMP = {"pat.lee@example.com": ("Pat Lee", "Sales"), "sam.cho@example.com": ("Sam Cho", "Engineering")}


def person(email: str, team: str, anthropic: str = "Standard", openai: str = "No account", name: str = "") -> dict[str, object]:
    return {"name": name, "email": email, "team": team, "seats": {"anthropic": anthropic, "openai": openai}}


class PeopleRowsTest(unittest.TestCase):
    def test_each_seat_held_is_one_row_and_no_account_is_none(self) -> None:
        rows, _ = roster.people_rows(
            {"rows": [person("pat.lee@example.com", "Sales", openai="Premium"), person("sam.cho@example.com", "Engineering", anthropic="No account")]},
            PRICED, RAMP,
        )

        self.assertEqual(
            [(row["email"], row["vendor"], row["seat_type"]) for row in rows],
            [("pat.lee@example.com", "anthropic", "standard"), ("pat.lee@example.com", "openai", "premium")],
        )

    def test_name_and_team_are_left_to_ramp_unless_the_roster_says_otherwise(self) -> None:
        rows, summary = roster.people_rows(
            {"rows": [
                person("pat.lee@example.com", "sales"),
                person("sam.cho@example.com", "Research"),
                person("contractor@example.com", "Engineering", name="Jo Park"),
            ]},
            PRICED, RAMP,
        )

        by_email = {row["email"]: row for row in rows}
        self.assertEqual((by_email["pat.lee@example.com"]["name"], by_email["pat.lee@example.com"]["team"]), ("", ""))
        self.assertEqual(by_email["sam.cho@example.com"]["team"], "Research")
        self.assertEqual(
            (by_email["contractor@example.com"]["name"], by_email["contractor@example.com"]["team"]), ("Jo Park", "Engineering")
        )
        self.assertIn("not in Ramp, so their name and team come from the roster: contractor@example.com", summary)

    def test_a_seat_type_settings_dont_price_is_refused_with_the_known_ones(self) -> None:
        with self.assertRaises(roster.RosterError) as refused:
            roster.people_rows({"rows": [person("pat.lee@example.com", "Sales", anthropic="Premim")]}, PRICED, RAMP)

        self.assertIn("pat.lee@example.com has a anthropic seat 'Premim', but settings price only: premium, standard", str(refused.exception))

    def test_a_person_listed_twice_is_refused(self) -> None:
        with self.assertRaises(roster.RosterError) as refused:
            roster.people_rows({"rows": [person("pat.lee@example.com", "Sales"), person("Pat.Lee@example.com", "Sales")]}, PRICED, RAMP)

        self.assertIn("pat.lee@example.com is on the roster twice", str(refused.exception))

    def test_the_summary_counts_seats_held_by_type(self) -> None:
        _, summary = roster.people_rows(
            {"rows": [person("pat.lee@example.com", "Sales"), person("sam.cho@example.com", "Engineering")]}, PRICED, RAMP
        )

        self.assertEqual(summary, ["anthropic standard: 2 held"])


class CompareWithExportTest(unittest.TestCase):
    def test_names_idle_claude_seats_and_people_missing_from_the_roster(self) -> None:
        roster_rows = {"rows": [
            person("pat.lee@example.com", "Sales"),
            person("sam.cho@example.com", "Engineering"),
            person("jo.park@example.com", "Engineering", anthropic="No account"),
        ]}

        lines = roster.compare_with_export(roster_rows, {"pat.lee@example.com", "new.hire@example.com"})

        self.assertEqual(lines, [
            "Claude seat on the roster, but not in the export: sam.cho@example.com",
            "In the export, but not on the roster: new.hire@example.com",
        ])

    def test_says_so_when_they_match(self) -> None:
        lines = roster.compare_with_export({"rows": [person("pat.lee@example.com", "Sales")]}, {"pat.lee@example.com"})

        self.assertEqual(lines, ["The roster's Claude seats match the export."])


class FolderTest(unittest.TestCase):
    def test_writes_people_csv_from_the_settings_and_the_newest_ramp_list(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "aicfo-spend.json").write_text(
                json.dumps({"seats": {"Anthropic": {"Standard": {"price_usd": 25, "bought": 3}}}}), encoding="utf-8"
            )
            snapshot = folder / "raw/2026-10/ramp-people_2026-10-27_2026-10-27_saved-20261027T090000Z.json"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text(json.dumps({"users": [
                {"email": "pat.lee@example.com", "first_name": "Pat", "last_name": "Lee", "department_name": "Sales"}
            ]}), encoding="utf-8")
            roster_file = folder / "roster-rows.json"
            roster_file.write_text(json.dumps({"rows": [person("pat.lee@example.com", "Sales")]}), encoding="utf-8")

            roster.main([str(folder), str(roster_file)])

            with (folder / "people.csv").open(encoding="utf-8", newline="") as written:
                self.assertEqual(
                    list(csv.reader(written)),
                    [list(roster.PEOPLE_COLUMNS), ["pat.lee@example.com", "", "", "anthropic", "standard"]],
                )


class SeatsTabDownloadTest(unittest.TestCase):
    def test_reads_the_seats_tab_download_one_file_per_person_as_it_comes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            download = Path(temporary) / "seats-read"
            (download / "seats").mkdir(parents=True)
            for email, team, seat in (("pat.lee@example.com", "Sales", "standard"), ("jo.park@example.com", "", "none")):
                (download / "seats" / f"{email}.json").write_text(json.dumps(
                    {"name": "", "email": email, "team": team, "anthropic": seat, "openai": "none"}
                ), encoding="utf-8")

            rows = roster.read_roster(download)

            self.assertEqual(rows, {"rows": [
                {"name": "", "email": "jo.park@example.com", "team": "", "seats": {"anthropic": "none", "openai": "none"}},
                {"name": "", "email": "pat.lee@example.com", "team": "Sales", "seats": {"anthropic": "standard", "openai": "none"}},
            ]})

    def test_a_rows_file_still_reads_as_before(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rows_file = Path(temporary) / "roster-rows.json"
            rows_file.write_text(json.dumps({"rows": [person("pat.lee@example.com", "Sales")]}), encoding="utf-8")

            self.assertEqual(roster.read_roster(rows_file), {"rows": [person("pat.lee@example.com", "Sales")]})


if __name__ == "__main__":
    unittest.main()
