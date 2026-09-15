"""The rate card, and the statement drawn from it (axor-packaging.md §1).

One ladder: a rung carries a monthly price and an allowance of governed nodes,
and a fleet past the allowance is charged per node. `node_activity` measures the
fleet; this turns a measurement into a line a customer can be asked to pay.

Three rules shape every function here.

**Money is integer cents.** Not a float, not a Decimal parsed from a string on
the way in. A cent that cannot be represented exactly is a cent somebody
eventually argues about, and the arithmetic here is addition and one
multiplication — there is nothing a float buys.

**A month that is not over is not a bill.** The peak of a month still running
can only go up, so a statement for it is marked provisional and says which day
it was computed on. Only a closed month is final.

**A price nobody agreed to is never invented.** The `enterprise` rung is
contracted: it has no list price, so a statement for it carries the measured
usage and refuses to total it, exactly as the issuing CLI refuses to guess its
fleet size.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

# rung -> (monthly price in cents, price per governed node beyond the
# allowance, in cents). The allowance itself is not here: it is the ceiling
# inside each license, so a rung sold with a negotiated fleet bills against the
# fleet it was sold with rather than the list one.
#
# `community` is free and cannot overage: the free rung is where safety lives,
# and safety never costs (monetization Line 1). `enterprise` is absent because
# it is contracted — see the module docstring.
RATE_CARD: dict[str, tuple[int, int]] = {
    "community": (0, 0),
    "team": (299_00, 75_00),
    "security": (1_500_00, 50_00),
}
CURRENCY = "USD"


def money(cents: int) -> str:
    """``12345`` -> ``"$123.45"``. Formatting only ever happens at the edge; the
    arithmetic above it is integers all the way down."""
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{part:02d}"


@dataclass(frozen=True)
class Statement:
    """One month, one tenant, one rung — and the evidence behind the number."""

    month: str  # YYYY-MM
    organization: str
    workspace_tier: str
    currency: str
    base_cents: int
    included_nodes: int
    peak_nodes: int
    peak_day: str | None
    billable_nodes: int  # peak beyond the allowance, never negative
    overage_cents: int
    total_cents: int
    provisional: bool  # the month is still running; the peak can still rise
    computed_on: str
    priced: bool  # false for a contracted rung: usage measured, total withheld
    note: str

    def as_dict(self) -> dict[str, object]:
        return {
            "month": self.month,
            "organization": self.organization,
            "workspace_tier": self.workspace_tier,
            "currency": self.currency,
            "base_cents": self.base_cents,
            "base": money(self.base_cents),
            "included_nodes": self.included_nodes,
            "peak_nodes": self.peak_nodes,
            "peak_day": self.peak_day,
            "billable_nodes": self.billable_nodes,
            "overage_cents": self.overage_cents,
            "overage": money(self.overage_cents),
            "total_cents": self.total_cents,
            "total": money(self.total_cents),
            "provisional": self.provisional,
            "computed_on": self.computed_on,
            "priced": self.priced,
            "note": self.note,
        }


def month_is_closed(month: str, on: date | None = None) -> bool:
    """Whether `month` (YYYY-MM) has ended. A statement for an open month is
    provisional, because its peak can still rise."""
    today = on or datetime.now(UTC).date()
    return month < today.strftime("%Y-%m")


def statement(
    month: str,
    organization: str,
    workspace_tier: str,
    included_nodes: int,
    peak_nodes: int,
    peak_day: str | None,
    *,
    on: date | None = None,
) -> Statement:
    """The statement for one month, from the rung and the measured peak.

    The allowance is the license's own ceiling, not the rung's list allowance:
    a customer who negotiated 200 nodes is billed against 200.
    """
    closed = month_is_closed(month, on)
    today = (on or datetime.now(UTC).date()).isoformat()
    billable = max(0, peak_nodes - included_nodes)
    rate = RATE_CARD.get(workspace_tier)
    if rate is None:
        return Statement(
            month=month, organization=organization,
            workspace_tier=workspace_tier, currency=CURRENCY,
            base_cents=0, included_nodes=included_nodes,
            peak_nodes=peak_nodes, peak_day=peak_day,
            billable_nodes=billable, overage_cents=0, total_cents=0,
            provisional=not closed, computed_on=today, priced=False,
            note=(
                f"the {workspace_tier} rung is contracted and has no list "
                "price; the usage above is what it is billed from, and the "
                "amount is whatever the contract says."
            ),
        )
    base, per_node = rate
    overage = billable * per_node
    return Statement(
        month=month, organization=organization, workspace_tier=workspace_tier,
        currency=CURRENCY, base_cents=base, included_nodes=included_nodes,
        peak_nodes=peak_nodes, peak_day=peak_day, billable_nodes=billable,
        overage_cents=overage, total_cents=base + overage,
        provisional=not closed, computed_on=today, priced=True,
        note=(
            "final" if closed else
            f"provisional: {month} is still running and its peak can only rise"
        ),
    )
