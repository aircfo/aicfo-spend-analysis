"""Spend with other AI vendors paid through Ramp, beside the Anthropic and OpenAI numbers.

Which vendors count is the company's call, listed in settings as `other_ai_vendors`: Claude
proposes them from Ramp's spend by vendor at setup, and the operator confirms. Ramp's spend
report gives what was paid but not what for, so it stays out of the teams, the totals and the
forecast, and is shown on its own until the vendor can be read like Anthropic and OpenAI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from aicfo_spend.months import Month
from aicfo_spend.raw_store import RAMP_VENDOR_SPEND, fullest_in_month, saved_files
from aicfo_spend.report import ZERO, dollars
from aicfo_spend.settings import Settings
from aicfo_spend.sources.ramp import read_vendor_spend


@dataclass(frozen=True)
class OtherAiSpend:
    # Each vendor named in settings with what Ramp paid it, biggest first; $0 where nothing was paid.
    vendors: list[tuple[str, Decimal]]
    # The last day the saved spend reaches; None when none is saved for the month.
    through: date | None

    @property
    def total(self) -> Decimal:
        return sum((amount for _, amount in self.vendors), ZERO)


def other_ai_spend(workspace: Path, month: Month, settings: Settings) -> OtherAiSpend | None:
    """None when settings name no other AI vendors."""
    if not settings.other_ai_vendors:
        return None
    saved = fullest_in_month(saved_files(workspace, RAMP_VENDOR_SPEND, month), month)
    if saved is None:
        return OtherAiSpend([(name, ZERO) for name in settings.other_ai_vendors], None)
    paid = read_vendor_spend(saved.path)
    vendors = [(name, paid.get(name.lower(), ZERO)) for name in settings.other_ai_vendors]
    return OtherAiSpend(sorted(vendors, key=lambda vendor: -vendor[1]), saved.end)


def summarize_other_ai_spend(month: Month, spend: OtherAiSpend) -> str:
    if spend.through is None:
        return f"Other AI vendors: no Ramp spend by vendor is saved for {month.label}"
    lines = [f"Other AI vendors paid through Ramp, through {spend.through} (not in the teams, totals or forecast above):"]
    lines += [f"  {name}: {dollars(amount)}" for name, amount in spend.vendors]
    lines.append(f"  Total: {dollars(spend.total)}")
    return "\n".join(lines)
