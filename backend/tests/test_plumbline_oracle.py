"""The offline oracle: is it the same problem, does it ever sign a number it cannot stand
behind, and is it structurally kept out of the checkout path.

Three claims are load-bearing for the pitch, so each has tests rather than a docstring:

  1. The oracle maximises the model the greedy allocator approximates. Checked against a
     brute-force enumeration that shares no code with the Z3 encoding — different
     algorithm, different author's reasoning, same answer required.
  2. The oracle never reports a bound it did not establish. The specific failure an
     adversary measured — Z3 reporting a lower bound above its upper bound after a timeout
     — is tested by name, and a real 1ms timeout is tested end to end.
  3. The oracle cannot end up on the hot path. Tested statically (nothing in the checkout
     path imports it), dynamically (the guard rejects hot-path importers) and in a real
     subprocess (a process that declares itself a checkout cannot import it at all).
"""

from __future__ import annotations

import ast
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

from caveat.cart import Cart, CartLine
from plumbline.allocate import allocate, candidates
from plumbline.manifest import (
    KIND_CREDIT,
    KIND_EARN,
    KIND_PROTECTION,
    KIND_UNPRICED,
    Benefit,
    Eligibility,
    Manifest,
    build_manifest,
)
from plumbline.oracle import (
    BRUTE_FORCE_MAX_CANDIDATES,
    CHECKOUT_PROCESS_ENV,
    HOT_PATH_MODULES,
    REASON_BOUNDS_NOT_CLOSED,
    REASON_INCOHERENT_BOUNDS,
    REASON_NON_NUMERAL_BOUND,
    REASON_TIMEOUT,
    REASON_UNSAT,
    REASON_WITNESS_REJECTED,
    RESULT_SAT,
    RESULT_UNKNOWN,
    RESULT_UNSAT,
    STATUS_OPTIMAL,
    STATUS_UNKNOWN,
    OfflineOnlyError,
    OracleModelError,
    assert_offline,
    brute_force_optimum,
    classify_bounds,
    optimum,
)
from plumbline.witness import Verification, verify_witness

BACKEND = Path(__file__).resolve().parents[1]
T0 = 1_753_600_000
MCCS = (5812, 7011, 5411)

# Generous, because a test that fails on a slow machine teaches nothing. The instances
# these run on are small; the timeout exists so a pathological one cannot hang the suite.
TEST_TIMEOUT_MS = 30_000


def _cart(*lines: CartLine, merchant: str = "m_taj") -> Cart:
    return Cart(merchant=merchant, currency="INR", lines=tuple(lines))


def _line(sku: str, amount: int, mcc: int = 5812, category: str = "dining") -> CartLine:
    return CartLine(sku=sku, description=sku, amount=amount, mcc=mcc, category=category)


def _manifest(*benefits: Benefit) -> Manifest:
    return build_manifest(
        manifest_id="mf_oracle_test",
        issuer="test_issuer",
        product="test_product",
        benefits=benefits,
        issued_at=T0,
    )


def _random_instance(seed: int) -> tuple[Manifest, Cart]:
    """A small instance, sized so brute force stays tractable."""
    rng = random.Random(seed)
    cart = _cart(
        *(
            _line(
                f"sku_{i}",
                rng.randrange(1_000, 80_000),
                rng.choice(MCCS),
                rng.choice(("dining", "travel")),
            )
            for i in range(rng.randrange(1, 4))
        )
    )
    benefits = []
    for i in range(rng.randrange(1, 4)):
        kind = rng.choice((KIND_CREDIT, KIND_EARN, KIND_PROTECTION))
        benefits.append(
            Benefit(
                benefit_id=f"ben_{i}",
                kind=kind,
                label=f"ben_{i}",
                eligibility=Eligibility(mccs=(rng.choice(MCCS),)),
                rate_bp=rng.randrange(100, 900) if kind == KIND_EARN else 0,
                capacity_minor=rng.choice((None, rng.randrange(500, 50_000))),
                flat_minor=rng.randrange(100, 9_000) if kind == KIND_PROTECTION else 0,
                exclusivity_group=rng.choice((None, "grp_a")),
            )
        )
    return _manifest(*benefits), cart


# --------------------------------------------------------------------------------------
# 1. Same problem: Z3 against brute force
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(60))
def test_oracle_optimum_equals_brute_force(seed: int) -> None:
    """The Z3 encoding and an independent enumeration must agree exactly.

    This is the test that makes the gap a gap. If the encoding were wrong the difference
    would still be reported as a number, and it would be quoted on a slide as the
    allocator's shortfall rather than as the oracle's bug.
    """
    manifest, cart = _random_instance(seed)
    if len(candidates(manifest, cart)) > BRUTE_FORCE_MAX_CANDIDATES:
        pytest.skip("instance too large for exhaustive enumeration")

    result = optimum(manifest, cart, timeout_ms=TEST_TIMEOUT_MS)
    assert result.status == STATUS_OPTIMAL, result.detail
    assert result.optimum_minor == brute_force_optimum(manifest, cart)


@pytest.mark.parametrize("seed", range(60))
def test_greedy_never_exceeds_the_optimum(seed: int) -> None:
    """The whole soundness argument, cross-checked against a solver.

    The greedy witness is a feasible point of the model the oracle maximises, so it can
    never come out above the optimum. If it ever does, either the allocator emitted an
    infeasible allocation or the oracle is modelling a different problem — and both make
    every published number meaningless.
    """
    manifest, cart = _random_instance(seed)
    result = optimum(manifest, cart, timeout_ms=TEST_TIMEOUT_MS)
    if not result.resolved:
        pytest.skip(f"oracle did not resolve: {result.reason}")
    assert allocate(manifest, cart).witness.realized_minor() <= result.optimum_minor


@pytest.mark.parametrize("seed", range(30))
def test_oracle_witness_verifies_and_realizes_the_optimum(seed: int) -> None:
    """The oracle's own number is constructive too: an exhibited, verifiable allocation."""
    manifest, cart = _random_instance(seed)
    result = optimum(manifest, cart, timeout_ms=TEST_TIMEOUT_MS)
    if not result.resolved:
        pytest.skip(f"oracle did not resolve: {result.reason}")

    assert result.witness is not None
    verification = verify_witness(
        witness=result.witness,
        manifest=manifest,
        cart=cart,
        asserted_minor=result.optimum_minor,
    )
    assert verification.ok, verification.codes()
    assert verification.realized_minor == result.optimum_minor


def test_oracle_beats_greedy_on_a_packing_trap() -> None:
    """A cart where greedy provably leaves value behind, so the gap is not hypothetical.

    One multiplier at 1% with ₹10 of annual headroom left, and three eligible lines of
    ₹600, ₹500 and ₹500. Greedy takes the largest yield first (₹6), stranding ₹4 of
    headroom that neither remaining ₹5 yield fits. Taking the two ₹5 yields instead
    realizes the whole ₹10 — 40% more, from the same manifest and the same cart.
    """
    cart = _cart(
        _line("sku_a", 60_000, category="x"),
        _line("sku_b", 50_000, category="x"),
        _line("sku_c", 50_000, category="x"),
    )
    manifest = _manifest(
        Benefit(
            benefit_id="ben_earn",
            kind=KIND_EARN,
            label="1% back, ₹10 of annual cap left",
            eligibility=Eligibility(categories=("x",)),
            rate_bp=100,
            capacity_minor=1_000,
        )
    )

    greedy = allocate(manifest, cart).witness.realized_minor()
    result = optimum(manifest, cart, timeout_ms=TEST_TIMEOUT_MS)

    assert greedy == 600
    assert result.optimum_minor == 1_000
    assert result.optimum_minor == brute_force_optimum(manifest, cart)


def test_exclusivity_is_modelled_not_summed() -> None:
    """Two credits in one group cannot both claim the dinner. This is the demo case."""
    cart = _cart(_line("sku_dinner", 100_000))
    manifest = _manifest(
        Benefit(
            benefit_id="ben_a",
            kind=KIND_CREDIT,
            label="dining credit A",
            eligibility=Eligibility(mccs=(5812,)),
            capacity_minor=30_000,
            exclusivity_group="grp_dining",
        ),
        Benefit(
            benefit_id="ben_b",
            kind=KIND_CREDIT,
            label="dining credit B",
            eligibility=Eligibility(mccs=(5812,)),
            capacity_minor=20_000,
            exclusivity_group="grp_dining",
        ),
    )
    result = optimum(manifest, cart, timeout_ms=TEST_TIMEOUT_MS)
    assert result.optimum_minor == 30_000


def test_per_line_offset_bound_is_modelled() -> None:
    """Credits with no declared group still cannot offset more than the line costs.

    Exclusivity groups are a manifest-authoring convenience. This bound is structural, and
    an oracle that missed it would report an optimum no valid witness could reach — which
    would show up as a permanent negative-looking gap against a correct allocator.
    """
    cart = _cart(_line("sku_dinner", 40_000))
    manifest = _manifest(
        Benefit(
            benefit_id="ben_a",
            kind=KIND_CREDIT,
            label="credit A",
            eligibility=Eligibility(mccs=(5812,)),
            capacity_minor=30_000,
        ),
        Benefit(
            benefit_id="ben_b",
            kind=KIND_CREDIT,
            label="credit B",
            eligibility=Eligibility(mccs=(5812,)),
            capacity_minor=30_000,
        ),
    )
    result = optimum(manifest, cart, timeout_ms=TEST_TIMEOUT_MS)
    assert result.optimum_minor == 40_000
    assert result.optimum_minor == brute_force_optimum(manifest, cart)


def test_capacity_bounds_a_credit_across_lines() -> None:
    cart = _cart(_line("sku_a", 40_000), _line("sku_b", 40_000))
    manifest = _manifest(
        Benefit(
            benefit_id="ben_credit",
            kind=KIND_CREDIT,
            label="₹500 credit",
            eligibility=Eligibility(mccs=(5812,)),
            capacity_minor=50_000,
        )
    )
    assert optimum(manifest, cart, timeout_ms=TEST_TIMEOUT_MS).optimum_minor == 50_000


def test_unpriced_benefits_are_never_allocated() -> None:
    """CONSIDERED-BUT-UNPRICED means the agent saw it, not that it scored."""
    cart = _cart(_line("sku_a", 40_000))
    manifest = _manifest(
        Benefit(benefit_id="ben_perk", kind=KIND_UNPRICED, label="lounge access")
    )
    result = optimum(manifest, cart, timeout_ms=TEST_TIMEOUT_MS)
    assert result.optimum_minor == 0
    assert result.witness is not None
    assert result.witness.assignments == ()


def test_unenrolled_benefit_yields_nothing() -> None:
    cart = _cart(_line("sku_a", 40_000))
    manifest = _manifest(
        Benefit(
            benefit_id="ben_offer",
            kind=KIND_CREDIT,
            label="enrollment-gated offer",
            eligibility=Eligibility(mccs=(5812,)),
            capacity_minor=20_000,
            requires_enrollment=True,
            enrolled=False,
        )
    )
    assert optimum(manifest, cart, timeout_ms=TEST_TIMEOUT_MS).optimum_minor == 0


def test_no_eligible_candidate_is_a_resolved_zero() -> None:
    """Zero is an answer, not a failure — and the empty witness exhibits it."""
    cart = _cart(_line("sku_a", 40_000, mcc=5411))
    manifest = _manifest(
        Benefit(
            benefit_id="ben_travel",
            kind=KIND_CREDIT,
            label="travel credit",
            eligibility=Eligibility(mccs=(7011,)),
            capacity_minor=20_000,
        )
    )
    result = optimum(manifest, cart, timeout_ms=TEST_TIMEOUT_MS)
    assert result.status == STATUS_OPTIMAL
    assert result.optimum_minor == 0
    assert result.candidates == 0


def test_oracle_is_deterministic() -> None:
    manifest, cart = _random_instance(7)
    first = optimum(manifest, cart, timeout_ms=TEST_TIMEOUT_MS)
    second = optimum(manifest, cart, timeout_ms=TEST_TIMEOUT_MS)
    assert first.optimum_minor == second.optimum_minor
    assert first.witness == second.witness


def test_duplicate_sku_is_refused_with_an_actionable_message() -> None:
    cart = _cart(_line("sku_same", 10_000), _line("sku_same", 20_000))
    manifest = _manifest(
        Benefit(
            benefit_id="ben_a",
            kind=KIND_CREDIT,
            label="credit",
            eligibility=Eligibility(mccs=(5812,)),
            capacity_minor=50_000,
        )
    )
    with pytest.raises(OracleModelError) as excinfo:
        optimum(manifest, cart, timeout_ms=TEST_TIMEOUT_MS)
    assert "sku_same" in str(excinfo.value)
    assert "fold" in str(excinfo.value)


def test_brute_force_refuses_instances_it_cannot_enumerate() -> None:
    cart = _cart(*(_line(f"sku_{i}", 10_000 + i) for i in range(10)))
    manifest = _manifest(
        *(
            Benefit(
                benefit_id=f"ben_{i}",
                kind=KIND_CREDIT,
                label=f"credit {i}",
                eligibility=Eligibility(mccs=(5812,)),
                capacity_minor=5_000,
            )
            for i in range(5)
        )
    )
    with pytest.raises(OracleModelError) as excinfo:
        brute_force_optimum(manifest, cart)
    assert str(BRUTE_FORCE_MAX_CANDIDATES) in str(excinfo.value)


def test_optimum_rejects_a_nonpositive_timeout() -> None:
    manifest, cart = _random_instance(1)
    with pytest.raises(OracleModelError):
        optimum(manifest, cart, timeout_ms=0)


# --------------------------------------------------------------------------------------
# 2. It never signs a number it did not establish
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(4))
def test_a_1ms_timeout_yields_unknown_and_no_number(seed: int) -> None:
    """The end-to-end version of the argument, at the size the panel benchmarked.

    A 1ms budget on 20 lines x 40 benefits cannot finish. What matters is not that it
    fails but how: UNKNOWN with a reason, and `optimum_minor` is None, so there is no
    number for a caller to pick up by accident.
    """
    from plumbline.bench import generate_instance

    manifest, cart = generate_instance(20260825 + seed, n_lines=20, n_benefits=40)
    result = optimum(manifest, cart, timeout_ms=1)

    assert result.status == STATUS_UNKNOWN
    assert result.optimum_minor is None
    assert result.witness is None
    assert result.reason in {
        REASON_TIMEOUT,
        REASON_INCOHERENT_BOUNDS,
        REASON_NON_NUMERAL_BOUND,
        REASON_BOUNDS_NOT_CLOSED,
    }
    assert result.detail


def test_require_optimum_refuses_to_substitute_a_bound() -> None:
    from plumbline.bench import generate_instance

    manifest, cart = generate_instance(20260825, n_lines=20, n_benefits=40)
    result = optimum(manifest, cart, timeout_ms=1)
    with pytest.raises(OracleModelError) as excinfo:
        result.require_optimum()
    assert "do not substitute a bound" in str(excinfo.value)


def test_classify_bounds_names_the_incoherent_case() -> None:
    """The exact pathology the adversary measured: lower above upper after a timeout."""
    status, reason, detail = classify_bounds(
        result=RESULT_UNKNOWN, lower_minor=500, upper_minor=100
    )
    assert status == STATUS_UNKNOWN
    assert reason == REASON_INCOHERENT_BOUNDS
    assert "500" in detail and "100" in detail


def test_incoherent_bounds_outrank_a_sat_result() -> None:
    """Incoherence is checked before anything else, sat included.

    A solver claiming sat while its bounds cross is not reporting an optimum whatever the
    check said, and the naming matters: folded into "timeout" it would look like a budget
    problem rather than evidence that reading a bound is unsafe.
    """
    status, reason, _ = classify_bounds(
        result=RESULT_SAT, lower_minor=9_999, upper_minor=1
    )
    assert (status, reason) == (STATUS_UNKNOWN, REASON_INCOHERENT_BOUNDS)


def test_classify_bounds_rejects_non_numeral_bounds() -> None:
    """Infinity and epsilon expressions are not integers, so they are not an optimum."""
    for lower, upper in ((None, 100), (100, None), (None, None)):
        status, reason, _ = classify_bounds(
            result=RESULT_SAT, lower_minor=lower, upper_minor=upper
        )
        assert (status, reason) == (STATUS_UNKNOWN, REASON_NON_NUMERAL_BOUND)


def test_classify_bounds_rejects_unsat() -> None:
    """Unsat cannot be true — the empty allocation is always feasible — so it is a bug."""
    status, reason, detail = classify_bounds(
        result=RESULT_UNSAT, lower_minor=0, upper_minor=0
    )
    assert (status, reason) == (STATUS_UNKNOWN, REASON_UNSAT)
    assert "empty allocation" in detail


def test_classify_bounds_rejects_an_unfinished_search() -> None:
    status, reason, _ = classify_bounds(
        result=RESULT_UNKNOWN, lower_minor=100, upper_minor=900
    )
    assert (status, reason) == (STATUS_UNKNOWN, REASON_TIMEOUT)


def test_classify_bounds_rejects_bounds_that_did_not_close() -> None:
    status, reason, _ = classify_bounds(
        result=RESULT_SAT, lower_minor=100, upper_minor=900
    )
    assert (status, reason) == (STATUS_UNKNOWN, REASON_BOUNDS_NOT_CLOSED)


def test_classify_bounds_accepts_a_closed_coherent_optimum() -> None:
    status, reason, detail = classify_bounds(
        result=RESULT_SAT, lower_minor=4_200, upper_minor=4_200
    )
    assert (status, reason, detail) == (STATUS_OPTIMAL, None, "")


def test_the_oracle_discards_its_own_number_if_the_witness_fails(monkeypatch) -> None:
    """Fault injection: if the extracted allocation does not verify, nothing is published.

    This is the last line of defence. Even with a coherent, closed optimum from the
    solver, a number whose allocation a counterparty would reject is not reported.
    """
    import plumbline.oracle as oracle_module

    def rejecting(**kwargs):
        return Verification(ok=False, realized_minor=0, asserted_minor=0, failures=())

    monkeypatch.setattr(oracle_module, "verify_witness", rejecting)

    manifest, cart = _random_instance(3)
    result = optimum(manifest, cart, timeout_ms=TEST_TIMEOUT_MS)
    assert result.status == STATUS_UNKNOWN
    assert result.reason == REASON_WITNESS_REJECTED
    assert result.optimum_minor is None
    assert result.witness is None


@pytest.mark.parametrize("seed", range(20))
def test_a_number_is_present_exactly_when_the_status_is_optimal(seed: int) -> None:
    manifest, cart = _random_instance(seed)
    result = optimum(manifest, cart, timeout_ms=TEST_TIMEOUT_MS)
    assert (result.optimum_minor is not None) == (result.status == STATUS_OPTIMAL)
    assert (result.witness is not None) == (result.status == STATUS_OPTIMAL)
    assert (result.reason is None) == (result.status == STATUS_OPTIMAL)


def test_result_to_dict_is_json_shaped() -> None:
    manifest, cart = _random_instance(2)
    payload = optimum(manifest, cart, timeout_ms=TEST_TIMEOUT_MS).to_dict(
        currency=cart.currency
    )
    for key in (
        "status",
        "resolved",
        "optimum_minor",
        "witness",
        "reason",
        "detail",
        "elapsed_ms",
        "candidates",
        "variables",
        "constraints",
        "timeout_ms",
        "lower_minor",
        "upper_minor",
    ):
        assert key in payload
    assert payload["resolved"] is (payload["status"] == STATUS_OPTIMAL)


# --------------------------------------------------------------------------------------
# 3. It cannot reach the checkout path
# --------------------------------------------------------------------------------------


def test_assert_offline_rejects_a_hot_path_importer() -> None:
    with pytest.raises(OfflineOnlyError) as excinfo:
        assert_offline(caller_modules=["plumbline.allocate"], env={})
    message = str(excinfo.value)
    assert "plumbline.allocate" in message
    # The error has to say what to do instead, or it just moves the argument to a review.
    assert "plumbline.allocate.allocate" in message


def test_assert_offline_names_every_offender() -> None:
    with pytest.raises(OfflineOnlyError) as excinfo:
        assert_offline(caller_modules=["caveat.api", "plumbline.witness", "tests"], env={})
    message = str(excinfo.value)
    assert "caveat.api" in message and "plumbline.witness" in message


def test_assert_offline_allows_offline_callers() -> None:
    assert_offline(caller_modules=["plumbline.bench", "tests.test_plumbline_oracle"], env={})


def test_assert_offline_rejects_a_declared_checkout_process() -> None:
    with pytest.raises(OfflineOnlyError) as excinfo:
        assert_offline(caller_modules=[], env={CHECKOUT_PROCESS_ENV: "1"})
    assert CHECKOUT_PROCESS_ENV in str(excinfo.value)


@pytest.mark.parametrize("value", ["", "0", "false", "False", "  "])
def test_falsey_checkout_markers_do_not_trip_the_guard(value: str) -> None:
    assert_offline(caller_modules=[], env={CHECKOUT_PROCESS_ENV: value})


def test_a_checkout_process_cannot_import_the_oracle_at_all() -> None:
    """The guard in a real interpreter, not a unit test of its arguments."""
    env = dict(os.environ, PYTHONPATH=str(BACKEND), **{CHECKOUT_PROCESS_ENV: "1"})
    proc = subprocess.run(
        [sys.executable, "-c", "import plumbline.oracle"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode != 0
    assert "OfflineOnlyError" in proc.stderr


def test_no_checkout_module_imports_the_oracle_or_the_harness() -> None:
    """Static import graph. The guard is a backstop; this is the actual enforcement.

    Parses each checkout-path module rather than importing it, so the test fails on the
    commit that adds the import instead of at the runtime that would have executed it.
    """
    scanned = 0
    for name in sorted(HOT_PATH_MODULES):
        path = BACKEND / (name.replace(".", os.sep) + ".py")
        if not path.exists():
            continue  # modules on the roadmap that are not written yet
        scanned += 1
        for imported in _imported_names(path):
            assert "oracle" not in imported, f"{name} imports {imported}"
            assert not imported.endswith("bench"), f"{name} imports {imported}"
    # A guard that scans nothing passes trivially, so the count is asserted too.
    assert scanned >= 5, (
        f"only {scanned} of {len(HOT_PATH_MODULES)} listed checkout modules exist; the "
        f"list has gone stale and is protecting less than it appears to"
    )


def test_the_hot_path_list_covers_the_valuation_modules() -> None:
    """A guard listing modules that do not exist would pass while protecting nothing."""
    for name in ("plumbline.allocate", "plumbline.witness", "plumbline.manifest", "caveat.api"):
        assert name in HOT_PATH_MODULES
        assert (BACKEND / (name.replace(".", os.sep) + ".py")).exists()


def test_valuation_pulls_in_no_solver_even_transitively() -> None:
    """"Verification needs no solver" is checked, not asserted.

    Imports the whole valuation path in a clean interpreter and asserts z3 never loaded.
    A transitive dependency would defeat any amount of care in the import statements.
    """
    env = dict(os.environ, PYTHONPATH=str(BACKEND))
    env.pop(CHECKOUT_PROCESS_ENV, None)
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, plumbline.allocate, plumbline.witness, plumbline.manifest;"
            "loaded=[m for m in sys.modules if m=='z3' or m.startswith('z3.')];"
            "print(loaded); sys.exit(1 if loaded else 0)",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, f"a solver reached the valuation path: {proc.stdout}"


def _imported_names(path: Path) -> set[str]:
    """Every module name a file imports, relative imports included."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add(module)
            names.update(
                f"{module}.{alias.name}" if module else alias.name
                for alias in node.names
            )
    return names
