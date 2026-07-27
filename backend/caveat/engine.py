"""Composition root: one object that wires the whole governance plane together.

Everything the API, the agent harness, and the tests need hangs off a CaveatEngine.
Construct one, seed it, and drive it. Timestamps are always passed in explicitly so any
run is reproducible — a demo that replays identically is a demo that cannot embarrass you
on stage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .cart import Cart
from .constraints import Constraint, ConstraintSet
from .entailment import EntailmentResult, entails, naive_subset_check
from .ledger import MerkleLedger
from .mandate import (
    DelegationRejected,
    DischargeService,
    Mandate,
    MandateAuthority,
)
from .pdp import Decision, PolicyDecisionPoint
from .revocation import RevocationRecord, RevocationService
from .stepup import StepUpService
from .store import Store

Listener = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class DelegationOutcome:
    accepted: bool
    entailment: EntailmentResult
    naive_verdict: bool
    mandate: Mandate | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "entailment": self.entailment.to_dict(),
            # What a set-containment check would have said. When these disagree, the
            # naive check was about to authorize an escalation.
            "naive_subset_check": self.naive_verdict,
            "naive_disagrees": self.naive_verdict != self.accepted,
            "mandate": self.mandate.to_dict(include_serialized=False) if self.mandate else None,
        }


class CaveatEngine:
    def __init__(self, store: Store | None = None) -> None:
        self.store = store or Store()
        self.discharges = DischargeService()
        self.authority = MandateAuthority(self.discharges)
        self.revocation = RevocationService(self.store, self.discharges)
        self.stepup = StepUpService(self.discharges)
        self.ledger = MerkleLedger()
        self.pdp = PolicyDecisionPoint(
            store=self.store,
            authority=self.authority,
            revocation=self.revocation,
            stepup=self.stepup,
            ledger=self.ledger,
        )
        self._mandates: dict[str, Mandate] = {}
        self._listeners: list[Listener] = []

    # -- events ------------------------------------------------------------------------

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        for listener in list(self._listeners):
            try:
                listener(kind, payload)
            except Exception:  # pragma: no cover - a bad listener must not break policy
                pass

    # -- operators ---------------------------------------------------------------------

    def register_operator(
        self, operator_id: str, name: str, *, now: int, metadata: dict[str, Any] | None = None
    ) -> None:
        self.store.put_operator(operator_id, name, now, metadata)
        entry = self.ledger.append(
            "operator_registered", {"operator_id": operator_id, "name": name}, ts=now
        )
        self.store.append_ledger(entry)
        self._emit("operator_registered", {"operator_id": operator_id, "name": name})

    # -- mandates ----------------------------------------------------------------------

    def grant(
        self,
        *,
        holder: str,
        scope: ConstraintSet,
        now: int,
        mandate_id: str | None = None,
    ) -> Mandate:
        """Card Member grants scoped authority. This is the root of a delegation tree."""
        mandate = self.authority.issue_root(
            holder=holder, scope=scope, created_at=now, mandate_id=mandate_id
        )
        self._persist(mandate)
        key = self.authority.root_key(mandate.root_id)
        if key:
            self.store.put_root_key(mandate.root_id, key)
        entry = self.ledger.append(
            "mandate_granted",
            {
                "mandate_id": mandate.mandate_id,
                "root_id": mandate.root_id,
                "holder": holder,
                "scope": scope.to_dicts(),
                "depth": 0,
            },
            ts=now,
        )
        self.store.append_ledger(entry)
        self._emit("mandate_granted", mandate.to_dict(include_serialized=False))
        return mandate

    def delegate(
        self,
        *,
        parent: Mandate,
        child_holder: str,
        declared_scope: ConstraintSet,
        now: int,
        mandate_id: str | None = None,
    ) -> DelegationOutcome:
        """Attempt a delegation hop. Rejection is a first-class, recorded outcome."""
        naive = naive_subset_check(declared_scope, parent.scope)
        try:
            child, result = self.authority.delegate(
                parent=parent,
                child_holder=child_holder,
                declared_scope=declared_scope,
                created_at=now,
                mandate_id=mandate_id,
            )
        except DelegationRejected as exc:
            result = exc.result
            entry = self.ledger.append(
                "delegation_rejected",
                {
                    "parent_id": parent.mandate_id,
                    "root_id": parent.root_id,
                    "child_holder": child_holder,
                    "declared_scope": declared_scope.to_dicts(),
                    "reason": "SCOPE_ESCALATION",
                    "entailment": result.to_dict(),
                    "naive_subset_check": naive,
                },
                ts=now,
            )
            self.store.append_ledger(entry)
            self.store.record_operator_event(
                child_holder, "scope_escalation_attempt", now, {"parent_id": parent.mandate_id}
            )
            outcome = DelegationOutcome(accepted=False, entailment=result, naive_verdict=naive)
            self._emit("delegation_rejected", outcome.to_dict())
            return outcome

        self._persist(child)
        entry = self.ledger.append(
            "delegation_accepted",
            {
                "mandate_id": child.mandate_id,
                "parent_id": parent.mandate_id,
                "root_id": child.root_id,
                "child_holder": child_holder,
                "declared_scope": declared_scope.to_dicts(),
                "entailment": result.to_dict(),
                "depth": child.depth,
            },
            ts=now,
        )
        self.store.append_ledger(entry)
        outcome = DelegationOutcome(
            accepted=True, entailment=result, naive_verdict=naive, mandate=child
        )
        self._emit("delegation_accepted", outcome.to_dict())
        return outcome

    def attenuate(
        self,
        *,
        parent: Mandate,
        child_holder: str,
        added: Sequence[Constraint],
        now: int,
        mandate_id: str | None = None,
    ) -> DelegationOutcome:
        return self.delegate(
            parent=parent,
            child_holder=child_holder,
            declared_scope=parent.scope.with_added(added),
            now=now,
            mandate_id=mandate_id,
        )

    def _persist(self, mandate: Mandate) -> None:
        self._mandates[mandate.mandate_id] = mandate
        self.store.put_mandate(mandate)

    def mandate(self, mandate_id: str) -> Mandate | None:
        return self._mandates.get(mandate_id)

    def mandates(self) -> list[Mandate]:
        return list(self._mandates.values())

    def descendants(self, mandate_id: str) -> list[Mandate]:
        out: list[Mandate] = []
        frontier = [mandate_id]
        while frontier:
            current = frontier.pop()
            for m in self._mandates.values():
                if m.parent_id == current:
                    out.append(m)
                    frontier.append(m.mandate_id)
        return out

    # -- authorization -----------------------------------------------------------------

    def authorize(
        self,
        *,
        mandate: Mandate,
        intent_cart: Cart,
        executed_cart: Cart,
        now: int,
        geo: str = "IN",
        txn_id: str | None = None,
        allow_step_up: bool = True,
    ) -> Decision:
        decision = self.pdp.evaluate(
            mandate=mandate,
            intent_cart=intent_cart,
            executed_cart=executed_cart,
            now=now,
            geo=geo,
            txn_id=txn_id,
            allow_step_up=allow_step_up,
        )
        self._record_operator_signal(decision, now)
        self._emit("decision", decision.to_dict())
        return decision

    def _record_operator_signal(self, decision: Decision, now: int) -> None:
        self.store.record_operator_event(
            decision.holder,
            {
                "ALLOW": "authorized",
                "STEP_UP": "step_up_required",
                "DENY": "denied",
            }[decision.outcome],
            now,
            {"txn_id": decision.txn_id, "reasons": list(decision.reason_codes)},
        )
        if decision.diff.diverged():
            self.store.record_operator_event(
                decision.holder, "cart_divergence", now, {"txn_id": decision.txn_id}
            )
        if decision.verdict == "INJECTION_COMPROMISE":
            self.store.record_operator_event(
                decision.holder, "injection_absorbed", now, {"txn_id": decision.txn_id}
            )

    # -- kill switch -------------------------------------------------------------------

    def revoke(self, root_id: str, *, now: int, cause: str, actor: str = "cardholder") -> RevocationRecord:
        record = self.revocation.revoke(root_id, now=now, cause=cause, actor=actor)
        entry = self.ledger.append("revocation", record.to_dict(), ts=now)
        self.store.append_ledger(entry)
        self._emit("revocation", record.to_dict())
        return record

    # -- step-up -----------------------------------------------------------------------

    def satisfy_step_up(self, challenge_id: str, *, now: int, method: str = "passkey") -> dict[str, Any]:
        challenge, err = self.stepup.satisfy(challenge_id, now=now, method=method)
        payload = {
            "challenge": challenge.to_dict() if challenge else None,
            "error": err,
        }
        if challenge and not err:
            entry = self.ledger.append(
                "step_up_satisfied",
                {
                    "challenge_id": challenge.challenge_id,
                    "cart_hash": challenge.cart_hash,
                    "method": method,
                    "root_id": challenge.root_id,
                },
                ts=now,
            )
            self.store.append_ledger(entry)
        self._emit("step_up", payload)
        return payload

    # -- introspection -----------------------------------------------------------------

    def ledger_root(self) -> str:
        return self.ledger.root()

    def verify_ledger(self) -> tuple[bool, str | None]:
        return self.ledger.verify_chain()

    def check_entailment(self, child: ConstraintSet, parent: ConstraintSet) -> EntailmentResult:
        return entails(child, parent)
