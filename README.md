# aicfo-spend-analysis

Open-source AI spend analysis, built on Ramp. Each month it answers two questions about a company's AI spend: **where did it go**, and **where is it heading?**

- Every dollar of Anthropic and OpenAI spend goes to a person and a team. Anything it can't place shows as Unassigned, with the reason.
- It projects this month and next, and flags days that spike.
- Ramp holds the Admin keys. This tool reads the AI spend Ramp already collects and never touches a key.
- It's plain Python with no add-on packages, and it runs without Claude. In Claude, the `ai-spend-analysis` skill in [aircfo/claude-startup-finance](https://github.com/aircfo/claude-startup-finance) fetches the data and shows the results.

## Status

The design is approved: [docs/design.md](docs/design.md). v1 is being built. Until it's done, the v0.3.0 scripts it replaces live in [`legacy/`](legacy/), moved here from `claude-startup-finance` with their history.

## License

MIT. See [LICENSE](LICENSE).
