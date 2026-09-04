"""Compliance, stopping rules, and the P&L.

The compliance tests matter because a rule that can be bypassed is a comment.
The P&L tests matter because every line in it is a subtraction, and a
subtraction that silently returns zero would make the whole statement flattering
and wrong.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from antar.actuator import ACTION_BY_CLASS, Actuator, RazorpayClient
from antar.compliance import IST_OFFSET, ArmMonitor, ComplianceLinter, ContactHistory
from antar.config import load_config
from antar.economics import build_pnl
from antar.evaluation import TruthBook
from antar.models import FailureEvent
from antar.sensorium import FailureRecord
from antar.taxonomy import CLASS_META, DeclineClass

# 14:00 IST -- comfortably inside allowed hours.
NOON_IST = datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)


def rec(txn="p1", ts=NOON_IST, cls=DeclineClass.D_DEAD_INSTRUMENT,
        consent=True, amount=50_000, customer="c1") -> FailureRecord:
    return FailureRecord(
        txn_id=txn, customer_id=customer, ts=ts.isoformat(), amount_paise=amount,
        issuer="HDFC", method="UPI", reason_code="card_expired",
        decline_class=cls, contactable=CLASS_META[cls].contactable,
        customer={"has_consent": consent, "tenure_days": 100, "inflow_day": 1,
                  "prior_txns": 10, "prior_failures": 2, "prior_self_recoveries": 1,
                  "self_recovery_rate": 0.5},
    )


@pytest.fixture(scope="module")
def cfg():
    return load_config()


# ------------------------------------------------------------- linter

def test_daytime_contact_is_allowed(cfg):
    assert ComplianceLinter(cfg).check(rec(), "instrument_update_link")


def test_quiet_hours_block_contact(cfg):
    # 23:00 IST
    night = datetime(2026, 9, 1, 17, 30, tzinfo=timezone.utc)
    v = ComplianceLinter(cfg).check(rec(ts=night), "instrument_update_link")
    assert not v and v.rule == "quiet_hours"


def test_quiet_hours_use_local_time_not_utc(cfg):
    """IST is UTC+5:30; a rule applied to UTC would ban the wrong half of the day."""
    linter = ComplianceLinter(cfg)
    utc_night_ist_morning = datetime(2026, 9, 1, 4, 0, tzinfo=timezone.utc)  # 09:30 IST
    assert (utc_night_ist_morning + IST_OFFSET).hour == 9
    assert linter.check(rec(ts=utc_night_ist_morning), "instrument_update_link")


def test_silent_actions_bypass_quiet_hours(cfg):
    """Nothing reaches the customer, so the rules protecting their attention
    do not apply. A retry at 2am is just a retry."""
    night = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)
    assert ComplianceLinter(cfg).check(rec(ts=night), "silent_retry")


def test_missing_consent_blocks_marketing_actions(cfg):
    v = ComplianceLinter(cfg).check(rec(consent=False), "reminder_with_link")
    assert not v and v.rule == "no_consent"


def test_service_actions_do_not_require_consent(cfg):
    assert ComplianceLinter(cfg).check(rec(consent=False), "retry_on_inflow_day")


def test_contact_cap_is_enforced(cfg):
    linter = ComplianceLinter(cfg)
    r = rec()
    for _ in range(cfg.compliance.max_contacts_per_window):
        assert linter.check(r, "instrument_update_link")
        linter.commit(r, "instrument_update_link")
    v = linter.check(r, "instrument_update_link")
    assert not v and v.rule == "contact_cap"


def test_cap_window_expires(cfg):
    linter = ComplianceLinter(cfg)
    old = NOON_IST - timedelta(days=cfg.compliance.contact_window_days + 1)
    for _ in range(5):
        linter.commit(rec(ts=old), "instrument_update_link")
    assert linter.check(rec(), "instrument_update_link")


def test_opt_out_is_absolute_even_for_silent_actions(cfg):
    linter = ComplianceLinter(cfg)
    linter.history.opt_out("c1")
    for action in ("instrument_update_link", "silent_retry"):
        v = linter.check(rec(), action)
        assert not v and v.rule == "opted_out"


def test_silent_actions_do_not_consume_the_cap(cfg):
    linter = ComplianceLinter(cfg)
    r = rec()
    for _ in range(10):
        linter.commit(r, "silent_retry")
    assert linter.check(r, "instrument_update_link")


def test_history_is_per_customer():
    h = ContactHistory(window_days=7, max_contacts=2)
    h.record("a", NOON_IST)
    h.record("a", NOON_IST)
    assert h.at_cap("a", NOON_IST)
    assert not h.at_cap("b", NOON_IST)


# --------------------------------------------------- the gate actually gates

def test_actuator_honours_the_linter(cfg):
    night = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)
    results = Actuator(RazorpayClient("", ""), linter=ComplianceLinter(cfg)).execute(
        [rec(ts=night, cls=DeclineClass.D_DEAD_INSTRUMENT)]
    )
    assert results[0].action == "blocked_by_compliance"
    assert results[0].rule == "quiet_hours"
    assert not results[0].executed


def test_actuator_without_a_linter_still_acts(cfg):
    results = Actuator(RazorpayClient("", "")).execute([rec()])
    assert results[0].action == ACTION_BY_CLASS[DeclineClass.D_DEAD_INSTRUMENT]


def test_cap_accumulates_across_a_batch(cfg):
    """The cap must count contacts made during this run, not just history."""
    linter = ComplianceLinter(cfg)
    batch = [rec(txn=f"p{i}", customer="same") for i in range(6)]
    results = Actuator(RazorpayClient("", ""), linter=linter).execute(batch)
    blocked = [r for r in results if r.action == "blocked_by_compliance"]
    assert len(blocked) == 6 - cfg.compliance.max_contacts_per_window


# ------------------------------------------------------- stopping rules

def _arm(effect: float, n: int = 4000, seed: int = 0):
    import numpy as np
    rng = np.random.default_rng(seed)
    control = (rng.random(n // 10) < 0.30).astype(float)
    treated = (rng.random(n) < 0.30 + effect).astype(float)
    return treated, control


def test_a_clear_effect_keeps_running():
    t, c = _arm(0.25)
    assert not ArmMonitor().assess("strong", t, c).paused


def test_a_null_arm_pauses_itself():
    t, c = _arm(0.0)
    state = ArmMonitor().assess("null", t, c)
    assert state.paused
    assert "indistinguishable" in state.reason


def test_a_thin_arm_waits_rather_than_concluding():
    t, c = _arm(0.2, n=200)
    state = ArmMonitor().assess("thin", t[:40], c[:20])
    assert not state.paused
    assert "gathering" in state.reason


def test_retention_harm_overrides_a_positive_effect():
    t, c = _arm(0.25)
    state = ArmMonitor().assess("harmful", t, c, harm_exceeds_value=True)
    assert state.paused
    assert "retention damage" in state.reason


# ------------------------------------------------------------------ P&L

def _truth_and_records(n=400, seed=0):
    import numpy as np
    rng = np.random.default_rng(seed)
    events, records = [], {}
    for i in range(n):
        txn = f"t{i}"
        y0, y1 = (0, 1) if i % 4 == 0 else (1, 1) if i % 4 == 1 else (0, 0)
        o1 = int(rng.random() < 0.02)
        events.append(FailureEvent(
            txn_id=txn, customer_id=f"c{i}", ts=NOON_IST, amount_paise=100_000,
            issuer="HDFC", method="UPI", reason_code="card_expired",
            decline_class=DeclineClass.D_DEAD_INSTRUMENT, in_outage=False,
            p0=float(y0), p1=float(y1), y0=y0, y1=y1,
            q0=0.002, q1=0.02, o0=0, o1=o1,
        ))
        records[txn] = rec(txn=txn, amount=100_000, customer=f"c{i}")
    return TruthBook(events), records


def test_pnl_subtracts_self_recovery(cfg):
    truth, records = _truth_and_records()
    ids = list(records)
    pnl = build_pnl(cfg, contacted=ids, eligible=ids,
                    actions={t: "instrument_update_link" for t in ids},
                    records=records, truth=truth)
    assert pnl.last_touch_claim > pnl.incremental_recovery
    assert pnl.self_recovery == pytest.approx(
        pnl.last_touch_claim - pnl.incremental_recovery
    )
    assert pnl.overstatement > 1.0


def test_pnl_charges_for_retention_damage(cfg):
    truth, records = _truth_and_records()
    ids = list(records)
    pnl = build_pnl(cfg, contacted=ids, eligible=ids,
                    actions={t: "instrument_update_link" for t in ids},
                    records=records, truth=truth)
    assert pnl.retention_damage > 0, "opt-outs caused by contact must cost something"
    assert pnl.optout_delta > 0
    assert pnl.net_value < pnl.incremental_margin


def test_pnl_channel_cost_tracks_the_action_taken(cfg):
    truth, records = _truth_and_records()
    ids = list(records)
    free = build_pnl(cfg, contacted=ids, eligible=ids,
                     actions={t: "silent_retry" for t in ids},
                     records=records, truth=truth)
    paid = build_pnl(cfg, contacted=ids, eligible=ids,
                     actions={t: "instrument_update_link" for t in ids},
                     records=records, truth=truth)
    assert free.channel_cost == 0.0
    assert paid.channel_cost > free.channel_cost


def test_contacting_nobody_creates_nothing_and_costs_nothing(cfg):
    truth, records = _truth_and_records()
    pnl = build_pnl(cfg, contacted=[], eligible=list(records),
                    actions={}, records=records, truth=truth)
    assert pnl.net_value == 0.0
    assert pnl.channel_cost == 0.0
    assert pnl.retention_damage == 0.0


def test_discounts_only_cost_on_recoveries_they_closed(cfg):
    """Offering 5% to someone who never comes back costs nothing."""
    truth, records = _truth_and_records()
    ids = list(records)
    pnl = build_pnl(cfg, contacted=ids, eligible=ids,
                    actions={t: "reminder_with_link" for t in ids},
                    records=records, truth=truth)
    persuadables = sum(1 for t in ids if truth[t].y1 > truth[t].y0)
    expected = persuadables * 1000.0 * cfg.economics.discount_rate.reminder_with_link
    assert pnl.discount_cost == pytest.approx(expected)
