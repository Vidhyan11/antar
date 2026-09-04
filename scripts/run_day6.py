"""Day 6 -- compliance, stopping rules, and the Counterfactual P&L.

The P&L is the artifact the whole project has been building toward. Everything
before it was estimation; this is the statement that says what the estimation
was worth, with every subtraction the industry leaves out.

Both policies get the same treatment, because a P&L that only flatters one of
them is a sales deck.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from antar.actuator import ACTION_BY_CLASS, Actuator, RazorpayClient
from antar.compliance import ArmMonitor, ComplianceLinter
from antar.config import load_config
from antar.economics import build_pnl
from antar.ledger import Ledger
from antar.pipeline import run_experiment
from antar.policies.baseline import NaiveRecoveryBot
from antar.policies.uplift import UpliftTargeter
from antar.triage.agent import FreezeRegistry, TriageAgent
from antar.triage.detector import detect_clusters, merge_adjacent

ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / "data" / "ledger_day6.db"
RESULTS_PATH = ROOT / "data" / "day6_results.json"
RULE = "-" * 78
TRAIN_FRACTION = 0.5
BUDGET_FRACTION = 0.25
ALPHA = 0.05
HOLDOUT = 0.10


def banner(title: str) -> None:
    print(f"\n{RULE}\n  {title}\n{RULE}")


def compute() -> dict[str, Any]:
    cfg = load_config()
    data = run_experiment(cfg)
    train, evaluate = data.split_by_time(TRAIN_FRACTION)
    by_id = data.by_id

    # ---------------------------------------------------------- triage
    incidents = TriageAgent().assess_all(merge_adjacent(detect_clusters(data.records)))
    freeze = FreezeRegistry(incidents)
    frozen = freeze.frozen_txn_ids()

    # ---------------------------------------------------------- fit
    ids = [r.txn_id for r in train]
    treated_set = data.assignment.treatment
    antar = UpliftTargeter().fit(
        train, [data.outcomes[t] for t in ids], [t in treated_set for t in ids]
    )
    treated_train = [r for r in train if r.txn_id in treated_set]
    naive = NaiveRecoveryBot().fit(
        treated_train, [data.outcomes[r.txn_id] for r in treated_train]
    )

    budget = int(len(evaluate) * BUDGET_FRACTION)
    picks = {
        "baseline": naive.select(evaluate, budget=budget).chosen,
        "antar": antar.select(freeze.filter(evaluate), budget=budget).chosen,
    }

    # ------------------------------------------------ act, under the rules
    #
    # BOTH policies go through the same linter and the same freeze. Running one
    # constrained and the other unconstrained would compare a system wearing a
    # seatbelt against one that is not, and would flatter whichever we chose to
    # leave unbuckled.
    BLOCKED = ("blocked_by_compliance", "blocked_by_incident", "escalated_to_human")

    def act(name: str, chosen: list[str], ledger: Ledger) -> tuple[list[str], dict[str, int]]:
        actuator = Actuator(RazorpayClient(), ledger, linter=ComplianceLinter(cfg))
        results = actuator.execute([by_id[t] for t in chosen], frozen_txn_ids=frozen)
        vetoes: dict[str, int] = {}
        for r in results:
            if r.action in BLOCKED:
                # Group by rule, not by the detail string -- otherwise every
                # quiet-hours veto is its own row because it carries a timestamp.
                key = r.rule or r.action
                vetoes[key] = vetoes.get(key, 0) + 1
        return [r.txn_id for r in results if r.action not in BLOCKED], vetoes

    with Ledger(LEDGER_PATH, fresh=True) as ledger:
        baseline_delivered, baseline_vetoes = act("baseline", picks["baseline"], ledger)
        delivered, vetoes = act("antar", picks["antar"], ledger)
        chain_ok = ledger.verify().ok
        entries = len(ledger)

    # ----------------------------------------------------------- P&L
    def pnl_for(chosen: list[str]) -> Any:
        return build_pnl(
            cfg,
            contacted=chosen,
            eligible=[r.txn_id for r in evaluate],
            actions={t: ACTION_BY_CLASS[by_id[t].decline_class] for t in chosen},
            records=by_id,
            truth=data.truth,
            alpha=ALPHA,
            holdout_fraction=HOLDOUT,
        )

    pnls = {
        "baseline": pnl_for(baseline_delivered),
        "antar": pnl_for(delivered),
    }

    # -------------------------------------------------- stopping rules
    monitor = ArmMonitor(alpha=ALPHA, holdout_fraction=HOLDOUT)
    arms = []
    for cls_name in sorted({by_id[t].decline_class.value for t in [r.txn_id for r in evaluate]}):
        pool = [r.txn_id for r in evaluate if r.decline_class.value == cls_name]
        t_arm = [float(data.outcomes[t]) for t in pool if t in treated_set]
        c_arm = [float(data.outcomes[t]) for t in pool if t not in treated_set]
        arms.append(monitor.assess(cls_name, t_arm, c_arm).to_payload())

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {"seed": cfg.seed, "budget_fraction": BUDGET_FRACTION,
                   "margin_rate": cfg.economics.margin_rate,
                   "customer_ltv_inr": cfg.economics.customer_ltv_inr},
        "pnl": {k: v.to_payload() for k, v in pnls.items()},
        "pnl_text": {k: v.render(k) for k, v in pnls.items()},
        "compliance": {
            "selected": len(picks["antar"]),
            "delivered": len(delivered),
            "vetoes": vetoes,
            "veto_total": len(picks["antar"]) - len(delivered),
            "baseline_selected": len(picks["baseline"]),
            "baseline_delivered": len(baseline_delivered),
            "baseline_vetoes": baseline_vetoes,
        },
        "arms": arms,
        "ledger": {"entries": entries, "chain_ok": chain_ok},
    }


def render(r: dict[str, Any]) -> None:
    banner("1. COMPLIANCE -- WHAT THE RULES REFUSED")
    c = r["compliance"]
    print(f"selected by the targeter : {c['selected']:,}")
    print(f"actually delivered       : {c['delivered']:,}")
    print(f"vetoed                   : {c['veto_total']:,}\n")
    for reason, n in sorted(c["vetoes"].items(), key=lambda kv: -kv[1]):
        print(f"    {n:>6,}  {reason}")
    print("\nEvery veto is written to the ledger with its reason. An audit trail that")
    print("only records what happened is half a trail -- the refusals are the half")
    print("that shows the rules were load-bearing rather than decorative.")

    banner("2. STOPPING RULES -- THE TRIAL'S SAFETY BOARD")
    print(f"{'arm':<22}{'effect':>9}{'95% CS':>22}{'state':>10}  reason")
    print(RULE)
    for a in r["arms"]:
        state = "PAUSED" if a["paused"] else "running"
        print(f"{a['arm']:<22}{a['effect']:>+9.3f}  [{a['lower']:>+6.3f},{a['upper']:>+6.3f}]"
              f"{state:>10}  {a['reason']}")
    print("\nAn arm that cannot be distinguished from doing nothing pauses itself.")
    print("Continuous monitoring is only legitimate with a time-uniform interval,")
    print("which is the entire reason day 3 built one.")

    banner("3. THE COUNTERFACTUAL P&L")
    print(r["pnl_text"]["baseline"].replace("baseline", "NAIVE BASELINE", 1))
    print()
    print(r["pnl_text"]["antar"].replace("antar", "ANTAR", 1))

    b, a = r["pnl"]["baseline"], r["pnl"]["antar"]
    banner("4. THE COMPARISON")
    print(f"{'':<34}{'baseline':>14}{'ANTAR':>14}")
    print(RULE)
    for label, key in (
        ("headline claim", "last_touch_claim"),
        ("actually caused", "incremental_recovery"),
        ("channel cost", "channel_cost"),
        ("retention damage", "retention_damage"),
        ("NET VALUE CREATED", "net_value"),
    ):
        print(f"{label:<34}{b[key]:>14,.0f}{a[key]:>14,.0f}")
    print(f"{'contacts':<34}{b['contacts']:>14,}{a['contacts']:>14,}")
    print(f"{'headline overstates by':<34}{b['overstatement']:>13.1f}x{a['overstatement']:>13.1f}x")

    b_per = b["net_value"] / max(b["contacts"], 1)
    a_per = a["net_value"] / max(a["contacts"], 1)
    print(f"{'net value per contact':<34}{b_per:>14,.0f}{a_per:>14,.0f}")

    print()
    if a["net_value"] > b["net_value"] and a["contacts"] < b["contacts"]:
        print(f"ANTAR creates {a['net_value'] / max(b['net_value'], 1e-9):.1f}x the net value on "
              f"{1 - a['contacts'] / max(b['contacts'], 1):.0%} fewer contacts.")
        print("The baseline wins the headline and loses the P&L: more messages, more")
        print("gross recovery booked, less money actually caused, more customers burned")
        print("doing it. On a conventional dashboard only the second of those is visible.")
    elif a["net_value"] > b["net_value"]:
        print(f"ANTAR creates {a['net_value'] / max(b['net_value'], 1e-9):.1f}x the net value.")
    else:
        print("ANTAR does NOT lead on net value in this run -- do not claim that it does.")
        print(f"Net value per contact is {a_per:,.0f} vs {b_per:,.0f}; the absolute total")
        print("depends on how much budget each policy chose to spend.")

    banner("5. READ THE CONFIDENCE INTERVAL BEFORE QUOTING THE POINT ESTIMATE")
    print(f"ANTAR net value : {a['net_value']:,.0f}  "
          f"[{a['ci_low']:+,.0f}, {a['ci_high']:+,.0f}]")
    print("\nThat interval is enormous, and it is honest. Per-transaction net value")
    print("ranges from a few rupees of margin to minus a whole customer lifetime,")
    print("so a time-uniform bound over a spread that wide is necessarily loose.")
    print("The incremental-recovery line is measured far more tightly than the")
    print("bottom line; it is the retention term that widens it.")
    print("\nThe defensible claim is the ordering and the efficiency, not the exact")
    print("rupee figure. A team quoting a point estimate this wide as though it were")
    print("precise would be making the same mistake the project exists to criticise.")


def main() -> int:
    results = compute()
    render(results)
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nresults written to {RESULTS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
