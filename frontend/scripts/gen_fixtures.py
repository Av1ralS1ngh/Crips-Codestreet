"""Generate console fixtures by driving the real CaveatEngine.

The mock layer in the console must carry the exact payload shapes the API will return,
so we produce them from the kernel rather than hand-writing JSON that drifts.
"""

from __future__ import annotations

import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))

from caveat.cart import Cart, CartLine
from caveat.constraints import (
    AmountMax,
    CategoryAllow,
    ConstraintSet,
    CumulativeMax,
    MccAllow,
    MerchantAllow,
    StepUpOver,
    fmt_money,
)
from caveat.engine import CaveatEngine

T0 = 1_753_600_000

GROCERY_SCOPE = ConstraintSet(
    [
        AmountMax(1_000_000),
        CumulativeMax(5_000_000),
        CategoryAllow(("groceries", "appliances")),
        MerchantAllow(("m_bigbasket", "m_croma")),
        MccAllow((5411, 5722)),
        StepUpOver(800_000),
    ]
)

ESPRESSO = CartLine(
    sku="sku_espresso_01",
    description="Budget espresso machine",
    amount=400_000,
    mcc=5722,
    category="appliances",
)
BEANS = CartLine(
    sku="sku_beans_01",
    description="Arabica beans 1kg",
    amount=120_000,
    mcc=5411,
    category="groceries",
)
GIFT_CARDS = [
    CartLine(
        sku=f"sku_giftcard_{i:02d}",
        description="Rs 5,000 stored-value gift card",
        amount=500_000,
        mcc=6540,
        category="stored_value",
    )
    for i in range(10)
]


def fresh() -> CaveatEngine:
    e = CaveatEngine()
    e.register_operator("op_shopbot", "ShopBot v2.1", now=T0)
    e.register_operator("op_pricechecker", "PriceCheckerBot", now=T0)
    e.register_operator("op_rogue", "BargainHunter (unverified)", now=T0)
    return e


out: dict = {}


# ---------------------------------------------------------------- clean purchase
e = fresh()
m = e.grant(holder="op_shopbot", scope=GROCERY_SCOPE, now=T0)
cart = Cart.of("m_croma", [ESPRESSO, BEANS])
d = e.authorize(mandate=m, intent_cart=cart, executed_cart=cart, now=T0 + 10)
out["clean_purchase"] = {
    "scenario": "clean_purchase",
    "label": "Clean purchase",
    "narrative": "Same agent, no injection. The honest path authorizes in single-digit milliseconds.",
    "intent_cart": cart.to_dict(),
    "executed_cart": cart.to_dict(),
    "governed": d.to_dict(),
    "ungoverned": {
        "authorized": True,
        "amount": cart.total(),
        "amount_display": fmt_money(cart.total()),
        "cart": cart.to_dict(),
        "note": "no governance layer — merchant page is trusted verbatim",
    },
    "mandate": m.to_dict(include_serialized=False),
    "ledger_root": e.ledger_root(),
    "ledger_size": len(e.ledger),
}

# ---------------------------------------------------------------- injection
e = fresh()
m = e.grant(holder="op_shopbot", scope=GROCERY_SCOPE, now=T0)
intent = Cart.of("m_croma", [ESPRESSO, BEANS])
executed = Cart.of("m_croma", [ESPRESSO, BEANS, *GIFT_CARDS])
d = e.authorize(mandate=m, intent_cart=intent, executed_cart=executed, now=T0 + 10)
out["injection"] = {
    "scenario": "injection",
    "label": "Prompt injection at the merchant page",
    "narrative": "Injected instruction appends 10 stored-value gift cards after intent was signed.",
    "intent_cart": intent.to_dict(),
    "executed_cart": executed.to_dict(),
    "governed": d.to_dict(),
    "ungoverned": {
        "authorized": True,
        "amount": executed.total(),
        "amount_display": fmt_money(executed.total()),
        "cart": executed.to_dict(),
        "note": "signature covered the intent, not the executed cart — it still validates",
    },
    "mandate": m.to_dict(include_serialized=False),
    "ledger_root": e.ledger_root(),
    "ledger_size": len(e.ledger),
}

# ---------------------------------------------------------------- escalation
e = fresh()
root = e.grant(holder="op_shopbot", scope=GROCERY_SCOPE, now=T0)
accepted = e.attenuate(
    parent=root,
    child_holder="op_pricechecker",
    added=[AmountMax(200_000), MerchantAllow(("m_bigbasket",))],
    now=T0 + 1,
)
dropped = e.delegate(
    parent=root,
    child_holder="op_rogue",
    declared_scope=ConstraintSet([AmountMax(1_000_000)]),
    now=T0 + 2,
)
widened = e.delegate(
    parent=root,
    child_holder="op_rogue",
    declared_scope=ConstraintSet(
        [
            AmountMax(5_000_000),
            CategoryAllow(("groceries", "appliances")),
            MerchantAllow(("m_bigbasket",)),
            MccAllow((5411,)),
        ]
    ),
    now=T0 + 3,
)
grand = None
if accepted.mandate is not None:
    grand = e.attenuate(
        parent=accepted.mandate,
        child_holder="op_pricechecker",
        added=[AmountMax(50_000)],
        now=T0 + 4,
    )
out["escalation"] = {
    "scenario": "escalation",
    "label": "Delegation-time escalation proof",
    "narrative": "Four delegation hops. Two narrow. Two widen — and one of those fools a subset check.",
    "root": root.to_dict(include_serialized=False),
    "delegations": [
        {
            "title": "Legitimate attenuation",
            "child_holder": "op_pricechecker",
            "parent_id": root.mandate_id,
            **accepted.to_dict(),
        },
        {
            "title": "Dropped constraint",
            "child_holder": "op_rogue",
            "parent_id": root.mandate_id,
            **dropped.to_dict(),
        },
        {
            "title": "Widened bound",
            "child_holder": "op_rogue",
            "parent_id": root.mandate_id,
            **widened.to_dict(),
        },
    ]
    + (
        [
            {
                "title": "Second-hop attenuation",
                "child_holder": "op_pricechecker",
                "parent_id": accepted.mandate.mandate_id,
                **grand.to_dict(),
            }
        ]
        if grand and accepted.mandate
        else []
    ),
    "mandates": [x.to_dict(include_serialized=False) for x in e.mandates()],
    "ledger_root": e.ledger_root(),
    "ledger_size": len(e.ledger),
}

# ---------------------------------------------------------------- kill switch
e = fresh()
root = e.grant(holder="op_shopbot", scope=GROCERY_SCOPE, now=T0)
chain = [root.to_dict(include_serialized=False)]
current = root
for i in range(1, 5):
    e.register_operator(f"op_hop{i}", f"SubAgent {i}", now=T0)
    o = e.attenuate(
        parent=current,
        child_holder=f"op_hop{i}",
        added=[AmountMax(1_000_000 - i * 100_000)],
        now=T0 + i,
    )
    assert o.mandate is not None
    current = o.mandate
    chain.append(current.to_dict(include_serialized=False))

probe_cart = Cart.of("m_croma", [ESPRESSO])
before = e.authorize(mandate=current, intent_cart=probe_cart, executed_cart=probe_cart, now=T0 + 10)
t_start = time.perf_counter()
record = e.revoke(root.root_id, now=T0 + 11, cause="cardholder tapped revoke")
after = e.authorize(mandate=current, intent_cart=probe_cart, executed_cart=probe_cart, now=T0 + 12)
containment_ms = (time.perf_counter() - t_start) * 1000.0
out["kill_switch"] = {
    "scenario": "kill_switch",
    "label": "Kill switch",
    "narrative": "Four hops deep, mid-checkout. One row written. The deepest agent fails closed.",
    "chain": chain,
    "revocation": {
        **record.to_dict(),
        "descendants_killed": len(chain) - 1,
        "deepest_depth": current.depth,
        "containment_ms": round(containment_ms, 3),
        "discharge_ttl_s": 2,
        "before": before.to_dict(),
        "after": after.to_dict(),
    },
    "ledger_root": e.ledger_root(),
    "ledger_size": len(e.ledger),
}

# ---------------------------------------------------------------- step up
e = fresh()
m = e.grant(holder="op_shopbot", scope=GROCERY_SCOPE, now=T0)
fridge = CartLine(
    sku="sku_fridge_01",
    description="Refrigerator 240L",
    amount=950_000,
    mcc=5722,
    category="appliances",
)
cart = Cart.of("m_croma", [fridge])
first = e.authorize(mandate=m, intent_cart=cart, executed_cart=cart, now=T0 + 10)
challenge_id = first.step_up_challenge_id
sat = e.satisfy_step_up(challenge_id, now=T0 + 20) if challenge_id else {"challenge": None}
second = e.authorize(mandate=m, intent_cart=cart, executed_cart=cart, now=T0 + 21)
out["step_up"] = {
    "scenario": "step_up",
    "label": "Step-up discharge",
    "narrative": "Over threshold: a discharge bound to one cart hash. Second factor, unique to the transaction.",
    "intent_cart": cart.to_dict(),
    "executed_cart": cart.to_dict(),
    "step_up": {
        "challenge": sat.get("challenge"),
        "first": first.to_dict(),
        "second": second.to_dict(),
    },
    "governed": second.to_dict(),
    "mandate": m.to_dict(include_serialized=False),
    "ledger_root": e.ledger_root(),
    "ledger_size": len(e.ledger),
}

# ---------------------------------------------------------------- evidence sample
e = fresh()
m = e.grant(holder="op_shopbot", scope=GROCERY_SCOPE, now=T0)
child = e.attenuate(parent=m, child_holder="op_pricechecker", added=[AmountMax(600_000)], now=T0 + 1)
intent = Cart.of("m_croma", [ESPRESSO, BEANS])
executed = Cart.of("m_croma", [ESPRESSO, BEANS, *GIFT_CARDS])
d = e.authorize(mandate=m, intent_cart=intent, executed_cart=executed, now=T0 + 10)
proof = e.ledger.inclusion_proof(d.ledger_seq)
ok, err = e.verify_ledger()
out["evidence_sample"] = {
    "txn_id": d.txn_id,
    "issued_at": T0 + 12,
    "decision": d.to_dict(),
    "mandate_chain": [m.to_dict(include_serialized=False)]
    + ([child.mandate.to_dict(include_serialized=False)] if child.mandate else []),
    "intent_cart": intent.to_dict(),
    "executed_cart": executed.to_dict(),
    "diff": d.diff.to_dict(),
    "decision_chain": [
        x.to_dict() for x in e.ledger.filter("decision")
    ],
    "inclusion_proof": proof.to_dict() if proof else None,
    "proof_verified": bool(proof and proof.verify()),
    "chain_verified": ok,
    "chain_error": err,
    "ledger_root": e.ledger_root(),
    "ledger_size": len(e.ledger),
    "verdict": d.verdict,
    "liable_party": d.liable_party,
    "ledger_entries": [x.to_dict() for x in e.ledger.entries],
}

# ---------------------------------------------------------------- ledger entry sample
out["ledger_entry_sample"] = [x.to_dict() for x in e.ledger.entries[:4]]

# ---------------------------------------------------------------- mandate tree sample
e = fresh()
root = e.grant(holder="op_shopbot", scope=GROCERY_SCOPE, now=T0)
a = e.attenuate(parent=root, child_holder="op_pricechecker", added=[AmountMax(200_000)], now=T0 + 1)
b = e.attenuate(parent=root, child_holder="op_shopbot", added=[MerchantAllow(("m_bigbasket",))], now=T0 + 2)
c = e.attenuate(parent=a.mandate, child_holder="op_pricechecker", added=[AmountMax(50_000)], now=T0 + 3) if a.mandate else None
out["mandate_tree_sample"] = [
    x.to_dict(include_serialized=False) for x in e.mandates()
]

# ---------------------------------------------------------------- operator fleet + exposure
# Modelled underwriting book. The event counts are a mock dataset; the score, band,
# premium and expected-loss arithmetic below is the model the backend should mirror.

BAND_PREMIUM_BPS = {"PRIME": 8, "STANDARD": 22, "WATCH": 65, "RESTRICTED": 180}

# Behavioural weights, scaled by 100 so the whole score stays integer arithmetic. A
# denial is ordinary operating noise; an attempt to widen declared scope is not.
WEIGHT_X100 = {
    "denied": 100,
    "step_up_required": 5,
    "cart_divergence": 1_200,
    "injection_absorbed": 3_000,
    "scope_escalation_attempt": 6_000,
}

# Shrinkage prior: an operator with no history is not trusted, it is merely unmeasured.
# Without this a brand-new agent scores 100 on zero evidence, which is how agent risk
# gets mispriced in the first place.
PRIOR_N = 150
PRIOR_BAD_X100 = 2_200


def band_for(score: int) -> str:
    if score >= 85:
        return "PRIME"
    if score >= 70:
        return "STANDARD"
    if score >= 45:
        return "WATCH"
    return "RESTRICTED"


def score_for(counts: dict[str, int]) -> int:
    bad_x100 = sum(WEIGHT_X100[k] * counts.get(k, 0) for k in WEIGHT_X100)
    decisions = counts.get("authorized", 0) + counts.get("denied", 0)
    risk_bps = (bad_x100 + PRIOR_BAD_X100) * 100 // (decisions + PRIOR_N)
    return max(0, min(100, 100 - risk_bps // 100))


FLEET = [
    # (operator_id, name, registered_at, counts, authorized_volume_paise)
    ("op_shopbot", "ShopBot v2.1", T0 - 86_400 * 210, {"authorized": 1842, "step_up_required": 61, "denied": 2}, 74_820_000),
    ("op_pricechecker", "PriceCheckerBot", T0 - 86_400 * 154, {"authorized": 903, "denied": 1}, 21_140_000),
    ("op_pantry", "PantryRefill Agent", T0 - 86_400 * 132, {"authorized": 2610, "step_up_required": 12, "denied": 1}, 96_355_000),
    ("op_travelmate", "TravelMate Concierge", T0 - 86_400 * 96, {"authorized": 411, "denied": 3, "step_up_required": 44}, 188_400_000),
    ("op_subsmgr", "SubscriptionKeeper", T0 - 86_400 * 88, {"authorized": 1204, "denied": 1}, 12_060_000),
    ("op_giftly", "Giftly Autobuy", T0 - 86_400 * 41, {"authorized": 188, "denied": 14, "cart_divergence": 3}, 33_910_000),
    ("op_bargain", "BargainHunter (unverified)", T0 - 86_400 * 17, {"authorized": 96, "denied": 11, "cart_divergence": 2, "injection_absorbed": 1}, 42_300_000),
    ("op_flashcart", "FlashCart Sniper", T0 - 86_400 * 9, {"authorized": 120, "denied": 18, "cart_divergence": 4, "injection_absorbed": 1}, 61_800_000),
    ("op_rogue", "NightMarket Relay", T0 - 86_400 * 3, {"authorized": 4, "denied": 21, "cart_divergence": 5, "injection_absorbed": 4, "scope_escalation_attempt": 3}, 18_900_000),
]

CUTOFF_SCORE = 70

operators = []
for operator_id, name, registered_at, counts, volume in FLEET:
    score = score_for(counts)
    band = band_for(score)
    premium_bps = BAND_PREMIUM_BPS[band]
    # Modelled Agent Purchase Protection exposure: authorized volume still inside the
    # 120-day chargeback window, times the band's expected-loss rate.
    expected_loss = volume * premium_bps // 10_000
    operators.append(
        {
            "operator_id": operator_id,
            "name": name,
            "registered_at": registered_at,
            "risk_score": score,
            "band": band,
            "premium_bps": premium_bps,
            "authorized_count": counts.get("authorized", 0),
            "denied_count": counts.get("denied", 0),
            "step_up_count": counts.get("step_up_required", 0),
            "divergence_count": counts.get("cart_divergence", 0),
            "injection_count": counts.get("injection_absorbed", 0),
            "escalation_attempts": counts.get("scope_escalation_attempt", 0),
            "authorized_volume": volume,
            "modelled_exposure": volume,
            "expected_loss": expected_loss,
            "covered": score >= CUTOFF_SCORE,
        }
    )

curve = []
for s in range(0, 101, 5):
    inside = [o for o in operators if o["risk_score"] >= s]
    exposure_at = sum(o["modelled_exposure"] for o in inside)
    loss_at = sum(o["expected_loss"] for o in inside)
    curve.append(
        {
            "score": s,
            "covered_exposure": exposure_at,
            "expected_loss": loss_at,
            "loss_rate_bps": (loss_at * 10_000 // exposure_at) if exposure_at else 0,
            "operators": len(inside),
        }
    )

total_exposure = sum(o["modelled_exposure"] for o in operators)
covered = sum(o["modelled_exposure"] for o in operators if o["covered"])
out["exposure"] = {
    "as_of": T0,
    "model": "modelled Agent Purchase Protection exposure — mock dataset, real arithmetic",
    "cutoff_score": CUTOFF_SCORE,
    "total_exposure": total_exposure,
    "covered_exposure": covered,
    "declined_exposure": total_exposure - covered,
    "expected_loss": sum(o["expected_loss"] for o in operators if o["covered"]),
    "blended_loss_bps": (
        sum(o["expected_loss"] for o in operators if o["covered"]) * 10_000 // covered
        if covered
        else 0
    ),
    "band_premium_bps": BAND_PREMIUM_BPS,
    "operators": operators,
    "curve": curve,
}

out["operators"] = operators

print(json.dumps(out, indent=1))
