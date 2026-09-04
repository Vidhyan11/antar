"""The baseline bot.

Two jobs for these tests. First, confirm the bot actually works -- a broken
control condition proves nothing. Second, pin its *failure mode* as an assertion:
it must demonstrably over-target the zero-uplift class. If a future change makes
the baseline accidentally smart, the comparison stops meaning anything and these
tests should be what tells us.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest

from antar.config import load_config
from antar.evaluation import TruthBook
from antar.features import amounts_by_txn
from antar.policies.baseline import NaiveRecoveryBot
from antar.sensorium import Sensorium
from antar.simulator.engine import Simulator
from antar.taxonomy import DeclineClass


@pytest.fixture(scope="module")
def fitted():
    cfg = load_config()
    cfg.population.n_customers = 1200
    cfg.window.days = 12
    cfg.window.attempts_per_day = 1400
    result = Simulator(cfg).run()

    directory = {cid: c.observable for cid, c in result.customers.items()}
    records = Sensorium(customer_directory=directory).observe_many(
        ev.to_raw_event() for ev in result.events
    )

    cutoff = (result.start + timedelta(days=cfg.window.days * 0.4)).isoformat()
    warmup = [r for r in records if r.ts < cutoff]
    live = [r for r in records if r.ts >= cutoff]

    truth = TruthBook(result.events)
    warm_ids = [r.txn_id for r in warmup]
    outcomes = truth.realise(warm_ids, treated=set(warm_ids))

    bot = NaiveRecoveryBot().fit(warmup, [outcomes[t] for t in warm_ids])
    return bot, live, truth


# ------------------------------------------------------------- mechanics

def test_predict_before_fit_raises():
    with pytest.raises(RuntimeError):
        NaiveRecoveryBot().predict_recovery_prob([])


def test_fit_rejects_mismatched_lengths(fitted):
    _, live, _ = fitted
    with pytest.raises(ValueError):
        NaiveRecoveryBot().fit(live[:10], [1, 0])


def test_fit_rejects_single_class(fitted):
    _, live, _ = fitted
    with pytest.raises(ValueError):
        NaiveRecoveryBot().fit(live[:20], [1] * 20)


def test_probabilities_are_valid(fitted):
    bot, live, _ = fitted
    probs = bot.predict_recovery_prob(live[:400])
    assert probs.shape == (400,)
    assert np.all((probs >= 0.0) & (probs <= 1.0))


def test_budget_is_respected(fitted):
    bot, live, _ = fitted
    sel = bot.select(live, budget=250)
    assert sel.n_contacted == 250
    assert len(set(sel.chosen)) == 250


def test_unbounded_budget_contacts_everyone(fitted):
    bot, live, _ = fitted
    assert bot.select(live).n_contacted == len(live)


# --------------------------------------------- the bot is genuinely good

def test_model_ranks_recovery_probability_well(fitted):
    """It must actually be good at its own objective, or the comparison is unfair."""
    bot, live, truth = fitted
    probs = bot.predict_recovery_prob(live)
    actual_p1 = np.array([truth[r.txn_id].p1 for r in live])
    assert np.corrcoef(probs, actual_p1)[0, 1] > 0.7


# -------------------------------------------------- ...and still wrong

def test_it_over_targets_the_zero_uplift_class(fitted):
    """The failure mode, pinned. Class A gets a far larger share of the budget
    than Class D, despite being worth roughly nothing."""
    bot, live, _ = fitted
    chosen = set(bot.select(live, budget=int(len(live) * 0.25)).chosen)

    def targeted_share(cls: DeclineClass) -> float:
        pool = [r for r in live if r.decline_class is cls]
        return len([r for r in pool if r.txn_id in chosen]) / len(pool)

    share_a = targeted_share(DeclineClass.A_TRANSIENT_RAIL)
    share_d = targeted_share(DeclineClass.D_DEAD_INSTRUMENT)
    assert share_a > share_d, "baseline should chase success rate into class A"


def test_most_of_its_contacts_change_nothing(fitted):
    bot, live, truth = fitted
    chosen = bot.select(live, budget=int(len(live) * 0.25)).chosen
    counts = truth.stratum_counts(chosen)
    wasted = (counts["sure_thing"] + counts["lost_cause"]) / len(chosen)
    assert wasted > 0.7


def test_it_claims_far_more_than_it_causes(fitted):
    bot, live, truth = fitted
    chosen = bot.select(live, budget=int(len(live) * 0.25)).chosen
    amounts = amounts_by_txn(live)
    claimed = truth.gross_claimed_value(chosen, amounts)
    caused = truth.true_incremental_value(chosen, amounts)
    assert claimed > caused * 2, "last-touch attribution should inflate substantially"
