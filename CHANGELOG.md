# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [semantic versioning](https://semver.org/). Each release's wheel is attached to its GitHub Release. The `ai-spend-analysis` skill ships in the same release; a copy of its folder on its own pins that wheel by SHA-256.

## [0.3.2] — 2026-10-29

### Added

- A scenario on the Forecast tab: new hires by team, the share on Premium, extra usage and API up or down, new API spend and seat prices, shown against next month's forecast in total and by team. New hires fill empty seats first. It runs on the viewer's screen and saves nothing. The dashboard data adds each team's Claude seat holders to the forecast and each seat type's price and seats held.
- Other AI vendors. At setup Claude reads six months of Ramp's spend by vendor, proposes the AI vendors among them for the operator to confirm, says which Ramp can connect, and lists them in settings as `other_ai_vendors`. The daily refresh saves the month's spend by vendor (`ramp-vendor-spend`), and `report` and the Spend tab's "Other AI tools" panel show it, apart from the teams, totals and forecast.
- Seat recommendations with a ledger. `recommend` reads the last three whole months of the claude.ai export and suggests upgrades, downgrades, reclaims, check-ins and prepaid credit bundles, each rule holding in two months; it keeps them in `recommendations.csv`, for the operator only. `decide` marks one done or declined, and `recommend` then reports what done ones saved, counting a reclaim or downgrade only once settings show fewer seats bought.
- A check against the bills. `bills.csv` lists what the vendors charged each month; `report` and the Spend tab's "Against the bills" panel say which seat and invoice lines tie, within $1 or 0.5%, and by how much the rest are off. A year's seats paid up front count a twelfth each month. Prepaid credits, the way Claude Team sells extra usage, show what was paid, the credit it bought and what was used, since they tie over time rather than each month. A charge id entered twice and a vendor the tool doesn't read are refused, and the page shows last month's check beside this month's. Refresh step 3 has Claude fill the file from Ramp's charges and receipts when a month closes.
- API spend by cost category (R&D, go-to-market, cost of revenue and the like), from a `Cost category` tag on the key in Ramp or `api_cost_categories` in settings. `report` lists it, `spend.csv` has a `cost_category` column, and the Spend tab has an "API by what it's for" panel. Setup step 7 has Claude propose a category for each key for the operator to confirm.
- `known_changes` in settings: what history can't show (a new project's API key, a retired tool) by month, team and product, added to the forecast and named in its notes.
- `report` on a month that has ended says how its forecast did against doing nothing, calling it last month's total; a forecast that loses is the sign to change the method.
- Usage beside spend. The claude.ai spend export's usage columns (requests, and tokens by kind, for every person, product and model, within seats too) are read into `out/<month>/usage.csv`, summarized by `report`, and shown on a new Usage tab: how many seat holders used Claude, how concentrated use is, and use by team, product and model beside extra usage. Use is counted in fresh tokens, leaving out cache reads, which are most of every prompt. The team view folds products and models fewer than 3 people used, and each Other holds at least 3 people; the person view lists everyone.
- `scripts/seat_rows.py` builds the Seats tab's rows in one pass, from Ramp's people list, the default seats and the member lists Claude reads in Chrome, as ready-to-write batches of 50. With `--existing` it adds only people new to Ramp.
- `save --remove-original` deletes the downloaded file once it's saved, so a claude.ai export doesn't sit in Downloads.

### Changed

- Next month's API forecast is last complete month's spend on current keys, not the 3-month average. On airCFO's twelve months of Ramp history it missed by 27% on average against 43%: API spend moves in steps an average lags. Spend no current key carries is left out, since a deleted key doesn't spend again.
- `roster_to_people.py` reads the Seats tab as ArtifactData saves it, one file per person, so no converter is needed.
- `save` refuses a claude.ai export whose file name says it covers other days than `--start` and `--end`.
- `init` on a folder already in use says what it holds, its saved downloads and published dashboard included, and that nothing was changed.
- `report` gives API spend not tied to a current key by vendor, with the usual cause, a key deleted or rotated this month.
- The dashboard's logo is published beside the page instead of inside it, a quarter less for Claude to read before each daily publish.
- Setup step 3 has Claude in Chrome read the seat counts from claude.ai and ChatGPT's prices, and still asks for Claude's prices, which claude.ai never shows. Setup step 4.3 gives the whole `--compare` command.

### Fixed

- The skill and the page name claude.ai's real pages: the export at claude.ai/analytics, with its MTD and Last month presets, and the members at claude.ai/admin-settings/members. It finds the export in Downloads by the days in its name, instead of asking the operator to change Chrome's download folder.
- `report --compare` says that comparing with an export is the skill's `roster_to_people.py --compare`, instead of argparse's generic error.
- The plugin installs and updates over https. The marketplace file named the repo as a `github` source, which Claude Code clones over SSH, so installing failed on any computer without a GitHub SSH key ("Permission denied (publickey)"). It takes effect as soon as it's on `main`, since installs read the marketplace file from there.

## [0.3.1] — 2026-10-28

The dashboard refreshed from the owner's review, and the fixes from the first end-to-end run on real data.

### Added

- The Overview flags data that has fallen behind: a source more than 2 days behind yesterday, or a claude.ai export missing for the month, with how to bring it up to date.

### Changed

- The dashboard's Overview leads with the alerts, and shows the month by product as a donut with its total in the middle.
- The Spend tab charts spend by vendor and the change from last month, each with its table a click away.
- The Unassigned tab says what each reason means for a finance lead, where it gets fixed, and that fixing it moves the dollars onto a team at the next refresh.
- The Forecast tab is one table: this month on pace for, beside next month's forecast, by team.
- The skill checks for Claude in Chrome before setup and every refresh. If it's missing, it says so first, gives the fix for Claude Code (`claude --chrome`) or the desktop app, and offers the export by hand, instead of carrying on with an older export. It recommends the Mac script for refreshes nobody watches.

### Fixed

- No plan is saved before any usage is. Setup publishes the page before the first refresh, and the dashboard then froze a plan of seats alone, so the month read as far over pace until it ended. Found in the first end-to-end run of the plugin on real data.

## [0.3.0] — 2026-10-28

The first release that installs as a Claude plugin, the engine and the skill together.

### Added

- The shared dashboard is split into tabs (Overview, Spend, Unassigned, Forecast, People in the person view, and Seats) and wears airCFO's brand: its navy masthead and logo, Bitter and Inter, and chart colors drawn from its palette and checked for color-blind readers in both themes. A tab can be linked to by its name, such as `#seats`.
- The Seats tab is the seats roster, replacing the separate Claude Doc: one row per person with a dropdown per vendor, kept in the page's own database so it survives every daily republish. Anyone who can open the page sees it (names, emails, teams and seat types, never spend); anyone given edit access can change a seat, and the next refresh writes `people.csv` from it. Setup moves an existing roster doc into it once.
- `dashboard.json` lists each vendor's seat types and how many were bought, which the Seats tab offers and counts against.

- The `ai-spend-analysis` Claude skill now lives in this repo, in `skills/ai-spend-analysis/`, and the repo installs as one plugin: `/plugin install ai-spend-analysis --marketplace aircfo/aicfo-spend-analysis`. The engine and the skill are released together under one version. It was copied from `claude-startup-finance`'s unmerged `feat/ai-spend-thin-wrapper` branch, where its earlier history is.
- The skill's scripts' tests (`tests/skill/`), which now run in this repo's checks, type check included, and its behavior tests (`evals/`), run by hand with `claude plugin eval`.
- `docs/releasing.md`: the release steps, including pinning the uploaded wheel's fingerprint.

### Changed

- The skill's launcher is `scripts/run_engine.py`, renamed from `aicfo_spend.py` so it can't be mistaken for the engine. It runs the engine that shipped beside it, trusting that only with the plugin's manifest beside it too. Only a copy of the skill folder on its own downloads the pinned release, as before. A test runs the Mac export script's hand-off through the real engine.
- The plugin installs from the release tag of its version, never from `main`.
- `pyproject.toml` pins hatchling exactly, so a rebuilt wheel has the same fingerprint as the released one.

### Fixed

- The launcher starts the engine in Python's isolated mode, so an `aicfo_spend` folder in the working directory can no longer run in its place, the pinned fingerprint notwithstanding.
- A failed engine download says what went wrong in plain words instead of printing a traceback.

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
