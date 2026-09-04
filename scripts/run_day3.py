"""Day 3 -- the holdout and the first honest number.

Assigns a randomised control arm, measures the average treatment effect with a
confidence sequence that stays valid under continuous monitoring, and checks the
estimate against ground truth the estimator never sees.

Then demonstrates *why* the sequential machinery is necessary, by running null
experiments under a stopping rule and counting how often ordinary statistics
invents an effect that isn't there.

Computation and presentation are split: `compute()` produces a plain dict which
is both printed here and written to data/day3_results.json for the console. The
console is a viewer, never a compute engine.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from antar.config import load_config
from antar.evaluation import TruthBook
from antar.holdout import assign, balance_report
from antar.ledger import Ledger
from antar.sensorium import Sensorium
from antar.simulator.engine import Simulator
from antar.stats.sequential import Interval, always_valid_difference, fixed_horizon_difference
from antar.stats.validation import peeking_false_positive_rates
from antar.taxonomy import DeclineClass

ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / "data" / "ledger_day3.db"
RESULTS_PATH = ROOT / "data" / "day3_results.json"
RULE = "-" * 78
SALT = "antar-holdout-v1"
HOLDOUT_FRACTION = 0.10
ALPHA = 0.05
MIN_ARM_FOR_SUBGROUP = 30


def _interval_dict(ci: Interval) -> dict[str, Any]:
    return {"point": ci.point, "lower": ci.lower, "upper": ci.upper,
            "n": ci.n, "method": ci.method}


def compute() -> dict[str, Any]:
    cfg = load_config()
    result = Simulator(cfg).run()
    truth = TruthBook(result.events)
    directory = {cid: c.observable for cid, c in result.customers.items()}
    records = Sensorium(customer_directory=directory).observe_many(
        ev.to_raw_event() for ev in result.events
    )
    by_id = {r.txn_id: r for r in records}
    txn_ids = [r.txn_id for r in records]

    assignment = assign(txn_ids, SALT, HOLDOUT_FRACTION)
    with Ledger(LEDGER_PATH, fresh=True) as ledger:
        ledger.append("experiment_started", {
            "salt": SALT, "holdout_fraction": HOLDOUT_FRACTION,
            "alpha": ALPHA, "n": len(txn_ids),
        })
        chain_ok = ledger.verify().ok

    outcomes = truth.realise(txn_ids, treated=assignment.treatment)
    treated_y = [float(outcomes[t]) for t in assignment.treatment]
    control_y = [float(outcomes[t]) for t in assignment.control]

    av = always_valid_difference(treated_y, control_y, alpha=ALPHA,
                                 n_target=len(txn_ids), holdout_fraction=HOLDOUT_FRACTION)
    fh = fixed_horizon_difference(treated_y, control_y, alpha=ALPHA)
    true_ate = float(np.mean([truth[t].uplift for t in txn_ids]))

    strata = {r.txn_id: r.decline_class.value for r in records}
    balance = [
        {"class": cls, "treatment": t, "control": c, "holdout_share": share}
        for cls, (t, c, share) in balance_report(assignment, strata).items()
    ]

    by_class = []
    for cls in DeclineClass:
        ids = [t for t in txn_ids if by_id[t].decline_class is cls]
        t_arm = [float(outcomes[t]) for t in ids if t in assignment.treatment]
        c_arm = [float(outcomes[t]) for t in ids if t in assignment.control]
        if len(t_arm) < MIN_ARM_FOR_SUBGROUP or len(c_arm) < MIN_ARM_FOR_SUBGROUP:
            continue
        ci = always_valid_difference(t_arm, c_arm, alpha=ALPHA,
                                     n_target=len(ids), holdout_fraction=HOLDOUT_FRACTION)
        by_class.append({
            "class": cls.value,
            **_interval_dict(ci),
            "truth": float(np.mean([truth[t].uplift for t in ids])),
            "detected": ci.excludes_zero(),
            "n_treated": len(t_arm),
            "n_control": len(c_arm),
        })

    peek = peeking_false_positive_rates(n_experiments=400, n_per_arm=3000, peek_every=100)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {"salt": SALT, "holdout_fraction": HOLDOUT_FRACTION,
                   "alpha": ALPHA, "n": len(txn_ids), "seed": cfg.seed},
        "assignment": {
            "treatment": len(assignment.treatment),
            "control": len(assignment.control),
            "realised_holdout": assignment.realised_holdout,
            "recomputable": assignment.verify(),
            "chain_ok": chain_ok,
        },
        "balance": balance,
        "ate": {
            "always_valid": _interval_dict(av),
            "fixed_horizon": _interval_dict(fh),
            "treated_rate": float(np.mean(treated_y)),
            "control_rate": float(np.mean(control_y)),
            "truth": true_ate,
            "covered": bool(av.lower <= true_ate <= av.upper),
            "width_ratio": av.radius / fh.radius,
        },
        "by_class": by_class,
        "peeking": {
            "n_experiments": peek.n_experiments,
            "peeks_per_experiment": peek.peeks_per_experiment,
            "always_valid_fpr": peek.always_valid_fpr,
            "fixed_horizon_fpr": peek.fixed_horizon_fpr,
            "alpha": peek.alpha,
        },
    }


def banner(title: str) -> None:
    print(f"\n{RULE}\n  {title}\n{RULE}")


def render(r: dict[str, Any]) -> None:
    a, ate = r["assignment"], r["ate"]

    banner("1. ARM ASSIGNMENT")
    print(f"salt               : {r['config']['salt']}")
    print(f"nominal holdout    : {r['config']['holdout_fraction']:.0%}")
    print(f"realised holdout   : {a['realised_holdout']:.2%}")
    print(f"treatment / control: {a['treatment']:,} / {a['control']:,}")
    print(f"recomputable from (txn_id, salt): {a['recomputable']}")

    print("\nBalance by decline class (Bernoulli assignment, so not exact):")
    print(f"{'class':<20}{'treatment':>11}{'control':>9}{'holdout%':>10}")
    for row in r["balance"]:
        print(f"{row['class']:<20}{row['treatment']:>11,}{row['control']:>9,}"
              f"{row['holdout_share']:>9.1%}")

    banner("2. AVERAGE TREATMENT EFFECT")
    av, fh = ate["always_valid"], ate["fixed_horizon"]
    print(f"treated recovery rate : {ate['treated_rate']:.4f}  (n={av['n'] - a['control']:,})")
    print(f"control recovery rate : {ate['control_rate']:.4f}  (n={a['control']:,})")
    print()
    print(f"always-valid ATE      : {av['point']:+.4f}  [{av['lower']:+.4f}, {av['upper']:+.4f}]")
    print(f"fixed-horizon ATE     : {fh['point']:+.4f}  [{fh['lower']:+.4f}, {fh['upper']:+.4f}]")
    print(f"ground truth ATE      : {ate['truth']:+.4f}   <- the estimator never sees this")
    print(f"truth inside interval : {ate['covered']}")
    print(f"\nThe sequential interval is {ate['width_ratio']:.2f}x wider. That width is what")
    print("buys the right to stop whenever the evidence is in.")

    banner("3. EFFECT BY DECLINE CLASS")
    print(f"{'class':<20}{'est. ATE':>10}{'95% CS':>22}{'truth':>9}{'  verdict'}")
    print(RULE)
    for row in r["by_class"]:
        verdict = "detected" if row["detected"] else "indistinguishable from zero"
        print(f"{row['class']:<20}{row['point']:>+10.3f}  "
              f"[{row['lower']:>+6.3f},{row['upper']:>+6.3f}]{row['truth']:>+9.3f}  {verdict}")

    print("\nClass A is where the baseline spends 65% of its budget. Here it cannot")
    print("even be distinguished from doing nothing at all.")
    print("\nBe careful reading the rest of that column. B, E and F are NOT null --")
    print("their true effects are real. Their control arms are 60-170 transactions,")
    print("which is simply too thin to resolve an effect this size under a")
    print("time-uniform bound. That is a power limitation, and reporting it as one")
    print("rather than as 'no effect' is the whole point of the project.")
    print("Tomorrow's uplift model ranks individuals and pools across classes, so")
    print("it does not need a separate holdout per class to be useful.")

    banner("4. WHY NOT JUST USE A t-INTERVAL")
    p = r["peeking"]
    print(f"{p['n_experiments']} null experiments, peeked {p['peeks_per_experiment']}x each")
    print(f"  fixed-horizon false positives : {p['fixed_horizon_fpr']:6.1%}")
    print(f"  always-valid  false positives : {p['always_valid_fpr']:6.1%}"
          f"   (target <= {p['alpha']:.0%})")
    print("\nBoth arms drew from the same distribution, so every alarm above is false.")
    print("An agent that monitors continuously and stops on significance needs the")
    print("second row, or its stopping rule manufactures the effect it stops for.")


def main() -> int:
    results = compute()
    render(results)
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nresults written to {RESULTS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
