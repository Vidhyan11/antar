"""Test-suite guards.

`antar/__init__.py` loads .env so the demo scripts pick up credentials without
anything being typed onto a command line. That is convenient for demos and a
hazard for tests: a future test constructing a bare `Actuator()` would quietly
start making real network calls against someone's Razorpay account.

So the test session runs with those variables blanked. Tests that want to
exercise credential handling pass keys explicitly, which is clearer anyway.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

BLANKED = ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "GEMINI_API_KEY")


@pytest.fixture(autouse=True, scope="session")
def _no_live_credentials_in_tests() -> Iterator[None]:
    """The suite must never touch a network, whatever is in .env."""
    saved = {k: os.environ.pop(k, None) for k in BLANKED}
    yield
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v
