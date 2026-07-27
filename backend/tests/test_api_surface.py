"""Every HTTP and WebSocket route on `caveat.api`, exercised the way the console drives it.

This module exists because the console's backend had no route-level coverage and a real
defect survived it: `POST /api/reset` — the control a judge presses between demo beats —
was a synchronous handler calling `asyncio.get_event_loop()`. FastAPI dispatches sync
handlers to a threadpool worker, where that call raises `RuntimeError`, so the endpoint
rebuilt the world and then returned 500. Nothing here recomputes a decision for display;
these tests assert the same route behaviour a browser would see.

The `world` behind the API is a module-level singleton, so every test resets it first and
the ordering between tests carries no meaning.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from caveat.api import app

try:  # `agent` sits beside `backend`, so it is absent under `cd backend && pytest tests`
    import agent  # noqa: F401

    AGENT_IMPORTABLE = True
except ImportError:
    AGENT_IMPORTABLE = False

CLEAN_CART: dict[str, Any] = {
    "merchant": "m_croma",
    "currency": "INR",
    "lines": [
        {
            "sku": "sku_kettle_01",
            "description": "1.7L electric kettle",
            "amount": 400_000,
            "mcc": 5722,
            "category": "appliances",
            "qty": 1,
        }
    ],
}

# Same merchant, same first line, plus stored value the signed intent never covered. This
# is the injected cart from the harness, expressed as an API payload.
INJECTED_CART: dict[str, Any] = {
    "merchant": "m_croma",
    "currency": "INR",
    "lines": [
        *CLEAN_CART["lines"],
        {
            "sku": "sku_giftcard_5000",
            "description": "Rs 5,000 stored-value gift card",
            "amount": 5_000_000,
            "mcc": 6540,
            "category": "stored_value",
            "qty": 10,
        },
    ],
}

# Above the demo mandate's step-up threshold (800,000) and below its amount cap.
STEP_UP_CART: dict[str, Any] = {
    "merchant": "m_croma",
    "currency": "INR",
    "lines": [
        {
            "sku": "sku_fridge_01",
            "description": "Double-door refrigerator",
            "amount": 900_000,
            "mcc": 5722,
            "category": "appliances",
            "qty": 1,
        }
    ],
}

NARROWED_SCOPE: list[dict[str, Any]] = [
    {"type": "amount_max", "value": 500_000},
    {"type": "cumulative_max", "value": 5_000_000},
    {"type": "category_allow", "values": ["appliances"]},
    {"type": "merchant_allow", "values": ["m_croma"]},
    {"type": "mcc_allow", "values": [5722]},
    {"type": "step_up_over", "value": 800_000},
]

WIDENED_SCOPE: list[dict[str, Any]] = [{"type": "amount_max", "value": 99_000_000}]


@pytest.fixture()
def client():
    """A client whose lifespan has run, so the hub is bound to a loop as in production."""
    with TestClient(app) as c:
        c.post("/api/reset")
        yield c


def root_id(client: TestClient) -> str:
    return client.get("/api/state").json()["root_mandate_id"]


def authorize(
    client: TestClient,
    mandate_id: str,
    intent: dict[str, Any],
    executed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/api/authorize",
        json={
            "mandate_id": mandate_id,
            "intent_cart": intent,
            "executed_cart": executed if executed is not None else intent,
            "geo": "IN",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------------------


def test_health_reports_a_verified_ledger(client) -> None:
    body = client.get("/api/health").json()
    assert body == {"ok": True, "ledger_verified": True, "ledger_error": None}


def test_state_carries_everything_the_console_renders(client) -> None:
    body = client.get("/api/state").json()
    for key in (
        "clock",
        "mandates",
        "root_mandate_id",
        "operators",
        "decisions",
        "ledger_root",
        "ledger_size",
        "ledger_verified",
        "revocations",
        "exposure",
        "pending_step_ups",
    ):
        assert key in body, key
    assert body["ledger_verified"] is True
    assert body["ledger_error"] is None
    assert body["root_mandate_id"] == body["mandates"][0]["mandate_id"]
    assert {o["operator_id"] for o in body["operators"]} >= {
        "op_shopbot",
        "op_pricechecker",
        "op_travelbot",
    }


def test_ledger_entries_chain_and_the_root_verifies(client) -> None:
    body = client.get("/api/ledger").json()
    assert body["verified"] is True and body["error"] is None
    assert body["size"] == len(body["entries"])
    assert [e["seq"] for e in body["entries"]] == list(range(body["size"]))
    for previous, entry in zip(body["entries"], body["entries"][1:]):
        assert entry["prev_hash"] == previous["entry_hash"]


def test_chain_endpoint_returns_the_hops_and_404s_an_unknown_mandate(client) -> None:
    mandate_id = root_id(client)
    body = client.get(f"/api/mandates/{mandate_id}/chain").json()
    assert body["mandate_id"] == mandate_id
    assert body["chain"] and body["chain"][0]["mandate_id"] == mandate_id
    assert body["descendants"] == []

    missing = client.get("/api/mandates/mnd_does_not_exist/chain")
    assert missing.status_code == 404
    assert "mnd_does_not_exist" in missing.json()["detail"]


def test_evidence_package_verifies_and_binds_the_decision_to_the_ledger(client) -> None:
    decision = authorize(client, root_id(client), CLEAN_CART)
    body = client.get(f"/api/evidence/{decision['txn_id']}").json()

    assert body["verification"]["ok"] is True
    assert all(check["ok"] for check in body["verification"]["checks"])
    names = {check["name"] for check in body["verification"]["checks"]}
    assert {"SIGNATURE", "MERKLE_INCLUSION_PROOF", "PROOF_BINDS_DECISION"} <= names
    assert body["package"]["signature"]


def test_evidence_404s_an_unknown_transaction(client) -> None:
    response = client.get("/api/evidence/txn_nope")
    assert response.status_code == 404
    assert "txn_nope" in response.json()["detail"]


# --------------------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------------------


def test_authorize_allows_a_cart_inside_the_mandate(client) -> None:
    decision = authorize(client, root_id(client), CLEAN_CART)
    assert decision["outcome"] == "ALLOW"
    assert decision["reason_codes"] == []
    assert decision["ledger_seq"] is not None


def test_authorize_denies_the_injected_cart_and_names_every_violation(client) -> None:
    decision = authorize(client, root_id(client), CLEAN_CART, INJECTED_CART)
    assert decision["outcome"] == "DENY"
    assert "MANDATE_CART_DIVERGENCE" in decision["reason_codes"]
    assert "AMOUNT_EXCEEDED" in decision["reason_codes"]
    assert "CATEGORY_NOT_ALLOWED" in decision["reason_codes"]
    assert decision["verdict"] == "INJECTION_COMPROMISE"
    assert "cardholder not liable" in decision["liable_party"]


def test_authorize_404s_an_unknown_mandate(client) -> None:
    response = client.post(
        "/api/authorize",
        json={
            "mandate_id": "mnd_nope",
            "intent_cart": CLEAN_CART,
            "executed_cart": CLEAN_CART,
        },
    )
    assert response.status_code == 404
    assert "mnd_nope" in response.json()["detail"]


def test_a_cart_missing_its_merchant_is_a_client_error_that_names_the_field(client) -> None:
    """A malformed cart must not surface as an opaque 500 with a stack trace in the log."""
    response = client.post(
        "/api/authorize",
        json={
            "mandate_id": root_id(client),
            "intent_cart": {"currency": "INR", "lines": CLEAN_CART["lines"]},
            "executed_cart": CLEAN_CART,
        },
    )
    assert response.status_code == 400, response.text
    assert "merchant" in response.json()["detail"]


def test_step_up_is_raised_then_satisfied_by_challenge_id(client) -> None:
    decision = authorize(client, root_id(client), STEP_UP_CART)
    assert decision["outcome"] == "STEP_UP"
    assert decision["reason_codes"] == ["STEP_UP_REQUIRED"]
    challenge_id = decision["step_up_challenge_id"]
    assert challenge_id

    pending = client.get("/api/state").json()["pending_step_ups"]
    assert challenge_id in {c["challenge_id"] for c in pending}

    body = client.post(f"/api/stepup/{challenge_id}").json()
    assert body["error"] is None
    assert body["challenge"]["satisfied"] is True
    assert body["challenge"]["cart_hash"]


def test_an_unknown_step_up_challenge_is_a_400_not_a_crash(client) -> None:
    response = client.post("/api/stepup/chg_nope")
    assert response.status_code == 400
    assert response.json()["detail"] == "UNKNOWN_CHALLENGE"


def test_delegate_accepts_a_narrowing_and_refuses_a_widening(client) -> None:
    parent = root_id(client)

    accepted = client.post(
        "/api/delegate",
        json={
            "parent_id": parent,
            "child_holder": "op_hop1",
            "declared_scope": NARROWED_SCOPE,
        },
    ).json()
    assert accepted["accepted"] is True
    assert accepted["entailment"]["entailed"] is True
    assert accepted["entailment"]["solver_result"] == "unsat"
    assert accepted["mandate"]["parent_id"] == parent

    refused = client.post(
        "/api/delegate",
        json={
            "parent_id": parent,
            "child_holder": "op_hop2",
            "declared_scope": WIDENED_SCOPE,
        },
    ).json()
    assert refused["accepted"] is False
    assert refused["entailment"]["solver_result"] == "sat"
    # The counterexample is the point: it names a transaction the child would allow and
    # the parent forbids, rather than reporting an unexplained refusal.
    assert refused["entailment"]["counterexample"]["amount"] > 1_000_000
    assert refused["entailment"]["counterexample"]["violated"]


def test_delegate_404s_an_unknown_parent(client) -> None:
    response = client.post(
        "/api/delegate",
        json={
            "parent_id": "mnd_nope",
            "child_holder": "op_hop1",
            "declared_scope": NARROWED_SCOPE,
        },
    )
    assert response.status_code == 404
    assert "mnd_nope" in response.json()["detail"]


def test_revoke_kills_the_subtree_and_the_next_authorization_denies(client) -> None:
    parent = root_id(client)
    client.post(
        "/api/delegate",
        json={
            "parent_id": parent,
            "child_holder": "op_hop1",
            "declared_scope": NARROWED_SCOPE,
        },
    )

    record = client.post("/api/revoke", json={"cause": "cardholder tapped revoke"}).json()
    assert record["root_id"] == parent
    assert record["rows_written"] >= 1
    assert record["descendants_killed"] == 1

    decision = authorize(client, parent, CLEAN_CART)
    assert decision["outcome"] == "DENY"
    assert decision["reason_codes"] == ["MANDATE_REVOKED"]


# --------------------------------------------------------------------------------------
# Reset — the control a judge presses between beats
# --------------------------------------------------------------------------------------


def test_reset_returns_200_and_rebuilds_the_world(client) -> None:
    before = root_id(client)
    authorize(client, before, CLEAN_CART)
    client.post("/api/revoke", json={"cause": "mid-demo"})

    response = client.post("/api/reset")
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True

    state = client.get("/api/state").json()
    assert state["clock"] == response.json()["clock"]
    assert state["decisions"] == []
    assert state["revocations"] == []
    assert state["mandates"][0]["revoked"] is False
    assert client.get("/api/health").json()["ledger_verified"] is True


def test_reset_is_idempotent_and_leaves_the_feed_serviceable(client) -> None:
    for _ in range(3):
        assert client.post("/api/reset").status_code == 200
    authorize(client, root_id(client), CLEAN_CART)
    with client.websocket_connect("/ws") as socket:
        # The backlog is cleared on reset, so a console attaching after one never replays
        # decisions against mandates the reset destroyed.
        message = socket.receive_json()
        assert message["kind"] == "decision"


# --------------------------------------------------------------------------------------
# Scenarios and the live feed
# --------------------------------------------------------------------------------------


def test_every_kernel_scenario_runs_over_http(client) -> None:
    if not AGENT_IMPORTABLE:
        pytest.skip("the `agent` package needs the repository root on the path")
    names = client.get("/api/scenarios").json()["scenarios"]
    assert names == ["clean_purchase", "escalation", "injection", "kill_switch", "step_up"]
    for name in names:
        response = client.post(f"/api/scenario/{name}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["scenario"] == name and body["live"] is False
        assert body["result"]


def test_an_unknown_kernel_scenario_404s_and_names_the_known_ones(client) -> None:
    if not AGENT_IMPORTABLE:
        pytest.skip("the `agent` package needs the repository root on the path")
    response = client.post("/api/scenario/overstatement")
    assert response.status_code == 404
    assert "clean_purchase" in response.json()["detail"]


def test_an_unimportable_agent_package_is_a_503_naming_the_command_that_fixes_it(
    client, monkeypatch
) -> None:
    """`cd backend && pytest tests` is a documented command and `agent` is not on that path.

    Without this the two scenario routes answer 500 with a bare ModuleNotFoundError, and a
    console driven from a server started that way shows five dead buttons and no reason.
    """
    import builtins

    real_import = builtins.__import__

    def refuse_agent(name, *args, **kwargs):
        if name == "agent" or name.startswith("agent."):
            raise ImportError("No module named 'agent'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse_agent)

    for response in (client.get("/api/scenarios"), client.post("/api/scenario/injection")):
        assert response.status_code == 503, response.text
        detail = response.json()["detail"]
        assert "PYTHONPATH=backend:." in detail
        assert "uvicorn caveat.api:app" in detail


def test_the_websocket_replays_the_backlog_then_streams_new_events(client) -> None:
    authorize(client, root_id(client), CLEAN_CART)
    with client.websocket_connect("/ws") as socket:
        replayed = socket.receive_json()
        assert replayed["kind"] == "decision"
        assert replayed["payload"]["outcome"] == "ALLOW"

        client.post("/api/revoke", json={"cause": "streamed"})
        streamed = socket.receive_json()
        assert streamed["kind"] == "revocation"
        assert streamed["seq"] > replayed["seq"]
