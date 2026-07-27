"""The manifest authoring agent — the LLM half of "propose, then dispose".

Onboarding a card product today means a human reading forty pages of terms and hand-writing
a manifest. That is the adoption bottleneck for the whole disclosure rail: a schema nobody
can populate is a schema nobody adopts. So a model reads the terms and drafts the manifest.

Then we refuse to trust a single number it wrote.

    The LLM proposes; the deterministic engine disposes.

Every draft goes to :mod:`plumbline.authoring`, which accepts or rejects it with typed reason
codes. The model sees the codes and revises, bounded. It never signs anything. It never
holds, sees or is told the signing key — the key is an argument to the run functions, never
reaches :class:`AuthoringSession`, the signature is produced by :func:`sign_if_accepted`
after the loop has ended, and no tool result in this module ever contains either.

**A visible rejection is the feature.** The demo is not "the model wrote a manifest"; the
demo is "the model wrote a manifest, the validator threw it out with reasons, the model
fixed exactly those things, and only then did anything get signed." An authoring loop with
no rejection in it proves nothing about who is trusted with the numbers.

Replay, as everywhere in this repo: ``--replay`` (the default) drives recorded drafts
through the *identical* validator, so a run with no API key produces the same verdicts,
the same reason codes and the same signature as a live one. ``--live`` calls claude-opus-5
to show the loop is real. Recorded verdicts are carried for inspection only — replay
re-validates rather than trusting them.

What this does NOT do, stated here because it is the easiest thing in the repo to
overclaim: the validator checks that a draft is internally consistent and structurally
sound. It cannot check that a rate is the card's real rate. Nothing in this pipeline
verifies a manifest against a real issuer's published terms, and accepted manifests carry
that limitation inside their signed bytes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from plumbline.authoring import (
    DRAFT_VERSION,
    UNVERIFIED_AGAINST_SOURCE,
    AcceptedDraft,
    AuthoringError,
    ValidationReport,
    draft_schema,
    reason_catalogue,
    schema_help,
    sign_accepted,
    validate_draft,
)
from plumbline.manifest import SignedManifest

MODEL = "claude-opus-5"
MAX_TOKENS = 8192
MAX_TURNS = 14
MAX_ATTEMPTS = 4

TRACE_DIR = Path(__file__).resolve().parent / "traces"
TRACE_PREFIX = "authoring_"

SOURCE_LIVE = "live"
SOURCE_REPLAY = "replay"

TOOL_SUBMIT = "submit_draft"
TOOL_SCHEMA = "get_schema"

# Prototype key, held by the controller and by nothing else in this module's reach. The
# model is never shown it, never asked about it, and no tool result carries it. Production
# signs with the issuer's HSM key; the gate above it is unchanged either way.
PROTOTYPE_SIGNING_KEY = "plumbline-prototype-issuer-key"
PROTOTYPE_KEY_ID = "prototype-authoring"

# Findings returned to the model per rejection. Bounded so one malformed draft cannot fill
# the context window with a thousand identical type errors. Truncation is deterministic:
# the first N in validator order, plus a count of what was elided.
MAX_FINDINGS_RETURNED = 12


class LiveRunError(RuntimeError):
    """Raised when the live path cannot run. Never raised on the replay path."""


# --------------------------------------------------------------------------------------
# Terms documents. Fictional issuers, deliberately: a demo that misquotes a real card's
# published terms hands a judge who recognises the term a reason to disbelieve everything
# else. The loop is the artifact, not the card.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TermsDocument:
    terms_id: str
    issuer: str
    product: str
    currency: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "terms_id": self.terms_id,
            "issuer": self.issuer,
            "product": self.product,
            "currency": self.currency,
            "text": self.text,
        }


MERIDIAN_VANTAGE = TermsDocument(
    terms_id="meridian_vantage",
    issuer="Meridian Bank",
    product="Vantage Card",
    currency="USD",
    text="""MERIDIAN BANK VANTAGE CARD — SUMMARY OF REWARDS AND BENEFITS
Effective 15 January 2026. Fictional product, written for a schema demonstration.

2. EARNING REWARDS
2.1 Dining. Cardmembers earn 4 points per dollar on eligible purchases at restaurants
    (merchant category codes 5812 and 5814), on the first $50,000 of such purchases each
    calendar year, and 1 point per dollar thereafter.
2.2 Travel. Cardmembers earn 3 points per dollar on eligible airfare purchased directly
    from airlines (merchant category code 3000-3299 range, reported by Meridian as 4511),
    with no annual limit.
2.3 Point value. For the purposes of statement redemption, points redeem at 1 cent each.

3. STATEMENT CREDITS
3.1 Dining credit. Up to $10 in statement credits each month for purchases at restaurants
    (merchant category code 5812). Unused amounts do not carry forward. As of the current
    statement, $10.00 of this month's credit remains.
3.2 Streaming credit. Up to $20 in statement credits each month for eligible digital
    entertainment subscriptions. Enrollment required. As of the current statement, $20.00
    remains and the account is NOT enrolled.
3.3 Restaurant week credit. Up to $25 in statement credits each calendar quarter for
    restaurant purchases (merchant category codes 5812 and 5814). As of the current
    statement, $25.00 of this quarter's credit remains.
3.4 The dining credit in 3.1 and the restaurant week credit in 3.3 may not both be applied
    to the same transaction.

5. PROTECTIONS
5.1 Purchase protection. Eligible purchases of consumer electronics are covered against
    damage or theft for 90 days, up to $500 per claim.
5.2 Trip delay insurance. Reimbursement for reasonable expenses following a covered delay.
    Amounts depend on the claim and on the itinerary.

6. MEMBER SERVICES
6.1 Concierge. 24-hour concierge service for travel, dining and entertainment requests.
""",
)

HALLWAY_SIGNAL = TermsDocument(
    terms_id="hallway_signal",
    issuer="Hallway Trust",
    product="Signal Card",
    currency="USD",
    text="""HALLWAY TRUST SIGNAL CARD — REWARDS SUMMARY
Effective 1 February 2026. Fictional product, written for a schema demonstration.

1. EARNING
1.1 Groceries. 5% back at supermarkets (MCC 5411) on the first $6,000 of purchases per
    calendar year.

2. ACCEPTANCE
2.1 The Signal Card is not accepted at a number of warehouse clubs and at some independent
    fuel retailers. Cardmembers should present an alternative form of payment at those
    merchants.

3. PROTECTIONS
3.1 Extended warranty. Eligible purchases receive one additional year of warranty cover.
""",
)

TERMS: Mapping[str, TermsDocument] = {
    MERIDIAN_VANTAGE.terms_id: MERIDIAN_VANTAGE,
    HALLWAY_SIGNAL.terms_id: HALLWAY_SIGNAL,
}


def terms_for(terms_id: str) -> TermsDocument:
    try:
        return TERMS[terms_id]
    except KeyError as exc:
        raise KeyError(f"no terms document {terms_id!r}; known: {sorted(TERMS)}") from exc


# --------------------------------------------------------------------------------------
# Agent surface
# --------------------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a manifest authoring assistant for a card benefit disclosure rail.

You are given the published terms of one card product as text. Your job is to draft a
benefit manifest in the schema below and submit it with the submit_draft tool.

You are NOT trusted with the numbers, and this is deliberate. A deterministic validator
checks every draft you submit and either accepts it or rejects it with typed reason codes.
If it rejects your draft, read the codes and the guidance, fix exactly what they name, and
submit a revised draft. Do not argue with a reason code and do not resubmit an unchanged
draft. You have a small, fixed number of attempts.

You never sign anything and you are never given a signing key. Signing happens after you
are done, in code you do not call, only if the validator accepted your draft.

Read the terms carefully and transcribe faithfully. Two things you must not do:

  * Do not invent a figure the terms do not state. If a benefit cannot be priced from the
    document without an assumption, declare it as kind "unpriced" — that is the honest
    option and it is fully supported.
  * Do not record where the card is or is not accepted, even if the terms discuss it. This
    schema has no acceptance field, on purpose. Skip that material entirely.

When the validator accepts a draft, stop and reply with one short sentence naming the
product and how many benefits you declared.
"""


def tools() -> list[dict[str, Any]]:
    return [
        {
            "name": TOOL_SCHEMA,
            "description": "Return the draft manifest schema and the full catalogue of validator reason codes.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": TOOL_SUBMIT,
            "description": (
                "Submit a draft manifest to the deterministic validator. Returns ACCEPTED, "
                "or REJECTED with typed reason codes and fix guidance. The validator never "
                "repairs a draft for you."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"draft": draft_schema()},
                "required": ["draft"],
            },
        },
    ]


def user_prompt(doc: TermsDocument, *, issued_at: int, manifest_id: str) -> str:
    return "\n".join(
        [
            schema_help(),
            "",
            "=" * 78,
            f"Draft a manifest for this product. Use manifest_id {manifest_id!r}, issuer "
            f"{doc.issuer!r}, product {doc.product!r}, currency {doc.currency!r}, and "
            f"issued_at {issued_at} exactly as given — do not sample a clock.",
            "The manifest 'source' field must name this document.",
            "=" * 78,
            "",
            doc.text,
        ]
    )


# --------------------------------------------------------------------------------------
# Run records
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Attempt:
    """One draft submission and the verdict the validator returned on it."""

    index: int
    draft: dict[str, Any]
    report: ValidationReport
    accepted: bool

    def headline(self) -> str:
        if self.accepted:
            return f"attempt {self.index}: ACCEPTED ({self.report.benefit_count} benefits)"
        codes = ", ".join(self.report.reason_codes()) or "(no codes)"
        return f"attempt {self.index}: REJECTED [{codes}]"

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "draft": self.draft,
            "accepted": self.accepted,
            "report": self.report.to_dict(),
            "headline": self.headline(),
        }

    def to_trace_dict(self) -> dict[str, Any]:
        # Replay re-validates rather than trusting the recorded verdict, so the verdict is
        # carried for inspection only. If the validator's rules change, a replayed trace
        # must move with them; a trace that pinned its own verdicts would hide that.
        return {
            "tool": TOOL_SUBMIT,
            "input": {"draft": self.draft},
            "recorded_verdict": self.report.verdict,
            "recorded_reason_codes": list(self.report.reason_codes()),
        }


@dataclass(frozen=True)
class AuthoringRun:
    """The whole loop: every draft, every verdict, and the signature if one was earned."""

    terms_id: str
    source: str
    model: str
    attempts: tuple[Attempt, ...]
    narration: tuple[str, ...]
    signed: SignedManifest | None
    issued_at: int
    error: str | None = None

    @property
    def accepted(self) -> bool:
        return any(a.accepted for a in self.attempts)

    @property
    def rejections(self) -> int:
        return sum(1 for a in self.attempts if not a.accepted)

    def final_report(self) -> ValidationReport | None:
        return self.attempts[-1].report if self.attempts else None

    def headline(self) -> str:
        if self.error:
            return f"ERROR / {self.error}"
        if self.signed is not None:
            return (
                f"SIGNED after {self.rejections} rejection(s) "
                f"({len(self.attempts)} draft(s) submitted)"
            )
        if not self.attempts:
            return "NO DRAFT SUBMITTED"
        return f"UNSIGNED — {len(self.attempts)} draft(s), none accepted"

    def to_dict(self) -> dict[str, Any]:
        return {
            "terms_id": self.terms_id,
            "source": self.source,
            "model": self.model,
            "issued_at": self.issued_at,
            "attempts": [a.to_dict() for a in self.attempts],
            "rejections": self.rejections,
            "accepted": self.accepted,
            "signed": self.signed.to_dict() if self.signed else None,
            "narration": list(self.narration),
            "headline": self.headline(),
            "error": self.error,
            "limitation": reason_catalogue()[UNVERIFIED_AGAINST_SOURCE],
        }


# --------------------------------------------------------------------------------------
# The session — the model's only surface, and the wall between it and the key
# --------------------------------------------------------------------------------------


class AuthoringSession:
    """Executes the drafting agent's tool calls against the real validator.

    Live and replay share this object, so a replayed draft is judged by the same code a
    live one would be. The session holds the accepted draft when one is earned; it does
    NOT hold the signing key and has no method that signs. Signing is a separate call the
    controller makes after the loop, which is why no tool result can ever leak a signature.
    """

    def __init__(self, doc: TermsDocument, *, max_attempts: int = MAX_ATTEMPTS) -> None:
        self.doc = doc
        self.max_attempts = max_attempts
        self.attempts: list[Attempt] = []
        self.accepted: AcceptedDraft | None = None

    @property
    def finished(self) -> bool:
        return self.accepted is not None or len(self.attempts) >= self.max_attempts

    def attempts_left(self) -> int:
        return max(0, self.max_attempts - len(self.attempts))

    def execute(self, tool: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if tool == TOOL_SCHEMA:
            return {
                "schema": schema_help(),
                "reason_codes": reason_catalogue(),
                "attempts_remaining": self.attempts_left(),
            }
        if tool == TOOL_SUBMIT:
            return self.submit(dict((payload or {}).get("draft") or {}))
        return {"error": f"no such tool: {tool!r}"}

    def submit(self, draft: Any) -> dict[str, Any]:
        """Validate one draft. The only place a draft is judged, live or replayed."""
        if self.accepted is not None:
            return {
                "verdict": "ALREADY_ACCEPTED",
                "message": "a draft has already been accepted; stop and summarise",
                "attempts_remaining": 0,
            }
        if len(self.attempts) >= self.max_attempts:
            return {
                "verdict": "OUT_OF_ATTEMPTS",
                "message": (
                    f"{self.max_attempts} attempts used; the loop is closed and nothing "
                    f"will be signed"
                ),
                "attempts_remaining": 0,
            }

        report, accepted = validate_draft(draft)
        index = len(self.attempts) + 1
        self.attempts.append(
            Attempt(
                index=index,
                draft=draft if isinstance(draft, dict) else {"__invalid__": repr(draft)},
                report=report,
                accepted=report.accepted,
            )
        )
        if accepted is not None:
            self.accepted = accepted

        findings = [f for f in report.findings if f.blocking] or list(report.findings)
        shown = findings[:MAX_FINDINGS_RETURNED]
        result: dict[str, Any] = {
            "verdict": report.verdict,
            "attempt": index,
            "attempts_remaining": self.attempts_left(),
            "reason_codes": list(report.reason_codes()),
            "findings": [
                {"code": f.code, "path": f.path, "message": f.message, "fix": f.guidance()}
                for f in shown
            ],
            "findings_elided": max(0, len(findings) - len(shown)),
            "advisories": [
                {"code": f.code, "path": f.path, "message": f.message}
                for f in report.advisories()
            ],
            "limitation": reason_catalogue()[UNVERIFIED_AGAINST_SOURCE],
        }
        if report.accepted:
            result["message"] = (
                "Accepted. The manifest will be signed by the issuer side after this "
                "session ends. You are done — reply with one short sentence."
            )
            result["manifest_hash"] = report.manifest_hash
            result["benefit_count"] = report.benefit_count
            result["priced_count"] = report.priced_count
        else:
            result["message"] = (
                "Rejected. Nothing was repaired for you. Fix exactly what the reason codes "
                "name and submit a revised draft."
            )
        return result


def sign_if_accepted(
    session: AuthoringSession,
    *,
    key: str | bytes = PROTOTYPE_SIGNING_KEY,
    key_id: str = PROTOTYPE_KEY_ID,
) -> SignedManifest | None:
    """Sign the accepted draft, if the validator produced one.

    Called by the controller after the loop has ended. The model has no route to this
    function: it is not a tool, it takes an :class:`AcceptedDraft` that only the validator
    mints, and by the time it runs the conversation is over.
    """
    if session.accepted is None:
        return None
    return sign_accepted(session.accepted, key, key_id=key_id)


# --------------------------------------------------------------------------------------
# Replay
# --------------------------------------------------------------------------------------


def run_recorded(
    doc: TermsDocument,
    recorded: Sequence[Mapping[str, Any]],
    *,
    issued_at: int,
    narration: Sequence[str] = (),
    max_attempts: int = MAX_ATTEMPTS,
    key: str | bytes = PROTOTYPE_SIGNING_KEY,
    model: str = MODEL,
    source: str = SOURCE_REPLAY,
) -> AuthoringRun:
    """Drive recorded drafts through the real validator.

    Nothing is faked except the model's choices. The validator, the reason codes and the
    signature are the same objects a live run would produce.
    """
    session = AuthoringSession(doc, max_attempts=max_attempts)
    for call in recorded:
        tool = str(call.get("tool", TOOL_SUBMIT))
        session.execute(tool, dict(call.get("input", {}) or {}))
    signed = sign_if_accepted(session, key=key)
    return AuthoringRun(
        terms_id=doc.terms_id,
        source=source,
        model=model,
        attempts=tuple(session.attempts),
        narration=tuple(narration),
        signed=signed,
        issued_at=issued_at,
    )


# --------------------------------------------------------------------------------------
# Live
# --------------------------------------------------------------------------------------


def _client() -> Any:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise LiveRunError(
            "--live needs Anthropic credentials: export ANTHROPIC_API_KEY (or "
            "ANTHROPIC_AUTH_TOKEN). The demo path does not need them — run without "
            "--live to replay the recorded authoring loop."
        )
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise LiveRunError(f"anthropic SDK is not importable: {exc}") from exc
    return anthropic.Anthropic()


def run_live(
    doc: TermsDocument,
    *,
    issued_at: int,
    manifest_id: str | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    max_turns: int = MAX_TURNS,
    client: Any | None = None,
    key: str | bytes = PROTOTYPE_SIGNING_KEY,
    session: AuthoringSession | None = None,
) -> AuthoringRun:
    """Manual tool-use loop, so the whole authoring trace is inspectable.

    No temperature/top_p/top_k: claude-opus-5 rejects them. ``stop_reason == "refusal"`` is
    checked before content is read, because on a refusal there is no content to read.
    """
    client = client or _client()
    session = session or AuthoringSession(doc, max_attempts=max_attempts)
    manifest_id = manifest_id or f"{doc.terms_id.replace('_', '-')}-2026"
    narration: list[str] = []
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_prompt(doc, issued_at=issued_at, manifest_id=manifest_id)}
    ]
    error: str | None = None

    for _ in range(max_turns):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=tools(),
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
                    "is_error": False,
                }
            )
        # All results for one assistant turn go back in a single user message.
        messages.append({"role": "user", "content": results})

        if session.finished:
            # The loop is closed by the validator, not by the model deciding it is done.
            break
    else:
        error = f"agent did not finish within {max_turns} turns"

    if session.accepted is None and error is None and len(session.attempts) >= session.max_attempts:
        error = (
            f"no draft passed validation within {session.max_attempts} attempts; "
            f"nothing was signed"
        )

    signed = sign_if_accepted(session, key=key)
    return AuthoringRun(
        terms_id=doc.terms_id,
        source=SOURCE_LIVE,
        model=MODEL,
        attempts=tuple(session.attempts),
        narration=tuple(narration),
        signed=signed,
        issued_at=issued_at,
        error=error,
    )


# --------------------------------------------------------------------------------------
# Traces
# --------------------------------------------------------------------------------------


def trace_path(terms_id: str, trace_dir: Path | None = None) -> Path:
    return (trace_dir or TRACE_DIR) / f"{TRACE_PREFIX}{terms_id}.json"


def load_trace(terms_id: str, trace_dir: Path | None = None) -> dict[str, Any]:
    path = trace_path(terms_id, trace_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"no recorded authoring trace at {path}. Record one with "
            f"`python -m agent.author --terms {terms_id} --live --record`."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def write_trace(
    run: AuthoringRun,
    *,
    trace_dir: Path | None = None,
    provenance: str | None = None,
    note: str | None = None,
) -> Path:
    """Persist an authoring run as a replayable trace.

    `provenance` is written verbatim so a reader can tell a real claude-opus-5 transcript
    from an authored one. Never label an authored trace as live.
    """
    path = trace_path(run.terms_id, trace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob: dict[str, Any] = {
        "terms_id": run.terms_id,
        "model": run.model,
        "provenance": provenance or f"{run.source}-{run.model}",
        "issued_at": run.issued_at,
    }
    if note:
        blob["note"] = note
    blob["narration"] = list(run.narration)
    blob["attempts"] = [a.to_trace_dict() for a in run.attempts]
    path.write_text(json.dumps(blob, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def recorded_attempts(trace: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list(trace.get("attempts", []))


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def author_manifest(
    doc: TermsDocument,
    *,
    issued_at: int,
    live: bool = False,
    record: bool = False,
    max_attempts: int = MAX_ATTEMPTS,
    trace_dir: Path | None = None,
    key: str | bytes = PROTOTYPE_SIGNING_KEY,
) -> AuthoringRun:
    """Run the authoring loop once, live or replayed."""
    if live:
        run = run_live(doc, issued_at=issued_at, max_attempts=max_attempts, key=key)
        if record:
            write_trace(run, trace_dir=trace_dir)
        return run
    trace = load_trace(doc.terms_id, trace_dir)
    return run_recorded(
        doc,
        recorded_attempts(trace),
        issued_at=int(trace.get("issued_at", issued_at)),
        narration=trace.get("narration", ()),
        max_attempts=max_attempts,
        key=key,
        model=str(trace.get("model", MODEL)),
    )


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------

_WIDTH = 92


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
    words = text.split()
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


def render_run(run: AuthoringRun, doc: TermsDocument, colour: bool | None = None) -> str:
    c = _C(_use_colour() if colour is None else colour)
    out: list[str] = []
    out.append(c.bold("PLUMBLINE — manifest authoring: the model proposes, the validator disposes"))
    out.append("=" * _WIDTH)
    out.append(f"terms     {doc.issuer} {doc.product}  ({doc.terms_id}, {len(doc.text)} chars)")
    out.append(f"model     {run.model}    source  {run.source}    issued_at  {run.issued_at}")
    out.append("=" * _WIDTH)

    for a in run.attempts:
        out.append("")
        head = c.green(a.headline()) if a.accepted else c.red(a.headline())
        out.append(head)
        if not a.accepted:
            for f in a.report.errors():
                out.append(f"  {c.red(f.code):<40} {f.path}")
                for chunk in _wrap(f.message, _WIDTH - 6):
                    out.append(f"      {c.dim(chunk)}")
        for f in a.report.advisories():
            if f.code == UNVERIFIED_AGAINST_SOURCE:
                continue
            out.append(f"  {c.yellow(f.code):<40} {f.path}")

    out.append("")
    out.append("=" * _WIDTH)
    if run.signed is not None:
        m = run.signed.manifest
        out.append(
            f"{c.green('SIGNED')}    {m.manifest_id}  "
            f"{len(m.benefits)} benefit(s), {len(m.priced())} priced"
        )
        out.append(f"          key_id {run.signed.key_id}  sig {run.signed.signature[:32]}…")
        out.append(f"          content_hash {m.content_hash()}")
        out.append(
            f"          {c.dim('the model never held the key; the signature was produced')}"
        )
        out.append(f"          {c.dim('after the loop closed, from the validated draft only')}")
    else:
        out.append(f"{c.red('NOT SIGNED')}  {run.headline()}")
        out.append(
            f"          {c.dim('the loop terminated without an accepted draft; a validator')}"
        )
        out.append(f"          {c.dim('that cannot be satisfied signs nothing, which is the point')}")
    if run.error:
        out.append(f"{c.yellow('note')}      {run.error}")
    out.append("-" * _WIDTH)
    for chunk in _wrap("LIMITATION: " + reason_catalogue()[UNVERIFIED_AGAINST_SOURCE], _WIDTH):
        out.append(c.yellow(chunk))
    out.append("=" * _WIDTH)
    return "\n".join(out)


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agent.author",
        description="Draft a benefit manifest from card terms; the validator decides.",
    )
    parser.add_argument("--terms", default=MERIDIAN_VANTAGE.terms_id, choices=sorted(TERMS))
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--replay", action="store_true", help="replay the recorded loop (default)")
    group.add_argument("--live", action="store_true", help="call claude-opus-5 for real")
    parser.add_argument("--record", action="store_true", help="write the run to agent/traces/")
    parser.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS)
    parser.add_argument(
        "--issued-at", type=int, default=1753600000, help="explicit timestamp, so runs replay"
    )
    parser.add_argument("--json", action="store_true", help="emit the structured run")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args(argv)

    if args.record and not args.live:
        print("--record requires --live; a replay has nothing new to record", file=sys.stderr)
        return 2

    doc = terms_for(args.terms)
    try:
        run = author_manifest(
            doc,
            issued_at=args.issued_at,
            live=args.live,
            record=args.record,
            max_attempts=args.max_attempts,
        )
    except LiveRunError as exc:
        print(f"live run unavailable: {exc}", file=sys.stderr)
        return 2
    except (FileNotFoundError, ValueError) as exc:
        print(f"replay unavailable: {exc}", file=sys.stderr)
        return 2
    except AuthoringError as exc:  # pragma: no cover - the gate raising is itself a bug here
        print(f"signing gate refused: {exc}", file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps(run.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render_run(run, doc, colour=False if args.no_color else None))

    if args.record:
        print(f"\ntrace written to {trace_path(doc.terms_id)}", file=sys.stderr)

    # Exit non-zero if a manifest was signed without the validator ever rejecting anything
    # on a terms document whose recorded loop is supposed to show a rejection. The demo is
    # also a gate: an authoring loop with no visible rejection proves nothing.
    if run.signed is not None and run.rejections == 0 and args.terms == MERIDIAN_VANTAGE.terms_id:
        print(
            "expected at least one rejection in the recorded loop; the demo's whole claim "
            "is that the validator throws drafts out",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
