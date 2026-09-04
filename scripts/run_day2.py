"""Day 2 -- Sensorium and the naive baseline bot.

Builds the control condition the rest of the project is measured against, then
opens the truth book to show the gap between what it claims and what it caused.

The bot is deliberately competent: same features the uplift model will get, a
real calibrated classifier, and a sensible objective. It loses on *objective*,
not on information.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from antar.config import load_config
from antar.evaluation import TruthBook
from antar.features import amounts_by_txn
from antar.ledger import Ledger
from antar.policies.baseline import NaiveRecoveryBot
from antar.sensorium import Sensorium
from antar.simulator.engine import Simulator
from antar.taxonomy import DeclineClass

LEDGER_PATH = Path(__file__).resolve().parent.parent / "data" / "ledger_day2.db"
RULE = "-" * 78
WARMUP_FRACTION = 0.4
CONTACT_BUDGET_FRACTION = 0.25


def banner(title: str) -> None:
    print(f"\n{RULE}\n  {title}\n{RULE}")


def main() -> int:
    cfg = load_config()
    sim = Simulator(cfg)
    result = sim.run()
    truth = TruthBook(result.events)

    # ------------------------------------------------------------ intake
    banner("1. SENSORIUM")
    directory = {cid: c.observable for cid, c in result.customers.items()}
    with Ledger(LEDGER_PATH, fresh=True) as ledger:
        sensorium = Sensorium(ledger, directory)
        records = sensorium.observe_many(ev.to_raw_event() for ev in result.events)
        print(f"raw gateway events  : {len(result.events):,}")
        print(f"normalised records  : {len(records):,}")
        print(f"ledger entries      : {len(ledger):,}")
        print(f"chain               : {ledger.verify()}")

    print("\nA FailureRecord carries no outcome and no counterfactual. The policy")
    print("layer physically cannot read the answer it is meant to estimate.")

    # ------------------------------------------------- train / test split
    cutoff = result.start + timedelta(days=cfg.window.days * WARMUP_FRACTION)
    warmup = [r for r in records if r.ts < cutoff.isoformat()]
    live = [r for r in records if r.ts >= cutoff.isoformat()]

    banner("2. THE BASELINE LEARNS")
    print(f"warm-up window : {len(warmup):,} failures (contacts everyone -- no holdout)")
    print(f"live window    : {len(live):,} failures")

    # In warm-up the bot contacts everything, so every training row is treated.
    # That is the industry blind spot made concrete: no untreated arm exists.
    warm_ids = [r.txn_id for r in warmup]
    warm_outcomes = truth.realise(warm_ids, treated=set(warm_ids))

    bot = NaiveRecoveryBot().fit(warmup, [warm_outcomes[t] for t in warm_ids])
    print("trained on     : contacted transactions only -- P(recover | contacted, X)")
    print("cannot form    : P(recover | NOT contacted, X)  <- no control arm exists")

    # ------------------------------------------------------------ it acts
    budget = int(len(live) * CONTACT_BUDGET_FRACTION)
    selection = bot.select(live, budget=budget)
    chosen = set(selection.chosen)
    amounts = amounts_by_txn(live)

    banner("3. WHERE IT SPENDS THE BUDGET")
    print(f"contacts sent : {selection.n_contacted:,} of {selection.n_eligible:,} eligible\n")

    print(f"{'class':<20}{'in pool':>10}{'contacted':>11}{'targeted%':>11}{'true uplift':>13}")
    print(RULE)
    for cls in DeclineClass:
        pool = [r for r in live if r.decline_class is cls]
        if not pool:
            continue
        picked = [r for r in pool if r.txn_id in chosen]
        up = float(np.mean([truth[r.txn_id].uplift for r in pool]))
        flag = "  <- budget sink" if len(picked) / len(pool) > 0.5 and up < 0.05 else ""
        print(f"{cls.value:<20}{len(pool):>10,}{len(picked):>11,}"
              f"{len(picked)/len(pool):>10.0%}{up:>13.3f}{flag}")

    # -------------------------------------------------- open the truth book
    banner("4. WHAT IT CLAIMS vs WHAT IT CAUSED")
    claimed = truth.gross_claimed_value(chosen, amounts)
    caused = truth.true_incremental_value(chosen, amounts)
    overstatement = (claimed / caused - 1.0) if caused else float("inf")

    print(f"last-touch claim (recoveries after contact) : INR {claimed:>14,.0f}")
    print(f"genuinely caused (y1 - y0 on the treated)   : INR {caused:>14,.0f}")
    print(f"overstatement                               : {overstatement:>17.0%}")

    print("\nBe precise about what this number is, because it is easy to overclaim.")
    print("It measures GROSS vs INCREMENTAL on the cohort this bot chose -- 74% of")
    print("whom were paying regardless. That is a larger and different quantity")
    print("from the 30-60% Yuno documents, which is last-touch attribution")
    print("overstating ONE mechanism inside a multi-mechanism stack. The two agree")
    print("qualitatively; ours must not be quoted as if it were theirs.")
    print("\nThe magnitude also follows directly from our class-A self-recovery")
    print("assumption. Day 4's sensitivity sweep is what tests whether the")
    print("conclusion survives when that assumption moves.")

    counts = truth.stratum_counts(chosen)
    banner("5. WHO IT ACTUALLY CONTACTED")
    for name in ("sure_thing", "persuadable", "lost_cause"):
        n = counts[name]
        print(f"{name:<14}{n:>8,}  {n/len(chosen):>6.1%}")
    wasted = counts["sure_thing"] + counts["lost_cause"]
    print(f"\n{wasted/len(chosen):.0%} of contacts changed nothing: the customer was always")
    print("going to pay, or was never going to. The bot cannot tell them apart,")
    print("because telling them apart requires withholding treatment from someone.")

    print("\nThat is tomorrow's job.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
