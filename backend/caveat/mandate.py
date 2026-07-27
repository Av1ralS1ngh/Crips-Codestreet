"""Mandates as macaroons: authority that can only ever narrow.

A mandate is an HMAC caveat chain, not a signed blob of claims. The distinction is the
whole point:

  * Any holder can append a caveat offline, with no issuer round-trip, producing a
    strictly narrower credential.
  * No holder can remove a caveat, because each caveat's HMAC is chained off the previous
    signature and reversing that needs the root key. Attenuation is a property of the
    credential, not of a policy server a compromised agent can lie to.

Two third-party caveats carry the operational controls:

  revocation  every mandate carries `revocation@root:<root_id>` with a short-TTL
              discharge. The kill switch flips one row and every descendant credential
              in flight — including sub-agents nobody enumerated — fails closed at its
              next discharge fetch. Containment is O(1) in the number of descendants.

  step-up     `stepup@txn:<cart_hash>` forces the holder to obtain a discharge bound to
              one specific transaction, released only on a passkey tap. That discharge is
              a second factor, dynamically created and unique to that transaction — a
              design compatible with RBI's 2025 Authentication Directions while keeping a
              single standing mandate. (Compatible-by-design; not certified compliance.)

Note on third-party caveat keys: a production deployment encrypts the caveat key to the
third party inside the caveat id. Here both services are in-process, so the discharge
service stores caveat_id -> caveat_key itself. Same protocol shape, mocked transport.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from pymacaroons import Macaroon, Verifier
from pymacaroons.exceptions import MacaroonException

from .constraints import Constraint, ConstraintSet
from .entailment import EntailmentResult, entails

LOCATION = "caveat.amex"
REVOCATION_LOCATION = "revocation.caveat.amex"
STEPUP_LOCATION = "stepup.caveat.amex"

SCOPE_PREFIX = "scope = "
HOLDER_PREFIX = "holder = "
DEPTH_PREFIX = "depth = "

MACAROON_VERSION = 2


class DelegationRejected(Exception):
    """Raised when a child's declared scope does not entail its parent's."""

    def __init__(self, result: EntailmentResult) -> None:
        self.result = result
        detail = result.counterexample.summary() if result.counterexample else result.solver_result
        super().__init__(f"SCOPE_ESCALATION: {detail}")


def cart_hash(cart: Any) -> str:
    """Stable identifier for a cart, used to bind step-up discharges to one transaction."""
    raw = json.dumps(cart, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Mandate:
    """A minted credential plus the metadata the console and PDP need."""

    mandate_id: str
    root_id: str
    holder: str
    parent_id: str | None
    depth: int
    scope: ConstraintSet
    serialized: str
    created_at: int
    entailment_ms: float | None = None

    def macaroon(self) -> Macaroon:
        return Macaroon.deserialize(self.serialized)

    def caveat_texts(self) -> list[str]:
        """First-party caveat predicates as text. pymacaroons hands these back as bytes."""
        out = []
        for c in self.macaroon().caveats:
            cid = c.caveat_id
            if isinstance(cid, bytes):
                try:
                    cid = cid.decode("utf-8")
                except UnicodeDecodeError:
                    continue  # third-party caveat ids can be opaque binary
            out.append(cid)
        return out

    def to_dict(self, include_serialized: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "mandate_id": self.mandate_id,
            "root_id": self.root_id,
            "holder": self.holder,
            "parent_id": self.parent_id,
            "depth": self.depth,
            "scope": self.scope.to_dicts(),
            "scope_display": self.scope.describe(),
            "created_at": self.created_at,
            "entailment_ms": self.entailment_ms,
            "caveat_count": len(self.macaroon().caveats),
        }
        if include_serialized:
            out["serialized"] = self.serialized
        return out


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    reason: str | None = None
    detail: str | None = None
    offline_verifiable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "detail": self.detail,
            "offline_verifiable": self.offline_verifiable,
        }


class DischargeService:
    """Issues the third-party discharges for revocation freshness and step-up.

    Holds the caveat keys because, in this prototype, the third party runs in-process.
    """

    def __init__(self) -> None:
        self._caveat_keys: dict[str, str] = {}

    def register(self, caveat_id: str, caveat_key: str) -> None:
        self._caveat_keys[caveat_id] = caveat_key

    def key_for(self, caveat_id: str) -> str | None:
        return self._caveat_keys.get(caveat_id)

    def discharge(self, caveat_id: str, location: str, extra_caveats: Iterable[str] = ()) -> Macaroon:
        key = self._caveat_keys.get(caveat_id)
        if key is None:
            raise KeyError(f"no caveat key registered for {caveat_id!r}")
        m = Macaroon(location=location, identifier=caveat_id, key=key, version=MACAROON_VERSION)
        for c in extra_caveats:
            m.add_first_party_caveat(c)
        return m


class MandateAuthority:
    """Mints root mandates and validates every delegation hop."""

    def __init__(self, discharges: DischargeService | None = None) -> None:
        self.discharges = discharges or DischargeService()
        self._root_keys: dict[str, str] = {}

    # -- issuance ----------------------------------------------------------------------

    def issue_root(
        self,
        *,
        holder: str,
        scope: ConstraintSet,
        created_at: int,
        mandate_id: str | None = None,
        step_up_cart_hash: str | None = None,
    ) -> Mandate:
        mandate_id = mandate_id or f"mnd_{secrets.token_hex(8)}"
        root_id = mandate_id
        root_key = secrets.token_hex(32)
        self._root_keys[root_id] = root_key

        m = Macaroon(
            location=LOCATION, identifier=mandate_id, key=root_key, version=MACAROON_VERSION
        )
        m.add_first_party_caveat(f"{HOLDER_PREFIX}{holder}")
        m.add_first_party_caveat(f"{DEPTH_PREFIX}0")
        for c in scope:
            m.add_first_party_caveat(_scope_caveat(c))

        m = self._attach_revocation(m, root_id)
        if step_up_cart_hash:
            m = self._attach_step_up(m, step_up_cart_hash)

        return Mandate(
            mandate_id=mandate_id,
            root_id=root_id,
            holder=holder,
            parent_id=None,
            depth=0,
            scope=scope,
            serialized=m.serialize(),
            created_at=created_at,
        )

    def delegate(
        self,
        *,
        parent: Mandate,
        child_holder: str,
        declared_scope: ConstraintSet,
        created_at: int,
        mandate_id: str | None = None,
    ) -> tuple[Mandate, EntailmentResult]:
        """Issue a child mandate, but only if its declared scope entails the parent's.

        This is the declared-scope path — the one that matters when the credential
        crosses into ACP/AP2/MCP and the caveat chain does not travel with it.
        """
        result = entails(declared_scope, parent.scope)
        if not result.entailed:
            raise DelegationRejected(result)

        mandate_id = mandate_id or f"mnd_{secrets.token_hex(8)}"
        m = parent.macaroon()
        m.add_first_party_caveat(f"{HOLDER_PREFIX}{child_holder}")
        m.add_first_party_caveat(f"{DEPTH_PREFIX}{parent.depth + 1}")
        # Append only what the parent's chain does not already carry. Re-adding an
        # identical caveat is a no-op for evaluation, so skipping it keeps the chain
        # readable without weakening it. Whatever the child declares, the parent's
        # caveats stay on the chain and are still evaluated — the cryptographic backstop
        # behind the entailment proof.
        existing = set(parent.caveat_texts())
        for c in declared_scope:
            caveat = _scope_caveat(c)
            if caveat not in existing:
                m.add_first_party_caveat(caveat)
                existing.add(caveat)

        child = Mandate(
            mandate_id=mandate_id,
            root_id=parent.root_id,
            holder=child_holder,
            parent_id=parent.mandate_id,
            depth=parent.depth + 1,
            scope=declared_scope,
            serialized=m.serialize(),
            created_at=created_at,
            entailment_ms=result.elapsed_ms,
        )
        return child, result

    def attenuate(
        self,
        *,
        parent: Mandate,
        child_holder: str,
        added: Sequence[Constraint],
        created_at: int,
        mandate_id: str | None = None,
    ) -> tuple[Mandate, EntailmentResult]:
        """Convenience wrapper: the child inherits the parent's scope plus extra limits.

        Always entails by construction, but we still run the proof so the ledger records
        one, and so a bug in this helper cannot silently widen authority.
        """
        return self.delegate(
            parent=parent,
            child_holder=child_holder,
            declared_scope=parent.scope.with_added(added),
            created_at=created_at,
            mandate_id=mandate_id,
        )

    # -- third-party caveats -----------------------------------------------------------

    def _attach_revocation(self, m: Macaroon, root_id: str) -> Macaroon:
        caveat_key = secrets.token_hex(32)
        caveat_id = f"revocation@root:{root_id}"
        self.discharges.register(caveat_id, caveat_key)
        m.add_third_party_caveat(REVOCATION_LOCATION, caveat_key, caveat_id)
        return m

    def _attach_step_up(self, m: Macaroon, txn_hash: str) -> Macaroon:
        caveat_key = secrets.token_hex(32)
        caveat_id = f"stepup@txn:{txn_hash}"
        self.discharges.register(caveat_id, caveat_key)
        m.add_third_party_caveat(STEPUP_LOCATION, caveat_key, caveat_id)
        return m

    def require_step_up(self, mandate: Mandate, txn_hash: str) -> Mandate:
        """Bind a fresh step-up requirement to one specific transaction."""
        m = self._attach_step_up(mandate.macaroon(), txn_hash)
        return Mandate(
            mandate_id=mandate.mandate_id,
            root_id=mandate.root_id,
            holder=mandate.holder,
            parent_id=mandate.parent_id,
            depth=mandate.depth,
            scope=mandate.scope,
            serialized=m.serialize(),
            created_at=mandate.created_at,
            entailment_ms=mandate.entailment_ms,
        )

    # -- verification ------------------------------------------------------------------

    def verify(
        self,
        mandate: Mandate,
        *,
        discharges: Sequence[Macaroon] = (),
        satisfied_predicates: Iterable[str] = (),
    ) -> VerificationResult:
        """Cryptographically verify the caveat chain.

        Scope caveats are satisfied here because the PDP evaluates them separately against
        the concrete transaction; this call answers the narrower question "is this
        credential authentic and unrevoked", not "should this purchase go through".
        """
        root_key = self._root_keys.get(mandate.root_id)
        if root_key is None:
            return VerificationResult(False, "UNKNOWN_ROOT", "no root key for this mandate")

        v = Verifier()
        satisfied = set(satisfied_predicates)
        v.satisfy_general(
            lambda predicate: (
                predicate.startswith(SCOPE_PREFIX)
                or predicate.startswith(HOLDER_PREFIX)
                or predicate.startswith(DEPTH_PREFIX)
                or predicate in satisfied
            )
        )
        try:
            # Binding is called on the authorizing macaroon, passing the discharge —
            # each discharge is bound to this credential's signature so it cannot be
            # replayed against a different mandate.
            authorizing = mandate.macaroon()
            bound = [authorizing.prepare_for_request(d) for d in discharges]
            ok = v.verify(authorizing, root_key, bound)
        except MacaroonException as exc:
            return VerificationResult(False, "CAVEAT_UNSATISFIED", str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            return VerificationResult(False, "VERIFICATION_ERROR", str(exc))

        if not ok:
            return VerificationResult(False, "SIGNATURE_INVALID", "macaroon signature check failed")
        return VerificationResult(True)

    def root_key(self, root_id: str) -> str | None:
        return self._root_keys.get(root_id)

    def adopt_root_key(self, root_id: str, key: str) -> None:
        """Used when rehydrating state from the store."""
        self._root_keys[root_id] = key


def _scope_caveat(c: Constraint) -> str:
    return SCOPE_PREFIX + json.dumps(c.to_dict(), sort_keys=True, separators=(",", ":"))


def scope_from_caveats(caveat_texts: Iterable[str]) -> ConstraintSet:
    """Recover the accumulated scope from a serialized macaroon's caveats.

    This is the chained path: every scope caveat ever appended, in order. A holder that
    tried to widen a bound still carries the tighter parent caveat here, so evaluating
    all of them is the cryptographic backstop behind the entailment proof.
    """
    from .constraints import constraint_from_dict

    out = []
    for text in caveat_texts:
        if text.startswith(SCOPE_PREFIX):
            out.append(constraint_from_dict(json.loads(text[len(SCOPE_PREFIX) :])))
    return ConstraintSet(out)
