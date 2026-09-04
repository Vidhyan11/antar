"""Arm assignment.

The control group is the product. Everything ANTAR claims rests on there being
a set of failures it deliberately did not touch, so the assignment mechanism has
to be three things at once:

*   **Random** -- otherwise the comparison is confounded and the whole exercise
    collapses back into the attribution guesswork we are trying to replace.
*   **Deterministic** -- the same transaction must land in the same arm on every
    replay, or nothing is reproducible.
*   **Auditable** -- anyone holding the salt can recompute any transaction's arm
    from its id alone and check we did not move someone after the fact.

A keyed hash gives all three. There is no random number generator state to
persist, no assignment table to trust, and no way to quietly re-roll a
transaction that landed inconveniently.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

_MAX_64 = float(1 << 64)


class Arm(str, Enum):
    TREATMENT = "treatment"
    CONTROL = "control"


def uniform_hash(txn_id: str, salt: str) -> float:
    """Map a transaction id to a uniform draw in [0, 1) via SHA-256."""
    digest = hashlib.sha256(f"{salt}\x1f{txn_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / _MAX_64


def assign_arm(txn_id: str, salt: str, holdout_fraction: float) -> Arm:
    """Assign one transaction. Reproducible from (txn_id, salt) alone."""
    if not 0.0 < holdout_fraction < 1.0:
        raise ValueError("holdout_fraction must be strictly between 0 and 1")
    return Arm.CONTROL if uniform_hash(txn_id, salt) < holdout_fraction else Arm.TREATMENT


@dataclass(frozen=True)
class Assignment:
    arms: dict[str, Arm]
    salt: str
    holdout_fraction: float

    @property
    def treatment(self) -> set[str]:
        return {t for t, a in self.arms.items() if a is Arm.TREATMENT}

    @property
    def control(self) -> set[str]:
        return {t for t, a in self.arms.items() if a is Arm.CONTROL}

    @property
    def realised_holdout(self) -> float:
        return len(self.control) / len(self.arms) if self.arms else 0.0

    def verify(self) -> bool:
        """Recompute every assignment from scratch. An auditor's entry point."""
        return all(
            assign_arm(t, self.salt, self.holdout_fraction) is arm
            for t, arm in self.arms.items()
        )


def assign(txn_ids: Sequence[str], salt: str, holdout_fraction: float) -> Assignment:
    return Assignment(
        arms={t: assign_arm(t, salt, holdout_fraction) for t in txn_ids},
        salt=salt,
        holdout_fraction=holdout_fraction,
    )


def balance_report(
    assignment: Assignment, strata: dict[str, str]
) -> dict[str, tuple[int, int, float]]:
    """Realised holdout share per stratum.

    Hash assignment is Bernoulli, not block-randomised, so strata will not come
    out exactly at the nominal rate. Reporting the realised share per stratum is
    how you notice if a small stratum ended up badly imbalanced -- which is a
    power problem, not a bias problem, but is worth seeing before trusting a
    subgroup estimate.
    """
    out: dict[str, list[int]] = {}
    for txn_id, arm in assignment.arms.items():
        key = strata.get(txn_id, "unknown")
        counts = out.setdefault(key, [0, 0])
        counts[0 if arm is Arm.TREATMENT else 1] += 1
    return {
        k: (t, c, c / (t + c) if (t + c) else 0.0)
        for k, (t, c) in sorted(out.items())
    }
