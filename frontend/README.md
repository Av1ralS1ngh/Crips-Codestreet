# PLUMBLINE — value disclosure console

Vite + React 19 + TypeScript + Tailwind v4. Dark, single-look, built to be read from the
back of a room.

```bash
npm install
npm run dev        # mock transport — runs with no backend at all
npm run dev:live   # live transport — expects uvicorn on :8000
npm run build      # tsc -b && vite build
npm run smoke      # headless jsdom run of all six demo beats
```

## The single flag

`src/lib/transport.ts` picks the transport:

```
VITE_CAVEAT_TRANSPORT=live   -> src/lib/liveClient.ts   (fetch + WebSocket, vite-proxied)
anything else (default)      -> src/mock/mockClient.ts  (recorded kernel output)
```

The header badge shows `MOCK` or `LIVE` at all times and toggles the transport at runtime,
so a backend that dies on stage costs one click rather than a restart. The console never
presents mock data as live.

`vite.config.ts` proxies `/api` and `/ws` to `http://127.0.0.1:8000` (override with
`CAVEAT_BACKEND`), so nothing needs CORS or an absolute URL.

The valuation endpoint is `GET /api/plumbline/state`, returning the `PlumblineState` envelope.
It is loaded and errored separately from `/api/state`, so a backend that does not serve it
yet degrades to a named banner on the five valuation screens instead of blanking the kernel
screens too. One request, not five, because the beats are five views of one corpus and a
judge switching screens must never see two screens disagree because two fetches interleaved.

**The contract lives in `src/lib/plumbline.ts`, and `scripts/gen_plumbline_fixtures.py` is its
reference producer** — that script builds a conforming payload by calling `plumbline.allocate`,
`plumbline.witness`, `plumbline.manifest` and `caveat.ledger` directly, so the backend route can
be assembled from the same calls.

## Where the data comes from

Two recorded fixture sets, both produced by driving the real backend. Neither is
hand-written JSON, which is why the proofs actually verify in the browser and why the wire
types cannot drift from `to_dict()`.

```bash
../.venv/bin/python scripts/gen_fixtures.py > src/mock/fixtures.json
PYTHONPATH=../backend ../.venv/bin/python scripts/gen_plumbline_fixtures.py
```

`src/mock/fixtures.json` — decisions, entailment results, counterexamples, mandates, ledger
entries and inclusion proofs from the real `CaveatEngine`.

`src/mock/plumblineFixtures.json` — manifests, allocations, witnesses, verification results,
failure codes, signed tree heads, consistency proofs and latency percentiles from the real
`plumbline` and `caveat` packages. The generator asserts, before writing, that every
overstatement reconciliation closes to the rupee and that the ledger's tree is exactly RFC
6962's MTH.

Modelled, and labelled as such wherever displayed: the card terms (public terms, no live
offers feed), the per-benefit annual costs that key the attribution 2×2, the 180-receipt
corpus, and the nine-operator underwriting book. Signatures are HMAC under prototype keys.

## Screens

| Beat | Screen | What it has to land |
|---|---|---|
| 01 | Overstatement | per-line sum vs witness-backed value, reconciled to the rupee, with the allocation table as the derivation |
| 02 | Receipt | full candidate set, ranked, with the signing boundary drawn across the middle of the screen |
| 03 | Omission | a dropped card, two tree heads, and the consistency proof that catches the edit |
| 04 | Refusal | the evaluator declining to sign, with its reason code, anchored in the log |
| 05 | Attribution | benefit-level selection influence, keyed to a cut/protect decision |
| 06 | Kernel | mandate chain, delegation entailment, kill switch, exposure — supporting evidence |

Keys `1`–`6` switch beats. `S`/`M`/`L` in the header scales the whole UI for the room. The
decision feed rides with the kernel screen only: nothing on the five valuation screens
passes through the PDP, and a rail that always says "no traffic" teaches a judge that the
rail means nothing.

## What the console verifies for itself

Three checks are recomputed in the browser rather than read off a server boolean, and each
displays its own verdict beside the backend's:

- **`src/lib/witness.ts`** — a port of `plumbline/witness.py :: verify_witness`. Re-reads the
  manifest, re-derives what each assignment should yield, re-checks every balance. Linear
  time, no solver. Porting it is the argument, not a convenience: the claim is that any
  counterparty can check an asserted value with arithmetic.
- **`src/lib/transparency.ts`** — RFC 6962 consistency proofs. The smoke test replays 29
  generated `(m, n)` vectors, negatives included, through this implementation.
- **`src/lib/merkle.ts`** — RFC 6962 inclusion proofs, as before.

Deliberately *not* ported: the allocator. Producing an allocation is a decision and belongs
to the engine; the console only ever checks one it was handed.

## Conventions this console holds to

- Money is an integer count of paise everywhere; `format.money` is a port of the kernel's
  `fmt_money`, and the smoke test asserts the two agree on every `*_display` string either
  fixture set carries.
- Reason codes, benefit kinds, verifier failure codes and quadrant names are module-level
  constants in `src/lib/types.ts` and `src/lib/plumbline.ts`, mirroring the closed enums in
  the kernel. Never inline one at a call site.
- Every latency figure renders through `Provenance` and carries the problem size and the
  method it was measured under. The MaxSMT figures are the adversarial panel's benchmark and
  are marked `not measured here` on screen.
- The signing boundary is a visual primitive (`Seal`, `SignedRegion`, `BoundaryRule`), not a
  caption. Issuer-signed facts and cardholder-owned ranking are never rendered the same way.
- Stated limitations — prototype keys, modelled card terms, greedy-not-optimal allocation,
  the discharge-TTL bound, non-offline-verifiable stateful caveats, selection influence not
  retention — are rendered in the UI rather than kept off screen.
