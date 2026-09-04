"""The naive recovery bot -- our control condition.

This is what the rest of the market builds, and it is built here **properly**.
It gets the same features the uplift model will get, a real calibrated
classifier rather than a rule table, and a sensible objective: rank by expected
recovered rupees, spend the budget top-down. A strawman baseline would make the
whole comparison worthless, so this one is meant to be good.

Its single flaw is the one the entire industry shares: **it has no control
group.** Every row it ever trains on was contacted, so the only thing it can
learn is

    P(recovers | contacted, X)

which is p1. It cannot form P(recovers | not contacted, X), so it cannot know
that the customers it ranks highest -- transient rail failures -- were going to
pay anyway. It then books every post-contact recovery as its own work.

Nothing about that is stupid. It is what you get when the metric is gross
recovery and nobody is willing to withhold treatment.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from antar.features import build_features
from antar.sensorium import FailureRecord


@dataclass
class Selection:
    """Who a policy chose to contact, and what it expected to get."""

    chosen: list[str]
    scores: dict[str, float]
    n_eligible: int

    @property
    def n_contacted(self) -> int:
        return len(self.chosen)


class NaiveRecoveryBot:
    """Contacts by predicted P(recover | contacted) x amount. No holdout."""

    name = "naive_baseline"

    def __init__(self, random_state: int = 0) -> None:
        self.model = Pipeline(
            [
                ("scale", StandardScaler()),
                ("lr", LogisticRegression(max_iter=1000, random_state=random_state)),
            ]
        )
        self.fitted = False

    # -- learning --------------------------------------------------------

    def fit(self, records: Sequence[FailureRecord], outcomes: Sequence[int]) -> NaiveRecoveryBot:
        """Train on contacted transactions only -- all this bot ever sees.

        `outcomes` are realised recoveries for transactions that WERE contacted.
        There is no untreated arm in this data set, by construction, because the
        bot never withholds.
        """
        if len(records) != len(outcomes):
            raise ValueError("records and outcomes must be the same length")

        X = build_features(records)
        y = np.asarray(outcomes, dtype=int)

        if len(np.unique(y)) < 2:
            raise ValueError("need both recovered and non-recovered examples to fit")

        self.model.fit(X, y)
        self.fitted = True
        return self

    # -- scoring ---------------------------------------------------------

    def predict_recovery_prob(self, records: Sequence[FailureRecord]) -> np.ndarray:
        if not self.fitted:
            raise RuntimeError("fit() before predicting")
        return self.model.predict_proba(build_features(records))[:, 1]

    def score(self, records: Sequence[FailureRecord]) -> dict[str, float]:
        """Expected recovered rupees. The industry's objective function."""
        probs = self.predict_recovery_prob(records)
        return {
            r.txn_id: float(p * r.amount_inr)
            for r, p in zip(records, probs, strict=True)
        }

    # -- acting ----------------------------------------------------------

    def select(self, records: Sequence[FailureRecord], budget: int | None = None) -> Selection:
        """Rank by expected recovery and spend the contact budget top-down.

        Every failure is eligible. The bot has no concept of a failure that
        should not be contacted -- `contactable` is ANTAR's judgement about
        which class the customer is even the right lever for, and honouring it
        here would hand the baseline our thesis for free. Real dunning tools
        email on every failed payment, so this one does too.

        Note what else is *not* here: no check on whether contacting changes
        anything, and no reason to ever leave budget unspent.
        """
        eligible = list(records)
        scores = self.score(eligible)
        ranked = sorted(eligible, key=lambda r: -scores[r.txn_id])
        chosen = ranked if budget is None else ranked[:budget]
        return Selection(
            chosen=[r.txn_id for r in chosen],
            scores=scores,
            n_eligible=len(eligible),
        )
