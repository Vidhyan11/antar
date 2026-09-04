"""The simulator.

Its job is not realism for its own sake. It is to manufacture *ground truth*:
for every failed payment we know both potential outcomes -- what would have
happened if we intervened, and what would have happened if we did nothing.

Real payment data can never tell you that. You observe one arm and guess at the
other, which is precisely why the industry's recovery numbers are unfalsifiable.
Here we know the answer, so on day 6 we can check whether the uplift model
recovers it, and on day 9 we can sweep the assumptions to show the conclusion
does not depend on them.

Two structural choices worth reading:

*   Potential outcomes are drawn with a SINGLE uniform per transaction, so
    y1 >= y0 always holds. That is the monotonicity (no-defiers) assumption: we
    never simulate a customer whom contacting actively prevents from paying.
*   Latent customer traits drive the outcomes, but the model only ever sees
    noisy observable proxies for them. Without that gap, CATE estimation would
    be trivial and day 6 would prove nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np

from antar.models import Customer, FailureEvent, Outage
from antar.taxonomy import REASON_CODE_MAP, DeclineClass

# Reason codes grouped by the class they map to, so the simulator emits raw
# codes and the Sensorium does the classifying -- same as production.
CODES_BY_CLASS: dict[DeclineClass, list[str]] = {}
for _code, _cls in REASON_CODE_MAP.items():
    CODES_BY_CLASS.setdefault(_cls, []).append(_code)

# Payment attempts are not uniform across the day. Rough Indian consumer shape:
# quiet overnight, a morning rise, a lunch bump, an evening peak.
_HOUR_WEIGHTS = np.array(
    [0.6, 0.4, 0.3, 0.2, 0.2, 0.4, 0.9, 1.6, 2.4, 3.0, 3.4, 3.6,
     3.8, 3.4, 3.0, 3.0, 3.2, 3.8, 4.6, 5.2, 4.8, 3.6, 2.2, 1.2]
)
_HOUR_WEIGHTS = _HOUR_WEIGHTS / _HOUR_WEIGHTS.sum()


@dataclass
class SimulationResult:
    customers: dict[str, Customer]
    outages: list[Outage]
    events: list[FailureEvent]
    attempts: int
    start: datetime
    end: datetime

    @property
    def failure_rate(self) -> float:
        return len(self.events) / self.attempts if self.attempts else 0.0


class Simulator:
    def __init__(self, cfg: SimpleNamespace, rng: np.random.Generator | None = None) -> None:
        self.cfg = cfg
        self.rng = rng or np.random.default_rng(cfg.seed)
        self.start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        self.end = self.start + timedelta(days=cfg.window.days)

    # -- population ------------------------------------------------------

    def build_population(self) -> dict[str, Customer]:
        p = self.cfg.population
        n = p.n_customers
        rng = self.rng

        reliability = rng.beta(*p.reliability_beta, size=n)
        responsiveness = rng.beta(*p.responsiveness_beta, size=n)

        # Tenure correlates with responsiveness: engaged, long-standing customers
        # are the ones a nudge actually moves. The model sees tenure, not the
        # latent trait, and has to work out the relationship itself.
        tenure = np.clip(rng.normal(320 * responsiveness + 110, 160), 5, 1500).astype(int)

        prior_txns = rng.poisson(np.clip(tenure / 45.0, 1, None)).astype(int) + 1
        prior_failures = rng.binomial(prior_txns, 0.16)
        # The observable proxy for reliability -- noisy, but real signal.
        prior_self_rec = rng.binomial(np.maximum(prior_failures, 0), np.clip(reliability, 0.02, 0.98))

        languages = rng.choice(p.languages, size=n, p=p.language_weights)
        consent = rng.random(n) < p.consent_rate
        inflow_day = rng.integers(1, 29, size=n)

        customers: dict[str, Customer] = {}
        for i in range(n):
            cid = f"cust_{i:06d}"
            customers[cid] = Customer(
                customer_id=cid,
                tenure_days=int(tenure[i]),
                inflow_day=int(inflow_day[i]),
                language=str(languages[i]),
                has_consent=bool(consent[i]),
                prior_txns=int(prior_txns[i]),
                prior_failures=int(prior_failures[i]),
                prior_self_recoveries=int(prior_self_rec[i]),
                _reliability=float(reliability[i]),
                _responsiveness=float(responsiveness[i]),
            )
        return customers

    # -- rail incidents --------------------------------------------------

    def schedule_outages(self) -> list[Outage]:
        o = self.cfg.rails.outages
        rng = self.rng
        span_minutes = int((self.end - self.start).total_seconds() // 60)

        outages: list[Outage] = []
        for _ in range(o.count):
            start_offset = int(rng.integers(0, span_minutes - o.max_minutes))
            duration = int(rng.integers(o.min_minutes, o.max_minutes))
            outages.append(
                Outage(
                    issuer=str(rng.choice(self.cfg.rails.issuers)),
                    method=str(rng.choice(["UPI", "CARD", "NETBANKING"], p=self.cfg.rails.method_weights)),
                    start=self.start + timedelta(minutes=start_offset),
                    end=self.start + timedelta(minutes=start_offset + duration),
                )
            )
        return sorted(outages, key=lambda x: x.start)

    # -- the causal core -------------------------------------------------

    def _potential_outcomes(
        self, cls: DeclineClass, cust: Customer, u: float
    ) -> tuple[float, float, int, int]:
        base = getattr(self.cfg.recovery, cls.value)
        h = self.cfg.heterogeneity

        # Reliability moves the untreated baseline.
        rel_mult = 1.0 - h.reliability_span / 2 + h.reliability_span * cust._reliability
        p0 = float(np.clip(base.p0 * rel_mult, 0.0, 0.995))

        # Responsiveness moves the size of the treatment effect, not the baseline.
        gap = max(base.p1 - base.p0, 0.0)
        rsp_mult = 1.0 - h.responsiveness_span / 2 + h.responsiveness_span * cust._responsiveness
        p1 = float(np.clip(p0 + gap * rsp_mult, p0, 0.999))

        # One uniform, two thresholds -> y1 >= y0 by construction.
        return p0, p1, int(u < p0), int(u < p1)

    def _optout_outcomes(self, cust: Customer, u: float) -> tuple[float, float, int, int]:
        """Potential outcomes for opting out, treated versus not.

        Same single-uniform construction as recovery, so being contacted can
        never make someone *less* likely to leave. Customers who already
        tolerate a lot -- long tenure, consent given -- are less annoyed by one
        more message, which is what gives the model something real to find when
        we later charge the agent for the damage it does.
        """
        o = self.cfg.economics.optout
        base = float(o.base_rate)
        tolerance = 0.35 + 0.65 * cust._responsiveness
        contact = float(np.clip(base + o.contact_rate / max(tolerance, 0.2), 0.0, 0.5))
        return base, contact, int(u < base), int(u < contact)

    # -- main loop -------------------------------------------------------

    def run(self) -> SimulationResult:
        cfg = self.cfg
        rng = self.rng
        customers = self.build_population()
        outages = self.schedule_outages()
        cust_ids = list(customers.keys())

        n_attempts = int(cfg.window.days * cfg.window.attempts_per_day)
        class_names = list(cfg.class_mix.__dict__.keys())
        class_probs = np.array([getattr(cfg.class_mix, k) for k in class_names], dtype=float)
        class_probs = class_probs / class_probs.sum()

        # Draw attempt timestamps up front, then sort -- cheaper than stepping a clock.
        days = rng.integers(0, cfg.window.days, size=n_attempts)
        hours = rng.choice(24, size=n_attempts, p=_HOUR_WEIGHTS)
        minutes = rng.integers(0, 60, size=n_attempts)
        order = np.argsort(days * 1440 + hours * 60 + minutes)

        issuers = rng.choice(cfg.rails.issuers, size=n_attempts, p=cfg.rails.issuer_weights)
        methods = rng.choice(cfg.rails.methods, size=n_attempts, p=cfg.rails.method_weights)
        who = rng.choice(cust_ids, size=n_attempts)
        amounts = np.clip(
            rng.lognormal(cfg.amount.lognormal_mu, cfg.amount.lognormal_sigma, size=n_attempts) * 100,
            cfg.amount.min_paise,
            cfg.amount.max_paise,
        ).astype(int)
        fail_roll = rng.random(n_attempts)
        class_roll = rng.random(n_attempts)
        outcome_roll = rng.random(n_attempts)
        optout_roll = rng.random(n_attempts)

        events: list[FailureEvent] = []
        for n, i in enumerate(order):
            ts = self.start + timedelta(days=int(days[i]), hours=int(hours[i]), minutes=int(minutes[i]))
            issuer, method = str(issuers[i]), str(methods[i])

            in_outage = any(o.covers(ts, issuer, method) for o in outages)
            fail_p = cfg.rails.outages.failure_rate_during if in_outage else cfg.window.base_failure_rate
            if fail_roll[i] >= fail_p:
                continue

            if in_outage and class_roll[i] < cfg.rails.outages.class_a_share:
                cls = DeclineClass.A_TRANSIENT_RAIL
            else:
                cls = DeclineClass(str(rng.choice(class_names, p=class_probs)))

            reason_code = str(rng.choice(CODES_BY_CLASS[cls]))
            cust = customers[str(who[i])]
            p0, p1, y0, y1 = self._potential_outcomes(cls, cust, float(outcome_roll[i]))
            q0, q1, o0, o1 = self._optout_outcomes(cust, float(optout_roll[i]))

            events.append(
                FailureEvent(
                    txn_id=f"pay_{n:08d}",
                    customer_id=cust.customer_id,
                    ts=ts,
                    amount_paise=int(amounts[i]),
                    issuer=issuer,
                    method=method,
                    reason_code=reason_code,
                    decline_class=cls,
                    in_outage=in_outage,
                    p0=p0,
                    p1=p1,
                    y0=y0,
                    y1=y1,
                    q0=q0,
                    q1=q1,
                    o0=o0,
                    o1=o1,
                )
            )

        return SimulationResult(
            customers=customers,
            outages=outages,
            events=events,
            attempts=n_attempts,
            start=self.start,
            end=self.end,
        )
