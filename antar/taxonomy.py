"""Canonical decline taxonomy.

The six classes are the backbone of ANTAR. They are not six flavours of the same
event: each has a different *self-recovery base rate*, which means each has a
different causal uplift from intervening. Class A looks best on a naive
success-rate metric and is worth almost nothing. Class D looks worst and is
worth the most.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DeclineClass(str, Enum):
    A_TRANSIENT_RAIL = "A_TRANSIENT_RAIL"
    B_AUTH_DROPOFF = "B_AUTH_DROPOFF"
    C_FUNDS = "C_FUNDS"
    D_DEAD_INSTRUMENT = "D_DEAD_INSTRUMENT"
    E_RISK_DECLINE = "E_RISK_DECLINE"
    F_INTENT_LOSS = "F_INTENT_LOSS"


@dataclass(frozen=True)
class ClassMeta:
    label: str
    breaks_at: str
    contactable: bool
    canonical_action: str
    note: str


CLASS_META: dict[DeclineClass, ClassMeta] = {
    DeclineClass.A_TRANSIENT_RAIL: ClassMeta(
        label="Transient rail failure",
        breaks_at="routing",
        contactable=False,
        canonical_action="silent_retry_or_reroute",
        note="PSP timeout, NPCI throttle, issuer down. Customer retries anyway.",
    ),
    DeclineClass.B_AUTH_DROPOFF: ClassMeta(
        label="Authentication drop-off",
        breaks_at="authentication",
        contactable=True,
        canonical_action="same_session_nudge",
        note="OTP / UPI PIN not entered. Value decays within minutes.",
    ),
    DeclineClass.C_FUNDS: ClassMeta(
        label="Insufficient funds",
        breaks_at="bank_decision",
        contactable=True,
        canonical_action="retry_on_inflow_day",
        note="Balance or limit. Timing against inflow is the lever.",
    ),
    DeclineClass.D_DEAD_INSTRUMENT: ClassMeta(
        label="Dead instrument",
        breaks_at="bank_decision",
        contactable=True,
        canonical_action="instrument_update_link",
        note="Expired card, revoked mandate. Cannot ever self-recover.",
    ),
    DeclineClass.E_RISK_DECLINE: ClassMeta(
        label="Issuer risk decline",
        breaks_at="bank_decision",
        contactable=True,
        canonical_action="reroute_alternate_instrument",
        note="Risk or velocity block. Re-hammering the same rail makes it worse.",
    ),
    DeclineClass.F_INTENT_LOSS: ClassMeta(
        label="Intent loss",
        breaks_at="checkout",
        contactable=True,
        canonical_action="reminder_or_offer",
        note="Abandoned before auth. Fatigue cost bites hardest here.",
    ),
}


# Raw gateway reason codes -> canonical class. The Sensorium uses this; keeping
# it beside the taxonomy makes the mapping auditable in one place.
REASON_CODE_MAP: dict[str, DeclineClass] = {
    "gateway_timeout": DeclineClass.A_TRANSIENT_RAIL,
    "psp_unavailable": DeclineClass.A_TRANSIENT_RAIL,
    "npci_throttled": DeclineClass.A_TRANSIENT_RAIL,
    "issuer_unavailable": DeclineClass.A_TRANSIENT_RAIL,
    "beneficiary_bank_down": DeclineClass.A_TRANSIENT_RAIL,
    "otp_not_entered": DeclineClass.B_AUTH_DROPOFF,
    "three_ds_abandoned": DeclineClass.B_AUTH_DROPOFF,
    "upi_app_switch_failed": DeclineClass.B_AUTH_DROPOFF,
    "auth_timeout": DeclineClass.B_AUTH_DROPOFF,
    "insufficient_funds": DeclineClass.C_FUNDS,
    "limit_exceeded": DeclineClass.C_FUNDS,
    "card_expired": DeclineClass.D_DEAD_INSTRUMENT,
    "mandate_revoked": DeclineClass.D_DEAD_INSTRUMENT,
    "mandate_paused": DeclineClass.D_DEAD_INSTRUMENT,
    "account_closed": DeclineClass.D_DEAD_INSTRUMENT,
    "issuer_risk_block": DeclineClass.E_RISK_DECLINE,
    "velocity_rule": DeclineClass.E_RISK_DECLINE,
    "do_not_honour": DeclineClass.E_RISK_DECLINE,
    "checkout_abandoned": DeclineClass.F_INTENT_LOSS,
}


def classify(reason_code: str) -> DeclineClass:
    """Map a raw gateway reason code to its canonical class.

    Unknown codes fail loudly rather than silently bucketing into a default --
    a misclassified failure is a wrong action, and we would rather see it.
    """
    try:
        return REASON_CODE_MAP[reason_code]
    except KeyError as exc:
        raise KeyError(
            f"unmapped reason_code {reason_code!r}; add it to REASON_CODE_MAP"
        ) from exc


def is_contactable(cls: DeclineClass) -> bool:
    return CLASS_META[cls].contactable
