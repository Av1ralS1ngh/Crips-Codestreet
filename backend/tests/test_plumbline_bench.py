"""The benchmark harness: are the numbers it produces reproducible, and are they honest.

Two different standards apply to the two halves, and conflating them is how a benchmark
slide stops being evidence:

  * The gap section must be bit-for-bit reproducible from a seed. A judge re-running the
    command has to get the same figures, not similar ones.
  * The latency section cannot be, because it measures hardware. What must hold instead is
    that everything needed to repeat it — seeds, shapes, sample counts, the resolution of
    the percentile itself — is recorded next to the number.

Beyond reproducibility these tests pin the ways a benchmark can flatter itself: imputing a
zero gap for instances the oracle could not resolve, dropping the solver's timeouts out of
its own latency sample, or quoting a p99 finer than the sample count supports.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from plumbline.bench import (
    DEFAULT_SEED,
    DEFAULT_SIZES,
    SCHEMA,
    BenchSize,
    GapReport,
    GapSample,
    _distribution,
    _headline_size,
    _percentile,
    _rank,
    default_artifact_path,
    format_table,
    gap_report,
    generate_cart,
    generate_instance,
    generate_manifest,
    generate_portfolio,
    main,
    measure_gap,
    run_benchmark,
    time_greedy,
    time_oracle,
    write_artifact,
)
from plumbline.oracle import (
    CHECKOUT_PROCESS_ENV,
    REASON_TIMEOUT,
    STATUS_OPTIMAL,
    STATUS_UNKNOWN,
    OracleResult,
)

BACKEND = Path(__file__).resolve().parents[1]

TINY = {
    "gap_instances": 3,
    "gap_lines": 4,
    "gap_benefits": 5,
    "gap_timeout_ms": 5_000,
    "headline_gap_instances": 1,
    "headline_gap_timeout_ms": 2_000,
    "sizes": DEFAULT_SIZES[:1],
    "greedy_reps": 5,
    "oracle_reps": 1,
    "oracle_timeout_ms": 500,
}


def _sample(**kwargs) -> GapSample:
    base = {
        "seed": 1,
        "n_lines": 3,
        "n_benefits": 4,
        "candidates": 6,
        "greedy_minor": 100,
        "optimum_minor": 100,
        "status": STATUS_OPTIMAL,
        "reason": None,
        "oracle_ms": 1.0,
    }
    return GapSample(**{**base, **kwargs})


def _strip_timings(payload: dict) -> dict:
    """Drop the one field that measures the machine rather than the instance.

    Everything else in a gap report must be identical run to run; if this helper ever has
    to grow a second key, a non-deterministic field has been added to the artifact.
    """
    def clean(sample: dict | None) -> dict | None:
        if sample is None:
            return None
        return {k: v for k, v in sample.items() if k != "oracle_ms"}

    return {
        **payload,
        "samples": [clean(s) for s in payload["samples"]],
        "worst": clean(payload["worst"]),
    }


# --------------------------------------------------------------------------------------
# Instance generation
# --------------------------------------------------------------------------------------


def test_generate_instance_is_deterministic() -> None:
    first_manifest, first_cart = generate_instance(41, n_lines=6, n_benefits=8)
    second_manifest, second_cart = generate_instance(41, n_lines=6, n_benefits=8)
    assert first_manifest.body() == second_manifest.body()
    assert first_cart.hash() == second_cart.hash()


def test_different_seeds_give_different_instances() -> None:
    first, _ = generate_instance(41, n_lines=6, n_benefits=8)
    second, _ = generate_instance(42, n_lines=6, n_benefits=8)
    assert first.body() != second.body()


def test_generated_carts_have_unique_skus() -> None:
    """Witnesses key assignments by SKU, so a generator emitting duplicates would be
    measuring a problem neither the allocator nor the verifier can express."""
    for seed in range(20):
        cart = generate_cart(seed, 20)
        skus = [line.sku for line in cart.lines]
        assert len(skus) == len(set(skus)) == 20


def test_generated_amounts_are_integer_minor_units() -> None:
    cart = generate_cart(3, 12)
    assert all(isinstance(line.amount, int) and line.amount > 0 for line in cart.lines)


def test_generate_manifest_produces_the_requested_count_with_unique_ids() -> None:
    manifest = generate_manifest(9, 2, 30)
    assert len(manifest.benefits) == 30
    assert len({b.benefit_id for b in manifest.benefits}) == 30


def test_generate_portfolio_gives_distinct_instruments_over_one_cart() -> None:
    manifests, cart = generate_portfolio(5, instruments=4, n_lines=7, n_benefits=9)
    assert len(manifests) == 4
    assert len({m.manifest_id for m in manifests}) == 4
    assert len(cart.lines) == 7


@pytest.mark.parametrize(
    "call",
    [
        lambda: generate_cart(1, 0),
        lambda: generate_manifest(1, 0, 0),
        lambda: generate_portfolio(1, instruments=0, n_lines=2, n_benefits=2),
    ],
)
def test_degenerate_shapes_are_refused(call) -> None:
    with pytest.raises(ValueError):
        call()


# --------------------------------------------------------------------------------------
# Percentiles
# --------------------------------------------------------------------------------------


def test_percentile_is_nearest_rank_with_no_interpolation() -> None:
    """Every figure printed must be an observation that occurred, not a point between two.

    Nearest rank on 1..100: p50 is the 50th value, p90 the 90th, p99 the 99th.
    """
    values = list(range(1, 101))
    assert _percentile(values, 5_000) == 50
    assert _percentile(values, 9_000) == 90
    assert _percentile(values, 9_900) == 99


def test_percentile_ranks_are_clamped_into_the_sample() -> None:
    assert _rank(0, 9_900) == 0
    assert _rank(1, 9_900) == 1
    assert _rank(10, 9_900) == 10  # ceil(0.99 * 10) = 10, i.e. the max
    assert _rank(200, 9_900) == 198
    assert _percentile([], 5_000) == 0


def test_p99_of_a_small_sample_is_the_maximum() -> None:
    """Honest by construction: with 5 samples a p99 cannot be finer than the slowest."""
    assert _percentile([1, 2, 3, 4, 5], 9_900) == 5


def test_distribution_is_ordered_and_defined_on_the_empty_case() -> None:
    assert _distribution([]) == {"min": 0, "p50": 0, "p90": 0, "p99": 0, "max": 0}
    dist = _distribution(sorted([9, 1, 5, 3, 7]))
    assert dist["min"] <= dist["p50"] <= dist["p90"] <= dist["p99"] <= dist["max"]


# --------------------------------------------------------------------------------------
# Gap arithmetic
# --------------------------------------------------------------------------------------


def test_gap_is_floor_divided_so_it_is_never_rounded_up() -> None:
    """A gap rounded upward reports the allocator as worse than it was measured to be."""
    sample = _sample(greedy_minor=999, optimum_minor=1_000)
    assert sample.gap_minor == 1
    assert sample.gap_bp == 10  # 0.1%, floored


def test_a_zero_optimum_is_a_zero_gap_not_a_division_error() -> None:
    sample = _sample(greedy_minor=0, optimum_minor=0)
    assert sample.gap_bp == 0
    assert sample.greedy_is_optimal


def test_an_unresolved_sample_reports_no_gap_at_all() -> None:
    """Not a zero. A timeout is an absence of information, and imputing zero for it biases
    every figure in the flattering direction."""
    sample = _sample(optimum_minor=None, status=STATUS_UNKNOWN, reason=REASON_TIMEOUT)
    assert sample.gap_minor is None
    assert sample.gap_bp is None
    assert not sample.resolved
    assert not sample.greedy_is_optimal


def test_unresolved_instances_are_excluded_from_the_distribution() -> None:
    report = GapReport(
        samples=(
            _sample(seed=1, greedy_minor=100, optimum_minor=100),
            _sample(seed=2, greedy_minor=50, optimum_minor=100),
            _sample(
                seed=3,
                optimum_minor=None,
                status=STATUS_UNKNOWN,
                reason=REASON_TIMEOUT,
            ),
        ),
        timeout_ms=1_000,
    )
    payload = report.to_dict()
    assert payload["instances"] == 3
    assert payload["resolved"] == 2
    assert payload["unresolved"] == 1
    assert payload["unresolved_reasons"] == {REASON_TIMEOUT: 1}
    # The 50% gap and the 0% gap, and nothing imputed for the timeout.
    assert payload["gap_bp"]["max"] == 5_000
    assert payload["greedy_optimal"] == 1
    assert payload["greedy_optimal_fraction_bp"] == 5_000
    assert payload["worst"]["seed"] == 2


def test_a_report_with_nothing_resolved_claims_nothing() -> None:
    report = GapReport(
        samples=(_sample(optimum_minor=None, status=STATUS_UNKNOWN, reason="X"),),
        timeout_ms=10,
    )
    payload = report.to_dict()
    assert payload["greedy_optimal_fraction_bp"] == 0
    assert payload["gap_bp"] == {"min": 0, "p50": 0, "p90": 0, "p99": 0, "max": 0}
    assert payload["worst"] is None


def test_gap_report_is_reproducible_from_its_seeds() -> None:
    seeds = [DEFAULT_SEED + i for i in range(6)]
    first = gap_report(seeds=seeds, n_lines=5, n_benefits=7, timeout_ms=10_000)
    second = gap_report(seeds=seeds, n_lines=5, n_benefits=7, timeout_ms=10_000)
    assert _strip_timings(first.to_dict()) == _strip_timings(second.to_dict())


def test_gap_report_resolves_its_instances_and_never_goes_negative() -> None:
    report = gap_report(
        seeds=[DEFAULT_SEED + i for i in range(8)],
        n_lines=6,
        n_benefits=8,
        timeout_ms=10_000,
    )
    payload = report.to_dict()
    assert payload["resolved"] >= 1
    assert payload["gap_bp"]["min"] >= 0
    assert 0 <= payload["greedy_optimal_fraction_bp"] <= 10_000


def test_measure_gap_refuses_to_publish_if_greedy_beats_the_optimum(monkeypatch) -> None:
    """Fault injection on the invariant that makes the gap meaningful.

    Greedy is a feasible point of the model the oracle maximises, so an optimum below it
    means the two disagree about the constraints. Reporting the difference as a negative
    gap would publish a solver bug as an allocator result.
    """
    import plumbline.bench as bench_module

    def understating(manifest, cart, *, timeout_ms):
        return OracleResult(
            status=STATUS_OPTIMAL,
            optimum_minor=-1,
            witness=None,
            reason=None,
            detail="",
            elapsed_ms=0.0,
            candidates=0,
            variables=0,
            constraints=0,
            timeout_ms=timeout_ms,
        )

    monkeypatch.setattr(bench_module, "optimum", understating)
    manifest, cart = generate_instance(DEFAULT_SEED, n_lines=6, n_benefits=8)
    with pytest.raises(AssertionError) as excinfo:
        measure_gap(manifest, cart, seed=DEFAULT_SEED, timeout_ms=1_000)
    assert "no gap can be quoted" in str(excinfo.value)


# --------------------------------------------------------------------------------------
# Latency rows
# --------------------------------------------------------------------------------------


def test_time_greedy_reports_one_sample_per_rep() -> None:
    size = BenchSize("t", 2, 4, 6)
    row = time_greedy(size, seed=DEFAULT_SEED, reps=12, pool=3, warmup=1).to_dict()
    assert row["samples"] == 12
    assert row["allocator"] == "greedy"
    assert row["min_ms"] <= row["p50_ms"] <= row["p90_ms"] <= row["p99_ms"]
    assert row["p99_ms"] <= row["max_ms"]
    assert row["instruments"] == 2 and row["lines"] == 4 and row["benefits"] == 6


def test_every_latency_row_states_its_own_p99_resolution() -> None:
    """A p99 over few samples is the max. Saying so on the row stops it reading as more."""
    row = time_greedy(BenchSize("t", 1, 3, 4), seed=1, reps=7, pool=2, warmup=0).to_dict()
    assert row["p99_resolution"] == "ceil(0.99 x 7) = 7 of 7 slowest"


def test_time_oracle_counts_what_it_could_not_resolve() -> None:
    """Timeouts stay in the latency sample — that time is time a checkout would spend —
    and are counted as unresolved, because what they did not produce is a value."""
    size = BenchSize("t", 2, 20, 40)
    row = time_oracle(size, seed=DEFAULT_SEED, reps=1, timeout_ms=10, pool=1).to_dict()
    assert row["samples"] == 1
    assert row["instances_resolved"] + row["instances_unresolved"] == 2
    assert row["instances_unresolved"] >= 1
    assert row["max_ms"] > 0


def test_greedy_rows_omit_the_resolution_counts_the_oracle_needs() -> None:
    row = time_greedy(BenchSize("t", 1, 3, 4), seed=1, reps=3, pool=1, warmup=0).to_dict()
    assert "instances_unresolved" not in row


def test_a_zero_pool_is_refused() -> None:
    with pytest.raises(ValueError):
        time_greedy(BenchSize("t", 1, 2, 3), seed=1, reps=1, pool=0)


# --------------------------------------------------------------------------------------
# The artifact
# --------------------------------------------------------------------------------------


def test_headline_size_is_the_largest_configured_shape() -> None:
    assert _headline_size(DEFAULT_SIZES).label == "8x20x40"
    with pytest.raises(ValueError):
        _headline_size([])


def test_run_benchmark_produces_the_documented_schema() -> None:
    report = run_benchmark(seed=DEFAULT_SEED, **TINY)
    assert report["schema"] == SCHEMA
    for key in (
        "reproduce",
        "environment",
        "config",
        "gap",
        "gap_at_headline_size",
        "latency",
        "headline",
        "notes",
    ):
        assert key in report
    # A figure lifted onto a slide has to be able to name the command behind it.
    assert "plumbline.bench" in report["reproduce"]
    assert str(DEFAULT_SEED) in report["reproduce"]
    assert set(report["latency"]) == {"greedy", "oracle"}
    assert report["environment"]["z3"]
    assert report["config"]["seed"] == DEFAULT_SEED
    assert report["gap"]["instances"] == TINY["gap_instances"]
    assert report["gap_at_headline_size"]["size"]["label"] == DEFAULT_SIZES[0].label


def test_the_headline_block_carries_every_number_the_slide_quotes() -> None:
    report = run_benchmark(seed=DEFAULT_SEED, **TINY)
    head = report["headline"]
    for key in (
        "size",
        "greedy_p50_ms",
        "greedy_p99_ms",
        "oracle_p50_ms",
        "oracle_p99_ms",
        "oracle_over_greedy_p99",
        "gap_p50_bp",
        "gap_p90_bp",
        "gap_max_bp",
        "greedy_optimal_fraction_bp",
        "gap_resolved_instances",
        "headline_gap_resolved",
        "headline_gap_unresolved",
        "headline_gap_timeout_ms",
    ):
        assert key in head, key
    assert head["greedy_p99_ms"] > 0
    assert head["oracle_p99_ms"] >= head["greedy_p99_ms"]


def test_the_report_states_its_own_limitations() -> None:
    """The notes are load-bearing: they are what stops a figure being over-read."""
    notes = " ".join(run_benchmark(seed=DEFAULT_SEED, **TINY)["notes"]).lower()
    assert "deterministic" in notes
    assert "not reproducible across" in notes
    assert "timeout is not evidence of a small gap" in notes
    assert "never optimal by" in notes


def test_write_artifact_round_trips_as_sorted_json(tmp_path: Path) -> None:
    report = run_benchmark(seed=DEFAULT_SEED, **TINY)
    path = write_artifact(report, tmp_path / "nested" / "bench.json")
    assert path.exists()
    raw = path.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert json.loads(raw) == report
    assert list(json.loads(raw)) == sorted(json.loads(raw))


def test_default_artifact_path_sits_in_the_repo_artifacts_directory() -> None:
    path = default_artifact_path()
    assert path.parent.name == "artifacts"
    assert path.name == "plumbline_bench.json"
    assert (path.parent.parent / "backend").is_dir()


def test_format_table_prints_both_gap_profiles_and_the_contrast() -> None:
    text = format_table(run_benchmark(seed=DEFAULT_SEED, **TINY))
    assert "greedy" in text and "oracle" in text
    assert "optimality gap" in text
    assert "headline size" in text
    assert "unresolved" in text


def test_main_quick_writes_a_readable_artifact(tmp_path: Path, capsys) -> None:
    out = tmp_path / "bench.json"
    assert main(["--quick", "--out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == SCHEMA
    assert payload["gap"]["instances"] == 12
    assert "optimality gap" in capsys.readouterr().out


def test_main_quiet_writes_the_artifact_without_printing(tmp_path: Path, capsys) -> None:
    out = tmp_path / "bench.json"
    assert main(["--quick", "--quiet", "--out", str(out)]) == 0
    assert out.exists()
    assert capsys.readouterr().out == ""


def test_the_harness_is_offline_only_too() -> None:
    """bench imports the oracle, so it inherits the posture. Checked in a real process."""
    env = dict(os.environ, PYTHONPATH=str(BACKEND), **{CHECKOUT_PROCESS_ENV: "1"})
    proc = subprocess.run(
        [sys.executable, "-c", "import plumbline.bench"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode != 0
    assert "OfflineOnlyError" in proc.stderr
