"""Verify Razorpay test-mode credentials, and create one real Payment Link.

Run this after filling in .env. It makes exactly one write call, so the link it
creates is the one you can point at in the Razorpay dashboard on camera.

Nothing here can touch real money: the client refuses any key that does not
carry the rzp_test_ prefix, and notification is suppressed on the link so
nothing reaches a real inbox.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from antar.actuator import RazorpayClient
from antar.env import redact

RULE = "-" * 70


def main() -> int:
    client = RazorpayClient()

    print(RULE)
    print("  RAZORPAY CREDENTIALS")
    print(RULE)
    print(f"key id     : {redact(client.key_id)}")
    print(f"key secret : {redact(client.key_secret, keep=0)}")
    print(f"mode       : {client.mode}")

    if not client.key_id:
        print("\nNo credentials found.")
        print("  1. cp .env.example .env")
        print("  2. paste your test keys into .env")
        print("  3. run this again")
        return 1

    if not client.key_id.startswith("rzp_test_"):
        print("\nREFUSED: that is not a test-mode key.")
        print("Nothing in this project should ever touch live money. Switch the")
        print("Razorpay dashboard to Test Mode and generate a key there.")
        return 1

    print(f"\n{RULE}")
    print("  LIVE CALL: creating one payment link")
    print(RULE)

    ref, err, body = client.create_payment_link(
        amount_paise=49_900,
        description="ANTAR recovery - instrument update",
        reference_id="antar_smoke_test_001",
    )

    print(f"request : amount={body['amount']} paise, ref={body['reference_id']}")
    if err:
        print(f"\nFAILED  : {err}")
        print("\nCommon causes: the secret was pasted with a trailing space, or the")
        print("key was regenerated in the dashboard after you copied it.")
        return 1

    print(f"created : {ref}")
    print("\nOpen dashboard.razorpay.com -> Payment Links (Test Mode) and you should")
    print("see it there. That is the shot: Razorpay's own product confirming the")
    print("agent really executed, rather than us claiming it did.")
    print("\nNow re-run `python scripts/run_day5.py` -- the mode line will read")
    print("razorpay-test and the 'confirmed by API' count will be non-zero.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
