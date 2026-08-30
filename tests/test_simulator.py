"""Simulator invariants.

Two of these matter more than the rest:

*   `test_no_defiers` -- the monotonicity assumption the causal machinery rests
    on. If it ever fails, every uplift number downstream is meaningless.
*   `test_ledger_payload_carries_no_ground_truth` -- the leakage guard. The
    ledger must contain only what a real deployment could observe. If p0/p1/y0/y1
    ever reached it, the P&L would be quietly cheating and still look correct.
"""

from __future__ import annotations

import numpy as np
import pytest

from antar.config import load_config
from antar.simulator.engine import Simulator
from antar.taxonomy import CLASS_META, DeclineClass, classify

GROUND_TRUTH_FIELDS = {"p0", "p1", "y0", "y1", "true_uplift", "stratum",
                       "_reliability", "_responsiveness"}


@pytest.fixture(scope="module")
def small_run():
    cfg = load_config()
    cfg.population.n_customers = 400
    cfg.window.days = 5
    cfg.window.attempts_per_day = 900
    return Simulator(cfg).run()


# ------------------------------------------------------ causal invariants

def test_no_defiers(small_run):
    """y1 >= y0 for every transaction: contacting someone never stops them paying."""
    violations = [e.txn_id for e in small_run.events if e.y1 < e.y0]
    assert not violations, f"{len(violations)} defiers, e.g. {violations[:3]}"


def test_treated_probability_dominates(small_run):
    assert all(e.p1 >= e.p0 for e in small_run.events)


def test_probabilities_are_valid(small_run):
    assert all(0.0 <= e.p0 <= 1.0 and 0.0 <= e.p1 <= 1.0 for e in small_run.events)


def test_strata_are_exhaustive(small_run):
    strata = {e.stratum for e in small_run.events}
    assert strata <= {"sure_thing", "persuadable", "lost_cause"}
    assert "persuadable" in strata, "no persuadables generated -- nothing to learn"


# ------------------------------------------------------- the thesis holds

def test_class_a_has_near_zero_uplift(small_run):
    a = [e.true_uplift for e in small_run.events if e.decline_class is DeclineClass.A_TRANSIENT_RAIL]
    assert a and np.mean(a) < 0.05


def test_dead_instrument_outranks_transient_rail_on_uplift(small_run):
    """The inversion the whole project argues about, asserted as a test."""
    by_class = {}
    for cls in DeclineClass:
        vals = [e.true_uplift for e in small_run.events if e.decline_class is cls]
        if vals:
            by_class[cls] = float(np.mean(vals))

    assert by_class[DeclineClass.D_DEAD_INSTRUMENT] > by_class[DeclineClass.A_TRANSIENT_RAIL]

    # And on raw treated success rate the ordering flips.
    p1_a = np.mean([e.p1 for e in small_run.events if e.decline_class is DeclineClass.A_TRANSIENT_RAIL])
    p1_d = np.mean([e.p1 for e in small_run.events if e.decline_class is DeclineClass.D_DEAD_INSTRUMENT])
    assert p1_a > p1_d


def test_dead_instruments_almost_never_self_recover(small_run):
    d = [e.y0 for e in small_run.events if e.decline_class is DeclineClass.D_DEAD_INSTRUMENT]
    assert d and np.mean(d) < 0.05


# --------------------------------------------------------- leakage guard

def test_ledger_payload_carries_no_ground_truth(small_run):
    for ev in small_run.events[:500]:
        payload = ev.to_ledger_payload()
        leaked = GROUND_TRUTH_FIELDS & set(payload)
        assert not leaked, f"ground truth leaked into ledger: {leaked}"


def test_observable_features_carry_no_latent_traits(small_run):
    for cust in list(small_run.customers.values())[:200]:
        leaked = GROUND_TRUTH_FIELDS & set(cust.observable)
        assert not leaked, f"latent trait exposed as a feature: {leaked}"


def test_observable_proxy_correlates_with_latent_reliability(small_run):
    """There must be learnable signal, or day 6 proves nothing either way."""
    custs = [c for c in small_run.customers.values() if c.prior_failures >= 3]
    assert len(custs) > 30
    observed = [c.prior_self_recoveries / c.prior_failures for c in custs]
    latent = [c._reliability for c in custs]
    assert np.corrcoef(observed, latent)[0, 1] > 0.4


# ------------------------------------------------------------ mechanics

def test_determinism():
    cfg = load_config()
    cfg.population.n_customers = 200
    cfg.window.days = 3
    cfg.window.attempts_per_day = 500
    a, b = Simulator(cfg).run(), Simulator(cfg).run()
    assert [e.txn_id for e in a.events] == [e.txn_id for e in b.events]
    assert [e.p1 for e in a.events] == [e.p1 for e in b.events]


def test_every_reason_code_classifies(small_run):
    for ev in small_run.events:
        assert classify(ev.reason_code) is ev.decline_class


def test_unknown_reason_code_fails_loudly():
    with pytest.raises(KeyError):
        classify("something_we_never_mapped")


def test_outages_concentrate_class_a(small_run):
    during = [e for e in small_run.events if e.in_outage]
    if not during:
        pytest.skip("no outage-window failures in this small run")
    share_a = np.mean([e.decline_class is DeclineClass.A_TRANSIENT_RAIL for e in during])
    assert share_a > 0.8, "outages must produce correlated Class A bursts for Triage to find"


def test_class_a_is_not_contactable():
    assert not CLASS_META[DeclineClass.A_TRANSIENT_RAIL].contactable
    assert CLASS_META[DeclineClass.D_DEAD_INSTRUMENT].contactable
