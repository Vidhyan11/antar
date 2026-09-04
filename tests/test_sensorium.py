"""Sensorium and truth-book invariants.

The leakage tests here are the ones that matter. If ground truth can reach a
policy, every number the project produces downstream is worthless *and still
looks correct*, which is the worst kind of bug to have.
"""

from __future__ import annotations

import pytest

from antar.config import load_config
from antar.evaluation import TruthBook
from antar.features import FEATURE_COLUMNS, build_features
from antar.ledger import Ledger
from antar.models import RawGatewayEvent
from antar.sensorium import Sensorium, UnknownCustomer
from antar.simulator.engine import Simulator
from antar.taxonomy import DeclineClass

GROUND_TRUTH_FIELDS = {"p0", "p1", "y0", "y1", "true_uplift", "stratum",
                       "_reliability", "_responsiveness"}


@pytest.fixture(scope="module")
def run():
    cfg = load_config()
    cfg.population.n_customers = 400
    cfg.window.days = 6
    cfg.window.attempts_per_day = 900
    return Simulator(cfg).run()


@pytest.fixture(scope="module")
def records(run):
    directory = {cid: c.observable for cid, c in run.customers.items()}
    sens = Sensorium(customer_directory=directory)
    return sens.observe_many(ev.to_raw_event() for ev in run.events)


# ------------------------------------------------------------- the airlock

def test_raw_event_has_no_ground_truth(run):
    for ev in run.events[:300]:
        leaked = GROUND_TRUTH_FIELDS & set(vars(ev.to_raw_event()))
        assert not leaked, f"ground truth crossed the airlock: {leaked}"


def test_failure_record_has_no_ground_truth(records):
    for rec in records[:300]:
        leaked = GROUND_TRUTH_FIELDS & set(vars(rec))
        assert not leaked
        assert not (GROUND_TRUTH_FIELDS & set(rec.customer))


def test_features_contain_no_ground_truth(records):
    frame = build_features(records[:200])
    assert not (GROUND_TRUTH_FIELDS & set(frame.columns))
    assert list(frame.columns) == list(FEATURE_COLUMNS)
    assert not frame.isna().any().any()


def test_policies_never_import_the_truth_book():
    """Architectural guard: no module under antar.policies may import the
    truth book. Parsed from the AST rather than grepped, so a docstring that
    merely mentions the rule doesn't trip it."""
    import ast
    from pathlib import Path

    import antar.policies as pkg

    for path in Path(pkg.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not any(m.startswith("antar.evaluation") for m in imported), (
            f"{path.name} imports the truth book"
        )


# ---------------------------------------------------------------- intake

def test_sensorium_classifies_consistently(run, records):
    by_id = {ev.txn_id: ev for ev in run.events}
    for rec in records[:300]:
        assert rec.decline_class is by_id[rec.txn_id].decline_class


def test_sensorium_marks_contactability(records):
    for rec in records[:200]:
        if rec.decline_class is DeclineClass.A_TRANSIENT_RAIL:
            assert not rec.contactable
        if rec.decline_class is DeclineClass.D_DEAD_INSTRUMENT:
            assert rec.contactable


def test_sensorium_writes_a_verifiable_chain(tmp_path, run):
    directory = {cid: c.observable for cid, c in run.customers.items()}
    with Ledger(tmp_path / "s.db", fresh=True) as led:
        sens = Sensorium(led, directory)
        sens.observe_many(ev.to_raw_event() for ev in run.events[:200])
        assert len(led) == 200
        assert led.verify().ok


def test_unknown_customer_raises_in_strict_mode():
    sens = Sensorium(customer_directory={})
    raw = RawGatewayEvent("t1", "ghost", "2026-09-01T10:00:00+00:00",
                          50000, "HDFC", "UPI", "card_expired")
    with pytest.raises(UnknownCustomer):
        sens.observe(raw)


def test_unknown_customer_tolerated_when_not_strict():
    sens = Sensorium(customer_directory={}, strict_customers=False)
    raw = RawGatewayEvent("t1", "ghost", "2026-09-01T10:00:00+00:00",
                          50000, "HDFC", "UPI", "card_expired")
    rec = sens.observe(raw)
    assert rec.customer == {}
    assert "ghost" in sens.unknown_customers


def test_unmapped_reason_code_fails_loudly():
    sens = Sensorium(customer_directory={}, strict_customers=False)
    raw = RawGatewayEvent("t1", "c", "2026-09-01T10:00:00+00:00",
                          50000, "HDFC", "UPI", "brand_new_code")
    with pytest.raises(KeyError):
        sens.observe(raw)


# ------------------------------------------------------------ truth book

def test_gross_exceeds_incremental(run):
    truth = TruthBook(run.events)
    amounts = {ev.txn_id: ev.amount_paise for ev in run.events}
    ids = [ev.txn_id for ev in run.events]
    gross = truth.gross_claimed_value(ids, amounts)
    incr = truth.true_incremental_value(ids, amounts)
    assert gross > incr > 0, "sure-things must inflate the gross figure"


def test_realise_returns_the_arm_you_asked_for(run):
    truth = TruthBook(run.events)
    ids = [ev.txn_id for ev in run.events[:100]]
    treated = truth.realise(ids, treated=set(ids))
    control = truth.realise(ids, treated=set())
    assert all(treated[t] >= control[t] for t in ids)


def test_incremental_value_is_zero_when_nobody_is_treated(run):
    truth = TruthBook(run.events)
    amounts = {ev.txn_id: ev.amount_paise for ev in run.events}
    assert truth.true_incremental_value([], amounts) == 0.0


def test_strata_partition_the_population(run):
    truth = TruthBook(run.events)
    ids = [ev.txn_id for ev in run.events]
    counts = truth.stratum_counts(ids)
    assert sum(counts.values()) == len(ids)
