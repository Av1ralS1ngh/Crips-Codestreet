"""The MCP surface: a transport, and provably nothing more.

Three things these tests are here to protect.

  1. The server contains no valuation logic. Every number it returns must be one
     `evaluate` / `allocate` / `witness` produced. That is asserted twice — once
     structurally, by reading the module source for arithmetic, and once behaviourally, by
     comparing every figure the server emits against a direct call into the evaluator.
  2. The wire format is real. The built-in JSON-RPC path is exercised through
     `handle_jsonrpc`, and if the official SDK is installed a live subprocess is driven over
     stdin/stdout so "we speak MCP" is a test result rather than a claim.
  3. Determinism. Same arguments, same bytes. An agent trace that cannot replay is not
     evidence of anything.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from caveat.cart import Cart, CartLine
from plumbline import mcp_server as srv
from plumbline.evaluate import CRITERION_MAX_PROTECTION_THEN_VALUE, evaluate
from plumbline.manifest import canonical_json
from plumbline.products import AMEX_GOLD_ID, AMEX_PLATINUM_ID, CHASE_SAPPHIRE_RESERVE_ID
from plumbline.receipt import witness_content_hash

CLOCK = srv.DEFAULT_AS_OF
REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------------------
# The transport-only property
# --------------------------------------------------------------------------------------


def test_server_module_contains_no_valuation_arithmetic():
    """No +, -, * or / anywhere in mcp_server.py.

    A transport that re-derives a figure is a second implementation of the hot path, and two
    implementations of a hot path disagree eventually. The only way to keep "this server
    computes nothing" true under maintenance is to make adding arithmetic fail a test.
    """
    source = (REPO_ROOT / "backend" / "plumbline" / "mcp_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
        ):
            # `a + "\n"` is string building, not arithmetic. Requiring a literal string on
            # one side keeps the exemption narrow: `total() + fee()` is still an offender.
            if isinstance(node.op, ast.Add) and (
                _is_string_literal(node.left) or _is_string_literal(node.right)
            ):
                continue
            offenders.append(ast.unparse(node))
    assert offenders == [], (
        "mcp_server.py must contain no valuation arithmetic; every number it returns is "
        f"computed by the evaluator. Found: {offenders}"
    )


def _is_string_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    return isinstance(node, ast.JoinedStr)


def test_every_value_matches_a_direct_evaluator_call():
    """The server is a projection of `evaluate()`, checked figure by figure."""
    cart = srv.EVERYDAY_BASKET
    manifests = [
        s for s in srv.signed_catalogue(CLOCK) if s.manifest.currency == cart.currency
    ]
    direct = evaluate(
        cart=cart,
        manifests=manifests,
        now=CLOCK,
        keys={srv.ISSUER_KEY_ID: srv.ISSUER_KEY},
    )
    served = srv.dispatch("value_cart", {"cart": srv.CART_EVERYDAY, "as_of": CLOCK})

    assert served["ok"] is True
    assert served["cart"]["hash"] == direct.cart_hash
    assert served["cart"]["total_minor"] == cart.total()

    by_id = {i["instrument_id"]: i for i in served["instruments"]}
    assert set(by_id) == {c.manifest_id for c in direct.candidates}
    for c in direct.candidates:
        row = by_id[c.manifest_id]
        assert row["asserted_value_minor"] == c.asserted_minor
        assert row["naive_per_line_sum_minor"] == c.naive_sum_minor
        assert row["overstatement_avoided_minor"] == c.overstatement_avoided_minor()
        assert row["protection_value_minor"] == c.protection_value_minor
        assert row["manifest_content_hash"] == c.manifest_hash
        assert row["witness_content_hash"] == witness_content_hash(
            c.witness, currency=cart.currency
        )
        assert row["derivation"] == c.derivation.to_dict(currency=cart.currency)

    assert direct.ranking is not None
    assert [e["manifest_id"] for e in served["ranking"]["entries"]] == [
        e.manifest_id for e in direct.ranking.entries
    ]
    assert served["ranking"]["policy_hash"] == direct.ranking.policy_hash
    assert served["ranking"]["issuer_endorsed"] is False


# --------------------------------------------------------------------------------------
# value_cart
# --------------------------------------------------------------------------------------


def test_value_cart_defaults_to_every_instrument_in_the_carts_currency():
    """Omission is the attack, so the default candidate set is everything."""
    served = srv.dispatch("value_cart", {"cart": srv.CART_EVERYDAY})
    ids = {i["instrument_id"] for i in served["instruments"]}
    assert ids == {AMEX_GOLD_ID, AMEX_PLATINUM_ID, CHASE_SAPPHIRE_RESERVE_ID}
    assert served["candidate_set_size"] == 3


def test_value_cart_carries_the_full_line_item_derivation_and_witness_hash():
    served = srv.dispatch("value_cart", {"cart": srv.CART_EVERYDAY})
    gold = next(i for i in served["instruments"] if i["instrument_id"] == AMEX_GOLD_ID)
    assert gold["witness_status"] == srv.WITNESS_VERIFIED
    assert gold["witness_verified"] is True
    assert len(gold["witness_content_hash"]) == 64
    assert gold["derivation"]["kind"] == "instrument"
    assert gold["derivation_lines"], "the derivation must be renderable for a human"
    # The tree is self-consistent: an internal node equals the sum of its children.
    total = sum(child["value_minor"] for child in gold["derivation"]["children"])
    assert total == gold["asserted_value_minor"]


def test_value_cart_ranks_the_gold_first_on_an_everyday_basket():
    """The fact the whole selector demo turns on, pinned so a manifest edit cannot move it
    silently. Ranked by advertised generosity the Platinum leads; ranked by what an
    allocation on this basket can actually realize, it is last."""
    served = srv.dispatch("value_cart", {"cart": srv.CART_EVERYDAY})
    order = [e["manifest_id"] for e in served["ranking"]["entries"]]
    assert order == [AMEX_GOLD_ID, CHASE_SAPPHIRE_RESERVE_ID, AMEX_PLATINUM_ID]


def test_value_cart_accepts_an_inline_cart_in_minor_units():
    served = srv.dispatch(
        "value_cart",
        {
            "cart": {
                "merchant": "m_inline",
                "currency": "USD",
                "lines": [
                    {
                        "sku": "x1",
                        "description": "Dinner",
                        "amount_minor": 12_000,
                        "mcc": 5812,
                        "category": "dining",
                    }
                ],
            }
        },
    )
    assert served["ok"] is True
    assert served["cart"]["total_minor"] == 12_000
    assert served["cart"]["label"] == "inline"


def test_inline_cart_refuses_a_float_amount_rather_than_guessing():
    served = srv.dispatch(
        "value_cart",
        {
            "cart": {
                "lines": [
                    {"sku": "x1", "description": "Dinner", "amount_minor": 120.5, "mcc": 5812}
                ]
            }
        },
    )
    assert served["ok"] is False
    assert served["error"]["code"] == srv.MCP_ERR_BAD_ARGUMENTS


def test_a_proposed_value_is_a_hypothesis_to_reject_not_a_source_of_value():
    """The LLM proposes; the deterministic engine disposes."""
    honest = srv.dispatch("value_cart", {"cart": srv.CART_EVERYDAY})
    gold_value = next(
        i["asserted_value_minor"] for i in honest["instruments"] if i["instrument_id"] == AMEX_GOLD_ID
    )
    inflated = srv.dispatch(
        "value_cart", {"cart": srv.CART_EVERYDAY, "claims": {AMEX_GOLD_ID: gold_value + 1}}
    )
    gold = next(i for i in inflated["instruments"] if i["instrument_id"] == AMEX_GOLD_ID)
    assert gold["status"] != "ATTESTED"
    assert gold["asserted_value_minor"] is None
    codes = {r["code"] for r in gold["refusals"]}
    assert "PLUMBLINE_REFUSE_CLAIM_UNSUPPORTED_BY_WITNESS" in codes


def test_a_criterion_outside_the_closed_set_is_refused():
    served = srv.dispatch("value_cart", {"cart": srv.CART_EVERYDAY, "criterion": "max_vibes"})
    assert served["ok"] is False
    assert served["error"]["code"] == srv.MCP_ERR_UNKNOWN_CRITERION


def test_a_different_criterion_changes_the_recorded_policy_hash():
    base = srv.dispatch("value_cart", {"cart": srv.CART_EVERYDAY})
    other = srv.dispatch(
        "value_cart",
        {"cart": srv.CART_EVERYDAY, "criterion": CRITERION_MAX_PROTECTION_THEN_VALUE},
    )
    assert base["ranking"]["policy_hash"] != other["ranking"]["policy_hash"]


def test_a_mixed_currency_candidate_set_is_refused_rather_than_converted():
    served = srv.dispatch(
        "value_cart",
        {"cart": srv.CART_EVERYDAY, "instruments": [AMEX_GOLD_ID, "hdfc-infinia-metal-2026"]},
    )
    assert served["ok"] is False
    assert served["error"]["code"] == srv.MCP_ERR_MIXED_CURRENCY


def test_an_empty_cart_produces_a_typed_refusal_not_a_zero():
    served = srv.dispatch(
        "value_cart", {"cart": {"merchant": "m_empty", "currency": "USD", "lines": []}}
    )
    assert served["signable"] is False
    assert served["ranking"] is None
    assert {r["code"] for r in served["refusals"]} & {"PLUMBLINE_REFUSE_EMPTY_CART"}


# --------------------------------------------------------------------------------------
# explain_derivation
# --------------------------------------------------------------------------------------


def test_explain_derivation_names_the_benefit_and_the_line_it_attached_to():
    served = srv.dispatch(
        "explain_derivation", {"instrument": AMEX_GOLD_ID, "cart": srv.CART_EVERYDAY}
    )
    assert served["ok"] is True
    attached = {(a["benefit_id"], a["line_sku"]): a["value_minor"] for a in served["attached"]}
    assert ("amex_gold_credit_dunkin", "wk_dunkin") in attached
    assert attached[("amex_gold_credit_dunkin", "wk_dunkin")] == 700
    assert all(a["explanation"] for a in served["attached"])
    assert sum(attached.values()) == served["asserted_value_minor"]


def test_explain_derivation_reports_exclusivity_and_capacity_blocks_with_reason_codes():
    """The half a bare number cannot answer: which benefits were looked at and lost."""
    served = srv.dispatch(
        "explain_derivation", {"instrument": AMEX_GOLD_ID, "cart": srv.CART_EVERYDAY}
    )
    reasons = {b["benefit_id"]: b["reason_code"] for b in served["blocked"]}
    # The base 1x shares an exclusivity group with the 4x rates, so on the four lines a 4x
    # rate claimed it is displaced — but it still earns on the two ride-hail lines, so it is
    # applied rather than blocked. The bonus rates that admit no line of this basket are.
    assert reasons["amex_gold_earn_hotels_5x"] == srv.BLOCK_INELIGIBLE
    assert reasons["amex_gold_earn_flights_3x"] == srv.BLOCK_INELIGIBLE
    assert all(r in {
        srv.BLOCK_EXCLUSIVITY,
        srv.BLOCK_CAPACITY,
        srv.BLOCK_EXHAUSTED,
        srv.BLOCK_NOT_ENROLLED,
        srv.BLOCK_INELIGIBLE,
        srv.BLOCK_ZERO_VALUE,
        srv.BLOCK_UNPRICED,
    } for r in reasons.values())


def test_exclusivity_block_is_reported_when_a_bonus_rate_displaces_the_base_rate():
    """A single-line dining cart: 4x claims the line, so 1x is displaced, not merely unused."""
    cart = {
        "merchant": "m_solo",
        "currency": "USD",
        "lines": [
            {
                "sku": "solo_dinner",
                "description": "Dinner",
                "amount_minor": 20_000,
                "mcc": 5812,
                "category": "dining",
            }
        ],
    }
    served = srv.dispatch("explain_derivation", {"instrument": AMEX_GOLD_ID, "cart": cart})
    reasons = {b["benefit_id"]: b["reason_code"] for b in served["blocked"]}
    assert reasons["amex_gold_earn_base_1x"] == srv.BLOCK_EXCLUSIVITY


def test_explain_derivation_carries_the_unpriced_declarations():
    served = srv.dispatch(
        "explain_derivation", {"instrument": AMEX_GOLD_ID, "cart": srv.CART_EVERYDAY}
    )
    labels = {u["label"] for u in served["considered_but_unpriced"]}
    assert "No airport lounge access" in labels, (
        "the Gold's absence of lounge access is declared so the comparison is honest"
    )
    assert all(u["rationale"] for u in served["considered_but_unpriced"])


# --------------------------------------------------------------------------------------
# get_manifest
# --------------------------------------------------------------------------------------


def test_get_manifest_returns_signed_facts_and_a_verified_signature():
    served = srv.dispatch("get_manifest", {"product": AMEX_PLATINUM_ID})
    assert served["ok"] is True
    assert served["signature"]["status"] == srv.SIG_VERIFIED
    assert served["signature"]["key_id"] == srv.ISSUER_KEY_ID
    assert len(served["signature"]["value"]) == 64
    assert served["benefits"], "a manifest with no benefits declares nothing"
    assert served["considered_but_unpriced"]


def test_get_manifest_signature_covers_facts_and_never_a_ranking():
    """The bytes the issuer signed carry no ranking vocabulary anywhere in them.

    Checked with `receipt.find_ranking_vocabulary`, the same recursive scan that guards
    `issuer_sign_facts`, so the property the signing path enforces is the property the wire
    surface exhibits.
    """
    from plumbline.receipt import find_ranking_vocabulary

    signed = srv.resolve_instrument(AMEX_GOLD_ID, CLOCK)
    assert find_ranking_vocabulary(signed.manifest.body()) == ()

    served = srv.dispatch("get_manifest", {"product": AMEX_GOLD_ID})
    assert find_ranking_vocabulary(served["benefits"]) == ()
    assert "never a ranking" in served["signature"]["covers"].lower()


def test_get_manifest_resolves_a_product_name_but_refuses_an_ambiguous_one():
    by_name = srv.dispatch("get_manifest", {"product": "Sapphire"})
    assert by_name["instrument_id"] == CHASE_SAPPHIRE_RESERVE_ID
    ambiguous = srv.dispatch("get_manifest", {"product": "American Express"})
    assert ambiguous["ok"] is False
    assert ambiguous["error"]["code"] == srv.MCP_ERR_UNKNOWN_INSTRUMENT


def test_unknown_instrument_is_a_typed_error_not_an_exception():
    served = srv.dispatch("get_manifest", {"product": "no-such-card"})
    assert served["ok"] is False
    assert served["error"]["code"] == srv.MCP_ERR_UNKNOWN_INSTRUMENT
    assert served["error"]["remedy"]


# --------------------------------------------------------------------------------------
# Dispatch and determinism
# --------------------------------------------------------------------------------------


def test_unknown_tool_returns_a_typed_error():
    served = srv.dispatch("delete_everything", {})
    assert served["ok"] is False
    assert served["error"]["code"] == srv.MCP_ERR_UNKNOWN_TOOL


def test_dispatch_never_raises_on_bad_arguments():
    for name in srv.TOOL_NAMES:
        result = srv.dispatch(name, {"nonsense": object()})
        assert isinstance(result, dict)
        assert "ok" in result


@pytest.mark.parametrize("name", list(srv.TOOL_NAMES))
def test_every_tool_is_byte_identical_across_two_calls(name):
    args = {
        "value_cart": {"cart": srv.CART_EVERYDAY},
        "explain_derivation": {"instrument": AMEX_GOLD_ID, "cart": srv.CART_EVERYDAY},
        "get_manifest": {"product": AMEX_GOLD_ID},
    }.get(name, {})
    first = canonical_json(srv.dispatch(name, args))
    second = canonical_json(srv.dispatch(name, args))
    assert first == second, f"{name} is not deterministic; an agent trace cannot replay"


def test_no_measured_latency_leaks_into_a_tool_response():
    """`elapsed_ms` measures the machine, not the decision. It must not reach the wire."""
    blob = json.dumps(srv.dispatch("value_cart", {"cart": srv.CART_EVERYDAY}))
    assert "elapsed_ms" not in blob


def test_tool_specs_and_handlers_agree():
    assert {s["name"] for s in srv.TOOL_SPECS} == set(srv.HANDLERS)
    for spec in srv.TOOL_SPECS:
        assert spec["description"].strip()
        assert spec["inputSchema"]["type"] == "object"


# --------------------------------------------------------------------------------------
# The wire
# --------------------------------------------------------------------------------------


def test_builtin_jsonrpc_handles_the_mcp_handshake():
    init = srv.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert init["result"]["serverInfo"]["name"] == srv.SERVER_NAME
    assert init["result"]["capabilities"]["tools"] is not None
    assert srv.handle_jsonrpc({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    listed = srv.handle_jsonrpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert [t["name"] for t in listed["result"]["tools"]] == list(srv.TOOL_NAMES)


def test_builtin_jsonrpc_tools_call_returns_the_same_payload_as_dispatch():
    response = srv.handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "value_cart", "arguments": {"cart": srv.CART_EVERYDAY}},
        }
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload == srv.dispatch("value_cart", {"cart": srv.CART_EVERYDAY})
    assert response["result"]["isError"] is False


def test_builtin_jsonrpc_reports_an_unknown_method():
    response = srv.handle_jsonrpc({"jsonrpc": "2.0", "id": 4, "method": "resources/list"})
    assert response["error"]["code"] == -32601


def test_builtin_stdio_loop_round_trips_over_real_streams():
    import io

    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "get_manifest", "arguments": {"product": AMEX_GOLD_ID}},
            }
        )
        + "\n"
    )
    stdout = io.StringIO()
    srv._serve_stdio_builtin(stdin=stdin, stdout=stdout)
    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(lines) == 2
    assert [t["name"] for t in lines[0]["result"]["tools"]] == list(srv.TOOL_NAMES)
    manifest = json.loads(lines[1]["result"]["content"][0]["text"])
    assert manifest["instrument_id"] == AMEX_GOLD_ID


@pytest.mark.skipif(not srv.sdk_available(), reason="official MCP SDK not installed")
def test_official_sdk_server_answers_over_a_real_stdio_subprocess():
    """Spawn the server and speak MCP to it. Proves the surface is a server, not a name."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT / "backend"), str(REPO_ROOT), env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    proc = subprocess.Popen(
        [sys.executable, "-m", "plumbline.mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        cwd=str(REPO_ROOT),
        env=env,
    )
    try:
        def send(obj):
            proc.stdin.write(json.dumps(obj) + "\n")
            proc.stdin.flush()

        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            }
        )
        init = json.loads(proc.stdout.readline())
        assert init["result"]["serverInfo"]["name"] == srv.SERVER_NAME
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        listed = json.loads(proc.stdout.readline())
        assert set(t["name"] for t in listed["result"]["tools"]) == set(srv.TOOL_NAMES)

        send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "value_cart", "arguments": {"cart": srv.CART_EVERYDAY}},
            }
        )
        called = json.loads(proc.stdout.readline())
        payload = json.loads(called["result"]["content"][0]["text"])
        assert payload == srv.dispatch("value_cart", {"cart": srv.CART_EVERYDAY})
    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=10)


def test_selftest_runs_without_a_client():
    lines = srv.selftest_lines()
    joined = "\n".join(lines)
    assert AMEX_GOLD_ID in joined
    assert "issuer_endorsed=False" in joined


def test_cli_print_tools_emits_valid_schemas(capsys):
    assert srv.main(["--print-tools"]) == 0
    blob = json.loads(capsys.readouterr().out)
    assert [t["name"] for t in blob["tools"]] == list(srv.TOOL_NAMES)


# --------------------------------------------------------------------------------------
# The demo basket itself
# --------------------------------------------------------------------------------------


def test_every_demo_cart_is_addressable_and_single_currency():
    for cart_id, cart in srv.DEMO_CARTS.items():
        skus = [line.sku for line in cart.lines]
        assert len(skus) == len(set(skus)), f"{cart_id} repeats a SKU"
        assert cart.lines, f"{cart_id} is empty"
        assert cart_id in srv.CART_DESCRIPTIONS


def test_resolve_cart_rejects_an_unknown_name():
    with pytest.raises(srv.ToolError) as exc:
        srv.resolve_cart("no_such_basket")
    assert exc.value.code == srv.MCP_ERR_UNKNOWN_CART
