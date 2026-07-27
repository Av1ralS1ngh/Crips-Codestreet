"""The Decision Receipt: what it records, what it refuses to sign, and how it fails.

Four things are under test, and the order reflects what an adversary would attack first:

  the signing boundary    there must be no code path that puts an issuer signature over a
                          comparison between instruments. Not "we do not do that" —
                          "the function raises".
  omission                a receipt that names only the winner cannot evidence a silent
                          exclusion. Every instrument the mandate authorised must appear,
                          and dropping one must be detectable from the document and from
                          the transparency log.
  compliance symmetry     a faithful platform must earn a positive attestation from the
                          same machinery that catches an unfaithful one.
  graceful degrade        a missing counterpart receipt proceeds by default and is recorded
                          as a gap. Enforcement is a mode the Card Member elects.
"""

from __future__ import annotations

import copy
import random

import pytest

from caveat.cart import Cart, CartLine
from plumbline.attribution import observe_receipt
from plumbline.evaluate import (
    CRITERION_MAX_ASSERTED,
    CRITERION_MAX_PROTECTION_THEN_VALUE,
    STATUS_ATTESTED,
    STATUS_REFUSED,
    EvaluationError,
    ValuationPolicy,
    evaluate,
)
from plumbline.manifest import (
    KIND_CREDIT,
    KIND_EARN,
    KIND_PROTECTION,
    KIND_UNPRICED,
    MANIFEST_VERSION,
    WINDOW_ANNUAL,
    WINDOW_MONTHLY,
    Benefit,
    Eligibility,
    build_manifest,
    verify_manifest,
)
from plumbline.receipt import (
    ATTEST_CANDIDATE_SET_INCOMPLETE,
    ATTEST_CHOSEN_NOT_A_CANDIDATE,
    ATTEST_CHOSEN_NOT_AUTHORIZED,
    ATTEST_CHOSEN_UNVERIFIED,
    ATTEST_DEVIATED,
    ATTEST_FAITHFUL,
    ATTEST_NO_RANKING,
    ATTEST_ORDER_INCONSISTENT,
    ATTEST_RANKED_SET_WRONG,
    ATTEST_UNKNOWN_CRITERION,
    CHECK_CANDIDATE_DIGEST,
    CHECK_CART_HASH,
    CHECK_EVALUATION_AGREES,
    CHECK_MANIFEST_SIGNATURES,
    CHECK_NO_ISSUER_SIGNED_RANKING,
    CHECK_RANKING,
    CHECK_SIGNATURE,
    CHECK_SIGNER_NOT_ISSUER,
    CHECK_WITNESS_HASHES,
    DEFAULT_DISCLOSURES,
    POSTURE_ENFORCE,
    POSTURE_OBSERVE_ONLY,
    REASON_DISCLOSURE_CAVEAT_UNDISCHARGED,
    REASON_SELECTION_ATTESTED,
    REASON_UNATTESTED_SELECTION,
    ROLE_AGENT,
    ROLE_CARDHOLDER,
    ROLE_ISSUER,
    ROLE_PLATFORM,
    UNPRICED_DISCLOSURE,
    WITNESS_ABSENT,
    WITNESS_REFUSED,
    WITNESS_VERIFIED,
    AnchoredReceipt,
    AttestationFinding,
    CandidateRecord,
    CheckoutSession,
    CounterpartAssessment,
    DecisionReceipt,
    Identity,
    IssuerSigningBoundaryError,
    MandateBinding,
    RankingAttestation,
    ReceiptError,
    ReceiptVerification,
    SignedReceipt,
    UnpricedConsideration,
    anchor_receipt,
    assess_counterpart,
    attest_ranking,
    build_receipt,
    build_receipt_from_evaluation,
    candidate_from_valuation,
    candidate_record,
    candidate_set_digest,
    find_issuer_role_signatures,
    find_ranking_vocabulary,
    instruments_authorized,
    instruments_considered,
    issuer_sign_facts,
    issuer_signature_scope_violations,
    key_id,
    policy_from_dict,
    ranking_from_candidates,
    ranking_from_dict,
    receipts_from_log,
    record_unattested_selection,
    sign_receipt,
    stable_evaluation_body,
    unpriced_considerations,
    verify_receipt,
    witness_content_hash,
)
from plumbline.transparency import (
    ENTRY_RECEIPT,
    ENTRY_UNATTESTED_SELECTION,
    TransparencyLog,
)
from plumbline.witness import Assignment, Witness, verify_witness

T0 = 1_753_600_000

AMEX_KEY = "prototype-amex-issuer-key"
RIVAL_KEY = "prototype-rival-issuer-key"
THIN_KEY = "prototype-thin-issuer-key"
AGENT_KEY = "prototype-agent-signing-key"
LOG_KEY = "prototype-transparency-log-key"

AMEX_KEY_ID = "amex-prototype-2026"
RIVAL_KEY_ID = "rival-prototype-2026"
THIN_KEY_ID = "thin-prototype-2026"
ISSUER_KEYS = {AMEX_KEY_ID: AMEX_KEY, RIVAL_KEY_ID: RIVAL_KEY, THIN_KEY_ID: THIN_KEY}

MCC_DINING = 5812
MCC_APPLIANCE = 5722

DINNER = CartLine(
    sku="sku_dinner",
    description="Tasting menu for two",
    amount=800_000,
    mcc=MCC_DINING,
    category="dining",
)
ESPRESSO = CartLine(
    sku="sku_espresso",
    description="Espresso machine",
    amount=400_000,
    mcc=MCC_APPLIANCE,
    category="appliances",
)

AMEX_ID = "mf_amex_platinum"
RIVAL_ID = "mf_rival_sapphire"
THIN_ID = "mf_thin_store"

# Every manifest and cart in this file is INR-denominated, and the serializers that
# render money now require the currency rather than defaulting one.
CURRENCY = "INR"

AGENT = Identity(kind=ROLE_AGENT, identifier="agent_shopbot_v2", name="ShopBot v2.1")
PLATFORM = Identity(kind=ROLE_PLATFORM, identifier="platform_acme", name="Acme Checkout")


# ======================================================================================
# Fixtures — a three-instrument wallet and a cart that makes them compete
# ======================================================================================


@pytest.fixture()
def cart() -> Cart:
    return Cart.of("m_resy_partner", [DINNER, ESPRESSO])


@pytest.fixture()
def amex_manifest():
    """Two dining credits in one exclusivity group, a capped multiplier, unpriced service."""
    return build_manifest(
        manifest_id=AMEX_ID,
        issuer="American Express",
        product="Platinum",
        issued_at=T0 - 86_400,
        benefits=[
            Benefit(
                benefit_id="amex_dining_5x",
                kind=KIND_EARN,
                label="5x on dining",
                eligibility=Eligibility(mccs=(MCC_DINING,)),
                rate_bp=500,
                capacity_minor=200_000,
                window=WINDOW_ANNUAL,
            ),
            Benefit(
                benefit_id="amex_resy_credit",
                kind=KIND_CREDIT,
                label="Resy dining credit",
                eligibility=Eligibility(mccs=(MCC_DINING,)),
                capacity_minor=50_000,
                exclusivity_group="dining_offset",
                window=WINDOW_MONTHLY,
            ),
            Benefit(
                benefit_id="amex_dining_credit",
                kind=KIND_CREDIT,
                label="Monthly dining credit",
                eligibility=Eligibility(mccs=(MCC_DINING,)),
                capacity_minor=25_000,
                exclusivity_group="dining_offset",
                window=WINDOW_MONTHLY,
            ),
            Benefit(
                benefit_id="amex_purchase_protection",
                kind=KIND_PROTECTION,
                label="Purchase protection",
                eligibility=Eligibility(mccs=(MCC_APPLIANCE,)),
                flat_minor=12_000,
            ),
            Benefit(
                benefit_id="amex_concierge",
                kind=KIND_UNPRICED,
                label="24/7 Concierge and Fine Hotels",
                note="service value, deliberately not scored",
            ),
        ],
    )


@pytest.fixture()
def rival_manifest():
    """A flat earn card. Wins nothing here, and must still appear in every candidate set."""
    return build_manifest(
        manifest_id=RIVAL_ID,
        issuer="Rival Bank",
        product="Sapphire",
        issued_at=T0 - 86_400,
        benefits=[
            Benefit(
                benefit_id="rival_dining_3x",
                kind=KIND_EARN,
                label="3x on dining",
                eligibility=Eligibility(mccs=(MCC_DINING,)),
                rate_bp=300,
            ),
            Benefit(
                benefit_id="rival_lounge",
                kind=KIND_UNPRICED,
                label="Lounge network access",
                note="membership value, deliberately not scored",
            ),
        ],
    )


@pytest.fixture()
def thin_manifest():
    """An instrument worth nothing on this cart. Considered, ranked last, and recorded."""
    return build_manifest(
        manifest_id=THIN_ID,
        issuer="Thin Store Card",
        product="Store",
        issued_at=T0 - 86_400,
        benefits=[
            Benefit(
                benefit_id="thin_fuel_2x",
                kind=KIND_EARN,
                label="2x on fuel",
                eligibility=Eligibility(mccs=(5541,)),
                rate_bp=200,
            ),
        ],
    )


@pytest.fixture()
def signed_manifests(amex_manifest, rival_manifest, thin_manifest):
    return {
        AMEX_ID: issuer_sign_facts(amex_manifest, key=AMEX_KEY, key_reference=AMEX_KEY_ID),
        RIVAL_ID: issuer_sign_facts(rival_manifest, key=RIVAL_KEY, key_reference=RIVAL_KEY_ID),
        THIN_ID: issuer_sign_facts(thin_manifest, key=THIN_KEY, key_reference=THIN_KEY_ID),
    }


@pytest.fixture()
def session(cart) -> CheckoutSession:
    return CheckoutSession.of("sess_demo_01", cart, T0)


@pytest.fixture()
def mandate() -> MandateBinding:
    return MandateBinding(
        mandate_id="mnd_cardholder_root",
        authorized_instrument_ids=(AMEX_ID, RIVAL_ID, THIN_ID),
    )


@pytest.fixture()
def evaluation(cart, signed_manifests):
    return evaluate(
        cart=cart,
        manifests=list(signed_manifests.values()),
        now=T0,
        keys=ISSUER_KEYS,
        policy=ValuationPolicy(),
    )


@pytest.fixture()
def receipt(evaluation, session, mandate, signed_manifests):
    return build_receipt_from_evaluation(
        receipt_id="rcpt_demo_01",
        issued_at=T0,
        evaluation=evaluation,
        session=session,
        mandate=mandate,
        agent=AGENT,
        platform=PLATFORM,
        signed_manifests=signed_manifests,
    )


@pytest.fixture()
def signed(receipt):
    return sign_receipt(receipt, key=AGENT_KEY)


# ======================================================================================
# The signing boundary — structurally impossible, not merely discouraged
# ======================================================================================


def test_issuer_signs_a_manifest_and_the_signature_verifies(amex_manifest):
    signed_manifest = issuer_sign_facts(amex_manifest, key=AMEX_KEY, key_reference=AMEX_KEY_ID)
    assert verify_manifest(signed_manifest, AMEX_KEY)
    assert not verify_manifest(signed_manifest, RIVAL_KEY)
    assert signed_manifest.key_id == AMEX_KEY_ID
    assert signed_manifest.manifest.content_hash() == amex_manifest.content_hash()


def test_issuer_cannot_sign_a_receipt(receipt):
    with pytest.raises(IssuerSigningBoundaryError, match="the issuer signs facts only"):
        issuer_sign_facts(receipt.body(), key=AMEX_KEY)


def test_issuer_cannot_sign_a_ranking(receipt):
    with pytest.raises(IssuerSigningBoundaryError, match="the issuer signs facts only"):
        issuer_sign_facts(receipt.ranking.to_dict(currency=CURRENCY), key=AMEX_KEY)


def test_issuer_cannot_sign_an_arbitrary_object():
    for subject in ("just a string", 42, ["a", "list"], None):
        with pytest.raises(IssuerSigningBoundaryError, match="Refusing to sign"):
            issuer_sign_facts(subject, key=AMEX_KEY)


def test_issuer_cannot_sign_a_manifest_that_smuggles_a_ranking(amex_manifest):
    """Structurally a manifest, but it names an order. Refused on content, not on type."""
    body = amex_manifest.body()
    body["ranking"] = {"chosen_instrument_id": AMEX_ID}
    with pytest.raises(IssuerSigningBoundaryError, match="ranking content at"):
        issuer_sign_facts(body, key=AMEX_KEY)


def test_issuer_cannot_sign_a_ranking_buried_inside_a_benefit(amex_manifest):
    body = amex_manifest.body()
    body["benefits"][0]["note"] = "fine"
    body["benefits"][0]["comparison"] = {"beats": RIVAL_ID}
    with pytest.raises(IssuerSigningBoundaryError) as exc:
        issuer_sign_facts(body, key=AMEX_KEY)
    assert "$.benefits[0].comparison" in str(exc.value)


def test_issuer_cannot_sign_a_manifest_that_smuggles_an_acceptance_predicate(amex_manifest):
    """Must-fix #3, enforced at the signer and not only at the drafting validator.

    `Manifest` has no acceptance field, so the key is dropped and the signature would be
    byte-identical to the clean one — which is exactly the problem. Discarding it silently
    leaves the caller believing an issuer signature covers where the card is refused.
    """
    body = amex_manifest.body()
    body["acceptance"] = {"declined_at": ["m_costco"]}
    with pytest.raises(IssuerSigningBoundaryError) as exc:
        issuer_sign_facts(body, key=AMEX_KEY)
    assert "acceptance content at" in str(exc.value)
    assert "routing layer" in str(exc.value)


def test_issuer_cannot_sign_an_acceptance_predicate_buried_below_a_benign_key(amex_manifest):
    body = amex_manifest.body()
    body["notes"] = {"internal": {"declined_merchants": ["m_costco"]}}
    with pytest.raises(IssuerSigningBoundaryError) as exc:
        issuer_sign_facts(body, key=AMEX_KEY)
    assert "notes.internal.declined_merchants" in str(exc.value)


def test_issuer_cannot_sign_free_text_that_names_where_the_card_is_refused(amex_manifest):
    body = amex_manifest.body()
    body["source"] = f"{body['source']} Not accepted at warehouse clubs."
    with pytest.raises(IssuerSigningBoundaryError, match="acceptance content at"):
        issuer_sign_facts(body, key=AMEX_KEY)


def test_the_real_catalogue_still_signs_cleanly_through_both_screens():
    """The two content screens must reject smuggling without rejecting real manifests."""
    from plumbline import products as P
    from plumbline import scenarios as S

    for manifest in P.catalogue(S.DEMO_CLOCK):
        signed = issuer_sign_facts(manifest, key=AMEX_KEY)
        assert signed.signature


def test_sign_receipt_refuses_the_issuer_role(receipt):
    with pytest.raises(IssuerSigningBoundaryError, match="carries a ranking"):
        sign_receipt(receipt, key=AMEX_KEY, signer_role=ROLE_ISSUER)


def test_sign_receipt_refuses_an_unknown_role(receipt):
    with pytest.raises(ReceiptError, match="unknown signer role"):
        sign_receipt(receipt, key=AGENT_KEY, signer_role="merchant")


def test_the_cardholder_may_sign_a_receipt(receipt):
    signed = sign_receipt(receipt, key=AGENT_KEY, signer_role=ROLE_CARDHOLDER)
    assert signed.signer_role == ROLE_CARDHOLDER
    assert verify_receipt(signed, AGENT_KEY).ok


def test_a_valuation_policy_may_not_belong_to_an_issuer():
    with pytest.raises(EvaluationError, match="an issuer signs facts"):
        ValuationPolicy(author=ROLE_ISSUER)


def test_the_issuer_signature_on_a_candidate_covers_only_the_manifest(
    receipt, signed_manifests
):
    """The signature travelling in the receipt verifies over manifest bytes, and nothing else."""
    candidate = receipt.candidate(AMEX_ID)
    signed_manifest = signed_manifests[AMEX_ID]
    assert candidate.issuer_signature == signed_manifest.signature
    assert verify_manifest(signed_manifest, AMEX_KEY)

    # The same signature does not cover the candidate record, the ranking or the receipt.
    for payload in (
        candidate.to_dict(currency=CURRENCY),
        receipt.ranking.to_dict(currency=CURRENCY),
        receipt.body(),
    ):
        assert not verify_manifest(
            {"body": payload, "signature": candidate.issuer_signature}, AMEX_KEY
        )


def test_no_issuer_key_verifies_the_receipt(receipt, signed):
    for issuer_key in (AMEX_KEY, RIVAL_KEY, THIN_KEY):
        assert not verify_receipt(signed, issuer_key).ok


def test_ranking_vocabulary_scan_is_recursive():
    hits = find_ranking_vocabulary({"a": {"b": [{"ranking": 1}, {"ok": 2}]}, "rank": 3})
    assert "$.a.b[0].ranking" in hits
    assert "$.rank" in hits
    assert len(hits) == 2
    assert find_ranking_vocabulary({"benefit_id": "x", "rate_bp": 500}) == ()


def test_a_forged_issuer_role_signature_is_found_anywhere_in_the_document(signed):
    document = signed.to_dict()
    document["receipt"]["candidate_set"]["candidates"][0]["extra_signature"] = {
        "role": ROLE_ISSUER,
        "value": "deadbeef",
    }
    assert find_issuer_role_signatures(document)
    assert issuer_signature_scope_violations(document)
    result = verify_receipt(document, AGENT_KEY)
    assert result.failed(CHECK_NO_ISSUER_SIGNED_RANKING)
    assert not result.ok


def test_a_candidate_that_grows_a_ranking_field_beside_its_issuer_signature_is_flagged(signed):
    document = signed.to_dict()
    document["receipt"]["candidate_set"]["candidates"][0]["rank"] = 1
    violations = issuer_signature_scope_violations(document)
    assert violations and "ranking content" in violations[0]
    assert verify_receipt(document, AGENT_KEY).failed(CHECK_NO_ISSUER_SIGNED_RANKING)


def test_the_receipt_states_that_the_ranking_is_not_issuer_endorsed(receipt, signed):
    body = signed.to_dict()
    assert body["receipt"]["ranking"]["issuer_endorsed"] is False
    assert "endorsed by no issuer" in body["receipt"]["valuation_policy"]["issuer_endorsement"]
    assert body["signature"]["role"] == ROLE_AGENT
    assert "no issuer key covers this document" in body["signature"]["covers"]
    assert receipt.ranking.issuer_endorsed is False


# ======================================================================================
# The full candidate set
# ======================================================================================


def test_every_authorised_instrument_appears_including_the_losers(receipt, mandate):
    ids = {c.instrument_id for c in receipt.candidates}
    assert ids == set(mandate.authorized_instrument_ids)
    assert receipt.chosen_instrument_id == AMEX_ID
    # The rival lost and the thin card was worth nothing. Both are still on the receipt.
    assert receipt.candidate(RIVAL_ID).asserted_value_minor == 24_000
    assert receipt.candidate(THIN_ID).asserted_value_minor == 0


def test_the_winner_is_witness_backed_and_the_witness_is_hashed(receipt, evaluation):
    winner = receipt.candidate(AMEX_ID)
    assert winner.witness_status == WITNESS_VERIFIED
    assert winner.status == STATUS_ATTESTED
    assert winner.asserted_value_minor == 102_000
    valuation = evaluation.valuation(AMEX_ID)
    assert winner.witness_hash == witness_content_hash(
        valuation.witness, currency=valuation.currency
    )
    assert winner.realized_minor >= winner.asserted_value_minor


def test_the_asserted_value_is_below_the_naive_sum(evaluation):
    """The demo's first beat, asserted here as a property of the receipt's inputs."""
    valuation = evaluation.valuation(AMEX_ID)
    assert valuation.asserted_minor < valuation.naive_sum_minor
    assert valuation.overstatement_avoided_minor() > 0


def test_candidate_set_digest_moves_when_an_instrument_is_dropped(receipt):
    full = candidate_set_digest(receipt.candidates)
    without_amex = candidate_set_digest(
        [c for c in receipt.candidates if c.instrument_id != AMEX_ID]
    )
    assert full != without_amex


def test_candidate_set_digest_is_order_independent(receipt):
    shuffled = list(receipt.candidates)
    random.Random(1).shuffle(shuffled)
    assert candidate_set_digest(shuffled) == candidate_set_digest(receipt.candidates)


def test_manifest_hashes_are_recorded_per_instrument(receipt, signed_manifests):
    hashes = receipt.manifest_hashes()
    for manifest_id, signed_manifest in signed_manifests.items():
        assert hashes[manifest_id] == signed_manifest.manifest.content_hash()


def test_considered_but_unpriced_is_carried(receipt):
    unpriced = {(u.instrument_id, u.benefit_id) for u in receipt.unpriced}
    assert (AMEX_ID, "amex_concierge") in unpriced
    assert (RIVAL_ID, "rival_lounge") in unpriced
    assert UNPRICED_DISCLOSURE in receipt.disclosures
    assert "does not claim to be the whole worth" in UNPRICED_DISCLOSURE


def test_unpriced_considerations_helper_reads_a_manifest(amex_manifest):
    entries = unpriced_considerations(AMEX_ID, amex_manifest)
    assert [e.benefit_id for e in entries] == ["amex_concierge"]
    assert entries[0].label == "24/7 Concierge and Fine Hotels"


# ======================================================================================
# Omission — the attack the receipt exists to make detectable
# ======================================================================================


def test_dropping_an_instrument_is_caught_by_the_attestation(
    evaluation, session, mandate, signed_manifests
):
    """A platform silently drops Amex. The mandate says it should have been considered."""
    thinned = evaluate(
        cart=Cart.of("m_resy_partner", [DINNER, ESPRESSO]),
        manifests=[signed_manifests[RIVAL_ID], signed_manifests[THIN_ID]],
        now=T0,
        keys=ISSUER_KEYS,
    )
    doctored = build_receipt_from_evaluation(
        receipt_id="rcpt_doctored",
        issued_at=T0,
        evaluation=thinned,
        session=session,
        mandate=mandate,
        agent=AGENT,
        platform=PLATFORM,
        signed_manifests=signed_manifests,
    )
    assert doctored.attestation.outcome == ATTEST_CANDIDATE_SET_INCOMPLETE
    assert not doctored.attestation.faithful
    assert AMEX_ID in doctored.attestation.headline()
    assert doctored.chosen_instrument_id == RIVAL_ID

    result = verify_receipt(sign_receipt(doctored, key=AGENT_KEY), AGENT_KEY)
    assert result.failed(CHECK_RANKING)


def test_a_receipt_naming_only_the_winner_cannot_hide_the_omission(receipt, mandate):
    winner_only = [c for c in receipt.candidates if c.instrument_id == AMEX_ID]
    attestation = attest_ranking(
        candidates=winner_only,
        ranking=None,
        policy=ValuationPolicy(),
        chosen_instrument_id=AMEX_ID,
        cart_total_minor=receipt.session.cart_total_minor,
        mandate=mandate,
    )
    assert attestation.outcome == ATTEST_CANDIDATE_SET_INCOMPLETE
    assert RIVAL_ID in attestation.headline() and THIN_ID in attestation.headline()


def test_the_corpus_counts_consideration_against_authorisation(
    evaluation, session, mandate, signed_manifests
):
    """Omission at corpus scale: every receipt is well formed, one issuer never appears."""
    log = TransparencyLog("plumbline-demo-log", signing_key=LOG_KEY)
    without_amex = evaluate(
        cart=Cart.of("m_resy_partner", [DINNER]),
        manifests=[signed_manifests[RIVAL_ID], signed_manifests[THIN_ID]],
        now=T0,
        keys=ISSUER_KEYS,
    )
    for i in range(4):
        r = build_receipt_from_evaluation(
            receipt_id=f"rcpt_{i}",
            issued_at=T0 + i,
            evaluation=without_amex,
            session=session,
            mandate=mandate,
            agent=AGENT,
            platform=PLATFORM,
            signed_manifests=signed_manifests,
        )
        anchor_receipt(log, sign_receipt(r, key=AGENT_KEY), timestamp=T0 + i, key=LOG_KEY)

    corpus = receipts_from_log(log)
    assert len(corpus) == 4
    considered = instruments_considered(corpus)
    authorized = instruments_authorized(corpus)
    assert authorized[AMEX_ID] == 4
    assert considered.get(AMEX_ID, 0) == 0
    assert considered[RIVAL_ID] == 4


def test_omission_leaves_a_signature_in_the_log(
    evaluation, session, mandate, signed_manifests
):
    """The demo's third beat, end to end: a retroactive edit fails the consistency proof."""
    log = TransparencyLog("plumbline-demo-log", signing_key=LOG_KEY)
    honest = build_receipt_from_evaluation(
        receipt_id="rcpt_honest",
        issued_at=T0,
        evaluation=evaluation,
        session=session,
        mandate=mandate,
        agent=AGENT,
        platform=PLATFORM,
        signed_manifests=signed_manifests,
    )
    anchored = anchor_receipt(log, sign_receipt(honest, key=AGENT_KEY), timestamp=T0, key=LOG_KEY)
    assert anchored.verify_anchor()
    for i in range(1, 4):
        log.append(kind=ENTRY_RECEIPT, body={"receipt_id": f"rcpt_{i}"}, timestamp=T0 + i)
    published = log.signed_tree_head(timestamp=T0 + 10)

    # The platform rebuilds the log with Amex removed from the already-published receipt.
    thinned = evaluate(
        cart=Cart.of("m_resy_partner", [DINNER, ESPRESSO]),
        manifests=[signed_manifests[RIVAL_ID], signed_manifests[THIN_ID]],
        now=T0,
        keys=ISSUER_KEYS,
    )
    edited_receipt = build_receipt_from_evaluation(
        receipt_id="rcpt_honest",
        issued_at=T0,
        evaluation=thinned,
        session=session,
        mandate=mandate,
        agent=AGENT,
        platform=PLATFORM,
        signed_manifests=signed_manifests,
    )
    doctored = TransparencyLog("plumbline-demo-log", signing_key=LOG_KEY)
    anchor_receipt(
        doctored, sign_receipt(edited_receipt, key=AGENT_KEY), timestamp=T0, key=LOG_KEY
    )
    for i in range(1, 5):
        doctored.append(kind=ENTRY_RECEIPT, body={"receipt_id": f"rcpt_{i}"}, timestamp=T0 + i)

    assert not doctored.prove_extends(published).verify()


# ======================================================================================
# Compliance symmetry — a faithful platform earns a positive attestation
# ======================================================================================


def test_a_faithful_platform_gets_a_positive_attestation(receipt):
    assert receipt.attestation.faithful
    assert receipt.attestation.outcome == ATTEST_FAITHFUL
    assert receipt.attestation.checked_against_mandate
    headline = receipt.attestation.headline()
    assert "all 3 instrument(s) the mandate authorised were considered" in headline
    assert AMEX_ID in headline
    assert receipt.attestation.expected_choice == AMEX_ID


def test_the_positive_attestation_survives_independent_verification(signed):
    result = verify_receipt(signed, AGENT_KEY)
    assert result.ok, result.render_text()
    assert not result.failures
    assert {c.name for c in result.checks} >= {
        CHECK_SIGNATURE,
        CHECK_SIGNER_NOT_ISSUER,
        CHECK_CANDIDATE_DIGEST,
        CHECK_NO_ISSUER_SIGNED_RANKING,
        CHECK_RANKING,
    }


def test_choosing_against_the_stated_criterion_is_marked(
    evaluation, session, mandate, signed_manifests
):
    deviant = build_receipt_from_evaluation(
        receipt_id="rcpt_deviant",
        issued_at=T0,
        evaluation=evaluation,
        session=session,
        mandate=mandate,
        agent=AGENT,
        platform=PLATFORM,
        signed_manifests=signed_manifests,
        chosen_instrument_id=RIVAL_ID,
    )
    assert deviant.attestation.outcome == ATTEST_DEVIATED
    assert AMEX_ID in deviant.attestation.headline()
    assert deviant.chosen_instrument_id == RIVAL_ID
    assert verify_receipt(sign_receipt(deviant, key=AGENT_KEY), AGENT_KEY).failed(CHECK_RANKING)


def test_choosing_an_instrument_outside_the_candidate_set_is_marked(
    evaluation, session, mandate, signed_manifests
):
    stray = build_receipt_from_evaluation(
        receipt_id="rcpt_stray",
        issued_at=T0,
        evaluation=evaluation,
        session=session,
        mandate=mandate,
        agent=AGENT,
        platform=PLATFORM,
        signed_manifests=signed_manifests,
        chosen_instrument_id="mf_never_considered",
    )
    assert stray.attestation.outcome == ATTEST_CHOSEN_NOT_AUTHORIZED
    assert ATTEST_CHOSEN_NOT_A_CANDIDATE in stray.attestation.codes()


def test_choosing_an_instrument_the_mandate_never_authorised_is_marked(
    cart, session, signed_manifests
):
    """Omission's mirror: the agent brings its own card and picks it.

    Every instrument the mandate authorised IS in the candidate set, so the omission check
    passes. What fails is that the winner is not one of them.
    """
    house_card = build_manifest(
        manifest_id="mf_agent_house_card",
        issuer="Agent Affiliate Co",
        product="House",
        issued_at=T0 - 86_400,
        benefits=[
            Benefit(
                benefit_id="house_dining_20x",
                kind=KIND_EARN,
                label="20x on dining",
                eligibility=Eligibility(mccs=(MCC_DINING,)),
                rate_bp=2_000,
            )
        ],
    )
    house_key = "prototype-house-issuer-key"
    manifests = dict(signed_manifests)
    manifests["mf_agent_house_card"] = issuer_sign_facts(
        house_card, key=house_key, key_reference="house-prototype-2026"
    )
    ev = evaluate(
        cart=cart,
        manifests=list(manifests.values()),
        now=T0,
        keys={**ISSUER_KEYS, "house-prototype-2026": house_key},
    )
    r = build_receipt_from_evaluation(
        receipt_id="rcpt_house",
        issued_at=T0,
        evaluation=ev,
        session=session,
        # The Card Member never authorised the house card.
        mandate=MandateBinding("mnd_x", (AMEX_ID, RIVAL_ID, THIN_ID)),
        agent=AGENT,
        platform=PLATFORM,
        signed_manifests=manifests,
    )
    assert r.chosen_instrument_id == "mf_agent_house_card"
    assert r.attestation.outcome == ATTEST_CHOSEN_NOT_AUTHORIZED
    assert ATTEST_CANDIDATE_SET_INCOMPLETE not in r.attestation.codes()
    assert verify_receipt(sign_receipt(r, key=AGENT_KEY), AGENT_KEY).failed(CHECK_RANKING)


def test_a_reordered_ranking_is_marked(receipt):
    reversed_entries = tuple(reversed(receipt.ranking.entries))
    tampered = type(receipt.ranking)(
        policy_id=receipt.ranking.policy_id,
        policy_hash=receipt.ranking.policy_hash,
        criterion=receipt.ranking.criterion,
        baseline_minor=receipt.ranking.baseline_minor,
        entries=reversed_entries,
    )
    attestation = attest_ranking(
        candidates=receipt.candidates,
        ranking=tampered,
        policy=receipt.policy,
        chosen_instrument_id=AMEX_ID,
        cart_total_minor=receipt.session.cart_total_minor,
        mandate=receipt.mandate,
    )
    assert ATTEST_ORDER_INCONSISTENT in attestation.codes()


def test_a_ranking_naming_an_unknown_instrument_is_marked(receipt):
    entries = list(receipt.ranking.entries)
    entries[1] = type(entries[1])(
        rank=2,
        manifest_id="mf_phantom",
        asserted_minor=99_999,
        baseline_minor=0,
        incremental_minor=99_999,
        protection_value_minor=0,
        margin_over_next_minor=None,
    )
    tampered = type(receipt.ranking)(
        policy_id=receipt.ranking.policy_id,
        policy_hash=receipt.ranking.policy_hash,
        criterion=receipt.ranking.criterion,
        baseline_minor=receipt.ranking.baseline_minor,
        entries=tuple(entries),
    )
    attestation = attest_ranking(
        candidates=receipt.candidates,
        ranking=tampered,
        policy=receipt.policy,
        chosen_instrument_id=AMEX_ID,
        cart_total_minor=receipt.session.cart_total_minor,
        mandate=receipt.mandate,
    )
    assert attestation.outcome == ATTEST_RANKED_SET_WRONG


def test_a_criterion_that_does_not_match_the_policy_cannot_be_checked(receipt):
    other = type(receipt.ranking)(
        policy_id=receipt.ranking.policy_id,
        policy_hash=receipt.ranking.policy_hash,
        criterion=CRITERION_MAX_PROTECTION_THEN_VALUE,
        baseline_minor=0,
        entries=receipt.ranking.entries,
    )
    attestation = attest_ranking(
        candidates=receipt.candidates,
        ranking=other,
        policy=receipt.policy,
        chosen_instrument_id=AMEX_ID,
        cart_total_minor=receipt.session.cart_total_minor,
        mandate=receipt.mandate,
    )
    assert attestation.outcome == ATTEST_UNKNOWN_CRITERION
    assert attestation.expected_order == ()


def test_an_unverified_winner_is_marked(receipt, mandate):
    candidates = list(receipt.candidates)
    winner = receipt.candidate(AMEX_ID)
    candidates[candidates.index(winner)] = type(winner)(
        **{**winner.__dict__, "witness_status": WITNESS_REFUSED}
    )
    ranking = ranking_from_candidates(
        candidates, policy=ValuationPolicy(), cart_total_minor=receipt.session.cart_total_minor
    )
    attestation = attest_ranking(
        candidates=candidates,
        ranking=ranking,
        policy=ValuationPolicy(),
        chosen_instrument_id=AMEX_ID,
        cart_total_minor=receipt.session.cart_total_minor,
        mandate=mandate,
    )
    assert attestation.outcome == ATTEST_CHOSEN_UNVERIFIED


# ======================================================================================
# Ranking recomputation must agree with the evaluator
# ======================================================================================


@pytest.mark.parametrize(
    "criterion",
    [None, CRITERION_MAX_ASSERTED, CRITERION_MAX_PROTECTION_THEN_VALUE],
)
def test_receipt_side_ranking_agrees_with_the_evaluator(cart, signed_manifests, criterion):
    """Pins `criterion_sort_key` to `evaluate._rank` against a live evaluation.

    The receipt has to be checkable by someone holding only the receipt, so it reproduces
    the ordering rule from the recorded numbers. This test is what stops the two drifting.
    """
    policy = ValuationPolicy() if criterion is None else ValuationPolicy(criterion=criterion)
    ev = evaluate(
        cart=cart,
        manifests=list(signed_manifests.values()),
        now=T0,
        keys=ISSUER_KEYS,
        policy=policy,
    )
    records = [
        r
        for r in (
            build_receipt_from_evaluation(
                receipt_id="rcpt_x",
                issued_at=T0,
                evaluation=ev,
                session=CheckoutSession.of("sess", cart, T0),
                mandate=MandateBinding("mnd", tuple(signed_manifests)),
                agent=AGENT,
                platform=PLATFORM,
                signed_manifests=signed_manifests,
            ).candidates
        )
    ]
    rebuilt = ranking_from_candidates(
        records, policy=policy, cart_total_minor=cart.total()
    )
    assert rebuilt is not None and ev.ranking is not None
    assert [e.manifest_id for e in rebuilt.entries] == [e.manifest_id for e in ev.ranking.entries]
    assert [e.asserted_minor for e in rebuilt.entries] == [
        e.asserted_minor for e in ev.ranking.entries
    ]
    assert rebuilt.baseline_minor == ev.ranking.baseline_minor


def test_ranking_is_independent_of_candidate_input_order(receipt):
    baseline = ranking_from_candidates(
        receipt.candidates,
        policy=ValuationPolicy(),
        cart_total_minor=receipt.session.cart_total_minor,
    )
    rng = random.Random(7)
    for _ in range(10):
        shuffled = list(receipt.candidates)
        rng.shuffle(shuffled)
        again = ranking_from_candidates(
            shuffled,
            policy=ValuationPolicy(),
            cart_total_minor=receipt.session.cart_total_minor,
        )
        assert again.entries == baseline.entries


def test_ranking_respects_the_policy_floor(receipt):
    """An instrument worth nothing is attested and still legitimately unranked."""
    policy = ValuationPolicy(min_value_to_rank_minor=30_000)
    ranking = ranking_from_candidates(
        receipt.candidates, policy=policy, cart_total_minor=receipt.session.cart_total_minor
    )
    assert [e.manifest_id for e in ranking.entries] == [AMEX_ID]


def test_ranking_from_candidates_rejects_an_unknown_criterion(receipt):
    class Rogue:
        criterion = "whatever_the_agent_felt_like"
        policy_id = "p"
        baseline_earn_bp = 0
        min_value_to_rank_minor = 0

        def policy_hash(self):
            return "x"

    with pytest.raises(ReceiptError, match="unknown criterion"):
        ranking_from_candidates(
            receipt.candidates, policy=Rogue(), cart_total_minor=1
        )


# ======================================================================================
# Refusal is a first-class outcome
# ======================================================================================


def test_a_witness_that_double_counts_is_refused_not_quietly_reduced(
    amex_manifest, cart, signed_manifests
):
    """Both dining credits claim the same dinner. The verifier rejects; nothing is asserted."""
    overstating = Witness(
        manifest_id=AMEX_ID,
        cart_hash=cart.hash(),
        assignments=(
            Assignment(
                line_sku="sku_dinner",
                benefit_id="amex_resy_credit",
                consumed_minor=50_000,
                value_minor=50_000,
            ),
            Assignment(
                line_sku="sku_dinner",
                benefit_id="amex_dining_credit",
                consumed_minor=25_000,
                value_minor=25_000,
            ),
        ),
    )
    verification = verify_witness(
        witness=overstating, manifest=amex_manifest, cart=cart, asserted_minor=75_000
    )
    assert not verification.ok

    record = candidate_record(
        instrument_id=AMEX_ID,
        signed_manifest=signed_manifests[AMEX_ID],
        witness=overstating,
        verification=verification,
        unpriced=amex_manifest.unpriced(),
    )
    assert record.status == STATUS_REFUSED
    assert record.witness_status == WITNESS_REFUSED
    assert record.asserted_value_minor is None
    assert "WITNESS_EXCLUSIVITY_VIOLATED" in record.failure_codes
    assert record.witness_hash  # the rejected allocation is still hashed and shown
    assert record.unpriced_benefit_ids == ("amex_concierge",)


def test_a_candidate_with_no_witness_is_recorded_as_considered(signed_manifests):
    record = candidate_record(
        instrument_id=THIN_ID,
        signed_manifest=signed_manifests[THIN_ID],
        witness=None,
        verification=None,
        refusal_codes=("PLUMBLINE_REFUSE_MANIFEST_STALE",),
        note="manifest arrived after the pricing deadline",
    )
    assert record.witness_status == WITNESS_ABSENT
    assert record.status == STATUS_REFUSED
    assert record.asserted_value_minor is None
    assert record.refusal_codes == ("PLUMBLINE_REFUSE_MANIFEST_STALE",)


def test_a_receipt_where_nothing_was_attestable_still_records_the_consideration(
    session, mandate, signed_manifests, amex_manifest, cart
):
    refused = [
        candidate_record(
            instrument_id=instrument_id,
            signed_manifest=signed_manifests[instrument_id],
            witness=None,
            verification=None,
            refusal_codes=("PLUMBLINE_REFUSE_MANIFEST_SIGNATURE_INVALID",),
        )
        for instrument_id in (AMEX_ID, RIVAL_ID, THIN_ID)
    ]
    r = build_receipt(
        receipt_id="rcpt_all_refused",
        issued_at=T0,
        session=session,
        mandate=mandate,
        agent=AGENT,
        platform=PLATFORM,
        candidates=refused,
        policy=ValuationPolicy(),
    )
    assert r.ranking is None
    assert r.chosen_instrument_id is None
    assert len(r.candidates) == 3
    # Refusing everything is correct behaviour, not a violation: the outcome is faithful
    # and the refusal travels alongside it as a note.
    assert r.attestation.outcome == ATTEST_FAITHFUL
    assert ATTEST_NO_RANKING in r.attestation.codes()
    assert "none could be valued from a verified witness" in r.attestation.headline()
    assert any(
        "a number being invented" in f.detail for f in r.attestation.findings
    )
    assert verify_receipt(sign_receipt(r, key=AGENT_KEY), AGENT_KEY).ok


def test_a_missing_ranking_when_something_was_rankable_is_marked(receipt, mandate):
    attestation = attest_ranking(
        candidates=receipt.candidates,
        ranking=None,
        policy=ValuationPolicy(),
        chosen_instrument_id=None,
        cart_total_minor=receipt.session.cart_total_minor,
        mandate=mandate,
    )
    assert attestation.outcome == ATTEST_RANKED_SET_WRONG


# ======================================================================================
# Verification and tampering
# ======================================================================================


def test_verification_passes_with_every_optional_cross_check(
    signed, cart, signed_manifests, evaluation
):
    witnesses = {
        v.manifest_id: v.witness for v in evaluation.candidates if v.witness is not None
    }
    result = verify_receipt(
        signed,
        AGENT_KEY,
        cart=cart,
        manifests=signed_manifests,
        issuer_keys=ISSUER_KEYS,
        witnesses=witnesses,
    )
    assert result.ok, result.render_text()
    names = {c.name for c in result.checks}
    assert {
        CHECK_CART_HASH,
        CHECK_MANIFEST_SIGNATURES,
        CHECK_WITNESS_HASHES,
        CHECK_EVALUATION_AGREES,
    } <= names


def test_verification_accepts_plain_json(signed, cart):
    document = copy.deepcopy(signed.to_dict())
    assert verify_receipt(document, AGENT_KEY, cart=cart).ok


def test_an_altered_body_breaks_the_signature(signed):
    document = copy.deepcopy(signed.to_dict())
    document["receipt"]["selection"]["instrument_id"] = RIVAL_ID
    result = verify_receipt(document, AGENT_KEY)
    assert result.failed(CHECK_SIGNATURE)
    assert not result.ok


def test_removing_a_candidate_after_signing_breaks_the_digest(signed):
    document = copy.deepcopy(signed.to_dict())
    candidates = document["receipt"]["candidate_set"]["candidates"]
    document["receipt"]["candidate_set"]["candidates"] = [
        c for c in candidates if c["instrument_id"] != AMEX_ID
    ]
    result = verify_receipt(document, AGENT_KEY)
    assert result.failed(CHECK_CANDIDATE_DIGEST)
    assert result.failed(CHECK_SIGNATURE)


def test_a_receipt_for_a_different_cart_is_rejected(signed):
    other = Cart.of("m_resy_partner", [DINNER])
    assert verify_receipt(signed, AGENT_KEY, cart=other).failed(CHECK_CART_HASH)


def test_a_substituted_manifest_is_rejected(signed, signed_manifests, amex_manifest):
    tweaked = build_manifest(
        manifest_id=AMEX_ID,
        issuer="American Express",
        product="Platinum",
        issued_at=T0 - 86_400,
        benefits=[b for b in amex_manifest.benefits if b.benefit_id != "amex_dining_5x"],
    )
    manifests = dict(signed_manifests)
    manifests[AMEX_ID] = issuer_sign_facts(tweaked, key=AMEX_KEY, key_reference=AMEX_KEY_ID)
    result = verify_receipt(
        signed, AGENT_KEY, manifests=manifests, issuer_keys=ISSUER_KEYS
    )
    assert result.failed(CHECK_MANIFEST_SIGNATURES)


def test_a_manifest_whose_issuer_key_is_unknown_is_reported_not_assumed(
    signed, signed_manifests
):
    result = verify_receipt(signed, AGENT_KEY, manifests=signed_manifests, issuer_keys={})
    assert result.failed(CHECK_MANIFEST_SIGNATURES)
    detail = next(c.detail for c in result.checks if c.name == CHECK_MANIFEST_SIGNATURES)
    assert "no key supplied" in detail


def test_a_manifest_signed_by_the_wrong_issuer_is_rejected(signed, signed_manifests):
    keys = dict(ISSUER_KEYS)
    keys[AMEX_KEY_ID] = "an-entirely-different-key"
    result = verify_receipt(signed, AGENT_KEY, manifests=signed_manifests, issuer_keys=keys)
    assert result.failed(CHECK_MANIFEST_SIGNATURES)
    detail = next(c.detail for c in result.checks if c.name == CHECK_MANIFEST_SIGNATURES)
    assert "does not verify" in detail


def test_a_substituted_witness_is_rejected(signed, evaluation, cart):
    fake = Witness(manifest_id=AMEX_ID, cart_hash=cart.hash(), assignments=())
    result = verify_receipt(signed, AGENT_KEY, witnesses={AMEX_ID: fake})
    assert result.failed(CHECK_WITNESS_HASHES)


def test_an_embedded_evaluation_that_disagrees_is_rejected(signed):
    document = copy.deepcopy(signed.to_dict())
    for entry in document["receipt"]["evaluation"]["candidates"]:
        if entry["manifest_id"] == AMEX_ID:
            entry["asserted_minor"] = 999_999
    result = verify_receipt(document, AGENT_KEY)
    assert result.failed(CHECK_EVALUATION_AGREES)


def test_a_malformed_document_fails_structurally():
    assert not verify_receipt({"nonsense": True}, AGENT_KEY).ok
    assert not verify_receipt({"receipt": {"candidate_set": {}}}, AGENT_KEY).ok


def test_an_unusable_policy_is_reported_rather_than_raised(signed):
    document = copy.deepcopy(signed.to_dict())
    document["receipt"]["valuation_policy"]["criterion"] = "made_up_criterion"
    result = verify_receipt(document, AGENT_KEY)
    assert not result.ok
    assert result.failed(CHECK_RANKING)


# ======================================================================================
# Determinism, serialisation, and the corpus contract
# ======================================================================================


def test_the_receipt_replays_byte_for_byte(
    evaluation, session, mandate, signed_manifests, cart
):
    """No wall clock, no measured latency, no dict iteration order in the signed bytes."""
    def build():
        ev = evaluate(
            cart=cart,
            manifests=list(signed_manifests.values()),
            now=T0,
            keys=ISSUER_KEYS,
            policy=ValuationPolicy(),
        )
        return build_receipt_from_evaluation(
            receipt_id="rcpt_replay",
            issued_at=T0,
            evaluation=ev,
            session=session,
            mandate=mandate,
            agent=AGENT,
            platform=PLATFORM,
            signed_manifests=signed_manifests,
        )

    first, second = build(), build()
    assert first.body() == second.body()
    assert first.receipt_hash() == second.receipt_hash()
    assert (
        sign_receipt(first, key=AGENT_KEY).signature
        == sign_receipt(second, key=AGENT_KEY).signature
    )


def test_stable_evaluation_body_drops_the_only_field_that_does_not_replay(evaluation):
    body = stable_evaluation_body(evaluation)
    assert all("elapsed_ms" not in c for c in body["candidates"])
    assert [c["manifest_id"] for c in body["candidates"]] == sorted(
        c["manifest_id"] for c in body["candidates"]
    )
    assert "elapsed_ms" in evaluation.to_dict()["candidates"][0]


def test_receipt_round_trips_through_json(signed):
    restored = SignedReceipt.from_dict(signed.to_dict())
    assert restored.receipt.body() == signed.receipt.body()
    assert restored.signature == signed.signature
    assert restored.signer_role == signed.signer_role
    assert restored.receipt.chosen_instrument_id == AMEX_ID
    assert verify_receipt(restored, AGENT_KEY).ok


def test_decision_receipt_from_dict_reports_a_missing_field(signed):
    document = copy.deepcopy(signed.to_dict()["receipt"])
    del document["mandate"]
    with pytest.raises(ReceiptError, match="missing required field"):
        DecisionReceipt.from_dict(document)


def test_policy_and_ranking_deserialise_from_the_receipt(signed):
    body = signed.to_dict()["receipt"]
    policy = policy_from_dict(body["valuation_policy"])
    assert policy.policy_hash() == body["valuation_policy"]["policy_hash"]
    ranking = ranking_from_dict(body["ranking"])
    assert ranking.chosen_manifest_id == AMEX_ID
    assert ranking.issuer_endorsed is False


def test_the_attribution_module_can_read_the_receipt(signed):
    """Cross-module contract: the corpus reader consumes this envelope as published."""
    observation = observe_receipt(signed.to_dict()["receipt"])
    assert observation.decision_id == "rcpt_demo_01"
    assert observation.chosen_manifest_id == AMEX_ID
    assert observation.chosen_attested
    assert observation.attested_candidates == 3
    assert observation.benefits


def test_render_text_shows_the_full_candidate_set_and_the_criterion(receipt):
    text = receipt.render_text()
    for instrument_id in (AMEX_ID, RIVAL_ID, THIN_ID):
        assert instrument_id in text
    assert "the full set and not the winner" in text
    assert "CONSIDERED BUT UNPRICED" in text
    assert "endorsed by no issuer" in text
    assert receipt.policy.criterion in text


def test_ordered_instrument_ids_covers_everything_considered(receipt):
    order = receipt.ordered_instrument_ids()
    assert set(order) == {c.instrument_id for c in receipt.candidates}
    assert order[0] == AMEX_ID
    assert len(order) == len(set(order))


def test_session_hash_binds_the_cart(cart):
    session = CheckoutSession.of("sess_x", cart, T0)
    other = CheckoutSession.of("sess_x", Cart.of("m_resy_partner", [DINNER]), T0)
    assert session.session_hash() != other.session_hash()
    assert CheckoutSession.from_dict(session.to_dict()) == session


def test_key_id_is_stable_and_does_not_leak_the_key():
    assert key_id(AGENT_KEY) == key_id(AGENT_KEY)
    assert key_id(AGENT_KEY) != key_id(AMEX_KEY)
    assert AGENT_KEY not in key_id(AGENT_KEY)


# ======================================================================================
# build_receipt input validation
# ======================================================================================


def test_build_receipt_refuses_an_empty_candidate_set(session, mandate):
    with pytest.raises(ReceiptError, match="records nothing"):
        build_receipt(
            receipt_id="r",
            issued_at=T0,
            session=session,
            mandate=mandate,
            agent=AGENT,
            platform=PLATFORM,
            candidates=[],
            policy=ValuationPolicy(),
        )


def test_build_receipt_refuses_duplicate_candidates(receipt, session, mandate):
    duplicated = [receipt.candidates[0], receipt.candidates[0]]
    with pytest.raises(ReceiptError, match="duplicate candidate"):
        build_receipt(
            receipt_id="r",
            issued_at=T0,
            session=session,
            mandate=mandate,
            agent=AGENT,
            platform=PLATFORM,
            candidates=duplicated,
            policy=ValuationPolicy(),
        )


def test_build_receipt_refuses_an_unknown_posture(receipt, session, mandate):
    with pytest.raises(ReceiptError, match="unknown posture"):
        build_receipt(
            receipt_id="r",
            issued_at=T0,
            session=session,
            mandate=mandate,
            agent=AGENT,
            platform=PLATFORM,
            candidates=list(receipt.candidates),
            policy=ValuationPolicy(),
            posture="whatever",
        )


def test_build_receipt_refuses_an_unknown_witness_status(receipt, session, mandate):
    bad = type(receipt.candidates[0])(
        **{**receipt.candidates[0].__dict__, "witness_status": "PROBABLY_FINE"}
    )
    with pytest.raises(ReceiptError, match="unknown witness status"):
        build_receipt(
            receipt_id="r",
            issued_at=T0,
            session=session,
            mandate=mandate,
            agent=AGENT,
            platform=PLATFORM,
            candidates=[bad],
            policy=ValuationPolicy(),
        )


def test_build_receipt_refuses_an_unknown_status(receipt, session, mandate):
    bad = type(receipt.candidates[0])(
        **{**receipt.candidates[0].__dict__, "status": "MOSTLY_ATTESTED"}
    )
    with pytest.raises(ReceiptError, match="unknown status"):
        build_receipt(
            receipt_id="r",
            issued_at=T0,
            session=session,
            mandate=mandate,
            agent=AGENT,
            platform=PLATFORM,
            candidates=[bad],
            policy=ValuationPolicy(),
        )


# ======================================================================================
# Graceful degrade — observe-only is the default, enforcement is elected
# ======================================================================================


def test_no_counterpart_receipt_proceeds_by_default(session, mandate):
    assessment = assess_counterpart(
        receipt=None,
        posture=POSTURE_OBSERVE_ONLY,
        session=session,
        mandate=mandate,
        assessed_at=T0,
    )
    assert assessment.proceeds is True
    assert assessment.reason_code == REASON_UNATTESTED_SELECTION
    assert assessment.coverage_eligible is False
    assert "coverage is conditioned on evidence; authorization is not" in assessment.detail
    assert assessment.record["cart_hash"] == session.cart_hash


def test_the_cardholder_may_elect_enforcement(session, mandate):
    assessment = assess_counterpart(
        receipt=None,
        posture=POSTURE_ENFORCE,
        session=session,
        mandate=mandate,
        assessed_at=T0,
    )
    assert assessment.proceeds is False
    assert assessment.reason_code == REASON_DISCLOSURE_CAVEAT_UNDISCHARGED
    # The failure is the agent's own delegated authority, not an issuer refusing a platform.
    assert "its own delegated authority requires" in assessment.detail
    assert "the Card Member elected enforcement" in assessment.detail
    assert mandate.mandate_id in assessment.detail
    assert "decline" not in assessment.detail
    assert "platform" not in assessment.detail


def test_a_present_receipt_discharges_the_caveat_and_earns_coverage(session, mandate, signed):
    for posture in (POSTURE_OBSERVE_ONLY, POSTURE_ENFORCE):
        assessment = assess_counterpart(
            receipt=signed,
            posture=posture,
            session=session,
            mandate=mandate,
            assessed_at=T0,
        )
        assert assessment.proceeds is True
        assert assessment.reason_code == REASON_SELECTION_ATTESTED
        assert assessment.coverage_eligible is True
        assert assessment.record["receipt_hash"] == signed.receipt.receipt_hash()


def test_assess_counterpart_rejects_an_unknown_posture(session, mandate):
    with pytest.raises(ReceiptError, match="unknown posture"):
        assess_counterpart(
            receipt=None, posture="advisory", session=session, mandate=mandate, assessed_at=T0
        )


def test_the_gap_lands_in_the_log_under_both_postures(session, mandate):
    log = TransparencyLog("plumbline-demo-log", signing_key=LOG_KEY)
    for posture in (POSTURE_OBSERVE_ONLY, POSTURE_ENFORCE):
        assessment = assess_counterpart(
            receipt=None, posture=posture, session=session, mandate=mandate, assessed_at=T0
        )
        seq = record_unattested_selection(log, assessment, timestamp=T0)
        assert log.get(seq).kind == ENTRY_UNATTESTED_SELECTION
    assert len(log.filter(ENTRY_UNATTESTED_SELECTION)) == 2
    assert log.inclusion_proof(0).verify()


def test_an_attested_selection_is_not_recordable_as_a_gap(session, mandate, signed):
    log = TransparencyLog("plumbline-demo-log")
    assessment = assess_counterpart(
        receipt=signed,
        posture=POSTURE_OBSERVE_ONLY,
        session=session,
        mandate=mandate,
        assessed_at=T0,
    )
    with pytest.raises(ReceiptError, match="anchor the receipt"):
        record_unattested_selection(log, assessment, timestamp=T0)


def test_the_posture_is_recorded_on_the_receipt(
    evaluation, session, mandate, signed_manifests
):
    for posture in (POSTURE_OBSERVE_ONLY, POSTURE_ENFORCE):
        r = build_receipt_from_evaluation(
            receipt_id="rcpt_posture",
            issued_at=T0,
            evaluation=evaluation,
            session=session,
            mandate=mandate,
            agent=AGENT,
            platform=PLATFORM,
            signed_manifests=signed_manifests,
            posture=posture,
        )
        assert r.body()["posture"] == posture


# ======================================================================================
# Anchoring
# ======================================================================================


def test_anchoring_binds_the_receipt_to_a_published_head(signed):
    log = TransparencyLog("plumbline-demo-log", signing_key=LOG_KEY)
    anchored = anchor_receipt(log, signed, timestamp=T0, key=LOG_KEY)
    assert anchored.seq == 0
    assert anchored.verify_anchor()
    assert anchored.inclusion_proof.verify()
    assert anchored.sth.tree_size == 1
    assert anchored.to_dict()["log"]["seq"] == 0
    assert log.find_leaf(signed.leaf_hash(T0)) == 0


def test_candidate_from_valuation_without_a_manifest_says_what_is_missing(evaluation):
    """A receipt built without manifests must not put display labels in an id field."""
    record = candidate_from_valuation(evaluation.valuation(AMEX_ID))
    assert record.unpriced_benefit_ids == ()
    assert record.issuer_signature == ""
    assert "ids are absent because no manifest was supplied" in record.note


def test_a_receipt_built_without_manifests_still_carries_the_unpriced_labels(
    evaluation, session, mandate
):
    bare = build_receipt_from_evaluation(
        receipt_id="rcpt_bare",
        issued_at=T0,
        evaluation=evaluation,
        session=session,
        mandate=mandate,
        agent=AGENT,
        platform=PLATFORM,
    )
    labels = {(u.instrument_id, u.label) for u in bare.unpriced}
    assert (AMEX_ID, "24/7 Concierge and Fine Hotels") in labels
    assert all(u.benefit_id == "" for u in bare.unpriced)
    assert all("benefit id unavailable" in u.note for u in bare.unpriced)
    assert bare.attestation.faithful


def test_instrument_ids_may_differ_from_manifest_ids(
    evaluation, session, mandate, signed_manifests
):
    """Two cards on one product: distinct instrument ids, one manifest content hash."""
    aliases = {AMEX_ID: "card_amex_1234", RIVAL_ID: "card_rival_9876", THIN_ID: "card_thin_0001"}
    aliased_mandate = MandateBinding(
        mandate_id="mnd_alias", authorized_instrument_ids=tuple(aliases.values())
    )
    r = build_receipt_from_evaluation(
        receipt_id="rcpt_alias",
        issued_at=T0,
        evaluation=evaluation,
        session=session,
        mandate=aliased_mandate,
        agent=AGENT,
        platform=PLATFORM,
        signed_manifests=signed_manifests,
        instrument_ids=aliases,
    )
    assert r.chosen_instrument_id == "card_amex_1234"
    assert r.candidate("card_amex_1234").manifest_id == AMEX_ID
    assert r.attestation.faithful
    assert verify_receipt(sign_receipt(r, key=AGENT_KEY), AGENT_KEY).ok


def test_neither_receipt_nor_transparency_loads_a_solver():
    """"Verification needs no solver" has to hold for the import graph as well."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    backend = str(Path(__file__).resolve().parents[1])
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, plumbline.receipt, plumbline.transparency;"
            "loaded=[m for m in sys.modules if m=='z3' or m.startswith('z3.')];"
            "print(loaded); sys.exit(1 if loaded else 0)",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": backend},
        timeout=120,
    )
    assert proc.returncode == 0, f"a solver reached the receipt path: {proc.stdout}{proc.stderr}"


def test_many_receipts_anchor_and_each_proof_still_verifies(
    evaluation, session, mandate, signed_manifests
):
    log = TransparencyLog("plumbline-demo-log", signing_key=LOG_KEY)
    anchors = []
    for i in range(12):
        r = build_receipt_from_evaluation(
            receipt_id=f"rcpt_{i}",
            issued_at=T0 + i,
            evaluation=evaluation,
            session=session,
            mandate=mandate,
            agent=AGENT,
            platform=PLATFORM,
            signed_manifests=signed_manifests,
        )
        anchors.append(anchor_receipt(log, sign_receipt(r, key=AGENT_KEY), timestamp=T0 + i, key=LOG_KEY))
    for anchored in anchors:
        assert anchored.verify_anchor()
    final = log.signed_tree_head(timestamp=T0 + 100)
    for anchored in anchors:
        assert log.inclusion_proof(anchored.seq).verify_against(final)
    assert len(receipts_from_log(log)) == 12


# ======================================================================================
# Serialisation of every carried type — a receipt travels as JSON or it travels not at all
# ======================================================================================


def test_unpriced_consideration_round_trips(amex_manifest):
    entry = unpriced_considerations(AMEX_ID, amex_manifest)[0]
    assert UnpricedConsideration.from_dict(entry.to_dict()) == entry


def test_candidate_record_round_trips(receipt):
    for record in receipt.candidates:
        assert CandidateRecord.from_dict(record.to_dict(currency=CURRENCY)) == record


def test_ranking_attestation_round_trips(receipt):
    assert RankingAttestation.from_dict(receipt.attestation.to_dict()) == receipt.attestation
    assert receipt.attestation.to_dict()["faithful"] is True


def test_attestation_finding_carries_a_code_and_a_reason(receipt):
    finding = receipt.attestation.findings[0]
    assert isinstance(finding, AttestationFinding)
    assert finding.to_dict()["code"] == ATTEST_FAITHFUL
    assert finding.to_dict()["detail"]


def test_counterpart_assessment_serialises(session, mandate):
    assessment = assess_counterpart(
        receipt=None,
        posture=POSTURE_OBSERVE_ONLY,
        session=session,
        mandate=mandate,
        assessed_at=T0,
    )
    assert isinstance(assessment, CounterpartAssessment)
    d = assessment.to_dict()
    assert d["proceeds"] is True
    assert d["reason_code"] == REASON_UNATTESTED_SELECTION
    assert d["record"]["mandate_id"] == mandate.mandate_id


def test_anchored_receipt_serialises(signed):
    log = TransparencyLog("plumbline-demo-log", signing_key=LOG_KEY)
    anchored = anchor_receipt(log, signed, timestamp=T0, key=LOG_KEY)
    assert isinstance(anchored, AnchoredReceipt)
    d = anchored.to_dict()
    assert d["log"]["sth"]["tree_size"] == 1
    assert d["log"]["inclusion_proof"]["leaf_index"] == 0
    assert d["receipt"]["signature"]["role"] == ROLE_AGENT


def test_receipt_verification_renders_and_serialises(signed):
    result = verify_receipt(signed, AGENT_KEY)
    assert isinstance(result, ReceiptVerification)
    assert result.to_dict()["failures"] == []
    assert result.render_text().startswith("VERIFIED")

    bad = verify_receipt(signed, "the-wrong-key")
    assert bad.to_dict()["failures"] == [CHECK_SIGNATURE]
    assert bad.render_text().startswith("VERIFICATION FAILED")
    assert "[FAIL]" in bad.render_text()


def test_identity_and_mandate_round_trip(mandate):
    assert Identity.from_dict(AGENT.to_dict()) == AGENT
    assert MandateBinding.from_dict(mandate.to_dict()) == mandate


def test_default_disclosures_state_the_limits_without_overclaiming():
    joined = " ".join(DEFAULT_DISCLOSURES)
    assert "not optimal" in joined
    assert "prototype keys" in joined
    assert "No live offers feed is claimed" in joined
    for forbidden in ("optimal allocation", "guaranteed", "provably optimal"):
        assert forbidden not in joined


def test_the_manifest_version_marker_is_what_gates_issuer_signing(amex_manifest):
    body = amex_manifest.body()
    assert body["version"] == MANIFEST_VERSION
    body["version"] = "plumbline/receipt/1"
    with pytest.raises(IssuerSigningBoundaryError, match="whose version is"):
        issuer_sign_facts(body, key=AMEX_KEY)


def test_witness_and_status_constants_are_distinct():
    assert len({WITNESS_VERIFIED, WITNESS_REFUSED, WITNESS_ABSENT}) == 3
    assert STATUS_ATTESTED != STATUS_REFUSED
    assert CRITERION_MAX_ASSERTED != CRITERION_MAX_PROTECTION_THEN_VALUE
