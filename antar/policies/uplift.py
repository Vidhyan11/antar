"""The uplift model -- ANTAR's targeting core.

The baseline learns P(recover | contacted, X) and ranks by it. This learns two
functions, one per arm, and ranks by the *difference*:

    CATE(x) = P(recover | treated, x) - P(recover | control, x)

That is a T-learner, and it is only estimable because there is a control arm to
fit the second model on. Everything the holdout costs is bought back here: this
is the object the baseline structurally cannot form.

Why a T-learner rather than something fancier. The control arm is ~10% of the
data -- roughly a thousand rows -- and a flexible learner on a thousand rows
mostly fits noise, which a uplift model punishes twice because the noise enters
through a difference. Two regularised logistic regressions are the right amount
of model for the amount of data, and they are legible enough to explain in a
sentence.

Ranking by CATE alone maximises *responses*. We rank by CATE x amount, which
maximises *rupees* -- the merchant cares about the second one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from antar.features import build_features
from antar.policies.baseline import Selection
from antar.sensorium import FailureRecord

# numpy renamed trapz -> trapezoid in 2.0. Support both so CI's version matrix
# does not decide whether the Qini coefficient exists.
_trapezoid = getattr(np, "trapezoid", None) or np.trapz


def _arm_model(random_state: int) -> Pipeline:
    return Pipeline([
        ("scale", StandardScaler()),
        ("lr", LogisticRegression(max_iter=1000, C=0.5, random_state=random_state)),
    ])


@dataclass
class QiniCurve:
    """Incremental responders as a function of how deep you target."""

    fractions: np.ndarray
    incremental: np.ndarray
    random_line: np.ndarray

    @property
    def coefficient(self) -> float:
        """Area between the curve and the random line, normalised.

        Positive means the ranking finds uplift that random targeting would
        miss. Zero means the model is no better than picking at random, which
        is the honest verdict for a model that has learned nothing.
        """
        area = float(_trapezoid(self.incremental - self.random_line, self.fractions))
        denom = abs(float(_trapezoid(self.random_line, self.fractions)))
        return area / denom if denom > 1e-12 else 0.0


class UpliftTargeter:
    """T-learner over the randomised arms. Ranks by expected incremental rupees."""

    name = "antar_uplift"

    def __init__(self, random_state: int = 0) -> None:
        self.model_treated = _arm_model(random_state)
        self.model_control = _arm_model(random_state)
        self.fitted = False

    # -- learning --------------------------------------------------------

    def fit(
        self,
        records: Sequence[FailureRecord],
        outcomes: Sequence[int],
        treated_mask: Sequence[bool],
    ) -> UpliftTargeter:
        X = build_features(records)
        y = np.asarray(outcomes, dtype=int)
        t = np.asarray(treated_mask, dtype=bool)

        if t.sum() < 50 or (~t).sum() < 50:
            raise ValueError("both arms need at least 50 observations to fit a T-learner")
        for arm_y, label in ((y[t], "treated"), (y[~t], "control")):
            if len(np.unique(arm_y)) < 2:
                raise ValueError(f"{label} arm has a single outcome class; cannot fit")

        self.model_treated.fit(X[t], y[t])
        self.model_control.fit(X[~t], y[~t])
        self.fitted = True
        return self

    # -- scoring ---------------------------------------------------------

    def predict_cate(self, records: Sequence[FailureRecord]) -> np.ndarray:
        """Estimated per-transaction treatment effect."""
        if not self.fitted:
            raise RuntimeError("fit() before predicting")
        X = build_features(records)
        p1 = self.model_treated.predict_proba(X)[:, 1]
        p0 = self.model_control.predict_proba(X)[:, 1]
        return p1 - p0

    def score(self, records: Sequence[FailureRecord]) -> dict[str, float]:
        """Expected *incremental* rupees -- the objective the baseline cannot write."""
        cate = self.predict_cate(records)
        return {r.txn_id: float(c * r.amount_inr) for r, c in zip(records, cate, strict=True)}

    # -- acting ----------------------------------------------------------

    def select(self, records: Sequence[FailureRecord], budget: int | None = None) -> Selection:
        """Spend the budget on the highest expected incremental value.

        The one thing the baseline never does: transactions whose estimated
        effect is zero or negative are dropped **even when budget remains**.
        There is no reason to spend on a contact that changes nothing, and no
        reason to spend a budget just because it exists.

        Deliberately *not* here: a filter on `contactable`. An earlier version
        honoured that taxonomy flag and refused to contact transient rail
        failures at all. The sensitivity sweep killed it -- at low self-recovery
        rates class A carries real uplift, and the hardcoded rule forbade ANTAR
        from touching the largest and most valuable cohort, handing the sweep to
        the baseline. Freezing a domain judgement into a rule is the exact
        failure this project exists to argue against; the estimator subsumes it.
        Where the judgement is genuinely right, the model ranks those rows last
        on its own, which is a far stronger claim than asserting it.

        `contactable` still governs *which action* is appropriate once a
        transaction is chosen -- a silent retry is not a message -- and that is
        the actuator's business, not the targeter's.
        """
        eligible = list(records)
        scores = self.score(eligible)
        worthwhile = [r for r in eligible if scores[r.txn_id] > 0.0]
        ranked = sorted(worthwhile, key=lambda r: -scores[r.txn_id])
        chosen = ranked if budget is None else ranked[:budget]
        return Selection(
            chosen=[r.txn_id for r in chosen],
            scores=scores,
            n_eligible=len(eligible),
        )


# ------------------------------------------------------------- evaluation

def qini_curve(
    scores: Sequence[float],
    outcomes: Sequence[int],
    treated_mask: Sequence[bool],
    *,
    n_points: int = 50,
) -> QiniCurve:
    """Qini curve for a ranking, computed on randomised-arm data.

    At each depth k, the incremental responders among the top-k ranked
    transactions are

        responders_treated(k) - responders_control(k) * n_treated(k)/n_control(k)

    The control term is rescaled to the treated arm's size, which is what makes
    the two comparable when the split is 90/10.

    This is the honest way to evaluate an uplift ranking. Ordinary accuracy is
    not: a model can rank *outcomes* perfectly and *uplift* terribly, which is
    exactly the failure the baseline demonstrates.
    """
    s = np.asarray(scores, dtype=float)
    y = np.asarray(outcomes, dtype=int)
    t = np.asarray(treated_mask, dtype=bool)

    order = np.argsort(-s)
    y, t = y[order], t[order]

    cum_treated = np.cumsum(t)
    cum_control = np.cumsum(~t)
    cum_resp_t = np.cumsum(y * t)
    cum_resp_c = np.cumsum(y * ~t)

    n = len(y)
    ks = np.unique(np.linspace(1, n, n_points).astype(int))

    incremental = []
    for k in ks:
        nt, nc = cum_treated[k - 1], cum_control[k - 1]
        scaled_control = (cum_resp_c[k - 1] * nt / nc) if nc > 0 else 0.0
        incremental.append(cum_resp_t[k - 1] - scaled_control)

    fractions = ks / n
    incremental = np.asarray(incremental, dtype=float)
    random_line = fractions * incremental[-1]
    return QiniCurve(fractions, incremental, random_line)
