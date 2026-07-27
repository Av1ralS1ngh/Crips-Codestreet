"""The console's valuation envelope: one builder, two transports, and no drift between them.

Two defects motivated this module, both found by a reader who ran the code rather than read
the slides.

The first: `src/mock/plumblineFixtures.json` carried four instruments, two of them attributed
to `American Express` with real product names and terms nobody published — a "Taj Epicure
dining credit" worth a rupee amount for a benefit that is a membership, a Membership Rewards
rate that is not the published one, and no annual fee at all. `ReceiptView.tsx` renders
`{issuer} {product}`, so a real issuer's name went on a projector beside numbers its own
people would not recognise. The tests below pin the manifests the console renders to
`products.py`, byte for byte, and require anything not from there to say it is invented.

The second: `liveClient.ts` requested `GET /api/plumbline/state` and no such route existed, so
five of the six screens 404'd in live mode and rendered the fabricated fixtures in mock mode.
The route now exists and calls `plumbline.console.build_state`, the same function the fixture
generator writes to disk. `test_the_mock_fixture_matches_the_live_route` diffs the two, which
is the only check that actually keeps them honest — two producers of one envelope is how a
console ends up showing a card the backend has never heard of.

`valuation.latency` is excluded from that diff, and only that. It is a measurement of the
host that produced it and is the single field in the envelope that is not a pure function of
a fixed clock. Everything else must match to the byte.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from caveat.api import app
from caveat.cart import Cart
from plumbline import console as C
from plumbline import products as P
from plumbline.manifest import Manifest, verify_manifest
from plumbline.witness import Witness, verify_witness

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "frontend" / "src" / "mock" / "plumblineFixtures.json"

# Every key `frontend/src/lib/plumbline.ts :: PlumblineState` declares. The console reads all of
# them; a route that dropped one would blank a screen rather than error.
ENVELOPE_KEYS = {
    "generated_at",
    "disclosure",
    "cart",
    "cart_hash",
    "instruments",
    "valuation",
    "receipt",
    "refusals",
    "omission",
    "attribution",
}


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def live(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/plumbline/state")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(scope="module")
def fixture() -> dict[str, Any]:
    if not FIXTURE.is_file():
        pytest.fail(
            f"the console's mock corpus is missing: expected {FIXTURE}. Regenerate it with "
            f"`PYTHONPATH=backend .venv/bin/python frontend/scripts/gen_plumbline_fixtures.py`."
        )
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def without_latency(state: dict[str, Any]) -> dict[str, Any]:
    """Everything in the envelope that must replay. Latency is a measurement, not a fact."""
    trimmed = json.loads(json.dumps(state))
    trimmed["valuation"].pop("latency", None)
    return trimmed


# --------------------------------------------------------------------------------------
# The route exists, and returns the envelope the console's type declares
# --------------------------------------------------------------------------------------


def test_the_route_exists_and_returns_the_declared_envelope(live: dict[str, Any]) -> None:
    assert set(live) == ENVELOPE_KEYS, (
        f"the envelope does not match PlumblineState; missing "
        f"{sorted(ENVELOPE_KEYS - set(live))}, unexpected {sorted(set(live) - ENVELOPE_KEYS)}"
    )
    assert live["generated_at"] == C.CONSOLE_CLOCK
    assert live["cart"]["currency"] == P.INR
    assert live["cart_hash"] == Cart.from_dict(live["cart"]).hash()
    assert live["instruments"], "a candidate set of nothing ranks nothing"
    assert live["valuation"]["instruments"], "no instrument was valued"
    assert live["refusals"], "refusal is a first-class output and the console shows it"
    assert live["omission"]["vectors"], "the browser verifier is handed no proof vectors"
    assert live["attribution"]["benefits"], "the 2x2 has nothing to place"


def test_the_route_is_cached_and_refreshable(client: TestClient) -> None:
    """Two GETs return identical bytes, including latency, so a figure on screen holds still."""
    first = client.get("/api/plumbline/state").json()
    second = client.get("/api/plumbline/state").json()
    assert first == second

    refreshed = client.get("/api/plumbline/state", params={"refresh": "true"}).json()
    assert without_latency(refreshed) == without_latency(first)
    assert set(refreshed["valuation"]["latency"]) == set(first["valuation"]["latency"])


def test_the_latency_block_carries_its_conditions(live: dict[str, Any]) -> None:
    """A bare latency number is exactly the thing this project argues against."""
    latency = live["valuation"]["latency"]
    for row in ("demo", "demo_verify", "bench", "bench_verify"):
        sample = latency[row]
        assert sample["problem_size"] and sample["method"]
        assert sample["runs"] > 0
        assert sample["p99_ms"] >= sample["p50_ms"] >= sample["min_ms"]
    assert latency["solver"]["measured_here"] is False
    assert latency["solver"]["source"]


# --------------------------------------------------------------------------------------
# Mock and live agree by construction
# --------------------------------------------------------------------------------------


def test_the_mock_fixture_matches_the_live_route(
    fixture: dict[str, Any], live: dict[str, Any]
) -> None:
    """The whole point of the shared builder, asserted rather than asserted-in-prose.

    In MOCK mode the console renders `plumblineFixtures.json`; in LIVE mode it renders this
    route. If they can disagree, the demo a judge sees is not the system the tests cover.
    """
    mock_side = without_latency(fixture)
    live_side = without_latency(live)
    if mock_side != live_side:
        differing = sorted(k for k in ENVELOPE_KEYS if mock_side.get(k) != live_side.get(k))
        pytest.fail(
            f"the checked-in console fixture and GET /api/plumbline/state disagree on "
            f"{differing}. Regenerate the fixture: "
            f"`PYTHONPATH=backend .venv/bin/python frontend/scripts/gen_plumbline_fixtures.py`."
        )


def test_the_builder_is_deterministic_apart_from_latency() -> None:
    a = C.build_state(reps=(20, 5))
    b = C.build_state(reps=(20, 5))
    assert without_latency(a) == without_latency(b)


def test_the_fixture_is_not_stale() -> None:
    """The checked-in bytes are what the builder produces today, not what it produced once."""
    if not FIXTURE.is_file():
        pytest.skip("no checked-in fixture")
    recorded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert without_latency(recorded) == without_latency(C.build_state(reps=(20, 5)))


# --------------------------------------------------------------------------------------
# Provenance: no real product is quoted with terms that are not its own
# --------------------------------------------------------------------------------------


def catalogue_bodies() -> dict[str, dict[str, Any]]:
    return {m.manifest_id: m.body() for m in P.catalogue(C.CONSOLE_CLOCK)}


def test_every_real_product_on_screen_is_byte_identical_to_products_py(
    live: dict[str, Any],
) -> None:
    """A manifest bearing a real issuer's name must be the one `products.py` built.

    Not "similar to" and not "derived from": the same bytes. Anything else is a card term
    with no provenance rendered under a name whose owner is in the room.
    """
    catalogue = catalogue_bodies()
    real = [i for i in live["instruments"] if i["instrument_id"] in catalogue]
    assert real, "the console renders no real product at all"
    for record in real:
        assert record["manifest"] == catalogue[record["instrument_id"]], (
            f"{record['instrument_id']} on the console differs from the manifest "
            f"products.py builds for it"
        )


def test_no_manifest_claims_published_terms_unless_products_py_sourced_it(
    live: dict[str, Any],
) -> None:
    """Provenance is a claim, and an invented instrument may not make it.

    The fixture this replaces asserted "publicly published card terms, modelled" for all four
    of its instruments, two of which were invented outright. A source line is the only thing
    on screen telling a reader which numbers came from an issuer, so it has to be true.
    """
    catalogue = catalogue_bodies()
    for record in live["instruments"]:
        source = record["source"]
        assert source, f"{record['instrument_id']} carries no provenance line at all"
        if record["instrument_id"] in catalogue:
            assert "publicly published card terms" in source
            assert source == catalogue[record["instrument_id"]]["source"]
        else:
            assert "publicly published card terms" not in source, (
                f"{record['instrument_id']} is not in products.py but claims published-terms "
                f"provenance"
            )
            assert "HYPOTHETICAL" in source and "invented" in source
            assert record["issuer_signed"] is False


def test_an_invented_instrument_never_borrows_a_real_issuer_name(
    live: dict[str, Any],
) -> None:
    catalogue = catalogue_bodies()
    real_issuers = {m.issuer for m in P.catalogue(C.CONSOLE_CLOCK)}
    for record in live["instruments"]:
        if record["instrument_id"] in catalogue:
            continue
        assert record["issuer"] not in real_issuers, (
            f"{record['instrument_id']} is invented and carries the real issuer name "
            f"{record['issuer']!r}"
        )
        assert record["signature"] is None if "signature" in record else True


def test_the_disclosure_names_the_invented_instrument(live: dict[str, Any]) -> None:
    """The envelope-level disclosure is what the console prints under every screen."""
    disclosure = live["disclosure"]
    assert "publicly published terms" in disclosure
    assert "no live Offers feed" in disclosure
    assert "synthetic member state" in disclosure
    invented = [i for i in live["instruments"] if i["instrument_id"] not in catalogue_bodies()]
    for record in invented:
        assert record["product"] in disclosure, (
            f"{record['product']} is invented and the envelope disclosure does not say so"
        )


def test_only_the_issuer_signed_instruments_carry_a_signature(live: dict[str, Any]) -> None:
    """The signing boundary. An issuer signs its own facts and nothing else."""
    by_id = {i["instrument_id"]: i for i in live["instruments"]}
    for candidate in live["valuation"]["instruments"]:
        record = by_id[candidate["instrument_id"]]
        assert candidate["issuer_signed"] == record["issuer_signed"]
        if candidate["issuer_signed"]:
            assert candidate["signature"] and candidate["key_id"]
            manifest = Manifest.from_dict(record["manifest"])
            assert verify_manifest(
                {"body": manifest.body(), "signature": candidate["signature"]}, C.ISSUER_KEY
            )
        else:
            assert candidate["signature"] is None and candidate["key_id"] is None


def test_no_issuer_signature_covers_the_ranking(live: dict[str, Any]) -> None:
    """The corpus contains no issuer-signed assertion that a competitor won."""
    assert live["receipt"]["policy"]["endorsed_by_issuer"] is False
    assert live["receipt"]["policy"]["owner"] == "cardholder"
    assert live["receipt"]["policy"]["policy_hash"]


# --------------------------------------------------------------------------------------
# The arithmetic on screen is the arithmetic the kernel signed
# --------------------------------------------------------------------------------------


def test_every_witness_on_screen_verifies(live: dict[str, Any]) -> None:
    by_id = {i["instrument_id"]: i for i in live["instruments"]}
    cart = Cart.from_dict(live["cart"])
    for candidate in live["valuation"]["instruments"]:
        manifest = Manifest.from_dict(by_id[candidate["instrument_id"]]["manifest"])
        witness = Witness.from_dict(candidate["witness"])
        result = verify_witness(
            witness=witness,
            manifest=manifest,
            cart=cart,
            asserted_minor=candidate["asserted_minor"],
        )
        assert result.ok, (candidate["instrument_id"], [f.to_dict() for f in result.failures])
        assert result.supports_assertion
        assert candidate["verification"]["ok"] is True


def test_every_reconciliation_closes_and_never_understates(live: dict[str, Any]) -> None:
    """naive >= capped >= witness, and both steps account for every unit of the difference."""
    for candidate in live["valuation"]["instruments"]:
        r = candidate["reconciliation"]
        assert r["naive_minor"] >= r["capped_minor"] >= r["witness_minor"]
        assert r["witness_minor"] == candidate["asserted_minor"]
        by_cause = {row["cause"]: row["minor"] for row in r["by_cause"]}
        assert r["naive_minor"] - r["capped_minor"] == by_cause[C.CAUSE_BALANCE]
        assert r["capped_minor"] - r["witness_minor"] == (
            by_cause[C.CAUSE_EXCLUSIVITY] + by_cause[C.CAUSE_EXHAUSTED]
        )


def test_the_headline_instrument_actually_shows_an_overstatement(live: dict[str, Any]) -> None:
    """Demo beat one has to have something to demonstrate."""
    headline = next(
        c
        for c in live["valuation"]["instruments"]
        if c["instrument_id"] == live["valuation"]["headline_instrument"]
    )
    assert headline["issuer_signed"], "the headline of the valuation screen must be signed"
    assert headline["reconciliation"]["overstatement_minor"] > 0
    assert headline["collisions"], "no exclusivity collision is exhibited on the headline card"


def test_all_three_overstatement_causes_are_exhibited(live: dict[str, Any]) -> None:
    """The cart is chosen so a judge can see each cause at least once across the set."""
    seen: dict[str, int] = {}
    for candidate in live["valuation"]["instruments"]:
        for row in candidate["reconciliation"]["by_cause"]:
            seen[row["cause"]] = seen.get(row["cause"], 0) + row["minor"]
    for cause in (C.CAUSE_BALANCE, C.CAUSE_EXCLUSIVITY, C.CAUSE_EXHAUSTED):
        assert seen.get(cause, 0) > 0, f"{cause} is never exhibited on this cart"


def test_unpriced_value_is_declared_on_every_instrument(live: dict[str, Any]) -> None:
    """The integer never claims to be the whole worth of the card."""
    for candidate in live["valuation"]["instruments"]:
        assert candidate["unpriced"], candidate["instrument_id"]
        for entry in candidate["unpriced"]:
            assert entry["note"], f"{entry['benefit_id']} is unpriced with no rationale"


def test_both_refusals_actually_refuse_and_name_a_code(live: dict[str, Any]) -> None:
    by_id = {i["instrument_id"]: i for i in live["instruments"]}
    cart = Cart.from_dict(live["cart"])
    for case in live["refusals"]:
        assert case["reason_code"] == C.REFUSED_NO_WITNESS
        assert case["verification"]["ok"] is False
        assert case["verification"]["failures"], case["case_id"]
        assert case["supported_minor"] < case["asserted_minor"]
        manifest = Manifest.from_dict(by_id[case["witness"]["manifest_id"]]["manifest"])
        result = verify_witness(
            witness=Witness.from_dict(case["witness"]),
            manifest=manifest,
            cart=cart,
            asserted_minor=case["asserted_minor"],
        )
        assert not result.ok
        assert [f.code for f in result.failures] == [
            f["code"] for f in case["verification"]["failures"]
        ]


def test_the_refusals_are_built_on_a_real_products_terms(live: dict[str, Any]) -> None:
    """A refusal demonstrated against an invented term proves nothing about a real card."""
    catalogue = catalogue_bodies()
    for case in live["refusals"]:
        assert case["witness"]["manifest_id"] in catalogue, case["case_id"]


def test_the_omission_case_drops_an_issuer_signed_instrument(live: dict[str, Any]) -> None:
    """Omission is the attack, and the instrument at risk of being dropped is the signed one."""
    omission = live["omission"]
    dropped = omission["omitted_instrument"]["instrument_id"]
    by_id = {i["instrument_id"]: i for i in live["instruments"]}
    assert by_id[dropped]["issuer_signed"] is True
    assert dropped in omission["candidates_published"]
    assert dropped not in omission["candidates_served"]
    assert len(omission["candidates_served"]) == len(omission["candidates_published"]) - 1


def test_the_attribution_book_scores_only_issuer_signed_benefits(live: dict[str, Any]) -> None:
    """An issuer can only cut its own cost line, so only its own benefits are placed."""
    signed = {
        i["instrument_id"] for i in live["instruments"] if i["issuer_signed"]
    }
    assert live["attribution"]["benefits"]
    for row in live["attribution"]["benefits"]:
        assert row["manifest_id"] in signed
        assert row["annual_cost_minor"] >= 0
    assert "modelled" in live["attribution"]["cost_source"]
    assert "does not measure" in live["attribution"]["caveat"]


def test_the_attribution_corpus_has_more_than_one_winner(live: dict[str, Any]) -> None:
    """A corpus one instrument always wins measures nothing about which benefits decide."""
    wins = {row["manifest_id"]: row["wins"] for row in live["attribution"]["wins"]}
    assert sum(wins.values()) == live["attribution"]["corpus_size"]
    assert sum(1 for v in wins.values() if v > 0) >= 2
    signed = {i["instrument_id"] for i in live["instruments"] if i["issuer_signed"]}
    assert any(wins.get(mid, 0) > 0 for mid in signed), (
        "no issuer-signed instrument ever wins, so every attribution row is zero and the "
        "2x2 has nothing to say"
    )
    assert any(
        row["quadrant"] == C.QUADRANT_LOAD_BEARING for row in live["attribution"]["benefits"]
    )


# --------------------------------------------------------------------------------------
# Structural guards on the candidate set itself
# --------------------------------------------------------------------------------------


def test_the_candidate_set_is_single_currency() -> None:
    """Ranking across currencies would need an FX rate this system does not carry."""
    for spec in C.instrument_specs():
        assert spec.manifest.currency == C.CURRENCY


def test_the_console_cart_is_channel_neutral() -> None:
    """No candidate's own booking portal appears as the merchant.

    A cart routed through one issuer's portal is a cart the other candidates cannot pay at
    all, which would make the ranking a comparison between one card and a set of cards that
    were never eligible.
    """
    merchant = C.CART.merchant.lower()
    for token in ("smartbuy", "amextravel", "chasetravel", "hdfcbank", "americanexpress"):
        assert token not in merchant, f"the console cart is routed through {token}"


def test_the_hypothetical_is_the_only_instrument_not_in_the_catalogue() -> None:
    catalogue = set(P.catalogue_by_id(C.CONSOLE_CLOCK))
    outside = [
        s.manifest.manifest_id
        for s in C.instrument_specs()
        if s.manifest.manifest_id not in catalogue
    ]
    assert outside == [C.HYPOTHETICAL_ID], (
        f"instruments on the console with no entry in products.py: {outside}. Every card "
        f"term rendered beside an issuer's name must come from the sourced catalogue."
    )
