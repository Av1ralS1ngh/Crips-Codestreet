"""Print every headline number in this repository, and where it came from.

The pitch's thesis is that agents should show their work. A number on a slide that nobody
can reproduce from a clean clone refutes that thesis by example, so this script is the
reproduction path: one command, one screen, every figure traced to the artifact or the code
that produced it.

Three kinds of figure, and they are not interchangeable:

  * DETERMINISTIC — the scenario fingerprint and the per-scenario digests. Computed here,
    twice, from a fixed clock. Two runs that differ are not reproducible and this script
    exits non-zero. This is the figure a judge should be handed the controls for.
  * MEASURED, PORTABLE — the optimality gap. Depends on seeds and generators, not on the
    machine, so it reproduces anywhere and is read out of the committed artifact.
  * MEASURED, MACHINE-BOUND — the latency percentiles. These do NOT reproduce on other
    hardware and are labelled as such every time they are printed, because an unlabelled
    latency figure is the easiest number in the deck to be wrong about.

Nothing here regenerates `artifacts/plumbline_bench.json`. That artifact holds the full run
(roughly five minutes, nearly all of it in Z3) and is the provenance for the numbers spoken
aloud; a `--quick` run writes different, smaller numbers under the same schema, so
overwriting it would silently replace the evidence with a rehearsal of the evidence.
`scripts/demo.sh` proves the harness executes by writing a quick run to a temp path instead.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCH_ARTIFACT = REPO_ROOT / "artifacts" / "plumbline_bench.json"

# Schema the reader below understands. A bump means this script needs updating rather than
# silently reporting a field that has moved.
BENCH_SCHEMA = "plumbline/bench/1"


class HeadlineError(RuntimeError):
    """A headline number could not be reproduced or could not be read."""


def _rule(title: str, width: int = 88) -> str:
    return f"\n{title}\n{'-' * width}"


# --------------------------------------------------------------------------------------
# Deterministic: the scenario fingerprint
# --------------------------------------------------------------------------------------


def scenario_report() -> tuple[str, dict[str, str], list[str]]:
    """(fingerprint, per-scenario digest, lines to print).

    The fingerprint is computed twice from the same fixed clock. Determinism is the claim,
    so it is checked rather than asserted.
    """
    from plumbline import scenarios as S
    from plumbline.manifest import canonical_json

    first = S.run_all()
    second = S.run_all()

    per_scenario: dict[str, str] = {}
    drift: list[str] = []
    for name in sorted(first):
        a = canonical_json(first[name])
        b = canonical_json(second[name])
        if a != b:
            drift.append(name)
        per_scenario[name] = hashlib.sha256(a).hexdigest()

    fingerprint = hashlib.sha256(canonical_json(first)).hexdigest()
    if hashlib.sha256(canonical_json(second)).hexdigest() != fingerprint:
        drift.append("(aggregate)")
    if drift:
        raise HeadlineError(
            f"these scenarios did not replay byte-for-byte at a fixed clock: "
            f"{', '.join(drift)}. Something in the decision path is reading a wall clock, "
            f"iterating a set, or hashing an address."
        )

    lines = [
        _rule("DETERMINISM — the scenario fingerprint (computed twice, fixed clock)"),
        f"  clock                 {S.DEMO_CLOCK}  (explicit, never time.time())",
        f"  scenarios             {len(first)}",
        "",
    ]
    for name, digest in per_scenario.items():
        lines.append(f"  {name:<22}{digest}")
    lines += [
        "",
        f"  FINGERPRINT           {fingerprint}",
        "",
        "  Two runs produced identical bytes. Perturb a manifest and this number moves;",
        "  that is the control to hand a judge.",
    ]
    return fingerprint, per_scenario, lines


# --------------------------------------------------------------------------------------
# Measured: the bench artifact
# --------------------------------------------------------------------------------------


def load_bench() -> dict[str, Any]:
    if not BENCH_ARTIFACT.is_file():
        raise HeadlineError(
            f"the bench artifact is missing: {BENCH_ARTIFACT}\n"
            f"  It is committed on purpose — it is the provenance for the latency and gap\n"
            f"  figures. Regenerate the FULL run (about five minutes) with:\n"
            f"    PYTHONPATH=backend .venv/bin/python -m plumbline.bench\n"
            f"  Do not substitute a --quick run: it writes smaller numbers under the same\n"
            f"  schema and they are not the numbers anyone quoted."
        )
    report = json.loads(BENCH_ARTIFACT.read_text(encoding="utf-8"))
    schema = str(report.get("schema", ""))
    if schema != BENCH_SCHEMA:
        raise HeadlineError(
            f"bench artifact schema is {schema!r}, this reader understands "
            f"{BENCH_SCHEMA!r}; update scripts/headline.py rather than reading fields that "
            f"may have moved"
        )
    for key in ("headline", "config", "environment", "latency", "gap", "gap_at_headline_size"):
        if key not in report:
            raise HeadlineError(f"bench artifact is missing {key!r}; it is truncated")
    return report


def bench_lines(report: dict[str, Any]) -> list[str]:
    head = report["headline"]
    cfg = report["config"]
    env = report["environment"]
    gap = report["gap"]
    headline_gap = report["gap_at_headline_size"]

    lines = [
        _rule("MEASURED — allocator latency (MACHINE-BOUND: does not reproduce elsewhere)"),
        f"  measured on           {env.get('platform', 'unknown')}",
        f"  z3                    {env.get('z3', 'unknown')}",
        f"  seed                  {cfg['seed']}   reps {cfg['greedy_reps']} per size",
        "",
        f"  {'size':<12}{'p50':>11}{'p99':>11}{'p99/p50':>10}",
    ]
    for row in report["latency"]["greedy"]:
        spread = row["p99_ms"] / row["p50_ms"] if row["p50_ms"] else float("nan")
        lines.append(
            f"  {row['label']:<12}{row['p50_ms']:>9.3f}ms{row['p99_ms']:>9.3f}ms"
            f"{spread:>9.2f}x"
        )
    lines += [
        "",
        f"  The claim that carries the argument is the SPREAD at {head['size']}:",
        f"  p99 is {head['greedy_p99_ms'] / head['greedy_p50_ms']:.2f}x p50, against the "
        f"solver's 6x at constant problem size.",
        f"  Say 'sub-millisecond for a single instrument, ~{head['greedy_p50_ms']:.0f}ms at "
        f"{head['size']}'. Never say microseconds for the headline size.",
        "",
        _rule("MEASURED — optimality gap (PORTABLE: seeds and generators, not hardware)"),
        "  Two different worst-case gaps exist in this repo and they are NOT",
        "  interchangeable. Whichever is spoken aloud carries its conditions.",
        "",
        f"  gap A   {gap['gap_bp']['max'] / 100:.2f}% worst over {gap['resolved']} "
        f"Z3-resolved instances at {head['gap_shape']}",
        f"          median {gap['gap_bp']['p50'] / 100:.2f}%, p90 "
        f"{gap['gap_bp']['p90'] / 100:.2f}%, solver timeout {gap['timeout_ms']}ms",
        f"          greedy exactly optimal on {gap['greedy_optimal']}/{gap['resolved']} "
        f"({gap['greedy_optimal_fraction_bp'] / 100:.2f}%)",
        f"          {gap['unresolved']} instance(s) unresolved and excluded, never imputed",
        "",
        "  gap B   45.9% worst over 303 smaller instances against the max-flow oracle",
        "          in backend/tests/test_plumbline_core.py. Different generator, different",
        "          sizes, different solver. Printed by the test run above.",
        "",
        f"  at the headline {head['size']}: the oracle resolves only "
        f"{headline_gap['resolved']}/"
        f"{headline_gap['resolved'] + headline_gap['unresolved']} instances even at a "
        f"{headline_gap['timeout_ms']}ms timeout.",
        f"  The honest sentence is 'the gap we could measure there is at most "
        f"{headline_gap['gap_bp']['max'] / 100:.2f}%' — not a hit rate.",
        "",
        f"  artifact              {BENCH_ARTIFACT.relative_to(REPO_ROOT)}",
        "  full-run command      PYTHONPATH=backend .venv/bin/python -m plumbline.bench",
    ]
    return lines


# --------------------------------------------------------------------------------------
# The things this repo does not claim
# --------------------------------------------------------------------------------------

LIMITS = (
    "Signatures are HMAC-SHA256 under prototype keys. There is no asymmetric crypto here.",
    "Manifests model publicly published card terms. No live Offers feed is claimed.",
    "Remaining balances are SYNTHETIC member state, labelled on every manifest.",
    "The greedy allocator is conservative by construction and NOT optimal. Quote the gap.",
    "Cumulative-budget and velocity caveats are stateful and not offline-verifiable.",
    "All rails, merchants and authorization responses are mocked.",
    "The trust model is demonstrated, not deployed: nobody countersigns these manifests.",
)


def limit_lines() -> list[str]:
    return [
        _rule("STATED LIMITS — say these out loud; hiding one refutes the thesis"),
        *(f"  - {line}" for line in LIMITS),
    ]


# --------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/headline.py",
        description="Print every headline number and prove the deterministic ones replay.",
    )
    parser.add_argument(
        "--fingerprint-only",
        action="store_true",
        help="print just the scenario fingerprint, one line, for scripting",
    )
    args = parser.parse_args(argv)

    try:
        fingerprint, _, scenario_out = scenario_report()
    except HeadlineError as exc:
        print(f"FAIL determinism: {exc}", file=sys.stderr)
        return 1

    if args.fingerprint_only:
        print(fingerprint)
        return 0

    out = list(scenario_out)
    try:
        out += bench_lines(load_bench())
    except HeadlineError as exc:
        print("\n".join(out))
        print(f"\nFAIL bench artifact: {exc}", file=sys.stderr)
        return 1

    out += limit_lines()
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
