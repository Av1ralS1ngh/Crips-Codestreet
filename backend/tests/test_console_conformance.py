"""The console ships a second witness verifier. This pins it to the first.

`frontend/src/lib/witness.ts` re-implements `plumbline/witness.py :: verify_witness` in the
browser, and porting it is the point rather than a convenience: the correctness argument is
that an asserted value is sound because a concrete allocation realising it can be exhibited,
and that checking such an allocation is linear-time arithmetic needing no solver and no
trust in the issuer's tooling. A console that displayed the server's `ok` boolean would be
asserting that claim rather than demonstrating it.

Two implementations of one rule drift. The failure mode is asymmetric and nasty: a console
verifier *missing* a check does not error, it prints VERIFIED under a number the evaluator
refuses — on the projector, in front of the people being asked to trust the mechanism. It is
strictly worse than shipping no console verifier at all.

This file is a source-level guard, not a behavioural one. It cannot run TypeScript, so it
checks the one thing that is checkable from here and that catches the realistic regression:
a failure code added to the kernel and never ported. What the console actually *does* with
each code is pinned on the other side, by the forgery cases in `frontend/scripts/smoke.tsx`
(`npm run smoke`), which hand-build a witness for every check and assert the browser refuses
it with the same code the kernel uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plumbline import witness as kernel_witness

REPO_ROOT = Path(__file__).resolve().parents[2]
CONSOLE_VOCABULARY = REPO_ROOT / "frontend" / "src" / "lib" / "plumbline.ts"
CONSOLE_VERIFIER = REPO_ROOT / "frontend" / "src" / "lib" / "witness.ts"


def _read(path: Path) -> str:
    if not path.is_file():
        pytest.fail(
            f"the console verifier is part of the correctness argument and is missing: "
            f"expected {path}. If the frontend has moved, update the paths at the top of "
            f"{Path(__file__).name}; do not delete this test."
        )
    return path.read_text(encoding="utf-8")


def _kernel_codes() -> dict[str, str]:
    """Constant name -> wire string, read off the kernel module itself."""
    return {
        name: value
        for name, value in vars(kernel_witness).items()
        if name.startswith("ERR_") and isinstance(value, str)
    }


def test_the_kernel_failure_code_table_is_complete() -> None:
    """FAILURE_CODES is what the rest of the codebase iterates. It must list every code."""
    declared = set(kernel_witness.FAILURE_CODES)
    defined = set(_kernel_codes().values())
    assert declared == defined, (
        f"witness.FAILURE_CODES and the ERR_* constants disagree; "
        f"only in FAILURE_CODES: {sorted(declared - defined)}, "
        f"only as constants: {sorted(defined - declared)}"
    )
    assert len(kernel_witness.FAILURE_CODES) == len(set(kernel_witness.FAILURE_CODES))


@pytest.mark.parametrize("code", sorted(kernel_witness.FAILURE_CODES))
def test_every_kernel_failure_code_exists_in_the_console_vocabulary(code: str) -> None:
    source = _read(CONSOLE_VOCABULARY)
    assert f'"{code}"' in source, (
        f"{code} is a kernel verifier failure code with no counterpart in "
        f"{CONSOLE_VOCABULARY.relative_to(REPO_ROOT)}. Add the export there, emit it from "
        f"verifyWitness in witness.ts, and pin it with a forgery case in scripts/smoke.tsx. "
        f"A console that cannot name this failure will display VERIFIED for a witness the "
        f"evaluator refuses."
    )


@pytest.mark.parametrize("name", sorted(_kernel_codes()))
def test_every_kernel_failure_code_is_reachable_in_the_console_verifier(name: str) -> None:
    """Declaring a code is not porting a check. The verifier itself must reference it."""
    source = _read(CONSOLE_VERIFIER)
    assert name in source, (
        f"{name} is exported to the console but never referenced by verifyWitness in "
        f"{CONSOLE_VERIFIER.relative_to(REPO_ROOT)}, so the browser cannot produce it. "
        f"Port the check and add a forgery case to scripts/smoke.tsx."
    )


def test_the_console_vocabulary_declares_no_code_the_kernel_does_not_have() -> None:
    """Drift runs both ways. A console-only code is a verdict with no kernel meaning."""
    source = _read(CONSOLE_VOCABULARY)
    kernel = set(kernel_witness.FAILURE_CODES)
    declared = {
        line.split('"')[1]
        for line in source.splitlines()
        if line.startswith("export const ERR_") and '"WITNESS_' in line
    }
    assert declared <= kernel, (
        f"the console declares verifier codes the kernel never emits: "
        f"{sorted(declared - kernel)}. The kernel is the authority for this vocabulary."
    )


def test_the_console_verifier_does_not_reimplement_the_allocator() -> None:
    """Producing an allocation is a decision, and the decision belongs to the engine.

    The console only ever checks a witness it was handed. A client-side allocator would be a
    second hot path with no signature over its output and no oracle measuring its gap.
    """
    source = _read(CONSOLE_VERIFIER)
    assert "function allocate" not in source
    assert "export function allocate" not in source
