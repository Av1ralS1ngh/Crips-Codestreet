# PLUMBLINE — the layer where value is declared, proved, and contestable

Submission for **American Express CodeStreet 2026**, **Problem Statement #5 — Governance Layer
for Financial Agents** (permissions, spend limits, audit logs, emergency controls), with
demonstrated reach into **#2** (Card Benefit Activation) and **#6** (Benefit-Underutilization
Analytics).

Selected over three alternatives by a 10-judge adversarial panel (38 pts vs 27 / 24 / 11), after
each candidate was steelmanned and then attacked by adversaries playing a skeptical AmEx executive
and a distinguished engineer. **Every "must-fix" from that panel is encoded below as a standing
constraint.** Violating one is how we lose.

## The framing that makes this PS#5 and not a rewards feature

**Value disclosure is a permission.** A mandate today says "you may spend up to X at Y." It cannot
say "you may act on my behalf only if you show your work about which instrument you chose and why."

Instrument selection is the one step in an agentic transaction that no audit log covers, no protocol
specifies, and no emergency control reaches. We govern it with the same primitives as everything
else: an unremovable caveat, a deterministic decision function, a transparency log, and a kill
switch.

## The verified gap (check these before quoting them)

- **ACP** has exactly one formally defined extension — **Discount** (merchant promo codes). Its docs
  list loyalty as a "potential future feature," unspecified.
- **UCP shipped a Loyalty Extension in January 2026** (Shopify/Google, via Talon.One). It models
  memberships, wallets and tiers — **inside the seller's checkout response object**. No MCC earn
  rates, no statement credits with remaining balance, no protections, and it explicitly cannot
  compare two payment instruments.
- **AP2's** Payment Mandate records *which* instrument was used, tokenized. Not *why*. The spec:
  "The Mandate selection mechanism is outside the scope of this specification."
- **Amex ACE Cart Context** is explicitly for "validation, authorizations, and dispute
  investigations" — provenance, not valuation.

So issuer-side value is not merely undefined; **as of January 2026 it is being defined on the
seller's side of the wire, in the seller's response object, by the seller's vendor.**

## Standing constraints — the panel's must-fixes

These are not style notes. Each one is a sentence an AmEx executive could use to end the pitch.

### 1. The inversion trap — TERMINAL if unaddressed in the first two minutes

Card Member Services expense is recognised largely **as benefits are used**. AmEx's own Q2 2026
variance commentary attributes the +50% to *"higher usage of Card Member benefits."*

**Therefore "the money is already spent, we are not asking you to spend more" is FALSE on their own
accounting, and the CFO who wrote that commentary is in the room. Never say it.**

The correct frame, delivered before any architecture:

> Every benefit dollar is expensed on use, and you have no record of which of those dollars actually
> caused a card to be chosen. The receipt corpus is that record. It is the first instrument that
> tells you which benefits to **cut**.

Structural reinforcement, and it must hold in the code:

- PLUMBLINE asserts value the member **need not redeem** — earn rates, protections, coverage.
- **It never surfaces an unused credit to a member as a nudge.** The number goes into the agent's
  ranking, not the member's inbox. A nudge product converts breakage into recognised expense; that
  is the opposite of the pitch.

### 2. Self-inflicted suppression — relocate the enforcement point

"No receipt → no discharge → decline" would make Amex the only credential in a third-party checkout
that hard-fails. A platform facing a credential it cannot discharge does not comply — **it routes
around**, which is precisely the 10-K risk factor, self-administered.

**The fix is architectural, not a toggle.** The receipt obligation is a caveat on the mandate the
**Card Member issues to their own agent**. It is not a condition Amex imposes on a platform. The
platform is asked for nothing and never sees the check. An agent that cannot produce a receipt fails
to discharge **the cardholder's own delegated authority** — architecturally identical to a spend
limit denying, which is exactly what PS#5 asks for.

Then the carrot, using a surface Amex already shipped: **no receipt, no Agent Purchase Protection
coverage.** Coverage is conditioned on evidence; authorization is not.

Default posture is **observe-only**: the evaluator computes and signs, the receipt is emitted, and
absence of a counterpart receipt is logged as an *unattested selection* — never a decline.
Enforcement is a mode the Card Member elects. Demo the graceful-degrade path live.

### 3. Delete the acceptance predicate — and say the deletion out loud

An issuer-signed field naming where the card will be refused is a machine-readable instruction to
route away from Amex, **with Amex's signature on the reason**, in the year they message 99% US
parity. Nobody at that company signs that field.

Acceptance belongs in the agent's own routing layer, from data the agent already holds. Say *"we
built it, then removed it, here is why"* before anyone asks. Turning the worst unforced error into
evidence of judgement is worth more than the field.

### 4. What Amex signs, and what it never signs

Pre-empt *"you want us to fund the machine-readable commoditization of the thing we charge $895
for."* They have a third option they litigated to the Supreme Court to protect: keep value
illegible, keep the counter closed.

The counter: **illegibility worked when a human read marketing copy. To a ranking machine, illegible
equals absent, and absent is not a premium.**

Then narrow the signature scope, and enforce it in code:

- The manifest carries **signed facts**: earn rates, protections, credit balances, caps.
- The **valuation policy and the ranking belong to the cardholder or the agent**. Its hash is
  recorded; it is *not* issuer-endorsed.
- **Amex never signs "we beat Chase on this cart."** The corpus therefore contains no Amex-signed
  assertion that a competitor won — which is also the answer to the churn objection.
- Non-numeric value (service, membership, brand) is carried as **CONSIDERED-BUT-UNPRICED**, so the
  receipt proves the agent saw it and the integer never claims to be the whole worth of the card.

### 5. Forbidden claims

- ❌ "AI agents choose cards today." **False.** Every shipped protocol pins a human-chosen
  instrument. Say this yourself, first, before anyone corrects you — it buys the rest of the
  argument.
- ❌ "The window is closing." Not 72 hours after the CEO said *"we're sort of in the preseason."*
  Say instead: **the transaction clock and the standards clock are different clocks.** UCP's Loyalty
  Extension shipped in January; ACP's Discount Extension is still the only formally defined
  extension — both during the preseason. Schemas set in a quiet market are the ones nobody reopens
  when it gets loud. And: **you cannot backfill a disclosure record for a choice already made.**
- ❌ "Today you could not detect that." → **"No industry mechanism exists to record it."** Amex wrote
  the risk factor; never tell the authors they are blind to their own filing.
- ❌ The 4,200-question / 62%-affiliate / sub-6%-issuer statistic. Untraceable, and TPG/NerdWallet/
  Bankrate are Amex's *paid* acquisition channel — the line reads as "you called your own marketing
  channel a threat." The gap is provable from the specs alone.
- ❌ Any claim that Amex forgot, missed, or is blind to something in its own filings.
- ❌ Unlabelled figures. State the period on **every** number spoken aloud, and mark derived numbers
  as derived — the ~$7.8B annualised run-rate especially. In a pitch whose thesis is *"we make agents
  show their work,"* one unsourced number is a thematic self-refutation.

### 6. Roadmap collision — cede the manifest, claim the receipt

The Chairman's Letter 2026 priority is **distribution**: surface Resy inventory, Amex Offers search
and enrollment, and Amex Travel booking inside third-party AI. This is **disclosure**: make the
choice among instruments produce a signed, contestable record. Lead with that distinction rather
than defending it. Distribution without a signed value contract is Amex's data narrated by someone
else's response object.

Distinguish the member-scoped state overlay from the in-app benefit trackers shipped 18 Sept 2025:
same underlying state, opposite consumer — one renders to a human, one is read by a machine that is
about to choose.

## Architecture

Keep the working kernel: macaroon mandates that can only narrow, Z3 entailment at every delegation
hop, revocation as a freshness caveat, the transparency log, evidence packages, liability routing.
Add three artifacts.

```
backend/caveat/            existing kernel
  constraints.py  entailment.py  mandate.py  cart.py  ledger.py  pdp.py  money.py
  engine.py  store.py  revocation.py  stepup.py  registry.py  evidence.py
  exposure.py  api.py  adapters/{ap2,acp,mcp}.py
backend/plumbline/           NEW
  manifest.py    issuer-signed benefit manifest (HMAC-SHA256 over sorted-key canonical JSON)
  allocate.py    deterministic capacitated allocator — THE HOT PATH
  witness.py     allocation witness + linear-time verifier
  oracle.py      Z3 Optimize, OFFLINE ONLY — measures the optimality gap
  evaluate.py    cart -> per-instrument incremental value + derivation tree
  receipt.py     signed Decision Receipt (full candidate set, not just the winner)
  transparency.py RFC 6962-style log: inclusion + consistency proofs, signed tree heads
  attribution.py the receipt corpus -> which benefits actually won transactions
  products.py    the modelled card catalogue + the point valuation policy
  authoring.py   manifest validator — the deterministic gate on a drafted manifest
  scenarios.py   the five demo beats, one fixed clock, byte-identical across runs
  bench.py       offline gap + latency harness -> artifacts/plumbline_bench.json
  mcp_server.py  the evaluator over MCP stdio, for an agent that derives instead of guesses
```

### The hard core, corrected

**Value on a cart is a capacitated allocation, not a sum.** Two credits compete for one line.
Multipliers have annual caps with partial consumption. Credits have reset windows and remaining
balances. Offers are one-per-account and enrollment-gated. Naive per-line summation double-counts
and **overstates** — which is the one error that must never happen.

**The panel benchmarked the MaxSMT approach and it does not hold a checkout budget.** Measured at
8 instruments × 20 lines × 40 benefits: 451ms–2695ms, 6× variance at constant problem size, timeouts
at 2s, and — worst — on timeout Z3 reported `lower > upper`, so a naive implementation reading the
bound signs an **incoherent number**. There is no DP fallback: capacitated assignment with
exclusivity groups is a generalized assignment problem, NP-hard.

**So the soundness argument is constructive, not solver-dependent.** This also fixes a genuine logic
bug the panel found: `UNSAT(realizable < asserted)` is trivially SAT, because the empty allocation
realizes zero.

> **Conservatism by construction.** The evaluator asserts a value only if it can **exhibit a
> concrete, valid allocation realizing at least that value.** The witness *is* the line-item
> derivation. Since the witness is achievable, the asserted value can never exceed the best value
> obtainable **under the constraints the manifest declares.** Conservatism is proved by producing an
> allocation, not by an unsat proof.

**State the claim at exactly that width and no wider.** It is *not* "never exceeds what the card can
actually deliver" — the verifier enforces the manifest's vocabulary (per-benefit capacity, per-line
credit offset, per-(line, group) exclusivity) completely, and nothing outside it. A term the manifest
failed to declare is not a term the witness can honour. That is a statement about manifest quality,
which the authoring validator gates, not about the soundness proof. An engineer on the panel will
find the one-word overreach if we leave it in; conceding the boundary unprompted is worth more than
the stronger sentence.

Consequences, all good:

- The **hot path is a deterministic greedy allocator** producing a witness. Measured in
  `artifacts/plumbline_bench.json`, 300 reps per size on an M-series laptop: **33µs p50 at 2×4×6 and
  1.93ms p50 / 2.12ms p99 at 8×20×40**. Say *sub-millisecond for a single instrument, ~2ms at the
  size MaxSMT was benchmarked on*, and **never say "microseconds" for the headline size** — it is
  wrong by ~60×, and one wrong number refutes the thesis of the pitch. The claim that carries the
  argument is the **spread**, not the mean: p99 is within **1.10×** of p50, against the solver's 6×.
- **Verification is linear-time and needs no solver**: re-add the numbers, check the capacities. Any
  counterparty can verify without Z3.
- **Z3 runs offline** as an oracle measuring the optimality gap and finding adversarial carts where
  greedy underperforms. It never sits in the checkout path.
- **Never quote the ~5ms entailment figure in the same breath as valuation latency.** Different
  components, different budgets. Ship a measured p50/p99 table.

### Transparency log, framed correctly

Frame as **RFC 6962 Certificate Transparency**, not "a Merkle chain." Inclusion proofs bind a
receipt to a tree; **consistency proofs make retroactive editing detectable**; a witness kills the
split-view attack where a platform shows Amex one log and the cardholder another. **Omission is the
attack** — an agent that drops Amex from the candidate set entirely — and split-view defence is the
correct mechanism.

Defusal sentence for the exec who hears "Merkle" and thinks crypto: *this is the mechanism
underpinning public-web TLS trust for a decade — no chain, no token, no consensus.*

### The attribution product — the answer to must-fix #1

Every signed receipt records the full candidate set, each instrument's derivation, and the stated
decision criterion. So the corpus is a running, cart-level record of **which benefits actually
appeared in a winning derivation and which never do** — benefit-level attribution against a
revealed-preference outcome at the moment of choice, not a renewal signal the CFO says takes *"two
years to find its way into the P&L."*

The 2×2, re-keyed from usage to selection-influence:

| | rarely decisive | often decisive |
|---|---|---|
| **high cost** | dead weight — cut or renegotiate | load-bearing — protect |
| **low cost** | noise | an option — fund it, stop apologising for breakage |

### Refusal is a typed, first-class output

When the evaluator cannot exhibit a witness for an asserted value, it **refuses to sign**, with a
reason code, anchored in the log. A system that visibly declines to make a claim is the strongest
possible answer to "punish vaporware." Demo the refusal on stage.

### Zero-adoption answer

*"What is this worth if no platform ever ships your receipt?"* The corpus generated from Amex's own
agent surfaces plus consented cardholder-side instrumentation still tells Amex where benefit dollars
influence instrument choice — a spend-allocation decision **entirely inside Amex's four walls**,
needing no partner, no lawyer and no standards body. Unilaterally useful on day one.

### Compliance symmetry

The receipt must certify **compliance as readily as violation**. A receipt that can only catch
cheating is something a platform routes around; a receipt that can prove *"this platform ranked
instruments faithfully"* is something a platform wants to display.

## Demo

1. **The naive sum overstates.** Show a cart where per-line summation claims a value the card cannot
   actually deliver — two credits competing for one line. Then the witness-backed number, lower and
   achievable, with the allocation shown. *The intuitive answer overstates; ours is provable.*
2. **The refusal.** Feed an assertion no witness supports. The evaluator declines to sign, with a
   reason code, in the log.
3. **Omission leaves a signature.** A platform silently drops Amex from the candidate set. The
   consistency proof makes the edit detectable and attributable.
4. **Graceful degrade.** No counterpart receipt: the transaction **proceeds**, the gap is recorded as
   an unattested selection. Then the cardholder elects enforcement and the same flow denies.
5. **The attribution close.** The corpus ranks benefits by how often they appear in a winning
   derivation. Which to cut.
6. **Hand the judge the controls.** Perturb a manifest; watch the ranking and the witness move.

## Conventions

- Money is **integer minor units**. Never floats in the decision path.
- **The LLM proposes; the deterministic engine disposes.** No model output ever becomes a number in
  a signed artifact.
- Timestamps are explicit parameters, never `time.time()` inside decision logic — runs must replay.
- Reason codes are module-level constants, never inline strings.
- Every state-changing decision lands in the transparency log. If it is not in the log, it did not
  happen.
- Comments state constraints the code cannot show. No narration.

## Commands

```bash
cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests -q   # 1468 pass, 2 skip
PYTHONPATH=backend:. .venv/bin/python -m pytest backend/tests agent/tests -q   # all 1584
PYTHONPATH=backend:. .venv/bin/python -m uvicorn caveat.api:app --port 8000
npm run dev --prefix frontend            # mock transport; `npm run dev:live` needs the API
npm run build --prefix frontend && npm run smoke --prefix frontend   # 232 rendered assertions
PYTHONPATH=backend:. .venv/bin/python -m agent.shopper --scenario injection --replay
PYTHONPATH=backend .venv/bin/python -m plumbline.bench --quick    # full run is ~5 min in Z3
```

The kernel-only command leaves the repository root off the path, so `agent` is not importable and
the two `/api/scenario/*` routes answer **503 naming the fix** rather than 500. Two tests skip for
the same reason. Start the server with `PYTHONPATH=backend:.` or those five console buttons die.

## Honest limitations — state these, never hide them

- Signatures are **HMAC-SHA256 under a shared prototype secret**. There is no asymmetric crypto in
  this repo. Production signs with the issuer's private key in an HSM; the canonicalisation and the
  verification flow are unchanged, which is the part the argument rests on. Say "HMAC under a
  prototype key" out loud — claiming Ed25519 we did not implement would refute our own thesis.
- Cumulative-budget and velocity caveats are stateful and **not** offline-verifiable. Only structural
  caveats verify from the credential alone.
- Manifests model **publicly published card terms**. We never claim access to a live Offers feed.
- The greedy allocator is conservative by construction but **not optimal**; the offline oracle
  reports the measured gap. Quote the gap, never imply optimality. **Two different worst-case gaps
  exist in this repo and they are not interchangeable** — 10.36% over 150 Z3-resolved instances at
  12 lines × 20 benefits (`artifacts/plumbline_bench.json`), and 45.9% over 303 smaller instances
  against the max-flow oracle in `test_plumbline_core.py`. Different generators, different sizes,
  different solvers. Whichever is spoken aloud must be spoken with its conditions attached.
- At the headline 8×20×40 size the oracle **resolves only 6 of 12 instances even at a 30s timeout**
  (the rest return `ORACLE_NON_NUMERAL_BOUND`), and greedy is exactly optimal on 1 of those 6. The
  honest sentence is *"the gap we could measure there is at most 0.92%"*, not a hit rate.
- All rails, merchants and authorization responses are mocked. Round 2 explicitly permits this.
- The trust model is **demonstrated, not deployed** — there is no counterparty signing manifests.
