"""The triage agent.

Sits between the detector and every outbound action. The detector says *this
bucket is statistically surprising*; the agent decides whether that surprise is
one problem about a bank or many problems about customers, and if it is the
former it freezes the cohort so nobody gets messaged about an outage.

Two design choices are worth defending.

**The agent's job is narrow on purpose.** It receives a compact, pre-computed
summary and returns a verdict. It does not roam the ledger, and it does not
choose actions. Free-tier models are perfectly reliable at a bounded
classify-and-explain task and unreliable at open-ended tool loops, so the task
was shaped to the thing that works rather than the thing that demos well.

**The model cannot authorise anything.** Its output is parsed into a schema and
range-checked before it is allowed to influence a decision, and a malformed or
missing verdict fails closed to `release_to_targeting` -- the option that keeps
ANTAR's normal safeguards in play rather than freezing the book on a bad parse.
The LLM writes the reasoning; a deterministic policy writes the cheque.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from antar.llm.provider import CachingProvider, default_provider
from antar.triage.detector import FailureCluster

SCHEMA_NAME = "incident_verdict"

VALID_ACTIONS = {"freeze_and_reroute", "freeze_and_wait", "release_to_targeting"}

PROMPT = """\
You are the triage function of a payment-recovery system. A statistical detector \
has flagged a burst of failed payments on one payment rail. Decide whether this is \
ONE systemic incident (a bank or PSP degrading) or MANY independent customer problems.

This matters because the two demand opposite responses. A systemic incident must \
NOT trigger customer messaging: the customers did nothing wrong, contacting them \
teaches them the merchant is broken, and the real fix is to wait or re-route. \
Independent failures should flow on to normal targeting.

Signals of a systemic incident: failure count far above the rail's own baseline, \
failures concentrated in the transient-rail decline class, many distinct customers \
affected in a short window.

Signals of independent failures: counts only modestly elevated, failures spread \
across decline classes (expired cards, insufficient funds), few distinct customers.

Evidence:
{evidence}

Respond with JSON only, no prose outside it, matching exactly:
{{"is_systemic": <bool>,
  "confidence": <float 0-1>,
  "hypothesis": "<one sentence naming the rail and what you think happened>",
  "recommended_action": "<freeze_and_reroute | freeze_and_wait | release_to_targeting>",
  "note": "<two sentences an on-call engineer could act on>"}}
"""


@dataclass
class Incident:
    issuer: str
    method: str
    start: datetime
    end: datetime
    observed: int
    expected: float
    class_a_share: float
    is_systemic: bool
    confidence: float
    hypothesis: str
    recommended_action: str
    note: str
    verdict_source: str
    txn_ids: list[str] = field(default_factory=list)

    @property
    def frozen(self) -> bool:
        return self.is_systemic and self.recommended_action.startswith("freeze")

    def covers(self, ts: datetime, issuer: str, method: str) -> bool:
        return issuer == self.issuer and method == self.method and self.start <= ts < self.end

    def to_payload(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "method": self.method,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "observed": self.observed,
            "expected": round(self.expected, 2),
            "class_a_share": round(self.class_a_share, 3),
            "is_systemic": self.is_systemic,
            "confidence": self.confidence,
            "hypothesis": self.hypothesis,
            "recommended_action": self.recommended_action,
            "note": self.note,
            "verdict_source": self.verdict_source,
            "frozen": self.frozen,
            "affected_transactions": len(self.txn_ids),
        }


def _validate(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Schema and range check. Anything that fails here never reaches a decision."""
    try:
        action = str(raw["recommended_action"])
        confidence = float(raw["confidence"])
        if action not in VALID_ACTIONS or not 0.0 <= confidence <= 1.0:
            return None
        return {
            "is_systemic": bool(raw["is_systemic"]),
            "confidence": confidence,
            "hypothesis": str(raw["hypothesis"])[:400],
            "recommended_action": action,
            "note": str(raw["note"])[:800],
        }
    except (KeyError, TypeError, ValueError):
        return None


FAIL_CLOSED = {
    "is_systemic": False,
    "confidence": 0.0,
    "hypothesis": "Verdict unavailable or malformed.",
    "recommended_action": "release_to_targeting",
    "note": (
        "The model returned nothing usable, so this episode was released to normal "
        "targeting rather than frozen. Failing closed here means declining to take "
        "the more drastic action on unvalidated output."
    ),
}


class TriageAgent:
    def __init__(self, provider: CachingProvider | None = None) -> None:
        self.provider = provider or default_provider()

    def assess(self, episode: Sequence[FailureCluster]) -> Incident:
        """Turn one episode of breaching buckets into an incident verdict."""
        first, last = episode[0], episode[-1]
        observed = sum(c.observed for c in episode)
        expected = sum(c.expected for c in episode)
        txn_ids = [t for c in episode for t in c.txn_ids]
        a_share = sum(c.class_a_share * c.observed for c in episode) / max(observed, 1)

        evidence = {
            "issuer": first.issuer,
            "method": first.method,
            "window_start": first.bucket_start.isoformat(),
            "window_minutes": int((last.bucket_end - first.bucket_start).total_seconds() // 60),
            "failures_observed": observed,
            "failures_expected": round(expected, 2),
            "lift_vs_baseline": round(observed / expected, 1) if expected else 999.0,
            "share_transient_rail_class": round(a_share, 3),
            "distinct_customers": len(set(txn_ids)),
            "consecutive_breaching_buckets": len(episode),
        }

        import json as _json

        completion = self.provider.complete(
            PROMPT.format(evidence=_json.dumps(evidence, indent=2)), SCHEMA_NAME, evidence
        )
        verdict = _validate(completion.data) or FAIL_CLOSED
        source = completion.source if _validate(completion.data) else "fail-closed"

        return Incident(
            issuer=first.issuer,
            method=first.method,
            start=first.bucket_start,
            end=last.bucket_end,
            observed=observed,
            expected=expected,
            class_a_share=a_share,
            verdict_source=source,
            txn_ids=txn_ids,
            **verdict,
        )

    def assess_all(self, episodes: Sequence[Sequence[FailureCluster]]) -> list[Incident]:
        return [self.assess(e) for e in episodes]


class FreezeRegistry:
    """Which transactions are currently forbidden from being contacted.

    This is the object that stands between an outage and four thousand
    apology messages.
    """

    def __init__(self, incidents: Sequence[Incident]) -> None:
        self.incidents = [i for i in incidents if i.frozen]

    def is_frozen(self, ts: datetime, issuer: str, method: str) -> bool:
        return any(i.covers(ts, issuer, method) for i in self.incidents)

    def frozen_txn_ids(self) -> set[str]:
        return {t for i in self.incidents for t in i.txn_ids}

    def filter(self, records: Sequence[Any]) -> list[Any]:
        """Drop records inside a frozen cohort before targeting ever sees them."""
        blocked = self.frozen_txn_ids()
        return [r for r in records if r.txn_id not in blocked]
