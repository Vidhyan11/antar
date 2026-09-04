"""The Counterfactual P&L.

Every line here except the first is a subtraction, and that is the point. A
conventional recovery dashboard shows the top line and stops.

    Last-touch claim          what a normal tool reports
    - self-recovery           what the control arm says was coming anyway
    = INCREMENTAL RECOVERY    money we actually caused
    x margin                  revenue is not profit
    - channel cost            what the messages cost to send
    - discounts               margin given away to close the recovery
    - retention damage        customers lost because we contacted them
    = NET VALUE CREATED

The retention line is the one no vendor reports, for the obvious reason that a
tool paid on gross recovery has no incentive to price the customers it burns.
It is measured here the same way everything else is -- from the control arm --
so the agent is charged for annoying people using evidence rather than a guess.

The confidence interval on the bottom line is always-valid, so it survives the
fact that ANTAR watches this number continuously and stops arms on it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

from antar.evaluation import TruthBook
from antar.sensorium import FailureRecord
from antar.stats.sequential import always_valid_difference


@dataclass
class PnL:
    """One recovery P&L, in rupees."""

    contacts: int
    eligible: int

    last_touch_claim: float
    self_recovery: float
    incremental_recovery: float

    margin_rate: float
    incremental_margin: float
    channel_cost: float
    discount_cost: float
    retention_damage: float
    net_value: float

    ci_low: float
    ci_high: float

    optout_delta: float
    customers_lost: float

    @property
    def overstatement(self) -> float:
        """How many times larger the headline is than the truth."""
        return (self.last_touch_claim / self.incremental_recovery
                if self.incremental_recovery > 1e-9 else float("inf"))

    def to_payload(self) -> dict[str, float | int]:
        return {
            "contacts": self.contacts,
            "eligible": self.eligible,
            "last_touch_claim": self.last_touch_claim,
            "self_recovery": self.self_recovery,
            "incremental_recovery": self.incremental_recovery,
            "margin_rate": self.margin_rate,
            "incremental_margin": self.incremental_margin,
            "channel_cost": self.channel_cost,
            "discount_cost": self.discount_cost,
            "retention_damage": self.retention_damage,
            "net_value": self.net_value,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "optout_delta": self.optout_delta,
            "customers_lost": self.customers_lost,
            "overstatement": self.overstatement,
        }

    def render(self, title: str, width: int = 62) -> str:
        rule = "-" * width

        def line(label: str, value: float, negative: bool = False) -> str:
            shown = f"({abs(value):>13,.0f})" if negative else f" {value:>13,.0f} "
            return f"  {label:<38}{shown}"

        return "\n".join([
            title,
            rule,
            line("Last-touch claim (what a tool reports)", self.last_touch_claim),
            line("Less: control-implied self-recovery", self.self_recovery, True),
            rule,
            line("INCREMENTAL RECOVERY", self.incremental_recovery),
            line(f"x gross margin ({self.margin_rate:.0%})", self.incremental_margin),
            line("Less: channel cost", self.channel_cost, True),
            line("Less: discounts given", self.discount_cost, True),
            line("Less: measured retention damage *", self.retention_damage, True),
            rule,
            line("NET VALUE CREATED", self.net_value),
            f"  {'95% always-valid CI':<38} [{self.ci_low:>+11,.0f},{self.ci_high:>+11,.0f}]",
            "",
            f"  Contacts sent: {self.contacts:,} of {self.eligible:,} eligible "
            f"({1 - self.contacts / max(self.eligible, 1):.0%} deliberately not contacted)",
            f"  Headline overstates what was caused by {self.overstatement:.1f}x",
            "",
            f"  * treated-vs-control opt-out delta of {self.optout_delta:+.4f}, "
            f"~{self.customers_lost:.0f} customers,",
            "    priced at merchant LTV. The agent is charged for annoying people.",
        ])


def _channel_cost(cfg: SimpleNamespace, action: str) -> float:
    return float(getattr(cfg.economics.channel_cost_inr, action, 0.0))


def _discount_rate(cfg: SimpleNamespace, action: str) -> float:
    return float(getattr(cfg.economics.discount_rate, action, 0.0))


def build_pnl(
    cfg: SimpleNamespace,
    *,
    contacted: Sequence[str],
    eligible: Sequence[str],
    actions: Mapping[str, str],
    records: Mapping[str, FailureRecord],
    truth: TruthBook,
    alpha: float = 0.05,
    holdout_fraction: float = 0.10,
) -> PnL:
    """Assemble the P&L for one policy's chosen cohort.

    `contacted` is who the policy acted on; `eligible` is everyone it could have.
    The truth book supplies both arms, which is the only reason any of the
    subtractions below can be computed rather than estimated.
    """
    econ = cfg.economics
    contacted = list(contacted)

    claim = truth.gross_claimed_value(contacted, {t: records[t].amount_paise for t in contacted})
    incremental = truth.true_incremental_value(
        contacted, {t: records[t].amount_paise for t in contacted}
    )
    self_recovery = claim - incremental

    channel = sum(_channel_cost(cfg, actions.get(t, "")) for t in contacted)

    # A discount only costs anything on transactions the concession actually
    # closed -- offering 5% to someone who does not come back costs nothing.
    discount = sum(
        _discount_rate(cfg, actions.get(t, ""))
        * records[t].amount_inr
        * max(truth[t].y1 - truth[t].y0, 0)
        for t in contacted
    )

    margin = incremental * float(econ.margin_rate)

    # Retention damage, measured rather than assumed: the treated-vs-control
    # difference in opt-out rate over the same cohort, priced at LTV.
    treated_optout = [float(truth[t].o1) for t in contacted]
    control_optout = [float(truth[t].o0) for t in contacted]
    optout_delta = (float(np.mean(treated_optout)) - float(np.mean(control_optout))
                    if contacted else 0.0)
    customers_lost = optout_delta * len(contacted)
    retention = customers_lost * float(econ.customer_ltv_inr)

    net = margin - channel - discount - retention

    # Interval on the net figure. Per-transaction net value is bounded, so a
    # sub-Gaussian confidence sequence applies; the bound is the largest net
    # contribution any single transaction can make.
    per_txn_treated, per_txn_control = [], []
    for t in contacted:
        amt = records[t].amount_inr
        act = actions.get(t, "")
        gain1 = truth[t].y1 * amt * float(econ.margin_rate)
        gain0 = truth[t].y0 * amt * float(econ.margin_rate)
        cost = _channel_cost(cfg, act) + float(truth[t].o1) * float(econ.customer_ltv_inr)
        cost0 = float(truth[t].o0) * float(econ.customer_ltv_inr)
        per_txn_treated.append(gain1 - cost)
        per_txn_control.append(gain0 - cost0)

    bound = max(
        (max(records[t].amount_inr for t in contacted) * float(econ.margin_rate)
         + float(econ.customer_ltv_inr)) if contacted else 1.0,
        1.0,
    )
    ci = always_valid_difference(
        per_txn_treated, per_txn_control, alpha=alpha, bound=bound,
        n_target=max(len(contacted) * 2, 2), holdout_fraction=holdout_fraction,
    )

    return PnL(
        contacts=len(contacted),
        eligible=len(eligible),
        last_touch_claim=claim,
        self_recovery=self_recovery,
        incremental_recovery=incremental,
        margin_rate=float(econ.margin_rate),
        incremental_margin=margin,
        channel_cost=channel,
        discount_cost=discount,
        retention_damage=retention,
        net_value=net,
        ci_low=net - ci.radius * len(contacted),
        ci_high=net + ci.radius * len(contacted),
        optout_delta=optout_delta,
        customers_lost=customers_lost,
    )
