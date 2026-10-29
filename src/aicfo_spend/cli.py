"""The aicfo-spend command."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

from aicfo_spend import __version__
from aicfo_spend.bills import BILLS_FILE_NAME, check_bills, read_bills, summarize_bills
from aicfo_spend.dashboard import build_dashboard, write_dashboard
from aicfo_spend.errors import InputError, UserError
from aicfo_spend.forecast import describe_forecast, describe_score, forecast_after
from aicfo_spend.months import Month
from aicfo_spend.other_vendors import other_ai_spend, summarize_other_ai_spend
from aicfo_spend.pace import describe_forecast_check, describe_pace, pace_of
from aicfo_spend.raw_store import SOURCES, save_raw
from aicfo_spend import recommendations
from aicfo_spend.report import build_month, describe_change, summarize
from aicfo_spend.settings import SETTINGS_FILE_NAME, Settings, load_settings
from aicfo_spend.spend_table import write_spend_csv
from aicfo_spend.usage import USAGE_FILE_NAME, build_usage, summarize_usage, write_usage_csv
from aicfo_spend.workspace import create_workspace


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    commands: dict[str, Callable[[argparse.Namespace], int]] = {
        "init": _run_init,
        "settings": _run_settings,
        "save": _run_save,
        "report": _run_report,
        "forecast": _run_forecast,
        "pace": _run_pace,
        "dashboard": _run_dashboard,
        "recommend": _run_recommend,
        "decide": _run_decide,
    }
    try:
        return commands[args.command](args)
    except UserError as error:
        print(f"aicfo-spend: {error}", file=sys.stderr)
        return 1


def describe_settings(settings: Settings) -> str:
    lines = [f"Company: {settings.company_name or '(not set)'}"]
    if settings.visibility == "team":
        lines.append("Visibility: team (shared outputs show teams, not people)")
    else:
        lines.append("Visibility: person (shared outputs can show each person's spend)")
    if settings.seats:
        lines.append("Seats:")
        for vendor, plans in settings.seats.items():
            for seat_type, plan in plans.items():
                lines.append(f"  {vendor} {seat_type}: {plan.bought} bought at ${plan.price_usd:,.2f} a month")
    else:
        lines.append(f"Seats: none yet; add each vendor's seat prices and seats bought to {SETTINGS_FILE_NAME}")
    alerts = settings.alerts
    lines.append(
        f"Alerts: a spike is a team's day over {alerts.spike_multiple}× its 28-day median and "
        f"${alerts.spike_min_usd:,.2f} above it; over pace is {alerts.over_pace_percent}% above the forecast"
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aicfo-spend",
        description="Where a company's AI money went and where it's heading.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    init = commands.add_parser("init", help="set up a company's workspace folder")
    init.add_argument("folder", help="where to create the workspace (outside any git repo)")
    init.add_argument("--company", help="the company's name (defaults to the folder's name)")

    settings = commands.add_parser("settings", help="check a workspace's settings file and summarize it")
    settings.add_argument("folder", help="the workspace folder")

    save = commands.add_parser("save", help="save a download into the workspace, once and read-only")
    save.add_argument("folder", help="the workspace folder")
    save.add_argument("source", choices=SOURCES, help="what the download is")
    save.add_argument("--start", required=True, help="first day it covers, like 2026-09-01")
    save.add_argument("--end", help="last day it covers (defaults to --start)")
    save.add_argument("--file", help="the downloaded file (reads standard input if left out)")
    save.add_argument(
        "--remove-original",
        action="store_true",
        help="delete --file once it's saved, such as a copy left in Downloads",
    )

    report = commands.add_parser("report", help="build a month's spend table and summarize it by team")
    report.add_argument("folder", help="the workspace folder")
    report.add_argument("month", help="the month, like 2026-09")
    # Not a report option: accepted only to say where comparing with the export lives.
    report.add_argument("--compare", help=argparse.SUPPRESS)

    forecast = commands.add_parser("forecast", help="forecast next month's spend, and optionally score it")
    forecast.add_argument("folder", help="the workspace folder")
    forecast.add_argument("--as-of", help="use only data through this day (defaults to today)")
    forecast.add_argument("--score", help="score the forecast against this month's actual spend, like 2026-09")

    pace = commands.add_parser("pace", help="where a month stands: so far, on pace for, and alerts")
    pace.add_argument("folder", help="the workspace folder")
    pace.add_argument("month", nargs="?", help="the month, like 2026-10 (defaults to this month)")

    dashboard = commands.add_parser("dashboard", help="write a month's numbers for a shared dashboard, rolled up to teams")
    dashboard.add_argument("folder", help="the workspace folder")
    dashboard.add_argument("month", nargs="?", help="the month, like 2026-10 (defaults to this month)")

    recommend = commands.add_parser("recommend", help="suggest seat changes for the operator and track what done ones saved")
    recommend.add_argument("folder", help="the workspace folder")
    recommend.add_argument("month", nargs="?", help="the last whole month to read, like 2026-09 (defaults to last month)")

    decide = commands.add_parser("decide", help="mark a seat recommendation done or declined")
    decide.add_argument("folder", help="the workspace folder")
    decide.add_argument("id", help="the recommendation, like R3")
    decide.add_argument("status", choices=(recommendations.DONE, recommendations.DECLINED))
    decide.add_argument("--on", help="the day it was done or declined (defaults to today)")
    decide.add_argument("--note", default="", help="what was done, in a few words")
    return parser


def _run_init(args: argparse.Namespace) -> int:
    folder = Path(args.folder)
    company: str = args.company or folder.expanduser().resolve().name
    created = create_workspace(folder, company)
    print(f"Set up a workspace for {company}:")
    for path in created:
        print(f"  created {path}")
    print(
        f"Next: add your seat prices and seats bought to {SETTINGS_FILE_NAME}, "
        f"then check it with `aicfo-spend settings {folder}`."
    )
    return 0


def _run_settings(args: argparse.Namespace) -> int:
    print(describe_settings(load_settings(Path(args.folder).expanduser())))
    return 0


def _run_save(args: argparse.Namespace) -> int:
    workspace = Path(args.folder).expanduser()
    if not (workspace / SETTINGS_FILE_NAME).exists():
        raise InputError(f"{workspace} isn't a workspace; set one up with `aicfo-spend init`")
    start = _day(args.start)
    end = _day(args.end) if args.end else start
    download: Path | None = None
    if args.file:
        download = Path(args.file).expanduser()
        if not download.is_file():
            raise InputError(f"no file at {download}")
        _check_dates_in_name(download, start, end)
        content = download.read_bytes()
    elif args.remove_original:
        raise InputError("--remove-original needs --file, the download to delete once it's saved")
    else:
        content = sys.stdin.buffer.read()
    path = save_raw(workspace, args.source, start, end, content)
    print(f"Saved {path.relative_to(workspace)}")
    if download is not None and args.remove_original:
        download.unlink()
        print(f"Removed {download}")
    return 0


# claude.ai names its spend export after the days it covers, like
# spend-report-acme-2026-10-01-to-2026-10-28.csv; a file named for other days is the wrong one.
DATES_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})-to-(\d{4}-\d{2}-\d{2})")


def _check_dates_in_name(download: Path, start: date, end: date) -> None:
    named = DATES_IN_NAME.search(download.name)
    if named is None:
        return
    first, last = _day(named.group(1)), _day(named.group(2))
    if (first, last) != (start, end):
        raise InputError(
            f"{download.name} is named for {first} to {last}, but --start and --end say {start} to {end}. "
            "Save it with the days in its name, or check it's the file you meant."
        )


def _run_report(args: argparse.Namespace) -> int:
    if args.compare:
        raise InputError(
            "report has no --compare. Comparing the roster with a claude.ai spend export is the skill's "
            "roster_to_people.py: python3 <skill>/scripts/roster_to_people.py <folder> <Seats tab rows> --compare <export>"
        )
    workspace = Path(args.folder).expanduser()
    settings = load_settings(workspace)
    month = Month.parse(args.month)
    spend = build_month(workspace, month, settings)
    output = workspace / "out" / month.label / "spend.csv"
    write_spend_csv(spend.rows, output)
    print(summarize(spend, settings.company_name))
    # The month's own report has already succeeded, so trouble with last month's files only skips the comparison.
    try:
        previous = build_month(workspace, month.previous_month(), settings)
        print("\n" + describe_change(spend, previous, show_people=settings.visibility == "person"))
    except UserError as error:
        print(f"\nChange from last month isn't available: {error}")
    if month.last_day < date.today():
        # Like the change from last month, trouble with last month's files only skips the check.
        try:
            check = describe_forecast_check(workspace, settings, spend)
        except UserError as error:
            check = f"How the forecast did isn't available: {error}"
        if check:
            print("\n" + check)
    if (workspace / BILLS_FILE_NAME).exists():
        print("\n" + summarize_bills(month, check_bills(spend, read_bills(workspace))))
    other = other_ai_spend(workspace, month, settings)
    if other is not None:
        print("\n" + summarize_other_ai_spend(month, other))
    usage = build_usage(workspace, month)
    print("\n" + summarize_usage(usage))
    usage_output = output.with_name(USAGE_FILE_NAME)
    if usage.covers is not None:
        write_usage_csv(usage, usage_output)
    print(f"\nWrote {output.relative_to(workspace)} ({len(spend.rows)} rows)"
          + (f" and {usage_output.relative_to(workspace)} ({len(usage.rows)} rows)" if usage.covers else ""))
    return 0


def _run_forecast(args: argparse.Namespace) -> int:
    workspace = Path(args.folder).expanduser()
    settings = load_settings(workspace)
    as_of = _day(args.as_of) if args.as_of else date.today()
    forecast = forecast_after(workspace, settings, as_of)
    print(describe_forecast(forecast))
    if args.score:
        month = Month.parse(args.score)
        if month != forecast.month:
            raise InputError(
                f"a forecast made as of {as_of} is for {forecast.month.label}; score it against that month"
            )
        print("\n" + describe_score(forecast, build_month(workspace, month, settings)))
    return 0


def _run_pace(args: argparse.Namespace) -> int:
    workspace = Path(args.folder).expanduser()
    settings = load_settings(workspace)
    month = Month.parse(args.month) if args.month else Month.of(date.today())
    pace = pace_of(workspace, settings, month)
    print(describe_pace(pace))
    # Rewritten every time, so the file never shows an alert a later check no longer raises.
    drafts = workspace / "out" / month.label / "alerts.txt"
    drafts.parent.mkdir(parents=True, exist_ok=True)
    drafts.write_text("\n".join(pace.alerts or ["No alerts."]) + "\n", encoding="utf-8")
    print(f"\nSaved the alert drafts to {drafts.relative_to(workspace)}")
    return 0


def _run_dashboard(args: argparse.Namespace) -> int:
    workspace = Path(args.folder).expanduser()
    settings = load_settings(workspace)
    month = Month.parse(args.month) if args.month else Month.of(date.today())
    path = write_dashboard(workspace, month, build_dashboard(workspace, settings, month))
    view = "people and teams" if settings.visibility == "person" else "teams only, small ones folded together"
    print(f"Wrote {path.relative_to(workspace)} ({view})")
    return 0


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise InputError(f"{text!r} isn't a date; write it like 2026-09-14") from None


def _run_recommend(args: argparse.Namespace) -> int:
    workspace = Path(args.folder).expanduser()
    settings = load_settings(workspace)
    today = date.today()
    last = Month.parse(args.month) if args.month else Month.of(today).previous_month()
    found, notes = recommendations.find_recommendations(workspace, settings, last, today)
    ledger, added = recommendations.add_new(recommendations.read_ledger(workspace), found)
    if added:
        recommendations.write_ledger(workspace, ledger)
    results = recommendations.outcomes(workspace, ledger, today)
    print(recommendations.describe(added, ledger, results, notes))
    return 0


def _run_decide(args: argparse.Namespace) -> int:
    workspace = Path(args.folder).expanduser()
    on = _day(args.on) if args.on else date.today()
    ledger = recommendations.decide(recommendations.read_ledger(workspace), args.id, args.status, on, args.note)
    recommendations.write_ledger(workspace, ledger)
    print(f"Marked {args.id.upper()} {args.status} on {on}")
    return 0
