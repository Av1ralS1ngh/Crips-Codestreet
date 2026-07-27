"""Step-up as a third-party discharge bound to one specific transaction.

RBI's (Authentication Mechanisms for Digital Payment Transactions) Directions, 2025 —
compliance required from 1 April 2026 — mandate at least two authentication factors, at
least one of which is dynamically created and unique to that transaction. A standing
agent mandate authorizing N autonomous purchases sits awkwardly against that.

A third-party caveat resolves the tension without abandoning the standing mandate: the
credential carries `stepup@txn:<cart_hash>`, and the only way to discharge it is a
passkey tap that mints a macaroon bound to that exact cart hash. The discharge *is* a
second factor, dynamically created and unique to that transaction.

We present this as a design compatible with the Directions. It is not certified
compliance, and the deck must not claim otherwise.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

from pymacaroons import Macaroon

from .mandate import STEPUP_LOCATION, DischargeService

REASON_STEP_UP_REQUIRED = "STEP_UP_REQUIRED"
REASON_STEP_UP_EXPIRED = "STEP_UP_CHALLENGE_EXPIRED"
REASON_STEP_UP_WRONG_CART = "STEP_UP_BOUND_TO_DIFFERENT_CART"

CHALLENGE_TTL_S = 120


@dataclass
class Challenge:
    """A pending human authentication, pinned to one cart."""

    challenge_id: str
    root_id: str
    mandate_id: str
    cart_hash: str
    amount: int
    created_at: int
    expires_at: int
    satisfied_at: int | None = None
    method: str | None = None

    @property
    def satisfied(self) -> bool:
        return self.satisfied_at is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "challenge_id": self.challenge_id,
            "root_id": self.root_id,
            "mandate_id": self.mandate_id,
            "cart_hash": self.cart_hash,
            "amount": self.amount,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "satisfied": self.satisfied,
            "satisfied_at": self.satisfied_at,
            "method": self.method,
        }


class StepUpService:
    """Issues per-transaction challenges and, on a passkey tap, their discharges."""

    def __init__(self, discharges: DischargeService) -> None:
        self.discharges = discharges
        self._challenges: dict[str, Challenge] = {}
        self._by_cart: dict[str, str] = {}

    def challenge(
        self, *, root_id: str, mandate_id: str, cart_hash: str, amount: int, now: int
    ) -> Challenge:
        challenge_id = f"chg_{secrets.token_hex(6)}"
        ch = Challenge(
            challenge_id=challenge_id,
            root_id=root_id,
            mandate_id=mandate_id,
            cart_hash=cart_hash,
            amount=amount,
            created_at=now,
            expires_at=now + CHALLENGE_TTL_S,
        )
        self._challenges[challenge_id] = ch
        self._by_cart[cart_hash] = challenge_id
        # Register the caveat key so a discharge can be minted once the human taps.
        caveat_id = f"stepup@txn:{cart_hash}"
        if self.discharges.key_for(caveat_id) is None:
            self.discharges.register(caveat_id, secrets.token_hex(32))
        return ch

    def get(self, challenge_id: str) -> Challenge | None:
        return self._challenges.get(challenge_id)

    def for_cart(self, cart_hash: str) -> Challenge | None:
        cid = self._by_cart.get(cart_hash)
        return self._challenges.get(cid) if cid else None

    def pending(self) -> list[Challenge]:
        return [c for c in self._challenges.values() if not c.satisfied]

    def satisfy(
        self, challenge_id: str, *, now: int, method: str = "passkey"
    ) -> tuple[Challenge | None, str | None]:
        """Record a human authentication. Returns (challenge, error_reason)."""
        ch = self._challenges.get(challenge_id)
        if ch is None:
            return None, "UNKNOWN_CHALLENGE"
        if now > ch.expires_at:
            return ch, REASON_STEP_UP_EXPIRED
        ch.satisfied_at = now
        ch.method = method
        return ch, None

    def discharge(self, cart_hash: str, *, now: int) -> tuple[Macaroon | None, str | None]:
        """Mint the discharge for a satisfied challenge, bound to this cart only."""
        ch = self.for_cart(cart_hash)
        if ch is None:
            return None, REASON_STEP_UP_REQUIRED
        if not ch.satisfied:
            return None, REASON_STEP_UP_REQUIRED
        if now > ch.expires_at:
            return None, REASON_STEP_UP_EXPIRED
        caveat_id = f"stepup@txn:{cart_hash}"
        try:
            m = self.discharges.discharge(
                caveat_id,
                STEPUP_LOCATION,
                extra_caveats=[f"txn = {cart_hash}", f"authenticated_at = {ch.satisfied_at}"],
            )
        except KeyError:
            return None, REASON_STEP_UP_REQUIRED
        return m, None

    def satisfied_predicates(self, cart_hash: str) -> set[str]:
        ch = self.for_cart(cart_hash)
        out = {f"txn = {cart_hash}"}
        if ch and ch.satisfied:
            out.add(f"authenticated_at = {ch.satisfied_at}")
        return out
