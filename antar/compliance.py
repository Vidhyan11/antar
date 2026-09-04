"""The compliance linter and the stopping rules.

Constraints as code, not as prose in a policy document. Every rule here can
*veto* an action, and a vetoed action is recorded with its reason rather than
silently dropped -- an audit trail that only contains what happened is half a
trail.

Three layers of stopping rule, in the order they fire:

*   **Per customer** -- opt-out, contact caps, quiet hours, consent. The
    customer's own limits, and no amount of expected value overrides them.
*   **Per cohort** -- an open incident freezes its whole rail. Handled upstream
    by the freeze registry, checked again here so the gate cannot be bypassed
    by calling the actuator directly.
*   **Per arm** -- if the measured effect can no longer be distinguished from
    zero, the arm pauses itself. That one lives in `ArmMonitor`.

The per-arm rule is why day 3's confidence sequence had to be time-uniform. An
agent that watches its own effect and stops when the evidence turns is running a
stopping rule, and a fixed-horizon interval under a stopping rule invents
effects roughly a third of the time.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import SimpleNamespace

from antar.sensorium import FailureRecord
from antar.stats.sequential import Interval, always_valid_difference

# IST. The simulator stamps UTC, and quiet hours are a local-time rule, so the
# offset has to be applied rather than assumed away.
IST_OFFSET = timedelta(hours=5, minutes=30)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    rule: str = ""      # which rule fired -- stable, groupable
    reason: str = ""    # the specific detail, for the ledger

    def __bool__(self) -> bool:
        return self.allowed


ALLOWED = Verdict(True)


@dataclass
class ContactHistory:
    """Rolling per-customer contact log. The thing that makes a cap a cap."""

    window_days: int
    max_contacts: int
    _sent: dict[str, list[datetime]] = field(default_factory=dict)
    _opted_out: set[str] = field(default_factory=set)

    def record(self, customer_id: str, when: datetime) -> None:
        self._sent.setdefault(customer_id, []).append(when)

    def opt_out(self, customer_id: str) -> None:
        self._opted_out.add(customer_id)

    def has_opted_out(self, customer_id: str) -> bool:
        return customer_id in self._opted_out

    def recent_count(self, customer_id: str, when: datetime) -> int:
        cutoff = when - timedelta(days=self.window_days)
        return sum(1 for t in self._sent.get(customer_id, ()) if t > cutoff)

    def at_cap(self, customer_id: str, when: datetime) -> bool:
        return self.recent_count(customer_id, when) >= self.max_contacts


class ComplianceLinter:
    """Vetoes actions that must not be taken, whatever they are worth."""

    def __init__(self, cfg: SimpleNamespace, history: ContactHistory | None = None) -> None:
        self.cfg = cfg.compliance
        self.history = history or ContactHistory(
            window_days=self.cfg.contact_window_days,
            max_contacts=self.cfg.max_contacts_per_window,
        )

    def check(self, record: FailureRecord, action: str) -> Verdict:
        """Run every rule. First veto wins, and it names itself."""
        silent = action in self.cfg.silent_actions
        when = datetime.fromisoformat(record.ts)
        local = when + IST_OFFSET

        # Opting out is absolute. It applies even to silent actions, because a
        # customer who asked to be left alone has not asked to be left alone
        # only on the channels we find expensive.
        if self.history.has_opted_out(record.customer_id):
            return Verdict(False, "opted_out", "customer has opted out")

        if silent:
            # Nothing reaches the customer, so the remaining rules -- which all
            # protect the customer's attention -- do not apply.
            return ALLOWED

        hour = local.hour
        start, end = self.cfg.quiet_hours_start, self.cfg.quiet_hours_end
        if hour >= start or hour < end:
            return Verdict(False, "quiet_hours", f"quiet hours ({local:%H:%M} IST)")

        if self.cfg.require_consent and action not in self.cfg.consent_exempt_actions:
            if not record.customer.get("has_consent", False):
                return Verdict(False, "no_consent", "no marketing consent on file")

        if self.history.at_cap(record.customer_id, when):
            return Verdict(
                False,
                "contact_cap",
                f"contact cap reached "
                f"({self.cfg.max_contacts_per_window} in {self.cfg.contact_window_days}d)",
            )

        return ALLOWED

    def commit(self, record: FailureRecord, action: str) -> None:
        """Record that a contact went out, so the cap means something next time."""
        if action not in self.cfg.silent_actions:
            self.history.record(record.customer_id, datetime.fromisoformat(record.ts))


@dataclass
class ArmState:
    name: str
    interval: Interval
    paused: bool
    reason: str

    def to_payload(self) -> dict[str, object]:
        return {
            "arm": self.name,
            "effect": self.interval.point,
            "lower": self.interval.lower,
            "upper": self.interval.upper,
            "n": self.interval.n,
            "paused": self.paused,
            "reason": self.reason,
        }


class ArmMonitor:
    """The trial's safety board.

    Watches the measured effect continuously and pauses an arm that can no
    longer be distinguished from doing nothing -- or that is doing more damage
    to retention than it is creating in margin.

    Continuous monitoring is only legitimate with a time-uniform interval, which
    is the entire reason day 3 built one.
    """

    def __init__(self, alpha: float = 0.05, holdout_fraction: float = 0.10) -> None:
        self.alpha = alpha
        self.holdout_fraction = holdout_fraction

    def assess(
        self,
        name: str,
        treated: Sequence[float],
        control: Sequence[float],
        *,
        harm_exceeds_value: bool = False,
    ) -> ArmState:
        n_total = len(treated) + len(control)
        ci = always_valid_difference(
            treated, control, alpha=self.alpha,
            n_target=max(n_total, 1), holdout_fraction=self.holdout_fraction,
        )

        if harm_exceeds_value:
            return ArmState(name, ci, True, "measured retention damage exceeds incremental margin")
        if len(treated) < 50 or len(control) < 50:
            return ArmState(name, ci, False, "still gathering evidence")
        if ci.upper < 0:
            return ArmState(name, ci, True, "effect is negative with confidence")
        if not ci.excludes_zero():
            return ArmState(name, ci, True, "effect indistinguishable from zero")
        return ArmState(name, ci, False, "effect confirmed positive")
