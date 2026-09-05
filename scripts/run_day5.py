"""Day 5 -- triage, the incident freeze, and real actions.

The demo beat this exists to produce: inject a rail outage, watch the baseline
answer it with a flood of customer messages, and watch ANTAR go quiet.

Silence is the feature. A recovery agent that keeps talking through a bank
outage is not being helpful, and no amount of good targeting fixes it.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from antar.actuator import Actuator, RazorpayClient
from antar.config import load_config
from antar.ledger import Ledger
from antar.llm.provider import default_provider
from antar.pipeline import run_experiment
from antar.policies.baseline import NaiveRecoveryBot
from antar.policies.uplift import UpliftTargeter
from antar.triage.agent import FreezeRegistry, TriageAgent
from antar.triage.detector import detect_clusters, merge_adjacent

ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / "data" / "ledger_day5.db"
RESULTS_PATH = ROOT / "data" / "day5_results.json"
RULE = "-" * 78
TRAIN_FRACTION = 0.5
BUDGET_FRACTION = 0.25


def banner(title: str) -> None:
    print(f"\n{RULE}\n  {title}\n{RULE}")


def compute() -> dict[str, Any]:
    cfg = load_config()
    data = run_experiment(cfg)
    train, evaluate = data.split_by_time(TRAIN_FRACTION)

    # ------------------------------------------------------------ detect
    # The detector sees the whole stream, not the model's evaluation split. It
    # is a monitoring rule, not a fitted model -- there is nothing to leak, and
    # in production it would always have prior history. Running it on the split
    # gave it a cold start and cost two real outages that fell in the first days
    # of the window, before four same-hour observations existed to compare with.
    clusters = detect_clusters(data.records)
    episodes = merge_adjacent(clusters)

    provider = default_provider()
    incidents = TriageAgent(provider).assess_all(episodes)
    freeze = FreezeRegistry(incidents)
    frozen_ids = freeze.frozen_txn_ids()

    # Score against every outage the simulator actually created, so detection
    # recall is measured honestly rather than on a window we chose afterwards.
    true_outages = list(data.simulation.outages)
    # Detectability floor. A rail carrying almost no traffic cannot have an
    # outage detected by anyone, and reporting a miss there as a model failure
    # would be dishonest. Expected peak-bucket failures are computed from the
    # config's own traffic weights, not fitted after the fact.
    iw = dict(zip(cfg.rails.issuers, cfg.rails.issuer_weights, strict=True))
    mw = dict(zip(cfg.rails.methods, cfg.rails.method_weights, strict=True))
    per_min = cfg.window.attempts_per_day / 1440
    bucket = 60  # detector's hourly bucket

    outage_rows = []
    for o in true_outages:
        mins = (o.end - o.start).total_seconds() / 60
        peak = (per_min * min(mins, bucket) * iw[o.issuer] * mw[o.method]
                * cfg.rails.outages.failure_rate_during)
        hit = any(i.issuer == o.issuer and i.method == o.method
                  and i.start < o.end and o.start < i.end for i in incidents)
        outage_rows.append({
            "issuer": o.issuer, "method": o.method, "minutes": round(mins),
            "expected_peak_bucket_failures": round(peak, 1),
            "above_floor": peak >= 8, "detected": hit,
        })

    detected = sum(1 for r_ in outage_rows if r_["detected"])
    detectable = sum(1 for r_ in outage_rows if r_["above_floor"])
    detected_of_detectable = sum(
        1 for r_ in outage_rows if r_["above_floor"] and r_["detected"]
    )

    # ------------------------------------------------------------ target
    ids = [r.txn_id for r in train]
    model = UpliftTargeter().fit(
        train, [data.outcomes[t] for t in ids], [t in data.assignment.treatment for t in ids]
    )
    treated_train = [r for r in train if r.txn_id in data.assignment.treatment]
    naive = NaiveRecoveryBot().fit(
        treated_train, [data.outcomes[r.txn_id] for r in treated_train]
    )

    budget = int(len(evaluate) * BUDGET_FRACTION)
    baseline_pick = set(naive.select(evaluate, budget=budget).chosen)
    antar_pick = model.select(freeze.filter(evaluate), budget=budget)

    baseline_in_incident = len(baseline_pick & frozen_ids)
    by_id = data.by_id

    # ------------------------------------------------------------- act
    client = RazorpayClient()
    with Ledger(LEDGER_PATH, fresh=True) as ledger:
        for inc in incidents:
            ledger.append("incident_opened", inc.to_payload())
        actuator = Actuator(client, ledger)
        chosen_records = [by_id[t] for t in antar_pick.chosen]
        actions = actuator.execute(chosen_records, frozen_txn_ids=frozen_ids)
        chain_ok = ledger.verify().ok
        ledger_entries = len(ledger)

    action_counts: dict[str, int] = {}
    for a in actions:
        action_counts[a.action] = action_counts.get(a.action, 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "detector": {
            "clusters": len(clusters),
            "episodes": len(episodes),
            "true_outages_in_window": len(true_outages),
            "detected": detected,
            "detectable": detectable,
            "detected_of_detectable": detected_of_detectable,
            "outages": outage_rows,
        },
        "incidents": [i.to_payload() for i in incidents],
        "freeze": {
            "frozen_transactions": len(frozen_ids),
            "baseline_would_contact_inside_incident": baseline_in_incident,
            "antar_contacts_inside_incident": len(set(antar_pick.chosen) & frozen_ids),
        },
        "actions": {
            "total": len(actions),
            "executed_live": sum(1 for a in actions if a.executed and not a.idempotent),
            "already_existed": sum(1 for a in actions if a.idempotent),
            "failed": sum(1 for a in actions if a.error and not a.dry_run),
            "errors": sorted({(a.error or "")[:110] for a in actions if a.error and not a.dry_run}),
            "dry_run": sum(1 for a in actions if a.dry_run),
            "blocked": sum(1 for a in actions if a.action == "blocked_by_incident"),
            "escalated": sum(1 for a in actions if a.action == "escalated_to_human"),
            "by_action": action_counts,
            "mode": client.mode,
            "sample": [
                {"txn_id": a.txn_id, "action": a.action, "reference": a.reference,
                 "executed": a.executed, "dry_run": a.dry_run, "idempotent": a.idempotent}
                for a in actions[:6]
            ],
        },
        "ledger": {"entries": ledger_entries, "chain_ok": chain_ok},
        "provider": {
            "verdict_sources": sorted({i.verdict_source for i in incidents}) or ["none"],
        },
    }


def render(r: dict[str, Any]) -> None:
    d, f, act = r["detector"], r["freeze"], r["actions"]

    banner("1. CORRELATED-FAILURE DETECTION")
    print(f"breaching buckets      : {d['clusters']}")
    print(f"merged into episodes   : {d['episodes']}")
    print(f"real outages in window : {d['true_outages_in_window']}")
    print(f"above detection floor  : {d['detectable']}")
    print(f"detected               : {d['detected_of_detectable']} of {d['detectable']} detectable"
          f"   ({d['detected']} of {d['true_outages_in_window']} overall)")
    print()
    print(f"{'issuer':<7}{'method':<12}{'mins':>6}{'exp peak/hr':>13}{'floor':>8}{'found':>8}")
    print(RULE)
    for row in d["outages"]:
        print(f"{row['issuer']:<7}{row['method']:<12}{row['minutes']:>6}"
              f"{row['expected_peak_bucket_failures']:>13.1f}"
              f"{'over' if row['above_floor'] else 'UNDER':>8}"
              f"{'yes' if row['detected'] else 'no':>8}")

    print("\nA Poisson tail against the same hour on previous days. Simple enough that")
    print("a reviewer can check it by hand, which an autoencoder would not be.")
    print("\nThe floor column is the honest part. A rail carrying almost no traffic")
    print("cannot have an outage detected by any method -- there is no signal there to")
    print("find. Scoring ourselves against outages below the floor would be marking")
    print("our own homework generously; scoring above it is the real number.")

    banner("2. THE AGENT'S VERDICTS")
    print(f"verdict source(s): {', '.join(r['provider']['verdict_sources'])}")
    for inc in r["incidents"]:
        state = "FROZEN" if inc["frozen"] else "released"
        print(f"\n  {inc['issuer']:<6} {inc['method']:<11} {inc['start'][11:16]}-{inc['end'][11:16]}"
              f"  {inc['observed']:>4} failures vs {inc['expected']:>6.1f} expected   [{state}]")
        print(f"    class-A share {inc['class_a_share']:.0%} | confidence {inc['confidence']:.2f}"
              f" | action {inc['recommended_action']}")
        print(f"    {inc['hypothesis']}")

    banner("3. WHAT EACH SYSTEM DOES DURING AN OUTAGE")
    print(f"transactions inside an open incident        : {f['frozen_transactions']:,}")
    print(f"baseline would contact, inside the incident : {f['baseline_would_contact_inside_incident']:,}")
    print(f"ANTAR contacts, inside the incident         : {f['antar_contacts_inside_incident']:,}")
    print("\nThose messages would tell customers their payment failed during a window")
    print("when the bank was down and nothing they could do would have helped.")
    print("Staying silent is the correct action, and it is the one no success-rate")
    print("metric will ever reward.")

    banner("4. ACTIONS TAKEN")
    print(f"mode              : {act['mode']}")
    print(f"actions issued    : {act['total']:,}")
    print(f"created by API    : {act['executed_live']:,}")
    print(f"already existed   : {act['already_existed']:,}   (idempotency key held on a replay)")
    print(f"failed            : {act['failed']:,}")
    print(f"dry-run recorded  : {act['dry_run']:,}")
    print(f"blocked by freeze : {act['blocked']:,}")
    print(f"escalated to human: {act['escalated']:,}")
    print("\nby action type:")
    for name, count in sorted(act["by_action"].items(), key=lambda kv: -kv[1]):
        print(f"    {name:<26}{count:>6,}")
    if act["errors"]:
        print("\nerrors (never silent -- a failure that prints nothing is the worst kind):")
        for e in act["errors"]:
            print(f"    {e}")

    print("\nsample:")
    for s in act["sample"]:
        tag = ("SAME" if s["idempotent"] else "LIVE") if s["executed"] else (
            "dry" if s["dry_run"] else "FAIL")
        print(f"    {s['txn_id']:<14}{s['action']:<26}{tag:<5}{s['reference']}")

    if act["already_existed"]:
        print("\nSAME means Razorpay refused to create a duplicate. Every write carries a")
        print("stable idempotency key derived from the transaction and the action, so")
        print("replaying a run cannot double-charge anyone -- the second run creates")
        print("nothing new, and that is the key working rather than an integration fault.")

    led = r["ledger"]
    print(f"\nledger: {led['entries']:,} entries, chain_ok={led['chain_ok']}")
    print("Every action was written down before the network call, so a call that")
    print("fails halfway still leaves a trace.")


def main() -> int:
    results = compute()
    render(results)
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nresults written to {RESULTS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
