"""Day 1 -- simulator + ledger.

Runs the simulation, writes every failure to the hash-chained ledger, verifies
the chain, then prints the table this whole project exists to argue about.

Console output is deliberately ASCII-only: Windows terminals default to cp1252
and a rupee sign would raise UnicodeEncodeError. Amounts are shown as INR.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from antar.config import load_config
from antar.ledger import Ledger, tamper_for_demo
from antar.simulator.engine import Simulator
from antar.taxonomy import DeclineClass

LEDGER_PATH = Path(__file__).resolve().parent.parent / "data" / "ledger.db"
RULE = "-" * 78


def banner(title: str) -> None:
    print(f"\n{RULE}\n  {title}\n{RULE}")


def main() -> int:
    cfg = load_config()
    print(f"config : {cfg._source_path}")
    print(f"seed   : {cfg.seed}")

    # ---------------------------------------------------------------- sim
    banner("1. SIMULATION")
    sim = Simulator(cfg)
    result = sim.run()

    print(f"window          : {result.start.date()} -> {result.end.date()}  ({cfg.window.days} days)")
    print(f"customers       : {len(result.customers):,}")
    print(f"payment attempts: {result.attempts:,}")
    print(f"failures        : {len(result.events):,}  ({result.failure_rate:.1%})")
    print(f"rail incidents  : {len(result.outages)}")
    for o in result.outages:
        mins = int((o.end - o.start).total_seconds() // 60)
        print(f"    {o.start:%Y-%m-%d %H:%M} +{mins:>4}min  {o.issuer:<6} {o.method}")

    # ------------------------------------------------------------- ledger
    banner("2. LEDGER")
    with Ledger(LEDGER_PATH, fresh=True) as ledger:
        ledger.append("run_started", {
            "seed": cfg.seed,
            "config_path": cfg._source_path,
            "window_days": cfg.window.days,
        })
        ledger.append_many("failure_observed", (ev.to_ledger_payload() for ev in result.events))
        ledger.append("run_completed", {"failures_recorded": len(result.events)})

        print(f"entries written : {len(ledger):,}")
        print(f"head hash       : {ledger.head()[:32]}...")
        print(f"verification    : {ledger.verify()}")

        try:
            ledger._conn.execute("UPDATE ledger SET kind = 'forged' WHERE seq = 2")
            print("append-only     : FAILED -- update was allowed")
        except Exception as exc:  # sqlite3.IntegrityError from the trigger
            print(f"append-only     : enforced ({exc})")

    # ------------------------------------------------- the argument itself
    banner("3. THE INVERSION  (why gross recovery is the wrong metric)")

    rows = []
    for cls in DeclineClass:
        evs = [e for e in result.events if e.decline_class is cls]
        if not evs:
            continue
        rows.append({
            "cls": cls,
            "n": len(evs),
            "share": len(evs) / len(result.events),
            "p0": float(np.mean([e.p0 for e in evs])),
            "p1": float(np.mean([e.p1 for e in evs])),
            "uplift": float(np.mean([e.true_uplift for e in evs])),
            "value": sum(e.amount_paise for e in evs) / 100.0,
        })

    by_success = {r["cls"]: i + 1 for i, r in enumerate(sorted(rows, key=lambda r: -r["p1"]))}
    by_uplift = {r["cls"]: i + 1 for i, r in enumerate(sorted(rows, key=lambda r: -r["uplift"]))}

    print(f"{'class':<20}{'n':>7}{'share':>8}{'p0':>8}{'p1':>8}{'uplift':>9}"
          f"{'rank p1':>9}{'rank up':>9}")
    print(RULE)
    for r in sorted(rows, key=lambda r: -r["uplift"]):
        cls = r["cls"]
        flag = ""
        if by_success[cls] == 1:
            flag = "  <- best success rate, near-zero value"
        if by_uplift[cls] == 1:
            flag = "  <- most valuable cohort"
        print(f"{cls.value:<20}{r['n']:>7,}{r['share']:>7.1%}{r['p0']:>8.2f}{r['p1']:>8.2f}"
              f"{r['uplift']:>9.3f}{by_success[cls]:>9}{by_uplift[cls]:>9}{flag}")

    corr = np.corrcoef(
        [by_success[r["cls"]] for r in rows],
        [by_uplift[r["cls"]] for r in rows],
    )[0, 1]
    print(f"\nrank correlation between treated success rate and true uplift: {corr:+.2f}")
    print("A recovery engine optimised for success rate walks away from the money.")

    # ---------------------------------------------------------- strata
    banner("4. WHO IS ACTUALLY WORTH CONTACTING")
    total = len(result.events)
    for name in ("sure_thing", "persuadable", "lost_cause"):
        evs = [e for e in result.events if e.stratum == name]
        val = sum(e.amount_paise for e in evs) / 100.0
        print(f"{name:<14}{len(evs):>8,}  {len(evs)/total:>6.1%}   INR {val:>14,.0f}")
    persuadable = [e for e in result.events if e.stratum == "persuadable"]
    print(f"\nOnly {len(persuadable)/total:.1%} of failures are persuadable.")
    print("Every contact outside that slice is cost without value -- and the naive")
    print("bot cannot tell the difference, because it never withholds treatment.")

    # ------------------------------------------------------- tamper demo
    banner("5. TAMPER DETECTION")
    with Ledger(LEDGER_PATH) as ledger:
        print(f"before tampering: {ledger.verify()}")
    tamper_for_demo(LEDGER_PATH, seq=2, new_payload={"txn_id": "pay_00000000", "amount_paise": 999999999})
    with Ledger(LEDGER_PATH) as ledger:
        print(f"after  tampering: {ledger.verify()}")
    print("\nThe attacker dropped the triggers and rewrote the row. The chain still")
    print("refuses to verify, and names the exact entry that moved.")

    print(f"\nledger written to {LEDGER_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
