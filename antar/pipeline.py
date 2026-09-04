"""One experiment, end to end.

Simulate, normalise, assign arms, and realise the outcomes a real deployment
would have seen. Day 3, day 4 and the sensitivity sweep all need exactly this,
and the sweep needs it dozens of times, so it lives in one place.

Note the shape of what comes back: `records`, `assignment` and `outcomes` are
what a policy may look at. `truth` is the evaluator's, and is kept in a separate
field with a separate name so that handing a policy the wrong thing has to be
deliberate rather than accidental.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace

from antar.evaluation import TruthBook
from antar.holdout import Assignment, assign
from antar.sensorium import FailureRecord, Sensorium
from antar.simulator.engine import SimulationResult, Simulator

DEFAULT_SALT = "antar-holdout-v1"
DEFAULT_HOLDOUT = 0.10


@dataclass
class ExperimentData:
    # -- policy-visible --------------------------------------------------
    records: list[FailureRecord]
    assignment: Assignment
    outcomes: dict[str, int]

    # -- evaluator-only --------------------------------------------------
    truth: TruthBook
    simulation: SimulationResult

    @property
    def by_id(self) -> dict[str, FailureRecord]:
        return {r.txn_id: r for r in self.records}

    def split_by_time(self, fraction: float) -> tuple[list[FailureRecord], list[FailureRecord]]:
        """Chronological train/eval split.

        Chronological rather than random: a model that trains on the future and
        predicts the past flatters itself, and Qini computed in-sample is not a
        measurement of anything.
        """
        cutoff = (
            self.simulation.start
            + timedelta(days=(self.simulation.end - self.simulation.start).days * fraction)
        ).isoformat()
        early = [r for r in self.records if r.ts < cutoff]
        late = [r for r in self.records if r.ts >= cutoff]
        return early, late

    def amounts(self) -> dict[str, int]:
        return {r.txn_id: r.amount_paise for r in self.records}


def run_experiment(
    cfg: SimpleNamespace,
    *,
    salt: str = DEFAULT_SALT,
    holdout_fraction: float = DEFAULT_HOLDOUT,
) -> ExperimentData:
    simulation = Simulator(cfg).run()
    truth = TruthBook(simulation.events)

    directory = {cid: c.observable for cid, c in simulation.customers.items()}
    records = Sensorium(customer_directory=directory).observe_many(
        ev.to_raw_event() for ev in simulation.events
    )

    txn_ids = [r.txn_id for r in records]
    assignment = assign(txn_ids, salt, holdout_fraction)
    outcomes = truth.realise(txn_ids, treated=assignment.treatment)

    return ExperimentData(
        records=records,
        assignment=assignment,
        outcomes=outcomes,
        truth=truth,
        simulation=simulation,
    )
