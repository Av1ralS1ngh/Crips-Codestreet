"""Tests for the plumbline demo scenarios.

The scenarios are what a judge sees, so these tests pin the numbers that appear on screen,
not merely the shapes around them. If a published card term is edited, or the allocator's
tie-breaking moves, or a display string picks up the wrong currency, a test here fails
before a rehearsal does.

The scenarios orchestrate `evaluate` and `receipt` rather than reimplementing them, so
these tests also serve as an integration check across the whole plumbline stack: manifest,
allocator, verifier, evaluator, receipt, transparency log.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

from caveat.cart import Cart, CartLine
from plumbline import products as P
from plumbline import scenarios as S
from plumbline import transparency as tlog
from plumbline.evaluate import REFUSE_CLAIM_UNSUPPORTED, STATUS_ATTESTED, STATUS_REFUSED
from plumbline.receipt import (
    ATTEST_CANDIDATE_SET_INCOMPLETE,
    ATTEST_FAITHFUL,
    POSTURE_ENFORCE,
    POSTURE_OBSERVE_ONLY,
    REASON_DISCLOSURE_CAVEAT_UNDISCHARGED,
    REASON_SELECTION_ATTESTED,
    REASON_UNATTESTED_SELECTION,
)
from plumbline.witness import ERR_CAPACITY


@pytest.fixture(scope="module")
def runs() -> dict:
    return S.run_all()


# --------------------------------------------------------------------------------------
# Reproducibility
# --------------------------------------------------------------------------------------


def test_every_scenario_is_byte_identical_across_runs() -> None:
    """A rehearsal and the live run must produce the same bytes, or the demo is a coin toss."""
    first = json.dumps(S.run_all(), sort_keys=True, separators=(",", ":"))
    second = json.dumps(S.run_all(), sort_keys=True, separators=(",", ":"))
    assert first == second
    assert S.fingerprint() == S.fingerprint()


def test_no_measured_timing_leaks_into_scenario_output(runs: dict) -> None:
    """`elapsed_ms` measures the machine, not the decision, and is the only field of an
    evaluation that does not replay. A receipt is hashed, so it must not travel."""
    assert "elapsed_ms" not in json.dumps(runs)


def test_scenario_output_is_json_serialisable(runs: dict) -> None:
    for name, result in runs.items():
        json.dumps(result)  # raises on anything the API could not return
        assert result["name"] == name
        assert result["clock"] == S.DEMO_CLOCK
        assert result["title"] and result["headline"]
        assert result["notes"] and all(isinstance(n, str) for n in result["notes"])


def _import_probe(statement: str) -> list[str]:
    """Import the scenario module in a clean interpreter and report what came with it.

    A clean interpreter is the only honest check: another test module in this session may
    already have imported the oracle or a solver, and reading this process's sys.modules
    would report their imports as ours.
    """
    proc = subprocess.run(
        [sys.executable, "-c", f"import sys, plumbline.scenarios, plumbline.products; {statement}"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_scenarios_do_not_drag_the_offline_oracle_onto_the_hot_path() -> None:
    """The oracle can take seconds and can time out reporting a lower bound above its upper
    bound. An import edge from this API-reachable module to there would put that behind a
    checkout budget."""
    assert _import_probe(
        "import json; print(json.dumps([m for m in sys.modules if m.startswith('plumbline.oracle')]))"
    ) == []


def test_scenarios_load_no_solver_at_all() -> None:
    """Verification needs no solver, and that claim is about the import graph too."""
    loaded = _import_probe(
        "import json; print(json.dumps([m for m in sys.modules if m == 'z3' or m.startswith('z3.')]))"
    )
    assert loaded == [], f"a solver reached the valuation path: {loaded}"


def test_run_rejects_an_unknown_name_and_names_the_known_ones() -> None:
    with pytest.raises(S.ScenarioError) as exc:
        S.run("attribution")
    for name in S.SCENARIOS:
        assert name in str(exc.value)


def test_registry_covers_the_five_demo_beats() -> None:
    assert set(S.SCENARIOS) == {
        "overstatement",
        "refusal",
        "omission",
        "graceful_degrade",
        "cross_instrument",
    }
    for name in S.SCENARIOS:
        assert isinstance(S.run_scenario(name), S.ScenarioResult)


def test_scenarios_replay_identically_at_a_different_clock() -> None:
    """The clock is a parameter, not a sample. Two runs at any chosen clock must agree."""
    other = S.DEMO_CLOCK + 86_400
    a = json.dumps(S.run_all(clock=other), sort_keys=True)
    b = json.dumps(S.run_all(clock=other), sort_keys=True)
    assert a == b
    assert a != json.dumps(S.run_all(), sort_keys=True)
    assert json.loads(a)["overstatement"]["clock"] == other


# --------------------------------------------------------------------------------------
# Beat one: the naive sum overstates
# --------------------------------------------------------------------------------------


def test_overstatement_numbers_are_exactly_these(runs: dict) -> None:
    """Pinned because they are spoken aloud.

    Naive per-line summation on the Platinum trip cart claims $2,092.01. The exhibited
    allocation realizes $773.77. The overstatement avoided is $1,318.24, so the naive figure
    is 2.70x the achievable one.
    """
    data = runs["overstatement"]["data"]
    assert data["naive_minor"] == 209_201
    assert data["asserted_minor"] == 77_377
    assert data["overstatement_avoided_minor"] == 131_824
    assert data["naive_display"] == "$2,092.01"
    assert data["asserted_display"] == "$773.77"
    assert data["overstatement_avoided_display"] == "$1,318.24"
    assert data["naive_over_witness_bp"] == 27_036


def test_overstatement_gap_attribution_reconciles_exactly(runs: dict) -> None:
    inst = runs["overstatement"]["data"]["instrument"]
    assert inst["gap_reconciles"] is True
    assert sum(g["delta_minor"] for g in inst["gap_items"]) == inst["overstatement_avoided_minor"]
    reasons = {g["reason"] for g in inst["gap_items"]}
    # Both published-term effects must be present, or the demo shows only one of them.
    assert S.GAP_CREDIT_BALANCE in reasons
    assert S.GAP_EXCLUSIVITY in reasons


def test_overstatement_largest_line_is_the_hotel_credit(runs: dict) -> None:
    """The $300 remaining balance against a $1,240 booking is the biggest single source."""
    top = runs["overstatement"]["data"]["instrument"]["gap_items"][0]
    assert top["benefit_id"] == "amex_plat_credit_hotel_h2"
    assert top["naive_minor"] == 124_000  # the whole line
    assert top["witness_minor"] == 30_000  # the remaining balance
    assert top["delta_display"] == "$940"
    assert top["reason"] == S.GAP_CREDIT_BALANCE


def test_overstatement_witness_verifies_and_the_receipt_re_derives(runs: dict) -> None:
    data = runs["overstatement"]["data"]
    assert data["instrument"]["status"] == STATUS_ATTESTED
    assert data["instrument"]["verification"]["ok"] is True
    assert data["instrument"]["verification"]["supports_assertion"] is True
    assert data["receipt_verified"] is True
    assert all(c["ok"] for c in data["receipt_verification"]["checks"])


def test_overstatement_receipt_is_anchored_in_the_log(runs: dict) -> None:
    anchor = runs["overstatement"]["data"]["anchor"]
    assert anchor["inclusion_ok"] is True
    assert anchor["inclusion_code"] == tlog.PROOF_OK
    assert tlog.InclusionProof.from_dict(anchor["inclusion_proof"]).verify()
    assert tlog.verify_tree_head(
        tlog.SignedTreeHead.from_dict(anchor["signed_tree_head"]), S.DEMO_LOG_KEY
    )


def test_overstatement_declares_what_it_could_not_score(runs: dict) -> None:
    inst = runs["overstatement"]["data"]["instrument"]
    ids = {b["benefit_id"] for b in inst["declared_but_unavailable"]}
    assert "amex_plat_credit_lululemon" in ids  # enrollment-gated, not enrolled
    assert "amex_plat_credit_hotel_h1" in ids  # window closed
    assert all(b["note"] for b in inst["declared_but_unavailable"])
    assert inst["considered_but_unpriced"]
    assert all(u["rationale"] for u in inst["considered_but_unpriced"])


def test_overstatement_derivation_lines_match_the_assignments(runs: dict) -> None:
    inst = runs["overstatement"]["data"]["instrument"]
    assignments = inst["witness"]["assignments"]
    assert len(inst["derivation_lines"]) == len(assignments)
    # Each line ends with its own value in the cart's currency. Labels carry dollar signs of
    # their own ("$300 prepaid hotel credit"), so match the tail rather than count them.
    for line, a in zip(inst["derivation_lines"], assignments):
        assert line.endswith(P.fmt_currency(a["value_minor"], P.USD))


# --------------------------------------------------------------------------------------
# Beat two: refusal
# --------------------------------------------------------------------------------------


def test_refusal_declines_both_probes_with_codes(runs: dict) -> None:
    probes = runs["refusal"]["data"]["probes"]
    assert len(probes) == 2
    assert all(p["signed"] is False and p["refusal_codes"] for p in probes)
    claim, forged = probes
    assert claim["probe"] == S.PROBE_CLAIM_ABOVE_WITNESS
    assert claim["status"] == STATUS_REFUSED
    assert claim["asserted_minor"] is None  # no silent downgrade to the provable figure
    assert claim["refusal_codes"] == [REFUSE_CLAIM_UNSUPPORTED]
    assert forged["probe"] == S.PROBE_FORGED_WITNESS
    assert forged["refusal_codes"] == [ERR_CAPACITY]


def test_a_refused_instrument_is_excluded_from_the_ranking(runs: dict) -> None:
    """A refused instrument carries no value at all, so there is nothing left to rank."""
    assert runs["refusal"]["data"]["probes"][0]["ranking"] is None


def test_refusal_numbers_are_exactly_these(runs: dict) -> None:
    """Pinned: the Infinia SmartBuy cart is where a shared monthly pool actually binds."""
    data = runs["refusal"]["data"]
    assert data["supported_assertion"]["asserted_minor"] == 1_097_663  # Rs 10,976.63
    assert data["probes"][0]["proposed_minor"] == 1_659_194  # the naive figure
    assert data["probes"][1]["proposed_minor"] == 1_523_663  # the forged total


def test_refusal_lower_bound_is_sound_not_merely_zero(runs: dict) -> None:
    """A failed verification still yields an achievable total, never an inflated one."""
    forged = runs["refusal"]["data"]["probes"][1]
    assert forged["verifier_lower_bound_minor"] == 167_132
    assert 0 <= forged["verifier_lower_bound_minor"] < forged["proposed_minor"]
    assert forged["verification"]["ok"] is False
    assert forged["verification"]["supports_assertion"] is False


def test_refusal_forged_witness_is_correct_line_by_line() -> None:
    """The whole point of probe two: nothing is wrong per assignment, only in aggregate."""
    manifests = S.signed_manifests([P.HDFC_INFINIA_ID])
    manifest = manifests[P.HDFC_INFINIA_ID].manifest
    cart = S.INR_TRIP_CART
    honest = S.evaluate_cart(cart, manifests).candidates[0]
    forged = S.forge_over_capacity(manifest, cart, honest.witness)
    by_id = {b.benefit_id: b for b in manifest.benefits}
    by_sku = {line.sku: line for line in cart.lines}
    for a in forged.assignments:
        benefit = by_id[a.benefit_id]
        line = by_sku[a.line_sku]
        assert a.value_minor == benefit.value_for_line(line, cart.merchant)
        assert a.consumed_minor == a.value_minor
    target = by_id["hdfc_infinia_earn_smartbuy_hotel_10x"]
    drawn = sum(a.consumed_minor for a in forged.assignments if a.benefit_id == target.benefit_id)
    assert drawn > (target.capacity_minor or 0)


def test_refusal_renders_rupees_because_the_cart_is_in_rupees(runs: dict) -> None:
    data = runs["refusal"]["data"]
    assert data["cart_total_display"].startswith("₹")
    assert "$" not in json.dumps(data)


def test_every_refusal_lands_in_the_log(runs: dict) -> None:
    entries = runs["refusal"]["data"]["log_entries"]
    refusals = [e for e in entries if e["kind"] == tlog.ENTRY_REFUSAL]
    assert len(refusals) == 2
    assert {e["body"]["probe"] for e in refusals} == {
        S.PROBE_CLAIM_ABOVE_WITNESS,
        S.PROBE_FORGED_WITNESS,
    }


def test_forge_helper_explains_a_manifest_it_cannot_use() -> None:
    uncapped = P.build_manifest(
        manifest_id="test-uncapped",
        issuer="Test",
        product="Uncapped",
        benefits=[P._earn("t_base", "1x", rate_bp=100, group="t:earn")],
        issued_at=S.DEMO_CLOCK,
        currency=P.USD,
    )
    cart = Cart.of("m", [CartLine("l1", "line", 1_000, 5812, P.CAT_DINING)], currency=P.USD)
    with pytest.raises(S.ScenarioError) as exc:
        S.forge_over_capacity(uncapped, cart, None)
    assert "capped" in str(exc.value)


# --------------------------------------------------------------------------------------
# Beat three: omission
# --------------------------------------------------------------------------------------


def test_omission_is_detectable_four_independent_ways(runs: dict) -> None:
    data = runs["omission"]["data"]
    # 1. the receipt attests its own candidate set against the mandate
    assert data["attestation"]["honest"]["outcome"] == ATTEST_FAITHFUL
    assert data["attestation"]["edited"]["outcome"] == ATTEST_CANDIDATE_SET_INCOMPLETE
    assert data["missing_from_edited_receipt"] == [P.AMEX_PLATINUM_ID]
    # 2. the honest inclusion proof fails against the edited head
    assert data["inclusion"]["against_honest_head"] == {"ok": True, "code": tlog.PROOF_OK}
    assert data["inclusion"]["against_edited_head"]["ok"] is False
    assert data["inclusion"]["against_edited_head"]["code"] == tlog.PROOF_ROOT_MISMATCH
    # 3. the edited log cannot prove it extends an already-published head
    assert data["consistency"]["honest_extends_published_head"]["ok"] is True
    assert data["consistency"]["edited_extends_published_head"]["ok"] is False
    # 4. two signed heads at one size with two roots is a split view
    assert data["audit"]["outcome"] == tlog.AUDIT_SPLIT_VIEW
    assert data["audit"]["ok"] is False


def test_omission_attestation_names_the_missing_instrument(runs: dict) -> None:
    edited = runs["omission"]["data"]["attestation"]["edited"]
    detail = " ".join(f["detail"] for f in edited["findings"])
    assert P.AMEX_PLATINUM_ID in detail


def test_omission_proofs_reverify_from_their_serialised_form(runs: dict) -> None:
    """A counterparty gets the dict, not the object. Verify from what actually travels."""
    proof = tlog.InclusionProof.from_dict(runs["omission"]["data"]["inclusion"]["proof"])
    assert proof.verify()


def test_omission_heads_are_signed_and_differ_only_in_root(runs: dict) -> None:
    heads = runs["omission"]["data"]["heads"]
    honest = tlog.SignedTreeHead.from_dict(heads["honest"])
    edited = tlog.SignedTreeHead.from_dict(heads["edited"])
    assert tlog.verify_tree_head(honest, S.DEMO_LOG_KEY)
    assert tlog.verify_tree_head(edited, S.DEMO_LOG_KEY)
    assert honest.tree_size == edited.tree_size
    assert honest.root_hash != edited.root_hash


def test_omission_honest_receipt_records_the_full_candidate_set(runs: dict) -> None:
    honest = runs["omission"]["data"]["honest_receipt"]["receipt"]
    edited = runs["omission"]["data"]["edited_receipt"]["receipt"]
    assert set(honest["candidate_set"]["instrument_ids"]) == set(S.USD_CANDIDATE_SET)
    assert len(edited["candidate_set"]["instrument_ids"]) == 2
    assert honest["candidate_set"]["digest"] != edited["candidate_set"]["digest"]


def test_the_mandate_is_the_reference_set_not_the_agents_say_so(runs: dict) -> None:
    """An agent that shortens the candidate set falsifies the mandate, not the receipt."""
    data = runs["omission"]["data"]
    assert set(data["mandate"]["authorized_instrument_ids"]) == set(S.USD_CANDIDATE_SET)
    assert data["edited_receipt"]["receipt"]["mandate"] == data["mandate"]


# --------------------------------------------------------------------------------------
# Beat four: graceful degrade
# --------------------------------------------------------------------------------------


def test_graceful_degrade_covers_the_whole_matrix(runs: dict) -> None:
    passes = runs["graceful_degrade"]["data"]["passes"]
    assert len(passes) == 4
    assert {(p["posture"], p["counterpart_receipt"]) for p in passes} == {
        (POSTURE_OBSERVE_ONLY, True),
        (POSTURE_OBSERVE_ONLY, False),
        (POSTURE_ENFORCE, True),
        (POSTURE_ENFORCE, False),
    }


def test_observe_mode_never_declines(runs: dict) -> None:
    """A credential that hard-failed inside a third-party checkout would be routed around."""
    for p in runs["graceful_degrade"]["data"]["passes"]:
        if p["posture"] == POSTURE_OBSERVE_ONLY:
            assert p["proceeds"] is True


def test_the_only_denial_is_the_one_the_cardholder_elected(runs: dict) -> None:
    denials = [p for p in runs["graceful_degrade"]["data"]["passes"] if not p["proceeds"]]
    assert len(denials) == 1
    only = denials[0]
    assert only["posture"] == POSTURE_ENFORCE
    assert only["counterpart_receipt"] is False
    # It denies against the cardholder's own mandate caveat, not against the platform.
    assert only["reason_code"] == REASON_DISCLOSURE_CAVEAT_UNDISCHARGED


def test_coverage_is_conditioned_on_evidence_and_authorization_is_not(runs: dict) -> None:
    for p in runs["graceful_degrade"]["data"]["passes"]:
        assert p["coverage_eligible"] is p["counterpart_receipt"]
        if p["posture"] == POSTURE_OBSERVE_ONLY and not p["counterpart_receipt"]:
            assert p["proceeds"] is True and p["coverage_eligible"] is False
            assert p["reason_code"] == REASON_UNATTESTED_SELECTION


def test_every_pass_lands_in_the_log(runs: dict) -> None:
    data = runs["graceful_degrade"]["data"]
    kinds = data["log_entry_kinds"]
    assert kinds[tlog.ENTRY_MANIFEST_PUBLISHED] == len(S.USD_CANDIDATE_SET)
    assert kinds[tlog.ENTRY_RECEIPT] == 2  # the two attested passes
    assert kinds[tlog.ENTRY_UNATTESTED_SELECTION] == 2  # the gap and the denial
    assert data["reason_code_counts"][REASON_SELECTION_ATTESTED] == 2
    assert sorted(p["log_seq"] for p in data["passes"]) == [3, 4, 5, 6]


# --------------------------------------------------------------------------------------
# Beat five: cross-instrument
# --------------------------------------------------------------------------------------


def test_cross_instrument_ranks_every_held_instrument(runs: dict) -> None:
    data = runs["cross_instrument"]["data"]
    for key in ("ranking_with_annual_credits", "ranking_annual_credits_spent"):
        ranking = data[key]
        assert {e["manifest_id"] for e in ranking["entries"]} == set(S.USD_CANDIDATE_SET)
        values = [e["asserted_minor"] for e in ranking["entries"]]
        assert values == sorted(values, reverse=True)
        assert [e["rank"] for e in ranking["entries"]] == [1, 2, 3]


def test_cross_instrument_numbers_are_exactly_these(runs: dict) -> None:
    """Pinned. A competitor leads this cart, and that is the honest answer, not a bug."""
    data = runs["cross_instrument"]["data"]
    first = {
        e["manifest_id"]: e["asserted_minor"]
        for e in data["ranking_with_annual_credits"]["entries"]
    }
    assert first[P.CHASE_SAPPHIRE_RESERVE_ID] == 37_836  # $378.36
    assert first[P.AMEX_PLATINUM_ID] == 20_056  # $200.56
    assert first[P.AMEX_GOLD_ID] == 5_212  # $52.12
    second = {
        e["manifest_id"]: e["asserted_minor"]
        for e in data["ranking_annual_credits_spent"]["entries"]
    }
    assert second[P.CHASE_SAPPHIRE_RESERVE_ID] == 7_836  # $78.36
    assert second[P.AMEX_PLATINUM_ID] == 5_256  # $52.56
    assert second[P.AMEX_GOLD_ID] == 5_212  # unchanged: Gold draws no annual credit here


def test_cross_instrument_shows_how_much_of_the_lead_was_non_recurring(runs: dict) -> None:
    data = runs["cross_instrument"]["data"]
    before = data["ranking_with_annual_credits"]["margin_minor"]
    after = data["ranking_annual_credits_spent"]["margin_minor"]
    assert after < before, "the second pass must actually move the picture"
    assert before == 17_780 and after == 2_580


def test_no_issuer_endorses_the_ranking(runs: dict) -> None:
    data = runs["cross_instrument"]["data"]
    for key in ("ranking_with_annual_credits", "ranking_annual_credits_spent"):
        assert data[key]["issuer_endorsed"] is False
        assert "No issuer signs it" in data[key]["note"]
    assert data["valuation_policy"]["author"] in ("cardholder", "agent")
    assert data["valuation_policy"]["policy_hash"] == S.CARDHOLDER_POLICY.policy_hash()


def test_cross_instrument_attests_its_own_ranking_as_faithful(runs: dict) -> None:
    """Compliance symmetry: the positive result must be as quotable as the negative one."""
    assert runs["cross_instrument"]["data"]["attestation"]["outcome"] == ATTEST_FAITHFUL
    assert runs["cross_instrument"]["data"]["receipt_verified"] is True


def test_cross_instrument_never_nets_the_annual_fee(runs: dict) -> None:
    for c in runs["cross_instrument"]["data"]["candidates_with_annual_credits"]:
        fee = P.profile(c["manifest_id"]).annual_fee_minor
        assert c["asserted_minor"] != c["naive_sum_minor"] - fee
        assert c["asserted_minor"] >= 0
    # And no fee figure appears inside any signed manifest body.
    for signed in S.signed_manifests(S.USD_CANDIDATE_SET).values():
        body = str(signed.manifest.body())
        assert str(P.profile(signed.manifest.manifest_id).annual_fee_minor) not in body


def test_every_candidate_carries_its_verification_and_its_unpriced_list(runs: dict) -> None:
    for c in runs["cross_instrument"]["data"]["candidates_with_annual_credits"]:
        assert c["status"] == STATUS_ATTESTED
        assert c["verification"]["ok"] is True
        assert c["considered_but_unpriced"]
        assert c["derivation_lines"]
        assert c["gap_reconciles"] is True


def test_usd_scenarios_never_render_a_rupee_sign(runs: dict) -> None:
    """A USD derivation shown with a rupee sign is one a Card Member is right not to trust.

    `ensure_ascii=False` matters: the default escapes the symbol to \\u20b9, and a substring
    search for the glyph then passes on output that renders as rupees on screen.
    """
    for name in ("overstatement", "omission", "graceful_degrade", "cross_instrument"):
        blob = json.dumps(runs[name], ensure_ascii=False)
        assert "₹" not in blob, name
        assert "\\u20b9" not in blob, name


def test_the_inr_scenario_never_renders_a_dollar_sign(runs: dict) -> None:
    assert "$" not in json.dumps(runs["refusal"], ensure_ascii=False)


# --------------------------------------------------------------------------------------
# Staging helpers
# --------------------------------------------------------------------------------------


def test_evaluate_cart_refuses_a_cross_currency_candidate_set() -> None:
    inr = S.signed_manifests([P.HDFC_INFINIA_ID])
    with pytest.raises(S.ScenarioError) as exc:
        S.evaluate_cart(S.USD_TRIP_CART, inr)
    assert "FX" in str(exc.value)


def test_signed_manifests_names_the_products_it_knows() -> None:
    with pytest.raises(S.ScenarioError) as exc:
        S.signed_manifests(["barclays-arrival"])
    assert P.AMEX_GOLD_ID in str(exc.value)


def test_signed_manifests_verify_under_the_demo_issuer_key() -> None:
    from plumbline.manifest import verify_manifest

    for signed in S.signed_manifests(S.USD_CANDIDATE_SET).values():
        assert signed.key_id == S.DEMO_ISSUER_KEY_ID
        assert verify_manifest(signed, S.ISSUER_KEYS[S.DEMO_ISSUER_KEY_ID])


def test_draw_down_zeroes_the_named_credits_and_nothing_else() -> None:
    manifest = P.catalogue_by_id(S.DEMO_CLOCK)[P.CHASE_SAPPHIRE_RESERVE_ID]
    targets = S.ANNUAL_CREDIT_IDS[P.CHASE_SAPPHIRE_RESERVE_ID]
    spent = S.draw_down(manifest, targets)
    before = {b.benefit_id: b.capacity_minor for b in manifest.benefits}
    after = {b.benefit_id: b.capacity_minor for b in spent.benefits}
    assert set(before) == set(after)
    for bid in after:
        assert after[bid] == (0 if bid in targets else before[bid])
    assert spent.manifest_id == manifest.manifest_id
    assert spent.content_hash() != manifest.content_hash()


def test_draw_down_names_the_benefit_it_could_not_find() -> None:
    manifest = P.catalogue_by_id(S.DEMO_CLOCK)[P.AMEX_GOLD_ID]
    with pytest.raises(S.ScenarioError) as exc:
        S.draw_down(manifest, ["csr_credit_travel"])
    assert "csr_credit_travel" in str(exc.value)


def test_gap_item_serialises_with_the_cart_currency() -> None:
    item = S.GapItem(
        line_sku="l1",
        benefit_id="b1",
        label="a credit",
        naive_minor=124_000,
        witness_minor=30_000,
        delta_minor=94_000,
        reason=S.GAP_CREDIT_BALANCE,
    )
    assert item.to_dict(P.USD)["delta_display"] == "$940"
    assert item.to_dict(P.INR)["delta_display"] == "₹940"


def test_relabel_only_touches_display_fields_with_an_integer_sibling() -> None:
    payload = {
        "total_minor": 12_345,
        "total_display": "₹123.45",
        "label_display": "untouched",
        "nested": [{"x_minor": 100, "x_display": "₹1"}],
    }
    out = S.relabel(payload, P.USD)
    assert out["total_display"] == "$123.45"
    assert out["label_display"] == "untouched"
    assert out["nested"][0]["x_display"] == "$1"
    assert out["total_minor"] == 12_345


def test_mandate_for_carries_the_authorized_set_and_the_caveat() -> None:
    mandate = S.mandate_for(S.USD_CANDIDATE_SET, "mnd_test")
    assert mandate.disclosure_caveat is True
    assert set(mandate.authorized_instrument_ids) == set(S.USD_CANDIDATE_SET)


def test_issue_receipt_is_signed_by_the_agent_never_the_issuer() -> None:
    manifests = S.signed_manifests(S.USD_CANDIDATE_SET)
    evaluation = S.evaluate_cart(S.USD_TRIP_CART, manifests)
    signed = S.issue_receipt(
        evaluation,
        receipt_id="rcpt_test",
        cart=S.USD_TRIP_CART,
        manifests=manifests,
        mandate=S.mandate_for(S.USD_CANDIDATE_SET, "mnd_test"),
    )
    assert signed.signer_role != "issuer"
    assert signed.receipt.chosen_instrument_id == P.CHASE_SAPPHIRE_RESERVE_ID


def test_publish_manifests_records_the_content_hash_and_the_key(runs: dict) -> None:
    log = S.new_log()
    manifests = S.signed_manifests(S.USD_CANDIDATE_SET)
    S.publish_manifests(log, manifests.values(), S.DEMO_CLOCK)
    assert len(log) == len(S.USD_CANDIDATE_SET)
    for entry in log.entries:
        assert entry.kind == tlog.ENTRY_MANIFEST_PUBLISHED
        assert entry.body["issuer_key_id"] == S.DEMO_ISSUER_KEY_ID
        assert "SYNTHETIC member state" in entry.body["source"]


# --------------------------------------------------------------------------------------
# Properties that must hold on carts nobody wrote by hand
# --------------------------------------------------------------------------------------

_CATEGORIES = (
    P.CAT_AIRFARE,
    P.CAT_AIRLINE_INCIDENTAL,
    P.CAT_PREPAID_HOTEL_AMEX,
    P.CAT_HOTEL_DIRECT,
    P.CAT_CHASE_TRAVEL,
    P.CAT_DINING,
    P.CAT_RESY_DINING,
    P.CAT_US_SUPERMARKET,
    P.CAT_UBER,
    P.CAT_LYFT,
    P.CAT_DIGITAL_ENTERTAINMENT,
    P.CAT_LULULEMON,
    P.CAT_SMARTBUY_HOTEL,
    P.CAT_INDIA_RETAIL,
)


def _random_cart(rng: random.Random, currency: str) -> Cart:
    return Cart.of(
        rng.choice(("m_a", "amextravel.com", "smartbuy.hdfcbank.com")),
        [
            CartLine(
                sku=f"sku_{i}",
                description=f"line {i}",
                amount=rng.randrange(0, 5_000_000 if currency == P.INR else 300_000),
                mcc=rng.choice((3000, 4722, 5411, 5651, 5812, 5815, 7011)),
                category=rng.choice(_CATEGORIES),
            )
            for i in range(rng.randrange(1, 7))
        ],
        currency=currency,
    )


@pytest.mark.parametrize("manifest_id", [p.manifest_id for p in P.PROFILES])
def test_gap_attribution_reconciles_on_random_carts(manifest_id: str) -> None:
    """Property: the attributed deltas sum to the difference, on every cart.

    An attribution that only roughly explained the gap would be a story about the number
    rather than a derivation of it, and the console renders it line by line.
    """
    manifest = P.catalogue_by_id(S.DEMO_CLOCK)[manifest_id]
    manifests = S.signed_manifests([manifest_id])
    rng = random.Random(f"gap-{manifest_id}")
    for _ in range(250):
        cart = _random_cart(rng, manifest.currency)
        if not cart.lines:
            continue
        valuation = S.evaluate_cart(cart, manifests).candidates[0]
        assert valuation.witness is not None
        items = S.attribute_gap(manifest, cart, valuation.witness)
        assert sum(g.delta_minor for g in items) == valuation.overstatement_avoided_minor()
        assert valuation.overstatement_avoided_minor() >= 0
        assert all(g.delta_minor > 0 and g.reason for g in items)


@pytest.mark.parametrize("manifest_id", [p.manifest_id for p in P.PROFILES])
def test_the_evaluator_always_supports_its_own_assertion(manifest_id: str) -> None:
    """Property: what is asserted is exactly what the exhibited allocation realizes."""
    manifest = P.catalogue_by_id(S.DEMO_CLOCK)[manifest_id]
    manifests = S.signed_manifests([manifest_id])
    rng = random.Random(f"assert-{manifest_id}")
    for _ in range(250):
        cart = _random_cart(rng, manifest.currency)
        valuation = S.evaluate_cart(cart, manifests).candidates[0]
        assert valuation.status == STATUS_ATTESTED
        assert valuation.verification is not None and valuation.verification.ok
        assert valuation.asserted_minor == valuation.witness.realized_minor()
        assert valuation.asserted_minor <= valuation.naive_sum_minor


@pytest.mark.parametrize("manifest_id", [p.manifest_id for p in P.PROFILES])
def test_a_proposed_value_above_the_witness_is_always_refused(manifest_id: str) -> None:
    """Property: the claim channel is a hypothesis to reject, never a source of a number."""
    manifest = P.catalogue_by_id(S.DEMO_CLOCK)[manifest_id]
    manifests = S.signed_manifests([manifest_id])
    rng = random.Random(f"claim-{manifest_id}")
    for _ in range(120):
        cart = _random_cart(rng, manifest.currency)
        honest = S.evaluate_cart(cart, manifests).candidates[0]
        inflated = (honest.asserted_minor or 0) + rng.randrange(1, 10_000)
        refused = S.evaluate_cart(
            cart, manifests, claims={manifest_id: inflated}
        ).candidates[0]
        assert refused.status == STATUS_REFUSED
        assert refused.asserted_minor is None
        assert REFUSE_CLAIM_UNSUPPORTED in {r.code for r in refused.refusals}


# --------------------------------------------------------------------------------------
# The API surface the console drives
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from caveat.api import app

    return TestClient(app)


def test_api_lists_the_plumbline_scenarios(client) -> None:
    body = client.get("/api/plumbline/scenarios").json()
    assert body["scenarios"] == sorted(S.SCENARIOS)
    assert body["clock"] == S.DEMO_CLOCK


def test_api_runs_every_scenario_and_returns_the_same_bytes(client) -> None:
    for name in S.SCENARIOS:
        first = client.post(f"/api/plumbline/scenario/{name}")
        second = client.post(f"/api/plumbline/scenario/{name}")
        assert first.status_code == 200, first.text
        assert first.json() == second.json()
        assert first.json()["result"] == S.run(name)


def test_api_404s_an_unknown_scenario_and_names_the_known_ones(client) -> None:
    response = client.post("/api/plumbline/scenario/attribution")
    assert response.status_code == 404
    assert "overstatement" in response.json()["detail"]


def test_api_products_endpoint_labels_provenance_on_every_product(client) -> None:
    body = client.get("/api/plumbline/products").json()
    assert len(body["products"]) == len(P.PROFILES)
    assert body["point_valuation_hash"] == P.DEFAULT_VALUATION.policy_hash()
    for product in body["products"]:
        assert "publicly published card terms" in product["source"]
        assert "SYNTHETIC member state" in product["source"]
        assert product["considered_but_unpriced"]
        assert "never netted" in product["fee_note"].lower()
        # The signed manifest body carries facts only — no ranking, no acceptance.
        assert "ranking" not in product["manifest"]
        assert "acceptance" not in json.dumps(product["manifest"]).lower()
