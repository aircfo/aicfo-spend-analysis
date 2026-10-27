"""The aicfo-spend command."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

from aicfo_spend import __version__
from aicfo_spend.errors import InputError, UserError
from aicfo_spend.forecast import describe_forecast, describe_score, forecast_after
from aicfo_spend.months import Month
from aicfo_spend.raw_store import SOURCES, save_raw
from aicfo_spend.report import build_month, describe_change, summarize
from aicfo_spend.settings import SETTINGS_FILE_NAME, Settings, load_settings
from aicfo_spend.spend_table import write_spend_csv
from aicfo_spend.workspace import create_workspace


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    commands: dict[str, Callable[[argparse.Namespace], int]] = {
        "init": _run_init,
        "settings": _run_settings,
        "save": _run_save,
        "report": _run_report,
        "forecast": _run_forecast,
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

    report = commands.add_parser("report", help="build a month's spend table and summarize it by team")
    report.add_argument("folder", help="the workspace folder")
    report.add_argument("month", help="the month, like 2026-09")

    forecast = commands.add_parser("forecast", help="forecast next month's spend, and optionally score it")
    forecast.add_argument("folder", help="the workspace folder")
    forecast.add_argument("--as-of", help="use only data through this day (defaults to today)")
    forecast.add_argument("--score", help="score the forecast against this month's actual spend, like 2026-09")
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
    if args.file:
        download = Path(args.file).expanduser()
        if not download.is_file():
            raise InputError(f"no file at {download}")
        content = download.read_bytes()
    else:
        content = sys.stdin.buffer.read()
    path = save_raw(workspace, args.source, start, end, content)
    print(f"Saved {path.relative_to(workspace)}")
    return 0


def _run_report(args: argparse.Namespace) -> int:
    workspace = Path(args.folder).expanduser()
    settings = load_settings(workspace)
    month = Month.parse(args.month)
    spend = build_month(workspace, month, settings)
    output = workspace / "out" / month.label / "spend.csv"
    write_spend_csv(spend.rows, output)
    print(summarize(spend, settings.company_name))
    previous = build_month(workspace, month.previous_month(), settings)
    print("\n" + describe_change(spend, previous, show_people=settings.visibility == "person"))
    print(f"\nWrote {output.relative_to(workspace)} ({len(spend.rows)} rows)")
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


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise InputError(f"{text!r} isn't a date; write it like 2026-09-14") from None
