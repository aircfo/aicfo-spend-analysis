# Adding a vendor

Most vendors need no code. Find the cases that fit the vendor; only the last one would involve writing any.

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

## 3. It has its own per-person usage export: not built yet

Today the tool reads per-person extra usage from one export, claude.ai's. A vendor whose per-person usage isn't in Ramp would need two things:

- **A reader** in `src/aicfo_spend/vendors/<vendor>/`, modelled on `vendors/anthropic/spend_export.py`.
- **Changes to the core**, so the report, the daily usage and the forecast read more than one usage export.

No vendor needs this yet, so it isn't built.

## Worked example: OpenAI

OpenAI needed cases 1 and 2, and no code at all:

- **API spend.** OpenAI's Admin key was plugged into Ramp, so OpenAI keys arrive in the same `ramp-api-keys` download as Anthropic's.
- **ChatGPT seats.** Each seat holder became a `people.csv` row with vendor `openai`, and the seat price went under `seats.openai` in settings.

`vendors/openai/` holds only a note saying so. `tests/test_openai.py` proves that OpenAI's API spend and ChatGPT seats show up beside Anthropic's, by team and by vendor, in the report, the forecast and the pace.
