# Pitch deck brief — PLUMBLINE

**Paste this whole document into Claude and ask it to build the deck.** It is self-contained: every
system number here was re-measured against the repository, every external figure was checked
against a primary source or is marked **[UNVERIFIED]**, every forbidden claim is listed, and every
likely objection has a prepared answer. Build only from what is here — do not fill gaps from
memory, and do not invent a statistic to make a slide land.

> **READ §5 BEFORE WRITING A SINGLE SLIDE.** This is an **attribution instrument** — it tells
> American Express which benefit dollars to **cut**. It is **not** a benefit-usage driver and it
> never nudges a member to redeem anything. Getting that backwards ends the pitch in the first two
> minutes, on their own accounting. Every slide must be consistent with it.

> Items marked **[UNVERIFIED]** below could not be confirmed against a primary source. Do not put
> them on a slide or say them aloud until you have checked them yourself.

---

## 0. The commission

Build a professional pitch deck for **American Express CodeStreet 2026**, a hackathon run by
American Express on HackerEarth.

- **Audience:** American Express leadership and engineers. Assume the room includes people who
  work on Amex's own agentic commerce programme — Amex shipped the **ACE Developer Kit on
  14 April 2026** — and assume deep domain knowledge and low tolerance for being told about their
  own business. *(Do not assert on a slide that the judges personally built ACE; you do not know
  the panel.)*
- **Venue:** in-person finale, Chennai, India. Grand finale **24–25 August 2026**. Top 6 teams.
- **Presenter:** one person, solo, experienced engineer.
- **Time:** roughly 3 minutes of demo plus questions. The deck supports a live demo; it does not
  replace it.
- **Problem statement:** #5 — *Governance Layer for Financial Agents* (permissions, spend limits,
  audit logs, emergency controls). Secondary reach into #2 (Card Benefit Activation) and #6
  (Benefit-Underutilization Analytics).
- **Judging criteria:** relevance to the problem statement, idea clarity, technical innovation,
  implementation feasibility, business impact and scalability.

**Tone:** confident, precise, unshowy. This is infrastructure, not a consumer app. It wins by
being *correct* in front of people who can tell. Never breathless. Never a sales voice.

---

## 1. The one-sentence pitch

> An AI shopping agent cannot see what a credit card is actually worth on the basket in front of
> it — so it guesses from marketing copy and guesses wrong. We built the missing layer: signed
> card facts, a valuation that shows its working and can be checked by anyone without trusting us,
> and a receipt that makes a suppressed card provable instead of invisible.

---

## 2. The problem, in American Express's own numbers

**Every figure below is from a primary source. Label the period on every one spoken aloud.**

### The cost side — from AmEx's Q2 2026 earnings presentation, reported 24 July 2026

| Line | Q2 2026 | vs Q2 2025 |
|---|---|---|
| Card Member Services expense | **$1,949M** | **+50%** |
| Net card fees (the revenue that expense justifies) | $2,862M | **+15%** |
| Total expenses | $14,482M | +12% |
| Total revenues net of interest expense | $19,637M | +10% |
| VCE (variable customer engagement) as % of revenue | **44.6%** | — |

- Card Member Services has grown **+53%, +49%, +50%** across Q4'25, Q1'26, Q2'26 — **three
  consecutive quarters**. *(Say three. Only three are listed; claiming four invites the one
  question you cannot answer.)*
- FY2025 Card Member Services: **$6,057M, +27%**.
- FY2026 VCE guidance was **raised** from "around 44%" to **44–45%**.
- On 24 July 2026 AmEx **beat on EPS** ($4.53 vs $4.40 estimate), **raised full-year revenue
  growth guidance to 10%**, **held EPS guidance at $17.30–$17.90**, and **the stock fell about 6%**.
- **Do not attribute that share-price move to benefits expense.** The reported reasons were a small
  revenue miss ($19.64B against a $19.69B consensus) and the decision to reinvest the upside rather
  than raise EPS guidance. Put the figures on the slide with no causal claim attached; if a judge
  raises it, agree with them. Our argument does not need the stock move and dies if it overreaches
  on it.
- **DERIVED, NOT AN AMEX FIGURE — label it as ours:** Q2'26 annualised run-rate of Card Member
  Services ≈ **$7.8B**.

### The revenue at risk

- Net card fees FY2025: **$9,993M, +18%**, **30 consecutive quarters of double-digit growth**.
  Average fee per card **≈ $117** (FY2025 average, on ~150M average cards in force). *Do not say
  "+14%" — that growth rate could not be confirmed against a primary source, and a nearby AmEx
  figure is "card fees are more than 14% **of revenue**," which is a different statistic.*
- That line is pure willingness-to-pay. Not protected by acceptance, interchange regulation, or
  credit underwriting. Protected only by someone believing the value exceeds the fee.

### What executives said — verbatim, attributable

**Christophe Le Caillec, CFO**, Q3 2025 earnings call, on the refresh economics:
> "the card fee dynamic is delayed and is summarized over 12 months. While the benefit is
> immediate and is available to everybody."

He added they went into it *"with our eyes wide open."*

**Stephen Squeri, Chairman & CEO**, Q2 2026 call:
> "There's inertia in that number... It takes from beginning to end, something like two years to
> find its way into the P&L."

**Squeri**, Q2 2026 call, on agentic commerce:
> "We're sort of in the preseason. We didn't even get to the early innings of the regular season yet."

**AmEx's own Q2 2026 variance commentary** — this one is load-bearing, see §5:
> "Card Member Services Expense: Increased 50 percent versus Q2'25, primarily due to higher usage
> of Card Member benefits and the new U.S. Platinum benefits."

**AmEx's own forward-looking-statement language**, Q1 and Q2 2026 presentations:
> "...the investments and enhancements that the company makes with respect to its value
> propositions... **potentially in a manner that is not cost-effective**... the company's ability
> to identify and negotiate **partner-funded value** for Card Members..."

**Luke Gebb, EVP & Head of Global Innovation**, *This Week in FinTech* interview, published
May 2026:
> "We want to ensure those perks are influential even when a human isn't clicking the button."

*(Attribute it to the interview, not to a dated speech. This quote is the single best thing in the
deck: their own head of innovation naming the exact problem we solve. Get the attribution right.)*

### The filed risk

AmEx's **FY2025 Form 10-K** discusses agentic commerce in its risk factors. The risk factor warns
about platforms that control payment method choice "through digital wallets, agentic or other
commerce-related experiences" and could:

> "suppress use of, or degrade the experience of using our products"

and could "require payments from us to participate."

**[UNVERIFIED — do not say aloud, do not put on a slide]** The claim that the 10-K went "from zero
to twelve mentions of *agentic* in one annual cycle, editing a live risk factor to insert the word"
could not be confirmed against the filing. The quoted risk-factor language above is what carries
the close; the mention count adds nothing and is exactly the kind of unsourced statistic §9
forbids. If you want it, run a full-text count on the FY2024 and FY2025 10-Ks yourself first and
put the method in the footnote.

---

## 3. The gap — verified by reading the actual specifications

This is the technical heart of the argument and every claim was checked against the real spec
repositories, not summarised from press coverage.

| Protocol | What it standardises about value |
|---|---|
| **ACP** (OpenAI / Stripe) | Exactly **one** formally defined extension: **Discount** — the *merchant's* promo codes. Its own docs list loyalty as a "potential future feature," unspecified. |
| **UCP** (Google, with Shopify) | Shipped a **Loyalty Extension on 28 January 2026**, authored by **Talon.One** as part of its Unified Incentives Protocol — but it lives **inside the seller's checkout response object**, models memberships, wallets and tiers, and **cannot compare two payment instruments**. |
| **AP2** (Google, ~60 organisations, donated to the **FIDO Alliance on 28 April 2026**) | Records *which* instrument was used, tokenised. Not *why*. The spec: **"The Mandate selection mechanism is outside the scope of this specification."** |
| **Visa Trusted Agent Protocol** | Carries agent identity, a hashed payment credential, and **card metadata and card art so a merchant can display the right card** — presentation, not valuation. No issuer-side value fields: nothing about what the card is worth on this cart. |
| **AmEx ACE Cart Context** | Explicitly for "validation, authorizations, and dispute investigations" — provenance, not valuation. |

**The synthesis:** the ecosystem has standardised **merchant-side cost signals** and left
**issuer-side value completely undefined**. An agent building a cart can read a promo code and a
shipping SLA. It cannot read that this card is worth more on this basket.

And as of January 2026 issuer value is no longer merely undefined — **it is being defined on the
seller's side of the wire, in the seller's response object, by the seller's vendor.**

---

## 4. The proof — our own agent getting it wrong

This is the demo's opening and the deck's most important slide.

**The basket: $1,078, six ordinary items.**

| Item | Amount |
|---|---|
| Weekly groceries, US supermarket | $486 |
| Neighbourhood dinner for four | $412 |
| Uber rides this week | $72 |
| Grubhub delivery order | $64 |
| Lyft ride to the airport | $26 |
| Dunkin' coffee run | $18 |

**Same AI model (claude-opus-5), same basket, run twice.**

| | Control — marketing copy only | Derived — PLUMBLINE over MCP |
|---|---|---|
| Tools available | product catalogue + marketing copy | `value_cart`, `explain_derivation`, `get_manifest` |
| Card chosen | **Amex Platinum** | **Amex Gold** |
| Value it claimed | **$150** (a guess) | **$67.18** (engine-computed) |
| What that card is actually worth here | **$25.78** | **$67.18** |
| Money left on the table | **$41.40** | none |
| Receipt verdict | `CHOICE_DEVIATES_FROM_STATED_CRITERION` | `RANKING_FAITHFUL` |

**Why Platinum loses on this basket** — and this is the insight, not a gotcha:

```
5x MR on prepaid hotels (amextravel.com) ........ BLOCKED_ADMITS_NO_LINE
5x MR on flights booked direct .................. BLOCKED_ADMITS_NO_LINE
$300 prepaid hotel credit, Jul–Dec .............. BLOCKED_ADMITS_NO_LINE
$100 Resy credit, Jul–Sep quarter ............... BLOCKED_ADMITS_NO_LINE
$200 airline fee credit ......................... BLOCKED_ADMITS_NO_LINE
$100 Fine Hotels + Resorts property credit ...... BLOCKED_ADMITS_NO_LINE
```

*(Those are Platinum's own benefit labels, verbatim from the engine. Three more block for
different reasons and are worth naming if asked: `$300 prepaid hotel credit, Jan–Jun` and
`$100 Resy credit, Apr–Jun` are `BLOCKED_BALANCE_EXHAUSTED`, and `$75 quarterly lululemon
credit` is `BLOCKED_NOT_ENROLLED` — three distinct reason codes, not one.)*

**Be ready for "then where does the $25.78 come from?"** — $10.78 of 1x base earn across all six
lines, plus **$15 of monthly Uber Cash** that *does* attach to the Uber line. Not everything blocks,
and saying so is what makes the blocked list credible.

Platinum is a travel card. This is a groceries-and-takeaway basket. **The advertised value is real
— it is just an answer to a different question.** Marketing describes a card *in general*; a
purchase happens *specifically*. Nothing in any shipped protocol bridges those two.

**The gate that fires.** In the control run the agent wrote, in its explanation to the customer,
"over $1,900 a year in statement credits" — lifted from marketing. A deterministic checker tried to
re-derive every figure in that sentence, failed on that one, and blocked it:

```
[FAIL] AGENT_NARRATIVE_REJECTED_UNVERIFIED_FIGURE   $1,900
```

In the derived run the same gate **passes**, because every figure — $67.18, $31.70, $25.78 — came
from the engine. **This is "the LLM proposes, the engine disposes" made visible on screen rather
than asserted on a slide.**

---

## 5. THE SINGLE MOST IMPORTANT FRAMING — the inversion trap

**This is terminal if not addressed in the first two minutes. Read it twice.**

Card Member Services expense is recognised largely **as benefits are used**. AmEx's own variance
commentary attributes the +50% to *"higher usage of Card Member benefits."*

**Therefore any product that drives benefit usage inflates the exact line item under the most
pressure — and the CFO who wrote that commentary is in the room.**

**NEVER say:** "the money is already spent, we're not asking you to spend more." It is *false* on
their own accounting.

**The correct frame, before any architecture appears:**

> Every benefit dollar is expensed on use, and there is no record anywhere of which of those
> dollars actually caused a card to be chosen. The receipt corpus is that record. It is the first
> instrument that tells you which benefits to **cut**.

Reinforce structurally: PLUMBLINE asserts value the member **need not redeem** (earn rates,
protections). **It never surfaces an unused credit to a member as a nudge.** The number goes into
the agent's ranking, not the member's inbox.

**Positioning note — for you, never for a slide.** Most competing teams choosing the benefits
themes will pitch "help cardholders use more of their perks." On AmEx's own accounting that
inflates the line under the most pressure, and the surface already exists — AmEx shipped in-app
benefit trackers on 18 September 2025. **Win this by being on the right side of it, silently.**
Never say a word about other teams, and never characterise AmEx's own economics back at them as if
it were news. Show the attribution instrument and let the contrast do the work.

---

## 6. What we built

Four artifacts on top of a working agent-governance kernel.

### 1. Benefit Manifest — issuer-signed facts
Per product: earn rates keyed by merchant category, statement credits **with remaining balance**,
protections, caps, eligibility predicates, enrollment state. Signed over canonical JSON.

**The signature boundary is load-bearing and enforced in code:**
- **SIGNED:** facts — rates, protections, balances, caps.
- **NOT SIGNED:** the valuation policy and the ranking. Their hash is recorded; they are *not*
  issuer-endorsed.
- **The issuer never signs "we beat another card on this cart."** The corpus therefore contains no
  issuer-signed assertion that a competitor won.
- Non-numeric value (service, membership, lounge access) is carried as
  **CONSIDERED_BUT_UNPRICED** — the receipt proves the agent saw it, and the integer never claims
  to be the whole worth of the card.

### 2. The valuation — and why it is genuinely hard
Valuing a cart is a **capacitated assignment problem**: cart lines assigned to benefit buckets
subject to remaining balances, annual caps, and exclusivity groups. Two $10 dining credits cannot
both cover the same $12 lunch.

**Naive per-line summation double-counts and therefore OVERSTATES** — and overstating is the one
error that must never happen, because an agent acting on an inflated number produces a purchase
the card cannot back.

**Conservatism by construction:**
> The evaluator asserts a value only if it can **exhibit a concrete, valid allocation realizing at
> least that value**. The witness *is* the line-item derivation. Because the witness is achievable,
> the assertion can never exceed the best value obtainable **under the constraints the manifest
> declares**.

Three consequences: the hot path is a deterministic allocator with **no solver**; verification is
**linear-time and needs no solver at all**, so a counterparty checks the issuer's claim with
arithmetic rather than trust; and a solver runs **offline only**, to measure how far below optimal
the allocation sits — a gap we quote rather than an optimality claim we make.

### 3. Decision Receipt + transparency log
Signed, carrying **the full candidate set — not just the winner, because omission is the attack.**
Records each instrument's value, its witness hash, verification status, the ranking, the stated
decision criterion, manifest hashes, and agent + platform identity.

The log is **RFC 6962 Certificate Transparency** — inclusion proofs bind a receipt to a tree head;
**consistency proofs make retroactive editing detectable**; a witness role kills the split-view
attack where a platform shows the issuer one log and the cardholder another.

*Defusal line for anyone who hears "Merkle" and thinks crypto:* this is the mechanism underpinning
public-web TLS trust for a decade. No chain, no token, no consensus.

### 4. The agentic layer
A **real MCP server** using the official Python MCP SDK, verified over a genuine stdio subprocess
handshake, serving `list_instruments`, `list_carts`, `value_cart`, `explain_derivation`,
`get_manifest`. An **AST scan of the transport module fails the build if any arithmetic operator
appears in it** (string building excepted, and that exception is written into the test) — "the
server contains no valuation logic of its own" is checked mechanically, not asserted.

*(Amex's **ACE Developer Kit**, announced **14 April 2026**, is aimed squarely at third-party
agents — Luke Gebb: "the purpose of the ACE Developer Kit is really about third-party agents —
Claude, ChatGPT, etc." MCP is the transport those agents speak. **Do not claim Amex shipped Resy
into Claude over MCP on a specific date** — that could not be confirmed, and it is a claim the room
would know better than you.)*

Plus a **manifest authoring agent**: an LLM drafts a manifest from card terms, a deterministic
validator rejects it with typed reason codes, the agent revises, and **only a draft that passes can
be signed**. A visible rejection is the feature — it proves the model is not trusted with numbers.

### Underneath: the governance kernel
Spending mandates as macaroons that can only narrow, with an SMT solver proving narrowing at each
delegation hop; revocation as a freshness caveat giving O(1) blast-radius containment; a working
prompt-injection defence; liability routing; and an evidence package.

---

## 7. Verified system numbers

**Three of these carts are different carts. Do not merge them.** The `$1,078` basket in §4 is
the agent demo; the `$2,681` amextravel.com cart is the overstatement beat; the `4×6×30` console
cart is an INR travel cart. Each row below names its own.

| Claim | Measured, and on what |
|---|---|
| Tests passing | **1,679** (0 failing, 0 skipped) — `backend/tests` + `agent/tests` |
| Rendered console assertions | **315** — `npm run smoke --prefix frontend` |
| Lines of code | **~57,300** — Python + TypeScript + CSS across `backend/`, `agent/`, `scripts/`, `frontend/src`, `frontend/scripts` (57,257 exactly; excludes `node_modules`, build output, JSON fixtures) |
| Naive summation overstates | median **37%**, p90 **91%**, max **100%** — 310 random carts, 183 of them overstate |
| Overstatement beat, $2,681 amextravel.com cart | naive **$2,092.01 → witness $773.77** ($1,318.24 avoided) |
| Allocator vs exact optimum | **295/303 random carts exactly optimal (97.4%)**, mean gap **0.411%**, **worst 45.9%** — max-flow oracle, small instances, `test_plumbline_core.py` |
| Allocator vs exact optimum, larger | worst gap **10.36%**, median **0.00%**, p90 **0.72%** — 150 Z3-resolved instances at **12 lines × 20 benefits**, `artifacts/plumbline_bench.json` |
| At 8×20×40 | the oracle resolves only **6 of 12** instances at a 30s timeout. The honest sentence is **"the gap we could measure there is at most 0.92%"** — not a hit rate |
| Allocator latency, console cart (4×6×30) | **p50 ≈ 0.08ms, p99 ≈ 0.09ms** — machine-bound, re-measured live on every console build, so read it off the screen rather than the slide. Say "well under a tenth of a millisecond"; do not print a third decimal place for a number that moves between runs |
| Verification latency, console cart | **p50 0.048ms**, no solver |
| Allocator latency at 8×20×40 | **p50 1.93ms, p99 2.12ms** — `artifacts/plumbline_bench.json`, 300 reps, seed 20260825, this host. **This is the figure `make headline` prints; quote no other.** |
| Solver alternative at 8×20×40 | **451–2695ms, 6× variance**, and on timeout it can report a lower bound **above** its upper bound. **CITED, NOT MEASURED HERE** — the adversarial panel's benchmark of the MaxSMT formulation; the code records it as `measured_here: False`. **Prefer our own measurement:** the offline Z3 oracle at that size runs **p50 13.6s / p99 14.4s** (`artifacts/plumbline_bench.json`) and returns a non-numeral bound on **6 of the 12** gap instances even at a 30s timeout |
| Determinism fingerprint | `d0e26c4735591a8b…` — identical across two runs |

**The three gap rows are three different measurements** — different generators, different sizes,
different solvers. They are not interchangeable and none of them may be spoken without its
conditions. **Never quote a hit rate in place of a gap.**

**The claim that actually carries the argument is the spread, not the speed:** p99 is **1.10× p50**
against the solver's **6×** at constant problem size. Predictability is what a checkout path needs.

**Never say "microseconds" for the headline size. Say "sub-millisecond for a single instrument,
about 2ms at 8×20×40."**

### The attribution finding — the CFO instrument
From a corpus of 180 receipts, per benefit: delete it from the manifest, re-run the allocator, see
whether the choice changes.

| Benefit | Card | Verdict | Modelled annual cost *(an input, not a measurement)* |
|---|---|---|---|
| 5 MR points per ₹100 on fuel | Amex Platinum Charge (India) | **LOAD-BEARING** | ₹15,000 |
| 3X MR on spend abroad | Amex Platinum Charge (India) | OPTION | ₹3,750 |
| **1 MR point per ₹40 (base earn)** | Amex Platinum Charge (India) | **DEAD WEIGHT** | **₹12,500** |
| 1 MR point per ₹50 (base earn) | Amex Platinum Travel (India) | NOISE | ₹2,500 |

*(Name the card on the slide. "DEAD WEIGHT" against an unnamed benefit reads as a swipe; against a
named base earn rate, with the caveat below, it reads as the analysis it is.)*

**Mandatory caveat, must appear on the slide:** this measures **selection influence at the moment
of choice**. It does **not** measure retention, incremental spend, or renewal. A benefit that never
appears in a winning derivation may still be why the card was taken out.

---

## 8. The six demo beats

1. **The overstatement.** Naive $2,092.01 beside the witness-backed $773.77, with the struck-through
   credit that could not legally apply and the exclusivity group named.
2. **The refusal.** Two assertions declined on the same cart:
   `PLUMBLINE_REFUSE_CLAIM_UNSUPPORTED_BY_WITNESS` and `WITNESS_CAPACITY_EXCEEDED`. **A system that
   visibly declines to make a claim is the strongest possible answer to "punish vaporware."**
3. **Omission.** An edited receipt names 2 candidates where the mandate authorised 3. Its own
   attestation reports `CANDIDATE_SET_INCOMPLETE`; the log audit reports `SPLIT_VIEW_DETECTED`.
4. **Graceful degrade.** Four passes: **3 proceed, 1 deny.** The only denial is the one the Card
   Member *elected*, and it denies **against their own mandate, not against the platform.**
5. **The attribution close.** The corpus of 180 receipts ranks benefits by how often they appear in
   a winning derivation. Which to **cut**. *This beat is the answer to §5 and must not be dropped
   for time — it is the whole reason the pitch is not a benefit-usage product.*
6. **Hand the judge the controls.** Perturb a manifest live; the ranking, the witness and the
   fingerprint all move.

---

## 9. FORBIDDEN CLAIMS — do not put any of these in the deck

Each is a sentence that hands the room a rebuttal.

- ❌ **"AI agents choose cards today."** FALSE. Every shipped protocol pins a human-chosen
  instrument. **Say this yourself, first, before anyone corrects you** — it buys the rest of the
  argument.
- ❌ **"The window is closing."** Not 72 hours after the CEO said "preseason." Say instead: **the
  transaction clock and the standards clock are different clocks.** UCP's Loyalty Extension shipped
  in January; ACP's Discount Extension is still the only formally defined extension — both during
  the preseason. Schemas set in a quiet market are the ones nobody reopens when it gets loud. And:
  **you cannot backfill a disclosure record for a choice already made.**
- ❌ **"Today you could not detect that."** → **"No industry mechanism exists to record it."** AmEx
  wrote the risk factor; never tell the authors they are blind to their own filing.
- ❌ **"AmEx has a churn problem."** Management has denied it for four straight quarters with data.
  Le Caillec: retention *"not only through the roof, they were flat year-over-year."* Our claim is
  about the **cost** of retention, not the fact of it.
- ❌ **Implying the 50% growth is a run-rate.** It laps a pre-refresh base and will decay from
  Q4 2026. It is a step change in **level**: $6.06B FY2025 → ~$7.8B annualised.
- ❌ **Any claim that AmEx forgot, missed, or is blind to something in its own filings.** Le Caillec
  pre-empted it: *"with our eyes wide open."* They wrote the risk factor. The gap is in the
  *industry's* record-keeping, never in their awareness.
- ❌ **The "4,200 questions / 62% affiliate / sub-6% issuer" statistic, and anything shaped like
  it.** Untraceable — and TPG, NerdWallet and Bankrate are AmEx's *paid* acquisition channel, so
  the line reads as "you called your own marketing channel a threat." The gap is provable from the
  specifications alone (§3). Do not reach for a consumer-behaviour statistic to prop it up.
- ❌ **Any unsourced or unlabelled figure.** In a pitch whose thesis is *"we make agents show their
  work,"* one unsourced number is a thematic self-refutation as well as a factual one. State the
  period on **every** number spoken aloud, and mark derived numbers as derived — the ~$7.8B
  annualised run-rate especially.
- ❌ **Quoting the ~5ms kernel entailment figure anywhere near valuation latency.** Different
  components, different budgets. Putting them on one slide invites a comparison that means nothing
  and looks like padding.
- ❌ **Claiming Ed25519 or asymmetric crypto.** It is HMAC-SHA256 under a prototype key. There is
  **no asymmetric crypto anywhere in this repo** — no signatures a third party can verify without
  the shared secret, no key ceremony, no HSM. Say "HMAC under a prototype key" out loud. Also do
  not say the transparency log "proves" anything cryptographically stronger than that: inclusion
  and consistency proofs make edits **detectable to a verifier holding the log**, which is the real
  and sufficient claim.
- ❌ **Any acceptance predicate** — an issuer-signed field naming where the card is refused. We
  built it, then deleted it. **Say the deletion out loud as evidence of judgement.**

---

## 10. Prepared answers to every likely objection

**"You shipped a benefit-usage product. That inflates our worst line."**
→ See §5. We assert value the member need not redeem, and never nudge a member toward an unused
credit. The receipt corpus is an attribution instrument for deciding what to **cut**.

**"So no receipt means our card declines? You have just made Amex the only credential in a
third-party checkout that hard-fails."** *(This is the second-most dangerous question in the room.
Have the answer cold.)*
→ **No. The enforcement point is not on the platform, and the platform is never asked for
anything.** The receipt obligation is a caveat on the mandate **the Card Member issues to their own
agent**. An agent that cannot produce a receipt fails to discharge **the cardholder's own delegated
authority** — architecturally identical to a spend limit denying, which is exactly what PS#5 asks
for. Amex imposes no condition on anyone; the platform never sees the check.

→ **The default posture is observe-only.** The evaluator computes and signs, the receipt is
emitted, and the absence of a counterpart receipt is logged as an **unattested selection** — never
a decline. Enforcement is a mode the Card Member elects. Demo beat 4 shows both paths: four passes,
three proceed, one denies, and the one that denies is the one the member turned on.

→ **Then the carrot, on a surface Amex already shipped:** Amex Agent Purchase Protection (announced
14 April 2026) covers purchases made by registered agents. **No receipt, no coverage.** Coverage is
conditioned on evidence; authorization never is. That is a benefit a platform wants to display, not
a credential it routes around.

→ Say why this matters unprompted: a hard-failing credential is precisely the 10-K risk factor,
self-administered. A platform facing a credential it cannot discharge does not comply — it routes
around. We designed the enforcement point so that can never be the outcome.

**"We shipped ACE in April. This is our roadmap."**
→ Your roadmap is **distribution** — getting Resy, Offers and Travel into third-party AI surfaces.
This is **disclosure** — making the choice among instruments produce a signed, contestable record.
Cede the manifest; claim the receipt. Distribution without a signed value contract is AmEx's data
narrated by someone else's response object.

**"We already ship benefit trackers in the app."** *(Shipped 18 September 2025 — know the date.)*
→ Same underlying state, **opposite consumer**. A tracker renders remaining balances **to a human
who then decides**. The manifest exposes the same state **to a machine that is about to choose**,
and produces a signed record of that choice. One is a display surface; the other is a decision
input plus an audit artifact. Neither replaces the other.

**"You want us to fund the machine-readable commoditisation of the thing we charge $895 for."**
→ They have a third option they litigated to the Supreme Court to protect: keep value illegible,
keep the counter closed. The counter: **illegibility worked when a human read marketing copy. To a
ranking machine, illegible equals absent, and absent is not a premium.** Then narrow the signature
scope — AmEx signs facts, never comparisons.

**"Our CEO said we're in the preseason. You say the window is closing."**
→ Two clocks. See §9.

**"Is that a real MCP server or a function call wearing the name?"**
→ Re-run with `--transport stdio`. It spawns the server as a real subprocess, does the full
protocol handshake, and returns identical rankings.

**"How do I know your number is right?"**
→ You don't have to trust it. The witness is the working. Re-add the numbers and check the
capacities — verification is linear-time and needs no solver. 0.048ms on the cart on screen.

**"What is this worth if no platform ever ships your receipt?"**
→ Still useful on day one, unilaterally. The corpus generated from Amex's own agent surfaces plus
consented cardholder-side instrumentation already tells you where benefit dollars influence
instrument choice. That is a spend-allocation decision **entirely inside Amex's four walls** — no
partner, no lawyer, no standards body. Adoption improves the corpus; it is not a precondition for
it.

**"Why would a platform ever want to carry this?"**
→ Because the receipt certifies **compliance as readily as violation**. A record that can only
catch cheating is something a platform routes around. A record that can prove *"this platform
ranked instruments faithfully"* is something a platform wants to display. `RANKING_FAITHFUL` is a
real verdict the engine emits, not a courtesy.

**"Why is AmEx ranked third on your India cart?"** *(This is real — be ready.)*
→ Third of four, on the console cart. HDFC Infinia earns **3.33%** (5 Reward Points per ₹150, at
its published 1 RP = ₹1 SmartBuy redemption); the Amex Platinum Charge (India) earns **0.62%**
(1 MR per ₹40, at the published ₹0.25 Pay-with-Points floor). We refused to fix it by picking a
friendlier redemption value or by trimming the candidate set. **The ₹66,000 card's priced
arithmetic is 0.62%, and its eight declared-but-unpriced entries — lounges, Taj Epicure Plus,
partner hotel rates, elite status, insurance, Amex Offers and the rest — are why the card is still
held. That is exactly what CONSIDERED_BUT_UNPRICED exists to say.**

---

## 11. Honest limitations — put these on a slide, do not bury them

The demo script prints these itself under the heading *"say these out loud; hiding one refutes the
thesis."*

- Signatures are **HMAC-SHA256 under prototype keys**. There is no asymmetric crypto in this
  build. Production signs with the issuer's key in an HSM; canonicalisation and verification flow
  are unchanged.
- Manifests model **publicly published card terms**. No live Offers feed is claimed.
- Remaining balances are **synthetic member state**, labelled on every manifest.
- The greedy allocator is **conservative by construction and not optimal**. We quote the measured
  gap, and there are **three separate gap measurements** (§7) that are not interchangeable —
  **45.9% worst** over 303 small instances against a max-flow oracle, **10.36% worst** over 150
  Z3-resolved instances at 12 lines × 20 benefits, and **at most 0.92%** on the subset we could
  resolve at the headline size. Different generators, different sizes, different solvers.
- **At the headline 8×20×40 size the oracle resolves only 6 of 12 instances even at a 30s
  timeout** (the rest return `ORACLE_NON_NUMERAL_BOUND`), and greedy is exactly optimal on 1 of
  those 6. The honest sentence is *"the gap we could measure there is at most 0.92%"* — **never a
  hit rate**, because the hit rate is computed over the instances the oracle happened to solve.
- The MCP server and the whole valuation path are **exercised against modelled carts and modelled
  manifests**. Nothing here has met a live merchant, a live agent, or a real Amex system.
- Cumulative-budget and velocity constraints are **stateful and not offline-verifiable**. Only
  structural constraints verify from the credential alone.
- All payment rails, merchants and authorization responses are **mocked**.
- The trust model is **demonstrated, not deployed** — nobody countersigns these manifests.
- The soundness claim has an exact width: **not** "never exceeds what the card can deliver," but
  "never exceeds the best value obtainable **under the constraints the manifest declares**."

---

## 12. Suggested slide structure (12–14 slides)

1. **Title.** PLUMBLINE — *the layer where value is declared, proved, and contestable.*
2. **The number.** $1,949M / +50% against +15%. Beat EPS, raised revenue guidance, held EPS
   guidance, stock −6%. One slide, their figures, **no commentary and no causal arrow** — see the
   warning in §2 about not attributing the share-price move to benefits expense.
3. **The frame.** "Every benefit dollar is expensed on use, and you have no record of which caused
   a card to be chosen." *(This must come before any architecture.)*
4. **What is not true.** Agents don't choose cards today. Preseason. Two clocks.
5. **The gap.** The protocol table from §3. One formal extension, and it's the merchant's discount.
6. **The proof.** Guessing vs deriving. $150 claimed, $25.78 real, $41.40 lost, $1,900 gate failure.
7. **Why Platinum lost.** The BLOCKED lines from §4 — Platinum's own benefit labels, verbatim, with
   their reason codes. *Marketing describes a card in general; a purchase happens specifically.*
8. **The mechanism.** Conservatism by construction. Witness = derivation. Verify without trust.
9. **The numbers.** Overstatement distribution, latency spread, optimality gap — **each with the
   conditions from §7 visible on the slide, and only one gap measurement per slide.**
10. **The receipt.** Full candidate set, signing boundary, omission detectable. **Put the
    enforcement point on this slide:** the receipt obligation is a caveat on the mandate the Card
    Member issues to their own agent — default observe-only, never a decline Amex imposes on a
    platform. *(This pre-empts the second-most dangerous question in the room; see §10.)*
11. **Attribution.** The 2×2, with the selection-influence caveat visible.
12. **Limitations.** §11, verbatim.
13. **Close.** Their 10-K sentence on screen: *"suppress use of, or degrade the experience of using
    our products."* Then: *you filed this risk. **No industry mechanism exists to record it.** This
    is one.* **Use exactly that phrasing.** Do not say "you cannot detect this today" or "this
    makes it observable for the first time" — both tell the authors of the filing that they are
    blind to their own filing, which §9 forbids. The gap is in the industry's record, not in their
    sight.

---

## 13. Design direction

- **Dark, high contrast, dense but calm.** This is an instrument panel, not a marketing site.
- **Typography:** a distinctive pairing — the console uses **Archivo** (display) with **JetBrains
  Mono** (data). Do **not** use Inter, Roboto, or a default system stack; they read as
  AI-generated.
- **Monospace for** hashes, reason codes, derivations, and anything a judge might verify.
- **Money right-aligned**, always with its period label.
- **Palette:** restrained, with **one** strong accent reserved for refusals and denials.
- **No purple gradients on dark.** No stock photography. No icon soup.
- **Every figure carries its source** in small type: "AmEx Q2 2026 earnings presentation, 24 July
  2026" / "measured, this host" / "derived — ours, not an AmEx figure."
- Numbers should feel *measured*, not *claimed*. Where something is uncertain, show the range.
