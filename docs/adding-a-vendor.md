# Adding a vendor

Most vendors need no code. Find the cases that fit the vendor; only the last one involves writing anything.

## 1. Its API spend is in Ramp: nothing to do

Ramp's AI spend covers the API keys of every provider whose Admin key is plugged into Ramp; today that's Anthropic and OpenAI. The month-to-date `ramp-api-keys` download already holds all of them, and the report lists each under its own vendor name. Each key lands on a team the same way: its Ramp Department tag, else its owner's team.

## 2. It sells seats: two settings

Add one row to `people.csv` for each seat: the person's email, the vendor and the seat type. Leave `name` and `team` blank for anyone Ramp already knows.

```csv
email,name,team,vendor,seat_type
pat.lee@example.com,,,openai,business
```

Then add the seat price and the number of seats bought to `aicfo-spend.json`:

```json
"seats": {
  "anthropic": {"standard": {"price_usd": 25, "bought": 10}},
  "openai": {"business": {"price_usd": 30, "bought": 4}}
}
```

The seats show up in `report`, `forecast` and `pace` like any other. A paid seat nobody holds shows as Unassigned, reason *empty seat*. Check the file with `aicfo-spend settings <folder>`.

## 3. It has its own usage export: a reader and three hooks

This is the only case that needs code, for a vendor whose per-person usage isn't in Ramp. The claude.ai spend export is the model to copy.

1. **A reader** in `src/aicfo_spend/vendors/<vendor>/`. It's one function that turns the saved file into rows of email, model and amount, and refuses a file it can't read with an `InputError` that names the line. See `vendors/anthropic/spend_export.py`.
2. **A source name** in `raw_store.py`, so `aicfo-spend save` can file the download and check it at save time.
3. **One call in `report.py`**, where extra usage is read, and **one entry in `daily.py`**, so the new usage counts toward the pace and the alerts.
4. **A test** with made-up data that shows the vendor's spend in a month's report.

Downloads follow the same rules as the others. They cover the month to date from the 1st, are saved once and never edited, and the one reaching furthest into the month counts.

## Worked example: OpenAI

OpenAI needed cases 1 and 2, and no code at all:

- **API spend.** OpenAI's Admin key was plugged into Ramp, so OpenAI keys arrive in the same `ramp-api-keys` download as Anthropic's.
- **ChatGPT seats.** Each seat holder became a `people.csv` row with vendor `openai`, and the seat price went under `seats.openai` in settings.

`vendors/openai/` holds only a note saying so. `tests/test_openai.py` proves that OpenAI's API spend and ChatGPT seats show up beside Anthropic's, by team and by vendor, in the report, the forecast and the pace.
