"""The deterministic half of manifest authoring: what the validator will and will not pass.

The load-bearing assertions in this file are the ones about what gets REJECTED. A validator
that accepts everything is a rubber stamp with reason codes, and the whole claim of this
subsystem is that a model's numbers do not become signed numbers without passing it.
"""

from __future__ import annotations

import copy

import pytest

from plumbline.authoring import (
    ACCEPTANCE_PREDICATE_PRESENT,
    ADVISORY_BALANCE_EXHAUSTED,
    ADVISORY_NOT_ENROLLED,
    ADVISORY_UNCAPPED_EARN,
    CREDIT_CARRIES_RATE,
    CREDIT_HAS_NO_BALANCE,
    DRAFT_DUPLICATE_BENEFIT_ID,
    DRAFT_EMPTY_MANIFEST,
    DRAFT_MISSING_FIELD,
    DRAFT_NOT_AN_OBJECT,
    DRAFT_UNKNOWN_CURRENCY,
    DRAFT_UNKNOWN_FIELD,
    DRAFT_UNKNOWN_KIND,
    DRAFT_UNKNOWN_WINDOW,
    DRAFT_VERSION,
    EARN_CAP_INCONSISTENT_WITH_RATE,
    EARN_CAP_NOT_VALUE_DENOMINATED,
    EXCLUSIVITY_GROUP_INERT,
    EXCLUSIVITY_GROUP_OF_ONE,
    MANIFEST_SOURCE_MISSING,
    NUMBER_IS_A_BOOL,
    NUMBER_IS_A_FLOAT,
    NUMBER_NEGATIVE,
    NUMBER_NOT_AN_INTEGER,
    PROTECTION_CARRIES_RATE,
    PROTECTION_HAS_NO_VALUE,
    PROVENANCE_PLACEHOLDER,
    RATE_IMPLAUSIBLE,
    SEAL_FORGED,
    SEAL_PAYLOAD_MUTATED,
    SEAL_REVALIDATION_FAILED,
    SEVERITY_ADVISORY,
    UNPRICED_CARRIES_NUMBERS,
    UNPRICED_IN_EXCLUSIVITY_GROUP,
    UNREACHABLE_EMPTY_SELECTOR,
    UNREACHABLE_INVALID_MCC,
    UNREACHABLE_ZERO_VALUE,
    UNVERIFIED_AGAINST_SOURCE,
    UNVERIFIED_SUFFIX,
    VERDICT_ACCEPTED,
    VERDICT_REJECTED,
    AcceptedDraft,
    AuthoringError,
    draft_schema,
    reason_catalogue,
    schema_help,
    sign_accepted,
    validate_and_sign,
    validate_draft,
)
from plumbline.manifest import (
    KIND_CREDIT,
    KIND_EARN,
    KIND_PROTECTION,
    KIND_UNPRICED,
    verify_manifest,
)

KEY = "test-authoring-key"
T0 = 1753600000


# ----------------------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------------------


def good_draft() -> dict:
    """A draft that passes. Every negative test in this file mutates a copy of this one."""
    return {
        "version": DRAFT_VERSION,
        "manifest_id": "testbank-rewards-2026",
        "issuer": "Test Bank",
        "product": "Rewards Card",
        "currency": "USD",
        "issued_at": T0,
        "source": "Test Bank Rewards Card terms, effective 2026-01-01, sections 2 to 6",
        "benefits": [
            {
                "benefit_id": "earn_dining",
                "kind": KIND_EARN,
                "label": "4x at restaurants",
                "provenance": "Section 2.1, dining multiplier table",
                "eligibility": {"mccs": [5812, 5814], "categories": ["dining"]},
                "rate_bp": 400,
                "cap_qualifying_spend_minor": 5_000_000,
                "capacity_minor": 200_000,
                "window": "annual",
            },
            {
                "benefit_id": "credit_dining_monthly",
                "kind": KIND_CREDIT,
                "label": "Monthly dining credit",
                "provenance": "Section 3.1, statement credit schedule",
                "eligibility": {"mccs": [5812]},
                "capacity_minor": 1_000,
                "window": "monthly",
                "exclusivity_group": "dining_line",
            },
            {
                "benefit_id": "credit_dining_quarterly",
                "kind": KIND_CREDIT,
                "label": "Quarterly restaurant credit",
                "provenance": "Section 3.3, statement credit schedule",
                "eligibility": {"mccs": [5812, 5814]},
                "capacity_minor": 2_500,
                "window": "quarterly",
                "exclusivity_group": "dining_line",
            },
            {
                "benefit_id": "prot_purchase",
                "kind": KIND_PROTECTION,
                "label": "Purchase protection",
                "provenance": "Section 5.1, purchase protection rider",
                "eligibility": {"categories": ["electronics"]},
                "flat_minor": 50_000,
            },
            {
                "benefit_id": "svc_concierge",
                "kind": KIND_UNPRICED,
                "label": "Concierge",
                "provenance": "Section 6.1, member services",
                "note": "service value no integer on a receipt claims to capture",
            },
        ],
    }


def with_benefit(**overrides) -> dict:
    """A draft whose first benefit carries the overrides."""
    d = good_draft()
    d["benefits"][0].update(overrides)
    return d


def codes(draft) -> tuple[str, ...]:
    report, accepted = validate_draft(draft)
    assert (accepted is not None) == report.accepted
    return report.reason_codes()


# ----------------------------------------------------------------------------------
# The happy path
# ----------------------------------------------------------------------------------


def test_a_valid_draft_is_accepted_and_signs():
    draft = good_draft()
    report, accepted = validate_draft(draft)

    assert report.verdict == VERDICT_ACCEPTED
    assert report.reason_codes() == ()
    assert accepted is not None
    assert report.benefit_count == 5
    assert report.priced_count == 4

    signed = sign_accepted(accepted, KEY, key_id="unit-test")
    assert verify_manifest(signed, KEY)
    assert not verify_manifest(signed, "some-other-key")
    assert signed.key_id == "unit-test"
    assert signed.manifest.content_hash() == report.manifest_hash


def test_validate_and_sign_is_the_same_path_in_one_call():
    report, signed = validate_and_sign(good_draft(), KEY)
    assert report.accepted and signed is not None
    assert verify_manifest(signed, KEY)

    report, signed = validate_and_sign(with_benefit(rate_bp=-1), KEY)
    assert not report.accepted and signed is None


def test_the_signed_manifest_carries_provenance_and_the_cap_derivation():
    _, accepted = validate_draft(good_draft())
    earn = accepted.manifest.benefits[0]
    # Provenance lands inside the benefit dict, which is what gets signed. Carrying it
    # anywhere else would leave it droppable without breaking the signature.
    assert "Section 2.1, dining multiplier table" in earn.note
    assert "5000000 qualifying spend at 400 bp = 200000 value headroom" in earn.note
    assert earn.to_dict()["note"] == earn.note


def test_the_unverified_limitation_lands_inside_the_signed_bytes():
    _, accepted = validate_draft(good_draft())
    assert UNVERIFIED_SUFFIX.strip() in accepted.manifest.source
    assert UNVERIFIED_SUFFIX.strip() in accepted.manifest.canonical().decode("utf-8")


def test_the_limitation_is_on_every_report_accepted_or_rejected():
    for draft in (good_draft(), with_benefit(rate_bp=-1), "not even an object"):
        report, _ = validate_draft(draft)
        assert UNVERIFIED_AGAINST_SOURCE in report.advisory_codes()
        assert "NOT" in report.to_dict()["limitation"]


def test_advisories_never_block():
    d = good_draft()
    d["benefits"][1]["capacity_minor"] = 0  # balance spent: state, not a structural defect
    d["benefits"][3]["requires_enrollment"] = True
    d["benefits"][3]["enrolled"] = False
    report, accepted = validate_draft(d)
    assert report.accepted and accepted is not None
    assert ADVISORY_BALANCE_EXHAUSTED in report.advisory_codes()
    assert ADVISORY_NOT_ENROLLED in report.advisory_codes()
    assert all(f.severity == SEVERITY_ADVISORY for f in report.advisories())


def test_an_uncapped_earn_is_an_advisory_not_an_error():
    d = with_benefit(capacity_minor=None, cap_qualifying_spend_minor=None)
    report, _ = validate_draft(d)
    assert report.accepted
    assert ADVISORY_UNCAPPED_EARN in report.advisory_codes()


# ----------------------------------------------------------------------------------
# Numbers
# ----------------------------------------------------------------------------------


def test_a_draft_with_a_negative_rate_is_rejected():
    report, accepted = validate_draft(with_benefit(rate_bp=-400))
    assert report.verdict == VERDICT_REJECTED
    assert accepted is None
    assert NUMBER_NEGATIVE in report.reason_codes()
    # And it is not repaired: the rejected draft is simply not turned into a manifest.
    assert report.manifest_hash is None


@pytest.mark.parametrize("field", ["rate_bp", "capacity_minor", "cap_qualifying_spend_minor"])
def test_every_magnitude_must_be_non_negative(field):
    assert NUMBER_NEGATIVE in codes(with_benefit(**{field: -1}))


def test_a_negative_flat_value_on_a_protection_is_rejected():
    d = good_draft()
    d["benefits"][3]["flat_minor"] = -100
    assert NUMBER_NEGATIVE in codes(d)


def test_floats_are_rejected_rather_than_coerced_even_when_exact():
    assert NUMBER_IS_A_FLOAT in codes(with_benefit(capacity_minor=200_000.0))
    assert NUMBER_IS_A_FLOAT in codes(with_benefit(rate_bp=4.5))


def test_a_number_written_as_a_string_is_rejected():
    assert NUMBER_NOT_AN_INTEGER in codes(with_benefit(rate_bp="400"))


def test_a_bool_where_a_number_belongs_is_rejected():
    # bool is a subclass of int, so a permissive check would let True through as 1.
    assert NUMBER_IS_A_BOOL in codes(with_benefit(rate_bp=True))


def test_a_rate_above_one_hundred_percent_is_a_decimal_shift():
    assert RATE_IMPLAUSIBLE in codes(with_benefit(rate_bp=40_000, capacity_minor=None,
                                                  cap_qualifying_spend_minor=None))


# ----------------------------------------------------------------------------------
# Earn capacity is value-denominated
# ----------------------------------------------------------------------------------


def test_a_spend_cap_in_the_value_slot_is_rejected():
    # The single most likely authoring error: the term sheet says "the first $50,000 of
    # dining" and the figure goes straight into capacity_minor, overstating by 10000/rate.
    report, _ = validate_draft(with_benefit(capacity_minor=5_000_000))
    assert EARN_CAP_INCONSISTENT_WITH_RATE in report.reason_codes()
    message = " ".join(f.message for f in report.errors())
    assert "200000 of value" in message
    assert "overstates" in message


def test_an_earn_cap_with_no_stated_derivation_is_rejected():
    d = with_benefit(capacity_minor=200_000)
    d["benefits"][0].pop("cap_qualifying_spend_minor")
    assert EARN_CAP_NOT_VALUE_DENOMINATED in codes(d)


def test_a_spend_cap_with_no_value_headroom_is_rejected():
    assert EARN_CAP_NOT_VALUE_DENOMINATED in codes(with_benefit(capacity_minor=None))


def test_an_off_by_one_cap_conversion_is_rejected():
    assert EARN_CAP_INCONSISTENT_WITH_RATE in codes(with_benefit(capacity_minor=200_001))


def test_the_conversion_uses_floor_division_so_no_float_ever_appears():
    # 3333 * 401 // 10000 == 133 (133.65 floored). Accepting the floored figure is the
    # conservative direction; accepting 134 would assert value the card cannot deliver.
    d = with_benefit(rate_bp=401, cap_qualifying_spend_minor=3_333, capacity_minor=133)
    assert codes(d) == ()
    assert EARN_CAP_INCONSISTENT_WITH_RATE in codes(
        with_benefit(rate_bp=401, cap_qualifying_spend_minor=3_333, capacity_minor=134)
    )


# ----------------------------------------------------------------------------------
# Per-kind coherence
# ----------------------------------------------------------------------------------


def test_a_credit_without_a_balance_is_rejected():
    d = good_draft()
    d["benefits"][1].pop("capacity_minor")
    assert CREDIT_HAS_NO_BALANCE in codes(d)


def test_a_credit_carrying_a_rate_is_rejected():
    d = good_draft()
    d["benefits"][1]["rate_bp"] = 200
    assert CREDIT_CARRIES_RATE in codes(d)


def test_a_protection_carrying_a_rate_is_rejected():
    d = good_draft()
    d["benefits"][3]["rate_bp"] = 200
    assert PROTECTION_CARRIES_RATE in codes(d)


def test_a_protection_with_no_stated_value_is_rejected():
    d = good_draft()
    d["benefits"][3].pop("flat_minor")
    report, _ = validate_draft(d)
    assert PROTECTION_HAS_NO_VALUE in report.reason_codes()
    # The guidance names the honest alternative rather than inviting an invented number.
    assert "unpriced" in reason_catalogue()[PROTECTION_HAS_NO_VALUE]


def test_an_unpriced_benefit_may_not_carry_numbers():
    d = good_draft()
    d["benefits"][4]["flat_minor"] = 5_000
    assert UNPRICED_CARRIES_NUMBERS in codes(d)


def test_an_unpriced_benefit_may_not_join_an_exclusivity_group():
    d = good_draft()
    d["benefits"][4]["exclusivity_group"] = "dining_line"
    assert UNPRICED_IN_EXCLUSIVITY_GROUP in codes(d)


# ----------------------------------------------------------------------------------
# Reachability
# ----------------------------------------------------------------------------------


def test_a_draft_with_an_unreachable_benefit_is_rejected():
    # A protection worth zero per line can never appear in a witness, so it occupies a
    # manifest slot and scores zero forever.
    d = good_draft()
    d["benefits"][3]["flat_minor"] = 0
    report, accepted = validate_draft(d)
    assert report.verdict == VERDICT_REJECTED
    assert accepted is None
    assert UNREACHABLE_ZERO_VALUE in report.reason_codes()


def test_an_earn_benefit_with_no_rate_is_unreachable():
    assert UNREACHABLE_ZERO_VALUE in codes(
        with_benefit(rate_bp=0, capacity_minor=None, cap_qualifying_spend_minor=None)
    )


@pytest.mark.parametrize("mcc", [0, -5812, 58120, 10_000])
def test_an_eligibility_mcc_outside_the_four_digit_range_is_unreachable(mcc):
    d = with_benefit(eligibility={"mccs": [mcc]})
    assert set(codes(d)) & {UNREACHABLE_INVALID_MCC, NUMBER_NEGATIVE}


def test_an_empty_selector_entry_is_unreachable():
    assert UNREACHABLE_EMPTY_SELECTOR in codes(with_benefit(eligibility={"categories": ["  "]}))


# ----------------------------------------------------------------------------------
# Exclusivity
# ----------------------------------------------------------------------------------


def test_an_exclusivity_group_of_one_is_rejected():
    # The split is the danger: two credits that should compete for one line both apply,
    # and the naive sum the allocator exists to prevent comes back through the manifest.
    d = good_draft()
    d["benefits"][2]["exclusivity_group"] = None
    report, _ = validate_draft(d)
    assert EXCLUSIVITY_GROUP_OF_ONE in report.reason_codes()
    assert "double-count" in reason_catalogue()[EXCLUSIVITY_GROUP_OF_ONE]


def test_a_group_whose_members_can_never_collide_is_inert():
    d = good_draft()
    # Disjoint MCC sets: no single cart line can ever admit both, so the exclusivity
    # constraint never binds and declaring it is decoration.
    d["benefits"][1]["eligibility"] = {"mccs": [5812]}
    d["benefits"][2]["eligibility"] = {"mccs": [4511]}
    assert EXCLUSIVITY_GROUP_INERT in codes(d)


def test_an_open_selector_can_always_collide():
    d = good_draft()
    d["benefits"][1]["eligibility"] = {"mccs": [5812]}
    d["benefits"][2]["eligibility"] = {}  # no restriction, so it admits that line too
    report, _ = validate_draft(d)
    assert EXCLUSIVITY_GROUP_INERT not in report.reason_codes()


def test_an_empty_group_name_is_rejected():
    d = good_draft()
    d["benefits"][1]["exclusivity_group"] = "   "
    report, _ = validate_draft(d)
    assert not report.accepted


# ----------------------------------------------------------------------------------
# The deleted field
# ----------------------------------------------------------------------------------


def test_a_draft_with_an_acceptance_predicate_is_rejected():
    d = good_draft()
    d["acceptance"] = {"not_accepted_merchants": ["warehouse_clubs"]}
    report, accepted = validate_draft(d)
    assert report.verdict == VERDICT_REJECTED
    assert accepted is None
    assert ACCEPTANCE_PREDICATE_PRESENT in report.reason_codes()


def test_an_acceptance_predicate_on_a_benefit_is_rejected():
    assert ACCEPTANCE_PREDICATE_PRESENT in codes(with_benefit(declined_merchants=["x"]))


def test_an_acceptance_predicate_written_as_free_text_is_rejected():
    assert ACCEPTANCE_PREDICATE_PRESENT in codes(
        with_benefit(note="this card is not accepted at warehouse clubs")
    )
    assert ACCEPTANCE_PREDICATE_PRESENT in codes(
        with_benefit(label="4x dining (does not accept fuel retailers)")
    )


def test_the_closed_schema_is_the_barrier_the_keyword_screen_is_only_the_reason_code():
    # An acceptance field the keyword screen does not recognise still cannot reach a
    # signature, because the schema is closed. This is the structural guarantee; the
    # screen exists to return the right code, not to be the wall.
    report, accepted = validate_draft(with_benefit(where_it_wont_work=["clubs"]))
    assert accepted is None
    assert DRAFT_UNKNOWN_FIELD in report.reason_codes()
    assert ACCEPTANCE_PREDICATE_PRESENT not in report.reason_codes()


def test_no_accepted_manifest_can_contain_an_acceptance_field():
    _, accepted = validate_draft(good_draft())
    for benefit in accepted.manifest.body()["benefits"]:
        assert not any("accept" in k.lower() or "declin" in k.lower() for k in benefit)


# ----------------------------------------------------------------------------------
# Provenance
# ----------------------------------------------------------------------------------


def test_a_benefit_without_provenance_is_rejected():
    d = good_draft()
    d["benefits"][0].pop("provenance")
    report, _ = validate_draft(d)
    assert DRAFT_MISSING_FIELD in report.reason_codes()


@pytest.mark.parametrize("value", ["", "TBD", "todo", "n/a", "unknown", "terms", "short"])
def test_a_placeholder_provenance_is_rejected(value):
    assert PROVENANCE_PLACEHOLDER in codes(with_benefit(provenance=value))


def test_the_manifest_must_name_its_source_document():
    d = good_draft()
    d["source"] = "terms"
    assert MANIFEST_SOURCE_MISSING in codes(d)
    d.pop("source")
    assert MANIFEST_SOURCE_MISSING in codes(d)


# ----------------------------------------------------------------------------------
# Structure
# ----------------------------------------------------------------------------------


def test_a_non_object_draft_is_rejected_without_raising():
    for value in ("[]", 7, None, ["a"]):
        report, accepted = validate_draft(value)
        assert report.verdict == VERDICT_REJECTED and accepted is None
        assert DRAFT_NOT_AN_OBJECT in report.reason_codes()


def test_missing_required_manifest_fields_are_named():
    d = good_draft()
    d.pop("issuer")
    d.pop("currency")
    report, _ = validate_draft(d)
    assert DRAFT_MISSING_FIELD in report.reason_codes()
    assert {f.path for f in report.errors() if f.code == DRAFT_MISSING_FIELD} == {
        "issuer",
        "currency",
    }


def test_an_empty_benefit_list_declares_nothing():
    d = good_draft()
    d["benefits"] = []
    assert DRAFT_EMPTY_MANIFEST in codes(d)


def test_duplicate_benefit_ids_are_rejected():
    d = good_draft()
    d["benefits"][2]["benefit_id"] = "credit_dining_monthly"
    assert DRAFT_DUPLICATE_BENEFIT_ID in codes(d)


def test_unknown_kinds_windows_and_currencies_are_rejected():
    assert DRAFT_UNKNOWN_KIND in codes(with_benefit(kind="cashback"))
    assert DRAFT_UNKNOWN_WINDOW in codes(with_benefit(window="fortnightly"))
    d = good_draft()
    d["currency"] = "GBP"
    assert DRAFT_UNKNOWN_CURRENCY in codes(d)


def test_the_report_serialises_with_guidance_for_every_finding():
    report, _ = validate_draft(with_benefit(rate_bp=-1))
    blob = report.to_dict()
    assert blob["verdict"] == VERDICT_REJECTED
    assert blob["accepted"] is False
    assert all(f["guidance"] for f in blob["findings"])
    assert blob["draft_hash"] and blob["manifest_hash"] is None


def test_validation_is_deterministic_and_does_not_mutate_the_draft():
    draft = good_draft()
    before = copy.deepcopy(draft)
    first, _ = validate_draft(draft)
    second, _ = validate_draft(draft)
    assert draft == before
    assert first.draft_hash == second.draft_hash
    assert first.manifest_hash == second.manifest_hash


# ----------------------------------------------------------------------------------
# The signing gate
# ----------------------------------------------------------------------------------


def test_a_raw_dict_cannot_be_signed():
    with pytest.raises(AuthoringError) as exc:
        sign_accepted(good_draft(), KEY)
    assert exc.value.code == SEAL_FORGED


def test_an_accepted_draft_cannot_be_forged():
    _, accepted = validate_draft(good_draft())
    with pytest.raises(AuthoringError) as exc:
        AcceptedDraft(
            manifest=accepted.manifest,
            report=accepted.report,
            payload_json=accepted.payload_json,
            manifest_hash=accepted.manifest_hash,
        )
    assert exc.value.code == SEAL_FORGED


def test_a_sealed_payload_swapped_for_an_invalid_one_fails_revalidation():
    import dataclasses
    import json

    _, accepted = validate_draft(good_draft())
    tampered = accepted.payload()
    tampered["benefits"][0]["capacity_minor"] = 999_999_999  # spend cap back in the value slot
    # `dataclasses.replace` carries the grant forward, so __post_init__ cannot catch this.
    # That is deliberate: it is exactly the attack sign_accepted has to catch on its own.
    forged = dataclasses.replace(accepted, payload_json=json.dumps(tampered, sort_keys=True))
    with pytest.raises(AuthoringError) as exc:
        sign_accepted(forged, KEY)
    assert exc.value.code == SEAL_REVALIDATION_FAILED


def test_a_sealed_payload_swapped_for_a_different_valid_one_fails_the_hash_check():
    import dataclasses
    import json

    _, accepted = validate_draft(good_draft())
    other = good_draft()
    other["benefits"][1]["capacity_minor"] = 9_999  # still valid, different manifest
    forged = dataclasses.replace(accepted, payload_json=json.dumps(other, sort_keys=True))
    with pytest.raises(AuthoringError) as exc:
        sign_accepted(forged, KEY)
    assert exc.value.code == SEAL_PAYLOAD_MUTATED


def test_signing_catches_a_manifest_swapped_under_a_valid_seal():
    import dataclasses

    _, good = validate_draft(good_draft())
    _, other = validate_draft({**good_draft(), "manifest_id": "testbank-rewards-2026-b"})
    swapped = dataclasses.replace(good, manifest=other.manifest)
    with pytest.raises(AuthoringError) as exc:
        sign_accepted(swapped, KEY)
    assert exc.value.code == SEAL_PAYLOAD_MUTATED


def test_a_rejected_draft_produces_no_accepted_object_at_all():
    for bad in (with_benefit(rate_bp=-1), {"acceptance": {}}, {}):
        report, accepted = validate_draft(bad)
        assert not report.accepted
        assert accepted is None


# ----------------------------------------------------------------------------------
# The schema handed to whoever is drafting
# ----------------------------------------------------------------------------------


def test_the_schema_documents_the_value_denomination_rule():
    props = draft_schema()["properties"]["benefits"]["items"]["properties"]
    assert "cap_qualifying_spend_minor * rate_bp // 10000" in props["capacity_minor"]["description"]
    assert "provenance" in draft_schema()["properties"]["benefits"]["items"]["required"]


def test_the_schema_help_states_the_deletion_out_loud():
    help_text = schema_help()
    assert "acceptance predicate" in help_text
    assert "MINOR units" in help_text


# ----------------------------------------------------------------------------------
# End to end: an authored manifest is a usable manifest
# ----------------------------------------------------------------------------------


def test_an_authored_manifest_values_a_cart_through_the_real_evaluator():
    from caveat.cart import Cart, CartLine
    from plumbline.evaluate import evaluate

    _, accepted = validate_draft(good_draft())
    signed = sign_accepted(accepted, KEY, key_id="authored")

    cart = Cart.of(
        "m_test",
        [CartLine(sku="s1", description="dinner", amount=3_000, mcc=5812, category="dining")],
        currency="USD",
    )
    ev = evaluate(cart=cart, manifests=[signed], now=T0, keys={"authored": KEY})

    assert ev.refusals == ()
    entry = ev.ranking.entries[0]
    # 400 bp of 3000 is 120, plus exactly ONE of the two competing dining credits (2500,
    # capped by the line). The exclusivity group the validator insisted on is what keeps
    # the other 1000 out — a group of one would have produced 3620 for a card that cannot
    # deliver it, which is the overstatement this whole subsystem exists to prevent.
    assert entry.asserted_minor == 2620
    assert entry.manifest_id == "testbank-rewards-2026"


def test_every_reason_code_has_guidance():
    import plumbline.authoring as authoring

    catalogue = reason_catalogue()
    declared = {
        v
        for k, v in vars(authoring).items()
        if k.isupper() and isinstance(v, str) and v == k
    }
    assert len(declared) > 30, "the constant scan found nothing; the check would be vacuous"
    missing = declared - set(catalogue)
    assert not missing, f"reason codes with no fix guidance: {sorted(missing)}"
