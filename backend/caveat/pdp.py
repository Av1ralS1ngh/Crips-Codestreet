"""The Policy Decision Point: the thing that says no.

Everything upstream — the LLM, the agent framework, the merchant page — is untrusted
input. The PDP is deterministic: same facts in, same decision out, every time. No language
model participates in the decision. It plans and explains; this engine decides.

Checks run in fail-closed order, but all findings are collected rather than
short-circuited, because an operator debugging a denial wants every reason at once:

  1. revocation      is the root still live?
  2. credential      does the macaroon chain verify under its discharges?
  3. cart divergence does the executed cart still match the signed intent?
  4. scope           does every line and the aggregate satisfy the accumulated caveats?
  5. step-up         is the total over a threshold that demands a human?

Outcome is ALLOW, STEP_UP, or DENY plus machine-readable reason codes, and it is written
to the Merkle ledger before it is returned. If it is not in the ledger, it did not happen.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from .cart import Cart, CartDiff, diff_carts
from .constraints import (
    REASON_CATEGORY_NOT_ALLOWED,
    REASON_MCC_NOT_ALLOWED,
    REASON_MERCHANT_NOT_ALLOWED,
    AmountMax,
    CategoryAllow,
    ConstraintSet,
    CumulativeMax,
    DecisionContext,
    ExpiresAt,
    GeoAllow,
    MccAllow,
    MerchantAllow,
    NotBefore,
    StepUpOver,
    VelocityMax,
    Violation,
    fmt_money,
)
from .ledger import MerkleLedger
from .mandate import Mandate, MandateAuthority, scope_from_caveats
from .revocation import RevocationService, is_fresh
from .stepup import StepUpService
from .store import AuthorizationRow, Store

Outcome = Literal["ALLOW", "STEP_UP", "DENY"]

# Reason codes beyond the per-constraint ones in constraints.py.
REASON_MANDATE_REVOKED = "MANDATE_REVOKED"
REASON_CREDENTIAL_INVALID = "CREDENTIAL_INVALID"
REASON_CART_DIVERGENCE = "MANDATE_CART_DIVERGENCE"
REASON_MERCHANT_SWAPPED = "MERCHANT_SWAPPED_AFTER_INTENT"
REASON_STEP_UP_REQUIRED = "STEP_UP_REQUIRED"

# Liability verdicts. These bridge into Problem Statement #1.
VERDICT_WITHIN_MANDATE = "WITHIN_MANDATE"
VERDICT_AGENT_ERROR = "AGENT_ERROR"
VERDICT_INJECTION_COMPROMISE = "INJECTION_COMPROMISE"
VERDICT_MERCHANT_MISREPRESENTATION = "MERCHANT_MISREPRESENTATION"

LIABLE_PARTY = {
    VERDICT_WITHIN_MANDATE: "cardholder",
    VERDICT_AGENT_ERROR: "amex (Agent Purchase Protection)",
    VERDICT_INJECTION_COMPROMISE: "fraud — cardholder not liable, merchant held harmless",
    VERDICT_MERCHANT_MISREPRESENTATION: "merchant",
}


@dataclass(frozen=True)
class Decision:
    txn_id: str
    outcome: Outcome
    reason_codes: tuple[str, ...]
    violations: tuple[Violation, ...]
    verdict: str
    liable_party: str
    mandate_id: str
    root_id: str
    holder: str
    intent_hash: str
    cart_hash: str
    diff: CartDiff
    amount: int
    elapsed_ms: float
    ledger_seq: int
    ts: int
    step_up_challenge_id: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def allowed(self) -> bool:
        return self.outcome == "ALLOW"

    def headline(self) -> str:
        if self.outcome == "ALLOW":
            return f"ALLOW {fmt_money(self.amount)}"
        primary = self.reason_codes[0] if self.reason_codes else "UNSPECIFIED"
        return f"{self.outcome} / {primary}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "txn_id": self.txn_id,
            "outcome": self.outcome,
            "headline": self.headline(),
            "reason_codes": list(self.reason_codes),
            "violations": [
                {"reason": v.reason, "detail": v.detail, "constraint": v.constraint}
                for v in self.violations
            ],
            "verdict": self.verdict,
            "liable_party": self.liable_party,
            "mandate_id": self.mandate_id,
            "root_id": self.root_id,
            "holder": self.holder,
            "intent_hash": self.intent_hash,
            "cart_hash": self.cart_hash,
            "diff": self.diff.to_dict(),
            "amount": self.amount,
            "amount_display": fmt_money(self.amount),
            "elapsed_ms": round(self.elapsed_ms, 3),
            "ledger_seq": self.ledger_seq,
            "ts": self.ts,
            "step_up_challenge_id": self.step_up_challenge_id,
            "notes": list(self.notes),
        }


class PolicyDecisionPoint:
    def __init__(
        self,
        *,
        store: Store,
        authority: MandateAuthority,
        revocation: RevocationService,
        stepup: StepUpService,
        ledger: MerkleLedger,
    ) -> None:
        self.store = store
        self.authority = authority
        self.revocation = revocation
        self.stepup = stepup
        self.ledger = ledger

    # ----------------------------------------------------------------------------------

    def evaluate(
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
        started = time.perf_counter()
        txn_id = txn_id or f"txn_{secrets.token_hex(6)}"

        reasons: list[str] = []
        violations: list[Violation] = []
        notes: list[str] = []
        challenge_id: str | None = None

        diff = diff_carts(intent_cart, executed_cart)
        total = executed_cart.total()

        # 1. Revocation. Cheapest check, hardest stop.
        revoked = self.revocation.is_revoked(mandate.root_id)
        if revoked:
            reasons.append(REASON_MANDATE_REVOKED)
            rec = self.store.revocation(mandate.root_id)
            if rec is not None:
                notes.append(f"root revoked at {rec['revoked_at']} by {rec['actor']}: {rec['cause']}")

        # 2. Credential verification, including freshness discharge.
        if not revoked:
            cred_ok, cred_note = self._verify_credential(mandate, now=now, cart_hash=executed_cart.hash())
            if not cred_ok:
                reasons.append(REASON_CREDENTIAL_INVALID)
                if cred_note:
                    notes.append(cred_note)

        # 3. Cart-vs-intent divergence: the AP2 post-signature mutation class.
        if diff.diverged():
            if diff.merchant_changed:
                reasons.append(REASON_MERCHANT_SWAPPED)
            reasons.append(REASON_CART_DIVERGENCE)
            notes.append(diff.summary())

        # 4. Scope. Evaluate the accumulated chain, not just this mandate's declared set —
        #    a child that declared a wider bound still carries its parent's tighter caveat.
        effective = self._effective_scope(mandate)
        violations.extend(self._evaluate_scope(effective, executed_cart, geo=geo, now=now, mandate=mandate))
        for v in violations:
            if v.reason not in reasons:
                reasons.append(v.reason)

        # 5. Step-up. A threshold breach is only outstanding if the human has not already
        #    discharged it for *this* cart — the discharge is bound to one cart hash, so a
        #    satisfied challenge authorizes this transaction and no other.
        if REASON_STEP_UP_REQUIRED in reasons:
            existing = self.stepup.for_cart(executed_cart.hash())
            if existing is not None and existing.satisfied and now <= existing.expires_at:
                reasons = [r for r in reasons if r != REASON_STEP_UP_REQUIRED]
                violations = [v for v in violations if v.reason != REASON_STEP_UP_REQUIRED]
                notes.append(
                    f"step-up satisfied at {existing.satisfied_at} via {existing.method}, "
                    f"bound to cart {existing.cart_hash[:12]}…"
                )

        hard_denied = any(r != REASON_STEP_UP_REQUIRED for r in reasons)
        step_up_needed = REASON_STEP_UP_REQUIRED in reasons

        outcome: Outcome
        if hard_denied:
            outcome = "DENY"
        elif step_up_needed and allow_step_up:
            outcome = "STEP_UP"
            ch = self.stepup.challenge(
                root_id=mandate.root_id,
                mandate_id=mandate.mandate_id,
                cart_hash=executed_cart.hash(),
                amount=total,
                now=now,
            )
            challenge_id = ch.challenge_id
            notes.append(
                f"step-up challenge {ch.challenge_id} bound to cart {executed_cart.hash()[:12]}…"
            )
        elif step_up_needed:
            outcome = "DENY"
        else:
            outcome = "ALLOW"

        verdict = classify_liability(outcome=outcome, reasons=tuple(reasons), diff=diff)
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        entry = self.ledger.append(
            "decision",
            {
                "txn_id": txn_id,
                "outcome": outcome,
                "reason_codes": reasons,
                "verdict": verdict,
                "mandate_id": mandate.mandate_id,
                "root_id": mandate.root_id,
                "holder": mandate.holder,
                "intent_hash": intent_cart.hash(),
                "cart_hash": executed_cart.hash(),
                "amount": total,
                "diff": diff.to_dict(),
                "violations": [
                    {"reason": v.reason, "detail": v.detail, "constraint": v.constraint}
                    for v in violations
                ],
            },
            ts=now,
        )
        self.store.append_ledger(entry)

        if outcome == "ALLOW":
            self.store.record_authorization(
                AuthorizationRow(
                    txn_id=txn_id,
                    root_id=mandate.root_id,
                    mandate_id=mandate.mandate_id,
                    amount=total,
                    merchant=executed_cart.merchant,
                    mcc=executed_cart.lines[0].mcc if executed_cart.lines else 0,
                    ts=now,
                )
            )

        return Decision(
            txn_id=txn_id,
            outcome=outcome,
            reason_codes=tuple(reasons),
            violations=tuple(violations),
            verdict=verdict,
            liable_party=LIABLE_PARTY.get(verdict, "under review"),
            mandate_id=mandate.mandate_id,
            root_id=mandate.root_id,
            holder=mandate.holder,
            intent_hash=intent_cart.hash(),
            cart_hash=executed_cart.hash(),
            diff=diff,
            amount=total,
            elapsed_ms=elapsed_ms,
            ledger_seq=entry.seq,
            ts=now,
            step_up_challenge_id=challenge_id,
            notes=tuple(notes),
        )

    # -- internals ---------------------------------------------------------------------

    def _effective_scope(self, mandate: Mandate) -> ConstraintSet:
        """Every scope caveat on the chain, ANDed. The cryptographic backstop."""
        chained = scope_from_caveats(mandate.caveat_texts())
        if len(chained) == 0:
            return mandate.scope
        return chained

    def _verify_credential(
        self, mandate: Mandate, *, now: int, cart_hash: str
    ) -> tuple[bool, str | None]:
        discharges = []
        rev = self.revocation.discharge(mandate.root_id, now=now)
        if rev is None:
            return False, "no freshness discharge available — root revoked"
        discharges.append(rev)

        step, _err = self.stepup.discharge(cart_hash, now=now)
        if step is not None:
            discharges.append(step)

        satisfied = {p for p in self.revocation.freshness_predicates(now)}
        satisfied |= self.stepup.satisfied_predicates(cart_hash)

        result = self.authority.verify(
            mandate, discharges=discharges, satisfied_predicates=satisfied
        )
        if not result.ok:
            return False, f"{result.reason}: {result.detail}"
        return True, None

    def _evaluate_scope(
        self,
        scope: ConstraintSet,
        cart: Cart,
        *,
        geo: str,
        now: int,
        mandate: Mandate,
    ) -> list[Violation]:
        """Set constraints bind every line; magnitude constraints bind the aggregate."""
        out: list[Violation] = []
        total = cart.total()
        seen: set[tuple[str, str]] = set()

        def add(v: Violation | None) -> None:
            if v is None:
                return
            key = (v.reason, v.detail)
            if key not in seen:
                seen.add(key)
                out.append(v)

        for c in scope:
            if isinstance(c, MerchantAllow):
                add(c.check(_ctx(amount=total, merchant=cart.merchant, mcc=0, category="", geo=geo, ts=now)))
            elif isinstance(c, MccAllow):
                for line in cart.lines:
                    add(
                        c.check(
                            _ctx(
                                amount=line.amount,
                                merchant=cart.merchant,
                                mcc=line.mcc,
                                category=line.category,
                                geo=geo,
                                ts=now,
                            )
                        )
                    )
            elif isinstance(c, CategoryAllow):
                for line in cart.lines:
                    add(
                        c.check(
                            _ctx(
                                amount=line.amount,
                                merchant=cart.merchant,
                                mcc=line.mcc,
                                category=line.category,
                                geo=geo,
                                ts=now,
                            )
                        )
                    )
            elif isinstance(c, GeoAllow):
                add(c.check(_ctx(amount=total, merchant=cart.merchant, mcc=0, category="", geo=geo, ts=now)))
            elif isinstance(c, (AmountMax, StepUpOver, ExpiresAt, NotBefore)):
                add(c.check(_ctx(amount=total, merchant=cart.merchant, mcc=0, category="", geo=geo, ts=now)))
            elif isinstance(c, CumulativeMax):
                spent = self.store.cumulative_spend(mandate.root_id) + total
                add(c.check(_ctx(amount=total, merchant=cart.merchant, mcc=0, category="", geo=geo, ts=now, cumulative=spent)))
            elif isinstance(c, VelocityMax):
                count = self.store.velocity_count(mandate.root_id, c.window_s, now) + 1
                add(
                    c.check(
                        _ctx(
                            amount=total,
                            merchant=cart.merchant,
                            mcc=0,
                            category="",
                            geo=geo,
                            ts=now,
                            velocity={c.window_s: count},
                        )
                    )
                )
        return out


def _ctx(**kwargs: Any) -> DecisionContext:
    return DecisionContext(**kwargs)


def classify_liability(*, outcome: Outcome, reasons: Sequence[str], diff: CartDiff) -> str:
    """Route liability. This is what makes an agent dispute adjudicable in seconds.

    Ordering matters: a merchant that mutated a locked cart is a different party from an
    agent that wandered outside its own declared scope, and both differ from an injected
    instruction that added line items the human never saw.
    """
    reasons = tuple(reasons)

    if REASON_MERCHANT_SWAPPED in reasons:
        return VERDICT_MERCHANT_MISREPRESENTATION

    if REASON_CART_DIVERGENCE in reasons:
        # Lines appeared after the human signed intent. Nobody authorized those.
        if diff.added or (diff.modified and diff.delta > 0):
            return VERDICT_INJECTION_COMPROMISE
        return VERDICT_MERCHANT_MISREPRESENTATION

    scope_breaches = {
        REASON_MCC_NOT_ALLOWED,
        REASON_MERCHANT_NOT_ALLOWED,
        REASON_CATEGORY_NOT_ALLOWED,
    }
    if scope_breaches & set(reasons):
        # The agent went somewhere its mandate never allowed: registered-agent error,
        # which is exactly what Agent Purchase Protection undertook to cover.
        return VERDICT_AGENT_ERROR

    if outcome == "DENY" and REASON_MANDATE_REVOKED in reasons:
        return VERDICT_AGENT_ERROR

    return VERDICT_WITHIN_MANDATE
