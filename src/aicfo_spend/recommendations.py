"""Seat recommendations for the operator, kept in a ledger so what they save can be checked.

Each rule reads the last three whole months of the claude.ai spend export and must hold in at
least two of them, so one odd month doesn't move a seat:

- Upgrade Standard to Premium: extra usage above the price gap. Premium includes five times
  Standard's usage, which Anthropic doesn't state in tokens, so the saving is at most this much.
- Downgrade Premium to Standard: no extra usage at all, and no more fresh tokens than some
  Standard holder used that month without paying extra.
- Reclaim: no requests in either of the last two months, nor in the month since, as far as it's saved.
- Check in: under 50 requests a month. It's a conversation, so it has no dollar figure.
- Prepaid credits: Claude Team sells extra usage in bundles for up to 30% less. The bundles
  are sized to the smallest month's extra usage, so the credits bought get used.

The ledger names people, so it stays in the company's folder and never goes on the shared
page. A recommendation is open until the operator marks it done or declined. For a done one
the ledger says what it saved: the seat's price each month the roster still shows the change,
or for an upgrade, how far extra usage fell, less the price gap.
"""

from __future__ import annotations

import csv
import os
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from aicfo_spend.errors import InputError
from aicfo_spend.months import Month
from aicfo_spend.report import ZERO, dollars
from aicfo_spend.settings import Settings
from aicfo_spend.sources.people import read_people_file
from aicfo_spend.usage import MonthUsage, build_usage

LEDGER_FILE_NAME = "recommendations.csv"
LEDGER_COLUMNS = (
    "id", "made_on", "basis", "vendor", "person", "action", "from_seat", "to_seat",
    "monthly_usd", "baseline_usd", "why", "status", "decided_on", "note", "seats_bought_then",
)
VENDOR = "anthropic"
STANDARD = "standard"
PREMIUM = "premium"

UPGRADE = "upgrade"
DOWNGRADE = "downgrade"
RECLAIM = "reclaim"
CHECK_IN = "check in"
BUNDLES = "buy credit bundles"
ACTIONS = (UPGRADE, DOWNGRADE, RECLAIM, CHECK_IN, BUNDLES)

OPEN = "open"
DONE = "done"
DECLINED = "declined"
STATUSES = (OPEN, DONE, DECLINED)

MONTHS_READ = 3
MONTHS_NEEDED = 2
CHECK_IN_REQUESTS = 50
# A declined recommendation comes back only after this long, in case things have changed.
ASK_AGAIN_AFTER = timedelta(days=90)
# Claude Team's prepaid bundles, as (price, credit), biggest first; at most $3,000 of credit a month.
BUNDLE_SIZES = ((Decimal(700), Decimal(1000)), (Decimal(200), Decimal(250)), (Decimal(45), Decimal(50)))
BUNDLE_CREDIT_CAP = Decimal(3000)
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Recommendation:
    id: str
    made_on: date
    basis: str
    vendor: str
    # Empty for a company-wide recommendation such as credit bundles.
    person: str
    action: str
    from_seat: str
    to_seat: str
    # The expected saving a month; None for a check-in.
    monthly_usd: Decimal | None
    # For an upgrade: the average extra usage a month before it, which later months are measured against.
    baseline_usd: Decimal | None
    why: str
    status: str = OPEN
    decided_on: date | None = None
    note: str = ""
    # For a reclaim or downgrade: the seats of its from_seat type bought when it was made. It
    # saves money only once settings show fewer bought, since an emptied seat is still paid for.
    seats_bought_then: int | None = None


@dataclass(frozen=True)
class PersonMonth:
    requests: int = 0
    fresh_tokens: int = 0
    extra_usd: Decimal = ZERO


@dataclass(frozen=True)
class Outcome:
    recommendation: Recommendation
    saved_usd: Decimal
    months: int
    # Set when the change didn't hold, such as a reclaimed seat given back.
    problem: str = ""


def whole_months(workspace: Path, last: Month) -> list[tuple[Month, dict[str, PersonMonth]]]:
    """Each person's use in the months up to `last` whose export covers the whole month, oldest first."""
    found: list[tuple[Month, dict[str, PersonMonth]]] = []
    month = last
    for _ in range(MONTHS_READ):
        usage = build_usage(workspace, month)
        if usage.covers == (month.first_day, month.last_day):
            found.append((month, _by_person(usage)))
        month = month.previous_month()
    return list(reversed(found))


def _by_person(usage: MonthUsage) -> dict[str, PersonMonth]:
    totals: dict[str, PersonMonth] = defaultdict(PersonMonth)
    for row in usage.rows:
        so_far = totals[row.person]
        totals[row.person] = PersonMonth(
            so_far.requests + row.requests, so_far.fresh_tokens + row.fresh_tokens, so_far.extra_usd + row.extra_usd
        )
    return dict(totals)


def find_recommendations(workspace: Path, settings: Settings, last: Month, today: date) -> tuple[list[Recommendation], list[str]]:
    """What the rules recommend from the months up to `last`, with notes on anything they couldn't judge."""
    months = whole_months(workspace, last)
    if len(months) < MONTHS_NEEDED:
        return [], [
            f"seat recommendations need at least {MONTHS_NEEDED} whole months of the claude.ai spend export "
            f"in the {MONTHS_READ} months to {last.label}; {len(months)} {'is' if len(months) == 1 else 'are'} saved"
        ]
    notes: list[str] = []
    basis = f"{months[0][0].label} to {months[-1][0].label}" if len(months) > 1 else months[0][0].label
    labels = ", ".join(month.label for month, _ in months)
    seats = {row.email: row.seat_type for row in read_people_file(workspace) if row.vendor == VENDOR}
    plans = settings.seats.get(VENDOR, {})
    found: list[Recommendation] = []

    def recommend(person: str, action: str, from_seat: str, to_seat: str, monthly: Decimal | None,
                  why: str, baseline: Decimal | None = None) -> None:
        bought = plans[from_seat].bought if action in (RECLAIM, DOWNGRADE) and from_seat in plans else None
        found.append(Recommendation("", today, basis, VENDOR, person, action, from_seat, to_seat,
                                    monthly.quantize(CENT) if monthly is not None else None,
                                    baseline.quantize(CENT) if baseline is not None else None, why,
                                    seats_bought_then=bought))

    def use(person: str) -> list[PersonMonth]:
        return [by_person.get(person, PersonMonth()) for _, by_person in months]

    # Every month after the last whole one read, saved in part if at all: someone who used
    # Claude since keeps their seat.
    used_since: set[str] = set()
    month = months[-1][0].next_month()
    while month <= Month.of(today):
        used_since |= {person for person, use_ in _by_person(build_usage(workspace, month)).items() if use_.requests}
        month = month.next_month()
    # Someone whose seat the ledger changed in these months was on another seat for part of them,
    # so their months say little about the seat they hold now, or about what a Standard seat covers.
    changed: set[str] = {
        item.person for item in read_ledger(workspace)
        if item.status == DONE and item.action in (UPGRADE, DOWNGRADE) and item.decided_on
        and item.decided_on >= months[0][0].first_day
    }

    gap: Decimal | None = None
    if STANDARD in plans and PREMIUM in plans:
        gap = plans[PREMIUM].price_usd - plans[STANDARD].price_usd
    else:
        notes.append(f"settings have no {VENDOR} {STANDARD} and {PREMIUM} seats, so there are no upgrades or downgrades to suggest")
    # Per month, the most fresh tokens a Standard holder used without paying extra: Premium use at
    # or under that fits in a Standard seat.
    standard_ceiling = [
        max((use_.fresh_tokens for person, use_ in by_person.items()
             if seats.get(person) == STANDARD and person not in changed and not use_.extra_usd), default=None)
        for _, by_person in months
    ]

    for person, seat in sorted(seats.items()):
        history = use(person)
        recent = history[-MONTHS_NEEDED:]
        if all(month.requests == 0 for month in recent) and person not in used_since:
            price = plans[seat].price_usd if seat in plans else None
            recommend(person, RECLAIM, seat, "", price,
                      f"no requests in {', '.join(m.label for m, _ in months[-MONTHS_NEEDED:])}. It saves only once "
                      "seats bought goes down; on yearly billing, at renewal")
            continue
        if sum(month.requests < CHECK_IN_REQUESTS for month in history) >= MONTHS_NEEDED:
            requests = ", ".join(str(month.requests) for month in history)
            recommend(person, CHECK_IN, seat, "", None, f"under {CHECK_IN_REQUESTS} requests a month ({requests} in {labels})")
        if gap is None or person in changed:
            continue
        baseline = sum((month.extra_usd for month in history), ZERO) / len(history)
        # The rule holds in two months, but the saving is the average, which one quiet month can sink.
        if seat == STANDARD and sum(month.extra_usd > gap for month in history) >= MONTHS_NEEDED and baseline > gap:
            extras = ", ".join(dollars(month.extra_usd) for month in history)
            recommend(person, UPGRADE, STANDARD, PREMIUM, baseline - gap,
                      f"extra usage of {extras} in {labels}, against Premium's {dollars(gap)} more a month. "
                      "Premium includes 5 times Standard's usage, so this saving is the most it could be", baseline)
        fits_standard = sum(
            ceiling is not None and month.fresh_tokens <= ceiling for month, ceiling in zip(history, standard_ceiling)
        )
        if seat == PREMIUM and all(not month.extra_usd for month in history) and fits_standard >= MONTHS_NEEDED:
            recommend(person, DOWNGRADE, PREMIUM, STANDARD, gap,
                      f"no extra usage in {labels}, and use a Standard holder had without paying extra")

    extra_by_month = [sum((use_.extra_usd for use_ in by_person.values()), ZERO) for _, by_person in months]
    saving, plan = bundle_plan(min(extra_by_month))
    if saving:
        recommend("", BUNDLES, "", "", saving,
                  f"extra usage of {', '.join(dollars(amount) for amount in extra_by_month)} in {labels}. Buying {plan} "
                  "each month at the bundle price saves this, if extra usage is paid at full price today")
    return found, notes


def bundle_plan(monthly_extra: Decimal) -> tuple[Decimal, str]:
    """The saving a month from covering this much extra usage with prepaid bundles, and the bundles to buy."""
    left = min(monthly_extra, BUNDLE_CREDIT_CAP)
    saving = ZERO
    bought: list[str] = []
    for price, credit in BUNDLE_SIZES:
        count = int(left // credit)
        if count:
            saving += (credit - price) * count
            left -= credit * count
            bought.append(f"{count} × {dollars(credit)} for {dollars(price)}")
    return saving, " and ".join(bought)


def read_ledger(workspace: Path) -> list[Recommendation]:
    path = workspace / LEDGER_FILE_NAME
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in LEDGER_COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise InputError(f"{LEDGER_FILE_NAME} is missing the columns {missing}")
        return [_recommendation(record, f"{LEDGER_FILE_NAME} line {number}") for number, record in enumerate(reader, start=2)]


def _recommendation(record: dict[str, str | None], where: str) -> Recommendation:
    def cell(column: str) -> str:
        return (record.get(column) or "").strip()

    def day(column: str) -> date | None:
        try:
            return date.fromisoformat(cell(column)) if cell(column) else None
        except ValueError:
            raise InputError(f"{where}: {column} must be a date like 2026-10-29, not {cell(column)!r}") from None

    def amount(column: str) -> Decimal | None:
        try:
            return Decimal(cell(column)) if cell(column) else None
        except InvalidOperation:
            raise InputError(f"{where}: {column} must be a dollar amount, not {cell(column)!r}") from None

    if cell("action") not in ACTIONS:
        raise InputError(f"{where}: action must be one of {', '.join(ACTIONS)}, not {cell('action')!r}")
    if cell("status") not in STATUSES:
        raise InputError(f"{where}: status must be one of {', '.join(STATUSES)}, not {cell('status')!r}")
    made_on = day("made_on")
    if made_on is None:
        raise InputError(f"{where} has no made_on date")
    if cell("seats_bought_then") and not cell("seats_bought_then").isdigit():
        raise InputError(f"{where}: seats_bought_then must be a whole number of seats, not {cell('seats_bought_then')!r}")
    return Recommendation(
        cell("id"), made_on, cell("basis"), cell("vendor"), cell("person").lower(), cell("action"),
        cell("from_seat"), cell("to_seat"), amount("monthly_usd"), amount("baseline_usd"), cell("why"),
        cell("status"), day("decided_on"), cell("note"),
        int(cell("seats_bought_then")) if cell("seats_bought_then") else None,
    )


def write_ledger(workspace: Path, ledger: list[Recommendation]) -> None:
    path = workspace / LEDGER_FILE_NAME
    partial = path.with_suffix(".csv.partial")
    with partial.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(LEDGER_COLUMNS)
        for item in ledger:
            writer.writerow([
                item.id, item.made_on.isoformat(), item.basis, item.vendor, item.person, item.action, item.from_seat,
                item.to_seat, "" if item.monthly_usd is None else f"{item.monthly_usd:f}",
                "" if item.baseline_usd is None else f"{item.baseline_usd:f}", item.why, item.status,
                item.decided_on.isoformat() if item.decided_on else "", item.note,
                "" if item.seats_bought_then is None else item.seats_bought_then,
            ])
    os.replace(partial, path)


def add_new(ledger: list[Recommendation], found: list[Recommendation]) -> tuple[list[Recommendation], list[Recommendation]]:
    """The ledger with what's newly recommended added, and those additions. A person and action
    already open or done isn't added again, nor one declined less than 90 days ago."""
    def already(item: Recommendation) -> bool:
        return any(
            old.person == item.person and old.action == item.action and old.vendor == item.vendor
            and (old.status != DECLINED or item.made_on - (old.decided_on or old.made_on) < ASK_AGAIN_AFTER)
            for old in ledger
        )

    next_number = max((int(old.id[1:]) for old in ledger if old.id[1:].isdigit()), default=0) + 1
    added: list[Recommendation] = []
    for item in found:
        if not already(item):
            added.append(replace(item, id=f"R{next_number}"))
            next_number += 1
    return ledger + added, added


def decide(ledger: list[Recommendation], identifier: str, status: str, on: date, note: str) -> list[Recommendation]:
    if status not in (DONE, DECLINED):
        raise InputError(f'a recommendation is marked "{DONE}" or "{DECLINED}", not {status!r}')
    if not any(item.id == identifier.upper() for item in ledger):
        raise InputError(f"{LEDGER_FILE_NAME} has no recommendation {identifier}; its ids are like R1")
    return [
        replace(item, status=status, decided_on=on, note=note or item.note) if item.id == identifier.upper() else item
        for item in ledger
    ]


def outcomes(workspace: Path, settings: Settings, ledger: list[Recommendation], today: date) -> list[Outcome]:
    """What each done recommendation has saved in the whole months since it was done. A reclaim or
    downgrade saves only once settings show fewer of its seats bought than when it was made: an
    emptied seat is still paid for. The roster and settings say only how things stand today, so
    a seat given back, or bought again, stops the saving for every month, not just the later ones."""
    seats = {row.email: row.seat_type for row in read_people_file(workspace) if row.vendor == VENDOR}
    last_whole = Month.of(today).previous_month()
    results: list[Outcome] = []
    for item in ledger:
        if item.status != DONE or item.decided_on is None or item.action == CHECK_IN:
            continue
        since = Month.of(item.decided_on).next_month()
        months: list[Month] = []
        while since <= last_whole:
            months.append(since)
            since = since.next_month()
        monthly = item.monthly_usd or ZERO
        if item.action in (RECLAIM, DOWNGRADE):
            plan = settings.seats.get(item.vendor, {}).get(item.from_seat)
            bought_now = plan.bought if plan else 0
            if item.action == RECLAIM and item.person in seats:
                problem = "the roster shows the seat given back"
            elif item.action == DOWNGRADE and seats.get(item.person) != item.to_seat:
                problem = f"the roster doesn't show a {item.to_seat} seat"
            elif item.seats_bought_then is not None and bought_now >= item.seats_bought_then:
                problem = (f"not saving yet: settings still show {bought_now} {item.from_seat} seats bought, "
                           f"{item.seats_bought_then} when it was made")
            else:
                problem = ""
            results.append(Outcome(item, ZERO if problem else monthly * len(months), len(months), problem))
        elif item.action == UPGRADE:
            gap = (item.baseline_usd or ZERO) - monthly
            measured = []
            for month in months:
                usage = build_usage(workspace, month)
                if usage.covers == (month.first_day, month.last_day):
                    measured.append(_by_person(usage).get(item.person, PersonMonth()))
            saved = sum(((item.baseline_usd or ZERO) - use_.extra_usd - gap for use_ in measured), ZERO)
            results.append(Outcome(item, saved, len(measured)))
        else:
            results.append(Outcome(item, monthly * len(months), len(months), "as estimated; the credit receipts in bills.csv show it"))
    return results


def describe(added: list[Recommendation], ledger: list[Recommendation], results: list[Outcome], notes: list[str]) -> str:
    lines = ["Seat recommendations, for you and not the shared page:"]
    lines += [f"  Note: {note}" for note in notes]
    still_open = [item for item in ledger if item.status == OPEN]
    if not still_open:
        lines.append("  Nothing open.")
    for item in still_open:
        who = item.person or "for the company"
        change = f" {item.from_seat} → {item.to_seat}" if item.to_seat else ""
        bound = "up to " if item.action == UPGRADE else ""
        saving = f", saves {bound}{dollars(item.monthly_usd)} a month" if item.monthly_usd else ""
        new = " (new)" if item in added else ""
        lines.append(f"  {item.id}{new}: {item.action} {who}{change}{saving}. Why: {item.why}")
    if results:
        total = sum((result.saved_usd for result in results), ZERO)
        lines += ["", f"Done so far has saved {dollars(total)}:"]
        for result in results:
            item = result.recommendation
            detail = f"; {result.problem}" if result.problem else ""
            lines.append(f"  {item.id} {item.action} {item.person or 'the company'}: {dollars(result.saved_usd)} "
                         f"over {result.months} whole month{'s' if result.months != 1 else ''}{detail}")
    lines += ["", "Mark one with `aicfo-spend decide <folder> <id> done` or `declined`."]
    return "\n".join(lines)
