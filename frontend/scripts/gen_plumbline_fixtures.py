"""Write the PLUMBLINE console fixtures by calling the same builder the API route calls.

Nothing in `src/mock/plumblineFixtures.json` is hand-written JSON, and — since this script
stopped owning any card terms of its own — nothing in it is hand-written Python either.
Every manifest, witness, verification result, failure code, Merkle root, inclusion proof,
consistency proof and latency figure comes from `plumbline.console.build_state`, which is the
function behind `GET /api/plumbline/state`.

That is the whole design. Two producers of one envelope is how a console ends up showing a
card in mock mode that the backend has never heard of; one producer and two transports
cannot drift, and `backend/tests/test_plumbline_console_state.py` diffs this file against a
live response to prove it.

Card terms live in `backend/plumbline/products.py` and nowhere else. If a rate on the console
looks wrong, that module is where it is wrong, and it carries a comment naming the published
term each number was read off.

Run:  PYTHONPATH=backend .venv/bin/python frontend/scripts/gen_plumbline_fixtures.py
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))

from plumbline import console  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parents[1] / "src" / "mock" / "plumblineFixtures.json"

# Re-exported for `gen_plumbline_beats.py`, which perturbs the same manifests over the same
# cart and asserts its baseline reproduces this file's recorded witness hashes.
CART = console.CART
T0 = console.CONSOLE_CLOCK
ISSUER_KEY = console.ISSUER_KEY
instrument_specs = console.instrument_specs
rank = console.rank


def main() -> None:
    payload = console.build_state(reps=console.FULL_REPS)
    OUT.write_text(json.dumps(payload, indent=1, sort_keys=False) + "\n")

    headline = next(
        v
        for v in payload["valuation"]["instruments"]
        if v["instrument_id"] == payload["valuation"]["headline_instrument"]
    )
    latency = payload["valuation"]["latency"]
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
    print(f"  cart         {console.money(console.CART.total())} over {len(console.CART.lines)} lines")
    for v in payload["valuation"]["instruments"]:
        r = v["reconciliation"]
        signed = "issuer-signed" if v["issuer_signed"] else "unsigned"
        print(
            f"  {v['product'][:34]:34s} {signed:13s} "
            f"naive {r['naive_display']:>11s}  witness {r['witness_display']:>11s}"
        )
    print(f"  headline     {headline['product']} overstated by {headline['reconciliation']['overstatement_display']}")
    print(f"  selected     {payload['receipt']['selected']}")
    print(f"  allocator    p50 {latency['demo']['p50_ms']} ms  p99 {latency['demo']['p99_ms']} ms")
    print(f"  at 8x20x40   p50 {latency['bench']['p50_ms']} ms  p99 {latency['bench']['p99_ms']} ms")
    print(f"  verifier     p50 {latency['bench_verify']['p50_ms']} ms")
    print(f"  vectors      {len(payload['omission']['vectors'])}")
    print(
        f"  attribution  {len(payload['attribution']['benefits'])} benefits over "
        f"{payload['attribution']['corpus_size']} receipts"
    )


if __name__ == "__main__":
    main()
