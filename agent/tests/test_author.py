"""The LLM half of manifest authoring: the loop, the wall, and the recorded traces.

The three properties this file is really asserting:

  1. the loop terminates. A model that cannot satisfy the validator runs out of attempts
     and nothing is signed. There is no path where persistence wins;
  2. the model never touches a signature. The key is not in the session, not in a tool
     result, and the signing call happens after the conversation is over;
  3. replay needs no API key and produces the same verdicts, because it re-validates
     rather than trusting anything recorded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import author
from plumbline.authoring import (
    ACCEPTANCE_PREDICATE_PRESENT,
    EARN_CAP_INCONSISTENT_WITH_RATE,
    EXCLUSIVITY_GROUP_OF_ONE,
    PROVENANCE_PLACEHOLDER,
    UNREACHABLE_ZERO_VALUE,
    UNVERIFIED_AGAINST_SOURCE,
    UNVERIFIED_SUFFIX,
    AuthoringError,
)
from plumbline.manifest import verify_manifest

T0 = 1753600000


def _draft(**overrides) -> dict:
    d = {
        "version": author.DRAFT_VERSION,
        "manifest_id": "unit-card-2026",
        "issuer": "Unit Bank",
        "product": "Unit Card",
        "currency": "USD",
        "issued_at": T0,
        "source": "Unit Bank Card terms, effective 2026-01-01, section 2",
        "benefits": [
            {
                "benefit_id": "earn_dining",
                "kind": "earn",
                "label": "3x dining",
                "provenance": "Section 2.1, dining multiplier table",
                "eligibility": {"mccs": [5812]},
                "rate_bp": 300,
                "cap_qualifying_spend_minor": 1_000_000,
                "capacity_minor": 30_000,
                "window": "annual",
            }
        ],
    }
    d.update(overrides)
    return d


def _submissions(*drafts) -> list[dict]:
    return [{"tool": author.TOOL_SUBMIT, "input": {"draft": d}} for d in drafts]


# ----------------------------------------------------------------------------------
# The session
# ----------------------------------------------------------------------------------


def test_a_good_draft_is_accepted_and_the_controller_signs_it():
    session = author.AuthoringSession(author.MERIDIAN_VANTAGE)
    result = session.submit(_draft())

    assert result["verdict"] == "ACCEPTED"
    assert result["reason_codes"] == []
    assert session.accepted is not None
    assert session.finished

    signed = author.sign_if_accepted(session, key="unit-key")
    assert verify_manifest(signed, "unit-key")
    assert signed.manifest.manifest_id == "unit-card-2026"


def test_a_rejection_returns_codes_and_fix_guidance_and_repairs_nothing():
    session = author.AuthoringSession(author.MERIDIAN_VANTAGE)
    bad = _draft()
    bad["benefits"][0]["capacity_minor"] = 1_000_000  # the spend cap in the value slot

    result = session.submit(bad)

    assert result["verdict"] == "REJECTED"
    assert EARN_CAP_INCONSISTENT_WITH_RATE in result["reason_codes"]
    assert all(f["fix"] for f in result["findings"])
    assert session.accepted is None
    # Nothing was repaired: the draft the session recorded is the draft that was submitted.
    assert session.attempts[0].draft["benefits"][0]["capacity_minor"] == 1_000_000
    assert author.sign_if_accepted(session) is None


def test_every_tool_result_states_the_limitation():
    session = author.AuthoringSession(author.MERIDIAN_VANTAGE)
    accepted = session.submit(_draft())
    rejected = author.AuthoringSession(author.MERIDIAN_VANTAGE).submit({"nope": 1})
    for result in (accepted, rejected):
        assert "NOT" in result["limitation"]
    schema = session.execute(author.TOOL_SCHEMA, {})
    assert UNVERIFIED_AGAINST_SOURCE in schema["reason_codes"]


def test_the_loop_closes_after_max_attempts_and_signs_nothing():
    session = author.AuthoringSession(author.MERIDIAN_VANTAGE, max_attempts=2)
    bad = _draft(acceptance={"not_accepted_merchants": ["clubs"]})

    first = session.submit(bad)
    second = session.submit(bad)
    third = session.submit(bad)

    assert first["verdict"] == "REJECTED" and second["verdict"] == "REJECTED"
    assert third["verdict"] == "OUT_OF_ATTEMPTS"
    assert len(session.attempts) == 2  # the third was never judged, so it is not an attempt
    assert session.finished
    assert author.sign_if_accepted(session) is None


def test_a_session_will_not_accept_a_second_draft_after_one_passes():
    session = author.AuthoringSession(author.MERIDIAN_VANTAGE)
    session.submit(_draft())
    again = session.submit(_draft(manifest_id="something-else-2026"))
    assert again["verdict"] == "ALREADY_ACCEPTED"
    assert author.sign_if_accepted(session, key="k").manifest.manifest_id == "unit-card-2026"


def test_no_tool_result_ever_carries_the_signing_key_or_a_signature():
    session = author.AuthoringSession(author.MERIDIAN_VANTAGE)
    results = [
        session.execute(author.TOOL_SCHEMA, {}),
        session.execute(author.TOOL_SUBMIT, {"draft": _draft()}),
    ]
    blob = json.dumps(results)
    signed = author.sign_if_accepted(session)

    assert author.PROTOTYPE_SIGNING_KEY not in blob
    assert signed.signature not in blob  # the signature is minted after the loop ends
    # And the session itself has no way to sign: signing is a module function taking an
    # AcceptedDraft, called by the controller after the conversation has ended.
    assert not hasattr(session, "sign")
    assert not any("key" in name.lower() for name in vars(session))


def test_findings_returned_to_the_model_are_bounded():
    session = author.AuthoringSession(author.MERIDIAN_VANTAGE)
    many = _draft()
    many["benefits"] = [
        {
            "benefit_id": f"b{i}",
            "kind": "earn",
            "label": f"b{i}",
            "provenance": "Section 2.1, multiplier table",
            "rate_bp": -1,
        }
        for i in range(40)
    ]
    result = session.submit(many)
    assert len(result["findings"]) == author.MAX_FINDINGS_RETURNED
    assert result["findings_elided"] > 0


# ----------------------------------------------------------------------------------
# Replay
# ----------------------------------------------------------------------------------


def test_the_recorded_meridian_loop_is_rejected_then_accepted_then_signed():
    run = author.author_manifest(author.MERIDIAN_VANTAGE, issued_at=T0)

    assert [a.accepted for a in run.attempts] == [False, True]
    assert run.rejections == 1
    assert set(run.attempts[0].report.reason_codes()) == {
        EARN_CAP_INCONSISTENT_WITH_RATE,
        PROVENANCE_PLACEHOLDER,
        UNREACHABLE_ZERO_VALUE,
        EXCLUSIVITY_GROUP_OF_ONE,
    }
    assert run.signed is not None
    assert verify_manifest(run.signed, author.PROTOTYPE_SIGNING_KEY)
    assert UNVERIFIED_SUFFIX.strip() in run.signed.manifest.source


def test_the_recorded_unfixable_loop_terminates_without_a_signature():
    run = author.author_manifest(author.HALLWAY_SIGNAL, issued_at=T0)

    assert len(run.attempts) == 4
    assert not any(a.accepted for a in run.attempts)
    assert all(
        ACCEPTANCE_PREDICATE_PRESENT in a.report.reason_codes() for a in run.attempts
    )
    assert run.signed is None
    assert "UNSIGNED" in run.headline()


def test_the_recorded_loop_tries_four_spellings_of_the_same_forbidden_fact():
    # Manifest field, benefit field, note text, label text. The point of the trace is that
    # rephrasing does not get past the gate.
    run = author.author_manifest(author.HALLWAY_SIGNAL, issued_at=T0)
    paths = [
        f.path
        for a in run.attempts
        for f in a.report.errors()
        if f.code == ACCEPTANCE_PREDICATE_PRESENT
    ]
    assert "acceptance" in paths
    assert "benefits[0].declined_merchants" in paths
    assert "benefits[0].note" in paths
    assert "benefits[0].label" in paths


def test_replay_revalidates_rather_than_trusting_the_recorded_verdict():
    # The trace carries a recorded verdict for inspection. Replay ignores it: swap the
    # recorded verdict for a lie and the replayed run is unchanged.
    trace = author.load_trace(author.MERIDIAN_VANTAGE.terms_id)
    lying = [dict(a, recorded_verdict="ACCEPTED", recorded_reason_codes=[]) for a in trace["attempts"]]
    run = author.run_recorded(author.MERIDIAN_VANTAGE, lying, issued_at=T0)
    assert [a.accepted for a in run.attempts] == [False, True]


def test_replay_is_byte_stable():
    a = author.author_manifest(author.MERIDIAN_VANTAGE, issued_at=T0)
    b = author.author_manifest(author.MERIDIAN_VANTAGE, issued_at=T0)
    assert a.signed.signature == b.signed.signature
    assert a.signed.manifest.content_hash() == b.signed.manifest.content_hash()


@pytest.mark.parametrize("terms_id", sorted(author.TERMS))
def test_every_shipped_trace_is_labelled_honestly(terms_id):
    trace = author.load_trace(terms_id)
    assert trace["model"] == "claude-opus-5"
    # An authored trace must never be labelled as a live transcript.
    assert trace["provenance"] == "authored (not a live transcript)"
    assert trace["note"]
    assert trace["attempts"]


def test_a_missing_trace_says_how_to_record_one(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        author.load_trace("meridian_vantage", trace_dir=Path(tmp_path))
    assert "--live --record" in str(exc.value)


# ----------------------------------------------------------------------------------
# The live loop, driven by a stub so it is testable without an API key
# ----------------------------------------------------------------------------------


class _Block:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _Response:
    def __init__(self, stop_reason, content, stop_details=None):
        self.stop_reason = stop_reason
        self.content = content
        self.stop_details = stop_details


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": [dict(m) for m in kwargs.get("messages", [])]})
        return self._responses.pop(0) if self._responses else _Response("end_turn", [])


class _FakeClient:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


class _StubbornClient:
    """A model that submits the same forbidden draft forever.

    This is the honest version of "a draft that cannot be fixed": the terms contain an
    acceptance predicate, the schema has none, and no amount of persistence changes that.
    """

    def __init__(self, draft):
        self.draft = draft
        self.calls = 0

    class _Messages:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kwargs):
            self.outer.calls += 1
            return _Response(
                "tool_use",
                [
                    _Block(type="text", text="Trying again."),
                    _Block(
                        type="tool_use",
                        id=f"t{self.outer.calls}",
                        name=author.TOOL_SUBMIT,
                        input={"draft": self.outer.draft},
                    ),
                ],
            )

    @property
    def messages(self):
        return self._Messages(self)


def test_the_revision_loop_terminates_on_a_draft_that_cannot_be_fixed():
    stubborn = _StubbornClient(_draft(acceptance={"not_accepted_merchants": ["clubs"]}))
    run = author.run_live(
        author.HALLWAY_SIGNAL, issued_at=T0, max_attempts=3, client=stubborn
    )

    assert len(run.attempts) == 3
    assert not run.accepted
    assert run.signed is None
    assert run.error is not None and "3 attempts" in run.error
    # It stopped because the validator closed the loop, not because it ran out of turns.
    assert stubborn.calls == 3
    assert all(ACCEPTANCE_PREDICATE_PRESENT in a.report.reason_codes() for a in run.attempts)


def test_the_live_loop_revises_after_a_rejection_and_then_signs():
    bad = _draft()
    bad["benefits"][0]["capacity_minor"] = 1_000_000
    client = _FakeClient(
        [
            _Response(
                "tool_use",
                [
                    _Block(type="text", text="Drafting from section 2."),
                    _Block(type="tool_use", id="t1", name=author.TOOL_SUBMIT, input={"draft": bad}),
                ],
            ),
            _Response(
                "tool_use",
                [
                    _Block(type="text", text="The cap was a spend figure. Fixing."),
                    _Block(
                        type="tool_use", id="t2", name=author.TOOL_SUBMIT, input={"draft": _draft()}
                    ),
                ],
            ),
            _Response("end_turn", [_Block(type="text", text="Done: 1 benefit declared.")]),
        ]
    )
    run = author.run_live(author.MERIDIAN_VANTAGE, issued_at=T0, client=client, key="unit-key")

    assert [a.accepted for a in run.attempts] == [False, True]
    assert run.rejections == 1
    assert run.signed is not None and verify_manifest(run.signed, "unit-key")
    assert run.error is None
    assert "SIGNED after 1 rejection" in run.headline()


def test_the_live_loop_batches_tool_results_into_one_user_message():
    rejected = _draft()
    rejected["benefits"][0]["capacity_minor"] = 1_000_000
    client = _FakeClient(
        [
            _Response(
                "tool_use",
                [
                    _Block(type="tool_use", id="t1", name=author.TOOL_SCHEMA, input={}),
                    _Block(
                        type="tool_use", id="t2", name=author.TOOL_SUBMIT, input={"draft": rejected}
                    ),
                ],
            ),
            _Response("end_turn", [_Block(type="text", text="Giving up.")]),
        ]
    )
    author.run_live(author.MERIDIAN_VANTAGE, issued_at=T0, client=client)

    second_request = client.messages.calls[1]["messages"]
    assert [m["role"] for m in second_request] == ["user", "assistant", "user"]
    assert [b["tool_use_id"] for b in second_request[2]["content"]] == ["t1", "t2"]


def test_the_live_loop_stops_as_soon_as_the_validator_accepts():
    # The loop is closed by the validator, not by the model deciding it is finished.
    client = _FakeClient(
        [
            _Response(
                "tool_use",
                [_Block(type="tool_use", id="t1", name=author.TOOL_SUBMIT, input={"draft": _draft()})],
            ),
            _Response("end_turn", [_Block(type="text", text="never reached")]),
        ]
    )
    run = author.run_live(author.MERIDIAN_VANTAGE, issued_at=T0, client=client)
    assert len(client.messages.calls) == 1
    assert run.signed is not None


def test_the_live_loop_sends_the_parameters_this_model_accepts():
    client = _FakeClient([_Response("end_turn", [_Block(type="text", text="done")])])
    author.run_live(author.MERIDIAN_VANTAGE, issued_at=T0, client=client)
    kwargs = client.messages.calls[0]

    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["max_tokens"] == author.MAX_TOKENS
    assert [t["name"] for t in kwargs["tools"]] == [author.TOOL_SCHEMA, author.TOOL_SUBMIT]
    # claude-opus-5 rejects these outright.
    assert not {"temperature", "top_p", "top_k"} & set(kwargs)
    # The timestamp is in the prompt, not sampled from a clock inside the loop.
    assert str(T0) in kwargs["messages"][0]["content"]


def test_the_live_loop_handles_a_refusal_without_reading_content():
    class _Exploding(list):
        def __iter__(self):  # pragma: no cover - must never run
            raise AssertionError("content was read on a refusal")

    client = _FakeClient(
        [_Response("refusal", _Exploding(), stop_details=_Block(category="cyber"))]
    )
    run = author.run_live(author.MERIDIAN_VANTAGE, issued_at=T0, client=client)
    assert run.error is not None and "refused" in run.error
    assert run.attempts == ()
    assert run.signed is None


def test_the_live_loop_gives_up_rather_than_looping_forever():
    looping = [
        _Response("tool_use", [_Block(type="tool_use", id=f"t{i}", name=author.TOOL_SCHEMA, input={})])
        for i in range(author.MAX_TURNS + 3)
    ]
    run = author.run_live(author.MERIDIAN_VANTAGE, issued_at=T0, client=_FakeClient(looping))
    assert run.error == f"agent did not finish within {author.MAX_TURNS} turns"
    assert run.signed is None


def test_live_fails_clearly_without_credentials_and_replay_still_works(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    with pytest.raises(author.LiveRunError) as exc:
        author.run_live(author.MERIDIAN_VANTAGE, issued_at=T0)
    assert "ANTHROPIC_API_KEY" in str(exc.value)

    run = author.author_manifest(author.MERIDIAN_VANTAGE, issued_at=T0)
    assert run.signed is not None


# ----------------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------------


def test_cli_replays_both_scenarios_and_exits_zero(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    assert author.main(["--terms", "meridian_vantage", "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "REJECTED" in out and "SIGNED" in out
    assert "LIMITATION" in out

    assert author.main(["--terms", "hallway_signal", "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "NOT SIGNED" in out
    assert ACCEPTANCE_PREDICATE_PRESENT in out


def test_cli_emits_structured_json(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert author.main(["--terms", "meridian_vantage", "--json"]) == 0
    blob = json.loads(capsys.readouterr().out)
    assert blob["rejections"] == 1
    assert blob["signed"]["key_id"] == author.PROTOTYPE_KEY_ID
    assert blob["limitation"]


def test_cli_live_without_credentials_exits_nonzero_without_a_traceback(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert author.main(["--terms", "meridian_vantage", "--live"]) == 2
    assert "live run unavailable" in capsys.readouterr().err


def test_cli_refuses_to_record_a_replay(capsys):
    assert author.main(["--terms", "meridian_vantage", "--record"]) == 2
    assert "--record requires --live" in capsys.readouterr().err


# ----------------------------------------------------------------------------------
# The wall between the model and the key
# ----------------------------------------------------------------------------------


def test_the_agent_module_never_calls_the_raw_signer():
    source = Path(author.__file__).read_text(encoding="utf-8")
    # sign_manifest is the unguarded signer in plumbline.manifest. The authoring agent must
    # reach a signature only through sign_accepted, which is gated on AcceptedDraft.
    assert "sign_manifest" not in source
    assert "sign_accepted" in source


def test_a_forged_acceptance_cannot_be_smuggled_into_the_controller():
    session = author.AuthoringSession(author.MERIDIAN_VANTAGE)
    session.accepted = {"manifest": "anything"}  # type: ignore[assignment]
    with pytest.raises(AuthoringError):
        author.sign_if_accepted(session)
