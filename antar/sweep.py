"""Sensitivity analysis.

The single most effective attack on this project is: *your headline number comes
from a self-recovery rate you invented, so you assumed your conclusion.* That is
a fair hit, and the answer is not to argue -- it is to move the assumption across
its plausible range and report what survives.

Two claims come out of this, and they are very different in strength:

*   **The magnitude does not survive.** How much money the baseline wastes
    depends directly on how often customers pay without being asked. We say so.
*   **The ranking does.** Targeting by estimated treatment effect beats targeting
    by predicted success rate at every self-recovery level, because it is simply
    the correct objective. That claim does not rest on the assumption at all.

Only the second claim goes in the pitch.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

from antar.evaluation import TruthBook
from antar.pipeline import ExperimentData, run_experiment
from antar.policies.baseline import NaiveRecoveryBot
from antar.policies.uplift import UpliftTargeter
from antar.taxonomy import DeclineClass


def scale_self_recovery(cfg: SimpleNamespace, scale: float) -> SimpleNamespace:
    """Return a copy of the config with every p0 multiplied by `scale`.

    p1 is left alone, so a lower scale means customers self-recover less and
    intervening matters more. p0 is clipped below p1 to preserve monotonicity --
    contacting someone must never reduce their chance of paying.
    """
    out = copy.deepcopy(cfg)
    for cls in DeclineClass:
        arm = getattr(out.recovery, cls.value)
        arm.p0 = float(np.clip(arm.p0 * scale, 0.0, min(arm.p1, 0.999)))
    return out


@dataclass
class PolicyOutcome:
    name: str
    contacts: int
    true_incremental_inr: float
    gross_claimed_inr: float
    persuadable_share: float
    wasted_share: float


@dataclass
class SweepPoint:
    scale: float
    mean_p0: float
    mean_true_uplift: float
    baseline: PolicyOutcome
    antar: PolicyOutcome

    @property
    def advantage(self) -> float:
        """How many rupees ANTAR causes per rupee the baseline causes."""
        b = self.baseline.true_incremental_inr
        return self.antar.true_incremental_inr / b if b > 1e-9 else float("inf")


def _evaluate(
    name: str, chosen: list[str], data: ExperimentData, truth: TruthBook
) -> PolicyOutcome:
    amounts = data.amounts()
    counts = truth.stratum_counts(chosen)
    n = max(len(chosen), 1)
    return PolicyOutcome(
        name=name,
        contacts=len(chosen),
        true_incremental_inr=truth.true_incremental_value(chosen, amounts),
        gross_claimed_inr=truth.gross_claimed_value(chosen, amounts),
        persuadable_share=counts["persuadable"] / n,
        wasted_share=(counts["sure_thing"] + counts["lost_cause"]) / n,
    )


def compare_policies(
    cfg: SimpleNamespace,
    *,
    train_fraction: float = 0.5,
    budget_fraction: float = 0.25,
) -> tuple[PolicyOutcome, PolicyOutcome, ExperimentData, UpliftTargeter, NaiveRecoveryBot]:
    """Fit both policies on the same data and score them on the same held-out window."""
    data = run_experiment(cfg)
    train, evaluate = data.split_by_time(train_fraction)

    train_ids = [r.txn_id for r in train]
    train_y = [data.outcomes[t] for t in train_ids]
    train_t = [t in data.assignment.treatment for t in train_ids]

    antar = UpliftTargeter().fit(train, train_y, train_t)

    # The baseline only ever sees contacted rows -- it has no control arm.
    treated_train = [r for r in train if r.txn_id in data.assignment.treatment]
    baseline = NaiveRecoveryBot().fit(
        treated_train, [data.outcomes[r.txn_id] for r in treated_train]
    )

    budget = int(len(evaluate) * budget_fraction)
    b_sel = baseline.select(evaluate, budget=budget)
    a_sel = antar.select(evaluate, budget=budget)

    return (
        _evaluate("naive_baseline", b_sel.chosen, data, data.truth),
        _evaluate("antar_uplift", a_sel.chosen, data, data.truth),
        data,
        antar,
        baseline,
    )


def sensitivity_sweep(
    cfg: SimpleNamespace,
    scales: list[float],
    *,
    budget_fraction: float = 0.25,
) -> list[SweepPoint]:
    points: list[SweepPoint] = []
    for scale in scales:
        scaled = scale_self_recovery(cfg, scale)
        b, a, data, _, _ = compare_policies(scaled, budget_fraction=budget_fraction)
        ids = [r.txn_id for r in data.records]
        points.append(
            SweepPoint(
                scale=scale,
                mean_p0=float(np.mean([data.truth[t].p0 for t in ids])),
                mean_true_uplift=float(np.mean([data.truth[t].uplift for t in ids])),
                baseline=b,
                antar=a,
            )
        )
    return points
