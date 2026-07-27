# PLUMBLINE

**The layer where value is declared, proved, and contestable.**

Submission for American Express CodeStreet 2026, **Problem Statement #5 — Governance Layer for
Financial Agents**, with demonstrated reach into **#2** (Card Benefit Activation) and **#6**
(Benefit-Underutilization Analytics).

---

## What this is

A mandate today says *"you may spend up to X at Y."* It cannot say *"you may act on my behalf
only if you show your work about which instrument you chose and why."*

Instrument selection is the one step in an agentic transaction that no audit log covers, no
protocol specifies, and no emergency control reaches. This repository governs it with the same
primitives as everything else: an unremovable caveat, a deterministic decision function, a
transparency log, and a kill switch.

Two things to say before anything else, because they are true and someone will otherwise say
them for us:

- **AI agents do not choose cards today.** Every shipped protocol pins a human-chosen
  instrument. This is a mechanism for a thing that has not happened yet.
- **We built an acceptance predicate — a signed field naming where a card would be refused —
  and then removed it.** An issuer-signed instruction to route away from your own network is
  not a feature. `backend/plumbline/authoring.py` now *rejects* any manifest draft containing one,
  in any spelling, and `backend/tests/test_products.py::test_no_acceptance_predicate_anywhere`
  keeps it out. The removal is the artifact.

---

## The correctness argument, in one paragraph

Valuing a cart is a **capacitated assignment problem**: cart lines to benefit buckets, subject to
remaining balances, annual caps, and exclusivity groups. Naive per-line summation double-counts
and **overstates**, and overstating is the one error that must never happen — an agent acting on
an inflated number produces a purchase the card cannot back.

The obvious approach is to assert a value and ask a solver to prove no better allocation is
needed. It fails twice. It does not hold a checkout budget, and the natural soundness query
(`no allocation realizes less than X`) is trivially satisfied by the empty allocation.

So the argument runs constructively:

> **Assert a value only if you can exhibit a concrete, valid allocation realizing at least that
> much.** The witness *is* the derivation. Because the exhibited allocation is achievable, the
> asserted value cannot exceed the true optimum.

Verification is then **linear-time arithmetic and needs no solver** — re-add the numbers, check
the capacities. A solver is still used, but **offline**, to measure how far below optimal the
greedy witness sits. That gap is a number we quote, not an optimality claim we make.

The full statement, with the failure modes it defends against, is the module docstring of
[`backend/plumbline/witness.py`](backend/plumbline/witness.py).

---

## The single command

From a clean clone, with **no `ANTHROPIC_API_KEY` and no network**:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # once
./scripts/demo.sh                                                    # or: make demo
```

That runs, stopping at the first failure and printing the exact fix for anything missing:

| # | step | what it proves |
|---|---|---|
| 0 | preflight | every prerequisite by name — venv, packages, Node 20+, the bench artifact; and that `plumbline.witness` still imports **no solver**, which is the verifier's whole claim |
| 1 | `backend/tests` + `agent/tests` | the kernel, the valuation path, the adapters and the agent harness |
| 2 | console build + smoke | the browser's second witness verifier still agrees with the kernel's |
| 3 | bench harness | `--quick` **to a temp path** — the committed artifact is never overwritten |
| 4 | headline numbers | the scenario fingerprint, computed twice and compared, plus every measured figure with its conditions attached |
| 5 | agent replays | all three agent paths on recorded traces, no API key, no network |
| 6 | serve | `uvicorn` and `vite` together, both waited for, both killed on Ctrl-C |

Useful variants:

```bash
./scripts/demo.sh --check-only      # steps 0-5, no servers. What CI runs.  (make check)
./scripts/demo.sh --serve-only      # skip the checks and bring both up
./scripts/demo.sh --skip-console    # backend only, when Node is unavailable
./scripts/demo.sh --api-port 8001 --web-port 5174
make help                           # every other target
```

Two decisions in that script are deliberate and worth knowing before you read its output:

- **It never regenerates `artifacts/plumbline_bench.json`.** That artifact holds the full ~5-minute
  run and is the provenance for every latency and gap figure quoted anywhere. A `--quick` run
  writes *smaller* numbers under the *same* schema, so overwriting it would silently replace the
  evidence with a rehearsal of the evidence. The quick run goes to a temp path purely to prove
  the harness executes. `make bench-full` regenerates it, and asks first.
- **The console transport is decided by asking the API, not by assuming.** After `uvicorn` is up
  the script fetches `/api/plumbline/state`; if that answers with an envelope the console starts in
  **live** mode, otherwise it falls back to **mock** and says exactly why. Five of the six screens
  read that one envelope, so discovering it is not serving is not something to do on stage.

Written for bash 3.2 — what stock macOS still ships as `/bin/bash`.

---

## Reproducing every headline number

Everything below is a command, and `./scripts/demo.sh` runs all of them. Nothing in this README
is a number you have to take on trust.

### Setup

```bash
# Python 3.13, from the repo root.
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python --version          # 3.13.7
npm ci --prefix frontend
```

### 1. The suites

```bash
PYTHONPATH=backend:. .venv/bin/python -m pytest backend/tests agent/tests -q   # make test
npm run smoke --prefix frontend                                                # make smoke
```

Last measured 2026-07-27, and still climbing — quote what the command just printed, not this
table:

| | count | what it covers |
|---|---|---|
| `backend/tests` + `agent/tests` | **1647 passing** | kernel, plumbline, adapters, agent + injection harness |
| `frontend` smoke | **232 checks** | the console mounted in jsdom and driven like a presenter |

`npm run smoke` type-checks, builds the real modules with `vite build --ssr`, and runs them
under node — it exercises the shipped console, not a re-implementation.

> **Note on the "185 tests" figure in `CLAUDE.md`.** It is stale by an order of magnitude. Quote
> the number you just measured, never the one in the contract.

### 2. The overstatement (demo beat 1)

```bash
PYTHONPATH=backend .venv/bin/python -c \
  "from plumbline import scenarios; print(scenarios.run('overstatement')['headline'])"
```

```
Per-line summation claims $2,092.01. The exhibited allocation realizes $773.77
 — lower, and achievable.
```

Per-line summation overstates by **$1,318.24** on a **$2,681** cart — the naive figure is
**2.70x** the defensible one. The full derivation, the reconciliation by cause, and the browser's
independent re-verification are on beat 01 of the console.

### 3. The optimality gap and the latency table

```bash
PYTHONPATH=backend .venv/bin/python -m plumbline.bench --quick   # seconds, same code path
PYTHONPATH=backend .venv/bin/python -m plumbline.bench --seed 20260825   # ~5 min, the artifact
```

The committed artifact is [`artifacts/plumbline_bench.json`](artifacts/plumbline_bench.json), and it
records the machine, the Python and Z3 versions, the seed, and the shape of every measurement.
From that run, on an M-series arm64 macOS box with **z3 5.0.0**:

| measure | value | shape |
|---|---|---|
| greedy allocator p50 / p99 | **1.93 ms / 2.12 ms** | 8 instruments x 20 lines x 40 benefits |
| offline oracle p50 / p99 | **13.6 s / 14.4 s** | same shape, 16 of 24 instances unresolved |
| greedy is optimal on | **101 of 150** (67.3%) | 12 lines x 20 benefits, 150 resolved, 0 unresolved |
| gap p50 / p90 / max | **0 bp / 72 bp / 1036 bp** | same 150 instances |
| gap at the headline size | **p50 13 bp, max 92 bp** | 6 of 12 resolved at a **30 s** solver timeout |

Read those two right-hand columns. **The gap distribution is measured at 12x20, not at the
headline 8x20x40 size** — at the headline size the solver leaves half the instances unresolved
even at fifteen times the checkout timeout, and unresolved instances are counted separately and
never imputed. A timeout is not evidence of a small gap.

The **greedy allocator is conservative by construction but not optimal.** Quote the measured gap;
never imply optimality.

### 4. The solver pathology this architecture exists to avoid

On timeout, Z3 can report a lower bound **above** its upper bound. An implementation that reads
that bound signs an incoherent number.

```bash
PYTHONPATH=backend .venv/bin/python -c \
  "from plumbline.oracle import classify_bounds; \
   print(classify_bounds(result='unknown', lower_minor=500, upper_minor=100))"
```

```
('ORACLE_UNKNOWN', 'ORACLE_INCOHERENT_BOUNDS',
 'solver reported lower bound 500 above upper bound 100; neither is a bound on the
  optimum, so no value is reported')
```

`classify_bounds` is pure, importable without z3, and checks `lower > upper` **first** — before
sat, before timeout — so the pathology gets its own reason code instead of being folded into
"timeout". `optimum_minor` is `None` unless the status is `ORACLE_OPTIMAL`.

### 5. Determinism

```bash
PYTHONPATH=backend:. .venv/bin/python scripts/headline.py                    # make headline
PYTHONPATH=backend:. .venv/bin/python scripts/headline.py --fingerprint-only # make fingerprint
```

`scripts/headline.py` computes every scenario twice and **exits non-zero if the bytes differ**,
then prints the per-scenario digests, the aggregate fingerprint, and every measured figure with
its conditions attached — machine-bound latency labelled as machine-bound, and the two
non-interchangeable optimality gaps each carrying its own generator, size and solver.

A SHA-256 over every scenario's full output at a fixed clock (`DEMO_CLOCK = 1785110400`).
Timestamps are explicit parameters throughout — never `time.time()` inside decision logic — so
two runs of the same code must agree. **The test is the equality, not the literal digest**: the
digest changes whenever any scenario's output changes, which is what it is for. Record it at
demo freeze and compare on the demo machine.

### 6. The governance kernel, live

```bash
PYTHONPATH=backend:. .venv/bin/python -m agent.shopper --scenario injection --replay
```

```
WITHOUT CAVEAT  ₹50,000 left the Card Member's account that the signed intent never covered.
WITH CAVEAT     refused in 0.36 ms — DENY / MANDATE_CART_DIVERGENCE;
                verdict INJECTION_COMPROMISE.
LEDGER          root 7717b5971a91b8a1… (3 entries, chain verified)
```

**Never quote the sub-millisecond entailment figure in the same breath as valuation latency.**
Different components, different budgets. The valuation numbers are in §3.

---

## Running the demo

```bash
./scripts/demo.sh          # checks, then uvicorn and vite together   (make demo)
./scripts/demo.sh --serve-only   # skip the checks, just bring both up
```

One terminal, both processes, and Ctrl-C stops both — including the vite dev server, which npm
runs as a grandchild and which an ordinary `kill` would orphan on its port.

By hand, if you want the pieces separately:

```bash
# terminal 1 — backend
PYTHONPATH=backend:. .venv/bin/python -m uvicorn caveat.api:app --port 8000   # make api

# terminal 2 — console
npm run dev --prefix frontend          # mock transport, needs no backend at all
npm run dev:live --prefix frontend     # live transport, expects :8000   (make web)
```

The header badge shows `MOCK` or `LIVE` at all times and switches at runtime. The console never
presents recorded data as live.

**Which transport to demo in is a question with a checkable answer, so the script checks it**
rather than taking a side: it fetches `/api/plumbline/state` once `uvicorn` is up and starts the
console in live mode only if that route returns an envelope. Five of the six valuation screens
read that one call, so if it is not serving they all show a "valuation service unavailable"
banner together — the script falls back to mock and prints the reason instead.

### The beats

| # | beat | scenario | console |
|---|---|---|---|
| 1 | The naive sum overstates | `overstatement` | screen 01 |
| 2 | It refuses to sign, and says why | `refusal` | screen 04 |
| 3 | Omission leaves a signature | `omission` | screen 03 |
| 4 | Graceful degrade: observe, then enforce | `graceful_degrade` | **no screen — see below** |
| 5 | Attribution: which benefits to cut | — | screen 05 |
| 6 | Hand the judge the controls | — | **not built — see below** |

Any scenario runs headless:

```bash
PYTHONPATH=backend .venv/bin/python -c \
  "import json; from plumbline import scenarios; print(json.dumps(scenarios.run('graceful_degrade'), indent=1))"
```

---

## Architecture

```
backend/caveat/      the governance kernel
  constraints.py  entailment.py  mandate.py  cart.py  ledger.py  pdp.py
  engine.py  store.py  revocation.py  stepup.py  registry.py  evidence.py
  exposure.py  money.py  api.py  adapters/{ap2,acp,mcp}.py

backend/plumbline/     value disclosure
  manifest.py     issuer-signed benefit manifest over canonical JSON
  allocate.py     deterministic capacitated allocator — THE HOT PATH
  witness.py      allocation witness + linear-time verifier
  oracle.py       Z3 Optimize, OFFLINE ONLY — measures the optimality gap
  bench.py        the benchmark harness behind artifacts/plumbline_bench.json
  evaluate.py     cart -> per-instrument incremental value + derivation tree
  receipt.py      signed Decision Receipt (full candidate set, not just the winner)
  transparency.py RFC 6962-style log: inclusion + consistency proofs, signed tree heads
  attribution.py  the receipt corpus -> which benefits actually won transactions
  products.py     modelled card catalogue      authoring.py   manifest authoring guardrails
  scenarios.py    the demo beats, deterministic  mcp_server.py  MCP surface

agent/               a vulnerable Claude agent, an injection harness, recorded traces
frontend/            Vite + React 19 + TS + Tailwind console
```

### What Amex signs, and what it never signs

Enforced in code, not by convention. `receipt.issuer_sign_facts()` is the **only** issuer-signing
entry point. It accepts a manifest by type and refuses everything else; it then recursively scans
the submitted object against a ranking vocabulary and refuses on any hit, reporting the JSON path.
`sign_receipt()` raises `IssuerSigningBoundaryError` on `signer_role="issuer"`. There is no
`force` and no lower-level signer.

- The manifest carries **signed facts**: earn rates, protections, credit balances, caps.
- The **valuation policy and the ranking belong to the cardholder or the agent.** Its hash is
  recorded; it is not issuer-endorsed.
- **There is no path that produces an issuer signature over a comparison.** The corpus therefore
  contains no Amex-signed assertion that a competitor won — and the `cross_instrument` scenario
  ranks Chase above both Amex products on its cart, unsigned by Amex, to show the boundary holds
  in the case that matters.
- Non-numeric value is carried as **CONSIDERED-BUT-UNPRICED**, so the receipt proves the agent
  saw it and the integer never claims to be the whole worth of the card.

### The receipt obligation is a caveat on the cardholder's own mandate

Not a condition Amex imposes on a platform. The platform is asked for nothing and never sees the
check. An agent that cannot produce a receipt fails to discharge **the cardholder's own delegated
authority** — architecturally identical to a spend limit denying.

Default posture is **observe-only** (`receipt.POSTURE_OBSERVE_ONLY`): the evaluator computes and
signs, the receipt is emitted, and absence of a counterpart receipt is logged as an *unattested
selection* — never a decline. Enforcement is a mode the Card Member elects.

### Two verifiers, one rule

The console re-implements the witness verifier in TypeScript
([`frontend/src/lib/witness.ts`](frontend/src/lib/witness.ts)) and runs it in the browser, because
a console that displayed the server's `ok` boolean would be *asserting* verifiability rather than
demonstrating it. Two implementations drift, and the failure mode is asymmetric: a console
verifier missing a check prints VERIFIED under a number the evaluator refuses.

So the drift is pinned from both sides. `backend/tests/test_console_conformance.py` reads the
kernel's own failure-code table and fails if any code has no counterpart in the console.
`frontend/scripts/smoke.tsx` hand-builds a forged witness for every check — cap evasion, per-line
over-offset, credit-over-line, duplicate SKU, manifest substitution, cart substitution, currency
mixing — and asserts the browser refuses each with the same code the kernel uses.

---

## Known gaps

Stated here rather than discovered on stage.

1. **`GET /api/plumbline/state` is not implemented.** The console fetches it
   ([`liveClient.ts`](frontend/src/lib/liveClient.ts)); `caveat.api` serves
   `/api/plumbline/products`, `/api/plumbline/scenarios` and `/api/plumbline/scenario/{name}` but not
   `/state`. **In live mode the five valuation screens render a named "valuation service
   unavailable" banner.** The kernel screens are unaffected — they load from `/api/state`, which
   exists. `frontend/scripts/gen_plumbline_fixtures.py` is the reference producer for the envelope
   and builds it by calling the real backend modules, so the route can be assembled from the same
   calls. **Until then, demo in mock mode.**
2. **Demo beat 4 has no screen.** `scenarios.graceful_degrade` is complete, deterministic and
   tested — four passes, three proceed, one denies, and the only denial is against the Card
   Member's own mandate. Nothing in the console renders it. This is the structural answer to the
   "you would make Amex the only credential that hard-fails" objection, and right now it can only
   be shown as JSON on a terminal.
3. **Demo beat 6 has no screen.** No surface lets a judge perturb a manifest and watch the
   ranking and the witness move. Console screen 06 is the governance kernel instead.
4. **Inclusion-proof generation is O(n) in log size** (~13 ms at 10,000 entries) because the tree
   is recomputed from the leaves. Proof *verification* — the part a counterparty runs, and the
   part the argument rests on — is O(log n) at 5–8 µs. One readable implementation was chosen over
   a cached fast path plus a reference implementation that must be kept in agreement.
5. **Attribution reports render under the kernel's default rupee sign.**
   `backend/plumbline/attribution.py` is the one module in the valuation path whose serializers
   still reach `fmt_money` without naming a currency. It is a corpus-level aggregate over many
   receipts and has no single currency to name, so fixing it means deciding what a mixed-currency
   corpus reports rather than threading an argument. The demo corpus is INR and the console
   renders it correctly; a USD corpus would show rupee signs. Everything upstream of it —
   manifest, witness, verification, derivation, ranking, receipt and the MCP payloads — now
   requires the currency explicitly, and `backend/tests/test_currency.py` fails if a new
   serializer defaults one.

---

## Honest limitations — state these, never hide them

- **Signatures are HMAC-SHA256 under prototype keys.** Not Ed25519 — there is no asymmetric
  signing anywhere in this repository, and `CLAUDE.md`'s architecture block is wrong to say
  otherwise. Production signs with the issuer's key in an HSM; canonicalisation and the
  verification flow are unchanged, which is the part the argument rests on.
- **Canonicalisation is sorted-key, tight-separator, UTF-8 JSON — not RFC 8785 JCS.** It is
  stable and sufficient for signing here. Do not call it JCS.
- Cumulative-budget and velocity caveats are stateful and **not** offline-verifiable. Only
  structural caveats verify from the credential alone.
- Manifests model **publicly published card terms**, and remaining balances are synthetic member
  state. Both facts are on every manifest's `source` field and the console shows them. We never
  claim access to a live Offers feed.
- The greedy allocator is conservative by construction but **not optimal**; §3 is the measured
  gap on a modelled distribution, and characterises the allocator on that distribution and
  nothing wider.
- All rails, merchants and authorization responses are **mocked**. Round 2 explicitly permits it.
- The trust model is **demonstrated, not deployed** — there is no counterparty signing manifests.
- Loss rates in the exposure book are **modelled, not observed**.

## Conventions

- Money is **integer minor units**. No floats in any decision path.
- **The LLM proposes; the deterministic engine disposes.** No model output ever becomes a number
  in a signed artifact.
- Timestamps are explicit parameters, never `time.time()` inside decision logic.
- Reason codes are module-level constants, never inline strings.
- Every state-changing decision lands in the transparency log.
- **PLUMBLINE never surfaces an unused credit to a member as a nudge.** Benefit expense is
  recognised on use; a nudge product converts breakage into recognised expense, which is the
  opposite of the argument. The number goes into the agent's ranking, not the member's inbox
  (`attribution.NEVER_A_NUDGE`).
