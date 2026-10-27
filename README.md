# aicfo-spend-analysis

Open-source AI spend analysis, built on Ramp. Each month it answers two questions about a company's AI spend: **where did it go**, and **where is it heading?**

- Every dollar of Anthropic and OpenAI spend goes to a person and a team. Anything it can't place shows as Unassigned, with the reason.
- It projects this month and next, and flags days that spike.
- Ramp holds the Admin keys. This tool reads the AI spend Ramp already collects and never touches a key.
- It's plain Python with no add-on packages, and it runs without Claude. In Claude, the `ai-spend-analysis` skill in [aircfo/claude-startup-finance](https://github.com/aircfo/claude-startup-finance) fetches the data and shows the results.

## Status

The design is approved: [docs/design.md](docs/design.md). v1 is being built. Until it's done, the v0.3.0 scripts it replaces live in [`legacy/`](legacy/), moved here from `claude-startup-finance` with their history.

## Try it

```
uv tool install git+https://github.com/aircfo/aicfo-spend-analysis
aicfo-spend init ~/acme-ai-spend --company "Acme"
aicfo-spend settings ~/acme-ai-spend
```

`init` sets up a company's folder, which must sit outside any git repo because it will hold real spend data. Add your seat prices and seats bought to its `aicfo-spend.json`, then `settings` checks the file and summarizes it.

Then save the month's inputs and report on it:

```
aicfo-spend save ~/acme-ai-spend ramp-people --start 2026-10-01 --file users.json
aicfo-spend save ~/acme-ai-spend ramp-api-keys --start 2026-09-01 --end 2026-09-30 --file keys.json
aicfo-spend save ~/acme-ai-spend claude-spend-export --start 2026-09-01 --end 2026-09-30 --file spend.csv
aicfo-spend report ~/acme-ai-spend 2026-09
aicfo-spend forecast ~/acme-ai-spend
aicfo-spend pace ~/acme-ai-spend
aicfo-spend dashboard ~/acme-ai-spend
```

`report` shows the month by team and vendor, and how it moved from last month. `forecast` projects next month; add `--as-of 2026-08-31 --score 2026-09` to replay a past forecast and see how far off it was. `pace` shows where this month stands and drafts the two alerts: a team's day spiking, and the month on pace to run well over its plan. `dashboard` writes all of it to `out/<month>/dashboard.json` for a shared page, rolled up to teams, with teams under 3 people folded together, unless the settings turn on the person view.

Spend downloads cover the month to date, from the 1st; the people list is one day's snapshot. Ramp's answers are saved as Ramp returns them, with every page joined into one list. In Claude, the skill does these steps for you.

No install? Run it from a checkout: `PYTHONPATH=src python3 -m aicfo_spend --help`.

## Development

```
uv run pytest && uv run mypy
```

See [CLAUDE.md](CLAUDE.md) for the rules every change follows, and [docs/adding-a-vendor.md](docs/adding-a-vendor.md) to add a vendor (usually no code).

## License

MIT. See [LICENSE](LICENSE).
