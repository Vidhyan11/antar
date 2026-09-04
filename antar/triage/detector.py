"""Correlated-failure detection.

Every recovery engine on the market treats a failed payment as an event about a
customer. Sometimes it is. But when an issuer degrades, four thousand failures
are *one* event about a bank, and answering them with four thousand messages
teaches four thousand people that the merchant is broken -- while the actual fix,
waiting or re-routing, goes untaken.

This is the gate that catches that, and it runs *before* anything is allowed to
contact anyone.

The statistic is deliberately simple: for each rail we ask how surprising the
current hour's failure count is under a Poisson model. A reviewer can check a
Poisson tail by hand. Nobody can audit an autoencoder, and "the anomaly model
said so" is exactly the unaccountable AI this project argues against.

Two things had to be right for it to work at all, and both were found by running
it rather than by reasoning about it:

*   **Bucket width.** A rail only becomes observable once a bucket holds enough
    traffic for its quiet count to be stable. At fifteen minutes the busiest rail
    here held under five expected failures even mid-outage -- nothing was
    genuinely detectable, and the one detection we got was a lucky draw. Hourly
    buckets clear the floor. The cost is time resolution: a freeze rounds out to
    the hour, erring towards staying quiet slightly longer than the outage. That
    is the right direction to err in.

*   **Seasonality.** Payment traffic swings roughly 26x between 4am and 8pm. A
    baseline averaged across all hours makes every busy evening look like an
    incident -- an early version raised nineteen episodes for four real outages.
    The baseline therefore compares each hour against *the same hour on previous
    days*, which is the only comparison that holds traffic constant.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from scipy.stats import poisson

from antar.sensorium import FailureRecord
from antar.taxonomy import DeclineClass

BUCKET_MINUTES = 60


@dataclass
class FailureCluster:
    """An hour on one rail holding more failures than that hour should."""

    issuer: str
    method: str
    bucket_start: datetime
    bucket_minutes: int
    observed: int
    expected: float
    p_value: float
    class_a_share: float
    history_days: int = 0
    txn_ids: list[str] = field(default_factory=list)

    @property
    def bucket_end(self) -> datetime:
        return self.bucket_start + timedelta(minutes=self.bucket_minutes)

    @property
    def lift(self) -> float:
        return self.observed / self.expected if self.expected > 0 else float("inf")

    def summary(self) -> dict[str, object]:
        """The compact view handed to the agent. No raw transactions, no PII."""
        return {
            "issuer": self.issuer,
            "method": self.method,
            "window_start": self.bucket_start.isoformat(),
            "window_minutes": self.bucket_minutes,
            "failures_observed": self.observed,
            "failures_expected": round(self.expected, 2),
            "lift_vs_baseline": round(self.lift, 1),
            "poisson_p_value": float(f"{self.p_value:.3g}"),
            "share_transient_rail_class": round(self.class_a_share, 3),
            "distinct_customers": len(set(self.txn_ids)),
        }


def detect_clusters(
    records: Sequence[FailureRecord],
    *,
    baseline_days: int = 10,
    min_history_days: int = 4,
    p_threshold: float = 1e-4,
    min_observed: int = 8,
    min_lift: float = 2.5,
) -> list[FailureCluster]:
    """Find rail-hours whose failure count is not plausibly ordinary traffic.

    The baseline for a given hour is the mean count in *that same hour* on up to
    `baseline_days` previous days for the same rail. Comparing an 8pm bucket
    against other 8pm buckets is what stops the diurnal cycle reading as a
    permanent incident.

    `min_observed` and `min_lift` are operational floors on top of the
    statistical test: a Poisson tail is easy to breach at tiny counts, and going
    from 0.4 expected to 3 observed is improbable but not actionable.
    """
    buckets: dict[tuple[str, str, datetime], list[FailureRecord]] = {}
    for r in records:
        ts = datetime.fromisoformat(r.ts)
        buckets.setdefault(
            (r.issuer, r.method, ts.replace(minute=0, second=0, microsecond=0)), []
        ).append(r)

    counts = {k: len(v) for k, v in buckets.items()}

    # (issuer, method, hour-of-day) -> the dated buckets sharing that slot
    slots: dict[tuple[str, str, int], list[datetime]] = {}
    for issuer, method, start in buckets:
        slots.setdefault((issuer, method, start.hour), []).append(start)

    clusters: list[FailureCluster] = []
    for (issuer, method, _hour), starts in slots.items():
        starts.sort()
        for i, start in enumerate(starts):
            history = [counts[(issuer, method, s)] for s in starts[max(0, i - baseline_days): i]]
            if len(history) < min_history_days:
                continue

            observed = counts[(issuer, method, start)]
            expected = max(sum(history) / len(history), 0.5)

            if observed < min_observed or observed < expected * min_lift:
                continue

            p = float(poisson.sf(observed - 1, expected))
            if p >= p_threshold:
                continue

            rows = buckets[(issuer, method, start)]
            a_share = sum(
                1 for r in rows if r.decline_class is DeclineClass.A_TRANSIENT_RAIL
            ) / len(rows)

            clusters.append(
                FailureCluster(
                    issuer=issuer,
                    method=method,
                    bucket_start=start,
                    bucket_minutes=BUCKET_MINUTES,
                    observed=observed,
                    expected=expected,
                    p_value=p,
                    class_a_share=a_share,
                    history_days=len(history),
                    txn_ids=[r.txn_id for r in rows],
                )
            )

    return sorted(clusters, key=lambda c: c.bucket_start)


def merge_adjacent(
    clusters: Sequence[FailureCluster], *, max_gap_minutes: int = 60
) -> list[list[FailureCluster]]:
    """Group consecutive breaching hours on the same rail into one episode.

    A three-hour outage produces three breaching buckets. Those are one
    incident, and reporting them as three would be its own kind of alert spam.
    """
    by_rail: dict[tuple[str, str], list[FailureCluster]] = {}
    for c in clusters:
        by_rail.setdefault((c.issuer, c.method), []).append(c)

    episodes: list[list[FailureCluster]] = []
    for rail_clusters in by_rail.values():
        rail_clusters.sort(key=lambda c: c.bucket_start)
        current: list[FailureCluster] = []
        for c in rail_clusters:
            if current and (c.bucket_start - current[-1].bucket_end) <= timedelta(
                minutes=max_gap_minutes
            ):
                current.append(c)
            else:
                if current:
                    episodes.append(current)
                current = [c]
        if current:
            episodes.append(current)

    return sorted(episodes, key=lambda e: e[0].bucket_start)
