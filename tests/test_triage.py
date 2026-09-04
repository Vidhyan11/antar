"""Triage, freeze, and actuation.

The detector tests encode two lessons that cost real debugging time and would
silently come back if anyone loosened the code:

*   seasonality must be handled, or a busy evening reads as an incident;
*   an incident on a rail with no traffic is not detectable by anyone, so the
    tests assert behaviour above the floor and do not pretend otherwise.

The actuator tests exist to prove the gate actually gates. A freeze that can be
bypassed is not a safety property, it is a comment.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from antar.actuator import ACTION_BY_CLASS, MAX_ACTION_PAISE, Actuator, RazorpayClient
from antar.ledger import Ledger
from antar.llm.provider import CachingProvider, DeterministicFallback
from antar.sensorium import FailureRecord
from antar.taxonomy import CLASS_META, DeclineClass
from antar.triage.agent import FAIL_CLOSED, FreezeRegistry, Incident, TriageAgent, _validate
from antar.triage.detector import detect_clusters, merge_adjacent

BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def rec(txn: str, ts: datetime, issuer="HDFC", method="UPI",
        cls=DeclineClass.A_TRANSIENT_RAIL, amount=50_000) -> FailureRecord:
    return FailureRecord(
        txn_id=txn, customer_id=f"c_{txn}", ts=ts.isoformat(), amount_paise=amount,
        issuer=issuer, method=method, reason_code="gateway_timeout",
        decline_class=cls, contactable=CLASS_META[cls].contactable,
        customer={"tenure_days": 100, "inflow_day": 1, "has_consent": True,
                  "prior_txns": 10, "prior_failures": 2, "prior_self_recoveries": 1,
                  "self_recovery_rate": 0.5},
    )


def steady_stream(per_hour: int, days: int, hour: int = 12) -> list[FailureRecord]:
    """A quiet, seasonal baseline: the same hour, the same load, every day."""
    out = []
    n = 0
    for d in range(days):
        for _ in range(per_hour):
            out.append(rec(f"q{n}", BASE + timedelta(days=d, hours=hour, minutes=n % 60)))
            n += 1
    return out


# ---------------------------------------------------------------- detector

def test_quiet_traffic_raises_nothing():
    assert detect_clusters(steady_stream(6, 12)) == []


def test_a_burst_is_detected():
    records = steady_stream(4, 10)
    spike_day = BASE + timedelta(days=10, hours=12)
    records += [rec(f"s{i}", spike_day + timedelta(minutes=i % 60)) for i in range(30)]
    clusters = detect_clusters(records)
    assert len(clusters) == 1
    assert clusters[0].observed == 30
    assert clusters[0].lift > 5


def test_diurnal_traffic_does_not_read_as_an_incident():
    """The bug that produced nineteen episodes for four outages.

    A busy hour is only suspicious compared with the *same* hour on other days.
    """
    records: list[FailureRecord] = []
    n = 0
    for d in range(12):
        for hour, load in ((3, 1), (12, 8), (20, 26)):  # 26x swing, as in the simulator
            for _ in range(load):
                records.append(rec(f"d{n}", BASE + timedelta(days=d, hours=hour,
                                                             minutes=n % 60)))
                n += 1
    assert detect_clusters(records) == [], "diurnal peaks must not be flagged"


def test_tiny_counts_do_not_breach():
    """A Poisson tail is easy to trip at low counts; the floor stops it."""
    records = steady_stream(1, 12)
    spike = BASE + timedelta(days=12, hours=12)
    records += [rec(f"t{i}", spike + timedelta(minutes=i)) for i in range(5)]
    assert detect_clusters(records) == []


def test_history_is_required_before_calling_anything():
    records = steady_stream(4, 2)
    spike = BASE + timedelta(days=2, hours=12)
    records += [rec(f"h{i}", spike + timedelta(minutes=i)) for i in range(30)]
    assert detect_clusters(records) == [], "cold start must not produce verdicts"


def test_rails_are_assessed_independently():
    """A burst on one rail must not implicate a quiet neighbour.

    Both rails get their own history first: a rail with no baseline is a cold
    start, and refusing to judge it is the correct behaviour, not independence.
    """
    records = steady_stream(5, 10)
    for i, r in enumerate(steady_stream(5, 10)):
        records.append(rec(f"sbi{i}", datetime.fromisoformat(r.ts), issuer="SBI"))

    spike = BASE + timedelta(days=10, hours=12)
    records += [rec(f"x{i}", spike + timedelta(minutes=i % 60), issuer="SBI")
                for i in range(30)]

    clusters = detect_clusters(records)
    assert [c.issuer for c in clusters] == ["SBI"]


def test_consecutive_hours_merge_into_one_episode():
    records = steady_stream(4, 10, hour=12) + steady_stream(4, 10, hour=13)
    for h in (12, 13):
        start = BASE + timedelta(days=10, hours=h)
        records += [rec(f"m{h}_{i}", start + timedelta(minutes=i % 60)) for i in range(30)]
    episodes = merge_adjacent(detect_clusters(records))
    assert len(episodes) == 1
    assert len(episodes[0]) == 2


# ------------------------------------------------------------------ agent

def test_validate_rejects_an_unknown_action():
    assert _validate({"is_systemic": True, "confidence": 0.9, "hypothesis": "x",
                      "recommended_action": "delete_everything", "note": "n"}) is None


def test_validate_rejects_an_out_of_range_confidence():
    assert _validate({"is_systemic": True, "confidence": 4.0, "hypothesis": "x",
                      "recommended_action": "freeze_and_wait", "note": "n"}) is None


def test_validate_rejects_missing_fields():
    assert _validate({"is_systemic": True}) is None


def test_fail_closed_releases_rather_than_freezes():
    """On unusable output, decline the drastic action rather than take it blind."""
    assert FAIL_CLOSED["recommended_action"] == "release_to_targeting"
    assert FAIL_CLOSED["is_systemic"] is False


def test_a_malformed_verdict_never_freezes(tmp_path):
    class Garbage:
        name = "garbage"

        def complete_json(self, prompt, schema_name, context=None):
            return {"is_systemic": True, "recommended_action": "freeze_and_reroute"}

    records = steady_stream(4, 10)
    spike = BASE + timedelta(days=10, hours=12)
    records += [rec(f"g{i}", spike + timedelta(minutes=i % 60)) for i in range(30)]
    provider = CachingProvider(Garbage(), fixture_dir=tmp_path, record=False)
    incidents = TriageAgent(provider).assess_all(merge_adjacent(detect_clusters(records)))
    assert incidents and not incidents[0].frozen
    assert incidents[0].verdict_source == "fail-closed"


def test_fallback_uses_context_not_the_prompt_text():
    """The bug that released every incident: facts were scraped out of the
    prompt, and the schema example printed alongside broke the parse."""
    verdict = DeterministicFallback().complete_json(
        "irrelevant prompt text {not: json} {{also: not}}",
        "incident_verdict",
        {"issuer": "HDFC", "method": "UPI", "lift_vs_baseline": 9.0,
         "share_transient_rail_class": 0.95},
    )
    assert verdict["is_systemic"] is True
    assert verdict["recommended_action"] == "freeze_and_reroute"


def test_fallback_releases_a_mixed_class_burst():
    verdict = DeterministicFallback().complete_json(
        "p", "incident_verdict",
        {"lift_vs_baseline": 9.0, "share_transient_rail_class": 0.2},
    )
    assert verdict["is_systemic"] is False


# ----------------------------------------------------------------- freeze

def _incident(frozen: bool) -> Incident:
    return Incident(
        issuer="HDFC", method="UPI", start=BASE, end=BASE + timedelta(hours=2),
        observed=50, expected=5, class_a_share=0.9, is_systemic=frozen,
        confidence=0.8, hypothesis="h",
        recommended_action="freeze_and_reroute" if frozen else "release_to_targeting",
        note="n", verdict_source="test", txn_ids=["a", "b"],
    )


def test_freeze_registry_ignores_released_incidents():
    assert FreezeRegistry([_incident(False)]).frozen_txn_ids() == set()


def test_freeze_registry_blocks_its_cohort():
    reg = FreezeRegistry([_incident(True)])
    assert reg.frozen_txn_ids() == {"a", "b"}
    assert reg.is_frozen(BASE + timedelta(hours=1), "HDFC", "UPI")
    assert not reg.is_frozen(BASE + timedelta(hours=1), "SBI", "UPI")
    assert not reg.is_frozen(BASE + timedelta(hours=9), "HDFC", "UPI")


def test_freeze_filter_removes_blocked_records():
    reg = FreezeRegistry([_incident(True)])
    records = [rec("a", BASE), rec("c", BASE)]
    assert [r.txn_id for r in reg.filter(records)] == ["c"]


# --------------------------------------------------------------- actuator

def test_every_decline_class_has_exactly_one_action():
    assert set(ACTION_BY_CLASS) == set(DeclineClass)


def test_actions_are_logged_before_they_are_attempted(tmp_path):
    with Ledger(tmp_path / "a.db", fresh=True) as led:
        Actuator(RazorpayClient("", ""), led).execute([rec("p1", BASE)])
        entries = list(led.entries(kind="action_taken"))
        assert len(entries) == 1
        assert entries[0].payload["txn_id"] == "p1"
        assert led.verify().ok


def test_the_freeze_gate_actually_blocks(tmp_path):
    with Ledger(tmp_path / "b.db", fresh=True) as led:
        results = Actuator(RazorpayClient("", ""), led).execute(
            [rec("p1", BASE), rec("p2", BASE)], frozen_txn_ids={"p1"}
        )
    blocked = [r for r in results if r.action == "blocked_by_incident"]
    assert [r.txn_id for r in blocked] == ["p1"]
    assert not blocked[0].executed


def test_oversized_amounts_escalate_instead_of_automating():
    results = Actuator(RazorpayClient("", "")).execute(
        [rec("big", BASE, amount=MAX_ACTION_PAISE + 1)]
    )
    assert results[0].action == "escalated_to_human"
    assert not results[0].executed


def test_no_network_call_without_test_mode_credentials():
    assert not RazorpayClient("", "").live
    assert not RazorpayClient("rzp_live_abc", "secret").live, "live keys must be refused"
    assert RazorpayClient("rzp_test_abc", "secret").live


@pytest.mark.parametrize("cls", list(DeclineClass))
def test_actions_are_allow_listed_per_class(cls):
    results = Actuator(RazorpayClient("", "")).execute([rec("p", BASE, cls=cls)])
    assert results[0].action == ACTION_BY_CLASS[cls]
    assert results[0].dry_run
