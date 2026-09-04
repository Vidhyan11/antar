"""Sensorium -- the intake layer.

Takes raw gateway events, classifies them against the canonical taxonomy,
enriches them with what we already know about the customer, and writes the
result to the ledger. Everything downstream reads FailureRecords.

The contract worth stating out loud: a FailureRecord contains *only what a real
deployment could observe at the moment the failure lands*. No outcome, no
counterfactual. That is enforced by construction -- `observe()` accepts a
RawGatewayEvent, and a RawGatewayEvent has nowhere to put an answer.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from antar.ledger import Ledger
from antar.models import RawGatewayEvent
from antar.taxonomy import CLASS_META, DeclineClass, classify


@dataclass(frozen=True)
class FailureRecord:
    """A normalised failure, ready for a policy to reason about."""

    txn_id: str
    customer_id: str
    ts: str
    amount_paise: int
    issuer: str
    method: str
    reason_code: str
    decline_class: DeclineClass
    contactable: bool
    customer: dict[str, Any] = field(default_factory=dict)

    @property
    def amount_inr(self) -> float:
        return self.amount_paise / 100.0

    def to_payload(self) -> dict[str, Any]:
        return {
            "txn_id": self.txn_id,
            "customer_id": self.customer_id,
            "ts": self.ts,
            "amount_paise": self.amount_paise,
            "issuer": self.issuer,
            "method": self.method,
            "reason_code": self.reason_code,
            "decline_class": self.decline_class.value,
            "contactable": self.contactable,
        }


class UnknownCustomer(KeyError):
    """Raised when a failure arrives for a customer we have no record of."""


class Sensorium:
    """Normalises raw gateway failures into FailureRecords, and logs them."""

    def __init__(
        self,
        ledger: Ledger | None = None,
        customer_directory: dict[str, dict[str, Any]] | None = None,
        *,
        strict_customers: bool = True,
    ) -> None:
        self.ledger = ledger
        self.directory = customer_directory or {}
        self.strict_customers = strict_customers
        self.unknown_customers: set[str] = set()

    def observe(self, raw: RawGatewayEvent) -> FailureRecord:
        decline_class = classify(raw.reason_code)

        try:
            customer = self.directory[raw.customer_id]
        except KeyError:
            # A failure for someone we have never seen is a real production
            # case (guest checkout, first transaction). We record it rather
            # than dropping it, but a policy that needs history will score it
            # conservatively because the features are absent.
            if self.strict_customers:
                raise UnknownCustomer(raw.customer_id) from None
            self.unknown_customers.add(raw.customer_id)
            customer = {}

        record = FailureRecord(
            txn_id=raw.txn_id,
            customer_id=raw.customer_id,
            ts=raw.ts,
            amount_paise=raw.amount_paise,
            issuer=raw.issuer,
            method=raw.method,
            reason_code=raw.reason_code,
            decline_class=decline_class,
            contactable=CLASS_META[decline_class].contactable,
            customer=customer,
        )

        if self.ledger is not None:
            self.ledger.append("failure_observed", record.to_payload())

        return record

    def observe_many(self, raws: Iterable[RawGatewayEvent]) -> list[FailureRecord]:
        return [self.observe(raw) for raw in raws]
