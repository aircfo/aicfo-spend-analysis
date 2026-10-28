# aicfo-spend-analysis

Open-source AI spend analysis, built on Ramp. Each month it answers two questions about a company's AI spend: **where did it go**, and **where is it heading?**

- Every dollar of Anthropic and OpenAI spend goes to a person and a team. Anything it can't place shows as Unassigned, with the reason.
- It projects this month and next, and flags days that spike.
- Ramp holds the Admin keys. This tool reads the AI spend Ramp already collects and never touches a key.
- It's plain Python with no add-on packages, and it runs without Claude. In Claude, the `ai-spend-analysis` skill that ships in this repo fetches the data and shows the results.

## Status

v0.2.0 is the latest release; [CHANGELOG.md](CHANGELOG.md) says what's in it, and the design is [docs/design.md](docs/design.md). Until a real month has been checked against them, the v0.3.0 scripts it replaces live in [`legacy/`](legacy/), moved here from `claude-startup-finance` with their history.

## In Claude

This repo installs as one Claude plugin, the engine and the skill together:

```
/plugin install ai-spend-analysis --marketplace aircfo/aicfo-spend-analysis
```

That needs Claude Code 2.1.275 or later, and installs the tagged release the marketplace file names, never unreleased work. The first release with the plugin in it is the next one after v0.2.0; until then, load a checkout with `claude --plugin-dir <checkout>`. Then ask "set up AI spend tracking". Setup is a conversation: Claude connects Ramp, sets up a folder for your company's files, builds the seats roster and publishes a dashboard that stays private until you share it. After that, a daily scheduled task keeps the numbers current. The skill is [skills/ai-spend-analysis/SKILL.md](skills/ai-spend-analysis/SKILL.md), and [its security overview](skills/ai-spend-analysis/references/security-overview.md) is one page for whoever approves it.

## Without Claude

```
uv tool install git+https://github.com/aircfo/aicfo-spend-analysis@v0.2.0
aicfo-spend init ~/acme-ai-spend --company "Acme"
aicfo-spend settings ~/acme-ai-spend
```

`init` sets up a company's folder, which must sit outside any git repo because it will hold real spend data. Add your seat prices and seats bought to its `aicfo-spend.json`, then `settings` checks the file and summarizes it.

Then save the month's inputs and report on it:

```
aicfo-spend save ~/acme-ai-spend ramp-people --start 2026-10-01 --file users.json
aicfo-spend save ~/acme-ai-spend ramp-api-keys --start 2026-09-01 --end 2026-09-30 --file keys.json
aicfo-spend save ~/acme-ai-spend ramp-provider-totals --start 2026-09-01 --end 2026-09-30 --file totals.json
aicfo-spend save ~/acme-ai-spend claude-spend-export --start 2026-09-01 --end 2026-09-30 --file spend.csv
aicfo-spend report ~/acme-ai-spend 2026-09
aicfo-spend forecast ~/acme-ai-spend
aicfo-spend pace ~/acme-ai-spend
aicfo-spend dashboard ~/acme-ai-spend
```

`report` shows the month by team and vendor, and how it moved from last month. `forecast` projects next month; add `--as-of 2026-08-31 --score 2026-09` to replay a past forecast and see how far off it was. `pace` shows where this month stands and drafts the two alerts: a team's day spiking, and the month on pace to run well over its plan. `dashboard` writes all of it to `out/<month>/dashboard.json` for a shared page, rolled up to teams, with teams under 3 people folded together, unless the settings turn on the person view.

Spend downloads cover the month to date, from the 1st; the people list is one day's snapshot. `ramp-provider-totals` is Ramp's AI spend summary (cost grouped by provider) for the same days as `ramp-api-keys`, so spend no current key carries is counted too. Ramp's answers are saved as Ramp returns them, with every page joined into one list. In Claude, the skill does these steps for you.

No install? Run it from a checkout: `PYTHONPATH=src python3 -m aicfo_spend --help`.

## Development

```
uv run pytest && uv run mypy
```

See [CLAUDE.md](CLAUDE.md) for the rules every change follows, and [docs/adding-a-vendor.md](docs/adding-a-vendor.md) to add a vendor (usually no code).

## License

MIT. See [LICENSE](LICENSE).
