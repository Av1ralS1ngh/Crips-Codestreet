"""SQLite persistence.

SQLite rather than Postgres+Redis is a deliberate substitution: zero external services
means zero setup failures on stage, and the revocation kill switch becomes a literal,
inspectable claim — "exactly one row was written" is a statement about a real table we
can show. Everything durable goes through this module, so swapping in Postgres is a
driver change rather than a rewrite.

The stateful constraints (cumulative budget, velocity) are evaluated against this store.
They are therefore NOT offline-verifiable, which we disclose rather than hide.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS mandates (
    mandate_id     TEXT PRIMARY KEY,
    root_id        TEXT NOT NULL,
    parent_id      TEXT,
    holder         TEXT NOT NULL,
    depth          INTEGER NOT NULL,
    scope_json     TEXT NOT NULL,
    serialized     TEXT NOT NULL,
    created_at     INTEGER NOT NULL,
    entailment_ms  REAL
);
CREATE INDEX IF NOT EXISTS idx_mandates_root ON mandates(root_id);
CREATE INDEX IF NOT EXISTS idx_mandates_parent ON mandates(parent_id);

CREATE TABLE IF NOT EXISTS root_keys (
    root_id  TEXT PRIMARY KEY,
    key      TEXT NOT NULL
);

-- The kill switch. One row here fails every descendant credential closed.
CREATE TABLE IF NOT EXISTS revocations (
    root_id     TEXT PRIMARY KEY,
    revoked_at  INTEGER NOT NULL,
    cause       TEXT NOT NULL,
    actor       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger (
    seq         INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    prev_hash   TEXT NOT NULL,
    entry_hash  TEXT NOT NULL,
    leaf_hash   TEXT NOT NULL,
    ts          INTEGER NOT NULL
);

-- Authorized spend, for cumulative-budget and velocity evaluation.
CREATE TABLE IF NOT EXISTS authorizations (
    txn_id      TEXT PRIMARY KEY,
    root_id     TEXT NOT NULL,
    mandate_id  TEXT NOT NULL,
    amount      INTEGER NOT NULL,
    merchant    TEXT NOT NULL,
    mcc         INTEGER NOT NULL,
    ts          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_root_ts ON authorizations(root_id, ts);

CREATE TABLE IF NOT EXISTS operators (
    operator_id  TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    registered_at INTEGER NOT NULL,
    metadata     TEXT NOT NULL DEFAULT '{}'
);

-- One row per behavioural signal; the risk score is derived, never stored stale.
CREATE TABLE IF NOT EXISTS operator_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_id  TEXT NOT NULL,
    event        TEXT NOT NULL,
    ts           INTEGER NOT NULL,
    detail       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_opevents ON operator_events(operator_id);
"""


@dataclass(frozen=True)
class AuthorizationRow:
    txn_id: str
    root_id: str
    mandate_id: str
    amount: int
    merchant: str
    mcc: int
    ts: int


class Store:
    """Thread-safe SQLite wrapper. Defaults to an in-memory database."""

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- mandates ----------------------------------------------------------------------

    def put_mandate(self, m: Any) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO mandates
                   (mandate_id, root_id, parent_id, holder, depth, scope_json,
                    serialized, created_at, entailment_ms)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    m.mandate_id,
                    m.root_id,
                    m.parent_id,
                    m.holder,
                    m.depth,
                    m.scope.to_json(),
                    m.serialized,
                    m.created_at,
                    m.entailment_ms,
                ),
            )
            self._conn.commit()

    def get_mandate_row(self, mandate_id: str) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM mandates WHERE mandate_id = ?", (mandate_id,)
            )
            return cur.fetchone()

    def mandates_for_root(self, root_id: str) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM mandates WHERE root_id = ? ORDER BY depth, created_at",
                (root_id,),
            )
            return list(cur.fetchall())

    def all_mandates(self) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM mandates ORDER BY created_at")
            return list(cur.fetchall())

    def children_of(self, mandate_id: str) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM mandates WHERE parent_id = ?", (mandate_id,)
            )
            return list(cur.fetchall())

    # -- root keys ---------------------------------------------------------------------

    def put_root_key(self, root_id: str, key: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO root_keys (root_id, key) VALUES (?,?)", (root_id, key)
            )
            self._conn.commit()

    def all_root_keys(self) -> dict[str, str]:
        with self._lock:
            cur = self._conn.execute("SELECT root_id, key FROM root_keys")
            return {r["root_id"]: r["key"] for r in cur.fetchall()}

    # -- revocation --------------------------------------------------------------------

    def revoke(self, root_id: str, revoked_at: int, cause: str, actor: str) -> int:
        """Flip one row. Returns rows written — the number we show on stage."""
        with self._lock:
            cur = self._conn.execute(
                """INSERT OR IGNORE INTO revocations (root_id, revoked_at, cause, actor)
                   VALUES (?,?,?,?)""",
                (root_id, revoked_at, cause, actor),
            )
            self._conn.commit()
            return cur.rowcount

    def is_revoked(self, root_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM revocations WHERE root_id = ?", (root_id,)
            )
            return cur.fetchone() is not None

    def revocation(self, root_id: str) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM revocations WHERE root_id = ?", (root_id,)
            )
            return cur.fetchone()

    def revocation_count(self) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) AS n FROM revocations")
            return int(cur.fetchone()["n"])

    # -- ledger ------------------------------------------------------------------------

    def append_ledger(self, entry: Any) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO ledger
                   (seq, kind, payload, prev_hash, entry_hash, leaf_hash, ts)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    entry.seq,
                    entry.kind,
                    json.dumps(entry.payload, sort_keys=True, separators=(",", ":")),
                    entry.prev_hash,
                    entry.entry_hash,
                    entry.leaf_hash,
                    entry.ts,
                ),
            )
            self._conn.commit()

    def load_ledger(self) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM ledger ORDER BY seq")
            return [
                {
                    "seq": r["seq"],
                    "kind": r["kind"],
                    "payload": json.loads(r["payload"]),
                    "prev_hash": r["prev_hash"],
                    "entry_hash": r["entry_hash"],
                    "leaf_hash": r["leaf_hash"],
                    "ts": r["ts"],
                }
                for r in cur.fetchall()
            ]

    # -- authorizations (stateful constraint inputs) -----------------------------------

    def record_authorization(self, row: AuthorizationRow) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO authorizations
                   (txn_id, root_id, mandate_id, amount, merchant, mcc, ts)
                   VALUES (?,?,?,?,?,?,?)""",
                (row.txn_id, row.root_id, row.mandate_id, row.amount, row.merchant, row.mcc, row.ts),
            )
            self._conn.commit()

    def cumulative_spend(self, root_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM authorizations WHERE root_id = ?",
                (root_id,),
            )
            return int(cur.fetchone()["total"])

    def velocity_count(self, root_id: str, window_s: int, now: int) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) AS n FROM authorizations WHERE root_id = ? AND ts > ?",
                (root_id, now - window_s),
            )
            return int(cur.fetchone()["n"])

    def authorizations_for_root(self, root_id: str) -> list[AuthorizationRow]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM authorizations WHERE root_id = ? ORDER BY ts", (root_id,)
            )
            return [
                AuthorizationRow(
                    txn_id=r["txn_id"],
                    root_id=r["root_id"],
                    mandate_id=r["mandate_id"],
                    amount=r["amount"],
                    merchant=r["merchant"],
                    mcc=r["mcc"],
                    ts=r["ts"],
                )
                for r in cur.fetchall()
            ]

    # -- operators ---------------------------------------------------------------------

    def put_operator(
        self, operator_id: str, name: str, registered_at: int, metadata: dict[str, Any] | None = None
    ) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO operators (operator_id, name, registered_at, metadata)
                   VALUES (?,?,?,?)""",
                (operator_id, name, registered_at, json.dumps(metadata or {}, sort_keys=True)),
            )
            self._conn.commit()

    def get_operator(self, operator_id: str) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM operators WHERE operator_id = ?", (operator_id,)
            )
            return cur.fetchone()

    def all_operators(self) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM operators ORDER BY operator_id")
            return list(cur.fetchall())

    def record_operator_event(
        self, operator_id: str, event: str, ts: int, detail: dict[str, Any] | None = None
    ) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO operator_events (operator_id, event, ts, detail)
                   VALUES (?,?,?,?)""",
                (operator_id, event, ts, json.dumps(detail or {}, sort_keys=True)),
            )
            self._conn.commit()

    def operator_events(self, operator_id: str) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM operator_events WHERE operator_id = ? ORDER BY ts", (operator_id,)
            )
            return [
                {
                    "event": r["event"],
                    "ts": r["ts"],
                    "detail": json.loads(r["detail"]),
                }
                for r in cur.fetchall()
            ]

    def operator_event_counts(self, operator_id: str) -> dict[str, int]:
        with self._lock:
            cur = self._conn.execute(
                """SELECT event, COUNT(*) AS n FROM operator_events
                   WHERE operator_id = ? GROUP BY event""",
                (operator_id,),
            )
            return {r["event"]: int(r["n"]) for r in cur.fetchall()}

    def reset(self, tables: Iterable[str] | None = None) -> None:
        """Wipe state between demo runs so every rehearsal starts identical."""
        names = list(tables) if tables else [
            "mandates",
            "root_keys",
            "revocations",
            "ledger",
            "authorizations",
            "operators",
            "operator_events",
        ]
        with self._lock:
            for t in names:
                self._conn.execute(f"DELETE FROM {t}")
            self._conn.commit()
