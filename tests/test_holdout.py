"""Arm assignment invariants.

The control group is the product, so its assignment mechanism gets the same
scrutiny as the ledger: reproducible, unbiased, and independently recomputable
by anyone holding the salt.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from antar.holdout import Arm, assign, assign_arm, balance_report, uniform_hash


def test_assignment_is_deterministic():
    ids = [f"pay_{i:06d}" for i in range(500)]
    assert assign(ids, "salt", 0.1).arms == assign(ids, "salt", 0.1).arms


def test_a_different_salt_reshuffles_the_arms():
    ids = [f"pay_{i:06d}" for i in range(2000)]
    a = assign(ids, "salt-a", 0.1)
    b = assign(ids, "salt-b", 0.1)
    assert a.arms != b.arms


def test_assignment_is_recomputable_from_id_and_salt():
    ids = [f"pay_{i:06d}" for i in range(300)]
    assert assign(ids, "audit", 0.15).verify()


def test_a_tampered_assignment_fails_verification():
    ids = [f"pay_{i:06d}" for i in range(300)]
    a = assign(ids, "audit", 0.15)
    victim = next(t for t, arm in a.arms.items() if arm is Arm.CONTROL)
    a.arms[victim] = Arm.TREATMENT  # someone moves an inconvenient transaction
    assert not a.verify()


@given(fraction=st.floats(min_value=0.02, max_value=0.5))
@settings(max_examples=25, deadline=None)
def test_realised_share_tracks_the_nominal_rate(fraction):
    ids = [f"pay_{i:07d}" for i in range(20_000)]
    realised = assign(ids, "salt", fraction).realised_holdout
    assert abs(realised - fraction) < 0.015


def test_hash_is_uniform_on_the_unit_interval():
    draws = np.array([uniform_hash(f"pay_{i:07d}", "salt") for i in range(20_000)])
    assert 0.0 <= draws.min() and draws.max() < 1.0
    assert abs(draws.mean() - 0.5) < 0.01
    # Ten equal buckets should each hold roughly a tenth of the draws.
    counts = np.histogram(draws, bins=10, range=(0.0, 1.0))[0]
    assert counts.min() > 0.9 * 2000 and counts.max() < 1.1 * 2000


def test_invalid_fractions_are_rejected():
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            assign_arm("pay_1", "salt", bad)


def test_arms_partition_the_population():
    ids = [f"pay_{i:06d}" for i in range(1000)]
    a = assign(ids, "salt", 0.2)
    assert a.treatment | a.control == set(ids)
    assert not (a.treatment & a.control)


def test_balance_report_covers_every_stratum():
    ids = [f"pay_{i:06d}" for i in range(3000)]
    a = assign(ids, "salt", 0.1)
    strata = {t: f"class_{i % 4}" for i, t in enumerate(ids)}
    report = balance_report(a, strata)
    assert set(report) == {f"class_{i}" for i in range(4)}
    assert sum(t + c for t, c, _ in report.values()) == 3000
    for _, _, share in report.values():
        assert 0.05 < share < 0.16


def test_assignment_is_independent_of_the_outcome_it_will_produce():
    """Sanity guard against a hash that accidentally correlates with the id's
    numeric part -- which would make the arms non-exchangeable."""
    ids = [f"pay_{i:06d}" for i in range(20_000)]
    a = assign(ids, "salt", 0.1)
    idx = np.array([int(t.split("_")[1]) for t in ids])
    is_control = np.array([a.arms[t] is Arm.CONTROL for t in ids], dtype=float)
    assert abs(np.corrcoef(idx, is_control)[0, 1]) < 0.03
