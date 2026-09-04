"""The truth book.

The only object in the system permitted to hold ground truth. Policies receive
FailureRecords; the truth book stays sealed until an evaluator opens it to
score what a policy did.

In production this object does not exist -- you genuinely cannot know the
counterfactual. That is the entire problem ANTAR is built around. Here it
exists so we can check whether the estimator that *will* run in production is
recovering the right answer.

Keeping it in a separate module with a loud name is deliberate: an import of
`antar.evaluation` inside a policy is a bug you can grep for.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from antar.models import FailureEvent


@dataclass(frozen=True)
class Truth:
    p0: float
    p1: float
    y0: int
    y1: int
    stratum: str
    q0: float = 0.0    # P(opt out | untreated)
    q1: float = 0.0    # P(opt out | treated)
    o0: int = 0        # realised opt-out if untreated
    o1: int = 0        # realised opt-out if treated

    @property
    def uplift(self) -> float:
        return self.p1 - self.p0

    @property
    def optout_uplift(self) -> float:
        """The damage half of the ledger: extra churn caused by contacting."""
        return self.q1 - self.q0

    def outcome(self, treated: bool) -> int:
        return self.y1 if treated else self.y0


class TruthBook(Mapping[str, Truth]):
    """txn_id -> ground truth. Read-only."""

    def __init__(self, events: Iterable[FailureEvent]) -> None:
        self._truth: dict[str, Truth] = {
            ev.txn_id: Truth(ev.p0, ev.p1, ev.y0, ev.y1, ev.stratum,
                             ev.q0, ev.q1, ev.o0, ev.o1)
            for ev in events
        }

    def __getitem__(self, txn_id: str) -> Truth:
        return self._truth[txn_id]

    def __iter__(self):
        return iter(self._truth)

    def __len__(self) -> int:
        return len(self._truth)

    # -- scoring ---------------------------------------------------------

    def realise(self, txn_ids: Iterable[str], treated: set[str]) -> dict[str, int]:
        """What the world would actually show, given who was treated.

        This is the bridge between the simulation and an honest evaluation: a
        policy picks who to treat, and only then does the truth book reveal the
        one outcome per transaction that a real deployment would have seen.
        """
        return {t: self[t].outcome(t in treated) for t in txn_ids}

    def true_incremental_value(
        self, treated: Iterable[str], amounts: Mapping[str, int]
    ) -> float:
        """Rupees genuinely caused by treating these transactions.

        Sums (y1 - y0) * amount over the treated set. For a sure-thing the term
        is zero because the money was arriving regardless; for a lost cause it
        is zero because it never arrives. Only persuadables contribute.
        """
        total_paise = 0
        for txn_id in treated:
            t = self[txn_id]
            total_paise += (t.y1 - t.y0) * amounts[txn_id]
        return total_paise / 100.0

    def gross_claimed_value(
        self, treated: Iterable[str], amounts: Mapping[str, int]
    ) -> float:
        """What a last-touch tool would report: every recovery after a contact.

        No counterfactual is subtracted, so money that was always going to
        arrive is booked as a win. This is the number the industry quotes.
        """
        total_paise = 0
        for txn_id in treated:
            if self[txn_id].y1 == 1:
                total_paise += amounts[txn_id]
        return total_paise / 100.0

    def stratum_counts(self, txn_ids: Iterable[str]) -> dict[str, int]:
        counts = {"sure_thing": 0, "persuadable": 0, "lost_cause": 0}
        for txn_id in txn_ids:
            counts[self[txn_id].stratum] += 1
        return counts
