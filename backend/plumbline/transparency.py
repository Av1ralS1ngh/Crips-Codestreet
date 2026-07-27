"""An RFC 6962 Certificate Transparency log for decision receipts.

This is the mechanism that has underpinned public-web TLS trust for a decade. No chain, no
token, no consensus — an append-only log, a signed head, and two proofs:

  inclusion    binds one receipt to one signed tree head. "This receipt is in that log."
  consistency  binds an older tree head to a newer one. "Nothing already published was
               changed or removed to get from there to here."

Consistency is the one that matters here, and it is worth being precise about why.
**Omission is the attack.** An agent that silently drops an instrument from the candidate
set produces a receipt that looks perfectly well-formed — every field present, every
signature valid — because the missing thing left no trace inside the document. Nothing in
a single receipt can detect that. What detects it is that the receipt was published before
anyone knew it would need to be edited, and a consistency proof against the earlier tree
head makes any later edit of that published entry fail arithmetic that a third party runs
for itself.

The remaining move available to a dishonest log is a **split view**: serve one log to the
issuer and a different one to the Card Member, each internally consistent. That is what
the auditor role below exists for. Two observers hand their signed tree heads to a witness;
equal tree sizes with unequal roots is a split view outright, and unequal sizes that no
consistency proof can relate is a fork. Neither is detectable from inside either view.

MERKLE CONVENTION — stated explicitly, because there is a second tree in this repository.
This module implements RFC 6962 section 2.1 exactly:

    MTH({})    = SHA-256()
    MTH({d0})  = SHA-256(0x00 || d0)
    MTH(D[n])  = SHA-256(0x01 || MTH(D[0:k]) || MTH(D[k:n])),  n > 1

where k is the largest power of two strictly less than n. That "the left subtree is always
a perfect tree" rule is what makes the tree append-only in the strong sense: every interior
node of an n-leaf tree survives unchanged into every m-leaf tree with m > n, which is the
property a consistency proof exploits.

`caveat/ledger.py` builds its tree bottom-up and promotes an odd trailing node to the next
level. That description sounds like a different shape, and the honest finding — checked in
`test_transparency.py` for every tree size up to 128 rather than argued — is that it is
not: odd-tail promotion computes the same root as the power-of-two split. The two logs are
still not interchangeable, for reasons that have nothing to do with tree shape:

  * the kernel hashes an entry's sequence number and its predecessor's hash into the leaf,
    so the same receipt has different leaves in the two logs and observers cannot compare
    their views of one document;
  * the kernel emits audit paths as (side, hash) pairs; RFC 6962 paths are bare ordered
    hashes and the verifier derives the sides from the index and the tree size;
  * the kernel has no consistency proof at all, which is the entire point of this module.

So: same root arithmetic, different leaf definition, different proof wire format, and one
proof this module has that the other does not.

Honest limitations:
  * Tree heads are signed with HMAC-SHA256 under a prototype key. A real log signs with a
    key the relying parties do not hold, which is what makes a signed head non-repudiable
    rather than merely authenticated. Canonicalisation, proof structure and the
    verification algorithms are unchanged.
  * The log is in-memory. Durability is somebody else's job; nothing here depends on it.
  * Proof GENERATION recomputes the tree from the leaves and is therefore O(n) in the log
    size — measured at roughly 13ms for an inclusion proof over 10,000 entries. A
    production log caches the right-hand spine and serves proofs in O(log n). Proof
    VERIFICATION, which is the part a counterparty runs and the part the argument rests
    on, is O(log n) and measured at 5-8 microseconds over the same logs. The choice here
    is deliberate: one obvious implementation an auditor can read beats two that have to
    be kept in agreement.
  * A witness detects a split view only among the heads it is actually shown. Gossip
    between witnesses is out of scope for this prototype, and no claim is made that a
    single witness makes split views impossible — only that it makes them detectable.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .manifest import canonical_json

LOG_VERSION = "plumbline/transparency/1"
STH_VERSION = "plumbline/sth/1"
SIGNATURE_ALG = "HMAC-SHA256"

# RFC 6962 domain separation: a leaf and an interior node must never hash alike, or an
# interior node could be passed off as a leaf.
LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"

# MTH of the empty tree, per RFC 6962: the hash of the empty string.
EMPTY_ROOT = hashlib.sha256(b"").hexdigest()

# Entry kinds. Closed vocabulary — an auditor's report cites these.
ENTRY_RECEIPT = "decision_receipt"
ENTRY_UNATTESTED_SELECTION = "unattested_selection"
ENTRY_REFUSAL = "valuation_refusal"
ENTRY_MANIFEST_PUBLISHED = "manifest_published"

ENTRY_KINDS = (
    ENTRY_RECEIPT,
    ENTRY_UNATTESTED_SELECTION,
    ENTRY_REFUSAL,
    ENTRY_MANIFEST_PUBLISHED,
)

# Audit outcomes, worst-first. `audit` reports the strongest finding it reached.
AUDIT_SPLIT_VIEW = "SPLIT_VIEW_DETECTED"
AUDIT_FORK = "LOG_FORK_DETECTED"
AUDIT_STH_SIGNATURE_INVALID = "STH_SIGNATURE_INVALID"
AUDIT_LOG_ID_MISMATCH = "OBSERVED_HEADS_NAME_DIFFERENT_LOGS"
AUDIT_UNRELATED = "VIEWS_NOT_RELATED_NO_PROOF_SUPPLIED"
AUDIT_CONSISTENT = "VIEWS_CONSISTENT"

# Severity order, worst first. Used to pick the headline outcome of an audit.
#
# AUDIT_LOG_ID_MISMATCH outranks AUDIT_UNRELATED because a differing log id is the cheapest
# way to dress a split view as a non-comparison: two heads, same size, different roots,
# different label. A witness that skips the pair on the strength of a string the log itself
# chose has been talked out of the one comparison it exists to make.
AUDIT_SEVERITY = (
    AUDIT_SPLIT_VIEW,
    AUDIT_FORK,
    AUDIT_STH_SIGNATURE_INVALID,
    AUDIT_LOG_ID_MISMATCH,
    AUDIT_UNRELATED,
    AUDIT_CONSISTENT,
)

# Proof failure codes.
PROOF_OK = "PROOF_VERIFIED"
PROOF_BAD_INDEX = "PROOF_LEAF_INDEX_OUT_OF_RANGE"
PROOF_ROOT_MISMATCH = "PROOF_RECOMPUTED_ROOT_MISMATCH"
PROOF_MALFORMED = "PROOF_MALFORMED"
PROOF_SIZE_REGRESSION = "PROOF_TREE_SHRANK"


class TransparencyError(ValueError):
    """Raised when a log operation is impossible, never when a proof merely fails."""


# --------------------------------------------------------------------------------------
# RFC 6962 section 2.1 — Merkle Tree Hash, and the two proof constructions
# --------------------------------------------------------------------------------------


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_leaf(data: bytes) -> str:
    """MTH of a single-leaf tree: SHA-256(0x00 || d)."""
    return _sha256_hex(LEAF_PREFIX + data)


def hash_children(left: str, right: str) -> str:
    """Interior node: SHA-256(0x01 || left || right), over raw hash bytes."""
    try:
        return _sha256_hex(NODE_PREFIX + bytes.fromhex(left) + bytes.fromhex(right))
    except ValueError as exc:
        raise TransparencyError(
            f"node hash inputs must be hex-encoded SHA-256 digests; got {left!r}, {right!r}"
        ) from exc


def split_point(n: int) -> int:
    """The largest power of two strictly less than n. Defined for n >= 2.

    This is the whole of the RFC 6962 shape rule: the left subtree is always a perfect
    binary tree, so its internal nodes never move as the log grows.
    """
    if n < 2:
        raise TransparencyError(f"split point is undefined for a tree of {n} leaf/leaves")
    return 1 << ((n - 1).bit_length() - 1)


def merkle_tree_hash(leaves: Sequence[str]) -> str:
    """MTH(D[n]) over a sequence of leaf hashes, hex in and hex out."""
    n = len(leaves)
    if n == 0:
        return EMPTY_ROOT
    if n == 1:
        return leaves[0]
    k = split_point(n)
    return hash_children(merkle_tree_hash(leaves[:k]), merkle_tree_hash(leaves[k:]))


def inclusion_path(index: int, leaves: Sequence[str]) -> tuple[str, ...]:
    """PATH(m, D[n]) — the audit path for leaf `index`, ordered leaf-upward."""
    n = len(leaves)
    if not 0 <= index < n:
        raise TransparencyError(f"leaf index {index} outside a tree of {n} leaves")
    if n == 1:
        return ()
    k = split_point(n)
    if index < k:
        return inclusion_path(index, leaves[:k]) + (merkle_tree_hash(leaves[k:]),)
    return inclusion_path(index - k, leaves[k:]) + (merkle_tree_hash(leaves[:k]),)


def consistency_path(first: int, leaves: Sequence[str]) -> tuple[str, ...]:
    """PROOF(m, D[n]) — the nodes relating the m-leaf tree to the n-leaf tree.

    Empty when the two sizes are equal: identical trees need no proof beyond identical
    roots, and RFC 6962 defines the proof that way rather than as a special case.
    """
    n = len(leaves)
    if first <= 0 or first > n:
        raise TransparencyError(
            f"consistency proof needs 0 < first <= n; got first={first}, n={n}"
        )
    if first == n:
        return ()
    return _subproof(first, leaves, True)


def _subproof(m: int, leaves: Sequence[str], is_root_of_old: bool) -> tuple[str, ...]:
    n = len(leaves)
    if m == n:
        # The old tree ends exactly here. Its root is already known to the verifier when
        # this subtree IS the old tree; otherwise it has to be supplied.
        return () if is_root_of_old else (merkle_tree_hash(leaves),)
    k = split_point(n)
    if m <= k:
        return _subproof(m, leaves[:k], is_root_of_old) + (merkle_tree_hash(leaves[k:]),)
    return _subproof(m - k, leaves[k:], False) + (merkle_tree_hash(leaves[:k]),)


# --------------------------------------------------------------------------------------
# RFC 6962 / RFC 9162 section 2.1.3 — verification, from the proof alone
# --------------------------------------------------------------------------------------


def verify_inclusion(
    *,
    leaf_hash: str,
    leaf_index: int,
    tree_size: int,
    path: Sequence[str],
    root_hash: str,
) -> bool:
    """Recompute a root from a leaf and an audit path.

    Deliberately takes only what travels in a proof — no access to the log — because the
    point of an audit path is that a counterparty holding neither the log nor a solver can
    check it with a few hashes.
    """
    if tree_size <= 0 or not 0 <= leaf_index < tree_size:
        return False
    fn, sn = leaf_index, tree_size - 1
    node = leaf_hash
    for sibling in path:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            node = hash_children(sibling, node)
            while fn != 0 and fn & 1 == 0:
                fn >>= 1
                sn >>= 1
        else:
            node = hash_children(node, sibling)
        fn >>= 1
        sn >>= 1
    return sn == 0 and hmac.compare_digest(node, root_hash)


def verify_consistency(
    *,
    first_size: int,
    first_root: str,
    second_size: int,
    second_root: str,
    path: Sequence[str],
) -> bool:
    """Check that the first tree is a prefix of the second, from the proof alone.

    Returns False — never raises — for a shrinking log, a truncated proof, or a proof that
    does not reproduce both roots. A retroactive edit anywhere in the first `first_size`
    entries lands here as False, which is the detection this whole module exists for.
    """
    if first_size < 0 or second_size < first_size:
        return False
    if first_size == 0:
        # Every tree extends the empty tree, and the proof carries nothing.
        return len(path) == 0
    if first_size == second_size:
        return len(path) == 0 and hmac.compare_digest(first_root, second_root)
    if not path:
        return False

    nodes = list(path)
    if first_size & (first_size - 1) == 0:
        # A perfect old tree: its root is the first proof node, and it is not transmitted.
        nodes.insert(0, first_root)

    fn, sn = first_size - 1, second_size - 1
    while fn & 1:
        fn >>= 1
        sn >>= 1

    fr = sr = nodes[0]
    for node in nodes[1:]:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            fr = hash_children(node, fr)
            sr = hash_children(node, sr)
            while fn != 0 and fn & 1 == 0:
                fn >>= 1
                sn >>= 1
        else:
            sr = hash_children(sr, node)
        fn >>= 1
        sn >>= 1

    return (
        sn == 0
        and hmac.compare_digest(fr, first_root)
        and hmac.compare_digest(sr, second_root)
    )


# --------------------------------------------------------------------------------------
# Signed tree heads
# --------------------------------------------------------------------------------------


def _key_bytes(key: str | bytes) -> bytes:
    return key.encode("utf-8") if isinstance(key, str) else key


def key_id(key: str | bytes) -> str:
    """A stable, non-reversible identifier for a signing key."""
    return hashlib.sha256(b"plumbline/transparency/key-id/v1" + _key_bytes(key)).hexdigest()[:16]


@dataclass(frozen=True)
class SignedTreeHead:
    """A log's commitment to its contents at one size. The unit an auditor compares."""

    log_id: str
    tree_size: int
    root_hash: str
    timestamp: int
    signature: str = ""
    signing_key_id: str = ""
    version: str = STH_VERSION

    def body(self) -> dict[str, Any]:
        """The exact object that gets signed. Signature fields are not inside it."""
        return {
            "version": self.version,
            "log_id": self.log_id,
            "tree_size": self.tree_size,
            "root_hash": self.root_hash,
            "timestamp": self.timestamp,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.body(),
            "signature": {
                "alg": SIGNATURE_ALG,
                "key_id": self.signing_key_id,
                "value": self.signature,
            },
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "SignedTreeHead":
        sig = d.get("signature") or {}
        return cls(
            log_id=str(d["log_id"]),
            tree_size=int(d["tree_size"]),
            root_hash=str(d["root_hash"]),
            timestamp=int(d["timestamp"]),
            signature=str(sig.get("value", "")),
            signing_key_id=str(sig.get("key_id", "")),
            version=str(d.get("version", STH_VERSION)),
        )


def sign_tree_head(sth: SignedTreeHead, key: str | bytes) -> SignedTreeHead:
    """Attach an HMAC-SHA256 signature over the canonical head body."""
    value = hmac.new(_key_bytes(key), canonical_json(sth.body()), hashlib.sha256).hexdigest()
    return SignedTreeHead(
        log_id=sth.log_id,
        tree_size=sth.tree_size,
        root_hash=sth.root_hash,
        timestamp=sth.timestamp,
        signature=value,
        signing_key_id=key_id(key),
        version=sth.version,
    )


def verify_tree_head(sth: SignedTreeHead | Mapping[str, Any], key: str | bytes) -> bool:
    """Recompute the head signature. Constant-time comparison."""
    if isinstance(sth, SignedTreeHead):
        sth = sth.to_dict()
    body = {k: sth[k] for k in ("version", "log_id", "tree_size", "root_hash", "timestamp")}
    signature = str((sth.get("signature") or {}).get("value", ""))
    expected = hmac.new(_key_bytes(key), canonical_json(body), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# --------------------------------------------------------------------------------------
# Proof objects — what actually travels
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class InclusionProof:
    """An RFC 6962 audit path. Sides are derived by the verifier, not carried."""

    leaf_index: int
    leaf_hash: str
    tree_size: int
    root_hash: str
    path: tuple[str, ...] = ()

    def verify(self) -> bool:
        return verify_inclusion(
            leaf_hash=self.leaf_hash,
            leaf_index=self.leaf_index,
            tree_size=self.tree_size,
            path=self.path,
            root_hash=self.root_hash,
        )

    def verify_against(self, sth: SignedTreeHead) -> bool:
        """Bind the proof to a specific published head, not merely to any root."""
        if sth.tree_size != self.tree_size or not hmac.compare_digest(
            sth.root_hash, self.root_hash
        ):
            return False
        return self.verify()

    def to_dict(self) -> dict[str, Any]:
        return {
            "leaf_index": self.leaf_index,
            "leaf_hash": self.leaf_hash,
            "tree_size": self.tree_size,
            "root_hash": self.root_hash,
            "path": list(self.path),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "InclusionProof":
        return cls(
            leaf_index=int(d["leaf_index"]),
            leaf_hash=str(d["leaf_hash"]),
            tree_size=int(d["tree_size"]),
            root_hash=str(d["root_hash"]),
            path=tuple(str(h) for h in d.get("path", ())),
        )


@dataclass(frozen=True)
class ConsistencyProof:
    """Evidence that an older published head is a prefix of a newer one."""

    first_size: int
    first_root: str
    second_size: int
    second_root: str
    path: tuple[str, ...] = ()

    def verify(self) -> bool:
        """Check the proof's own arithmetic: `first_root` is a prefix root of `second_root`.

        This says nothing about where `first_root` came from. A log that rewrote an entry
        and then produced a proof about its own rewritten past passes here, because the
        rewritten history is internally consistent — that is exactly what a forger builds.
        Use `verify_against` whenever an earlier head was actually published.
        """
        return verify_consistency(
            first_size=self.first_size,
            first_root=self.first_root,
            second_size=self.second_size,
            second_root=self.second_root,
            path=self.path,
        )

    def verify_against(self, old: SignedTreeHead) -> bool:
        """Bind the proof to a head someone actually published, then check it.

        The detection in this module comes from the earlier root being a commitment made
        before anyone knew the entry would need editing. A proof whose `first_root` the
        prover supplied is a claim about itself; a proof whose `first_root` is a
        counterparty's published head is evidence. `InclusionProof` has the same method for
        the same reason, and the asymmetry of having it on only one of the two is how an
        auditor ends up checking the weaker thing by accident.
        """
        if old.tree_size != self.first_size or not hmac.compare_digest(
            old.root_hash, self.first_root
        ):
            return False
        return self.verify()

    def to_dict(self) -> dict[str, Any]:
        return {
            "first_size": self.first_size,
            "first_root": self.first_root,
            "second_size": self.second_size,
            "second_root": self.second_root,
            "path": list(self.path),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ConsistencyProof":
        return cls(
            first_size=int(d["first_size"]),
            first_root=str(d["first_root"]),
            second_size=int(d["second_size"]),
            second_root=str(d["second_root"]),
            path=tuple(str(h) for h in d.get("path", ())),
        )


@dataclass(frozen=True)
class LogEntry:
    """One logged fact. The leaf hash covers the entry body, never its position.

    Position is implied by the tree, exactly as in RFC 6962. Hashing the index into the
    leaf would make the same receipt hash differently in two logs and destroy the ability
    to compare two observers' views of the same document.
    """

    seq: int
    kind: str
    body: dict[str, Any]
    timestamp: int
    leaf_hash: str

    def leaf_data(self) -> bytes:
        return leaf_data_for(kind=self.kind, body=self.body, timestamp=self.timestamp)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "kind": self.kind,
            "body": self.body,
            "timestamp": self.timestamp,
            "leaf_hash": self.leaf_hash,
        }


def leaf_data_for(*, kind: str, body: Mapping[str, Any], timestamp: int) -> bytes:
    """Canonical bytes hashed into a leaf. Stable across processes and runs."""
    return canonical_json({"kind": kind, "body": body, "timestamp": timestamp})


# --------------------------------------------------------------------------------------
# The log
# --------------------------------------------------------------------------------------


class TransparencyLog:
    """Append-only receipt log with signed heads and RFC 6962 proofs.

    Nothing here mutates or removes an entry, and there is deliberately no API that could.
    An operator determined to edit history must build a different log — which is exactly
    what a consistency proof against an already-published head detects.
    """

    def __init__(self, log_id: str, *, signing_key: str | bytes | None = None) -> None:
        self.log_id = log_id
        self._signing_key = signing_key
        self._entries: list[LogEntry] = []
        self._leaves: list[str] = []

    # -- writing -----------------------------------------------------------------------

    def append(self, *, kind: str, body: Mapping[str, Any], timestamp: int) -> LogEntry:
        """Add one entry. `timestamp` is explicit so a replayed run rebuilds the same log."""
        if kind not in ENTRY_KINDS:
            raise TransparencyError(
                f"unknown log entry kind {kind!r}; expected one of {', '.join(ENTRY_KINDS)}"
            )
        payload = dict(body)
        leaf = hash_leaf(leaf_data_for(kind=kind, body=payload, timestamp=timestamp))
        entry = LogEntry(
            seq=len(self._entries),
            kind=kind,
            body=payload,
            timestamp=timestamp,
            leaf_hash=leaf,
        )
        self._entries.append(entry)
        self._leaves.append(leaf)
        return entry

    # -- reading -----------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> tuple[LogEntry, ...]:
        return tuple(self._entries)

    @property
    def leaves(self) -> tuple[str, ...]:
        return tuple(self._leaves)

    def get(self, seq: int) -> LogEntry | None:
        if 0 <= seq < len(self._entries):
            return self._entries[seq]
        return None

    def filter(self, kind: str) -> tuple[LogEntry, ...]:
        return tuple(e for e in self._entries if e.kind == kind)

    def find_leaf(self, leaf_hash: str) -> int | None:
        """First index carrying this leaf. Duplicate leaves are legal in CT."""
        for i, leaf in enumerate(self._leaves):
            if hmac.compare_digest(leaf, leaf_hash):
                return i
        return None

    # -- heads and proofs --------------------------------------------------------------

    def root(self, tree_size: int | None = None) -> str:
        n = len(self._leaves) if tree_size is None else tree_size
        if not 0 <= n <= len(self._leaves):
            raise TransparencyError(
                f"cannot root a tree of size {n}; the log holds {len(self._leaves)} entries"
            )
        return merkle_tree_hash(self._leaves[:n])

    def signed_tree_head(
        self, *, timestamp: int, tree_size: int | None = None, key: str | bytes | None = None
    ) -> SignedTreeHead:
        """Publish a head. Unsigned if no key was supplied at construction or here."""
        n = len(self._leaves) if tree_size is None else tree_size
        head = SignedTreeHead(
            log_id=self.log_id,
            tree_size=n,
            root_hash=self.root(n),
            timestamp=timestamp,
        )
        material = key if key is not None else self._signing_key
        return sign_tree_head(head, material) if material is not None else head

    def inclusion_proof(self, seq: int, *, tree_size: int | None = None) -> InclusionProof:
        n = len(self._leaves) if tree_size is None else tree_size
        if not 0 <= n <= len(self._leaves):
            raise TransparencyError(
                f"cannot prove against a tree of size {n}; the log holds {len(self._leaves)}"
            )
        if not 0 <= seq < n:
            raise TransparencyError(
                f"entry {seq} is not inside a tree of size {n}; "
                f"request a proof against a head published at or after size {seq + 1}"
            )
        return InclusionProof(
            leaf_index=seq,
            leaf_hash=self._leaves[seq],
            tree_size=n,
            root_hash=self.root(n),
            path=inclusion_path(seq, self._leaves[:n]),
        )

    def consistency_proof(self, first_size: int, second_size: int | None = None) -> ConsistencyProof:
        n = len(self._leaves) if second_size is None else second_size
        if not 0 <= n <= len(self._leaves):
            raise TransparencyError(
                f"cannot prove against a tree of size {n}; the log holds {len(self._leaves)}"
            )
        if first_size < 0 or first_size > n:
            raise TransparencyError(
                f"consistency runs forward only: first_size={first_size} exceeds "
                f"second_size={n}. A log that shrank is not consistent with its own past."
            )
        path = () if first_size == 0 else consistency_path(first_size, self._leaves[:n])
        return ConsistencyProof(
            first_size=first_size,
            first_root=self.root(first_size),
            second_size=n,
            second_root=self.root(n),
            path=tuple(path),
        )

    def prove_extends(self, old: SignedTreeHead) -> ConsistencyProof:
        """A proof that this log still contains everything `old` committed to.

        The first root comes from the OLD head, not from this log, so a log that quietly
        rewrote an entry produces a proof that fails rather than one that agrees with
        itself.
        """
        if old.tree_size > len(self._leaves):
            raise TransparencyError(
                f"the published head claims {old.tree_size} entries but this log holds "
                f"{len(self._leaves)}; a shrinking log cannot be proved consistent"
            )
        path = () if old.tree_size == 0 else consistency_path(old.tree_size, self._leaves)
        return ConsistencyProof(
            first_size=old.tree_size,
            first_root=old.root_hash,
            second_size=len(self._leaves),
            second_root=self.root(),
            path=tuple(path),
        )


# --------------------------------------------------------------------------------------
# The witness / auditor role — split-view detection
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """One party's view of the log at one moment."""

    observer: str
    sth: SignedTreeHead

    def to_dict(self) -> dict[str, Any]:
        return {"observer": self.observer, "sth": self.sth.to_dict()}


@dataclass(frozen=True)
class Finding:
    code: str
    detail: str
    observers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "observers": list(self.observers)}


@dataclass(frozen=True)
class AuditReport:
    """What a witness can say after comparing the heads it was shown."""

    auditor_id: str
    outcome: str
    findings: tuple[Finding, ...]
    observations: tuple[Observation, ...]

    @property
    def ok(self) -> bool:
        return self.outcome == AUDIT_CONSISTENT

    def codes(self) -> tuple[str, ...]:
        return tuple(f.code for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "auditor_id": self.auditor_id,
            "outcome": self.outcome,
            "ok": self.ok,
            "findings": [f.to_dict() for f in self.findings],
            "observations": [o.to_dict() for o in self.observations],
        }

    def render_text(self) -> str:
        lines = [f"WITNESS {self.auditor_id}: {self.outcome}"]
        for obs in self.observations:
            lines.append(
                f"  seen by {obs.observer}: size={obs.sth.tree_size} "
                f"root={obs.sth.root_hash[:16]}…"
            )
        for f in self.findings:
            who = " / ".join(f.observers) if f.observers else "-"
            lines.append(f"  [{f.code}] {who}: {f.detail}")
        return "\n".join(lines)


class LogAuditor:
    """A third party that compares what different observers were shown.

    A dishonest log's last move, once inclusion and consistency both verify inside each
    view, is to maintain two views. Nothing inside either view reveals it. This role
    exists because the detection has to come from outside: the issuer and the Card Member
    hand their signed heads to the same witness, and equal sizes with unequal roots is a
    split view with a signature on each half.
    """

    def __init__(self, auditor_id: str) -> None:
        self.auditor_id = auditor_id
        self._observations: list[Observation] = []

    def observe(self, observer: str, sth: SignedTreeHead) -> Observation:
        obs = Observation(observer=observer, sth=sth)
        self._observations.append(obs)
        return obs

    @property
    def observations(self) -> tuple[Observation, ...]:
        return tuple(self._observations)

    def audit(
        self,
        *,
        proofs: Mapping[tuple[int, int], Sequence[str]] | None = None,
        key: str | bytes | None = None,
    ) -> AuditReport:
        """Compare every pair of observed heads.

        `proofs` maps (first_size, second_size) to a consistency path, modelling the
        witness asking the log to relate two heads it was shown. A pair the witness cannot
        relate is reported as unrelated rather than as consistent — a witness that assumes
        the best is not a witness.
        """
        proofs = proofs or {}
        findings: list[Finding] = []
        observed = sorted(self._observations, key=lambda o: (o.sth.tree_size, o.observer))

        if key is not None:
            for obs in observed:
                if not verify_tree_head(obs.sth, key):
                    findings.append(
                        Finding(
                            AUDIT_STH_SIGNATURE_INVALID,
                            f"head at size {obs.sth.tree_size} does not verify under the "
                            f"log's published key",
                            (obs.observer,),
                        )
                    )

        for i in range(len(observed)):
            for j in range(i + 1, len(observed)):
                a, b = observed[i], observed[j]
                pair = (a.observer, b.observer)
                if a.sth.log_id != b.sth.log_id:
                    # Reported, never skipped. The log id is a label the log chooses; if a
                    # differing one silenced the comparison, an operator serving two
                    # histories would only have to rename one of them.
                    findings.append(
                        Finding(
                            AUDIT_LOG_ID_MISMATCH,
                            f"heads name different logs ({a.sth.log_id!r} at size "
                            f"{a.sth.tree_size} vs {b.sth.log_id!r} at size "
                            f"{b.sth.tree_size}), so no proof can relate them. Two parties "
                            f"who believe they are reading one log and are shown two is "
                            f"the split view, not an exemption from it.",
                            pair,
                        )
                    )
                    continue
                if a.sth.tree_size == b.sth.tree_size:
                    if hmac.compare_digest(a.sth.root_hash, b.sth.root_hash):
                        findings.append(
                            Finding(
                                AUDIT_CONSISTENT,
                                f"identical head at size {a.sth.tree_size}",
                                pair,
                            )
                        )
                    else:
                        findings.append(
                            Finding(
                                AUDIT_SPLIT_VIEW,
                                f"two roots published at size {a.sth.tree_size}: "
                                f"{a.sth.root_hash} vs {b.sth.root_hash}. The log served "
                                f"different histories to different parties.",
                                pair,
                            )
                        )
                    continue

                path = proofs.get((a.sth.tree_size, b.sth.tree_size))
                if path is None:
                    findings.append(
                        Finding(
                            AUDIT_UNRELATED,
                            f"no consistency proof supplied relating size "
                            f"{a.sth.tree_size} to size {b.sth.tree_size}",
                            pair,
                        )
                    )
                    continue
                ok = verify_consistency(
                    first_size=a.sth.tree_size,
                    first_root=a.sth.root_hash,
                    second_size=b.sth.tree_size,
                    second_root=b.sth.root_hash,
                    path=path,
                )
                verdict = (
                    "verified"
                    if ok
                    else (
                        "FAILED: the earlier head is not a prefix of the later one, so a "
                        "published entry was changed or removed"
                    )
                )
                findings.append(
                    Finding(
                        AUDIT_CONSISTENT if ok else AUDIT_FORK,
                        f"size {a.sth.tree_size} → {b.sth.tree_size} consistency {verdict}",
                        pair,
                    )
                )

        if not findings:
            # Every pair of observed heads now yields a finding, so this is reachable only
            # below two observations. The count is stated rather than assumed: a witness
            # reporting VIEWS_CONSISTENT must say how many views it actually compared, or
            # the verdict reads as evidence when it is an absence of evidence.
            findings.append(
                Finding(
                    AUDIT_CONSISTENT,
                    f"nothing to compare: {len(observed)} head(s) observed, and a split "
                    f"view is only visible between two",
                )
            )

        seen = {f.code for f in findings}
        # AUDIT_SEVERITY ends with AUDIT_CONSISTENT, so this always resolves.
        outcome = next(code for code in AUDIT_SEVERITY if code in seen)
        return AuditReport(
            auditor_id=self.auditor_id,
            outcome=outcome,
            findings=tuple(findings),
            observations=tuple(observed),
        )


def audit_pair(
    *,
    auditor_id: str,
    issuer_view: SignedTreeHead,
    cardholder_view: SignedTreeHead,
    proof: ConsistencyProof | None = None,
    key: str | bytes | None = None,
) -> AuditReport:
    """The two-party case, which is the one in the demo: issuer vs Card Member."""
    auditor = LogAuditor(auditor_id)
    auditor.observe("issuer", issuer_view)
    auditor.observe("cardholder", cardholder_view)
    proofs = (
        {(proof.first_size, proof.second_size): proof.path} if proof is not None else None
    )
    return auditor.audit(proofs=proofs, key=key)


def check_inclusion_proof(proof: InclusionProof, sth: SignedTreeHead) -> tuple[bool, str]:
    """Verify a proof against a published head, returning a reason code either way."""
    if proof.tree_size <= 0 or not 0 <= proof.leaf_index < proof.tree_size:
        return False, PROOF_BAD_INDEX
    if sth.tree_size != proof.tree_size:
        return False, PROOF_MALFORMED
    if not hmac.compare_digest(sth.root_hash, proof.root_hash):
        return False, PROOF_ROOT_MISMATCH
    return (True, PROOF_OK) if proof.verify() else (False, PROOF_ROOT_MISMATCH)


def check_consistency_proof(
    proof: ConsistencyProof, old: SignedTreeHead | None = None
) -> tuple[bool, str]:
    """Verify a consistency proof, returning a reason code either way.

    Pass `old` — the earlier head as the relying party received it — whenever one exists.
    Without it this checks only that the proof agrees with itself, which an edited log's
    proof about its own edited past also does. `TransparencyLog.prove_extends` takes its
    first root from the published head for this reason, so a proof from that method already
    carries the binding; a proof from `consistency_proof()` does not.
    """
    if proof.second_size < proof.first_size:
        return False, PROOF_SIZE_REGRESSION
    if old is not None:
        return (True, PROOF_OK) if proof.verify_against(old) else (False, PROOF_ROOT_MISMATCH)
    return (True, PROOF_OK) if proof.verify() else (False, PROOF_ROOT_MISMATCH)


def replay_leaves(entries: Iterable[LogEntry]) -> tuple[str, ...]:
    """Recompute every leaf from its entry body — the check that the log's own leaf
    hashes were not simply asserted. An auditor holding the entries runs this before
    trusting any root computed over them."""
    return tuple(
        hash_leaf(leaf_data_for(kind=e.kind, body=e.body, timestamp=e.timestamp))
        for e in entries
    )
