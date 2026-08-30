"""Domain objects.

Deliberately plain dataclasses rather than Pydantic at this layer -- the
simulator generates millions of these and validation overhead is wasted here.
Pydantic enters at the API boundary (webhooks in, agent proposals out), where
validation is the enforcement mechanism rather than a formality.

The important thing in this file is `FailureEvent`, which carries BOTH potential
outcomes. That is the whole reason a simulator exists: in the real world you
only ever observe one of y0/y1 for a given transaction. Here we know both, so we
can check whether the uplift model recovers the truth.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from antar.taxonomy import DeclineClass


@dataclass
class Customer:
    customer_id: str
    tenure_days: int
    inflow_day: int          # day of month salary/credit lands -- the Class C lever
    language: str
    has_consent: bool
    prior_txns: int
    prior_failures: int
    prior_self_recoveries: int

    # Latent traits. NOT features -- the model never sees these. They exist so
    # the observable columns above have something real underneath them to be
    # correlated with, which is what makes CATE estimation a fair test.
    _reliability: float = field(repr=False, default=0.5)
    _responsiveness: float = field(repr=False, default=0.5)

    @property
    def observable(self) -> dict[str, Any]:
        """Exactly what the uplift model is allowed to see."""
        return {
            "customer_id": self.customer_id,
            "tenure_days": self.tenure_days,
            "inflow_day": self.inflow_day,
            "language": self.language,
            "has_consent": self.has_consent,
            "prior_txns": self.prior_txns,
            "prior_failures": self.prior_failures,
            "prior_self_recoveries": self.prior_self_recoveries,
            "self_recovery_rate": (
                self.prior_self_recoveries / self.prior_failures
                if self.prior_failures
                else 0.0
            ),
        }


@dataclass
class Outage:
    issuer: str
    method: str
    start: datetime
    end: datetime

    def covers(self, when: datetime, issuer: str, method: str) -> bool:
        return self.issuer == issuer and self.method == method and self.start <= when < self.end


@dataclass
class FailureEvent:
    txn_id: str
    customer_id: str
    ts: datetime
    amount_paise: int
    issuer: str
    method: str
    reason_code: str
    decline_class: DeclineClass
    in_outage: bool

    # --- ground truth, never visible to any model or policy ---------------
    p0: float          # P(recover | untreated) for this specific transaction
    p1: float          # P(recover | treated)
    y0: int            # realised outcome if untreated
    y1: int            # realised outcome if treated

    @property
    def true_uplift(self) -> float:
        """Individual expected treatment effect. The thing day 6 must recover."""
        return self.p1 - self.p0

    @property
    def stratum(self) -> str:
        """Which of the three customer archetypes this transaction actually is.

        The names come from uplift modelling: contacting a sure-thing is waste,
        contacting a lost-cause is waste, and the entire value of the system is
        in finding the persuadables.
        """
        if self.y0 == 1:
            return "sure_thing"      # would have recovered anyway
        if self.y1 == 1:
            return "persuadable"     # recovers only if we act -- the only cohort worth money
        return "lost_cause"          # recovers under neither arm

    def observed_outcome(self, treated: bool) -> int:
        """What the real world would let us see, given an arm assignment."""
        return self.y1 if treated else self.y0

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["ts"] = self.ts.isoformat()
        row["decline_class"] = self.decline_class.value
        row["true_uplift"] = self.true_uplift
        row["stratum"] = self.stratum
        return row

    def to_ledger_payload(self) -> dict[str, Any]:
        """What the Sensorium is allowed to write down.

        Ground truth is stripped: the ledger must contain only what a real
        deployment could actually observe, or the P&L would be cheating.
        """
        return {
            "txn_id": self.txn_id,
            "customer_id": self.customer_id,
            "ts": self.ts.isoformat(),
            "amount_paise": self.amount_paise,
            "issuer": self.issuer,
            "method": self.method,
            "reason_code": self.reason_code,
            "decline_class": self.decline_class.value,
        }
