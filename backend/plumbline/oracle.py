"""The offline oracle: an exact solver, used to measure how far below optimal greedy sits.

OFFLINE ONLY. This module must never be reachable from a checkout. Two guards enforce
that rather than asking politely — see `assert_offline` below and the import-graph test in
backend/tests/test_plumbline_oracle.py.

Why it is offline is the same reason the hot path is greedy, and it is worth stating in
full because the panel benchmarked it rather than arguing about it.

Measured at 8 instruments x 20 lines x 40 benefits, a solver on the hot path ran 451ms to
2695ms — six-fold variance at constant problem size — and timed out at 2s. Worse than
slow: on timeout Z3's Optimize can report a lower bound ABOVE its upper bound, because the
two bounds are maintained by different search procedures and neither is required to be
sound when the search is cut short. An implementation that reads `opt.upper(h)` after a
timeout therefore signs a number that is not a bound on anything. There is no
dynamic-programming fallback either: capacitated assignment with exclusivity groups is a
generalized assignment problem, which is NP-hard.

So the hot path asserts a value only if it can exhibit a concrete allocation realizing it,
and the assertion is sound because the allocation is achievable. That argument needs no
solver. What it does not give you is a distance: an exhibited allocation proves "at least
this much" and says nothing about how much was left on the table.

That distance is what this module measures, offline, where a 2.7s solve costs nothing and
a timeout costs an UNKNOWN rather than an incoherent signature.

Three properties make the measurement trustworthy:

  * It is the SAME problem. The model is built from `allocate.candidates`, the hot path's
    own enumeration, with the hot path's own per-candidate values. A test checks the
    oracle's optimum against brute-force enumeration on small instances.
  * It never reports a bound it cannot stand behind. Every path that is not a closed,
    coherent optimum returns UNKNOWN with a reason code. The specific pathology the
    adversary measured — lower > upper — is detected by name.
  * Its own optimum is witness-backed. The oracle extracts the allocation Z3 found and
    runs it through the same linear verifier a counterparty would use. If the witness does
    not verify, or does not realize the claimed optimum, the result is UNKNOWN. The
    offline number is constructive too.

Honest limitations:
  * An UNKNOWN is not evidence of a small gap. Instances that time out are reported as
    unresolved and excluded from the gap distribution; a gap quoted from this module is a
    gap over the instances that resolved. The unresolved fraction is reported alongside.
  * The gap is measured over generated instances, not observed carts. It characterises the
    allocator on the distribution described in bench.py, and nothing wider.
"""

from __future__ import annotations

import inspect
import os
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import z3

from caveat.cart import Cart, CartLine
from caveat.money import fmt_currency

from .allocate import Candidate, candidates
from .manifest import KIND_CREDIT, Manifest
from .witness import Assignment, Witness, verify_witness

# Declared for anything that wants to check a module's posture before importing it.
OFFLINE_ONLY = True

# Modules that run inside a checkout. Importing the oracle from any of them is a defect,
# not a preference: it puts an unbounded-latency solver behind a latency budget. Names are
# fully qualified module names as they appear in `__name__`.
HOT_PATH_MODULES = frozenset(
    {
        "plumbline.allocate",
        "plumbline.witness",
        "plumbline.manifest",
        "plumbline.evaluate",
        "plumbline.receipt",
        "caveat.api",
        "caveat.engine",
        "caveat.pdp",
        "caveat.stepup",
        "caveat.adapters.ap2",
        "caveat.adapters.acp",
        "caveat.adapters.mcp",
    }
)

# A serving process sets this so an accidental import fails at boot, loudly, rather than
# at the p99 of a checkout.
CHECKOUT_PROCESS_ENV = "PLUMBLINE_CHECKOUT_PROCESS"

# Result statuses. A caller may read `optimum_minor` if and only if status is OPTIMAL.
STATUS_OPTIMAL = "ORACLE_OPTIMAL"
STATUS_UNKNOWN = "ORACLE_UNKNOWN"

# Why an optimum was not established. Closed vocabulary.
REASON_TIMEOUT = "ORACLE_TIMEOUT"
REASON_INCOHERENT_BOUNDS = "ORACLE_INCOHERENT_BOUNDS"
REASON_NON_NUMERAL_BOUND = "ORACLE_NON_NUMERAL_BOUND"
REASON_BOUNDS_NOT_CLOSED = "ORACLE_BOUNDS_NOT_CLOSED"
REASON_UNSAT = "ORACLE_UNSAT_UNEXPECTED"
REASON_WITNESS_REJECTED = "ORACLE_WITNESS_REJECTED"

# Solver check outcomes, as strings so the classifier is pure and testable without z3.
RESULT_SAT = "sat"
RESULT_UNSAT = "unsat"
RESULT_UNKNOWN = "unknown"

DEFAULT_TIMEOUT_MS = 10_000

# Brute force is 2**n over candidates. Past this the caller wants the solver, and saying
# so beats a process that appears to hang.
BRUTE_FORCE_MAX_CANDIDATES = 18


class OfflineOnlyError(ImportError):
    """Raised when the oracle is pulled into a process or module that serves checkouts."""


class OracleModelError(ValueError):
    """Raised when an instance cannot be modelled — a malformed cart, not a hard one."""


def assert_offline(
    caller_modules: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Refuse to be part of a checkout path.

    Two independent signals, because they fail at different times. `caller_modules` is the
    chain of modules importing this one, which catches a hot-path module growing an import
    at the moment it is added. `env` catches a serving process that reaches the oracle by
    some route the import graph did not show — a plugin, a lazy import inside a handler.

    Both arguments are injectable so the guard itself is unit-testable; the import-time
    call below passes the real stack and the real environment.
    """
    env = os.environ if env is None else env
    if str(env.get(CHECKOUT_PROCESS_ENV, "")).strip() not in ("", "0", "false", "False"):
        raise OfflineOnlyError(
            f"plumbline.oracle was imported in a process that declared "
            f"{CHECKOUT_PROCESS_ENV}. The oracle runs an unbounded-latency solver and "
            f"must not sit behind a checkout budget. Use plumbline.allocate.allocate for "
            f"valuation; run the oracle from the benchmark harness instead."
        )

    if caller_modules is None:
        caller_modules = _calling_modules()
    offenders = sorted(set(caller_modules) & HOT_PATH_MODULES)
    if offenders:
        raise OfflineOnlyError(
            f"plumbline.oracle was imported from {', '.join(offenders)}, which runs inside "
            f"a checkout. The oracle can take seconds and can time out without producing "
            f"a usable bound. Value carts with plumbline.allocate.allocate; measure the gap "
            f"offline with plumbline.bench."
        )


def _calling_modules() -> list[str]:
    """Module names of every frame above this one, importlib machinery included."""
    out: list[str] = []
    frame = inspect.currentframe()
    try:
        while frame is not None:
            name = frame.f_globals.get("__name__")
            if isinstance(name, str):
                out.append(name)
            frame = frame.f_back
    finally:
        del frame
    return out


assert_offline()


@dataclass(frozen=True)
class OracleResult:
    """What the solver established, and — when it established nothing — why.

    `optimum_minor` is None unless `status` is OPTIMAL. `lower_minor` and `upper_minor`
    are carried for diagnosis only; they are deliberately not usable as a value, because
    the failure this module exists to avoid is exactly a caller reading a bound that the
    solver never claimed was sound.
    """

    status: str
    optimum_minor: int | None
    witness: Witness | None
    reason: str | None
    detail: str
    elapsed_ms: float
    candidates: int
    variables: int
    constraints: int
    timeout_ms: int
    lower_minor: int | None = None
    upper_minor: int | None = None

    @property
    def resolved(self) -> bool:
        return self.status == STATUS_OPTIMAL

    def require_optimum(self) -> int:
        """The optimum, or an exception. For callers that would otherwise write `or 0`."""
        if self.optimum_minor is None:
            raise OracleModelError(
                f"no optimum was established ({self.reason}: {self.detail}). Raise the "
                f"timeout above {self.timeout_ms}ms, shrink the instance, or record this "
                f"instance as unresolved — do not substitute a bound."
            )
        return self.optimum_minor

    def to_dict(self, *, currency: str) -> dict[str, Any]:
        """Serialize. `currency` is required and keyword-only — see `Witness.to_dict`."""
        return {
            "status": self.status,
            "resolved": self.resolved,
            "optimum_minor": self.optimum_minor,
            "optimum_display": (
                fmt_currency(self.optimum_minor, currency)
                if self.optimum_minor is not None
                else None
            ),
            "witness": (
                self.witness.to_dict(currency=currency) if self.witness is not None else None
            ),
            "reason": self.reason,
            "detail": self.detail,
            "elapsed_ms": round(self.elapsed_ms, 4),
            "candidates": self.candidates,
            "variables": self.variables,
            "constraints": self.constraints,
            "timeout_ms": self.timeout_ms,
            "lower_minor": self.lower_minor,
            "upper_minor": self.upper_minor,
        }


def classify_bounds(
    *,
    result: str,
    lower_minor: int | None,
    upper_minor: int | None,
) -> tuple[str, str | None, str]:
    """Decide whether a solver run established an optimum. Pure; no z3 involved.

    The incoherent case is checked first and named separately because it is the one the
    adversary actually measured: after a timeout Z3 reported a lower bound above its upper
    bound. Folding it into "timeout" would lose the evidence that reading a bound at all
    is unsafe, which is the whole reason this module refuses to.

    Returns (status, reason, detail).
    """
    if lower_minor is not None and upper_minor is not None and lower_minor > upper_minor:
        return (
            STATUS_UNKNOWN,
            REASON_INCOHERENT_BOUNDS,
            f"solver reported lower bound {lower_minor} above upper bound {upper_minor}; "
            f"neither is a bound on the optimum, so no value is reported",
        )
    if result == RESULT_UNSAT:
        return (
            STATUS_UNKNOWN,
            REASON_UNSAT,
            "model reported unsatisfiable, which cannot be true — the empty allocation is "
            "always feasible — so the model is wrong and its output is discarded",
        )
    if lower_minor is None or upper_minor is None:
        return (
            STATUS_UNKNOWN,
            REASON_NON_NUMERAL_BOUND,
            "solver returned a non-numeral bound (infinity or an epsilon expression); "
            "no integer optimum was established",
        )
    if result != RESULT_SAT:
        return (
            STATUS_UNKNOWN,
            REASON_TIMEOUT,
            f"solver did not finish (result {result!r}); bounds [{lower_minor}, "
            f"{upper_minor}] are not an established optimum",
        )
    if lower_minor != upper_minor:
        return (
            STATUS_UNKNOWN,
            REASON_BOUNDS_NOT_CLOSED,
            f"search returned sat but bounds did not close: [{lower_minor}, "
            f"{upper_minor}]",
        )
    return (STATUS_OPTIMAL, None, "")


def optimum(
    manifest: Manifest,
    cart: Cart,
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> OracleResult:
    """Exact maximum value of this cart on this manifest, or UNKNOWN with a reason.

    Models exactly what `allocate.allocate` solves greedily:

      * one binary per (line, benefit) candidate, over the hot path's own enumeration;
      * credits carry an integer draw in [0, line amount], because a credit's value is the
        spend it offsets and it may offset part of a line;
      * earn and protection are all-or-nothing at the value the manifest declares;
      * per-benefit capacity bounds the total draw;
      * per (line, exclusivity group), at most one benefit attaches;
      * per line, the credits attaching to it cannot together offset more than it costs.

    The last constraint is what makes this a genuine assignment problem rather than a set
    of independent knapsacks: credits compete for lines and lines compete for credits at
    the same time, which is precisely the structure greedy cannot see past.

    Never blocks longer than `timeout_ms` plus witness verification.
    """
    if timeout_ms <= 0:
        raise OracleModelError(f"timeout_ms must be positive, got {timeout_ms}")

    started = time.perf_counter()
    cands = candidates(manifest, cart)
    lines = _lines_by_sku(cart)

    if not cands:
        # No eligible pairing. The optimum is zero and the empty witness exhibits it.
        return OracleResult(
            status=STATUS_OPTIMAL,
            optimum_minor=0,
            witness=Witness(
                manifest_id=manifest.manifest_id, cart_hash=cart.hash(), assignments=()
            ),
            reason=None,
            detail="",
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            candidates=0,
            variables=0,
            constraints=0,
            timeout_ms=timeout_ms,
            lower_minor=0,
            upper_minor=0,
        )

    opt = z3.Optimize()
    opt.set("timeout", timeout_ms)

    selected: list[z3.ArithRef] = []
    draws: dict[int, z3.ArithRef] = {}
    value_terms: list[Any] = []
    n_constraints = 0

    for i, cand in enumerate(cands):
        x = z3.Int(f"x_{i}")
        opt.add(x >= 0, x <= 1)
        n_constraints += 2
        selected.append(x)
        if cand.benefit.kind == KIND_CREDIT:
            # The draw is the decision, not just the flag: a credit may offset part of a
            # line when its balance runs out mid-line. Bounding it by amount * x collapses
            # to zero when the pairing is not selected.
            draw = z3.Int(f"c_{i}")
            opt.add(draw >= 0, draw <= cand.line.amount * x)
            n_constraints += 2
            draws[i] = draw
            value_terms.append(draw)
        else:
            value_terms.append(cand.value_minor * x)

    for _benefit_id, idxs in _by_benefit(cands):
        benefit = cands[idxs[0]].benefit
        if benefit.capacity_minor is None:
            continue
        draw_terms = [
            draws[i] if i in draws else cands[i].consumed_minor * selected[i] for i in idxs
        ]
        opt.add(z3.Sum(draw_terms) <= benefit.capacity_minor)
        n_constraints += 1

    for idxs in _exclusivity_groups(cands):
        opt.add(z3.Sum([selected[i] for i in idxs]) <= 1)
        n_constraints += 1

    for sku, idxs in _credit_draws_by_line(cands):
        opt.add(z3.Sum([draws[i] for i in idxs]) <= lines[sku].amount)
        n_constraints += 1

    handle = opt.maximize(z3.Sum(value_terms))
    check = opt.check()
    # CheckSatResult is not hashable, so this is a chain rather than a lookup.
    if check == z3.sat:
        result = RESULT_SAT
    elif check == z3.unsat:
        result = RESULT_UNSAT
    else:
        result = RESULT_UNKNOWN

    lower = _as_int(_safe_bound(opt.lower, handle))
    upper = _as_int(_safe_bound(opt.upper, handle))
    status, reason, detail = classify_bounds(
        result=result, lower_minor=lower, upper_minor=upper
    )

    n_vars = len(selected) + len(draws)
    witness: Witness | None = None

    if status == STATUS_OPTIMAL:
        # classify_bounds only returns OPTIMAL with both bounds integral and equal.
        established: int = upper  # type: ignore[assignment]
        witness = _extract_witness(
            opt.model(), cands, selected, draws, manifest=manifest, cart=cart
        )
        # The oracle holds itself to the standard it exists to measure: the number it
        # reports is the number an exhibited allocation realizes, checked by the same
        # linear verifier a counterparty would run.
        check_witness = verify_witness(
            witness=witness, manifest=manifest, cart=cart, asserted_minor=established
        )
        realized = witness.realized_minor()
        if not check_witness.ok or realized != established:
            codes = ", ".join(f.code for f in check_witness.failures) or "none"
            status, reason = STATUS_UNKNOWN, REASON_WITNESS_REJECTED
            detail = (
                f"solver claimed {established} but its allocation realizes {realized} "
                f"and verifies as {check_witness.ok} (failures: {codes}); the claim "
                f"is discarded rather than reported"
            )
            witness = None

    return OracleResult(
        status=status,
        optimum_minor=upper if status == STATUS_OPTIMAL else None,
        witness=witness,
        reason=reason,
        detail=detail,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
        candidates=len(cands),
        variables=n_vars,
        constraints=n_constraints,
        timeout_ms=timeout_ms,
        lower_minor=lower,
        upper_minor=upper,
    )


def brute_force_optimum(manifest: Manifest, cart: Cart) -> int:
    """The optimum by exhaustive enumeration, with no solver anywhere in it.

    Exists to check the Z3 encoding. Agreement between the two on small instances is the
    evidence that the model the oracle maximises is the model the allocator approximates —
    an encoding bug would otherwise show up as a gap, and be quoted as one.

    Enumerates every subset of the same candidate list the solver models. Earn and
    protection are all-or-nothing, so a subset fixes their contribution outright. Credits
    are not: once chosen, how much each draws is still a decision, and because credits
    compete for lines (a line cannot be offset past its cost) while lines compete for
    credits (a balance is finite), the best draws are a max-flow — benefits on one side,
    lines on the other. Max-flow with integer capacities is integral, so no rounding
    question arises.
    """
    cands = candidates(manifest, cart)
    if len(cands) > BRUTE_FORCE_MAX_CANDIDATES:
        raise OracleModelError(
            f"brute force over {len(cands)} candidates is 2**{len(cands)} subsets; the "
            f"limit is {BRUTE_FORCE_MAX_CANDIDATES}. Shrink the instance or use "
            f"optimum() — brute force is a test oracle, not a solver."
        )
    _lines_by_sku(cart)

    groups = [tuple(idxs) for idxs in _exclusivity_groups(cands)]
    by_benefit = _by_benefit(cands)
    best = 0

    for mask in range(1 << len(cands)):
        if any(sum(1 for i in idxs if mask & (1 << i)) > 1 for idxs in groups):
            continue

        total = 0
        credit_edges: list[Candidate] = []
        feasible = True
        for _benefit_id, idxs in by_benefit:
            picked = [i for i in idxs if mask & (1 << i)]
            if not picked:
                continue
            benefit = cands[picked[0]].benefit
            if benefit.kind == KIND_CREDIT:
                credit_edges.extend(cands[i] for i in picked)
                continue
            drawn = sum(cands[i].consumed_minor for i in picked)
            if benefit.capacity_minor is not None and drawn > benefit.capacity_minor:
                feasible = False
                break
            total += sum(cands[i].value_minor for i in picked)

        if not feasible:
            continue
        total += _max_credit_flow(credit_edges)
        if total > best:
            best = total

    return best


def _max_credit_flow(edges: Sequence[Candidate]) -> int:
    """Most value a set of chosen (line, credit) pairings can realize.

    Every unit of draw is worth exactly one unit of value wherever it lands, so the best
    set of draws is the maximum flow from balances through the chosen pairings into lines.
    Edmonds-Karp: the graphs here have a handful of nodes and this is a test oracle, so
    clarity beats asymptotics.
    """
    if not edges:
        return 0

    lines: dict[str, int] = {}
    benefits: dict[str, int | None] = {}
    for cand in edges:
        lines[cand.line.sku] = cand.line.amount
        benefits[cand.benefit.benefit_id] = cand.benefit.capacity_minor

    total_spend = sum(lines.values())
    source, sink = "__source__", "__sink__"
    graph: dict[str, dict[str, int]] = {source: {}, sink: {}}

    def edge(a: str, b: str, capacity: int) -> None:
        graph.setdefault(a, {})[b] = graph.setdefault(a, {}).get(b, 0) + capacity
        graph.setdefault(b, {}).setdefault(a, 0)

    for bid, cap in benefits.items():
        # An uncapped credit cannot deliver more than the cart costs, so total spend is a
        # finite stand-in for infinity and keeps every capacity an int.
        edge(source, f"b:{bid}", total_spend if cap is None else cap)
    for sku, amount in lines.items():
        edge(f"l:{sku}", sink, amount)
    for cand in edges:
        edge(f"b:{cand.benefit.benefit_id}", f"l:{cand.line.sku}", cand.line.amount)

    flow = 0
    while True:
        parent: dict[str, str] = {source: source}
        queue = [source]
        while queue and sink not in parent:
            node = queue.pop(0)
            for nxt, capacity in graph[node].items():
                if capacity > 0 and nxt not in parent:
                    parent[nxt] = node
                    queue.append(nxt)
        if sink not in parent:
            return flow

        bottleneck = total_spend
        node = sink
        while node != source:
            bottleneck = min(bottleneck, graph[parent[node]][node])
            node = parent[node]
        node = sink
        while node != source:
            graph[parent[node]][node] -= bottleneck
            graph[node][parent[node]] += bottleneck
            node = parent[node]
        flow += bottleneck


def _lines_by_sku(cart: Cart) -> dict[str, CartLine]:
    """SKU is the key the allocator, the verifier and the witness all use.

    A cart with two lines sharing a SKU has no well-defined witness — the verifier looks
    lines up by SKU and would silently score one of them twice — so the oracle refuses it
    rather than optimising a problem nobody else is solving.
    """
    out: dict[str, CartLine] = {}
    for line in cart.lines:
        if line.sku in out:
            raise OracleModelError(
                f"cart has two lines with sku {line.sku!r}; witnesses key assignments by "
                f"sku, so lines must be folded before valuation (see cart.diff_carts)"
            )
        out[line.sku] = line
    return out


def _by_benefit(cands: Sequence[Candidate]) -> list[tuple[str, list[int]]]:
    """Candidate indices grouped by benefit, in first-appearance order."""
    order: list[str] = []
    groups: dict[str, list[int]] = {}
    for i, cand in enumerate(cands):
        bid = cand.benefit.benefit_id
        if bid not in groups:
            groups[bid] = []
            order.append(bid)
        groups[bid].append(i)
    return [(bid, groups[bid]) for bid in order]


def _exclusivity_groups(cands: Sequence[Candidate]) -> list[list[int]]:
    """Candidate indices that compete: same line, same exclusivity group, 2+ benefits."""
    buckets: dict[tuple[str, str], list[int]] = {}
    order: list[tuple[str, str]] = []
    for i, cand in enumerate(cands):
        group = cand.benefit.exclusivity_group
        if not group:
            continue
        key = (cand.line.sku, group)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(i)
    return [buckets[k] for k in order if len(buckets[k]) > 1]


def _credit_draws_by_line(cands: Sequence[Candidate]) -> list[tuple[str, list[int]]]:
    """Credit candidate indices grouped by line, in first-appearance order."""
    buckets: dict[str, list[int]] = {}
    order: list[str] = []
    for i, cand in enumerate(cands):
        if cand.benefit.kind != KIND_CREDIT:
            continue
        sku = cand.line.sku
        if sku not in buckets:
            buckets[sku] = []
            order.append(sku)
        buckets[sku].append(i)
    return [(sku, buckets[sku]) for sku in order]


def _safe_bound(getter: Any, handle: Any) -> Any:
    """Bound extraction must not raise; a solver that cannot report is an UNKNOWN."""
    try:
        return getter(handle)
    except z3.Z3Exception:
        return None


def _as_int(expr: Any) -> int | None:
    """An integer bound, or None for infinity, epsilon expressions and anything unparsed."""
    if expr is None:
        return None
    try:
        if z3.is_int_value(expr):
            return expr.as_long()
    except z3.Z3Exception:
        return None
    return None


def _extract_witness(
    model: Any,
    cands: Sequence[Candidate],
    selected: Sequence[Any],
    draws: Mapping[int, Any],
    *,
    manifest: Manifest,
    cart: Cart,
) -> Witness:
    """Read the allocation out of a model, in the same normal form the allocator emits."""
    assignments: list[Assignment] = []
    for i, cand in enumerate(cands):
        if _model_int(model, selected[i]) != 1:
            continue
        if cand.benefit.kind == KIND_CREDIT:
            drawn = _model_int(model, draws[i])
            if drawn <= 0:
                # Selected with a zero draw yields nothing; recording it would put an
                # assignment worth nothing into a signed derivation.
                continue
            assignments.append(
                Assignment(
                    line_sku=cand.line.sku,
                    benefit_id=cand.benefit.benefit_id,
                    consumed_minor=drawn,
                    value_minor=drawn,
                )
            )
        else:
            assignments.append(
                Assignment(
                    line_sku=cand.line.sku,
                    benefit_id=cand.benefit.benefit_id,
                    consumed_minor=cand.consumed_minor,
                    value_minor=cand.value_minor,
                )
            )
    assignments.sort(key=lambda a: (a.line_sku, a.benefit_id))
    return Witness(
        manifest_id=manifest.manifest_id,
        cart_hash=cart.hash(),
        assignments=tuple(assignments),
    )


def _model_int(model: Any, var: Any) -> int:
    value = model.eval(var, model_completion=True)
    return value.as_long() if z3.is_int_value(value) else 0
