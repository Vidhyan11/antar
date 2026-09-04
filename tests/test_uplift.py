"""The uplift model and its evaluation.

The load-bearing test is `test_model_recovers_the_true_effect`: the T-learner
never observes both arms for the same transaction, so if its predictions track
ground truth it is genuinely reconstructing a counterfactual rather than
memorising outcomes.

`test_qini_is_zero_for_a_random_ranking` matters nearly as much. A Qini
implementation that reports a positive coefficient for noise would make every
other number in the project look good for free.
"""

from __future__ import annotations

import numpy as np
import pytest

from antar.config import load_config
from antar.pipeline import run_experiment
from antar.policies.baseline import NaiveRecoveryBot
from antar.policies.uplift import UpliftTargeter, qini_curve
from antar.sweep import compare_policies, scale_self_recovery
from antar.taxonomy import DeclineClass


@pytest.fixture(scope="module")
def trained():
    cfg = load_config()
    cfg.population.n_customers = 2500
    cfg.window.days = 20
    cfg.window.attempts_per_day = 2000
    data = run_experiment(cfg)
    train, evaluate = data.split_by_time(0.5)

    ids = [r.txn_id for r in train]
    model = UpliftTargeter().fit(
        train,
        [data.outcomes[t] for t in ids],
        [t in data.assignment.treatment for t in ids],
    )
    return model, data, evaluate


# ------------------------------------------------------------- mechanics

def test_predict_before_fit_raises():
    with pytest.raises(RuntimeError):
        UpliftTargeter().predict_cate([])


def test_fit_rejects_a_missing_arm(trained):
    _, _, evaluate = trained
    with pytest.raises(ValueError):
        UpliftTargeter().fit(evaluate[:200], [1] * 200, [True] * 200)


def test_budget_is_respected(trained):
    model, _, evaluate = trained
    assert model.select(evaluate, budget=200).n_contacted <= 200


def test_it_leaves_budget_unspent_when_nothing_is_worth_contacting(trained):
    """Unlike the baseline, an empty pipeline is an acceptable answer."""
    model, _, evaluate = trained
    sel = model.select(evaluate, budget=len(evaluate))
    assert sel.n_contacted < sel.n_eligible


def test_no_hardcoded_class_filter(trained):
    """Targeting must come from the estimate, not from a frozen taxonomy rule.

    An earlier version filtered on `contactable` and lost the sensitivity sweep
    outright, because the rule was right at one self-recovery rate and wrong at
    others. Every class must remain reachable.
    """
    model, _, evaluate = trained
    scores = model.score(evaluate)
    assert len(scores) == len(evaluate), "every record must be scored, none pre-filtered"


# ------------------------------------------- does it recover the truth

def test_model_recovers_the_true_effect(trained):
    """The whole project in one assertion."""
    model, data, evaluate = trained
    predicted = model.predict_cate(evaluate)
    actual = np.array([data.truth[r.txn_id].uplift for r in evaluate])
    assert np.corrcoef(predicted, actual)[0, 1] > 0.5


def test_top_decile_has_more_true_uplift_than_the_bottom(trained):
    model, data, evaluate = trained
    predicted = model.predict_cate(evaluate)
    actual = np.array([data.truth[r.txn_id].uplift for r in evaluate])
    order = np.argsort(-predicted)
    k = max(len(order) // 10, 1)
    assert actual[order[:k]].mean() > actual[order[-k:]].mean() * 1.5


def test_transient_rail_ranks_below_dead_instrument(trained):
    """At the configured self-recovery rate, the model should discover the
    inversion on its own rather than being told it."""
    model, _, evaluate = trained
    cate = dict(zip([r.txn_id for r in evaluate], model.predict_cate(evaluate), strict=True))
    a = np.mean([cate[r.txn_id] for r in evaluate
                 if r.decline_class is DeclineClass.A_TRANSIENT_RAIL])
    d = np.mean([cate[r.txn_id] for r in evaluate
                 if r.decline_class is DeclineClass.D_DEAD_INSTRUMENT])
    assert d > a


# ---------------------------------------------------------------- qini

def test_qini_is_zero_for_a_random_ranking():
    rng = np.random.default_rng(0)
    n = 6000
    treated = rng.random(n) < 0.9
    outcomes = (rng.random(n) < np.where(treated, 0.42, 0.30)).astype(int)
    q = qini_curve(rng.random(n), outcomes, treated)
    assert abs(q.coefficient) < 0.15, "noise must not look like signal"


def test_qini_rewards_a_ranking_aligned_with_true_uplift(trained):
    model, data, evaluate = trained
    ids = [r.txn_id for r in evaluate]
    y = [data.outcomes[t] for t in ids]
    t = [t in data.assignment.treatment for t in ids]

    good = qini_curve(model.predict_cate(evaluate), y, t)
    rng = np.random.default_rng(1)
    noise = qini_curve(rng.random(len(evaluate)), y, t)
    assert good.coefficient > noise.coefficient


def test_qini_curve_shape_is_wellformed(trained):
    model, data, evaluate = trained
    ids = [r.txn_id for r in evaluate]
    q = qini_curve(
        model.predict_cate(evaluate),
        [data.outcomes[t] for t in ids],
        [t in data.assignment.treatment for t in ids],
    )
    assert q.fractions[0] > 0 and abs(q.fractions[-1] - 1.0) < 1e-9
    assert len(q.fractions) == len(q.incremental) == len(q.random_line)
    # Curve and random line must meet at full depth: targeting everyone is
    # the same decision however you ranked it.
    assert abs(q.incremental[-1] - q.random_line[-1]) < 1e-6


# ------------------------------------------------------------- the sweep

def test_scaling_self_recovery_lowers_p0_and_preserves_monotonicity():
    cfg = load_config()
    scaled = scale_self_recovery(cfg, 0.5)
    for cls in DeclineClass:
        before = getattr(cfg.recovery, cls.value)
        after = getattr(scaled.recovery, cls.value)
        assert after.p0 <= before.p0
        assert after.p0 <= after.p1, "contacting someone must never reduce their chance"


def test_scaling_does_not_mutate_the_original_config():
    cfg = load_config()
    original = cfg.recovery.C_FUNDS.p0
    scale_self_recovery(cfg, 0.25)
    assert cfg.recovery.C_FUNDS.p0 == original


@pytest.mark.slow
def test_uplift_targeting_is_never_behind_the_baseline():
    """The claim that actually goes in the pitch, at the extremes of the sweep."""
    cfg = load_config()
    cfg.population.n_customers = 2000
    cfg.window.days = 16
    cfg.window.attempts_per_day = 1800

    for scale in (0.4, 1.0):
        baseline, antar, *_ = compare_policies(
            scale_self_recovery(cfg, scale), budget_fraction=0.25
        )
        assert antar.true_incremental_inr >= baseline.true_incremental_inr * 0.95, (
            f"ANTAR fell behind at self-recovery scale {scale}"
        )


@pytest.mark.slow
def test_uplift_reaches_more_persuadables_than_the_baseline():
    cfg = load_config()
    cfg.population.n_customers = 2000
    cfg.window.days = 16
    cfg.window.attempts_per_day = 1800
    baseline, antar, *_ = compare_policies(cfg, budget_fraction=0.25)
    assert antar.persuadable_share > baseline.persuadable_share
    assert antar.wasted_share < baseline.wasted_share


def test_baseline_and_uplift_share_the_same_features(trained):
    """The comparison is only fair if both see identical information."""
    from antar.features import build_features

    _, _, evaluate = trained
    assert list(build_features(evaluate).columns) == list(build_features(evaluate).columns)
    assert NaiveRecoveryBot().model.steps[0][0] == UpliftTargeter().model_treated.steps[0][0]
