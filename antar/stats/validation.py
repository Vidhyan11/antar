"""Does the guarantee actually hold?

A confidence sequence is only worth using if its coverage claim survives
contact with a stopping rule. These helpers run the adversarial case directly:
many experiments where the true effect is *exactly zero*, monitored
continuously, stopping the moment an interval excludes zero.

Under that protocol a fixed-horizon interval is not a 95% interval at all -- it
is a machine for manufacturing effects out of noise. The confidence sequence
should hold at or below alpha. We measure both rather than asserting either.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from antar.stats.sequential import (
    always_valid_difference,
    default_rho,
    fixed_horizon_difference,  # noqa: F401  (re-exported for callers)
    mixture_radius,
)


@dataclass(frozen=True)
class PeekingResult:
    n_experiments: int
    peeks_per_experiment: int
    always_valid_fpr: float
    fixed_horizon_fpr: float
    alpha: float

    def __str__(self) -> str:
        return (
            f"{self.n_experiments} null experiments, peeked {self.peeks_per_experiment}x each\n"
            f"  fixed-horizon false positives : {self.fixed_horizon_fpr:6.1%}\n"
            f"  always-valid  false positives : {self.always_valid_fpr:6.1%}   (target <= {self.alpha:.0%})"
        )


def peeking_false_positive_rates(
    *,
    n_experiments: int = 400,
    n_per_arm: int = 3000,
    peek_every: int = 100,
    p: float = 0.35,
    alpha: float = 0.05,
    seed: int = 7,
) -> PeekingResult:
    """Run null experiments under continuous monitoring and count false alarms.

    Both arms draw from Bernoulli(p), so the true difference is zero and every
    rejection is a false positive by construction.
    """
    rng = np.random.default_rng(seed)
    peeks = list(range(peek_every, n_per_arm + 1, peek_every))
    sigma = 0.5
    rho = default_rho(sigma, n_per_arm)
    z = 1.959963984540054

    # Pre-compute the always-valid radius at each peek: it depends only on n.
    av_radius = {
        n: mixture_radius(n, sigma, alpha / 2, rho) * 2  # one radius per arm, summed
        for n in peeks
    }

    av_alarms = 0
    fh_alarms = 0

    for _ in range(n_experiments):
        a = rng.random(n_per_arm) < p
        b = rng.random(n_per_arm) < p
        cum_a = np.cumsum(a)
        cum_b = np.cumsum(b)

        av_hit = False
        fh_hit = False
        for n in peeks:
            diff = (cum_a[n - 1] - cum_b[n - 1]) / n

            if not av_hit and abs(diff) > av_radius[n]:
                av_hit = True

            if not fh_hit:
                # Fixed-horizon two-sample interval, recomputed at each peek --
                # exactly the mistake this exists to demonstrate.
                pa = cum_a[n - 1] / n
                pb = cum_b[n - 1] / n
                se = np.sqrt(pa * (1 - pa) / n + pb * (1 - pb) / n)
                if se > 0 and abs(diff) > z * se:
                    fh_hit = True

            if av_hit and fh_hit:
                break

        av_alarms += av_hit
        fh_alarms += fh_hit

    return PeekingResult(
        n_experiments=n_experiments,
        peeks_per_experiment=len(peeks),
        always_valid_fpr=av_alarms / n_experiments,
        fixed_horizon_fpr=fh_alarms / n_experiments,
        alpha=alpha,
    )


def coverage_rate(
    *,
    n_experiments: int = 300,
    n_per_arm: int = 2000,
    p_control: float = 0.30,
    true_effect: float = 0.08,
    alpha: float = 0.05,
    seed: int = 11,
) -> float:
    """Share of experiments whose final always-valid interval covers the truth."""
    rng = np.random.default_rng(seed)
    covered = 0
    for _ in range(n_experiments):
        control = (rng.random(n_per_arm) < p_control).astype(float)
        treated = (rng.random(n_per_arm) < p_control + true_effect).astype(float)
        ci = always_valid_difference(treated, control, alpha=alpha, n_target=n_per_arm)
        covered += ci.lower <= true_effect <= ci.upper
    return covered / n_experiments
