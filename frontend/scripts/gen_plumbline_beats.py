"""Generate the two beats the console could not previously show: graceful degrade, and
the perturbation axes a judge drives.

Same rule as `gen_plumbline_fixtures.py`: nothing here is hand-written JSON.

  * `degrade` is the verbatim output of `plumbline.scenarios.graceful_degrade`, recorded so
    the mock transport can serve it. In live mode the console POSTs
    `/api/plumbline/scenario/graceful_degrade` and gets the same bytes from the same
    function, because the scenario runs on a fixed clock.

  * `perturbation` is a set of axes over the SAME manifests and the SAME cart as
    `plumblineFixtures.json`. Every point on every axis is a real run of `plumbline.allocate`
    against a manifest with exactly one field changed, and carries the witness that run
    produced. The console re-applies the same one-field change to the signed manifest body
    carried here, then re-verifies the witness against it — so a point that disagrees with
    the engine fails visibly in the browser rather than rendering a pretty number.

    The signed manifest bodies and the cart travel in this block rather than being read
    from the valuation endpoint. The screen is a control a judge drives, so it must not
    become unavailable because a route somewhere else serves a different corpus; and a
    witness can only be checked against the manifest it was computed from, which is this
    one and no other.

    The derivation table is NOT carried. It is recomputed in the browser from the witness,
    the perturbed manifest and the cart — the three objects a counterparty holds — which is
    both smaller on the wire and a stronger claim than shipping a table and asking for it
    to be believed. `src/lib/derivation.ts` is the port; the smoke test asserts it
    reproduces this generator's own rows for every instrument at baseline.

What a perturbation may touch, and why the distinction is load-bearing:

  * a remaining balance or an annual-cap headroom is MEMBER STATE. Two Card Members hold
    the same product with different balances on the same day; moving one asserts nothing
    about the product's terms.
  * a rate is a PRODUCT TERM. This file perturbs a rate only on the one instrument in the
    candidate set that is invented outright and signed by nobody — Hypothetical Bank's
    Illustrative Reserve, built in `plumbline.console` and labelled as invented on its own
    provenance line — because putting a rate that is not its own beside a real issuer's
    product name is a claim about that product, and we do not make it. Every other axis is
    a balance, and a balance is member state.

The baseline point of every axis is asserted to reproduce the fixture's own recorded
witness hash before this file is written. If the two ever disagree, the axis is measuring
something other than the card on screen.

Run:  PYTHONPATH=backend .venv/bin/python frontend/scripts/gen_plumbline_beats.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from dataclasses import replace
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from gen_plumbline_fixtures import (  # noqa: E402  (path is set above)
    CART,
    ISSUER_KEY,
    T0,
    instrument_specs,
    rank,
)

from plumbline import console  # noqa: E402
from plumbline.allocate import allocate  # noqa: E402
from plumbline.manifest import Manifest, build_manifest, canonical_json, sign_manifest  # noqa: E402
from plumbline.witness import Witness, verify_witness  # noqa: E402
from plumbline import scenarios  # noqa: E402

fmt_money = console.money

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"

# The degrade run is a recording of a transport call, so it belongs with the other
# recordings the mock transport serves. The perturbation corpus is not: both transports
# use it, because it is a control a judge holds and it must not go dark because a route
# somewhere else is serving a different corpus.
OUT_DEGRADE = SRC / "mock" / "plumblineDegrade.json"
OUT_PERTURBATION = SRC / "data" / "plumblinePerturbations.json"
FIXTURES = SRC / "mock" / "plumblineFixtures.json"

FIELD_CAPACITY = "capacity_minor"
FIELD_RATE_BP = "rate_bp"

UNIT_MONEY = "money"
UNIT_BP = "bp"

# What the knob is a fact about. The console prints this beside every axis, because
# "member state" and "product term" are not the same kind of claim and only one of them is
# safe to move next to a real product's name.
FACT_MEMBER_STATE = "member state"
# Not "modelled term": the only instrument whose rate this file moves is invented outright
# rather than modelled from anyone's published terms, and the label a judge reads has to
# say which of the two it is.
FACT_INVENTED_TERM = "invented term"


def witness_dict(w: Witness, currency: str) -> dict[str, Any]:
    """Serialize a witness for the wire.

    `currency` is keyword-only and required on the kernel side, deliberately: a witness
    names a manifest and a cart and carries no currency of its own, so the caller — who is
    holding one of those — has to say. Defaulting it is how a manifest comes out under the
    wrong sign with every number right.
    """
    return w.to_dict(currency=currency)


def witness_hash(w: Witness, currency: str) -> str:
    return hashlib.sha256(canonical_json(witness_dict(w, currency))).hexdigest()


def perturb(manifest: Manifest, benefit_id: str, field: str, value: int) -> Manifest:
    """One field of one benefit, changed. Everything else is the signed body verbatim."""
    found = False
    benefits = []
    for b in manifest.benefits:
        if b.benefit_id == benefit_id:
            benefits.append(replace(b, **{field: value}))
            found = True
        else:
            benefits.append(b)
    if not found:
        raise KeyError(f"no benefit {benefit_id!r} in {manifest.manifest_id!r}")
    return build_manifest(
        manifest_id=manifest.manifest_id,
        issuer=manifest.issuer,
        product=manifest.product,
        currency=manifest.currency,
        benefits=benefits,
        issued_at=manifest.issued_at,
        source=manifest.source,
    )


# ---------------------------------------------------------------------------------------
# The axes. Values are chosen to bracket the point where a rule starts or stops binding,
# because that is the only place a judge learns anything: a slider that moves a number
# smoothly teaches nothing a spreadsheet could not.
# ---------------------------------------------------------------------------------------

AXES: list[dict[str, Any]] = [
    {
        "axis_id": "stay_credit_balance",
        "instrument": "hypothetical",
        "benefit_id": "hypo_stay_credit",
        "field": FIELD_CAPACITY,
        "unit": UNIT_MONEY,
        "fact": FACT_MEMBER_STATE,
        "label": "Annual stay credit · balance remaining",
        "what_it_is": (
            "how much of this account's stay credit is left today. Two Card Members hold "
            "the same product with different balances, so moving this asserts nothing about "
            "the product's terms."
        ),
        "watch_for": (
            "the balance lands whole on the first stay it admits and the second stay gets "
            "nothing, because the two share an exclusivity group. That is the shortfall no "
            "per-line implementation can see: it is not a property of either line alone."
        ),
        "values": [0, 10_000, 25_000, 50_000, 120_000, 300_000, 620_000, 900_000, 1_500_000],
    },
    {
        "axis_id": "dining_credit_balance",
        "instrument": "hypothetical",
        "benefit_id": "hypo_dining_credit_monthly",
        "field": FIELD_CAPACITY,
        "unit": UNIT_MONEY,
        "fact": FACT_MEMBER_STATE,
        "label": "Monthly dining credit · balance remaining",
        "what_it_is": (
            "how much of this month's dining credit is left. Member state again, and the "
            "only knob on this screen whose movement changes WHICH benefit wins a line."
        ),
        "watch_for": (
            "below ₹200 the enrolled partner credit takes the dinner instead and this one "
            "leaves the derivation entirely. At exactly ₹200 the tie breaks on benefit id "
            "and never on iteration order, which is why two runs produce the same receipt."
        ),
        "values": [0, 5_000, 10_000, 20_000, 30_000, 60_000, 100_000, 168_400, 250_000],
    },
    {
        "axis_id": "travel_multiplier_headroom",
        "instrument": "hypothetical",
        "benefit_id": "hypo_earn_travel",
        "field": FIELD_CAPACITY,
        "unit": UNIT_MONEY,
        "fact": FACT_MEMBER_STATE,
        "label": "Travel multiplier · annual cap headroom",
        "what_it_is": (
            "how much of the annual multiplier cap this account has not yet used. Member "
            "state, not a published rate."
        ),
        "watch_for": (
            "a multiplier is all-or-nothing under this manifest — no partial rate is "
            "declared — so a line contributes nothing until the headroom covers it whole, "
            "and then the base rate in the same group stops applying to that line."
        ),
        "values": [0, 10_000, 20_000, 35_500, 40_000, 71_000, 110_000, 200_000],
    },
    {
        "axis_id": "amex_fuel_cap_headroom",
        "instrument": "amex_platinum_india",
        "benefit_id": "amex_in_plat_earn_fuel",
        "field": FIELD_CAPACITY,
        "unit": UNIT_MONEY,
        "fact": FACT_MEMBER_STATE,
        "label": "Fuel earn · monthly cap headroom",
        "what_it_is": (
            "how much of the published 5,000 Membership Rewards Points a calendar month "
            "this account has not yet drawn on fuel. It is member state, which is the only "
            "kind of knob this screen puts on a real issuer's product: a balance differs "
            "between two holders of the same card, a rate does not."
        ),
        "watch_for": (
            "the fuel line is the one place on this cart where the published Amex rate is "
            "the only rate standing — every other instrument in the candidate set excludes "
            "fuel from its base earn. The line earns ₹60 and an earn benefit is never "
            "applied fractionally, so the two stops either side of ₹60 turn it on and off "
            "whole: below that the line is dropped rather than clipped, which understates, "
            "and understating is the safe direction."
        ),
        # Bracketed tightly around ₹60, the value of the fuel line's earn, because the
        # cliff is the only thing this axis has to teach. The top stop is the published
        # monthly cap and there is deliberately nothing above it: headroom past a published
        # cap is not member state, it is a term this product does not have.
        "values": [0, 1_000, 3_000, 5_900, 6_000, 10_000, 40_000, 75_000, 125_000],
    },
    {
        "axis_id": "hypothetical_base_rate",
        "instrument": "hypothetical",
        "benefit_id": "hypo_earn_base",
        "field": FIELD_RATE_BP,
        "unit": UNIT_BP,
        "fact": FACT_INVENTED_TERM,
        "label": "Illustrative Reserve base earn · rate",
        "what_it_is": (
            "a rate, and therefore a product term rather than member state. It is moved only "
            "here, on the one instrument in the candidate set that is invented outright and "
            "signed by nobody, because a rate printed beside a real product's name is a "
            "claim about that product and we do not make it."
        ),
        "watch_for": (
            "the only axis that moves a rate. Every line it admits re-values at once and the "
            "ranking moves with it, while the travel multiplier's own headroom holds the "
            "total down at the top end."
        ),
        "values": [0, 25, 50, 75, 100, 150, 250, 400, 600],
    },
]


def build_axis(
    spec: dict[str, Any], manifests: dict[str, Manifest], recorded: dict[str, Any]
) -> dict[str, Any]:
    base = manifests[spec["instrument"]]
    benefit = next(b for b in base.benefits if b.benefit_id == spec["benefit_id"])
    baseline_value = getattr(benefit, spec["field"])
    if baseline_value not in spec["values"]:
        raise ValueError(
            f"{spec['axis_id']}: the manifest's own value {baseline_value} is not on the "
            f"axis, so the judge cannot get back to the signed terms"
        )

    points = []
    for value in spec["values"]:
        moved = perturb(base, spec["benefit_id"], spec["field"], value)
        result = allocate(moved, CART)
        w = result.witness
        asserted = w.realized_minor()

        verification = verify_witness(
            witness=w, manifest=moved, cart=CART, asserted_minor=asserted
        )
        if not verification.ok:
            raise AssertionError(
                f"{spec['axis_id']} at {value}: the engine's own witness does not verify: "
                f"{[f.to_dict() for f in verification.failures]}"
            )

        ranking = rank(
            [
                {
                    "instrument_id": m.manifest_id,
                    "asserted_minor": (
                        asserted
                        if key == spec["instrument"]
                        else allocate(m, CART).witness.realized_minor()
                    ),
                }
                for key, m in manifests.items()
            ]
        )
        for entry in ranking:
            entry["asserted_display"] = fmt_money(entry["asserted_minor"])

        points.append(
            {
                "value": value,
                "value_display": (
                    fmt_money(value) if spec["unit"] == UNIT_MONEY else f"{value} bp"
                ),
                "manifest_hash": moved.content_hash(),
                "asserted_minor": asserted,
                "asserted_display": fmt_money(asserted),
                "witness": witness_dict(w, CART.currency),
                "witness_hash": witness_hash(w, CART.currency),
                "ranking": ranking,
                "selected": ranking[0]["instrument_id"],
                "allocator_stats": {
                    "considered": result.considered,
                    "assigned": result.assigned,
                    "skipped_capacity": result.skipped_capacity,
                    "skipped_exclusivity": result.skipped_exclusivity,
                },
            }
        )

    baseline_index = spec["values"].index(baseline_value)
    baseline = points[baseline_index]

    # The whole axis is worthless if its zero point is not the card the other screens
    # value. Compare against what the fixture actually recorded, not against a re-run.
    recorded_witness_hash = recorded[base.manifest_id]["witness_hash"]
    if baseline["witness_hash"] != recorded_witness_hash:
        raise AssertionError(
            f"{spec['axis_id']}: baseline witness {baseline['witness_hash'][:16]} does not "
            f"match the fixture's {recorded_witness_hash[:16]} — regenerate "
            f"plumblineFixtures.json first"
        )
    if baseline["manifest_hash"] != recorded[base.manifest_id]["manifest_hash"]:
        raise AssertionError(f"{spec['axis_id']}: baseline manifest hash drifted")

    return {
        "axis_id": spec["axis_id"],
        "instrument_id": base.manifest_id,
        "issuer": base.issuer,
        "product": base.product,
        "issuer_signed": recorded[base.manifest_id]["issuer_signed"],
        "benefit_id": spec["benefit_id"],
        "benefit_label": benefit.label,
        "benefit_kind": benefit.kind,
        "benefit_window": benefit.window,
        "benefit_note": benefit.note,
        "exclusivity_group": benefit.exclusivity_group,
        "field": spec["field"],
        "unit": spec["unit"],
        "fact": spec["fact"],
        "label": spec["label"],
        "what_it_is": spec["what_it_is"],
        "watch_for": spec["watch_for"],
        "baseline_value": baseline_value,
        "baseline_index": baseline_index,
        "signed_manifest_hash": recorded[base.manifest_id]["manifest_hash"],
        "points": points,
    }


# Short keys for the axes above. The manifests themselves come from
# `plumbline.console.instrument_specs`, which is the same candidate set the valuation endpoint
# serves — this file never builds a card of its own.
AXIS_INSTRUMENTS = {
    "amex_platinum_india": "amex-platinum-charge-in-2026",
    "amex_platinum_travel_india": "amex-platinum-travel-in-2026",
    "hdfc_infinia": "hdfc-infinia-metal-2026",
    "hypothetical": "hypothetical-illustrative-reserve",
}


def main() -> None:
    specs = {s.manifest.manifest_id: s for s in instrument_specs(T0)}
    unknown = [mid for mid in AXIS_INSTRUMENTS.values() if mid not in specs]
    if unknown:
        raise AssertionError(
            f"AXIS_INSTRUMENTS names instruments the console candidate set does not carry: "
            f"{', '.join(unknown)}"
        )
    manifests = {key: specs[mid].manifest for key, mid in AXIS_INSTRUMENTS.items()}

    fixture = json.loads(FIXTURES.read_text())
    recorded = {v["instrument_id"]: v for v in fixture["valuation"]["instruments"]}
    if fixture["cart_hash"] != CART.hash():
        raise AssertionError("the cart in the fixture is not the cart these axes perturb")

    axes = [build_axis(spec, manifests, recorded) for spec in AXES]

    instruments = []
    for key, m in manifests.items():
        signed = specs[m.manifest_id].issuer_signed
        sm = sign_manifest(m, ISSUER_KEY, key_id=console.ISSUER_KEY_ID) if signed else None
        instruments.append(
            {
                "instrument_id": m.manifest_id,
                "issuer": m.issuer,
                "product": m.product,
                "issuer_signed": signed,
                "source": m.source,
                "manifest_hash": m.content_hash(),
                "signature": sm.signature if sm else None,
                "key_id": sm.key_id if sm else None,
                "manifest": m.body(),
            }
        )
    for record in instruments:
        if record["manifest_hash"] != recorded[record["instrument_id"]]["manifest_hash"]:
            raise AssertionError(
                f"{record['instrument_id']}: manifest differs from the one the other "
                f"screens value; the two corpora would disagree on the same product"
            )

    degrade = scenarios.run("graceful_degrade")
    passes = degrade["data"]["passes"]
    proceeded = [p for p in passes if p["proceeds"]]
    denied = [p for p in passes if not p["proceeds"]]
    if len(passes) != 4 or len(denied) != 1:
        raise AssertionError(f"expected four passes and one denial, got {len(passes)}/{len(denied)}")
    if denied[0]["posture"] != "enforce" or denied[0]["counterpart_receipt"]:
        raise AssertionError("the only denial must be enforcement with no counterpart receipt")

    perturbation = {
        "generated_at": T0,
        "disclosure": (
            "Each stop on each axis is a real run of plumbline.allocate over a manifest with "
            "one field changed. The witness is the engine's; the verification is this "
            "browser's. Signatures are HMAC under prototype keys, and no signature survives "
            "a perturbation."
        ),
        "cart": CART.to_dict(),
        "cart_hash": CART.hash(),
        "criterion": fixture["receipt"]["criterion"],
        "policy_hash": fixture["receipt"]["policy"]["policy_hash"],
        "method": (
            "one field of one benefit is changed; plumbline.allocate is re-run over the same "
            "cart; the witness it produced is carried here and re-checked in the browser "
            "against a manifest the console perturbs for itself"
        ),
        "instruments": instruments,
        "axes": axes,
    }

    OUT_DEGRADE.parent.mkdir(parents=True, exist_ok=True)
    OUT_PERTURBATION.parent.mkdir(parents=True, exist_ok=True)
    OUT_DEGRADE.write_text(json.dumps(degrade, indent=1, sort_keys=False) + "\n")
    OUT_PERTURBATION.write_text(json.dumps(perturbation, indent=1, sort_keys=False) + "\n")
    print(f"wrote {OUT_DEGRADE} ({OUT_DEGRADE.stat().st_size / 1024:.0f} KB)")
    print(f"wrote {OUT_PERTURBATION} ({OUT_PERTURBATION.stat().st_size / 1024:.0f} KB)")
    print(f"  degrade      {len(proceeded)} proceed, {len(denied)} deny")
    for axis in axes:
        lo = axis["points"][0]
        hi = axis["points"][-1]
        ranks = {p["ranking"][0]["instrument_id"] for p in axis["points"]}
        print(
            f"  {axis['axis_id']:22s} {len(axis['points'])} points  "
            f"{lo['asserted_display']:>11s} → {hi['asserted_display']:<11s} "
            f"selected: {len(ranks)} distinct"
        )


if __name__ == "__main__":
    main()
