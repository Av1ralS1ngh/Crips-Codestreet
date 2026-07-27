"""The currency contract: no serializer in the valuation path defaults a currency symbol.

`caveat.money.fmt_money` takes a symbol and defaults it to the rupee. That default is right
for the kernel's own carts, which are INR throughout, and it was silently wrong for every
manifest that is not — a USD valuation came out with every integer correct and every sign a
rupee. Nothing raised, nothing looked broken, and `chase-sapphire-reserve-2026` rendered a
$5.72 line item as "₹572".

Two guards live here, and they fail differently on purpose:

  * an INTROSPECTION guard, which fails at the moment someone adds a serializer that
    defaults the currency again — before any USD figure is ever rendered;
  * an OUTPUT guard, which drives a USD manifest through every surface that serializes one
    and asserts the glyph appears nowhere.

The output guard alone would be enough today and useless tomorrow: it only covers the
surfaces that exist when it is written. The introspection guard is what makes the rule hold
for surfaces nobody has written yet.

`ensure_ascii=False` matters throughout. The json default escapes the symbol to \\u20b9, so
a substring search for the glyph passes on output that renders as rupees on screen.
"""

from __future__ import annotations

import inspect
import json

import pytest

from caveat.money import RUPEE, UnknownCurrencyError, fmt_currency, fmt_money
from plumbline import mcp_server as srv
from plumbline import products as P
from plumbline import scenarios as S
from plumbline.allocate import AllocationResult, allocate
from plumbline.evaluate import DerivationNode, RankedInstrument, Ranking
from plumbline.oracle import OracleResult
from plumbline.manifest import Benefit
from plumbline.receipt import CandidateRecord, witness_content_hash
from plumbline.witness import Verification, Witness

CLOCK = S.DEMO_CLOCK

# Every serializer in the valuation path that renders an amount. Each must take `currency`
# as a required keyword-only argument: required so a call site cannot omit it, keyword-only
# so it cannot be passed by position and land in the wrong slot.
MONEY_SERIALIZERS = (
    (Benefit, "describe"),
    (Witness, "to_dict"),
    (Verification, "to_dict"),
    (RankedInstrument, "to_dict"),
    (Ranking, "to_dict"),
    (DerivationNode, "to_dict"),
    (DerivationNode, "render_lines"),
    (CandidateRecord, "to_dict"),
    (AllocationResult, "to_dict"),
    (OracleResult, "to_dict"),
)


# The surfaces the output guard drives, named rather than discovered, so that dropping one
# from `_usd_surfaces` is a failure rather than a silently smaller test.
SURFACE_NAMES = (
    "signed_manifest",
    "evaluation",
    "attestable_body",
    "witness",
    "verification",
    "derivation",
    "derivation_lines",
    "witness_derivation",
    "ranking",
    "allocation",
    "receipt",
    "receipt_text",
    "evaluation_text",
    "mcp:value_cart",
    "mcp:explain_derivation",
    "mcp:describe_instrument",
)


# --------------------------------------------------------------------------------------
# The primitive
# --------------------------------------------------------------------------------------


def test_fmt_currency_names_the_symbol_and_refuses_to_guess() -> None:
    assert fmt_currency(57_200, "USD") == "$572"
    assert fmt_currency(57_200, "INR") == "₹572"
    with pytest.raises(UnknownCurrencyError) as exc:
        fmt_currency(100, "JPY")
    assert "JPY" in str(exc.value)


def test_fmt_money_keeps_its_rupee_default_for_the_kernel() -> None:
    """The kernel's own carts are INR and its ~60 call sites rely on this.

    The fix is not "remove the default" — it is "no manifest-derived figure reaches the
    defaulting form". This test exists so a future reader knows the default is deliberate
    where it survives.
    """
    assert fmt_money(57_200) == f"{RUPEE}572"


# --------------------------------------------------------------------------------------
# The introspection guard
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cls, method", MONEY_SERIALIZERS, ids=[f"{c.__name__}.{m}" for c, m in MONEY_SERIALIZERS]
)
def test_the_serializer_requires_an_explicit_currency(cls: type, method: str) -> None:
    param = inspect.signature(getattr(cls, method)).parameters.get("currency")
    assert param is not None, (
        f"{cls.__name__}.{method} renders money and does not take a currency. It will "
        f"default to the rupee and serialize a USD manifest with rupee signs, silently."
    )
    assert param.kind is inspect.Parameter.KEYWORD_ONLY, (
        f"{cls.__name__}.{method} takes `currency` positionally; keep it keyword-only so "
        f"it cannot be filled by a neighbouring argument"
    )
    assert param.default is inspect.Parameter.empty, (
        f"{cls.__name__}.{method} defaults `currency`. A default here is the whole bug: "
        f"the call site that forgets renders the wrong sign instead of raising."
    )


def test_witness_derivation_takes_the_currency_off_the_cart() -> None:
    """The one renderer that must NOT take a currency argument.

    `Witness.derivation` is handed the cart, and `verify_witness` already refuses a
    manifest and cart that disagree on currency — so there is exactly one right answer and
    a parameter could only ever be used to supply a wrong one. It used to take `symbol`,
    defaulted to the rupee.
    """
    params = inspect.signature(Witness.derivation).parameters
    assert "currency" not in params and "symbol" not in params, (
        "Witness.derivation should read the currency off the cart it is given, not accept "
        "one; a parameter here is a way to render a derivation under the wrong sign"
    )


def test_omitting_the_currency_raises_rather_than_rendering_rupees() -> None:
    """The behavioural half of the guard above, on the call site the validator found."""
    manifests = S.signed_manifests([P.CHASE_SAPPHIRE_RESERVE_ID], clock=CLOCK)
    evaluation = S.evaluate_cart(S.USD_TRIP_CART, manifests, clock=CLOCK)
    witness = evaluation.candidates[0].witness
    assert witness is not None
    with pytest.raises(TypeError):
        witness.to_dict()  # type: ignore[call-arg]


# --------------------------------------------------------------------------------------
# The output guard
# --------------------------------------------------------------------------------------


def _usd_surfaces() -> dict[str, object]:
    """Every serialized form a USD manifest reaches, with no relabelling in between.

    `scenarios.candidate_view` post-processes `*_display` fields through `relabel`, so a
    test that only drove the scenarios would pass with the defect fully intact. Everything
    here is the raw serializer output.
    """
    cart = S.USD_TRIP_CART
    ids = [m.manifest_id for m in P.catalogue_for_currency(P.USD, CLOCK)]
    manifests = S.signed_manifests(ids, clock=CLOCK)
    evaluation = S.evaluate_cart(cart, manifests, clock=CLOCK)
    receipt = S.issue_receipt(
        evaluation,
        receipt_id="rcpt_currency_guard",
        cart=cart,
        manifests=manifests,
        mandate=S.mandate_for(ids, "mandate_currency_guard"),
        clock=CLOCK,
    )
    top = evaluation.candidates[0]
    assert top.witness is not None and top.verification is not None
    assert top.derivation is not None
    assert evaluation.ranking is not None
    manifest = manifests[top.manifest_id].manifest

    surfaces: dict[str, object] = {
        "signed_manifest": [m.to_dict() for m in manifests.values()],
        "evaluation": evaluation.to_dict(),
        "attestable_body": [c.attestable_body() for c in evaluation.candidates],
        "witness": top.witness.to_dict(currency=cart.currency),
        "verification": top.verification.to_dict(currency=cart.currency),
        "derivation": top.derivation.to_dict(currency=cart.currency),
        "derivation_lines": top.derivation.render_lines(currency=cart.currency),
        "witness_derivation": top.witness.derivation(manifest, cart),
        "ranking": evaluation.ranking.to_dict(currency=cart.currency),
        "allocation": allocate(manifest, cart).to_dict(currency=cart.currency),
        "receipt": receipt.to_dict(),
        "receipt_text": receipt.receipt.render_text(),
        "evaluation_text": evaluation.render_text(),
    }
    for tool, args in (
        ("value_cart", {"cart": srv.CART_USD_TRIP, "as_of": CLOCK}),
        (
            "explain_derivation",
            {"instrument": top.manifest_id, "cart": srv.CART_USD_TRIP, "as_of": CLOCK},
        ),
        ("describe_instrument", {"instrument": top.manifest_id, "as_of": CLOCK}),
    ):
        surfaces[f"mcp:{tool}"] = srv.dispatch(tool, args)
    return surfaces


@pytest.fixture(scope="module")
def usd_surfaces() -> dict[str, object]:
    return _usd_surfaces()


@pytest.mark.parametrize("surface", SURFACE_NAMES)
def test_a_usd_manifest_never_emits_a_rupee_sign(
    surface: str, usd_surfaces: dict[str, object]
) -> None:
    """The headline guard. One case per surface so a failure names the one that broke."""
    blob = json.dumps(usd_surfaces[surface], ensure_ascii=False, default=str)
    assert RUPEE not in blob, f"{surface} rendered a USD figure with a rupee sign"
    assert "\\u20b9" not in blob, f"{surface} escaped a rupee sign into a USD payload"


def test_every_usd_surface_is_covered(usd_surfaces: dict[str, object]) -> None:
    """A surface added to `_usd_surfaces` and not to `SURFACE_NAMES` is an untested one."""
    assert sorted(usd_surfaces) == sorted(SURFACE_NAMES)


def test_the_usd_surfaces_actually_carry_dollar_figures(
    usd_surfaces: dict[str, object],
) -> None:
    """Guards the guard: deleting every symbol would satisfy the test above."""
    surfaces = usd_surfaces
    assert "$" in json.dumps(surfaces["witness"], ensure_ascii=False)
    assert "$" in json.dumps(surfaces["verification"], ensure_ascii=False)
    assert "$" in json.dumps(surfaces["ranking"], ensure_ascii=False)
    assert "$" in json.dumps(surfaces["receipt"], ensure_ascii=False)


def test_the_inr_path_still_renders_rupees() -> None:
    """The converse. An INR manifest under a dollar sign is the same defect mirrored."""
    cart = S.INR_TRIP_CART
    manifests = S.signed_manifests([P.HDFC_INFINIA_ID], clock=CLOCK)
    evaluation = S.evaluate_cart(cart, manifests, clock=CLOCK)
    top = evaluation.candidates[0]
    assert top.witness is not None and top.verification is not None

    blob = json.dumps(
        {
            "witness": top.witness.to_dict(currency=cart.currency),
            "verification": top.verification.to_dict(currency=cart.currency),
            "evaluation": evaluation.to_dict(),
        },
        ensure_ascii=False,
    )
    assert RUPEE in blob
    # Benefit labels legitimately quote published INR figures; no *rendered* amount may
    # carry a dollar sign, so check the display fields rather than the whole blob.
    displays = _display_values(json.loads(blob))
    assert displays
    assert not [d for d in displays if "$" in d]


def _display_values(obj: object) -> list[str]:
    out: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key.endswith("_display") and isinstance(value, str):
                out.append(value)
            else:
                out.extend(_display_values(value))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_display_values(item))
    return out


# --------------------------------------------------------------------------------------
# The witness content hash travels with the currency it was rendered under
# --------------------------------------------------------------------------------------


def test_the_witness_content_hash_is_reproducible_from_the_receipt_alone() -> None:
    """The hash covers the rendered total, so a verifier must not assume a currency."""
    cart = S.USD_TRIP_CART
    manifests = S.signed_manifests([P.CHASE_SAPPHIRE_RESERVE_ID], clock=CLOCK)
    evaluation = S.evaluate_cart(cart, manifests, clock=CLOCK)
    top = evaluation.candidates[0]
    assert top.witness is not None

    receipt = S.issue_receipt(
        evaluation,
        receipt_id="rcpt_witness_hash",
        cart=cart,
        manifests=manifests,
        mandate=S.mandate_for([P.CHASE_SAPPHIRE_RESERVE_ID], "mandate_witness_hash"),
        clock=CLOCK,
    )
    recorded = receipt.receipt.candidates[0].witness_hash
    body = receipt.to_dict()["receipt"]
    assert recorded == witness_content_hash(
        top.witness, currency=str(body["session"]["currency"])
    )
    assert recorded != witness_content_hash(top.witness, currency="INR")
