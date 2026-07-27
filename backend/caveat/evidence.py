"""The Agent Evidence Package: one click on a transaction, one portable case file.

American Express's published ACE architecture omits audit-logging requirements and a
dispute procedure for agentic transactions, while Agent Purchase Protection has already
committed to covering charges arising from registered-agent error. This module is the
artifact that gap needs: everything a dispute reviewer must see to adjudicate an agent
transaction, in one signed blob that verifies without access to our database.

What travels inside a package:

  mandate chain     root → acting mandate, one hop per delegation, each carrying the scope
                    that hop declared and the Z3 entailment result that admitted it. This
                    is the answer to "what was this agent actually allowed to do".
  carts             the signed intent cart, the executed cart, and the diff between them.
  decision          outcome, reason codes, violations, and elapsed time.
  liability         the verdict and the liable party, from pdp.classify_liability.
  inclusion proof   a Merkle audit path proving the decision entry sits under a published
                    ledger root, without handing the reviewer the whole log.
  operator risk     optional snapshot of the operator's behavioural score at issue time.

`verify_package` re-checks two independent things: the HMAC signature over the canonical
JSON body, and the Merkle inclusion proof recomputed from leaf and audit path. Tampering
with any field breaks the first; substituting a different decision breaks the second.

Honest limitations, stated here so they are also stated on the slide:
  * Signing is HMAC-SHA256 with a shared secret. That authenticates the package to anyone
    holding the key — fine inside one issuer's dispute flow, not sufficient for a
    third party who should not hold a signing key. A production deployment swaps this for
    a detached signature under the issuer's private key; the canonicalisation and the
    verification flow are unchanged.
  * The inclusion proof binds the decision to the ledger root **as it stood when the
    package was issued**. Proving that root is an ancestor of today's root needs a Merkle
    consistency proof, which this prototype does not implement. `verify_package` will
    check a caller-supplied `expected_root` if you have one out of band.
  * Everything below the PDP is mocked. The package proves what CAVEAT decided and why;
    it does not prove a settlement occurred.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .cart import Cart, CartDiff
from .constraints import fmt_money
from .ledger import InclusionProof, canonical
from .mandate import Mandate
from .pdp import (
    LIABLE_PARTY,
    Decision,
    VERDICT_AGENT_ERROR,
    VERDICT_INJECTION_COMPROMISE,
    VERDICT_MERCHANT_MISREPRESENTATION,
    VERDICT_WITHIN_MANDATE,
)

PACKAGE_VERSION = 1
SIGNATURE_ALG = "HMAC-SHA256"

# Ledger entry kinds this module reads back to reconstruct a delegation chain.
KIND_MANDATE_GRANTED = "mandate_granted"
KIND_DELEGATION_ACCEPTED = "delegation_accepted"

# Verification check names. Closed vocabulary — a reviewer's report cites these.
CHECK_STRUCTURE = "PACKAGE_STRUCTURE"
CHECK_SIGNATURE = "SIGNATURE"
CHECK_INCLUSION_PROOF = "MERKLE_INCLUSION_PROOF"
CHECK_PROOF_BINDS_DECISION = "PROOF_BINDS_DECISION"
CHECK_LEDGER_ROOT = "LEDGER_ROOT_MATCHES"
CHECK_INTENT_CART_HASH = "INTENT_CART_HASH"
CHECK_EXECUTED_CART_HASH = "EXECUTED_CART_HASH"
CHECK_CHAIN_CONTINUITY = "MANDATE_CHAIN_CONTINUITY"

VERDICT_MEANING = {
    VERDICT_WITHIN_MANDATE: "the executed cart matched the signed intent and stayed inside the mandate",
    VERDICT_AGENT_ERROR: "the agent acted outside its own declared scope",
    VERDICT_INJECTION_COMPROMISE: "the cart diverged from signed intent after the human signed it",
    VERDICT_MERCHANT_MISREPRESENTATION: "the merchant mutated a locked cart",
}

DISCLOSURES = (
    "Signature is HMAC-SHA256 under a shared secret; a production deployment signs with "
    "the issuer's private key. Canonicalisation and verification flow are unchanged.",
    "The inclusion proof binds this decision to the ledger root at issue time. Proving "
    "that root descends from a later root requires a Merkle consistency proof, which this "
    "prototype does not implement.",
    "Cumulative-budget and velocity caveats are evaluated against the store at decision "
    "time and are not offline-verifiable; only structural caveats verify from the "
    "credential alone.",
    "All payment rails, merchants and authorization responses in this prototype are mocked. "
    "This package evidences the governance decision, not a settlement.",
)


def _key_bytes(key: str | bytes) -> bytes:
    return key.encode("utf-8") if isinstance(key, str) else key


def key_id(key: str | bytes) -> str:
    """A stable, non-reversible identifier for a signing key.

    Lets a package say which key verifies it without carrying anything that helps forge it.
    """
    return hashlib.sha256(b"caveat/evidence/key-id/v1" + _key_bytes(key)).hexdigest()[:16]


def sign_body(body: Mapping[str, Any], key: str | bytes) -> str:
    """HMAC-SHA256 over the canonical JSON encoding of the package body."""
    return hmac.new(_key_bytes(key), canonical(body), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class MandateHop:
    """One delegation hop, with the scope it declared and the proof that admitted it."""

    mandate_id: str
    parent_id: str | None
    holder: str
    depth: int
    created_at: int
    declared_scope: tuple[dict[str, Any], ...]
    scope_display: tuple[str, ...]
    entailment: dict[str, Any] | None
    entailment_ms: float | None
    ledger_seq: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mandate_id": self.mandate_id,
            "parent_id": self.parent_id,
            "holder": self.holder,
            "depth": self.depth,
            "created_at": self.created_at,
            "declared_scope": [dict(c) for c in self.declared_scope],
            "scope_display": list(self.scope_display),
            "entailment": self.entailment,
            "entailment_ms": self.entailment_ms,
            "ledger_seq": self.ledger_seq,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "MandateHop":
        return cls(
            mandate_id=str(d["mandate_id"]),
            parent_id=d.get("parent_id"),
            holder=str(d["holder"]),
            depth=int(d["depth"]),
            created_at=int(d["created_at"]),
            declared_scope=tuple(d.get("declared_scope", ())),
            scope_display=tuple(d.get("scope_display", ())),
            entailment=d.get("entailment"),
            entailment_ms=d.get("entailment_ms"),
            ledger_seq=d.get("ledger_seq"),
        )


@dataclass(frozen=True)
class AgentEvidencePackage:
    """A signed, portable case file for exactly one agent transaction."""

    package_id: str
    issued_at: int
    body: dict[str, Any]
    signature: str
    signing_key_id: str

    # -- serialisation -----------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "caveat_evidence_version": PACKAGE_VERSION,
            "package": self.body,
            "signature": {
                "alg": SIGNATURE_ALG,
                "key_id": self.signing_key_id,
                "value": self.signature,
            },
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AgentEvidencePackage":
        body = dict(payload["package"])
        sig = payload.get("signature", {})
        return cls(
            package_id=str(body["package_id"]),
            issued_at=int(body["issued_at"]),
            body=body,
            signature=str(sig.get("value", "")),
            signing_key_id=str(sig.get("key_id", "")),
        )

    # -- convenience -------------------------------------------------------------------

    @property
    def txn_id(self) -> str:
        return str(self.body["decision"]["txn_id"])

    @property
    def verdict(self) -> str:
        return str(self.body["liability"]["verdict"])

    @property
    def liable_party(self) -> str:
        return str(self.body["liability"]["liable_party"])

    def hops(self) -> tuple[MandateHop, ...]:
        return tuple(MandateHop.from_dict(h) for h in self.body["mandate_chain"])

    def inclusion_proof(self) -> InclusionProof:
        return inclusion_proof_from_dict(self.body["ledger"]["inclusion_proof"])

    # -- the screenshot ----------------------------------------------------------------

    def render_text(self, width: int = 84) -> str:
        b = self.body
        d = b["decision"]
        diff = b["cart_diff"]
        led = b["ledger"]
        rule = "═" * width
        thin = "─" * width
        out: list[str] = [
            rule,
            "AGENT EVIDENCE PACKAGE".center(width),
            f"{self.package_id}   issued {self.issued_at}".center(width),
            rule,
            "",
            f"TRANSACTION   {d['txn_id']}",
            f"  outcome     {d['outcome']}  —  {d['headline']}",
            f"  amount      {fmt_money(int(d['amount']))}",
            f"  operator    {b['operator']['operator_id']}",
            f"  reasons     {', '.join(d['reason_codes']) or '(none)'}",
            "",
            f"LIABILITY     {b['liability']['verdict']}",
            f"  liable      {b['liability']['liable_party']}",
            f"  meaning     {b['liability']['meaning']}",
            "",
            thin,
            "MANDATE CHAIN  (root → acting)",
            thin,
        ]
        for hop in b["mandate_chain"]:
            marker = "root" if hop["parent_id"] is None else f"hop {hop['depth']}"
            out.append(f"  [{marker}] {hop['mandate_id']}  holder={hop['holder']}")
            for line in hop["scope_display"]:
                out.append(f"          scope: {line}")
            ent = hop.get("entailment")
            if ent is None:
                out.append("          entailment: n/a (root grant — nothing to narrow)")
            else:
                verdict = "ENTAILED (UNSAT)" if ent.get("entailed") else "REJECTED"
                ms = ent.get("elapsed_ms")
                out.append(f"          entailment: {verdict} in {ms} ms — child ⊆ parent proved by Z3")
        out += [
            "",
            thin,
            "CART: SIGNED INTENT vs EXECUTED",
            thin,
            f"  intent    merchant={b['intent_cart']['merchant']}  total={fmt_money(int(diff['intent_total']))}",
        ]
        for line in b["intent_cart"]["lines"]:
            out.append(f"            + {line['description']} {fmt_money(int(line['amount']))} (MCC {line['mcc']})")
        out.append(
            f"  executed  merchant={b['executed_cart']['merchant']}  total={fmt_money(int(diff['executed_total']))}"
        )
        for line in b["executed_cart"]["lines"]:
            out.append(f"            + {line['description']} {fmt_money(int(line['amount']))} (MCC {line['mcc']})")
        out += [
            "",
            f"  DIFF      {diff['summary']}",
        ]
        for line in diff["added"]:
            out.append(f"            ADDED    {line['description']} {fmt_money(int(line['amount']))} (MCC {line['mcc']})")
        for line in diff["removed"]:
            out.append(f"            REMOVED  {line['description']} {fmt_money(int(line['amount']))}")
        for change in diff["modified"]:
            out.append(
                f"            CHANGED  {change['before']['description']} "
                f"{fmt_money(int(change['before']['amount']))} → {fmt_money(int(change['after']['amount']))}"
            )
        if b["violations"]:
            out += ["", thin, "VIOLATIONS", thin]
            for v in b["violations"]:
                out.append(f"  {v['reason']}: {v['detail']}")
        out += [
            "",
            thin,
            "LEDGER",
            thin,
            f"  entry seq   {led['seq']}   tree size {led['tree_size']}",
            f"  leaf        {led['inclusion_proof']['leaf_hash']}",
            f"  root        {led['root']}",
            f"  audit path  {len(led['inclusion_proof']['path'])} node(s)",
        ]
        risk = b.get("operator_risk")
        if risk:
            out += [
                "",
                thin,
                "OPERATOR RISK AT ISSUE TIME",
                thin,
                f"  score       {risk['score']} / 100  ({risk['band']})",
                f"  cap         {risk['suggested_exposure_cap_display']} per transaction",
            ]
            for driver in risk.get("drivers", []):
                out.append(f"  driver      {driver}")
        out += [
            "",
            thin,
            "SIGNATURE",
            thin,
            f"  alg         {SIGNATURE_ALG}",
            f"  key_id      {self.signing_key_id}",
            f"  value       {self.signature}",
            "",
            thin,
            "DISCLOSURES",
            thin,
        ]
        for note in b["disclosures"]:
            out.append(f"  • {note}")
        out.append(rule)
        return "\n".join(out)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class PackageVerification:
    ok: bool
    checks: tuple[Check, ...]

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if not c.ok)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [c.to_dict() for c in self.checks],
            "failures": [c.name for c in self.failures],
        }

    def render_text(self) -> str:
        head = "VERIFIED" if self.ok else "VERIFICATION FAILED"
        lines = [head]
        for c in self.checks:
            lines.append(f"  [{'ok' if c.ok else 'FAIL'}] {c.name}: {c.detail}")
        return "\n".join(lines)


def inclusion_proof_from_dict(d: Mapping[str, Any]) -> InclusionProof:
    """Rebuild an InclusionProof from its serialized form so a reviewer can re-verify."""
    return InclusionProof(
        index=int(d["index"]),
        leaf_hash=str(d["leaf_hash"]),
        root=str(d["root"]),
        tree_size=int(d["tree_size"]),
        path=tuple((str(p["side"]), str(p["hash"])) for p in d.get("path", ())),
    )


# --------------------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------------------


def build_mandate_chain(engine: Any, mandate: Mandate) -> tuple[MandateHop, ...]:
    """Walk root → acting mandate, attaching each hop's declared scope and entailment.

    The entailment result is read back out of the ledger rather than recomputed, because
    the package must evidence what was actually proved at delegation time, not what would
    be proved today under a possibly-changed solver.
    """
    lineage: list[Mandate] = []
    current: Mandate | None = mandate
    seen: set[str] = set()
    while current is not None and current.mandate_id not in seen:
        seen.add(current.mandate_id)
        lineage.append(current)
        current = engine.mandate(current.parent_id) if current.parent_id else None
    lineage.reverse()

    hops: list[MandateHop] = []
    for m in lineage:
        kind = KIND_MANDATE_GRANTED if m.parent_id is None else KIND_DELEGATION_ACCEPTED
        matches = engine.ledger.filter(kind, mandate_id=m.mandate_id)
        entry = matches[0] if matches else None
        entailment = entry.payload.get("entailment") if entry else None
        hops.append(
            MandateHop(
                mandate_id=m.mandate_id,
                parent_id=m.parent_id,
                holder=m.holder,
                depth=m.depth,
                created_at=m.created_at,
                declared_scope=tuple(m.scope.to_dicts()),
                scope_display=tuple(m.scope.describe()),
                entailment=entailment,
                entailment_ms=m.entailment_ms,
                ledger_seq=entry.seq if entry else None,
            )
        )
    return tuple(hops)


def build_evidence_package(
    *,
    engine: Any,
    decision: Decision,
    intent_cart: Cart,
    executed_cart: Cart,
    signing_key: str | bytes,
    issued_at: int,
    mandate: Mandate | None = None,
    operator_risk: Mapping[str, Any] | None = None,
    package_id: str | None = None,
    disclosures: Sequence[str] = DISCLOSURES,
) -> AgentEvidencePackage:
    """Assemble and sign the case file for one decision.

    `issued_at` is an explicit parameter, like every other timestamp in this codebase, so a
    replayed run produces a byte-identical package and therefore an identical signature.
    """
    acting = mandate or engine.mandate(decision.mandate_id)
    if acting is None:
        raise ValueError(f"no mandate {decision.mandate_id!r} known to the engine")

    proof = engine.ledger.inclusion_proof(decision.ledger_seq)
    if proof is None:
        raise ValueError(f"no ledger entry at seq {decision.ledger_seq}")

    hops = build_mandate_chain(engine, acting)
    diff: CartDiff = decision.diff

    body: dict[str, Any] = {
        "package_id": package_id or f"aep_{secrets.token_hex(8)}",
        "issued_at": issued_at,
        "operator": {
            "operator_id": acting.holder,
            "acting_mandate_id": acting.mandate_id,
            "root_mandate_id": acting.root_id,
            "delegation_depth": acting.depth,
        },
        "decision": decision.to_dict(),
        "violations": [
            {"reason": v.reason, "detail": v.detail, "constraint": v.constraint}
            for v in decision.violations
        ],
        "liability": {
            "verdict": decision.verdict,
            "liable_party": decision.liable_party,
            "meaning": VERDICT_MEANING.get(decision.verdict, "not classified"),
            "party_table": dict(LIABLE_PARTY),
        },
        "mandate_chain": [h.to_dict() for h in hops],
        "intent_cart": intent_cart.to_dict(),
        "executed_cart": executed_cart.to_dict(),
        "cart_diff": diff.to_dict(),
        "ledger": {
            "seq": decision.ledger_seq,
            "root": proof.root,
            "tree_size": proof.tree_size,
            "inclusion_proof": proof.to_dict(),
        },
        "operator_risk": dict(operator_risk) if operator_risk else None,
        "disclosures": list(disclosures),
    }

    signature = sign_body(body, signing_key)
    return AgentEvidencePackage(
        package_id=str(body["package_id"]),
        issued_at=issued_at,
        body=body,
        signature=signature,
        signing_key_id=key_id(signing_key),
    )


# --------------------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------------------


def verify_package(
    payload: AgentEvidencePackage | Mapping[str, Any],
    key: str | bytes,
    *,
    expected_root: str | None = None,
) -> PackageVerification:
    """Re-derive everything the package claims. Signature first, then the Merkle proof.

    Deliberately takes a plain mapping as well as the dataclass: a reviewer receives JSON,
    not a Python object, and must be able to verify it with this function alone.
    """
    checks: list[Check] = []

    if isinstance(payload, AgentEvidencePackage):
        payload = payload.to_dict()

    body = payload.get("package")
    sig = payload.get("signature") or {}
    if not isinstance(body, Mapping) or "decision" not in body or "ledger" not in body:
        checks.append(Check(CHECK_STRUCTURE, False, "package body is missing or malformed"))
        return PackageVerification(ok=False, checks=tuple(checks))
    checks.append(Check(CHECK_STRUCTURE, True, "body carries decision, chain, carts and ledger proof"))

    expected_sig = sign_body(body, key)
    presented = str(sig.get("value", ""))
    sig_ok = hmac.compare_digest(expected_sig, presented)
    checks.append(
        Check(
            CHECK_SIGNATURE,
            sig_ok,
            f"{SIGNATURE_ALG} over canonical JSON"
            if sig_ok
            else "HMAC does not match the body — the package was altered or signed with another key",
        )
    )

    ledger = body["ledger"]
    try:
        proof = inclusion_proof_from_dict(ledger["inclusion_proof"])
    except (KeyError, TypeError, ValueError) as exc:
        checks.append(Check(CHECK_INCLUSION_PROOF, False, f"unreadable inclusion proof: {exc}"))
        return PackageVerification(ok=False, checks=tuple(checks))

    proof_ok = proof.verify()
    checks.append(
        Check(
            CHECK_INCLUSION_PROOF,
            proof_ok,
            f"audit path of {len(proof.path)} node(s) recomputes root {proof.root[:16]}…"
            if proof_ok
            else "audit path does not recompute the stated root",
        )
    )

    seq = body["decision"].get("ledger_seq")
    binds = proof.index == seq and str(ledger.get("root", "")) == proof.root
    checks.append(
        Check(
            CHECK_PROOF_BINDS_DECISION,
            binds,
            f"proof index {proof.index} matches decision ledger_seq {seq}"
            if binds
            else f"proof index {proof.index} does not bind decision ledger_seq {seq}",
        )
    )

    if expected_root is not None:
        root_ok = proof.root == expected_root
        checks.append(
            Check(
                CHECK_LEDGER_ROOT,
                root_ok,
                "package root matches the root supplied out of band"
                if root_ok
                else "package root differs from the supplied root; a consistency proof is needed",
            )
        )

    for name, cart_key, hash_key in (
        (CHECK_INTENT_CART_HASH, "intent_cart", "intent_hash"),
        (CHECK_EXECUTED_CART_HASH, "executed_cart", "cart_hash"),
    ):
        try:
            recomputed = Cart.from_dict(body[cart_key]).hash()
        except (KeyError, TypeError, ValueError) as exc:
            checks.append(Check(name, False, f"cart could not be rehydrated: {exc}"))
            continue
        stated = str(body["decision"].get(hash_key, ""))
        ok = recomputed == stated
        checks.append(
            Check(
                name,
                ok,
                f"{cart_key} hashes to {recomputed[:16]}…"
                if ok
                else f"{cart_key} hashes to {recomputed[:16]}… but the decision recorded {stated[:16]}…",
            )
        )

    chain_ok, chain_detail = _check_chain_continuity(body)
    checks.append(Check(CHECK_CHAIN_CONTINUITY, chain_ok, chain_detail))

    return PackageVerification(ok=all(c.ok for c in checks), checks=tuple(checks))


def _check_chain_continuity(body: Mapping[str, Any]) -> tuple[bool, str]:
    """Root first, each hop's parent is its predecessor, and the last hop is the actor.

    A structural check, not a cryptographic one: it catches a chain that was reordered or
    had a hop dropped before signing. Removing a hop after signing breaks the HMAC instead.
    """
    hops = body.get("mandate_chain") or []
    if not hops:
        return False, "mandate chain is empty"
    if hops[0].get("parent_id") is not None:
        return False, "first hop is not a root grant"
    for prev, nxt in zip(hops, hops[1:]):
        if nxt.get("parent_id") != prev.get("mandate_id"):
            return False, f"hop {nxt.get('mandate_id')} does not descend from {prev.get('mandate_id')}"
        if int(nxt.get("depth", 0)) != int(prev.get("depth", 0)) + 1:
            return False, f"hop {nxt.get('mandate_id')} has a non-contiguous depth"
    acting = body.get("operator", {}).get("acting_mandate_id")
    if acting and hops[-1].get("mandate_id") != acting:
        return False, "chain does not terminate at the acting mandate"
    return True, f"{len(hops)} hop(s), root → acting, depths contiguous"
