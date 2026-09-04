"""Ledger invariants.

The property-based tests here are the point. A ledger that happens to verify on
the three payloads someone thought to try is not an audit trail. These assert
the invariant across arbitrary generated histories.
"""

from __future__ import annotations

import sqlite3

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from antar.ledger import GENESIS_HASH, Ledger, canonical_json, tamper_for_demo

json_scalars = st.one_of(
    st.text(max_size=40),
    st.integers(min_value=-(10**12), max_value=10**12),
    st.booleans(),
    st.none(),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
)
payloads = st.dictionaries(st.text(min_size=1, max_size=20), json_scalars, max_size=8)


@pytest.fixture
def ledger(tmp_path):
    with Ledger(tmp_path / "test.db", fresh=True) as led:
        yield led


# --------------------------------------------------------------- basics

def test_empty_ledger_verifies(ledger):
    assert ledger.verify().ok
    assert len(ledger) == 0
    assert ledger.head() == GENESIS_HASH


def test_first_entry_links_to_genesis(ledger):
    entry = ledger.append("failure_observed", {"txn_id": "pay_1"})
    assert entry.seq == 1
    assert entry.prev_hash == GENESIS_HASH
    assert ledger.head() == entry.entry_hash


def test_entries_round_trip(ledger):
    ledger.append("a", {"n": 1})
    ledger.append("b", {"n": 2})
    got = list(ledger.entries())
    assert [e.payload["n"] for e in got] == [1, 2]
    assert [e.kind for e in got] == ["a", "b"]
    assert got[1].prev_hash == got[0].entry_hash


def test_kind_filter(ledger):
    ledger.append("keep", {"i": 1})
    ledger.append("drop", {"i": 2})
    ledger.append("keep", {"i": 3})
    assert [e.payload["i"] for e in ledger.entries(kind="keep")] == [1, 3]


def test_empty_kind_rejected(ledger):
    with pytest.raises(ValueError):
        ledger.append("", {"x": 1})


# ------------------------------------------------------- canonical form

def test_canonical_json_is_key_order_independent():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_canonical_json_rejects_unserializable():
    with pytest.raises(TypeError):
        canonical_json({"bad": object()})


# ------------------------------------------------- structural guarantee

def test_update_is_rejected_by_trigger(ledger):
    ledger.append("x", {"v": 1})
    with pytest.raises(sqlite3.IntegrityError):
        ledger._conn.execute("UPDATE ledger SET payload = '{}' WHERE seq = 1")


def test_delete_is_rejected_by_trigger(ledger):
    ledger.append("x", {"v": 1})
    with pytest.raises(sqlite3.IntegrityError):
        ledger._conn.execute("DELETE FROM ledger WHERE seq = 1")


# --------------------------------------------- cryptographic guarantee

@given(entries=st.lists(st.tuples(st.text(min_size=1, max_size=12), payloads), min_size=1, max_size=25))
@settings(max_examples=60, deadline=None)
def test_any_history_verifies(tmp_path_factory, entries):
    """However many entries, in whatever shape, an untouched chain verifies."""
    path = tmp_path_factory.mktemp("chain") / "led.db"
    with Ledger(path, fresh=True) as led:
        for kind, payload in entries:
            led.append(kind, payload)
        result = led.verify()
        assert result.ok, result.reason
        assert result.checked == len(entries)


@given(payload=payloads, forged=payloads)
@settings(max_examples=40, deadline=None)
def test_tampering_always_breaks_the_chain(tmp_path_factory, payload, forged):
    """Rewriting a payload is always caught, whatever the payload."""
    assume(canonical_json(payload) != canonical_json(forged))
    path = tmp_path_factory.mktemp("tamper") / "led.db"
    with Ledger(path, fresh=True) as led:
        led.append("target", payload)
        led.append("after", {"filler": True})
        assert led.verify().ok

    tamper_for_demo(path, seq=1, new_payload=forged)

    with Ledger(path) as led:
        result = led.verify()
        assert not result.ok
        assert result.broken_at == 1


def test_deleting_a_middle_entry_is_caught(tmp_path):
    """Splicing a row out leaves a sequence gap the verifier reports."""
    path = tmp_path / "led.db"
    with Ledger(path, fresh=True) as led:
        for i in range(4):
            led.append("e", {"i": i})

    conn = sqlite3.connect(path)
    conn.execute("DROP TRIGGER IF EXISTS ledger_no_delete")
    conn.execute("DELETE FROM ledger WHERE seq = 2")
    conn.commit()
    conn.close()

    with Ledger(path) as led:
        result = led.verify()
        assert not result.ok
        assert result.broken_at == 3
        assert "sequence gap" in result.reason


def test_reordering_is_caught(tmp_path):
    """The hash commits to seq, so renumbering entries does not survive."""
    path = tmp_path / "led.db"
    with Ledger(path, fresh=True) as led:
        led.append("first", {"i": 1})
        led.append("second", {"i": 2})

    conn = sqlite3.connect(path)
    conn.execute("DROP TRIGGER IF EXISTS ledger_no_update")
    conn.execute("UPDATE ledger SET seq = 99 WHERE seq = 2")
    conn.commit()
    conn.close()

    with Ledger(path) as led:
        assert not led.verify().ok


def test_persistence_across_reopen(tmp_path):
    path = tmp_path / "led.db"
    with Ledger(path, fresh=True) as led:
        led.append("x", {"v": 1})
        head = led.head()
    with Ledger(path) as led:
        assert len(led) == 1
        assert led.head() == head
        assert led.verify().ok
        led.append("y", {"v": 2})
        assert led.verify().ok


# --------------------------------------------------- batch equivalence

def test_append_many_produces_an_identical_chain(tmp_path):
    """The fast path must be the same chain, not a different one.

    Batching exists only to avoid an fsync per row; if it ever produced
    different hashes, verification would silently mean something else.
    """
    from datetime import datetime, timezone

    moment = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    payloads = [{"i": i, "txn": f"pay_{i}"} for i in range(50)]

    with Ledger(tmp_path / "one.db", fresh=True) as a:
        for p in payloads:
            a.append("failure_observed", p, ts=moment)
        sequential = [e.entry_hash for e in a.entries()]

    with Ledger(tmp_path / "two.db", fresh=True) as b:
        assert b.append_many("failure_observed", payloads, ts=moment) == 50
        batched = [e.entry_hash for e in b.entries()]
        assert b.verify().ok

    assert sequential == batched


def test_append_many_continues_an_existing_chain(tmp_path):
    with Ledger(tmp_path / "c.db", fresh=True) as led:
        led.append("start", {"n": 0})
        led.append_many("bulk", [{"n": i} for i in range(1, 4)])
        assert len(led) == 4
        assert [e.seq for e in led.entries()] == [1, 2, 3, 4]
        assert led.verify().ok


def test_append_many_with_no_payloads_is_a_noop(tmp_path):
    with Ledger(tmp_path / "d.db", fresh=True) as led:
        assert led.append_many("bulk", []) == 0
        assert len(led) == 0
        assert led.verify().ok
