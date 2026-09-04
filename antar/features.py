"""Feature construction.

Shared by the baseline bot (day 2) and the uplift model (day 4) so both see
exactly the same inputs. If the baseline lost because it had worse features,
the comparison would prove nothing -- it has to lose on *objective*, not on
information.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import numpy as np
import pandas as pd

from antar.sensorium import FailureRecord
from antar.taxonomy import DeclineClass

DECLINE_CLASSES = [c.value for c in DeclineClass]
METHODS = ["UPI", "CARD", "NETBANKING"]

FEATURE_COLUMNS = (
    [f"class_{c}" for c in DECLINE_CLASSES]
    + [f"method_{m}" for m in METHODS]
    + [
        "log_amount",
        "tenure_days",
        "prior_txns",
        "prior_failures",
        "self_recovery_rate",
        "has_consent",
        "days_to_inflow",
        "hour",
    ]
)


def _days_to_inflow(ts: datetime, inflow_day: int) -> int:
    """How many days until money is expected to land in this account.

    The lever for Class C: an insufficient-funds failure two days before salary
    is a very different proposition from one two days after.
    """
    return (inflow_day - ts.day) % 30


def build_features(records: Sequence[FailureRecord]) -> pd.DataFrame:
    rows = []
    for r in records:
        ts = datetime.fromisoformat(r.ts)
        c = r.customer
        row = {f"class_{k}": 0 for k in DECLINE_CLASSES}
        row[f"class_{r.decline_class.value}"] = 1
        for m in METHODS:
            row[f"method_{m}"] = int(r.method == m)

        row.update(
            log_amount=float(np.log1p(r.amount_paise / 100.0)),
            tenure_days=float(c.get("tenure_days", 0)),
            prior_txns=float(c.get("prior_txns", 0)),
            prior_failures=float(c.get("prior_failures", 0)),
            self_recovery_rate=float(c.get("self_recovery_rate", 0.0)),
            has_consent=float(bool(c.get("has_consent", False))),
            days_to_inflow=float(_days_to_inflow(ts, int(c.get("inflow_day", 1)))),
            hour=float(ts.hour),
        )
        rows.append(row)

    frame = pd.DataFrame(rows, columns=list(FEATURE_COLUMNS))
    return frame.fillna(0.0)


def amounts_by_txn(records: Sequence[FailureRecord]) -> dict[str, int]:
    return {r.txn_id: r.amount_paise for r in records}
