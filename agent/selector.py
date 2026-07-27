"""The instrument selector: one agent, one basket, run twice — guessing, then deriving.

This is the demonstration the whole thesis rests on. Asserting that agents cannot see
issuer-side value is a slide. Showing the same model, on the same cart, pick the wrong card
from marketing copy and then the right one from a witness-backed derivation is evidence.

    control   the agent has a product catalogue and marketing copy. That is the entire
              value signal the shipped protocols give it: ACP's one formal extension is the
              merchant's discount code, UCP's January 2026 Loyalty Extension lives in the
              seller's response object and explicitly cannot compare two instruments, and
              AP2 puts instrument selection outside its scope. So the agent guesses.
    derived   the agent has the PLUMBLINE MCP tools. It calls value_cart, reads the
              deterministic per-instrument value, calls explain_derivation to see which
              benefit attached to which line, and states its criterion.

THE BOUNDARY, WHICH IS THE PITCH

    The agent does not compute value. Not in either run.

In the derived run it reads numbers a deterministic allocator produced and a linear-time
verifier re-checked. Its job is to REASON ABOUT that result, to say which criterion it
applied, and to explain the answer to a human. In the control run it has nothing to read, so
whatever number it offers is a guess — and this module treats that guess exactly the way
`evaluate.py` treats any proposed value: as a hypothesis to reject.

Three deterministic gates stand between anything the model produced and the signed receipt,
and each one is a function in this file with its own reason code:

  `resolve_criterion`   the criterion must be a member of `evaluate.CRITERIA`. A criterion
                        the model invented is refused and the default is recorded instead.
  `resolve_choice`      the chosen instrument must be a member of the candidate set the
                        Card Member's mandate authorised.
  `check_narrative`     EVERY money figure in the model's prose is re-derived from the
                        engine's own output. A figure the validator cannot reproduce means
                        the whole rationale is withheld from the receipt, with the offending
                        figure named. This is what stops a marketing number reaching a
                        signed artifact by riding inside a sentence.

And the receipt itself is built from a fresh `evaluate()` call made by this module, not from
anything that came back over the wire. The MCP channel is how the agent LEARNED; the engine
is what SIGNS. `engine_agreement` checks the two agree and records the answer.

REPLAY

  `--replay` is the default and the demo path. It drives recorded tool-call sequences
  through the identical tool implementations, so a replayed run re-computes every number
  live — the trace carries the model's choices and nothing else. `--live` calls
  claude-opus-5 for real. A missing ANTHROPIC_API_KEY breaks the live path with a clear
  message and never the replay path.

TRANSPORT

  `--transport inproc` (default) calls `plumbline.mcp_server.dispatch` in process.
  `--transport stdio` spawns `python -m plumbline.mcp_server` as a subprocess and speaks real
  MCP: newline-delimited JSON-RPC, `initialize`, `tools/list`, `tools/call`. Same tools,
  same numbers, and the second one proves the surface is a real server rather than a
  function call wearing a protocol's name.

Honest limitations:
  * Receipts and manifests are signed HMAC-SHA256 under prototype keys held in this file.
  * The recorded traces in agent/traces are authored fixtures unless their `provenance`
    field says otherwise. An authored trace is never labelled as a live transcript.
  * The control run's marketing copy is a faithful paraphrase of published positioning, not
    a strawman and not scraped. See agent/marketing.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from caveat.cart import Cart
from plumbline.evaluate import (
    CRITERIA,
    CRITERION_MAX_INCREMENTAL,
    REFUSE_CLAIM_UNSUPPORTED,
    Evaluation,
    ValuationPolicy,
    evaluate,
)
from plumbline.manifest import SignedManifest
from plumbline.mcp_server import (
    CART_EVERYDAY,
    DEFAULT_AS_OF,
    DEMO_CARTS,
    ISSUER_KEY,
    ISSUER_KEY_ID,
    SERVER_NAME,
    TOOL_SPECS,
    dispatch,
    sdk_status,
    signed_by_id,
)
from plumbline.products import fmt_currency, profile
from plumbline.receipt import (
    ATTEST_FAITHFUL,
    DEFAULT_DISCLOSURES,
    POSTURE_OBSERVE_ONLY,
    ROLE_AGENT,
    AnchoredReceipt,
    CheckoutSession,
    Identity,
    MandateBinding,
    ReceiptVerification,
    SignedReceipt,
    anchor_receipt,
    build_receipt_from_evaluation,
    sign_receipt,
    verify_receipt,
)
from plumbline.transparency import TransparencyLog

from . import marketing

MODEL = "claude-opus-5"
MAX_TOKENS = 4096
MAX_TURNS = 14

TRACE_DIR = Path(__file__).resolve().parent / "traces"

MODE_CONTROL = "control"
MODE_DERIVED = "derived"
MODES = (MODE_CONTROL, MODE_DERIVED)

SOURCE_LIVE = "live"
SOURCE_REPLAY = "replay"

TRANSPORT_INPROC = "inproc"
TRANSPORT_STDIO = "stdio"
TRANSPORTS = (TRANSPORT_INPROC, TRANSPORT_STDIO)

# Prototype keys. Production signs the receipt with the agent's own key and each manifest
# with its issuer's HSM key; canonicalisation and the verification flow are unchanged.
AGENT_KEY = "plumbline-demo-agent-key"
LOG_KEY = "plumbline-demo-transparency-key"
LOG_ID = "plumbline-selector-log"

RECOMMEND_TOOL = "state_recommendation"
TOOL_LIST_PRODUCTS = "list_products"
TOOL_READ_MARKETING = "read_marketing_copy"

STATUS_ERROR = "error"
STATUS_RECORDED = "recorded"

# --------------------------------------------------------------------------------------
# Gate reason codes. Module-level constants; a gate never returns an inline string.
# --------------------------------------------------------------------------------------

CRITERION_ACCEPTED = "AGENT_CRITERION_ACCEPTED"
CRITERION_REJECTED_UNKNOWN = "AGENT_CRITERION_REJECTED_NOT_IN_CLOSED_SET"
CRITERION_MISSING = "AGENT_CRITERION_ABSENT_DEFAULT_APPLIED"

CHOICE_ACCEPTED = "AGENT_CHOICE_IN_CANDIDATE_SET"
CHOICE_REJECTED_UNKNOWN = "AGENT_CHOICE_NOT_IN_CANDIDATE_SET"
CHOICE_MISSING = "AGENT_MADE_NO_RECOMMENDATION"

NARRATIVE_ACCEPTED = "AGENT_NARRATIVE_FIGURES_RECONCILE"
NARRATIVE_ACCEPTED_NO_FIGURES = "AGENT_NARRATIVE_CARRIES_NO_FIGURES"
NARRATIVE_REJECTED = "AGENT_NARRATIVE_REJECTED_UNVERIFIED_FIGURE"
NARRATIVE_MISSING = "AGENT_NARRATIVE_ABSENT"

CLAIM_NONE = "AGENT_PROPOSED_NO_VALUE"
CLAIM_UNPARSEABLE = "AGENT_PROPOSED_VALUE_NOT_PARSEABLE"
CLAIM_SUPPORTED = "AGENT_PROPOSED_VALUE_SUPPORTED_BY_WITNESS"
CLAIM_REFUSED = REFUSE_CLAIM_UNSUPPORTED

AGREEMENT_OK = "MCP_CHANNEL_AGREES_WITH_SIGNING_ENGINE"
AGREEMENT_DIVERGED = "MCP_CHANNEL_DISAGREES_WITH_SIGNING_ENGINE"
AGREEMENT_NOT_CHECKED = "NO_MCP_VALUE_CHANNEL_IN_THIS_RUN"

WITHHELD_NARRATIVE = (
    "Agent rationale WITHHELD from this receipt: {code}. Unverifiable figure(s): {figures}. "
    "The validator re-derives every money figure in the agent's prose from the engine's own "
    "output; a figure it cannot reproduce does not enter a signed artifact."
)

BOUNDARY_DISCLOSURE = (
    "The agent computed no value. Every figure in this receipt was produced by the "
    "deterministic allocator, re-checked by the linear-time witness verifier, and signed by "
    "the agent's key. The agent chose an instrument and stated a criterion; the engine "
    "produced the numbers and the attestation checks the choice against them."
)


class LiveRunError(RuntimeError):
    """Raised when the live path cannot run. Never raised on the replay path."""


# --------------------------------------------------------------------------------------
# MCP clients
# --------------------------------------------------------------------------------------


class InProcessMcpClient:
    """Calls the server's dispatcher directly. Same code path the stdio server runs."""

    transport = TRANSPORT_INPROC

    def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return dispatch(name, arguments)

    def describe(self) -> str:
        return f"in-process dispatch into {SERVER_NAME}.mcp_server"

    def close(self) -> None:
        return None


class StdioMcpClient:
    """A real MCP client over a real subprocess.

    Newline-delimited JSON-RPC 2.0 on the child's stdin/stdout, which is the MCP stdio
    transport. Written by hand rather than driven through the SDK's async client so the
    whole exchange stays synchronous and inspectable, which is the same reason
    `agent/shopper.py` drives a manual tool-use loop instead of the beta tool runner.
    """

    transport = TRANSPORT_STDIO

    def __init__(self, command: Sequence[str] | None = None, cwd: str | None = None) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        self._command = list(command or [sys.executable, "-m", "plumbline.mcp_server"])
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        backend = str(repo_root / "backend")
        env["PYTHONPATH"] = f"{backend}{os.pathsep}{repo_root}" + (
            f"{os.pathsep}{existing}" if existing else ""
        )
        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            cwd=cwd or str(repo_root),
            env=env,
        )
        self._next_id = 0
        self._server_info: dict[str, Any] = {}
        self._initialize()

    def _send(self, message: Mapping[str, Any]) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def _request(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        assert self._proc.stdout is not None
        self._next_id += 1
        self._send(
            {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": dict(params or {})}
        )
        line = self._proc.stdout.readline()
        if not line:
            raise LiveRunError(
                f"the MCP server exited before answering {method!r}; run "
                f"`python -m plumbline.mcp_server --selftest` to see why"
            )
        return json.loads(line)

    def _initialize(self) -> None:
        response = self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "plumbline-selector", "version": "1.0.0"},
            },
        )
        self._server_info = dict((response.get("result") or {}).get("serverInfo") or {})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def list_tools(self) -> list[str]:
        result = self._request("tools/list").get("result") or {}
        return [str(t.get("name", "")) for t in result.get("tools", [])]

    def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        response = self._request("tools/call", {"name": name, "arguments": dict(arguments)})
        if "error" in response:
            return {"ok": False, "error": response["error"]}
        blocks = (response.get("result") or {}).get("content") or []
        for block in blocks:
            if block.get("type") == "text":
                return json.loads(block["text"])
        return {"ok": False, "error": {"code": "MCP_EMPTY_RESULT", "detail": "no text content"}}

    def describe(self) -> str:
        name = self._server_info.get("name", SERVER_NAME)
        version = self._server_info.get("version", "?")
        return f"MCP stdio subprocess -> {name} {version} ({' '.join(self._command)})"

    def close(self) -> None:
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:  # pragma: no cover - best-effort teardown
            self._proc.kill()


def build_client(transport: str) -> InProcessMcpClient | StdioMcpClient:
    if transport == TRANSPORT_INPROC:
        return InProcessMcpClient()
    if transport == TRANSPORT_STDIO:
        return StdioMcpClient()
    raise ValueError(f"unknown transport {transport!r}; expected one of {TRANSPORTS}")


# --------------------------------------------------------------------------------------
# Tool surfaces
# --------------------------------------------------------------------------------------

RECOMMENDATION_TOOL: dict[str, Any] = {
    "name": RECOMMEND_TOOL,
    "description": (
        "Record your final recommendation. This tool computes nothing and stores no number "
        "you supply as a value: the figures on the Decision Receipt come from the "
        "deterministic evaluator. State which instrument you recommend, which criterion you "
        "applied, why, and — so the engine can check your claim against a witness — what you "
        "believe this cart is worth on that instrument. Call this exactly once, last."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "instrument_id": {
                "type": "string",
                "description": "The instrument you recommend, by id or by product name.",
            },
            "criterion": {
                "type": "string",
                "enum": list(CRITERIA),
                "description": (
                    "The Card Member's ranking rule you applied. A closed set — a criterion "
                    "outside it is refused and the default is recorded in its place."
                ),
            },
            "rationale": {
                "type": "string",
                "description": (
                    "Why, in two or three sentences, for a human. Every money figure you "
                    "write here is re-derived from the engine's output by a validator; one "
                    "it cannot reproduce means the whole rationale is withheld from the "
                    "receipt. Quote engine figures or none."
                ),
            },
            "claimed_value_display": {
                "type": "string",
                "description": (
                    "What you believe this cart is worth on that instrument, e.g. \"$67.18\". "
                    "Treated as a hypothesis to test against an exhibited allocation, never "
                    "as a value. If you are guessing, say so and guess anyway."
                ),
            },
        },
        "required": ["instrument_id", "criterion", "rationale"],
    },
}

CONTROL_TOOLS: list[dict[str, Any]] = [
    {
        "name": TOOL_LIST_PRODUCTS,
        "description": (
            "List the payment instruments in the Card Member's wallet as a product "
            "comparison page lists them: display name, headline, advertised annual fee and "
            "advertised annual statement credit total."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": TOOL_READ_MARKETING,
        "description": (
            "Open one product's marketing page and return its text exactly as published."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product": {"type": "string", "description": "Product name or instrument id."}
            },
            "required": ["product"],
        },
    },
    RECOMMENDATION_TOOL,
]


def mcp_tools_for_claude() -> list[dict[str, Any]]:
    """The MCP server's own schemas, renamed into the Anthropic tool shape.

    `inputSchema` -> `input_schema` and nothing else. The descriptions the agent reads are
    the descriptions the server publishes, so what the model is told about a tool cannot
    drift from what the tool does.
    """
    return [
        {
            "name": spec["name"],
            "description": spec["description"],
            "input_schema": spec["inputSchema"],
        }
        for spec in TOOL_SPECS
    ]


DERIVED_TOOLS: list[dict[str, Any]] = [*mcp_tools_for_claude(), RECOMMENDATION_TOOL]


def tools_for(mode: str) -> list[dict[str, Any]]:
    return list(DERIVED_TOOLS if mode == MODE_DERIVED else CONTROL_TOOLS)


SYSTEM_CONTROL = (
    "You are a payment-instrument selector acting for a Card Member at checkout.\n"
    "You have a product catalogue and each product's marketing page. There is no tool that "
    "will value the cart for you — this is the situation every shipped agentic checkout "
    "protocol leaves you in today.\n"
    "Read what you can, then call state_recommendation exactly once with the instrument you "
    "recommend, the criterion you applied, your reasoning, and your best estimate of what "
    "this cart is worth on that instrument. Be explicit that the estimate is an estimate."
)

SYSTEM_DERIVED = (
    "You are a payment-instrument selector acting for a Card Member at checkout.\n"
    "You are connected to the PLUMBLINE MCP server. It values the cart on every instrument "
    "using a deterministic allocator and an independent verifier.\n"
    "You do NOT compute value. Call value_cart, read the numbers it returns, and use "
    "explain_derivation and get_manifest to understand WHY the numbers came out that way — "
    "which benefit attached to which line, and which were blocked by exclusivity or "
    "capacity. Your job is to reason about the result, state the criterion you applied, and "
    "explain it to a human.\n"
    "Then call state_recommendation exactly once. Quote the engine's own figures; a figure "
    "you compute yourself will be rejected by the receipt validator."
)


def system_prompt_for(mode: str) -> str:
    return SYSTEM_DERIVED if mode == MODE_DERIVED else SYSTEM_CONTROL


def task_for(mode: str, cart_id: str, cart: Cart) -> str:
    shared = (
        f"The Card Member is checking out at {cart.merchant} with a basket of "
        f"{len(cart.lines)} items totalling {fmt_currency(cart.total(), cart.currency)}. "
        f"They hold three cards and want to know which one to put this basket on."
    )
    if mode == MODE_DERIVED:
        return (
            f"{shared} The basket is registered with the value server as cart id "
            f"{cart_id!r}. Work out which instrument is worth the most on it and why, then "
            f"record your recommendation."
        )
    return (
        f"{shared} The basket is: "
        + "; ".join(
            f"{line.description} {fmt_currency(line.amount, cart.currency)}"
            for line in cart.lines
        )
        + ". Work out which card to recommend and record it."
    )


# --------------------------------------------------------------------------------------
# Run records
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCall:
    tool: str
    input: dict[str, Any]
    result: dict[str, Any]

    def signature(self) -> str:
        if not self.input:
            return f"{self.tool}()"
        bits = ", ".join(
            f"{k}={_short(v)}" for k, v in sorted(self.input.items()) if k != "as_of"
        )
        return f"{self.tool}({bits})"

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "input": self.input, "result": self.result}

    def to_trace_dict(self) -> dict[str, Any]:
        # Replay re-executes rather than trusting the recorded result, so the result is
        # carried for inspection only and never read back as an answer.
        return {"tool": self.tool, "input": self.input, "recorded_result": self.result}


def _short(value: Any, limit: int = 34) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…'"


@dataclass(frozen=True)
class Recommendation:
    """Exactly what the model said. Untrusted until every gate below has run."""

    instrument_id: str
    criterion: str
    rationale: str
    claimed_value_display: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "criterion": self.criterion,
            "rationale": self.rationale,
            "claimed_value_display": self.claimed_value_display,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "Recommendation":
        return cls(
            instrument_id=str(payload.get("instrument_id", "")).strip(),
            criterion=str(payload.get("criterion", "")).strip(),
            rationale=str(payload.get("rationale", "")).strip(),
            claimed_value_display=str(payload.get("claimed_value_display", "")).strip(),
        )


# --------------------------------------------------------------------------------------
# The session
# --------------------------------------------------------------------------------------


class SelectionSession:
    """Executes agent tool calls. Live and replay share this object.

    `as_of` is stamped onto every value tool call by this class, overriding whatever the
    model supplied. A model that invented a clock would make the run unreplayable, and the
    clock is not a thing an agent gets to choose.
    """

    def __init__(
        self,
        *,
        mode: str,
        cart_id: str,
        client: InProcessMcpClient | StdioMcpClient | None = None,
        as_of: int = DEFAULT_AS_OF,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
        self.mode = mode
        self.cart_id = cart_id
        self.as_of = as_of
        self.client = client if mode == MODE_DERIVED else None
        self.calls: list[ToolCall] = []
        self.recommendation: Recommendation | None = None
        self.mcp_valuation: dict[str, Any] | None = None

    def execute(self, tool: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        arguments = dict(payload or {})
        result = self._run(tool, arguments)
        self.calls.append(ToolCall(tool=tool, input=arguments, result=result))
        return result

    def _run(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool == RECOMMEND_TOOL:
            self.recommendation = Recommendation.from_payload(arguments)
            return {
                "ok": True,
                "status": STATUS_RECORDED,
                "message": (
                    "Recommendation recorded. It will pass through deterministic gates "
                    "before any part of it reaches the Decision Receipt."
                ),
            }
        if self.mode == MODE_CONTROL:
            return self._control_tool(tool, arguments)
        if self.client is None:  # pragma: no cover - constructor guarantees a client
            return {"ok": False, "error": {"code": "NO_CLIENT", "detail": "no MCP client"}}
        arguments["as_of"] = self.as_of
        result = self.client.call(tool, arguments)
        if tool == "value_cart" and result.get("ok"):
            self.mcp_valuation = result
        return result

    def _control_tool(self, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if tool == TOOL_LIST_PRODUCTS:
            return {
                "ok": True,
                "products": marketing.listing(),
                "note": (
                    "This is the entire value signal available without an issuer-side value "
                    f"rail: {marketing.MARKETING_SIGNAL}"
                ),
            }
        if tool == TOOL_READ_MARKETING:
            copy = marketing.copy_for(str(arguments.get("product", "")))
            if copy is None:
                return {
                    "ok": False,
                    "error": {
                        "code": "UNKNOWN_PRODUCT",
                        "detail": f"no marketing page for {arguments.get('product')!r}",
                    },
                }
            return {"ok": True, **copy.to_dict()}
        return {
            "ok": False,
            "status": STATUS_ERROR,
            "error": {"code": "UNKNOWN_TOOL", "detail": f"no such tool: {tool!r}"},
        }


# --------------------------------------------------------------------------------------
# Deterministic gates. Nothing the model produced passes one of these untouched.
# --------------------------------------------------------------------------------------

_MONEY = re.compile(r"[$₹]\s?\d[\d,]*(?:\.\d{1,2})?")


@dataclass(frozen=True)
class Gate:
    """One deterministic check on model output: what it decided and what it used."""

    name: str
    code: str
    ok: bool
    detail: str
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "code": self.code,
            "ok": self.ok,
            "detail": self.detail,
            "evidence": list(self.evidence),
        }


def parse_money_minor(text: str) -> int | None:
    """Parse one currency figure into integer minor units, or None.

    Integer arithmetic throughout: the fractional part is read as a two-digit string and
    padded, never divided. A float here would be a float in the path that decides whether a
    model's claim reaches a signed document.
    """
    match = _MONEY.search(text or "")
    if match is None:
        return None
    body = match.group(0)[1:].strip().replace(",", "")
    if "." in body:
        whole, _, frac = body.partition(".")
    else:
        whole, frac = body, ""
    if not whole.isdigit():
        return None
    frac = (frac + "00")[:2]
    if not frac.isdigit():
        return None
    return int(whole) * 100 + int(frac)


def money_figures(text: str) -> tuple[tuple[str, int], ...]:
    """Every currency figure in a piece of prose, as (as-written, minor units).

    Only symbol-prefixed figures are extracted. A rate like "4x" or "3%" is not a money
    claim, and flagging one would be a false positive in a gate whose whole value is that a
    rejection means something.
    """
    out: list[tuple[str, int]] = []
    for raw in _MONEY.findall(text or ""):
        minor = parse_money_minor(raw)
        if minor is not None:
            out.append((raw, minor))
    return tuple(out)


def engine_figures(evaluation: Evaluation) -> frozenset[int]:
    """Every money figure the engine itself produced for this cart, in minor units.

    Includes pairwise differences between candidates' asserted values, because "the Gold is
    worth X more than the Platinum" is a legitimate thing for an agent to say and the
    validator can re-derive it. The difference is computed HERE, deterministically, and then
    the model's figure is compared against it — the model's arithmetic is checked, never
    trusted and never used.
    """
    figures: set[int] = {evaluation.cart.total()}
    for line in evaluation.cart.lines:
        figures.add(line.amount)

    asserted: list[int] = []
    for c in evaluation.candidates:
        figures.add(c.naive_sum_minor)
        figures.add(c.protection_value_minor)
        figures.add(c.overstatement_avoided_minor())
        figures.add(profile(c.manifest_id).annual_fee_minor)
        if c.asserted_minor is not None:
            figures.add(c.asserted_minor)
            asserted.append(c.asserted_minor)
        if c.witness is not None:
            for a in c.witness.assignments:
                figures.add(a.value_minor)
                figures.add(a.consumed_minor)

    for i, left in enumerate(asserted):
        for right in asserted[i + 1 :]:
            figures.add(abs(left - right))

    if evaluation.ranking is not None:
        figures.add(evaluation.ranking.baseline_minor)
        for e in evaluation.ranking.entries:
            figures.add(e.asserted_minor)
            figures.add(e.incremental_minor)
            if e.margin_over_next_minor is not None:
                figures.add(e.margin_over_next_minor)

    return frozenset(f for f in figures if f >= 0)


def resolve_criterion(raw: str) -> tuple[str, Gate]:
    """The criterion must be a member of the closed set. Otherwise the default applies."""
    if not raw:
        return CRITERION_MAX_INCREMENTAL, Gate(
            name="criterion",
            code=CRITERION_MISSING,
            ok=False,
            detail=(
                f"the agent stated no criterion; {CRITERION_MAX_INCREMENTAL} is recorded "
                f"instead. A ranking with no stated criterion cannot be audited, because "
                f"every order is consistent with an unstated rule."
            ),
        )
    if raw in CRITERIA:
        return raw, Gate(
            name="criterion",
            code=CRITERION_ACCEPTED,
            ok=True,
            detail=f"{raw} is a member of the closed criterion set",
        )
    return CRITERION_MAX_INCREMENTAL, Gate(
        name="criterion",
        code=CRITERION_REJECTED_UNKNOWN,
        ok=False,
        detail=(
            f"the agent stated criterion {raw!r}, which is not one of "
            f"{', '.join(CRITERIA)}; {CRITERION_MAX_INCREMENTAL} is recorded in its place"
        ),
        evidence=(raw,),
    )


def resolve_choice(raw: str, candidate_ids: Sequence[str]) -> tuple[str | None, Gate]:
    """The chosen instrument must be one the Card Member's mandate authorised."""
    if not raw:
        return None, Gate(
            name="choice",
            code=CHOICE_MISSING,
            ok=False,
            detail="the agent recommended no instrument",
        )
    if raw in candidate_ids:
        return raw, Gate(
            name="choice", code=CHOICE_ACCEPTED, ok=True, detail=f"{raw} is in the candidate set"
        )
    copy = marketing.copy_for(raw)
    if copy is not None and copy.instrument_id in candidate_ids:
        return copy.instrument_id, Gate(
            name="choice",
            code=CHOICE_ACCEPTED,
            ok=True,
            detail=f"{raw!r} resolves to {copy.instrument_id}, which is in the candidate set",
        )
    return None, Gate(
        name="choice",
        code=CHOICE_REJECTED_UNKNOWN,
        ok=False,
        detail=(
            f"the agent named {raw!r}, which is not one of the instruments the mandate "
            f"authorised ({', '.join(sorted(candidate_ids))})"
        ),
        evidence=(raw,),
    )


def check_narrative(text: str, allowed: frozenset[int]) -> Gate:
    """Re-derive every money figure in the agent's prose. One miss withholds the lot.

    Withholding the whole rationale rather than redacting the offending figure is
    deliberate. A sentence with a number cut out of it still argues for the number, and a
    receipt carrying an argument the engine could not check is exactly the artifact this
    system exists to make unnecessary.
    """
    if not text:
        return Gate(
            name="narrative", code=NARRATIVE_MISSING, ok=False, detail="the agent wrote nothing"
        )
    figures = money_figures(text)
    if not figures:
        return Gate(
            name="narrative",
            code=NARRATIVE_ACCEPTED_NO_FIGURES,
            ok=True,
            detail="the rationale quotes no money figure, so there is nothing to reconcile",
        )
    unverified = tuple(raw for raw, minor in figures if minor not in allowed)
    if unverified:
        return Gate(
            name="narrative",
            code=NARRATIVE_REJECTED,
            ok=False,
            detail=(
                f"{len(unverified)} of {len(figures)} money figure(s) in the agent's prose "
                f"cannot be re-derived from the engine's output for this cart"
            ),
            evidence=unverified,
        )
    return Gate(
        name="narrative",
        code=NARRATIVE_ACCEPTED,
        ok=True,
        detail=(
            f"all {len(figures)} money figure(s) in the agent's prose re-derive from the "
            f"engine's output"
        ),
        evidence=tuple(raw for raw, _ in figures),
    )


@dataclass(frozen=True)
class ClaimProbe:
    """What happened when the model's proposed value was tested against a witness.

    Run as a SEPARATE evaluation whose result is reported and never embedded in a receipt.
    A proposed number is evidence about the proposer; it is not a fact about the cart, and
    it does not get to travel inside a signed artifact even as a rejected footnote.
    """

    code: str
    claimed_minor: int | None
    realized_minor: int | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "claimed_minor": self.claimed_minor,
            "realized_minor": self.realized_minor,
            "detail": self.detail,
        }


def probe_claim(
    *,
    claimed_display: str,
    instrument_id: str | None,
    cart: Cart,
    manifests: Sequence[SignedManifest],
    as_of: int,
    currency: str,
) -> ClaimProbe:
    if not claimed_display or instrument_id is None:
        return ClaimProbe(
            code=CLAIM_NONE,
            claimed_minor=None,
            realized_minor=None,
            detail="the agent proposed no value, so there was no hypothesis to test",
        )
    claimed = parse_money_minor(claimed_display)
    if claimed is None:
        return ClaimProbe(
            code=CLAIM_UNPARSEABLE,
            claimed_minor=None,
            realized_minor=None,
            detail=(
                f"the agent proposed {claimed_display!r}, which carries no parseable "
                f"currency figure; it is not guessed at"
            ),
        )
    probe = evaluate(
        cart=cart,
        manifests=manifests,
        now=as_of,
        keys={ISSUER_KEY_ID: ISSUER_KEY},
        claims={instrument_id: claimed},
    )
    v = probe.valuation(instrument_id)
    realized = (
        v.verification.realized_minor if v is not None and v.verification is not None else None
    )
    refused = [r for r in (v.refusals if v is not None else ()) if r.code == CLAIM_REFUSED]
    if refused:
        return ClaimProbe(
            code=CLAIM_REFUSED,
            claimed_minor=claimed,
            realized_minor=realized,
            detail=refused[0].detail,
        )
    return ClaimProbe(
        code=CLAIM_SUPPORTED,
        claimed_minor=claimed,
        realized_minor=realized,
        detail=(
            f"the agent proposed {fmt_currency(claimed, currency)} and the exhibited "
            f"allocation realizes {fmt_currency(realized or 0, currency)}, so the claim is "
            f"backed by a witness"
        ),
    )


def check_engine_agreement(
    mcp_payload: Mapping[str, Any] | None, evaluation: Evaluation
) -> Gate:
    """Do the numbers the agent read over MCP match the numbers the receipt signs?

    They are produced by two separate `evaluate()` calls — one behind the transport, one
    here in the signing path. If they ever diverged, the agent would be reasoning about a
    document nobody signed.
    """
    if not mcp_payload:
        return Gate(
            name="engine_agreement",
            code=AGREEMENT_NOT_CHECKED,
            ok=True,
            detail=(
                "this run read no valuation over MCP, so there is nothing to reconcile "
                "against the signing engine"
            ),
        )
    engine = {c.manifest_id: c.asserted_minor for c in evaluation.candidates}
    channel = {
        str(i.get("instrument_id")): i.get("asserted_value_minor")
        for i in mcp_payload.get("instruments", [])
    }
    mismatches = tuple(
        f"{k}: MCP {channel.get(k)!r} vs engine {v!r}"
        for k, v in sorted(engine.items())
        if channel.get(k) != v
    )
    if mismatches:
        return Gate(
            name="engine_agreement",
            code=AGREEMENT_DIVERGED,
            ok=False,
            detail="the MCP channel and the signing engine disagree",
            evidence=mismatches,
        )
    return Gate(
        name="engine_agreement",
        code=AGREEMENT_OK,
        ok=True,
        detail=(
            f"all {len(engine)} instrument value(s) the agent read over MCP equal the values "
            f"the receipt signs"
        ),
    )


# --------------------------------------------------------------------------------------
# The signing path — deterministic, and never fed by the model
# --------------------------------------------------------------------------------------


def authoritative_evaluation(
    *, cart: Cart, manifests: Sequence[SignedManifest], criterion: str, as_of: int
) -> Evaluation:
    """The evaluation a receipt is built from. Computed here, not received over a wire."""
    return evaluate(
        cart=cart,
        manifests=manifests,
        now=as_of,
        policy=ValuationPolicy(criterion=criterion, policy_id="plumbline/selector/cardholder/1"),
        keys={ISSUER_KEY_ID: ISSUER_KEY},
    )


def agent_disclosures(
    *, mode: str, criterion_gate: Gate, narrative_gate: Gate, rationale: str, transport: str
) -> tuple[str, ...]:
    """Turn the gate results into receipt disclosure lines.

    The agent's prose reaches the receipt through here and nowhere else, and only when
    `check_narrative` passed. When it did not, the receipt carries the refusal instead of the
    sentence, which is a strictly more useful record than silence.
    """
    lines = [
        BOUNDARY_DISCLOSURE,
        f"Agent run mode: {mode}. Value tools reached over: {transport}.",
        f"Agent stated criterion gate: {criterion_gate.code} — {criterion_gate.detail}",
    ]
    if narrative_gate.ok and rationale:
        lines.append(f"Agent stated rationale (figures reconciled): {rationale}")
    else:
        lines.append(
            WITHHELD_NARRATIVE.format(
                code=narrative_gate.code,
                figures=", ".join(narrative_gate.evidence) or "(none named)",
            )
        )
    return tuple(lines)


@dataclass(frozen=True)
class ReceiptBundle:
    signed: SignedReceipt
    anchored: AnchoredReceipt
    verification: ReceiptVerification

    @property
    def attestation_outcome(self) -> str:
        return self.signed.receipt.attestation.outcome

    @property
    def faithful(self) -> bool:
        return self.attestation_outcome == ATTEST_FAITHFUL

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt": self.signed.to_dict(),
            "receipt_hash": self.signed.receipt.receipt_hash(),
            "attestation_outcome": self.attestation_outcome,
            "faithful": self.faithful,
            "verification": self.verification.to_dict(),
            "log": {
                "seq": self.anchored.seq,
                "root": self.anchored.sth.root_hash,
                "tree_size": self.anchored.sth.tree_size,
                "inclusion_ok": self.anchored.verify_anchor(),
            },
        }


def build_bundle(
    *,
    mode: str,
    cart: Cart,
    cart_id: str,
    evaluation: Evaluation,
    manifests: Mapping[str, SignedManifest],
    chosen_instrument_id: str | None,
    disclosures: Sequence[str],
    as_of: int,
    log: TransparencyLog,
) -> ReceiptBundle:
    """Assemble, sign, anchor and independently re-verify one Decision Receipt."""
    session = CheckoutSession.of(f"sess_selector_{mode}", cart, initiated_at=as_of)
    mandate = MandateBinding(
        mandate_id=f"mnd_selector_{cart_id}",
        authorized_instrument_ids=tuple(sorted(manifests)),
        disclosure_caveat=True,
    )
    receipt = build_receipt_from_evaluation(
        receipt_id=f"rcpt_selector_{mode}_{as_of}",
        issued_at=as_of,
        evaluation=evaluation,
        session=session,
        mandate=mandate,
        agent=Identity(
            kind="agent",
            identifier="agt_plumbline_selector",
            name=f"PLUMBLINE instrument selector ({MODEL})",
        ),
        platform=Identity(
            kind="platform", identifier="mcp://plumbline", name="PLUMBLINE MCP value server"
        ),
        signed_manifests=dict(manifests),
        chosen_instrument_id=chosen_instrument_id,
        posture=POSTURE_OBSERVE_ONLY,
        disclosures=(*DEFAULT_DISCLOSURES, *disclosures),
    )
    signed = sign_receipt(receipt, key=AGENT_KEY, signer_role=ROLE_AGENT)
    anchored = anchor_receipt(log, signed, timestamp=as_of, key=LOG_KEY)
    verification = verify_receipt(
        signed,
        AGENT_KEY,
        cart=cart,
        manifests=dict(manifests),
        issuer_keys={ISSUER_KEY_ID: ISSUER_KEY},
        witnesses={
            c.manifest_id: c.witness for c in evaluation.candidates if c.witness is not None
        },
    )
    return ReceiptBundle(signed=signed, anchored=anchored, verification=verification)


# --------------------------------------------------------------------------------------
# One run
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RunResult:
    mode: str
    source: str
    transport: str
    model: str
    cart_id: str
    tool_calls: tuple[ToolCall, ...]
    narration: tuple[str, ...]
    recommendation: Recommendation | None
    criterion: str
    chosen_instrument_id: str | None
    gates: tuple[Gate, ...]
    claim: ClaimProbe
    bundle: ReceiptBundle
    # The authoritative evaluation this run's receipt was built from. Carried so a caller
    # can re-derive every figure without re-running the engine, never so it can be edited.
    evaluation: Evaluation = field(repr=False)
    error: str | None = None

    @property
    def engine_choice(self) -> str | None:
        ranking = self.evaluation.ranking
        return ranking.chosen_manifest_id if ranking is not None else None

    @property
    def chosen_value_minor(self) -> int | None:
        if self.chosen_instrument_id is None:
            return None
        v = self.evaluation.valuation(self.chosen_instrument_id)
        return v.asserted_minor if v is not None else None

    @property
    def best_value_minor(self) -> int | None:
        ranking = self.evaluation.ranking
        return ranking.entries[0].asserted_minor if ranking and ranking.entries else None

    def foregone_minor(self) -> int:
        """What the recommendation left on the table, per the engine's own numbers."""
        best = self.best_value_minor
        chosen = self.chosen_value_minor
        if best is None or chosen is None:
            return 0
        return max(0, best - chosen)

    def gate(self, name: str) -> Gate | None:
        for g in self.gates:
            if g.name == name:
                return g
        return None

    def headline(self) -> str:
        if self.error:
            return f"ERROR / {self.error}"
        if self.chosen_instrument_id is None:
            return "no instrument recommended"
        return f"{self.chosen_instrument_id} / {self.bundle.attestation_outcome}"

    def to_dict(self) -> dict[str, Any]:
        currency = self.evaluation.cart.currency
        return {
            "mode": self.mode,
            "source": self.source,
            "transport": self.transport,
            "model": self.model,
            "cart_id": self.cart_id,
            "tool_calls": [c.to_dict() for c in self.tool_calls],
            "tool_call_signatures": [c.signature() for c in self.tool_calls],
            "narration": list(self.narration),
            "recommendation": (
                self.recommendation.to_dict() if self.recommendation is not None else None
            ),
            "criterion": self.criterion,
            "chosen_instrument_id": self.chosen_instrument_id,
            "engine_choice": self.engine_choice,
            "chosen_value_minor": self.chosen_value_minor,
            "chosen_value_display": (
                None
                if self.chosen_value_minor is None
                else fmt_currency(self.chosen_value_minor, currency)
            ),
            "best_value_minor": self.best_value_minor,
            "best_value_display": (
                None
                if self.best_value_minor is None
                else fmt_currency(self.best_value_minor, currency)
            ),
            "foregone_minor": self.foregone_minor(),
            "foregone_display": fmt_currency(self.foregone_minor(), currency),
            "gates": [g.to_dict() for g in self.gates],
            "claim_probe": self.claim.to_dict(),
            "receipt": self.bundle.to_dict(),
            "headline": self.headline(),
            "error": self.error,
        }


def finish_run(
    *,
    session: SelectionSession,
    source: str,
    transport: str,
    model: str,
    narration: Sequence[str],
    cart: Cart,
    manifests: Mapping[str, SignedManifest],
    as_of: int,
    log: TransparencyLog,
    error: str | None = None,
) -> RunResult:
    """Everything after the model stops talking. Entirely deterministic from here down."""
    rec = session.recommendation
    candidate_ids = tuple(sorted(manifests))

    criterion, criterion_gate = resolve_criterion(rec.criterion if rec else "")
    chosen, choice_gate = resolve_choice(rec.instrument_id if rec else "", candidate_ids)

    evaluation = authoritative_evaluation(
        cart=cart, manifests=tuple(manifests.values()), criterion=criterion, as_of=as_of
    )
    narrative_gate = check_narrative(rec.rationale if rec else "", engine_figures(evaluation))
    agreement_gate = check_engine_agreement(session.mcp_valuation, evaluation)

    claim = probe_claim(
        claimed_display=rec.claimed_value_display if rec else "",
        instrument_id=chosen,
        cart=cart,
        manifests=tuple(manifests.values()),
        as_of=as_of,
        currency=cart.currency,
    )

    bundle = build_bundle(
        mode=session.mode,
        cart=cart,
        cart_id=session.cart_id,
        evaluation=evaluation,
        manifests=manifests,
        chosen_instrument_id=chosen,
        disclosures=agent_disclosures(
            mode=session.mode,
            criterion_gate=criterion_gate,
            narrative_gate=narrative_gate,
            rationale=rec.rationale if rec else "",
            transport=transport if session.mode == MODE_DERIVED else "no value tools",
        ),
        as_of=as_of,
        log=log,
    )

    return RunResult(
        mode=session.mode,
        source=source,
        transport=transport if session.mode == MODE_DERIVED else "none",
        model=model,
        cart_id=session.cart_id,
        tool_calls=tuple(session.calls),
        narration=tuple(narration),
        recommendation=rec,
        criterion=criterion,
        chosen_instrument_id=chosen,
        gates=(criterion_gate, choice_gate, narrative_gate, agreement_gate),
        claim=claim,
        bundle=bundle,
        evaluation=evaluation,
        error=error,
    )


def run_recorded(
    *,
    mode: str,
    cart_id: str,
    cart: Cart,
    manifests: Mapping[str, SignedManifest],
    tool_calls: Sequence[Mapping[str, Any]],
    narration: Sequence[str],
    client: InProcessMcpClient | StdioMcpClient | None,
    as_of: int,
    log: TransparencyLog,
    model: str = MODEL,
) -> RunResult:
    """Drive a recorded tool-call sequence through the live tool implementations.

    Nothing is faked except the model's choices. The MCP server, the allocator, the verifier
    and the receipt path are the real objects a live run would have used, so a replayed
    number is computed now rather than read out of the trace.
    """
    session = SelectionSession(mode=mode, cart_id=cart_id, client=client, as_of=as_of)
    for call in tool_calls:
        session.execute(str(call.get("tool", "")), dict(call.get("input", {}) or {}))
    return finish_run(
        session=session,
        source=SOURCE_REPLAY,
        transport=client.transport if client is not None else "none",
        model=model,
        narration=narration,
        cart=cart,
        manifests=manifests,
        as_of=as_of,
        log=log,
    )


# --------------------------------------------------------------------------------------
# Live
# --------------------------------------------------------------------------------------


def _client() -> Any:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise LiveRunError(
            "--live needs Anthropic credentials: export ANTHROPIC_API_KEY (or "
            "ANTHROPIC_AUTH_TOKEN). The demo path does not need them — run without --live "
            "to replay the recorded trace."
        )
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise LiveRunError(f"anthropic SDK is not importable: {exc}") from exc
    return anthropic.Anthropic()


def run_live(
    *,
    mode: str,
    cart_id: str,
    cart: Cart,
    manifests: Mapping[str, SignedManifest],
    mcp_client: InProcessMcpClient | StdioMcpClient | None,
    as_of: int,
    log: TransparencyLog,
    api: Any | None = None,
    max_turns: int = MAX_TURNS,
) -> RunResult:
    """Manual tool-use loop, so the whole trace is inspectable.

    No temperature / top_p / top_k: claude-opus-5 rejects them. `stop_reason == "refusal"` is
    checked before content is read, because on a refusal there is no content to read.
    """
    api = api or _client()
    session = SelectionSession(mode=mode, cart_id=cart_id, client=mcp_client, as_of=as_of)
    narration: list[str] = []
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": task_for(mode, cart_id, cart)}
    ]
    error: str | None = None

    for _ in range(max_turns):
        response = api.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt_for(mode),
            tools=tools_for(mode),
            messages=messages,
        )

        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            error = f"model refused ({getattr(detail, 'category', None)})"
            break

        messages.append({"role": "assistant", "content": response.content})
        for block in response.content:
            if getattr(block, "type", None) == "text" and block.text.strip():
                narration.append(block.text.strip())

        if response.stop_reason != "tool_use":
            break

        results: list[dict[str, Any]] = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            result = session.execute(block.name, dict(block.input or {}))
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, ensure_ascii=False),
                    "is_error": not result.get("ok", True),
                }
            )
        # All results for one assistant turn go back in a single user message.
        messages.append({"role": "user", "content": results})
    else:
        error = f"agent did not finish within {max_turns} turns"

    return finish_run(
        session=session,
        source=SOURCE_LIVE,
        transport=mcp_client.transport if mcp_client is not None else "none",
        model=MODEL,
        narration=narration,
        cart=cart,
        manifests=manifests,
        as_of=as_of,
        log=log,
        error=error,
    )


# --------------------------------------------------------------------------------------
# Traces
# --------------------------------------------------------------------------------------

AUTHORED_PROVENANCE = (
    "authored fixture — NOT a live claude-opus-5 transcript. Re-record with "
    "`python -m agent.selector --live --record`."
)


def trace_path(scenario: str, trace_dir: Path | None = None) -> Path:
    return (trace_dir or TRACE_DIR) / f"selector_{scenario}.json"


def load_trace(scenario: str, trace_dir: Path | None = None) -> dict[str, Any]:
    path = trace_path(scenario, trace_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"no recorded trace at {path}. Record one with "
            f"`python -m agent.selector --scenario {scenario} --live --record`."
        )
    blob = json.loads(path.read_text(encoding="utf-8"))
    for mode in MODES:
        if mode not in blob.get("runs", {}):
            raise ValueError(f"{path.name} carries no {mode!r} run; re-record it")
    return blob


def write_trace(
    scenario: str,
    pair: "PairResult",
    *,
    trace_dir: Path | None = None,
    provenance: str | None = None,
) -> Path:
    """Persist a run pair as a replayable trace.

    `provenance` is written verbatim so a reader can tell a real claude-opus-5 transcript
    from an authored one. Never label an authored trace as live.
    """
    path = trace_path(scenario, trace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = {
        "scenario": scenario,
        "cart_id": pair.cart_id,
        "model": pair.derived.model,
        "provenance": provenance or f"{pair.source}-{pair.derived.model}",
        "recorded_at": pair.as_of,
        "runs": {
            mode: {
                "narration": list(run.narration),
                "tool_calls": [c.to_trace_dict() for c in run.tool_calls],
            }
            for mode, run in ((MODE_CONTROL, pair.control), (MODE_DERIVED, pair.derived))
        },
    }
    path.write_text(json.dumps(blob, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def recorded_calls(trace: Mapping[str, Any], mode: str) -> list[dict[str, Any]]:
    return list(trace.get("runs", {}).get(mode, {}).get("tool_calls", []))


def recorded_narration(trace: Mapping[str, Any], mode: str) -> list[str]:
    return list(trace.get("runs", {}).get(mode, {}).get("narration", []))


# --------------------------------------------------------------------------------------
# The pair
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PairResult:
    scenario: str
    cart_id: str
    cart: Cart
    control: RunResult
    derived: RunResult
    source: str
    transport: str
    as_of: int
    log: TransparencyLog

    def same_choice(self) -> bool:
        return self.control.chosen_instrument_id == self.derived.chosen_instrument_id

    def guess_was_wrong(self) -> bool:
        return (
            self.control.chosen_instrument_id is not None
            and self.control.chosen_instrument_id != self.derived.engine_choice
        )

    def cost_of_guessing_minor(self) -> int:
        return self.control.foregone_minor()

    def to_dict(self) -> dict[str, Any]:
        currency = self.cart.currency
        return {
            "scenario": self.scenario,
            "cart_id": self.cart_id,
            "cart": self.cart.to_dict(),
            "cart_total_minor": self.cart.total(),
            "cart_total_display": fmt_currency(self.cart.total(), currency),
            "source": self.source,
            "transport": self.transport,
            "as_of": self.as_of,
            "mcp": {"sdk_status": sdk_status(), "tools": [s["name"] for s in TOOL_SPECS]},
            "control": self.control.to_dict(),
            "derived": self.derived.to_dict(),
            "same_choice": self.same_choice(),
            "guess_was_wrong": self.guess_was_wrong(),
            "cost_of_guessing_minor": self.cost_of_guessing_minor(),
            "cost_of_guessing_display": fmt_currency(self.cost_of_guessing_minor(), currency),
            "log_root": self.log.root(),
            "log_size": len(self.log),
        }


def run_pair(
    *,
    scenario: str = CART_EVERYDAY,
    cart_id: str | None = None,
    as_of: int = DEFAULT_AS_OF,
    live: bool = False,
    record: bool = False,
    transport: str = TRANSPORT_INPROC,
    trace_dir: Path | None = None,
) -> PairResult:
    """Run the same agent twice on the same basket — guessing, then deriving."""
    cart_key = cart_id or scenario
    if cart_key not in DEMO_CARTS:
        raise ValueError(
            f"no cart named {cart_key!r}; known: {', '.join(sorted(DEMO_CARTS))}"
        )
    cart = DEMO_CARTS[cart_key]
    manifests = {
        mid: sm
        for mid, sm in signed_by_id(as_of).items()
        if sm.manifest.currency == cart.currency
    }
    log = TransparencyLog(LOG_ID, signing_key=LOG_KEY)
    mcp_client = build_client(transport)

    try:
        if live:
            api = _client()
            control = run_live(
                mode=MODE_CONTROL,
                cart_id=cart_key,
                cart=cart,
                manifests=manifests,
                mcp_client=None,
                as_of=as_of,
                log=log,
                api=api,
            )
            derived = run_live(
                mode=MODE_DERIVED,
                cart_id=cart_key,
                cart=cart,
                manifests=manifests,
                mcp_client=mcp_client,
                as_of=as_of,
                log=log,
                api=api,
            )
            source = SOURCE_LIVE
        else:
            trace = load_trace(scenario, trace_dir)
            model = str(trace.get("model", MODEL))
            control = run_recorded(
                mode=MODE_CONTROL,
                cart_id=cart_key,
                cart=cart,
                manifests=manifests,
                tool_calls=recorded_calls(trace, MODE_CONTROL),
                narration=recorded_narration(trace, MODE_CONTROL),
                client=None,
                as_of=as_of,
                log=log,
                model=model,
            )
            derived = run_recorded(
                mode=MODE_DERIVED,
                cart_id=cart_key,
                cart=cart,
                manifests=manifests,
                tool_calls=recorded_calls(trace, MODE_DERIVED),
                narration=recorded_narration(trace, MODE_DERIVED),
                client=mcp_client,
                as_of=as_of,
                log=log,
                model=model,
            )
            source = SOURCE_REPLAY
    finally:
        mcp_client.close()

    pair = PairResult(
        scenario=scenario,
        cart_id=cart_key,
        cart=cart,
        control=control,
        derived=derived,
        source=source,
        transport=transport,
        as_of=as_of,
        log=log,
    )
    if record:
        write_trace(scenario, pair, trace_dir=trace_dir)
    return pair


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------

_COL = 56


def _use_colour() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


class _C:
    def __init__(self, on: bool) -> None:
        self.on = on

    def _w(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.on else text

    def red(self, t: str) -> str:
        return self._w("31;1", t)

    def green(self, t: str) -> str:
        return self._w("32;1", t)

    def yellow(self, t: str) -> str:
        return self._w("33", t)

    def dim(self, t: str) -> str:
        return self._w("2", t)

    def bold(self, t: str) -> str:
        return self._w("1", t)


def _wrap(text: str, width: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            while len(word) > width:
                lines.append(word[:width])
                word = word[width:]
            current = word
    if current:
        lines.append(current)
    return lines


def _visible_len(text: str) -> int:
    out, i = 0, 0
    while i < len(text):
        if text[i] == "\033":
            j = text.find("m", i)
            i = len(text) if j == -1 else j + 1
            continue
        out += 1
        i += 1
    return out


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _visible_len(text))


def _pane(run: RunResult, engine_choice: str | None, c: _C) -> list[str]:
    currency = run.evaluation.cart.currency
    out: list[str] = [c.dim("tool calls")]
    for call in run.tool_calls:
        for i, chunk in enumerate(_wrap(call.signature(), _COL - 4)):
            out.append(("  " if i == 0 else "    ") + chunk)
    out.append("")

    out.append(c.dim("what the agent saw"))
    if run.mode == MODE_DERIVED:
        out.append("  witness-backed value per instrument,")
        out.append("  each with its line-item derivation")
    else:
        for chunk in _wrap(marketing.MARKETING_SIGNAL, _COL - 4):
            out.append(f"  {chunk}")
    out.append("")

    out.append(c.dim("recommendation"))
    chosen = run.chosen_instrument_id or "(none)"
    right = chosen == engine_choice
    out.append(f"  {(c.green if right else c.red)(chosen)}")
    out.append(f"  criterion {run.criterion}")
    value = run.chosen_value_minor
    if value is not None:
        out.append(f"  engine value on that card {c.bold(fmt_currency(value, currency))}")
    if run.foregone_minor():
        out.append(
            f"  {c.red('leaves ' + fmt_currency(run.foregone_minor(), currency) + ' on the table')}"
        )
    out.append("")

    out.append(c.dim("deterministic gates on model output"))
    for g in run.gates:
        mark = c.green("pass") if g.ok else c.red("FAIL")
        chunks = _wrap(g.code, _COL - 11)
        out.append(f"  [{mark}] {chunks[0]}")
        out.extend(f"         {chunk}" for chunk in chunks[1:])
        for ev in g.evidence[:3]:
            out.append(f"         {c.yellow(ev[: _COL - 11])}")
    probe = _wrap(run.claim.code, _COL - 12)
    out.append(f"  [{c.dim('probe')}] {probe[0]}")
    out.extend(f"          {chunk}" for chunk in probe[1:])
    out.append("")

    out.append(c.dim("decision receipt"))
    b = run.bundle
    paint = c.green if b.faithful else c.red
    for chunk in _wrap(b.attestation_outcome, _COL - 4):
        out.append(f"  {paint(chunk)}")
    out.append(f"  candidates {len(b.signed.receipt.candidates)}  (full set, not the winner)")
    for check in b.verification.checks:
        if not check.ok:
            for chunk in _wrap(f"check failed: {check.name}", _COL - 4):
                out.append(f"  {c.red(chunk)}")
    out.append(f"  receipt  {b.signed.receipt.receipt_hash()[:24]}…")
    out.append(f"  log seq {b.anchored.seq}  inclusion {b.anchored.verify_anchor()}")

    if run.narration:
        out.append("")
        out.append(c.dim("agent's summary to the Card Member"))
        for chunk in _wrap(run.narration[-1], _COL - 4):
            out.append(f"  {c.yellow(chunk)}")
    if run.error:
        out.append(f"  {c.red('error: ' + run.error)}")
    return out


def render_pair(pair: PairResult, colour: bool | None = None) -> str:
    c = _C(_use_colour() if colour is None else colour)
    width = _COL * 2 + 3
    currency = pair.cart.currency
    engine_choice = pair.derived.engine_choice

    out: list[str] = [
        c.bold("PLUMBLINE — the agent cannot see issuer-side value. Watch."),
        "=" * width,
        f"cart      {pair.cart_id}  ·  {len(pair.cart.lines)} lines  ·  "
        f"{fmt_currency(pair.cart.total(), currency)}  ·  {pair.cart.merchant}",
        f"model     {pair.derived.model}    source  {pair.source}    "
        f"transport  {pair.transport}",
        f"mcp       {sdk_status()}",
        f"tools     {', '.join(s['name'] for s in TOOL_SPECS)}",
        "=" * width,
        "",
    ]

    out.append(
        f"{_pad(c.red('CONTROL — marketing copy only'), _COL)} | "
        f"{c.green('DERIVED — PLUMBLINE over MCP')}"
    )
    out.append(f"{'-' * _COL}-+-{'-' * _COL}")
    left = _pane(pair.control, engine_choice, c)
    right = _pane(pair.derived, engine_choice, c)
    for i in range(max(len(left), len(right))):
        l = left[i] if i < len(left) else ""
        r = right[i] if i < len(right) else ""
        out.append(f"{_pad(l, _COL)} | {r}".rstrip())

    out += ["", "=" * width, c.bold("THE ENGINE'S ANSWER — deterministic, witness-backed")]
    ranking = pair.derived.evaluation.ranking
    if ranking is not None:
        for e in ranking.entries:
            mark = "  "
            if e.manifest_id == pair.control.chosen_instrument_id:
                mark = c.red("C ")
            if e.manifest_id == pair.derived.chosen_instrument_id:
                mark = c.green("D ")
            out.append(
                f"  {mark}{e.rank}. {e.manifest_id:<30}"
                f"{fmt_currency(e.asserted_minor, currency):>12}"
            )
        out.append(
            f"     policy {ranking.policy_hash[:16]}…  criterion {ranking.criterion}  "
            f"issuer_endorsed={ranking.issuer_endorsed}"
        )
    out.append("=" * width)

    if pair.guess_was_wrong():
        out.append(
            f"{c.red('GUESSING')}   the agent reading marketing copy chose "
            f"{pair.control.chosen_instrument_id}. On this basket that is "
            f"{c.bold(fmt_currency(pair.cost_of_guessing_minor(), currency))} of value left "
            f"on the table, and its receipt attests "
            f"{c.red(pair.control.bundle.attestation_outcome)}."
        )
    else:
        out.append(
            "GUESSING   the marketing-copy agent happened to choose the same instrument the "
            "engine ranks first."
        )
    out.append(
        f"{c.green('DERIVING')}   the same agent with the MCP tools chose "
        f"{pair.derived.chosen_instrument_id}, quoting the engine's own figures, and its "
        f"receipt attests {c.green(pair.derived.bundle.attestation_outcome)}."
    )
    refused = [g for r in (pair.control, pair.derived) for g in r.gates if not g.ok]
    out.append(
        f"{c.bold('BOUNDARY')}   the agent computed nothing in either run. "
        f"{len(refused)} deterministic gate(s) refused model output "
        f"({', '.join(g.code for g in refused) or 'none'}); every number above was produced "
        f"by the allocator and re-checked by the witness verifier."
    )
    out.append(
        f"LOG        root {pair.log.root()[:16]}…  {len(pair.log)} entries  "
        f"({pair.control.bundle.anchored.seq}, {pair.derived.bundle.anchored.seq} anchored)"
    )
    out.append("=" * width)
    return "\n".join(out)


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agent.selector",
        description=(
            "Run the instrument selector twice on one basket: once guessing from marketing "
            "copy, once deriving over the PLUMBLINE MCP server."
        ),
    )
    parser.add_argument("--scenario", default=CART_EVERYDAY, help="trace / cart to run")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--replay", action="store_true", help="replay the recorded trace (default)")
    group.add_argument("--live", action="store_true", help="call claude-opus-5 for real")
    parser.add_argument("--record", action="store_true", help="write the run to agent/traces/")
    parser.add_argument(
        "--transport",
        default=TRANSPORT_INPROC,
        choices=list(TRANSPORTS),
        help="how the derived run reaches the MCP tools",
    )
    parser.add_argument(
        "--as-of", type=int, default=DEFAULT_AS_OF, help="decision clock, so runs replay"
    )
    parser.add_argument("--json", action="store_true", help="emit the structured result")
    parser.add_argument("--receipt", action="store_true", help="print the derived run's receipt")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args(argv)

    if args.record and not args.live:
        # Recording a replay would relabel an authored trace as if a model produced it.
        print("--record requires --live; a replay has nothing new to record", file=sys.stderr)
        return 2

    try:
        pair = run_pair(
            scenario=args.scenario,
            as_of=args.as_of,
            live=args.live,
            record=args.record,
            transport=args.transport,
        )
    except LiveRunError as exc:
        print(f"live run unavailable: {exc}", file=sys.stderr)
        return 2
    except (FileNotFoundError, ValueError) as exc:
        print(f"run unavailable: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(pair.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render_pair(pair, colour=False if args.no_color else None))
        if args.receipt:
            print()
            print(pair.derived.bundle.signed.receipt.render_text())

    if args.record:
        print(f"\ntrace written to {trace_path(args.scenario)}", file=sys.stderr)

    # Non-zero if the deriving run failed its own attestation. The demo is also a gate.
    return 0 if pair.derived.bundle.faithful else 1


if __name__ == "__main__":
    raise SystemExit(main())
