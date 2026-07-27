"""Tamper-evident decision ledger: hash chain for order, Merkle tree for inclusion proofs.

Every state-changing decision appends here. If it is not in the ledger, it did not happen.

Two independent guarantees:

  hash chain    each entry commits to its predecessor (prev_hash -> payload -> entry_hash),
                so removing or reordering history breaks every subsequent link.

  Merkle tree   the log has a single root hash, and any entry can be proved a member of
                that root with an O(log n) audit path. This is what ships inside an Agent
                Evidence Package: a dispute reviewer can verify one decision without
                being handed the entire log.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

GENESIS = "0" * 64


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(payload: Any) -> bytes:
    """Stable bytes for hashing. Sorted keys, tight separators, no incidental whitespace."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _leaf_hash(payload: bytes) -> str:
    # Domain separation between leaves and internal nodes (RFC 6962 style) so an
    # attacker cannot pass an internal node off as a leaf.
    return sha256_hex(b"\x00" + payload)


def _node_hash(left: str, right: str) -> str:
    return sha256_hex(b"\x01" + bytes.fromhex(left) + bytes.fromhex(right))


@dataclass(frozen=True)
class LedgerEntry:
    seq: int
    kind: str
    payload: dict[str, Any]
    prev_hash: str
    entry_hash: str
    leaf_hash: str
    ts: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "kind": self.kind,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
            "leaf_hash": self.leaf_hash,
            "ts": self.ts,
        }


@dataclass(frozen=True)
class InclusionProof:
    """An audit path proving `leaf_hash` sits at `index` under `root`."""

    index: int
    leaf_hash: str
    root: str
    tree_size: int
    path: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (side, hash)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "leaf_hash": self.leaf_hash,
            "root": self.root,
            "tree_size": self.tree_size,
            "path": [{"side": side, "hash": h} for side, h in self.path],
        }

    def verify(self) -> bool:
        """Recompute the root from the leaf and the audit path."""
        current = self.leaf_hash
        for side, sibling in self.path:
            current = (
                _node_hash(sibling, current) if side == "left" else _node_hash(current, sibling)
            )
        return current == self.root


class MerkleLedger:
    """Append-only log. In-memory by design; `store.py` owns durability."""

    def __init__(self, entries: Iterable[LedgerEntry] = ()) -> None:
        self._entries: list[LedgerEntry] = list(entries)

    # -- writing -----------------------------------------------------------------------

    def append(self, kind: str, payload: dict[str, Any], ts: int) -> LedgerEntry:
        prev_hash = self._entries[-1].entry_hash if self._entries else GENESIS
        seq = len(self._entries)
        body = {"seq": seq, "kind": kind, "payload": payload, "ts": ts, "prev_hash": prev_hash}
        raw = canonical(body)
        entry = LedgerEntry(
            seq=seq,
            kind=kind,
            payload=payload,
            prev_hash=prev_hash,
            entry_hash=sha256_hex(raw),
            leaf_hash=_leaf_hash(raw),
            ts=ts,
        )
        self._entries.append(entry)
        return entry

    # -- reading -----------------------------------------------------------------------

    @property
    def entries(self) -> Sequence[LedgerEntry]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, seq: int) -> LedgerEntry | None:
        if 0 <= seq < len(self._entries):
            return self._entries[seq]
        return None

    def filter(self, kind: str | None = None, **match: Any) -> list[LedgerEntry]:
        out = []
        for e in self._entries:
            if kind is not None and e.kind != kind:
                continue
            if all(e.payload.get(k) == v for k, v in match.items()):
                out.append(e)
        return out

    # -- Merkle ------------------------------------------------------------------------

    def _levels(self) -> list[list[str]]:
        """Bottom-up tree levels. Odd nodes are promoted, not duplicated."""
        if not self._entries:
            return [[]]
        levels = [[e.leaf_hash for e in self._entries]]
        while len(levels[-1]) > 1:
            current = levels[-1]
            nxt: list[str] = []
            for i in range(0, len(current) - 1, 2):
                nxt.append(_node_hash(current[i], current[i + 1]))
            if len(current) % 2 == 1:
                nxt.append(current[-1])  # promote the odd tail
            levels.append(nxt)
        return levels

    def root(self) -> str:
        levels = self._levels()
        return levels[-1][0] if levels[-1] else GENESIS

    def inclusion_proof(self, seq: int) -> InclusionProof | None:
        entry = self.get(seq)
        if entry is None:
            return None
        levels = self._levels()
        path: list[tuple[str, str]] = []
        index = seq
        for level in levels[:-1]:
            if index % 2 == 0:
                sibling_index = index + 1
                if sibling_index < len(level):
                    path.append(("right", level[sibling_index]))
                # else: promoted odd tail, no sibling at this level
            else:
                path.append(("left", level[index - 1]))
            index //= 2
        return InclusionProof(
            index=seq,
            leaf_hash=entry.leaf_hash,
            root=self.root(),
            tree_size=len(self._entries),
            path=tuple(path),
        )

    # -- integrity ---------------------------------------------------------------------

    def verify_chain(self) -> tuple[bool, str | None]:
        """Recompute every link. Returns (ok, first_failure_description)."""
        prev = GENESIS
        for i, e in enumerate(self._entries):
            if e.prev_hash != prev:
                return False, f"entry {i}: prev_hash mismatch"
            body = {
                "seq": e.seq,
                "kind": e.kind,
                "payload": e.payload,
                "ts": e.ts,
                "prev_hash": e.prev_hash,
            }
            raw = canonical(body)
            if sha256_hex(raw) != e.entry_hash:
                return False, f"entry {i}: payload does not match entry_hash"
            if _leaf_hash(raw) != e.leaf_hash:
                return False, f"entry {i}: leaf_hash mismatch"
            prev = e.entry_hash
        return True, None
