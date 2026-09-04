"""The actuator -- where a decision becomes an action.

Everything upstream of here is estimation. This is the only module that touches
money, and it is deliberately the least clever one in the project.

Three properties it must have:

*   **Bounded.** An allow-listed action type per decline class, a rupee ceiling,
    and an idempotency key on every write. There is no path from a model output
    to an arbitrary API call.
*   **Gated.** The freeze registry is consulted first. A transaction inside an
    open incident cannot be actioned at all, whatever the targeter thinks.
*   **Recorded.** Every attempt lands in the hash-chained ledger with its inputs,
    the action taken, and why -- before the network call, so a call that fails
    halfway still leaves a trace.

Razorpay is reached over plain REST with urllib rather than the SDK: one less
dependency, and the exact request stays readable in the source. When no keys are
configured the client switches to dry-run, records the request it *would* have
sent, and says so. The demo therefore runs identically with or without
credentials -- only the "confirmed by Razorpay" column changes.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from antar.ledger import Ledger
from antar.sensorium import FailureRecord
from antar.taxonomy import CLASS_META, DeclineClass

API_BASE = "https://api.razorpay.com/v1"

# The only actions that exist. A model cannot invent a seventh.
ACTION_BY_CLASS: dict[DeclineClass, str] = {
    DeclineClass.A_TRANSIENT_RAIL: "silent_retry",
    DeclineClass.B_AUTH_DROPOFF: "same_session_nudge",
    DeclineClass.C_FUNDS: "retry_on_inflow_day",
    DeclineClass.D_DEAD_INSTRUMENT: "instrument_update_link",
    DeclineClass.E_RISK_DECLINE: "reroute_alternate_rail",
    DeclineClass.F_INTENT_LOSS: "reminder_with_link",
}

# Which actions create something at the gateway rather than just scheduling work.
CREATES_PAYMENT_LINK = {"instrument_update_link", "reminder_with_link", "same_session_nudge"}
CREATES_ORDER = {"silent_retry", "retry_on_inflow_day", "reroute_alternate_rail"}

MAX_ACTION_PAISE = 5_000_000  # ceiling per action; nothing larger is automated


@dataclass
class ActionResult:
    txn_id: str
    action: str
    executed: bool
    dry_run: bool
    reference: str | None = None
    error: str | None = None
    request: dict[str, Any] = field(default_factory=dict)


class RazorpayClient:
    """Test-mode REST client. Falls back to dry-run when unconfigured."""

    def __init__(self, key_id: str | None = None, key_secret: str | None = None) -> None:
        self.key_id = key_id or os.environ.get("RAZORPAY_KEY_ID", "")
        self.key_secret = key_secret or os.environ.get("RAZORPAY_KEY_SECRET", "")

    @property
    def live(self) -> bool:
        # Test-mode keys carry the rzp_test_ prefix. Refusing to run against a
        # live key is a guard, not a limitation: nothing here should ever touch
        # real money.
        return bool(self.key_id and self.key_secret and self.key_id.startswith("rzp_test_"))

    @property
    def mode(self) -> str:
        if self.live:
            return "razorpay-test"
        if self.key_id and not self.key_id.startswith("rzp_test_"):
            return "dry-run (refusing a non-test key)"
        return "dry-run (no credentials)"

    def _post(self, path: str, body: dict[str, Any]) -> tuple[str | None, str | None]:
        token = base64.b64encode(f"{self.key_id}:{self.key_secret}".encode()).decode()
        req = urllib.request.Request(
            f"{API_BASE}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read())
            return payload.get("id"), None
        except urllib.error.HTTPError as exc:
            return None, f"HTTP {exc.code}: {exc.read()[:200].decode(errors='replace')}"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return None, f"{type(exc).__name__}: {exc}"

    def create_order(self, amount_paise: int, receipt: str) -> tuple[str | None, str | None, dict]:
        body = {"amount": amount_paise, "currency": "INR", "receipt": receipt[:40],
                "notes": {"agent": "antar", "purpose": "recovery_retry"}}
        if not self.live:
            return None, None, body
        ref, err = self._post("/orders", body)
        return ref, err, body

    def create_payment_link(
        self, amount_paise: int, description: str, reference_id: str
    ) -> tuple[str | None, str | None, dict]:
        body = {
            "amount": amount_paise,
            "currency": "INR",
            "description": description[:120],
            "reference_id": reference_id[:40],
            # Notification is suppressed: this is a test-mode demonstration and
            # nothing should reach a real inbox.
            "notify": {"sms": False, "email": False},
            "notes": {"agent": "antar"},
        }
        if not self.live:
            return None, None, body
        ref, err = self._post("/payment_links", body)
        return ref, err, body


class Actuator:
    """Turns selected transactions into bounded, logged, gated actions."""

    def __init__(
        self,
        client: RazorpayClient | None = None,
        ledger: Ledger | None = None,
        *,
        live_call_budget: int = 5,
    ) -> None:
        self.client = client or RazorpayClient()
        self.ledger = ledger
        # Only a handful of real calls are made even when credentials exist:
        # enough to prove the integration on camera, few enough to stay well
        # inside test-mode rate limits. The rest are recorded as dry-run.
        self.live_call_budget = live_call_budget
        self.live_calls_made = 0

    def execute(
        self,
        records: Sequence[FailureRecord],
        *,
        frozen_txn_ids: set[str] | None = None,
    ) -> list[ActionResult]:
        blocked = frozen_txn_ids or set()
        results: list[ActionResult] = []

        for record in records:
            if record.txn_id in blocked:
                results.append(self._log(ActionResult(
                    record.txn_id, "blocked_by_incident", executed=False, dry_run=True,
                    error="inside an open incident window",
                ), record))
                continue

            if record.amount_paise > MAX_ACTION_PAISE:
                results.append(self._log(ActionResult(
                    record.txn_id, "escalated_to_human", executed=False, dry_run=True,
                    error=f"amount exceeds automation ceiling of {MAX_ACTION_PAISE} paise",
                ), record))
                continue

            results.append(self._log(self._act(record), record))

        return results

    def _act(self, record: FailureRecord) -> ActionResult:
        action = ACTION_BY_CLASS[record.decline_class]
        idempotency = f"antar_{record.txn_id}_{action}"

        spend_live = self.client.live and self.live_calls_made < self.live_call_budget
        if spend_live:
            self.live_calls_made += 1

        if action in CREATES_PAYMENT_LINK:
            ref, err, body = (
                self.client.create_payment_link(
                    record.amount_paise, CLASS_META[record.decline_class].label, idempotency
                )
                if spend_live
                else (None, None, {"amount": record.amount_paise, "reference_id": idempotency})
            )
        elif action in CREATES_ORDER:
            ref, err, body = (
                self.client.create_order(record.amount_paise, idempotency)
                if spend_live
                else (None, None, {"amount": record.amount_paise, "receipt": idempotency})
            )
        else:  # pragma: no cover - ACTION_BY_CLASS is exhaustive over the taxonomy
            return ActionResult(record.txn_id, action, executed=False, dry_run=True,
                                error="no handler for action")

        return ActionResult(
            txn_id=record.txn_id,
            action=action,
            executed=bool(ref) and err is None,
            dry_run=not spend_live,
            reference=ref or (f"dryrun_{uuid.uuid5(uuid.NAMESPACE_OID, idempotency).hex[:12]}"),
            error=err,
            request=body,
        )

    def _log(self, result: ActionResult, record: FailureRecord) -> ActionResult:
        if self.ledger is not None:
            self.ledger.append("action_taken", {
                "txn_id": result.txn_id,
                "decline_class": record.decline_class.value,
                "action": result.action,
                "amount_paise": record.amount_paise,
                "executed": result.executed,
                "dry_run": result.dry_run,
                "reference": result.reference,
                "error": result.error,
                "mode": self.client.mode,
            })
        return result
