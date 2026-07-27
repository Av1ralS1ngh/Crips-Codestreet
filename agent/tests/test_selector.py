"""The selector: the agent reasons, the engine decides, and the gap between them is tested.

The single property every test here is really defending:

    Nothing a language model produced becomes a number in a signed artifact.

The agent chooses an instrument and states a criterion in words. Between those words and the
Decision Receipt sit four deterministic functions — `resolve_criterion`, `resolve_choice`,
`check_narrative` and `check_engine_agreement` — and the receipt's own numbers come from a
fresh `evaluate()` call this module makes, not from anything that arrived over MCP.

The second property, which is the demo: on an ordinary basket the marketing-copy run picks
the wrong card and its receipt says so, in the same machinery that certifies the derived run
as faithful. Compliance symmetry is not a slogan here; it is two assertions in one test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import selector as sel
from plumbline.evaluate import CRITERION_MAX_INCREMENTAL, CRITERION_MAX_PROTECTION_THEN_VALUE
from plumbline.mcp_server import CART_EVERYDAY, DEFAULT_AS_OF, DEMO_CARTS
from plumbline.products import AMEX_GOLD_ID, AMEX_PLATINUM_ID, CHASE_SAPPHIRE_RESERVE_ID
from plumbline.receipt import ATTEST_DEVIATED, ATTEST_FAITHFUL, CHECK_RANKING, verify_receipt

CLOCK = DEFAULT_AS_OF


@pytest.fixture(scope="module")
def pair():
    return sel.run_pair(scenario=CART_EVERYDAY, as_of=CLOCK)


# --------------------------------------------------------------------------------------
# The demo itself
# --------------------------------------------------------------------------------------


def test_the_marketing_agent_picks_the_wrong_card_and_the_derived_agent_picks_the_right_one(pair):
    assert pair.control.chosen_instrument_id == AMEX_PLATINUM_ID
    assert pair.derived.chosen_instrument_id == AMEX_GOLD_ID
    assert pair.derived.engine_choice == AMEX_GOLD_ID
    assert pair.guess_was_wrong() is True
    assert pair.same_choice() is False


def test_the_engine_ranks_the_gold_first_and_the_platinum_last(pair):
    ranking = pair.derived.evaluation.ranking
    assert ranking is not None
    assert [e.manifest_id for e in ranking.entries] == [
        AMEX_GOLD_ID,
        CHASE_SAPPHIRE_RESERVE_ID,
        AMEX_PLATINUM_ID,
    ]
    assert ranking.issuer_endorsed is False


def test_the_guess_has_a_measurable_cost_taken_from_the_engines_own_numbers(pair):
    best = pair.derived.best_value_minor
    chosen = pair.control.chosen_value_minor
    assert best is not None and chosen is not None
    assert pair.cost_of_guessing_minor() == best - chosen
    assert pair.cost_of_guessing_minor() > 0


def test_the_two_receipts_attest_opposite_outcomes_with_the_same_machinery(pair):
    """Compliance symmetry: one artifact says faithful, the other says deviated."""
    assert pair.derived.bundle.attestation_outcome == ATTEST_FAITHFUL
    assert pair.derived.bundle.faithful is True
    assert pair.control.bundle.attestation_outcome == ATTEST_DEVIATED
    assert pair.control.bundle.faithful is False
    assert pair.control.bundle.verification.failed(CHECK_RANKING)
    assert not pair.derived.bundle.verification.failed(CHECK_RANKING)


def test_the_deviating_receipt_still_signs_and_still_verifies_everything_else(pair):
    """A format that can only express compliant behaviour cannot evidence the other kind."""
    checks = {c.name: c.ok for c in pair.control.bundle.verification.checks}
    assert checks["RECEIPT_SIGNATURE"] is True
    assert checks["CANDIDATE_SET_DIGEST"] is True
    assert checks["SIGNER_ROLE_IS_NOT_ISSUER"] is True
    assert checks["NO_ISSUER_SIGNATURE_OVER_A_RANKING"] is True
    assert checks[CHECK_RANKING] is False


def test_both_receipts_record_the_full_candidate_set_not_the_winner(pair):
    for run in (pair.control, pair.derived):
        ids = {c.instrument_id for c in run.bundle.signed.receipt.candidates}
        assert ids == {AMEX_GOLD_ID, AMEX_PLATINUM_ID, CHASE_SAPPHIRE_RESERVE_ID}
        assert set(run.bundle.signed.receipt.mandate.authorized_instrument_ids) == ids


def test_both_receipts_are_anchored_with_a_valid_inclusion_proof(pair):
    for run in (pair.control, pair.derived):
        assert run.bundle.anchored.verify_anchor() is True
    assert len(pair.log) == 2


# --------------------------------------------------------------------------------------
# The boundary: no model output becomes a number
# --------------------------------------------------------------------------------------


def test_every_asserted_value_on_a_receipt_comes_from_the_engine(pair):
    for run in (pair.control, pair.derived):
        recorded = {
            c.instrument_id: c.asserted_value_minor
            for c in run.bundle.signed.receipt.candidates
        }
        engine = {c.manifest_id: c.asserted_minor for c in run.evaluation.candidates}
        assert recorded == engine


def test_the_agents_proposed_value_never_reaches_the_receipt(pair):
    """The control run proposed $150.00. It appears nowhere in the signed document."""
    claim = pair.control.claim
    assert claim.code == sel.CLAIM_REFUSED
    assert claim.claimed_minor == 15_000
    blob = json.dumps(pair.control.bundle.signed.to_dict())
    assert '"asserted_value_minor": 15000' not in blob
    assert str(claim.claimed_minor) not in {
        str(c.asserted_value_minor) for c in pair.control.bundle.signed.receipt.candidates
    }


def test_the_control_runs_claim_is_refused_by_the_witness(pair):
    probe = pair.control.claim
    assert probe.code == sel.CLAIM_REFUSED
    assert probe.realized_minor is not None
    assert probe.claimed_minor > probe.realized_minor


def test_the_derived_runs_claim_is_supported_because_it_quoted_the_engine(pair):
    probe = pair.derived.claim
    assert probe.code == sel.CLAIM_SUPPORTED
    assert probe.claimed_minor == pair.derived.chosen_value_minor


def test_the_marketing_figure_is_kept_out_of_the_signed_receipt(pair):
    """$1,900 is an advertised annual total. No allocation on this cart realizes it.

    The receipt records that the figure was refused and names it — a record of a refusal is
    strictly more useful than silence, and it is the same shape `evaluate.py` uses when it
    declines a proposed value. What must never happen is the figure appearing as a NUMBER:
    not as an asserted value, not in a ranking, not anywhere an integer field lives.
    """
    gate = pair.control.gate("narrative")
    assert gate is not None
    assert gate.ok is False
    assert gate.code == sel.NARRATIVE_REJECTED
    assert "$1,900" in gate.evidence

    body = pair.control.bundle.signed.receipt.body()
    blob = json.dumps(body)
    assert "advertises over" not in blob, "the rejected rationale must not be carried"
    assert sel.WITHHELD_NARRATIVE.split("{")[0] in blob

    # The only place the string may appear is inside the withholding disclosure.
    outside = json.dumps({k: v for k, v in body.items() if k != "disclosures"})
    assert "$1,900" not in outside
    assert 190_000 not in _numbers(body), "no integer field may carry the marketing figure"


def _numbers(payload) -> set[int]:
    """Every integer anywhere in a document. Used to prove a figure is not a value."""
    found: set[int] = set()
    if isinstance(payload, bool):
        return found
    if isinstance(payload, int):
        return {payload}
    if isinstance(payload, dict):
        for v in payload.values():
            found |= _numbers(v)
    elif isinstance(payload, (list, tuple)):
        for v in payload:
            found |= _numbers(v)
    return found


def test_the_derived_rationale_is_admitted_because_every_figure_reconciles(pair):
    gate = pair.derived.gate("narrative")
    assert gate is not None and gate.ok is True
    assert gate.code == sel.NARRATIVE_ACCEPTED
    body = json.dumps(pair.derived.bundle.signed.receipt.body())
    assert "The evaluator realizes $67.18" in body


def test_the_mcp_channel_and_the_signing_engine_agree(pair):
    gate = pair.derived.gate("engine_agreement")
    assert gate is not None
    assert gate.code == sel.AGREEMENT_OK
    assert gate.ok is True
    control_gate = pair.control.gate("engine_agreement")
    assert control_gate is not None and control_gate.code == sel.AGREEMENT_NOT_CHECKED


def test_the_boundary_disclosure_is_on_every_receipt(pair):
    for run in (pair.control, pair.derived):
        assert sel.BOUNDARY_DISCLOSURE in run.bundle.signed.receipt.disclosures


# --------------------------------------------------------------------------------------
# The gates, directly
# --------------------------------------------------------------------------------------


def test_resolve_criterion_accepts_only_the_closed_set():
    got, gate = sel.resolve_criterion(CRITERION_MAX_PROTECTION_THEN_VALUE)
    assert got == CRITERION_MAX_PROTECTION_THEN_VALUE
    assert gate.code == sel.CRITERION_ACCEPTED and gate.ok


def test_resolve_criterion_refuses_an_invented_one_and_records_the_default():
    got, gate = sel.resolve_criterion("maximise_delight")
    assert got == CRITERION_MAX_INCREMENTAL
    assert gate.code == sel.CRITERION_REJECTED_UNKNOWN
    assert gate.ok is False
    assert "maximise_delight" in gate.evidence


def test_resolve_criterion_handles_an_absent_one():
    got, gate = sel.resolve_criterion("")
    assert got == CRITERION_MAX_INCREMENTAL
    assert gate.code == sel.CRITERION_MISSING


def test_resolve_choice_refuses_an_instrument_outside_the_candidate_set():
    got, gate = sel.resolve_choice("some-other-card", [AMEX_GOLD_ID])
    assert got is None
    assert gate.code == sel.CHOICE_REJECTED_UNKNOWN


def test_resolve_choice_resolves_a_marketing_display_name():
    got, gate = sel.resolve_choice(
        "American Express Gold Card", [AMEX_GOLD_ID, AMEX_PLATINUM_ID]
    )
    assert got == AMEX_GOLD_ID
    assert gate.code == sel.CHOICE_ACCEPTED


def test_resolve_choice_reports_an_absent_recommendation():
    got, gate = sel.resolve_choice("", [AMEX_GOLD_ID])
    assert got is None and gate.code == sel.CHOICE_MISSING


@pytest.mark.parametrize(
    "text,expected",
    [
        ("$67.18", 6_718),
        ("$1,078", 107_800),
        ("₹26,50,000", 265_000_000),
        ("worth about $150.00 maybe", 15_000),
        ("$0.07", 7),
        ("no figure here", None),
        ("4x on dining", None),
        ("3% back", None),
    ],
)
def test_parse_money_minor_is_integer_only(text, expected):
    assert sel.parse_money_minor(text) == expected


def test_money_figures_ignores_rates_and_percentages():
    found = sel.money_figures("earns 4x and 3% and is worth $12.34 plus $5")
    assert [raw for raw, _ in found] == ["$12.34", "$5"]
    assert [minor for _, minor in found] == [1_234, 500]


def test_check_narrative_passes_prose_with_no_figures():
    gate = sel.check_narrative("The Gold Card fits this basket best.", frozenset())
    assert gate.ok and gate.code == sel.NARRATIVE_ACCEPTED_NO_FIGURES


def test_check_narrative_rejects_a_single_unverifiable_figure_and_withholds_everything():
    gate = sel.check_narrative("Worth $10.00 and $99.99 here.", frozenset({1_000}))
    assert gate.ok is False
    assert gate.code == sel.NARRATIVE_REJECTED
    assert gate.evidence == ("$99.99",)


def test_engine_figures_contains_pairwise_differences_it_derives_itself(pair):
    figures = sel.engine_figures(pair.derived.evaluation)
    values = sorted(
        c.asserted_minor for c in pair.derived.evaluation.candidates if c.asserted_minor
    )
    assert (values[-1] - values[0]) in figures
    assert pair.derived.evaluation.cart.total() in figures
    # And it does NOT contain the advertised annual credit total the control agent quoted.
    assert 190_000 not in figures


# --------------------------------------------------------------------------------------
# Replay, transport and determinism
# --------------------------------------------------------------------------------------


def test_replay_recomputes_and_does_not_read_numbers_out_of_the_trace():
    """The trace carries the model's choices only; the numbers are computed at replay."""
    blob = json.loads(
        (Path(sel.TRACE_DIR) / f"selector_{CART_EVERYDAY}.json").read_text(encoding="utf-8")
    )
    for mode in sel.MODES:
        for call in blob["runs"][mode]["tool_calls"]:
            assert "recorded_result" not in call or isinstance(call["recorded_result"], dict)
    # Values live nowhere in the trace as an authority: strip every recorded_result and the
    # run still produces the same numbers.
    stripped = {
        **blob,
        "runs": {
            mode: {
                "narration": blob["runs"][mode]["narration"],
                "tool_calls": [
                    {"tool": c["tool"], "input": c["input"]}
                    for c in blob["runs"][mode]["tool_calls"]
                ],
            }
            for mode in sel.MODES
        },
    }
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"selector_{CART_EVERYDAY}.json"
        path.write_text(json.dumps(stripped), encoding="utf-8")
        again = sel.run_pair(scenario=CART_EVERYDAY, as_of=CLOCK, trace_dir=Path(tmp))
    baseline = sel.run_pair(scenario=CART_EVERYDAY, as_of=CLOCK)
    assert again.derived.chosen_value_minor == baseline.derived.chosen_value_minor
    assert (
        again.derived.bundle.signed.receipt.receipt_hash()
        == baseline.derived.bundle.signed.receipt.receipt_hash()
    )


def test_the_trace_is_labelled_as_authored_not_as_a_live_transcript():
    blob = json.loads(
        (Path(sel.TRACE_DIR) / f"selector_{CART_EVERYDAY}.json").read_text(encoding="utf-8")
    )
    assert "authored" in blob["provenance"].lower()
    assert "NOT a live" in blob["provenance"]
    assert blob["model"] == sel.MODEL == "claude-opus-5"


def test_two_replays_produce_the_same_receipt_hash():
    a = sel.run_pair(scenario=CART_EVERYDAY, as_of=CLOCK)
    b = sel.run_pair(scenario=CART_EVERYDAY, as_of=CLOCK)
    assert a.log.root() == b.log.root()
    for left, right in ((a.control, b.control), (a.derived, b.derived)):
        assert (
            left.bundle.signed.receipt.receipt_hash()
            == right.bundle.signed.receipt.receipt_hash()
        )
        assert left.bundle.signed.signature == right.bundle.signed.signature


def test_the_stdio_transport_produces_the_same_valuation_as_in_process():
    """Same tools, same numbers, over a real MCP subprocess."""
    inproc = sel.run_pair(scenario=CART_EVERYDAY, as_of=CLOCK, transport=sel.TRANSPORT_INPROC)
    stdio = sel.run_pair(scenario=CART_EVERYDAY, as_of=CLOCK, transport=sel.TRANSPORT_STDIO)
    assert stdio.derived.chosen_instrument_id == inproc.derived.chosen_instrument_id
    assert stdio.derived.chosen_value_minor == inproc.derived.chosen_value_minor
    assert stdio.derived.gate("engine_agreement").code == sel.AGREEMENT_OK
    mcp_values = {
        i["instrument_id"]: i["asserted_value_minor"]
        for i in stdio.derived.tool_calls[1].result["instruments"]
    }
    engine = {c.manifest_id: c.asserted_minor for c in stdio.derived.evaluation.candidates}
    assert mcp_values == engine


def test_the_control_run_is_given_no_value_tool_at_all():
    names = {t["name"] for t in sel.tools_for(sel.MODE_CONTROL)}
    assert names == {sel.TOOL_LIST_PRODUCTS, sel.TOOL_READ_MARKETING, sel.RECOMMEND_TOOL}
    assert "value_cart" not in names


def test_the_derived_run_is_given_the_servers_own_published_schemas():
    from plumbline.mcp_server import TOOL_SPECS

    derived = {t["name"]: t for t in sel.tools_for(sel.MODE_DERIVED)}
    for spec in TOOL_SPECS:
        assert derived[spec["name"]]["description"] == spec["description"]
        assert derived[spec["name"]]["input_schema"] == spec["inputSchema"]


def test_the_session_pins_the_clock_the_model_cannot_choose_it():
    session = sel.SelectionSession(
        mode=sel.MODE_DERIVED,
        cart_id=CART_EVERYDAY,
        client=sel.InProcessMcpClient(),
        as_of=CLOCK,
    )
    session.execute("value_cart", {"cart": CART_EVERYDAY, "as_of": 1})
    assert session.calls[-1].input["as_of"] == CLOCK
    assert session.calls[-1].result["as_of"] == CLOCK


def test_an_agent_that_recommends_nothing_produces_a_receipt_that_says_so():
    from plumbline.mcp_server import signed_by_id
    from plumbline.transparency import TransparencyLog

    cart = DEMO_CARTS[CART_EVERYDAY]
    manifests = {
        mid: sm
        for mid, sm in signed_by_id(CLOCK).items()
        if sm.manifest.currency == cart.currency
    }
    log = TransparencyLog("t", signing_key=sel.LOG_KEY)
    run = sel.run_recorded(
        mode=sel.MODE_DERIVED,
        cart_id=CART_EVERYDAY,
        cart=cart,
        manifests=manifests,
        tool_calls=[{"tool": "value_cart", "input": {"cart": CART_EVERYDAY}}],
        narration=[],
        client=sel.InProcessMcpClient(),
        as_of=CLOCK,
        log=log,
    )
    assert run.chosen_instrument_id is None
    assert run.gate("choice").code == sel.CHOICE_MISSING
    assert run.gate("narrative").code == sel.NARRATIVE_MISSING
    assert run.claim.code == sel.CLAIM_NONE
    # The receipt is still produced, still signed, and still records everything considered.
    assert len(run.bundle.signed.receipt.candidates) == 3


def test_verify_receipt_accepts_the_derived_receipt_from_the_document_alone(pair):
    from plumbline.mcp_server import ISSUER_KEY, ISSUER_KEY_ID, signed_by_id

    cart = DEMO_CARTS[CART_EVERYDAY]
    manifests = {
        mid: sm
        for mid, sm in signed_by_id(CLOCK).items()
        if sm.manifest.currency == cart.currency
    }
    result = verify_receipt(
        json.loads(json.dumps(pair.derived.bundle.signed.to_dict())),
        sel.AGENT_KEY,
        cart=cart,
        manifests=manifests,
        issuer_keys={ISSUER_KEY_ID: ISSUER_KEY},
    )
    assert result.ok is True, result.render_text()


def test_a_tampered_receipt_fails_the_signature_check(pair):
    doc = json.loads(json.dumps(pair.derived.bundle.signed.to_dict()))
    doc["receipt"]["selection"]["instrument_id"] = AMEX_PLATINUM_ID
    result = verify_receipt(doc, sel.AGENT_KEY)
    assert result.ok is False
    assert result.failed("RECEIPT_SIGNATURE")


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def test_cli_replay_prints_the_side_by_side_and_exits_zero(capsys):
    assert sel.main(["--replay", "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "CONTROL — marketing copy only" in out
    assert "DERIVED — PLUMBLINE over MCP" in out
    assert ATTEST_FAITHFUL in out
    assert ATTEST_DEVIATED in out
    assert "issuer_endorsed=False" in out


def test_cli_json_mode_is_serialisable(capsys):
    assert sel.main(["--replay", "--json"]) == 0
    blob = json.loads(capsys.readouterr().out)
    assert blob["guess_was_wrong"] is True
    assert blob["derived"]["receipt"]["faithful"] is True
    assert blob["control"]["receipt"]["faithful"] is False


def test_cli_refuses_to_record_a_replay(capsys):
    assert sel.main(["--replay", "--record"]) == 2
    assert "requires --live" in capsys.readouterr().err


def test_live_path_fails_clearly_without_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(sel.LiveRunError) as exc:
        sel._client()
    assert "ANTHROPIC_API_KEY" in str(exc.value)
    # And the replay path is entirely unaffected by the missing key.
    assert sel.run_pair(scenario=CART_EVERYDAY, as_of=CLOCK).derived.bundle.faithful


# --------------------------------------------------------------------------------------
# The marketing copy is honest
# --------------------------------------------------------------------------------------


def test_marketing_copy_ranks_the_platinum_first_by_advertised_generosity():
    """If the copy were a strawman the control run's mistake would be ours, not the gap's."""
    from agent import marketing

    listed = marketing.listing()
    assert listed[0]["instrument_id"] == AMEX_PLATINUM_ID
    fees = {
        row["instrument_id"]: sel.parse_money_minor(row["advertised_annual_fee"])
        for row in listed
    }
    credits = {
        row["instrument_id"]: sel.parse_money_minor(row["advertised_credit_total"])
        for row in listed
    }
    assert fees[AMEX_PLATINUM_ID] == 89_500
    assert max(credits, key=lambda k: credits[k]) == AMEX_PLATINUM_ID


def test_advertised_fees_match_the_published_product_profiles():
    from agent import marketing
    from plumbline.products import profile

    for row in marketing.listing():
        assert (
            sel.parse_money_minor(row["advertised_annual_fee"])
            == profile(row["instrument_id"]).annual_fee_minor
        )


def test_marketing_pages_carry_no_machine_readable_valuation_signal():
    from agent import marketing

    for copy in marketing.COPY_BY_ID.values():
        text = copy.page_text().lower()
        for leak in ("mcc", "minor_minor", "capacity_minor", "exclusivity", "witness"):
            assert leak not in text
