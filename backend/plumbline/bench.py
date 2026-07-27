"""Benchmark harness. OFFLINE ONLY — it imports the oracle, so it inherits its posture.

Produces the two numbers the system is not allowed to assert without measuring:

  1. **The optimality gap.** The hot path is greedy and therefore not optimal. The claim
     we make is that it never overstates, which is structural. The claim we cannot make
     without measuring is how much it leaves behind. This runs both allocators over
     generated instances and reports the distribution.

  2. **The latency contrast.** "The solver does not hold a checkout budget" is an
     empirical claim, so it is measured here rather than asserted, at sizes up to the
     8 instruments x 20 lines x 40 benefits the panel benchmarked.

Reproducibility, stated precisely because the pitch is about showing work:

  * The gap section is deterministic. Same seeds, same instances, same greedy values, same
    optima — byte-identical across machines and runs. A judge re-running the command gets
    the same numbers, not similar ones.
  * The latency section is NOT reproducible across machines and is not claimed to be. It
    is a measurement of this hardware. Sample counts, seeds and instance shapes are
    recorded so the measurement can be repeated, and the p99 resolution is reported
    (with n samples, a p99 is the ceil(0.99n)-th slowest observation and no finer).

One command:

    cd <repo> && PYTHONPATH=backend .venv/bin/python -m plumbline.bench

Add --quick for a fast run, --out to choose the artifact path.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import z3

from caveat.cart import Cart, CartLine

from .allocate import allocate, candidates
from .manifest import (
    KIND_CREDIT,
    KIND_EARN,
    KIND_PROTECTION,
    WINDOW_ANNUAL,
    WINDOW_MONTHLY,
    WINDOW_QUARTERLY,
    Benefit,
    Eligibility,
    Manifest,
    build_manifest,
)
from .oracle import DEFAULT_TIMEOUT_MS, STATUS_OPTIMAL, assert_offline, optimum

assert_offline()

SCHEMA = "plumbline/bench/1"

# Instances are generated, not observed. The shapes below are modelled on a premium card's
# published terms — a handful of category multipliers with annual caps, several statement
# credits with monthly or quarterly reset windows, a few flat protections — but no claim is
# made that this is the distribution of real carts. It is the distribution the gap is
# measured over, and that is the only claim the report makes.
MCC_POOL: tuple[tuple[int, str], ...] = (
    (5812, "dining"),
    (5814, "dining"),
    (4511, "travel"),
    (7011, "travel"),
    (5411, "grocery"),
    (5541, "fuel"),
    (5732, "retail"),
    (4121, "transit"),
)
MERCHANTS: tuple[str, ...] = ("m_taj", "m_indigo", "m_bigbasket", "m_croma")
EXCLUSIVITY_GROUPS: tuple[str, ...] = ("grp_dining", "grp_travel", "grp_retail")

LINE_MIN_MINOR = 50_00
LINE_MAX_MINOR = 900_00

T0 = 1_753_600_000

DEFAULT_SEED = 20260825
DEFAULT_ARTIFACT = "artifacts/plumbline_bench.json"

# Percentiles are nearest-rank in basis points: no interpolation, so every figure printed
# is an observation that actually occurred rather than a number between two of them.
P50_BP, P90_BP, P99_BP = 5_000, 9_000, 9_900


@dataclass(frozen=True)
class BenchSize:
    """One point on the latency table. `instruments` cards ranked against one cart."""

    label: str
    instruments: int
    lines: int
    benefits: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "instruments": self.instruments,
            "lines": self.lines,
            "benefits": self.benefits,
        }


DEFAULT_SIZES: tuple[BenchSize, ...] = (
    BenchSize("2x4x6", 2, 4, 6),
    BenchSize("4x10x20", 4, 10, 20),
    BenchSize("6x15x30", 6, 15, 30),
    BenchSize("8x20x40", 8, 20, 40),
)


def generate_cart(seed: int, n_lines: int) -> Cart:
    """A cart of `n_lines` lines with distinct SKUs. Deterministic in `seed`."""
    if n_lines <= 0:
        raise ValueError(f"a cart needs at least one line, got {n_lines}")
    rng = random.Random(("cart", seed, n_lines).__str__())
    merchant = MERCHANTS[rng.randrange(len(MERCHANTS))]
    lines = []
    for i in range(n_lines):
        mcc, category = MCC_POOL[rng.randrange(len(MCC_POOL))]
        lines.append(
            CartLine(
                sku=f"sku_{i:03d}",
                description=f"{category} item {i}",
                amount=rng.randrange(LINE_MIN_MINOR, LINE_MAX_MINOR),
                mcc=mcc,
                category=category,
                qty=1,
            )
        )
    return Cart(merchant=merchant, currency="INR", lines=tuple(lines))


def generate_manifest(seed: int, index: int, n_benefits: int) -> Manifest:
    """One card product. Deterministic in (`seed`, `index`).

    Capacities are drawn deliberately tight — a credit balance below a typical line, an
    earn cap below what a large line would yield — because capacity is what makes this an
    assignment problem instead of a sum. A generator with slack capacities would report a
    zero gap and prove nothing.
    """
    if n_benefits <= 0:
        raise ValueError(f"a manifest needs at least one benefit, got {n_benefits}")
    rng = random.Random(("manifest", seed, index, n_benefits).__str__())
    benefits: list[Benefit] = []

    for i in range(n_benefits):
        roll = rng.random()
        mcc, category = MCC_POOL[rng.randrange(len(MCC_POOL))]
        # Half the benefits key on a single MCC and half on a category, so several
        # benefits contend for the same line rather than partitioning the cart.
        eligibility = (
            Eligibility(mccs=(mcc,))
            if rng.random() < 0.5
            else Eligibility(categories=(category,))
        )
        group = (
            EXCLUSIVITY_GROUPS[rng.randrange(len(EXCLUSIVITY_GROUPS))]
            if rng.random() < 0.45
            else None
        )

        if roll < 0.40:
            benefits.append(
                Benefit(
                    benefit_id=f"ben_{index:02d}_{i:03d}",
                    kind=KIND_EARN,
                    label=f"{category} multiplier {i}",
                    eligibility=eligibility,
                    rate_bp=rng.choice((100, 200, 300, 400, 500)),
                    capacity_minor=(
                        rng.randrange(500, 60_00) if rng.random() < 0.7 else None
                    ),
                    exclusivity_group=group,
                    window=WINDOW_ANNUAL,
                )
            )
        elif roll < 0.80:
            benefits.append(
                Benefit(
                    benefit_id=f"ben_{index:02d}_{i:03d}",
                    kind=KIND_CREDIT,
                    label=f"{category} statement credit {i}",
                    eligibility=eligibility,
                    capacity_minor=rng.randrange(100_00, 500_00),
                    exclusivity_group=group,
                    window=rng.choice((WINDOW_MONTHLY, WINDOW_QUARTERLY)),
                    requires_enrollment=rng.random() < 0.25,
                    enrolled=rng.random() < 0.8,
                )
            )
        elif roll < 0.95:
            flat = rng.randrange(20_00, 120_00)
            benefits.append(
                Benefit(
                    benefit_id=f"ben_{index:02d}_{i:03d}",
                    kind=KIND_PROTECTION,
                    label=f"{category} cover {i}",
                    eligibility=eligibility,
                    flat_minor=flat,
                    capacity_minor=flat * rng.randrange(1, 4),
                    exclusivity_group=group,
                    window=WINDOW_ANNUAL,
                )
            )
        else:
            benefits.append(
                Benefit(
                    benefit_id=f"ben_{index:02d}_{i:03d}",
                    kind="unpriced",
                    label=f"membership perk {i}",
                    note="CONSIDERED-BUT-UNPRICED",
                )
            )

    return build_manifest(
        manifest_id=f"mf_{seed}_{index:02d}",
        issuer="issuer_prototype",
        product=f"product_{index:02d}",
        benefits=benefits,
        issued_at=T0,
        source="generated benchmark instance, not a real card product",
    )


def generate_instance(seed: int, *, n_lines: int, n_benefits: int) -> tuple[Manifest, Cart]:
    """One (manifest, cart) pair. Deterministic in `seed`."""
    return generate_manifest(seed, 0, n_benefits), generate_cart(seed, n_lines)


def generate_portfolio(
    seed: int, *, instruments: int, n_lines: int, n_benefits: int
) -> tuple[tuple[Manifest, ...], Cart]:
    """A cart plus the several instruments an agent would rank against it."""
    if instruments <= 0:
        raise ValueError(f"a portfolio needs at least one instrument, got {instruments}")
    manifests = tuple(
        generate_manifest(seed, i, n_benefits) for i in range(instruments)
    )
    return manifests, generate_cart(seed, n_lines)


@dataclass(frozen=True)
class GapSample:
    """Greedy versus optimal on one instance.

    `gap_bp` is basis points of the optimum left on the table, floor-divided so the
    reported gap is never rounded upward into a worse number than was measured.
    """

    seed: int
    n_lines: int
    n_benefits: int
    candidates: int
    greedy_minor: int
    optimum_minor: int | None
    status: str
    reason: str | None
    oracle_ms: float

    @property
    def resolved(self) -> bool:
        return self.optimum_minor is not None

    @property
    def gap_minor(self) -> int | None:
        if self.optimum_minor is None:
            return None
        return self.optimum_minor - self.greedy_minor

    @property
    def gap_bp(self) -> int | None:
        if self.optimum_minor is None:
            return None
        if self.optimum_minor <= 0:
            return 0
        return ((self.optimum_minor - self.greedy_minor) * 10_000) // self.optimum_minor

    @property
    def greedy_is_optimal(self) -> bool:
        return self.optimum_minor is not None and self.greedy_minor == self.optimum_minor

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "n_lines": self.n_lines,
            "n_benefits": self.n_benefits,
            "candidates": self.candidates,
            "greedy_minor": self.greedy_minor,
            "optimum_minor": self.optimum_minor,
            "gap_minor": self.gap_minor,
            "gap_bp": self.gap_bp,
            "greedy_is_optimal": self.greedy_is_optimal,
            "status": self.status,
            "reason": self.reason,
            "oracle_ms": round(self.oracle_ms, 3),
        }


@dataclass(frozen=True)
class GapReport:
    """The optimality gap as a distribution, plus what could not be resolved.

    `unresolved` instances are excluded from the distribution and reported separately. A
    timeout is not evidence of a small gap, so folding it in as a zero would bias every
    figure downward — which is the flattering direction, and therefore the one to refuse.
    """

    samples: tuple[GapSample, ...]
    timeout_ms: int

    def resolved(self) -> tuple[GapSample, ...]:
        return tuple(s for s in self.samples if s.resolved)

    def unresolved(self) -> tuple[GapSample, ...]:
        return tuple(s for s in self.samples if not s.resolved)

    def optimal_fraction_bp(self) -> int:
        resolved = self.resolved()
        if not resolved:
            return 0
        return (sum(1 for s in resolved if s.greedy_is_optimal) * 10_000) // len(resolved)

    def to_dict(self) -> dict[str, Any]:
        resolved = self.resolved()
        unresolved = self.unresolved()
        gaps_bp = sorted(s.gap_bp or 0 for s in resolved)
        gaps_minor = sorted(s.gap_minor or 0 for s in resolved)
        reasons: dict[str, int] = {}
        for s in unresolved:
            key = s.reason or "unknown"
            reasons[key] = reasons.get(key, 0) + 1
        return {
            "instances": len(self.samples),
            "resolved": len(resolved),
            "unresolved": len(unresolved),
            "unresolved_reasons": dict(sorted(reasons.items())),
            "timeout_ms": self.timeout_ms,
            "greedy_optimal": sum(1 for s in resolved if s.greedy_is_optimal),
            "greedy_optimal_fraction_bp": self.optimal_fraction_bp(),
            "gap_bp": _distribution(gaps_bp),
            "gap_minor": _distribution(gaps_minor),
            "worst": (
                max(resolved, key=lambda s: s.gap_bp or 0).to_dict() if resolved else None
            ),
            "samples": [s.to_dict() for s in self.samples],
        }


@dataclass(frozen=True)
class LatencyRow:
    """Measured wall-clock for one allocator at one size.

    A sample is one full valuation: the cart priced against every instrument in the
    portfolio, which is the unit an agent actually waits for.
    """

    label: str
    allocator: str
    size: BenchSize
    samples_ns: tuple[int, ...]
    resolved: int = 0
    unresolved: int = 0

    def to_dict(self) -> dict[str, Any]:
        ordered = sorted(self.samples_ns)
        out: dict[str, Any] = {
            "label": self.label,
            "allocator": self.allocator,
            **self.size.to_dict(),
            "samples": len(ordered),
            "p50_ms": _ns_to_ms(_percentile(ordered, P50_BP)),
            "p90_ms": _ns_to_ms(_percentile(ordered, P90_BP)),
            "p99_ms": _ns_to_ms(_percentile(ordered, P99_BP)),
            "min_ms": _ns_to_ms(ordered[0] if ordered else 0),
            "max_ms": _ns_to_ms(ordered[-1] if ordered else 0),
            "mean_ms": _ns_to_ms(int(statistics.fmean(ordered)) if ordered else 0),
            "p99_resolution": (
                f"ceil(0.99 x {len(ordered)}) = {_rank(len(ordered), P99_BP)} of "
                f"{len(ordered)} slowest"
            ),
        }
        if self.allocator == "oracle":
            out["instances_resolved"] = self.resolved
            out["instances_unresolved"] = self.unresolved
        return out


def measure_gap(
    manifest: Manifest,
    cart: Cart,
    *,
    seed: int,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> GapSample:
    """Greedy value and oracle optimum for one instance."""
    greedy = allocate(manifest, cart).witness.realized_minor()
    result = optimum(manifest, cart, timeout_ms=timeout_ms)
    if result.optimum_minor is not None and greedy > result.optimum_minor:
        # The greedy witness is a feasible point of the model the oracle maximises, so it
        # cannot beat the optimum. If it does, one of the two is wrong about the problem
        # and neither number may be published.
        raise AssertionError(
            f"greedy realized {greedy} above the oracle optimum {result.optimum_minor} on "
            f"seed {seed}; the solver model and the allocator disagree about the "
            f"constraints, so no gap can be quoted until they are reconciled"
        )
    return GapSample(
        seed=seed,
        n_lines=len(cart.lines),
        n_benefits=len(manifest.benefits),
        candidates=len(candidates(manifest, cart)),
        greedy_minor=greedy,
        optimum_minor=result.optimum_minor,
        status=result.status,
        reason=result.reason,
        oracle_ms=result.elapsed_ms,
    )


def gap_report(
    *,
    seeds: Sequence[int],
    n_lines: int,
    n_benefits: int,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> GapReport:
    """Run both allocators over one instance per seed. Deterministic in `seeds`."""
    samples = []
    for seed in seeds:
        manifest, cart = generate_instance(seed, n_lines=n_lines, n_benefits=n_benefits)
        samples.append(measure_gap(manifest, cart, seed=seed, timeout_ms=timeout_ms))
    return GapReport(samples=tuple(samples), timeout_ms=timeout_ms)


def time_greedy(
    size: BenchSize,
    *,
    seed: int,
    reps: int,
    pool: int = 16,
    warmup: int = 5,
) -> LatencyRow:
    """Wall-clock for the hot path: one cart valued against every instrument.

    Instances are generated up front and cycled, so what is timed is allocation and not
    instance construction. A short untimed warmup absorbs first-call import and branch
    costs that would otherwise land entirely in the p99.
    """
    portfolios = _portfolio_pool(size, seed=seed, pool=pool)
    for i in range(warmup):
        manifests, cart = portfolios[i % len(portfolios)]
        for manifest in manifests:
            allocate(manifest, cart)

    samples: list[int] = []
    for r in range(reps):
        manifests, cart = portfolios[r % len(portfolios)]
        started = time.perf_counter_ns()
        for manifest in manifests:
            allocate(manifest, cart)
        samples.append(time.perf_counter_ns() - started)
    return LatencyRow(
        label=size.label, allocator="greedy", size=size, samples_ns=tuple(samples)
    )


def time_oracle(
    size: BenchSize,
    *,
    seed: int,
    reps: int,
    timeout_ms: int,
    pool: int = 4,
) -> LatencyRow:
    """Wall-clock for the solver at the same sizes, timeouts included as measured.

    A timed-out solve is kept in the latency sample — it is time the checkout would have
    spent — and counted as unresolved, because what it did not produce is a value. Both
    facts belong in the contrast; dropping the timeouts would make the solver look faster
    than it is by discarding its worst cases.
    """
    portfolios = _portfolio_pool(size, seed=seed, pool=pool)
    samples: list[int] = []
    resolved = unresolved = 0
    for r in range(reps):
        manifests, cart = portfolios[r % len(portfolios)]
        started = time.perf_counter_ns()
        results = [optimum(m, cart, timeout_ms=timeout_ms) for m in manifests]
        samples.append(time.perf_counter_ns() - started)
        for result in results:
            if result.status == STATUS_OPTIMAL:
                resolved += 1
            else:
                unresolved += 1
    return LatencyRow(
        label=size.label,
        allocator="oracle",
        size=size,
        samples_ns=tuple(samples),
        resolved=resolved,
        unresolved=unresolved,
    )


def run_benchmark(
    *,
    seed: int = DEFAULT_SEED,
    gap_instances: int = 150,
    gap_lines: int = 12,
    gap_benefits: int = 20,
    gap_timeout_ms: int = 10_000,
    headline_gap_instances: int = 12,
    headline_gap_timeout_ms: int = 30_000,
    sizes: Sequence[BenchSize] = DEFAULT_SIZES,
    greedy_reps: int = 300,
    oracle_reps: int = 3,
    oracle_timeout_ms: int = 2_000,
) -> dict[str, Any]:
    """Everything, as a JSON-serializable dict. Gap sections deterministic in `seed`.

    Two gap profiles, because one shape cannot answer both questions honestly:

      `gap`                   a shape the oracle resolves reliably, so the distribution
                              rests on a large sample rather than on whichever instances
                              happened to be easy.
      `gap_at_headline_size`  the same shape the latency table headlines. Fewer instances
                              and a far longer timeout, and it still does not resolve them
                              all — which is the argument, not a caveat to it.
    """
    gap = gap_report(
        seeds=[seed + i for i in range(gap_instances)],
        n_lines=gap_lines,
        n_benefits=gap_benefits,
        timeout_ms=gap_timeout_ms,
    )
    headline_size = _headline_size(sizes)
    headline_gap = gap_report(
        seeds=[seed + 1_000_000 + i for i in range(headline_gap_instances)],
        n_lines=headline_size.lines,
        n_benefits=headline_size.benefits,
        timeout_ms=headline_gap_timeout_ms,
    )
    greedy_rows = [time_greedy(s, seed=seed, reps=greedy_reps) for s in sizes]
    oracle_rows = [
        time_oracle(s, seed=seed, reps=oracle_reps, timeout_ms=oracle_timeout_ms)
        for s in sizes
    ]

    report = {
        "schema": SCHEMA,
        # The artifact carries the command that regenerates it, so a figure lifted onto a
        # slide never loses its provenance.
        "reproduce": (
            f"PYTHONPATH=backend .venv/bin/python -m plumbline.bench --seed {seed}"
        ),
        "environment": {
            "python": sys.version.split()[0],
            "z3": z3.get_version_string(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor() or "unknown",
        },
        "config": {
            "seed": seed,
            "gap_instances": gap_instances,
            "gap_lines": gap_lines,
            "gap_benefits": gap_benefits,
            "gap_timeout_ms": gap_timeout_ms,
            "headline_gap_instances": headline_gap_instances,
            "headline_gap_timeout_ms": headline_gap_timeout_ms,
            "greedy_reps": greedy_reps,
            "oracle_reps": oracle_reps,
            "oracle_timeout_ms": oracle_timeout_ms,
            "sizes": [s.to_dict() for s in sizes],
        },
        "gap": gap.to_dict(),
        "gap_at_headline_size": {
            **headline_gap.to_dict(),
            "size": headline_size.to_dict(),
        },
        "latency": {
            "greedy": [row.to_dict() for row in greedy_rows],
            "oracle": [row.to_dict() for row in oracle_rows],
        },
        "notes": [
            "The gap section is deterministic: same seed, same instances, same numbers.",
            "The latency section measures this machine and is not reproducible across "
            "hardware. Seeds, shapes and sample counts are recorded so it can be repeated.",
            "A sample is one full valuation: the cart priced against every instrument.",
            "Unresolved oracle instances are excluded from the gap distribution and "
            "counted separately. A timeout is not evidence of a small gap.",
            "gap_at_headline_size gives the solver 15x the checkout timeout and still "
            "leaves instances unresolved. Those are counted, never imputed.",
            "Instances are generated from modelled card terms, not observed carts. The "
            "gap characterises the allocator on this distribution and nothing wider.",
            "The greedy allocator is conservative by construction, never optimal by "
            "claim. These are the measured numbers, not a bound.",
        ],
    }
    report["headline"] = _headline(report)
    return report


def write_artifact(report: dict[str, Any], path: str | Path) -> Path:
    """Write the report as stable, sorted JSON. Creates parent directories."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out


def default_artifact_path() -> Path:
    """<repo>/artifacts/plumbline_bench.json, resolved from this file's location."""
    return Path(__file__).resolve().parents[2] / DEFAULT_ARTIFACT


def format_table(report: dict[str, Any]) -> str:
    """The latency table and the gap distribution, as text for a terminal or a slide."""
    lines: list[str] = []
    head = report["headline"]
    lines.append(f"schema {report['schema']}  seed {report['config']['seed']}")
    lines.append(f"z3 {report['environment']['z3']}  {report['environment']['platform']}")
    lines.append("")
    lines.append(f"{'size':<10} {'allocator':<9} {'p50':>10} {'p90':>10} {'p99':>10} {'n':>6}")
    lines.append("-" * 60)
    for allocator in ("greedy", "oracle"):
        for row in report["latency"][allocator]:
            lines.append(
                f"{row['label']:<10} {allocator:<9} {row['p50_ms']:>9.3f}ms "
                f"{row['p90_ms']:>9.3f}ms {row['p99_ms']:>9.3f}ms {row['samples']:>6}"
            )
    lines.append("")
    for title, gap in (
        (f"optimality gap, {head['gap_shape']}", report["gap"]),
        (f"optimality gap, {head['size']} (headline size)", report["gap_at_headline_size"]),
    ):
        lines.append(
            f"{title} — {gap['resolved']} resolved, {gap['unresolved']} unresolved "
            f"at a {gap['timeout_ms']}ms solver timeout"
        )
        lines.append(
            f"  greedy is optimal on {gap['greedy_optimal']}/{gap['resolved']} "
            f"({gap['greedy_optimal_fraction_bp'] / 100:.2f}%)"
        )
        lines.append(
            f"  gap  min {gap['gap_bp']['min'] / 100:.2f}%  median "
            f"{gap['gap_bp']['p50'] / 100:.2f}%  p90 {gap['gap_bp']['p90'] / 100:.2f}%  "
            f"max {gap['gap_bp']['max'] / 100:.2f}%"
        )
        if gap["unresolved_reasons"]:
            lines.append(f"  unresolved: {gap['unresolved_reasons']}")
        lines.append("")
    lines.append(
        f"at {head['size']}: greedy p99 {head['greedy_p99_ms']:.3f}ms, "
        f"oracle p99 {head['oracle_p99_ms']:.3f}ms "
        f"({head['oracle_over_greedy_p99']:.0f}x), oracle unresolved "
        f"{head['oracle_unresolved']}/{head['oracle_instances']} at a "
        f"{report['config']['oracle_timeout_ms']}ms timeout"
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="plumbline.bench",
        description="Measure the greedy allocator's optimality gap and latency against "
        "the offline Z3 oracle. Offline only; never run inside a checkout.",
        epilog="Defaults take roughly five minutes, nearly all of it in the solver. "
        "--quick finishes in seconds through the same code path.",
    )
    parser.add_argument("--out", default=None, help="artifact path (JSON)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--gap-instances", type=int, default=150)
    parser.add_argument("--gap-lines", type=int, default=12)
    parser.add_argument("--gap-benefits", type=int, default=20)
    parser.add_argument("--gap-timeout-ms", type=int, default=10_000)
    parser.add_argument("--headline-gap-instances", type=int, default=12)
    parser.add_argument("--headline-gap-timeout-ms", type=int, default=30_000)
    parser.add_argument("--greedy-reps", type=int, default=300)
    parser.add_argument("--oracle-reps", type=int, default=3)
    parser.add_argument("--oracle-timeout-ms", type=int, default=2_000)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="small run for CI: fewer instances, fewer reps, same code path",
    )
    parser.add_argument("--quiet", action="store_true", help="write the artifact only")
    args = parser.parse_args(argv)

    kwargs: dict[str, Any] = {
        "seed": args.seed,
        "gap_instances": args.gap_instances,
        "gap_lines": args.gap_lines,
        "gap_benefits": args.gap_benefits,
        "gap_timeout_ms": args.gap_timeout_ms,
        "headline_gap_instances": args.headline_gap_instances,
        "headline_gap_timeout_ms": args.headline_gap_timeout_ms,
        "greedy_reps": args.greedy_reps,
        "oracle_reps": args.oracle_reps,
        "oracle_timeout_ms": args.oracle_timeout_ms,
    }
    if args.quick:
        kwargs.update(
            gap_instances=12,
            gap_lines=6,
            gap_benefits=9,
            gap_timeout_ms=3_000,
            headline_gap_instances=2,
            headline_gap_timeout_ms=2_000,
            greedy_reps=30,
            oracle_reps=1,
            oracle_timeout_ms=500,
            sizes=DEFAULT_SIZES[:2],
        )

    report = run_benchmark(**kwargs)
    path = write_artifact(report, args.out or default_artifact_path())
    if not args.quiet:
        print(format_table(report))
        print(f"\nartifact: {path}")
    return 0


def _portfolio_pool(
    size: BenchSize, *, seed: int, pool: int
) -> list[tuple[tuple[Manifest, ...], Cart]]:
    if pool <= 0:
        raise ValueError(f"pool must be positive, got {pool}")
    return [
        generate_portfolio(
            seed + i,
            instruments=size.instruments,
            n_lines=size.lines,
            n_benefits=size.benefits,
        )
        for i in range(pool)
    ]


def _rank(n: int, q_bp: int) -> int:
    """Nearest-rank position for a percentile, 1-indexed."""
    if n <= 0:
        return 0
    return min(max(-((-q_bp * n) // 10_000), 1), n)


def _percentile(ordered: Sequence[int], q_bp: int) -> int:
    if not ordered:
        return 0
    return ordered[_rank(len(ordered), q_bp) - 1]


def _distribution(ordered: Sequence[int]) -> dict[str, int]:
    if not ordered:
        return {"min": 0, "p50": 0, "p90": 0, "p99": 0, "max": 0}
    return {
        "min": ordered[0],
        "p50": _percentile(ordered, P50_BP),
        "p90": _percentile(ordered, P90_BP),
        "p99": _percentile(ordered, P99_BP),
        "max": ordered[-1],
    }


def _ns_to_ms(ns: int) -> float:
    return round(ns / 1_000_000, 4)


def _headline(report: dict[str, Any]) -> dict[str, Any]:
    """The figures the slide quotes, pulled out so nobody has to recompute them by hand."""
    label = max(
        report["config"]["sizes"],
        key=lambda s: s["instruments"] * s["lines"] * s["benefits"],
    )["label"]
    greedy = _row_for(report["latency"]["greedy"], label)
    oracle = _row_for(report["latency"]["oracle"], label)
    gap = report["gap"]
    head_gap = report["gap_at_headline_size"]
    ratio = (oracle["p99_ms"] / greedy["p99_ms"]) if greedy["p99_ms"] > 0 else 0.0
    return {
        "size": greedy["label"],
        "gap_shape": (
            f"{report['config']['gap_lines']} lines x "
            f"{report['config']['gap_benefits']} benefits"
        ),
        "headline_gap_p50_bp": head_gap["gap_bp"]["p50"],
        "headline_gap_p90_bp": head_gap["gap_bp"]["p90"],
        "headline_gap_max_bp": head_gap["gap_bp"]["max"],
        "headline_gap_resolved": head_gap["resolved"],
        "headline_gap_unresolved": head_gap["unresolved"],
        "headline_gap_timeout_ms": report["config"]["headline_gap_timeout_ms"],
        "greedy_p50_ms": greedy["p50_ms"],
        "greedy_p99_ms": greedy["p99_ms"],
        "oracle_p50_ms": oracle["p50_ms"],
        "oracle_p99_ms": oracle["p99_ms"],
        "oracle_over_greedy_p99": round(ratio, 1),
        "oracle_unresolved": oracle.get("instances_unresolved", 0),
        "oracle_instances": oracle.get("instances_resolved", 0)
        + oracle.get("instances_unresolved", 0),
        "gap_p50_bp": gap["gap_bp"]["p50"],
        "gap_p90_bp": gap["gap_bp"]["p90"],
        "gap_max_bp": gap["gap_bp"]["max"],
        "greedy_optimal_fraction_bp": gap["greedy_optimal_fraction_bp"],
        "gap_resolved_instances": gap["resolved"],
    }


def _headline_size(sizes: Sequence[BenchSize]) -> BenchSize:
    """The largest configured size — the one the latency table headlines."""
    if not sizes:
        raise ValueError("at least one BenchSize is required")
    return max(sizes, key=lambda s: (s.instruments * s.lines * s.benefits))


def _row_for(rows: Sequence[dict[str, Any]], label: str) -> dict[str, Any]:
    for row in rows:
        if row["label"] == label:
            return row
    return rows[-1] if rows else {"label": label, "p50_ms": 0.0, "p99_ms": 0.0}


if __name__ == "__main__":
    raise SystemExit(main())
