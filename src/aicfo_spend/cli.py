"""The aicfo-spend command."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from aicfo_spend import __version__
from aicfo_spend.settings import SETTINGS_FILE_NAME, Settings, SettingsError, load_settings
from aicfo_spend.workspace import WorkspaceError, create_workspace


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    commands: dict[str, Callable[[argparse.Namespace], int]] = {
        "init": _run_init,
        "settings": _run_settings,
    }
    try:
        return commands[args.command](args)
    except (SettingsError, WorkspaceError) as error:
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
