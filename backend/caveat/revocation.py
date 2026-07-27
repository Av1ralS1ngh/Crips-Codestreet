"""Revocation as a freshness caveat — O(1) blast-radius containment.

Every mandate carries a third-party caveat `revocation@root:<root_id>` whose discharge
has a short TTL. Nothing else about revocation is distributed: the kill switch writes one
row, and every descendant credential in flight — including sub-agents nobody enumerated
or registered — fails closed the next time it needs a fresh discharge.

That is the whole design. There is no token sweep, no session registry to walk, and no
cache-invalidation storm, so containment cost does not grow with the number of agents
that inherited the mandate.

Honest bound: "sub-second" is really "sub-TTL". Revocation takes effect within
DISCHARGE_TTL_S of the row being written, and we quote the measured number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pymacaroons import Macaroon

from .mandate import REVOCATION_LOCATION, DischargeService
from .store import Store

# How long a freshness discharge stays good. Shorter = tighter containment, more traffic.
DISCHARGE_TTL_S = 2

REASON_MANDATE_REVOKED = "MANDATE_REVOKED"
REASON_DISCHARGE_STALE = "DISCHARGE_STALE"


@dataclass(frozen=True)
class RevocationRecord:
    root_id: str
    revoked_at: int
    cause: str
    actor: str
    rows_written: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_id": self.root_id,
            "revoked_at": self.revoked_at,
            "cause": self.cause,
            "actor": self.actor,
            "rows_written": self.rows_written,
        }


class RevocationService:
    """Issues freshness discharges, and refuses to once a root is revoked."""

    def __init__(self, store: Store, discharges: DischargeService) -> None:
        self.store = store
        self.discharges = discharges

    # -- the kill switch ---------------------------------------------------------------

    def revoke(self, root_id: str, *, now: int, cause: str, actor: str) -> RevocationRecord:
        rows = self.store.revoke(root_id, now, cause, actor)
        return RevocationRecord(
            root_id=root_id, revoked_at=now, cause=cause, actor=actor, rows_written=rows
        )

    def is_revoked(self, root_id: str) -> bool:
        return self.store.is_revoked(root_id)

    # -- discharge ---------------------------------------------------------------------

    def discharge(self, root_id: str, *, now: int) -> Macaroon | None:
        """Mint a short-lived freshness discharge, or None if the root is revoked.

        The TTL rides on the discharge as a first-party caveat, so a captured discharge
        cannot be replayed indefinitely after revocation.
        """
        if self.is_revoked(root_id):
            return None
        caveat_id = f"revocation@root:{root_id}"
        return self.discharges.discharge(
            caveat_id,
            REVOCATION_LOCATION,
            extra_caveats=[f"fresh_until = {now + DISCHARGE_TTL_S}"],
        )

    def freshness_predicates(self, now: int) -> set[str]:
        """Predicates the verifier should accept as satisfied at time `now`.

        A discharge carries `fresh_until = <ts>`; it satisfies verification only while
        that timestamp is still in the future.
        """
        return {f"fresh_until = {t}" for t in range(now, now + DISCHARGE_TTL_S + 1)}


def is_fresh(predicate: str, now: int) -> bool:
    """Evaluate a `fresh_until = <ts>` predicate without enumerating timestamps."""
    prefix = "fresh_until = "
    if not predicate.startswith(prefix):
        return False
    try:
        return int(predicate[len(prefix) :]) >= now
    except ValueError:
        return False
