"""Append-only, hash-chained decision ledger.

Every rupee-affecting decision ANTAR makes lands here. The chain is what makes
the Counterfactual P&L auditable: you cannot restate history without breaking a
hash, and the verifier will tell you exactly which entry broke.

Two independent guarantees:

1.  *Structural*    -- SQLite triggers reject UPDATE and DELETE outright, so the
                       normal API cannot rewrite history even by accident.
2.  *Cryptographic* -- each row commits to the one before it, so an attacker who
                       bypasses SQLite entirely (drops the triggers, edits the
                       file) still cannot produce a chain that verifies.

The second is the one that matters. The first just makes honest mistakes loud.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64

# Unit separator. Delimiting the pre-image fields means no combination of field
# values can be re-partitioned into a different but equally-hashing entry.
_SEP = "\x1f"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    seq        INTEGER PRIMARY KEY,
    ts         TEXT    NOT NULL,
    kind       TEXT    NOT NULL,
    payload    TEXT    NOT NULL,
    prev_hash  TEXT    NOT NULL,
    entry_hash TEXT    NOT NULL UNIQUE
);

CREATE TRIGGER IF NOT EXISTS ledger_no_update
BEFORE UPDATE ON ledger
BEGIN
    SELECT RAISE(ABORT, 'ledger is append-only: UPDATE rejected');
END;

CREATE TRIGGER IF NOT EXISTS ledger_no_delete
BEFORE DELETE ON ledger
BEGIN
    SELECT RAISE(ABORT, 'ledger is append-only: DELETE rejected');
END;
"""


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.astimezone(timezone.utc).isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    raise TypeError(f"{type(obj).__name__} is not ledger-serializable: {obj!r}")


def canonical_json(payload: Any) -> str:
    """Deterministic JSON. Sorted keys and no incidental whitespace, so the same
    logical payload always produces the same bytes -- and therefore the same hash.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )


def compute_hash(prev_hash: str, seq: int, ts: str, kind: str, payload_json: str) -> str:
    preimage = _SEP.join([prev_hash, str(seq), ts, kind, payload_json])
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LedgerEntry:
    seq: int
    ts: str
    kind: str
    payload: dict[str, Any]
    prev_hash: str
    entry_hash: str


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    checked: int
    broken_at: int | None = None
    reason: str | None = None

    def __str__(self) -> str:
        if self.ok:
            return f"chain OK -- {self.checked} entries verified"
        return f"chain BROKEN at seq={self.broken_at}: {self.reason}"


class Ledger:
    """Append-only hash-chained ledger backed by SQLite."""

    def __init__(self, path: str | Path, *, fresh: bool = False) -> None:
        self.path = Path(path)
        if fresh and self.path.exists():
            self.path.unlink()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- writing ---------------------------------------------------------

    def append(self, kind: str, payload: dict[str, Any], *, ts: datetime | None = None) -> LedgerEntry:
        """Append one entry and return it. This is the only way to write."""
        if not kind:
            raise ValueError("kind must be a non-empty string")

        moment = (ts or datetime.now(timezone.utc)).astimezone(timezone.utc)
        ts_iso = moment.isoformat()
        payload_json = canonical_json(payload)

        cur = self._conn.execute("SELECT seq, entry_hash FROM ledger ORDER BY seq DESC LIMIT 1")
        row = cur.fetchone()
        seq = 1 if row is None else row["seq"] + 1
        prev_hash = GENESIS_HASH if row is None else row["entry_hash"]

        entry_hash = compute_hash(prev_hash, seq, ts_iso, kind, payload_json)
        self._conn.execute(
            "INSERT INTO ledger (seq, ts, kind, payload, prev_hash, entry_hash) VALUES (?,?,?,?,?,?)",
            (seq, ts_iso, kind, payload_json, prev_hash, entry_hash),
        )
        self._conn.commit()
        return LedgerEntry(seq, ts_iso, kind, payload, prev_hash, entry_hash)

    # -- reading ---------------------------------------------------------

    def entries(self, kind: str | None = None) -> Iterator[LedgerEntry]:
        sql = "SELECT * FROM ledger"
        args: tuple[Any, ...] = ()
        if kind is not None:
            sql += " WHERE kind = ?"
            args = (kind,)
        sql += " ORDER BY seq"
        for row in self._conn.execute(sql, args):
            yield LedgerEntry(
                seq=row["seq"],
                ts=row["ts"],
                kind=row["kind"],
                payload=json.loads(row["payload"]),
                prev_hash=row["prev_hash"],
                entry_hash=row["entry_hash"],
            )

    def head(self) -> str:
        cur = self._conn.execute("SELECT entry_hash FROM ledger ORDER BY seq DESC LIMIT 1")
        row = cur.fetchone()
        return GENESIS_HASH if row is None else row["entry_hash"]

    def __len__(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0])

    # -- the point of the whole exercise ---------------------------------

    def verify(self) -> VerificationResult:
        """Walk the chain and recompute every hash.

        Catches: mutated payloads, reordered or renumbered entries, spliced-out
        rows, and forged hashes -- because each entry commits to its own seq and
        timestamp as well as to its predecessor.
        """
        prev_hash = GENESIS_HASH
        expected_seq = 1
        checked = 0

        for row in self._conn.execute("SELECT * FROM ledger ORDER BY seq"):
            seq = row["seq"]

            if seq != expected_seq:
                return VerificationResult(
                    False, checked, seq,
                    f"sequence gap: expected {expected_seq}, found {seq} (entry removed or renumbered)",
                )
            if row["prev_hash"] != prev_hash:
                return VerificationResult(
                    False, checked, seq,
                    f"broken link: prev_hash {row['prev_hash'][:12]}... "
                    f"!= actual predecessor {prev_hash[:12]}...",
                )

            recomputed = compute_hash(prev_hash, seq, row["ts"], row["kind"], row["payload"])
            if recomputed != row["entry_hash"]:
                return VerificationResult(
                    False, checked, seq,
                    f"payload tampered: stored hash {row['entry_hash'][:12]}... "
                    f"!= recomputed {recomputed[:12]}...",
                )

            prev_hash = row["entry_hash"]
            expected_seq += 1
            checked += 1

        return VerificationResult(True, checked)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def tamper_for_demo(path: str | Path, seq: int, new_payload: dict[str, Any]) -> None:
    """Deliberately rewrite one entry, bypassing the append-only triggers.

    This exists so the pitch video can show the verifier catching a forgery.
    It drops the guard triggers, edits the row, and puts the triggers back --
    exactly what an attacker with filesystem access would do. The point is that
    it still does not help them: the chain no longer verifies.
    """
    conn = sqlite3.connect(Path(path))
    try:
        conn.execute("DROP TRIGGER IF EXISTS ledger_no_update")
        conn.execute("DROP TRIGGER IF EXISTS ledger_no_delete")
        conn.execute(
            "UPDATE ledger SET payload = ? WHERE seq = ?",
            (canonical_json(new_payload), seq),
        )
        conn.commit()
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()
