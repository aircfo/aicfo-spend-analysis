# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [semantic versioning](https://semver.org/). Each release's wheel is attached to its GitHub Release. The `ai-spend-analysis` skill ships in the same release; a copy of its folder on its own pins that wheel by SHA-256.

## [Unreleased]

### Added

- The `ai-spend-analysis` Claude skill now lives in this repo, in `skills/ai-spend-analysis/`, and the repo installs as one plugin: `/plugin install ai-spend-analysis --marketplace aircfo/aicfo-spend-analysis`. The engine and the skill are released together under one version. It was copied from `claude-startup-finance`'s unmerged `feat/ai-spend-thin-wrapper` branch, where its earlier history is.
- The skill's behavior tests (`evals/`) and its scripts' tests (`tests/skill/`), which now run in this repo's checks, type check included.

### Changed

- The skill's launcher is `scripts/run_engine.py`, renamed from `aicfo_spend.py` so it can't be mistaken for the engine. It runs the engine that shipped beside it. Only a copy of the skill folder on its own downloads the pinned release, as before.

### Fixed

- The Mac export script hands its export to the launcher under its new name; a test now runs that hand-off through the real engine.

## [0.2.0] — 2026-10-27

### Added

- `ramp-provider-totals`: Ramp's own total for each vendor, saved beside the per-key spend for the same days. API spend Ramp counts but can't tie to a current key, usually because a key was deleted or rotated during the month, now shows as Unassigned with the reason "API spend not tied to a current key", so the totals match Ramp's. `pace` adds it to the month so far as it stands; it isn't projected or checked for spikes, because a deleted key's spend moves rather than recurs. On airCFO's real data that was $192 of OpenAI's $302 in October.
- Notes when a vendor's keys add up to more than Ramp's total by over $1 (a copying mistake in the saved per-key answer), when Ramp's total has a vendor none of whose keys spent, and when a forecast averages months saved without Ramp's totals.

### Changed

- The person view of `dashboard` lists only the keys themselves; spend no key carries stays in Unassigned by reason.

## [0.1.0] — 2026-10-27

The first release: where a company's AI spend went and where it's heading, built on Ramp.

### Added

- `init` and `settings`: a company's folder, with seat prices, seats bought, who sees what and the alert thresholds in `aicfo-spend.json`.
- `save`: each download from Ramp (API key spend month to date, the people list) and claude.ai (the spend export) is checked, filed under its month, and made read-only.
- `report`: every dollar of seats, extra usage and API spend goes to a person and a team, or to Unassigned with one of three reasons; by team, vendor and model, with the change from last month.
- `forecast`: next month from seats and each team's average usage over the last 3 complete months; `--as-of --score` replays a past forecast against what happened.
- `pace`: where this month is heading, a plan saved once per month, and two drafted alerts, a spike and over pace.
- `dashboard`: the shared numbers in `out/<month>/dashboard.json`, rolled up to teams, with teams under 3 people folded together, unless the person view is on.
- OpenAI's API spend and ChatGPT seats, with no code of their own; `docs/adding-a-vendor.md` shows how.
