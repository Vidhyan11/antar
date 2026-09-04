"""Always-valid confidence sequences.

ANTAR watches its own effect continuously and pauses an arm the moment the
evidence says it is not helping. That is a *stopping rule*, and stopping rules
break fixed-horizon statistics: a 95% t-interval is only 95% if you look once,
at a sample size fixed in advance. Look after every batch and stop when it
excludes zero, and you will find an effect in a large share of experiments where
none exists.

A confidence sequence is an interval that is valid at *every* sample size
simultaneously:

    P( exists n : mu not in CI_n )  <=  alpha

So you may peek continuously, stop whenever you like, and the guarantee holds.
That is the property that makes an auto-pausing agent statistically honest
rather than a p-hacking machine with good intentions.

Implementation: the normal-mixture boundary of Howard, Ramdas, McAuliffe and
Sekhon (2021), *Time-uniform, nonasymptotic, nonparametric confidence
sequences*. For observations bounded in [0, B] the sub-Gaussian parameter is
sigma = B/2 (Hoeffding's lemma).

One property worth knowing: **validity holds for any rho > 0.** The tuning
parameter only decides *where* the boundary is tightest, so a badly chosen rho
costs power, never correctness.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Interval:
    point: float
    lower: float
    upper: float
    n: int
    method: str

    @property
    def radius(self) -> float:
        return (self.upper - self.lower) / 2.0

    def excludes_zero(self) -> bool:
        return self.lower > 0.0 or self.upper < 0.0

    def __str__(self) -> str:
        return f"{self.point:+.4f}  [{self.lower:+.4f}, {self.upper:+.4f}]  (n={self.n:,}, {self.method})"


def default_rho(sigma: float, n_target: int) -> float:
    """Tune the mixture so the boundary is tightest near `n_target`.

    Any positive value is valid; this one just puts the sweet spot where we
    expect to be looking.
    """
    return max(sigma**2 * n_target / 4.0, 1e-9)


def mixture_radius(n: int, sigma: float, alpha: float, rho: float) -> float:
    """Half-width of the always-valid interval for a mean after n observations."""
    if n <= 0:
        return math.inf
    v = n * sigma**2
    inner = math.sqrt((v + rho) / rho) / alpha
    return math.sqrt(2.0 * (v + rho) * math.log(inner)) / n


def always_valid_mean(
    values: Sequence[float],
    *,
    alpha: float = 0.05,
    bound: float = 1.0,
    n_target: int = 5000,
) -> Interval:
    """Confidence sequence for the mean of observations bounded in [0, bound]."""
    arr = np.asarray(values, dtype=float)
    n = arr.size
    sigma = bound / 2.0
    rho = default_rho(sigma, n_target)
    mean = float(arr.mean()) if n else 0.0
    r = mixture_radius(n, sigma, alpha, rho)
    return Interval(mean, mean - r, mean + r, n, "always-valid")


def fixed_horizon_mean(values: Sequence[float], *, alpha: float = 0.05) -> Interval:
    """The ordinary interval, for comparison only.

    Valid if and only if you look exactly once, at a sample size chosen before
    seeing any data. ANTAR does not do that, which is why this is here as a
    foil rather than as the number we report.
    """
    arr = np.asarray(values, dtype=float)
    n = arr.size
    mean = float(arr.mean()) if n else 0.0
    if n < 2:
        return Interval(mean, -math.inf, math.inf, n, "fixed-horizon")
    z = 1.959963984540054 if abs(alpha - 0.05) < 1e-12 else _z_for(alpha)
    se = float(arr.std(ddof=1)) / math.sqrt(n)
    return Interval(mean, mean - z * se, mean + z * se, n, "fixed-horizon")


def _z_for(alpha: float) -> float:
    from scipy.stats import norm

    return float(norm.ppf(1.0 - alpha / 2.0))


def always_valid_difference(
    treated: Sequence[float],
    control: Sequence[float],
    *,
    alpha: float = 0.05,
    bound: float = 1.0,
    n_target: int = 5000,
    holdout_fraction: float | None = None,
) -> Interval:
    """Confidence sequence for the difference of two arm means.

    Each arm gets a sequence at alpha/2 and the radii are added. That union
    bound is conservative -- a joint construction would be tighter -- but it is
    unambiguously valid, and being able to say exactly why an interval is
    correct matters more here than being able to say it is narrow.

    **Tune each arm separately.** The arms are wildly different sizes -- a 10%
    holdout means the control arm has a ninth of the data -- and a single rho
    tuned on the total leaves the boundary loose precisely where the data is
    thinnest, which is the control arm that every claim depends on. Pass
    `holdout_fraction` and each arm's mixture is tuned to the size that arm is
    *designed* to reach. That split is known from the experiment design before
    any data arrives, so nothing here is chosen after seeing outcomes.
    """
    t = np.asarray(treated, dtype=float)
    c = np.asarray(control, dtype=float)
    sigma = bound / 2.0

    if holdout_fraction is None:
        n_control_target = n_target // 2
        n_treated_target = n_target - n_control_target
    else:
        n_control_target = max(int(n_target * holdout_fraction), 1)
        n_treated_target = max(n_target - n_control_target, 1)

    diff = (float(t.mean()) if t.size else 0.0) - (float(c.mean()) if c.size else 0.0)
    r = mixture_radius(t.size, sigma, alpha / 2, default_rho(sigma, n_treated_target)) + \
        mixture_radius(c.size, sigma, alpha / 2, default_rho(sigma, n_control_target))
    return Interval(diff, diff - r, diff + r, t.size + c.size, "always-valid")


def fixed_horizon_difference(
    treated: Sequence[float], control: Sequence[float], *, alpha: float = 0.05
) -> Interval:
    t = np.asarray(treated, dtype=float)
    c = np.asarray(control, dtype=float)
    n = t.size + c.size
    diff = (float(t.mean()) if t.size else 0.0) - (float(c.mean()) if c.size else 0.0)
    if t.size < 2 or c.size < 2:
        return Interval(diff, -math.inf, math.inf, n, "fixed-horizon")
    z = 1.959963984540054 if abs(alpha - 0.05) < 1e-12 else _z_for(alpha)
    se = math.sqrt(t.var(ddof=1) / t.size + c.var(ddof=1) / c.size)
    return Interval(diff, diff - z * se, diff + z * se, n, "fixed-horizon")
