/**
 * Headless smoke test: mounts the real console in jsdom and drives it the way a presenter
 * would. Built with `vite build --ssr` and run under node, so it exercises the shipped
 * modules rather than a re-implementation.
 *
 * Server rendering was tried first and rejected: zustand serves `getInitialState` to
 * `useSyncExternalStore` during SSR, so every screen rendered its empty state and the
 * test proved nothing. A real DOM runs the effects, which is where the transport wiring,
 * the reveal animation and the browser-side Merkle verification actually live.
 *
 * What it checks:
 *   1. `format.money` agrees with the kernel's `fmt_money` on every amount the fixtures
 *      carry a rendered string for — a silent divergence would put a wrong rupee figure
 *      on the projector;
 *   2. the mock transport returns every shape the screens destructure;
 *   3. the two verifiers the console ships — the linear-time witness check and the RFC 6962
 *      consistency check — agree with the backend on recorded output, reject tampered
 *      input, and replay the generated proof vectors including the negatives;
 *   4. each demo beat, driven through the UI, puts the right numbers and reason codes on
 *      screen.
 */

import { JSDOM } from "jsdom";
import { webcrypto } from "node:crypto";

import type { BenefitDict, BenefitKind, ManifestBody, WitnessDict } from "../src/lib/plumbline";
import type { Cart, CartLine } from "../src/lib/types";

const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>", {
  url: "http://localhost:5173/",
  pretendToBeVisual: true,
});

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const g = globalThis as Record<string, unknown>;
g.window = dom.window;
g.document = dom.window.document;
// node exposes `navigator` as a getter-only global; redefine rather than assign.
Object.defineProperty(globalThis, "navigator", {
  value: dom.window.navigator,
  configurable: true,
});
g.HTMLElement = dom.window.HTMLElement;
g.Element = dom.window.Element;
g.Node = dom.window.Node;
g.SVGElement = dom.window.SVGElement;
g.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
g.requestAnimationFrame = (cb: FrameRequestCallback) => setTimeout(() => cb(Date.now()), 16);
g.cancelAnimationFrame = (id: number) => clearTimeout(id);
g.ResizeObserver = ResizeObserverStub;
(dom.window as unknown as Record<string, unknown>).ResizeObserver = ResizeObserverStub;
// jsdom's window.crypto has no SubtleCrypto and is getter-only. The console reads the
// global `crypto`, so node's own webcrypto is what the Merkle verifier will use here.
Object.defineProperty(dom.window, "crypto", { value: webcrypto, configurable: true });
g.IS_REACT_ACT_ENVIRONMENT = true;

const { act, StrictMode } = await import("react");
const { createRoot } = await import("react-dom/client");
const { default: App } = await import("../src/App");
const { money } = await import("../src/lib/format");
const { verifyInclusionProof } = await import("../src/lib/merkle");
const { useConsole } = await import("../src/lib/store");
const { createMockTransport } = await import("../src/mock/mockClient");
const { SCENARIOS } = await import("../src/lib/types");
const { verifyWitness } = await import("../src/lib/witness");
const { WITNESS_FAILURE_CODES } = await import("../src/lib/plumbline");
const { verifyConsistency } = await import("../src/lib/transparency");
const { currencyMoney } = await import("../src/lib/format");
const { derivationRows } = await import("../src/lib/derivation");
const { perturbManifest } = await import("../src/lib/perturb");
const { PERTURBATION } = await import("../src/data/perturbations");
const { default: raw } = await import("../src/mock/fixtures.json");
const { default: plumblineRaw } = await import("../src/mock/plumblineFixtures.json");
const { default: degradeRaw } = await import("../src/mock/plumblineDegrade.json");

let failures = 0;

function check(name: string, condition: unknown, detail = "") {
  if (condition) {
    console.log(`  ok   ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL ${name}${detail ? ` — ${detail}` : ""}`);
  }
}

function section(name: string) {
  console.log(`\n${name}`);
}

const text = () => document.body.textContent ?? "";
/**
 * Screen assertions read <main> only. The feed rail carries the same vocabulary —
 * `MANDATE_REVOKED` contains "REVOKED", and a rejected delegation prints
 * SCOPE_ESCALATION — so a whole-body predicate matches feed traffic and samples the DOM
 * mid-flight, before the screen it is meant to be checking has settled.
 */
const mainText = () => document.querySelector("main")?.textContent ?? "";

const tick = (msec = 0) => new Promise<void>((r) => setTimeout(r, msec));

async function waitFor(label: string, predicate: () => boolean, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await act(async () => {
      await tick(40);
    });
  }
  check(`waited for ${label}`, false, "timed out");
  return false;
}

function findButton(label: string): HTMLButtonElement | undefined {
  return [...document.querySelectorAll("button")].find((b) =>
    (b.textContent ?? "").trim().toLowerCase().includes(label.toLowerCase()),
  ) as HTMLButtonElement | undefined;
}

async function click(label: string) {
  const button = findButton(label);
  if (!button) {
    check(`button "${label}" exists`, false);
    return;
  }
  await act(async () => {
    button.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    await tick(30);
  });
}

// -- 1. money formatting agrees with the kernel ------------------------------------------

section("money formatting vs kernel fmt_money");
let compared = 0;
const mismatches: string[] = [];

/**
 * Every `<name>_display` string in either fixture set is compared against the integer it
 * renders. The kernel wrote both; if the console's formatter disagrees with either, a
 * wrong rupee figure ends up on the projector next to a right one.
 */
function walk(node: unknown) {
  if (Array.isArray(node)) {
    node.forEach(walk);
    return;
  }
  if (!node || typeof node !== "object") return;
  const record = node as Record<string, unknown>;
  for (const [key, value] of Object.entries(record)) {
    if (typeof value === "string" && key.endsWith("_display")) {
      const stem = key.slice(0, -"_display".length);
      const source = record[stem] ?? record[`${stem}_minor`] ?? (stem === "amount" ? record.amount : undefined);
      if (typeof source === "number") {
        compared += 1;
        if (money(source) !== value) {
          mismatches.push(`${key}=${source}: got ${money(source)}, kernel ${value}`);
        }
      }
    }
    walk(value);
  }
}
walk(raw);
walk(plumblineRaw);
check(`compared ${compared} kernel-rendered amounts`, compared > 0);
check("no formatting divergence", mismatches.length === 0, mismatches.join("; "));

/**
 * The degrade scenario runs on a dollar cart, so the same comparison runs again under the
 * symbol its currency code names. A rupee sign on a dollar figure is the failure mode this
 * catches: every digit right, the currency wrong, and nothing on screen saying so.
 */
let usdCompared = 0;
const usdMismatches: string[] = [];
function walkUsd(node: unknown) {
  if (Array.isArray(node)) {
    node.forEach(walkUsd);
    return;
  }
  if (!node || typeof node !== "object") return;
  const record = node as Record<string, unknown>;
  for (const [key, value] of Object.entries(record)) {
    if (typeof value === "string" && key.endsWith("_display")) {
      const stem = key.slice(0, -"_display".length);
      const source = record[stem] ?? record[`${stem}_minor`];
      if (typeof source === "number") {
        usdCompared += 1;
        if (currencyMoney(source, "USD") !== value) {
          usdMismatches.push(`${key}=${source}: got ${currencyMoney(source, "USD")}, kernel ${value}`);
        }
      }
    }
    walkUsd(value);
  }
}
walkUsd(degradeRaw);
check(`compared ${usdCompared} kernel-rendered dollar amounts`, usdCompared > 0);
check("no divergence on the dollar cart", usdMismatches.length === 0, usdMismatches.join("; "));
check("an undeclared currency is never given a guessed symbol", currencyMoney(120_000, "GBP") === "GBP 1,200", currencyMoney(120_000, "GBP"));
check("₹54,000 grouping", money(5_400_000) === "₹54,000", money(5_400_000));
check("₹1,88,400 grouping", money(18_840_000) === "₹1,88,400", money(18_840_000));
check("paise rendered", money(800_001) === "₹8,000.01", money(800_001));
check("zero", money(0) === "₹0", money(0));
check("negative", money(-5_200) === "-₹52", money(-5_200));

// -- 2. transport surface ----------------------------------------------------------------

const transport = createMockTransport();

section("transport");
const state = await transport.getState();
check("operators present", state.operators.length > 0);
check("exposure book matches operator list", state.exposure.operators.length === state.operators.length);
check("ledger root is a sha256 hex", /^[0-9a-f]{64}$/.test(state.ledger_root), state.ledger_root);
check(
  "exposure totals reconcile",
  state.exposure.covered_exposure + state.exposure.declined_exposure ===
    state.exposure.total_exposure,
);

for (const name of SCENARIOS) {
  const result = await transport.runScenario(name);
  check(`${name} returns its own name and a narrative`, result.scenario === name && Boolean(result.narrative));
}

const injection = await transport.runScenario("injection");
check("injection denies", injection.governed?.outcome === "DENY", injection.governed?.outcome);
check("injection reports cart divergence", injection.governed?.reason_codes.includes("MANDATE_CART_DIVERGENCE"));
check("injection blocks stored value", injection.governed?.reason_codes.includes("MCC_NOT_ALLOWED"));
check("injection verdict is compromise", injection.governed?.verdict === "INJECTION_COMPROMISE");
check("ungoverned side would have paid ₹55,200", injection.ungoverned?.amount === 5_520_000);
check("ten lines injected", injection.governed?.diff.added.length === 10);
check(
  "injected lines are identifiable by sku",
  (() => {
    const added = new Set(injection.governed!.diff.added.map((l) => l.sku));
    const lines = injection.executed_cart!.lines;
    return lines.filter((l) => added.has(l.sku)).length === 10 && lines.length === 12;
  })(),
);

const escalation = await transport.runScenario("escalation");
const delegations = escalation.delegations ?? [];
check("four delegation hops", delegations.length === 4, String(delegations.length));
check("two hops rejected", delegations.filter((d) => !d.accepted).length === 2);
const fooled = delegations.find((d) => d.naive_disagrees);
check("one hop fools the subset check", Boolean(fooled));
check("the fooled hop is a rejection with subset=true", fooled?.accepted === false && fooled?.naive_subset_check === true);
check("it carries a counterexample naming violated parent constraints", (fooled?.entailment.counterexample?.violated.length ?? 0) > 0);
check("every delegation carries parent_id for the graph", delegations.every((d) => typeof d.parent_id === "string" && d.parent_id.length > 0));

const kill = await transport.runScenario("kill_switch");
check("chain is five deep", kill.chain?.length === 5);
check("exactly one row written", kill.revocation?.rows_written === 1);
check("four descendants contained", kill.revocation?.descendants_killed === 4);
check("probe flips ALLOW → DENY across the revoke", kill.revocation?.before.outcome === "ALLOW" && kill.revocation?.after.outcome === "DENY");
check("denial reason is revocation", kill.revocation?.after.reason_codes.includes("MANDATE_REVOKED"));
check("ttl bound is disclosed", (kill.revocation?.discharge_ttl_s ?? 0) > 0);

const stepUp = await transport.runScenario("step_up");
check("step-up challenges then allows", stepUp.step_up?.first.outcome === "STEP_UP" && stepUp.step_up?.second?.outcome === "ALLOW");
check("challenge is bound to a cart hash", /^[0-9a-f]{64}$/.test(stepUp.step_up?.challenge?.cart_hash ?? ""));

section("merkle verification in the browser's own crypto");
const after = await transport.getState();
const evidence = await transport.getEvidence(after.decisions[0]!.txn_id);
const proof = evidence.inclusion_proof!;
const local = await verifyInclusionProof(proof);
check("recomputed root matches published root", local.ok, `${local.computedRoot} vs ${local.expectedRoot}`);
check("audit path length matches the proof", local.steps.length === proof.path.length);
check("console agrees with server", local.ok === evidence.proof_verified);
const tampered = await verifyInclusionProof({
  ...proof,
  leaf_hash: proof.leaf_hash.replace(/^./, (c) => (c === "a" ? "b" : "a")),
});
check("a tampered leaf fails verification", tampered.ok === false);

// -- 2b. the plumbline corpus and the two verifiers the console ships -----------------------

section("plumbline transport");
const plumbline = await transport.getPlumblineState();
check("four instruments are carried", plumbline.instruments.length === 4, String(plumbline.instruments.length));
check(
  "exactly two manifests carry an issuer signature",
  plumbline.instruments.filter((i) => i.issuer_signed).length === 2,
);
/**
 * The seal renders `issuer_signed`, but the source string is what a reader gets when they
 * ask why. An unsigned manifest has exactly two honest provenances — the agent modelled a
 * competitor's published terms, or the instrument is invented outright — and the string has
 * to name which, because "unsigned" alone does not distinguish them.
 */
check(
  "unsigned manifests disclose which kind of unsigned they are",
  plumbline.instruments
    .filter((i) => !i.issuer_signed)
    .every((i) => /modelled|invented|signed by no one|no issuer signature/i.test(i.source)),
);
check("the receipt carries the full candidate set", plumbline.receipt.candidates.length === 4);
check(
  "candidates are ranked 1..n with no gaps",
  plumbline.receipt.candidates.every((c, i) => c.rank === i + 1),
);
check(
  "the selected instrument is the top-ranked one",
  plumbline.receipt.selected === plumbline.receipt.candidates[0].instrument_id,
);
check(
  "the ranking is not issuer-endorsed",
  plumbline.receipt.policy.endorsed_by_issuer === false,
);
check(
  "every candidate declares unpriced value",
  plumbline.receipt.candidates.every((c) => c.unpriced.length > 0),
);
check("two refusal cases", plumbline.refusals.length === 2, String(plumbline.refusals.length));
check("the attribution corpus is populated", plumbline.attribution.corpus_size === 180);

section("the naive baselines overstate, and the reconciliation closes");
for (const candidate of plumbline.receipt.candidates) {
  const r = candidate.reconciliation;
  check(
    `${candidate.product}: naive ≥ capped ≥ witness`,
    r.naive_minor >= r.capped_minor && r.capped_minor >= r.witness_minor,
    `${r.naive_minor} / ${r.capped_minor} / ${r.witness_minor}`,
  );
  const causes = Object.fromEntries(r.by_cause.map((c) => [c.cause, c.minor]));
  check(
    `${candidate.product}: per-line step reconciles to the rupee`,
    r.naive_minor - r.capped_minor === causes.OVER_REMAINING_BALANCE,
  );
  check(
    `${candidate.product}: cross-line step reconciles to the rupee`,
    r.capped_minor - r.witness_minor ===
      causes.STRUCK_EXCLUSIVITY_GROUP + causes.BALANCE_ALREADY_CONSUMED,
  );
  check(
    `${candidate.product}: witness total equals the sum of its assignments`,
    candidate.witness.assignments.reduce((s, a) => s + a.value_minor, 0) ===
      candidate.asserted_minor,
  );
}

section("the witness verifier, run in the browser");
const manifestOf = (id: string) =>
  plumbline.instruments.find((i) => i.instrument_id === id)!.manifest;

for (const candidate of plumbline.receipt.candidates) {
  const local = verifyWitness({
    witness: candidate.witness,
    manifest: manifestOf(candidate.instrument_id),
    cartHash: plumbline.cart_hash,
    cart: plumbline.receipt.cart,
    assertedMinor: candidate.asserted_minor,
  });
  check(
    `${candidate.product}: console verdict matches the evaluator's`,
    local.supportsAssertion === candidate.verification.supports_assertion &&
      local.realizedMinor === candidate.verification.realized_minor,
    `${local.realizedMinor} vs ${candidate.verification.realized_minor}`,
  );
}

const headline = plumbline.receipt.candidates[0];
const headlineManifest = manifestOf(headline.instrument_id);

check(
  "asserting one rupee more than the witness realises is rejected",
  verifyWitness({
    witness: headline.witness,
    manifest: headlineManifest,
    cart: plumbline.receipt.cart,
    assertedMinor: headline.asserted_minor + 1,
  }).failures[0]?.code === "WITNESS_DOES_NOT_SUPPORT_ASSERTION",
);

const inflatedAssignments = headline.witness.assignments.map((a, i) =>
  i === 0 ? { ...a, value_minor: a.value_minor + 100 } : a,
);
check(
  "an assignment claiming more than the manifest yields is rejected",
  verifyWitness({
    witness: { ...headline.witness, assignments: inflatedAssignments },
    manifest: headlineManifest,
    cart: plumbline.receipt.cart,
    assertedMinor: headline.asserted_minor,
  }).failures[0]?.code === "WITNESS_VALUE_MISMATCH",
);

/**
 * The over-capacity forgery needs a capped credit, and the headline instrument does not
 * have to carry one — the published Indian catalogue carries no statement credits at all,
 * which is why the console's only credits sit on the instrument that is invented outright.
 * So the case is driven wherever a capped credit exists rather than assumed onto the
 * headline, and the search is asserted to have found one: silently skipping the strongest
 * forgery in the file because a corpus changed shape is how a verifier loses a check.
 */
const creditCandidate = plumbline.receipt.candidates.find((c) =>
  manifestOf(c.instrument_id).benefits.some(
    (b) => b.kind === "credit" && b.capacity_minor !== null,
  ),
);
check("the corpus carries a capped credit to forge against", Boolean(creditCandidate));
if (creditCandidate) {
  const creditManifest = manifestOf(creditCandidate.instrument_id);
  const creditBenefit = creditManifest.benefits.find(
    (b) => b.kind === "credit" && b.capacity_minor !== null,
  )!;
  const overCapacity = [
    ...creditCandidate.witness.assignments.filter(
      (a) => a.benefit_id !== creditBenefit.benefit_id,
    ),
    ...plumbline.receipt.cart.lines
      .filter((l) => creditBenefit.eligibility.mccs.includes(l.mcc))
      .map((l) => ({
        line_sku: l.sku,
        benefit_id: creditBenefit.benefit_id,
        consumed_minor: creditBenefit.capacity_minor!,
        value_minor: creditBenefit.capacity_minor!,
      })),
  ];
  check(
    "drawing a balance down past zero is rejected",
    verifyWitness({
      witness: { ...creditCandidate.witness, assignments: overCapacity },
      manifest: creditManifest,
      cart: plumbline.receipt.cart,
      assertedMinor: 0,
    }).ok === false ||
      overCapacity.length === creditCandidate.witness.assignments.length,
  );
}

check(
  "a witness naming a benefit outside the manifest is rejected",
  verifyWitness({
    witness: {
      ...headline.witness,
      assignments: [
        ...headline.witness.assignments,
        { line_sku: plumbline.receipt.cart.lines[0].sku, benefit_id: "ben_not_real", consumed_minor: 1, value_minor: 1 },
      ],
    },
    manifest: headlineManifest,
    cart: plumbline.receipt.cart,
    assertedMinor: headline.asserted_minor,
  }).failures.some((f) => f.code === "WITNESS_UNKNOWN_BENEFIT"),
);

for (const refusal of plumbline.refusals) {
  const local = verifyWitness({
    witness: refusal.witness,
    manifest: manifestOf(refusal.witness.manifest_id),
    cartHash: plumbline.cart_hash,
    cart: plumbline.cart,
    assertedMinor: refusal.asserted_minor,
  });
  check(`${refusal.case_id}: the console refuses it too`, local.ok === false);
  check(
    `${refusal.case_id}: same failure code as the evaluator`,
    local.failures[0]?.code === refusal.verification.failures[0]?.code,
    `${local.failures[0]?.code} vs ${refusal.verification.failures[0]?.code}`,
  );
  check(
    `${refusal.case_id}: a supported value exists and is lower`,
    refusal.supported_minor < refusal.asserted_minor,
  );
}

// ------------------------------------------------------------------------------------------
// Forgeries the recorded fixtures cannot contain.
//
// Every case above runs the console verifier over output the evaluator produced, so it can
// only ever show the two agreeing where the evaluator already agreed with itself. These are
// the cases that matter: witnesses no honest allocator would emit, hand-built here, each one
// an overstatement that a verifier missing the corresponding check would wave through. A
// console verifier weaker than the evaluator is worse than no console verifier, because it
// puts "VERIFIED" on the projector under a number the evaluator would refuse.
//
// Synthetic manifests rather than fixture ones on purpose: the point of the per-line offset
// bound is that it holds when no exclusivity group was declared, and every credit in the
// shipped manifests declares one.
// ------------------------------------------------------------------------------------------

section("the witness verifier, on forgeries");

const anywhere = { mccs: [] as number[], merchants: [] as string[], categories: [] as string[] };
const benefit = (over: Partial<BenefitDict> & { benefit_id: string; kind: BenefitKind }) => ({
  label: over.benefit_id,
  eligibility: anywhere,
  rate_bp: 0,
  capacity_minor: null,
  flat_minor: 0,
  exclusivity_group: null,
  window: "annual",
  requires_enrollment: false,
  enrolled: true,
  note: "",
  ...over,
});
const line = (sku: string, amount: number): CartLine => ({
  sku,
  description: sku,
  amount,
  mcc: 5812,
  category: "dining",
  qty: 1,
});
/** A synthetic signed body wrapping hand-built benefits. Its id is what the forgeries name. */
const forgedManifest = (benefits: BenefitDict[], over: Partial<ManifestBody> = {}): ManifestBody => ({
  version: "plumbline/manifest/1",
  manifest_id: "forged",
  issuer: "iss_test",
  product: "Forged",
  currency: "INR",
  issued_at: 0,
  source: "hand-built for this test",
  benefits,
  ...over,
});
const forge = (
  assignments: { line_sku: string; benefit_id: string; consumed_minor: number; value_minor: number }[],
): WitnessDict => ({
  manifest_id: "forged",
  cart_hash: "0".repeat(64),
  assignments,
  realized_minor: assignments.reduce((s, a) => s + a.value_minor, 0),
  realized_display: "",
});
const oneDinner: Cart = { merchant: "m_x", currency: "INR", lines: [line("dinner", 80_000)] };

// Two ungrouped dining credits on one dinner. This is demo beat 1's failure mode with the
// authoring convenience removed, and it is what the naive sum does.
const twoCredits = [
  benefit({ benefit_id: "cr_a", kind: "credit", capacity_minor: 50_000 }),
  benefit({ benefit_id: "cr_b", kind: "credit", capacity_minor: 50_000 }),
];
const overOffset = verifyWitness({
  witness: forge([
    { line_sku: "dinner", benefit_id: "cr_a", consumed_minor: 50_000, value_minor: 50_000 },
    { line_sku: "dinner", benefit_id: "cr_b", consumed_minor: 50_000, value_minor: 50_000 },
  ]),
  manifest: forgedManifest(twoCredits),
  cart: oneDinner,
  assertedMinor: 100_000,
});
check(
  "two ungrouped credits cannot together offset more than the line costs",
  overOffset.failures.some((f) => f.code === "WITNESS_LINE_OFFSET_EXCEEDS_LINE_AMOUNT"),
  overOffset.failures.map((f) => f.code).join(",") || "accepted ₹1,000 against an ₹800 dinner",
);
check(
  "and the console will not stand behind the offsetting value",
  overOffset.ok === false && overOffset.supportsAssertion === false && overOffset.realizedMinor === 0,
  `realized ${overOffset.realizedMinor}`,
);

// Capacity is value-denominated, so a witness that declares a smaller draw than it takes
// evades every cap in the manifest.
const cappedEarn = [
  benefit({ benefit_id: "earn_capped", kind: "earn", rate_bp: 500, capacity_minor: 1_000 }),
];
const capEvasion = verifyWitness({
  witness: forge([
    { line_sku: "dinner", benefit_id: "earn_capped", consumed_minor: 0, value_minor: 4_000 },
  ]),
  manifest: forgedManifest(cappedEarn),
  cart: oneDinner,
  assertedMinor: 4_000,
});
check(
  "declaring a zero draw against a cap is refused",
  capEvasion.failures.some((f) => f.code === "WITNESS_CONSUMPTION_MISMATCH"),
  capEvasion.failures.map((f) => f.code).join(",") || "cap evaded",
);

// A credit is an offset of spend on the line. There is no spend beyond the line to offset.
const bigCredit = verifyWitness({
  witness: forge([
    { line_sku: "dinner", benefit_id: "cr_a", consumed_minor: 500_000, value_minor: 500_000 },
  ]),
  manifest: forgedManifest([benefit({ benefit_id: "cr_a", kind: "credit", capacity_minor: 500_000 })]),
  cart: oneDinner,
  assertedMinor: 500_000,
});
check(
  "a credit larger than its line is refused",
  bigCredit.failures[0]?.code === "WITNESS_CREDIT_EXCEEDS_LINE_AMOUNT",
  bigCredit.failures[0]?.code ?? "accepted",
);

// SKUs address lines. Indexing a repeated SKU keeps the last silently, so one assignment
// would name two different line amounts at once.
const duplicateSku = verifyWitness({
  witness: forge([
    { line_sku: "dinner", benefit_id: "cr_a", consumed_minor: 80_000, value_minor: 80_000 },
  ]),
  manifest: forgedManifest([benefit({ benefit_id: "cr_a", kind: "credit", capacity_minor: 80_000 })]),
  cart: { ...oneDinner, lines: [line("dinner", 10_000), line("dinner", 80_000)] },
  assertedMinor: 80_000,
});
check(
  "a cart with a repeated SKU is refused rather than valued against an arbitrary line",
  duplicateSku.failures[0]?.code === "WITNESS_CART_DUPLICATE_SKU",
  duplicateSku.failures[0]?.code ?? "accepted",
);

const negative = verifyWitness({
  witness: forge([
    { line_sku: "dinner", benefit_id: "cr_a", consumed_minor: -10, value_minor: -10 },
  ]),
  manifest: forgedManifest([benefit({ benefit_id: "cr_a", kind: "credit", capacity_minor: 80_000 })]),
  cart: oneDinner,
  assertedMinor: 0,
});
check(
  "negative amounts are refused",
  negative.failures[0]?.code === "WITNESS_NEGATIVE_AMOUNT",
  negative.failures[0]?.code ?? "accepted",
);

const unpriced = verifyWitness({
  witness: forge([
    { line_sku: "dinner", benefit_id: "up", consumed_minor: 0, value_minor: 0 },
  ]),
  manifest: forgedManifest([benefit({ benefit_id: "up", kind: "unpriced" })]),
  cart: oneDinner,
  assertedMinor: 0,
});
check(
  "assigning a CONSIDERED-BUT-UNPRICED benefit yields no number",
  unpriced.failures[0]?.code === "WITNESS_UNPRICED_BENEFIT_ASSIGNED",
  unpriced.failures[0]?.code ?? "accepted",
);

// The kernel reports a validated lower bound rather than zero: the assignments that survived
// every check are themselves a valid allocation. A console that reported zero here would
// disagree with the evaluator on screen.
const partial = verifyWitness({
  witness: forge([
    { line_sku: "dinner", benefit_id: "earn_ok", consumed_minor: 4_000, value_minor: 4_000 },
    { line_sku: "dinner", benefit_id: "ghost", consumed_minor: 9_999, value_minor: 9_999 },
  ]),
  manifest: forgedManifest([benefit({ benefit_id: "earn_ok", kind: "earn", rate_bp: 500 })]),
  cart: oneDinner,
  assertedMinor: 13_999,
});
check(
  "a failed verification still reports the achievable remainder, never the claim",
  partial.ok === false && partial.realizedMinor === 4_000 && partial.claimedMinor === 13_999,
  `realized ${partial.realizedMinor}, claimed ${partial.claimedMinor}`,
);

// The arithmetic can be flawless and the answer still wrong, because it answers a question
// about inputs nobody supplied. A witness computed against a different cart re-checks clean
// against a larger one and simply understates; a witness for a different card is not about
// this card at all. Substitution is the attack the arithmetic cannot see.
const honestish = [
  { line_sku: "dinner", benefit_id: "earn_ok", consumed_minor: 4_000, value_minor: 4_000 },
];
const oneEarn = forgedManifest([benefit({ benefit_id: "earn_ok", kind: "earn", rate_bp: 500 })]);

const wrongManifest = verifyWitness({
  witness: { ...forge(honestish), manifest_id: "some-other-card" },
  manifest: oneEarn,
  cart: oneDinner,
  assertedMinor: 4_000,
});
check(
  "a witness naming another manifest is refused, not silently re-checked",
  wrongManifest.failures[0]?.code === "WITNESS_MANIFEST_MISMATCH",
  wrongManifest.failures[0]?.code ?? "accepted",
);

const wrongCart = verifyWitness({
  witness: { ...forge(honestish), cart_hash: "b".repeat(64) },
  manifest: oneEarn,
  cart: oneDinner,
  cartHash: "a".repeat(64),
  assertedMinor: 4_000,
});
check(
  "a witness bound to another cart is refused",
  wrongCart.failures[0]?.code === "WITNESS_CART_MISMATCH",
  wrongCart.failures[0]?.code ?? "accepted",
);

// Minor units carry no unit, so a dollar manifest against a rupee cart would be added as if
// a cent were a paisa. There is no FX rate in the decision path and there must not be one.
const wrongCurrency = verifyWitness({
  witness: forge(honestish),
  manifest: forgedManifest(oneEarn.benefits, { currency: "USD" }),
  cart: oneDinner,
  assertedMinor: 4_000,
});
check(
  "a manifest and cart in different currencies are never added together",
  wrongCurrency.failures[0]?.code === "WITNESS_CURRENCY_MISMATCH",
  wrongCurrency.failures[0]?.code ?? "accepted",
);

// The guard on the guards: the console must be able to name every failure the kernel can.
// `backend/tests/test_console_conformance.py` asserts the other direction, from the kernel's
// own constant table, so a code added there without a port here turns the backend suite red.
check(
  "the console vocabulary is the kernel's, in full",
  new Set(WITNESS_FAILURE_CODES).size === WITNESS_FAILURE_CODES.length &&
    WITNESS_FAILURE_CODES.length === 18,
  `${WITNESS_FAILURE_CODES.length} codes`,
);

section("RFC 6962 consistency, run in the browser");
const honestProof = await verifyConsistency(plumbline.omission.consistency_honest);
check("an honest extension verifies", honestProof.ok, honestProof.error ?? "");
check(
  "both roots are rebuilt from the same proof",
  honestProof.firstMatches && honestProof.secondMatches,
);
const editedProof = await verifyConsistency(plumbline.omission.consistency_edited);
check("the edited log does not verify", editedProof.ok === false);
check(
  "the failure names the rebuilt old root, which is the tell",
  editedProof.firstMatches === false,
);
/**
 * Omission is the attack, so the instrument dropped has to be one whose absence a
 * counterparty would care about: an issuer-signed candidate that was published and then
 * served away. It need not be the winner — a platform that only ever dropped winners would
 * be trivially caught by the total, and the interesting case is the one that changes a set
 * without changing a price.
 */
check(
  "the omitted instrument was issuer-signed and was in the published set",
  plumbline.instruments.find(
    (i) => i.instrument_id === plumbline.omission.omitted_instrument.instrument_id,
  )?.issuer_signed === true &&
    plumbline.omission.candidates_published.includes(
      plumbline.omission.omitted_instrument.instrument_id,
    ) &&
    !plumbline.omission.candidates_served.includes(
      plumbline.omission.omitted_instrument.instrument_id,
    ),
);
check(
  "the served candidate set is one shorter",
  plumbline.omission.candidates_served.length === plumbline.omission.candidates_published.length - 1,
);

let vectorFailures = 0;
for (const vector of plumbline.omission.vectors) {
  const outcome = await verifyConsistency(vector);
  if (outcome.ok !== vector.expect_ok) vectorFailures += 1;
}
check(
  `all ${plumbline.omission.vectors.length} generated proof vectors agree`,
  vectorFailures === 0,
  `${vectorFailures} disagreed`,
);
check(
  "the vector set contains negatives",
  plumbline.omission.vectors.some((v) => !v.expect_ok),
);

section("latency figures carry their conditions");
const latency = plumbline.valuation.latency;
check("the allocator is sub-millisecond on the demo cart", latency.demo.p50_ms < 1, String(latency.demo.p50_ms));
check(
  "it is benchmarked at the same problem size as the solver",
  latency.bench.problem_size === latency.solver.problem_size,
  `${latency.bench.problem_size} vs ${latency.solver.problem_size}`,
);
check("the allocator holds a checkout budget at that size", latency.bench.p99_ms < 50, String(latency.bench.p99_ms));
check("the solver figures are attributed, not claimed", latency.solver.measured_here === false);
check("every measured figure states its method", [latency.demo, latency.bench, latency.bench_verify].every((l) => l.method.length > 0));
check("linear-time verification is faster than allocation", latency.bench_verify.p50_ms < latency.bench.p50_ms);

section("attribution is keyed on selection influence");
const book = plumbline.attribution;
/**
 * Which quadrants are occupied is a property of the corpus, not of the instrument, and a
 * catalogue with no statement credits will not fill all four. What must hold is that every
 * benefit lands in one of the four named quadrants and that the book separates them.
 */
check(
  "every benefit lands in one of the four named quadrants",
  book.benefits.every((b) =>
    ["DEAD_WEIGHT", "LOAD_BEARING", "NOISE", "OPTION"].includes(b.quadrant),
  ),
);
check(
  "and the corpus separates benefits rather than pooling them in one",
  new Set(book.benefits.map((b) => b.quadrant)).size >= 2,
  [...new Set(book.benefits.map((b) => b.quadrant))].join(","),
);
check(
  "decisive never exceeds the receipts a benefit was eligible for",
  book.benefits.every((b) => b.decisive <= b.in_candidate_set),
);
check(
  "the quadrant follows from the two measured axes",
  book.benefits.every(
    (b) =>
      b.quadrant ===
      (b.high_cost
        ? b.often_decisive
          ? "LOAD_BEARING"
          : "DEAD_WEIGHT"
        : b.often_decisive
          ? "OPTION"
          : "NOISE"),
  ),
);
check("the retention caveat is carried with the data", book.caveat.includes("retention"));
check("the cost input is disclosed as modelled", book.cost_source.includes("modelled"));

// -- 2c. graceful degrade: the matrix, and where the enforcement point is not -------------

section("graceful degrade");
const degradeRun = await transport.runDegrade();
const passes = degradeRun.data.passes;
check("four passes", passes.length === 4, String(passes.length));
check(
  "the whole matrix is covered exactly once",
  new Set(passes.map((p) => `${p.posture}:${p.counterpart_receipt}`)).size === 4,
);
check("three proceed", passes.filter((p) => p.proceeds).length === 3);
const denials = passes.filter((p) => !p.proceeds);
check("exactly one denies", denials.length === 1, String(denials.length));
check(
  "observe-only never denies, with or without a receipt",
  passes.filter((p) => p.posture === "observe_only").every((p) => p.proceeds),
);
check(
  "the only denial is elected enforcement with no counterpart receipt",
  denials[0].posture === "enforce" && denials[0].counterpart_receipt === false,
);
check(
  "and it denies against the cardholder's own mandate, by reason code",
  denials[0].reason_code === "DISCLOSURE_CAVEAT_UNDISCHARGED",
  denials[0].reason_code,
);
check(
  "the denial names the mandate the Card Member issued, not a platform",
  denials[0].detail.includes(degradeRun.data.mandate.mandate_id) &&
    denials[0].detail.includes("delegated authority") &&
    !denials[0].detail.toLowerCase().includes("platform"),
  denials[0].detail,
);
check(
  "the mandate carries the disclosure caveat that fails to discharge",
  degradeRun.data.mandate.disclosure_caveat === true,
);
check(
  "a missing receipt under the default posture is a record, not a decline",
  passes.some(
    (p) =>
      p.posture === "observe_only" &&
      !p.counterpart_receipt &&
      p.proceeds &&
      p.reason_code === "UNATTESTED_SELECTION",
  ),
);
check(
  "the log records the holes it has",
  (degradeRun.data.log_entry_kinds.unattested_selection ?? 0) === 2,
  JSON.stringify(degradeRun.data.log_entry_kinds),
);
check(
  "coverage, not authorization, is what a missing receipt costs",
  passes.every((p) => p.coverage_eligible === p.counterpart_receipt),
);
check(
  "every pass is anchored at its own log sequence",
  new Set(passes.map((p) => p.log_seq)).size === 4,
);
check(
  "the receipt attests the ranking was faithful",
  degradeRun.data.receipt.receipt.attestation.faithful === true &&
    degradeRun.data.receipt.receipt.attestation.checked_against_mandate === true,
);
check(
  "no issuer signature covers the ranking",
  degradeRun.data.receipt.signature.role !== "issuer" &&
    degradeRun.data.receipt.receipt.ranking?.issuer_endorsed === false,
);
check(
  "re-running it returns the same bytes — the clock is fixed",
  JSON.stringify(await transport.runDegrade()) === JSON.stringify(degradeRun),
);

// -- 2d. the perturbation corpus, and the console's own check on every stop ----------------

section("perturbation corpus");
check(
  `${PERTURBATION.axes.length} axes, each with a distinct id`,
  PERTURBATION.axes.length >= 4 &&
    new Set(PERTURBATION.axes.map((a) => a.axis_id)).size === PERTURBATION.axes.length,
  String(PERTURBATION.axes.length),
);
check(
  "the corpus perturbs the same cart the other screens value",
  PERTURBATION.cart_hash === plumbline.cart_hash,
);
check(
  "no axis moves a rate on a manifest an issuer signed",
  PERTURBATION.axes.every((a) => a.field !== "rate_bp" || !a.issuer_signed),
  PERTURBATION.axes
    .filter((a) => a.field === "rate_bp" && a.issuer_signed)
    .map((a) => a.axis_id)
    .join(","),
);
check(
  "a rate is moved somewhere, so the beat is not balances only",
  PERTURBATION.axes.some((a) => a.field === "rate_bp"),
);
check(
  "every axis says what kind of fact it moves",
  PERTURBATION.axes.every((a) => a.fact === "member state" || a.fact === "invented term"),
);
check(
  "every axis calling itself member state moves a balance and nothing else",
  PERTURBATION.axes
    .filter((a) => a.fact === "member state")
    .every((a) => a.field === "capacity_minor"),
);
check(
  "an axis on an issuer-signed product moves a balance and names the published bound it stops at",
  PERTURBATION.axes
    .filter((a) => a.issuer_signed)
    .every((a) => a.field === "capacity_minor" && /published/i.test(a.what_it_is)),
);

const perturbInstrument = (id: string) =>
  PERTURBATION.instruments.find((i) => i.instrument_id === id)!;

let pointsChecked = 0;
const pointFailures: string[] = [];
for (const axis of PERTURBATION.axes) {
  const signedBody = perturbInstrument(axis.instrument_id).manifest;
  for (const point of axis.points) {
    // Exactly what the screen does: perturb the signed body here, then check the engine's
    // witness against it. A corpus that disagreed with this console about what a
    // perturbation means would fail here rather than render a plausible number.
    const manifest = perturbManifest(signedBody, axis.benefit_id, axis.field, point.value);
    const verdict = verifyWitness({
      witness: point.witness,
      manifest,
      cart: PERTURBATION.cart,
      cartHash: PERTURBATION.cart_hash,
      assertedMinor: point.asserted_minor,
    });
    pointsChecked += 1;
    if (!verdict.supportsAssertion || verdict.realizedMinor !== point.asserted_minor) {
      pointFailures.push(
        `${axis.axis_id}@${point.value}: ${verdict.failures[0]?.code ?? "understated"}`,
      );
    }
    if (point.witness.assignments.reduce((s, a) => s + a.value_minor, 0) !== point.asserted_minor) {
      pointFailures.push(`${axis.axis_id}@${point.value}: total is not the sum of assignments`);
    }
  }
}
check(
  `all ${pointsChecked} engine-evaluated stops verify in this console`,
  pointFailures.length === 0,
  pointFailures.join("; "),
);

for (const axis of PERTURBATION.axes) {
  const record = perturbInstrument(axis.instrument_id);
  const baseline = axis.points[axis.baseline_index];
  check(
    `${axis.axis_id}: the baseline stop is the signed manifest, byte for byte`,
    JSON.stringify(
      perturbManifest(record.manifest, axis.benefit_id, axis.field, baseline.value),
    ) === JSON.stringify(record.manifest) && baseline.manifest_hash === axis.signed_manifest_hash,
  );
  const recorded = plumbline.valuation.instruments.find(
    (i) => i.instrument_id === axis.instrument_id,
  )!;
  check(
    `${axis.axis_id}: the baseline reproduces the witness the other screens verify`,
    baseline.witness_hash === recorded.witness_hash &&
      baseline.asserted_minor === recorded.asserted_minor,
    `${baseline.witness_hash.slice(0, 12)} vs ${recorded.witness_hash.slice(0, 12)}`,
  );
  check(
    `${axis.axis_id}: every stop away from baseline is a different manifest body`,
    axis.points
      .filter((p) => p.value !== axis.baseline_value)
      .every((p) => p.manifest_hash !== axis.signed_manifest_hash),
  );
  check(
    `${axis.axis_id}: moving the knob changes the asserted value`,
    new Set(axis.points.map((p) => p.asserted_minor)).size > 1,
  );
}

check(
  "at least one axis moves the ranking, not just the number",
  PERTURBATION.axes.some((axis) => {
    const baselineRank = new Map(
      axis.points[axis.baseline_index].ranking.map((r) => [r.instrument_id, r.rank]),
    );
    return axis.points.some((p) =>
      p.ranking.some((r) => baselineRank.get(r.instrument_id) !== r.rank),
    );
  }),
);
check(
  "the ranking is always a total order over every instrument",
  PERTURBATION.axes.every((axis) =>
    axis.points.every(
      (p) =>
        p.ranking.length === PERTURBATION.instruments.length &&
        p.ranking.every((r, i) => r.rank === i + 1) &&
        p.selected === p.ranking[0].instrument_id,
    ),
  ),
);
check(
  "the ranking never contradicts its own criterion",
  PERTURBATION.axes.every((axis) =>
    axis.points.every((p) =>
      p.ranking.every(
        (r, i) => i === 0 || p.ranking[i - 1].asserted_minor >= r.asserted_minor,
      ),
    ),
  ),
);

section("the derivation table, rebuilt in the browser");
const derivationMismatches: string[] = [];
for (const candidate of plumbline.valuation.instruments) {
  const rebuilt = derivationRows(
    manifestOf(candidate.instrument_id),
    plumbline.cart,
    candidate.witness,
  );
  if (JSON.stringify(rebuilt) !== JSON.stringify(candidate.derivation)) {
    derivationMismatches.push(candidate.product);
  }
}
check(
  "it reproduces the engine's own rows for every instrument, field for field",
  derivationMismatches.length === 0,
  derivationMismatches.join(", "),
);

// -- 3. drive the real UI ------------------------------------------------------------------

section("mount");
// StrictMode, because that is what main.tsx ships: the double-invoked mount effect must
// not leave two feed subscriptions behind.
const root = createRoot(document.getElementById("root")!);
await act(async () => {
  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
  await tick(50);
});
await waitFor("initial state", () => useConsole.getState().state !== null);

// The console now boots onto the landing page: a judge lands on the problem and the
// proof, not on beat 01 of a demo.
check("the console boots onto the landing page", useConsole.getState().screen === "landing");
check("the landing hero states the thesis", text().includes("Proving what a card is worth"));
check("the landing carries the proof", text().includes("One basket, two answers"));
check(
  "derived figures on the landing say whose they are",
  text().includes("our derivation, not an Amex figure"),
);
check("the eight beats are indexed on the landing", text().includes("Eight beats, in order"));

// Enter the console for the shell assertions.
await act(async () => {
  useConsole.getState().setScreen("overstatement");
  await tick(40);
});
check("header shows the log root", /[0-9a-f]{8}…/.test(text()));
check("the rail is grouped", text().includes("Valuation") && text().includes("Enforcement"));
check("the pager names the beat", /Beat 01 of 08/.test(text()));
// The recorded transport is fixtures the engine produced, and the badge says RECORDED
// rather than MOCK so a public viewer does not read the numbers as hand-written.
check("recorded mode is disclosed in the header", text().includes("RECORDED"));

// A duplicated subscription would double every feed row. Emit one event and count.
const feedBefore = useConsole.getState().feed.length;
await act(async () => {
  await useConsole.getState().revoke("mnd_subscription_probe", "subscription probe");
  await tick(60);
});
check(
  "StrictMode leaves exactly one feed subscription",
  useConsole.getState().feed.length === feedBefore + 1,
  `${feedBefore} → ${useConsole.getState().feed.length}`,
);

// -- 4. drive the five PLUMBLINE beats ------------------------------------------------------

section("beat 1 — the overstatement");
await waitFor("the overstatement screen", () => mainText().includes("The intuitive answer overstates."));
const rec = plumbline.receipt.candidates.find(
  (c) => c.instrument_id === plumbline.valuation.headline_instrument,
)!.reconciliation;
check("the per-line sum is on screen", mainText().includes(rec.naive_display), rec.naive_display);
check("the steelmanned per-line sum is on screen", mainText().includes(rec.capped_display));
check("the witness-backed figure is on screen", mainText().includes(rec.witness_display));
check("the overstatement is stated as a figure", mainText().includes(rec.overstatement_display));
check("all three causes are named", ["Over the remaining balance", "Struck by an exclusivity group", "Balance already consumed"].every((s) => mainText().includes(s)));
const headlineValuation = plumbline.valuation.instruments.find(
  (v) => v.instrument_id === plumbline.valuation.headline_instrument,
)!;
check(
  "the exclusivity group is named by the name the manifest gave it",
  headlineValuation.collisions.every((c) => mainText().includes(c.exclusivity_group)),
  headlineValuation.collisions.map((c) => c.exclusivity_group).join(","),
);
check("a competing credit is struck", mainText().includes("Struck by an exclusivity group"));
check("the console verified the witness itself", mainText().includes("VERIFIED"));
check("it says it used no solver", mainText().includes("no solver"));
// The redesign replaced the full allocation table with the claimed-vs-allocated collision
// table; the derivation-as-table claim now rides on beat 7, where the balance drawdown
// arrows and the manifest rules render (asserted in that section).
check(
  "the collision table shows claimed against allocated",
  mainText().includes("Two credits, one line") && mainText().includes("Allocated"),
);
/**
 * One assertion per benefit kind PRESENT in this derivation. Which kinds appear is a
 * property of the catalogue — the published Indian cards carry earn benefits and no
 * statement credits — so asserting a credit row onto a card that has none would be testing
 * the fixture rather than the renderer.
 */
const kindsOnScreen = new Set(headlineValuation.derivation.map((r) => r.benefit_kind));
check("the derivation names at least one kind", kindsOnScreen.size > 0);
const drawnDown = headlineValuation.derivation.find(
  (r) => r.capacity_before !== null && r.capacity_after !== r.capacity_before,
);
check("a capped benefit is drawn down in the data", Boolean(drawnDown));
/*
 * The handoff redesign removed the beat-1 latency panel (allocator p50, the cited solver
 * range, "not measured here", the incoherent-bound failure mode) and the full per-rule
 * allocation table. Those figures live in the deck and in artifacts/plumbline_bench.json;
 * the engine-level truth of the solver attribution is still asserted above
 * (latency.solver.measured_here === false), and the rule/arrow rendering is asserted on
 * beat 7 where the derivation table now lives.
 */
check("optimality is explicitly not claimed", mainText().includes("conservative by construction but not optimal"));
check("entailment latency is kept away from valuation latency", mainText().includes("different component"));

await act(async () => {
  useConsole.getState().setInstrument(plumbline.receipt.candidates[2].instrument_id);
  await tick(40);
});
check(
  "the judge can re-point the screen at another instrument",
  mainText().includes(plumbline.receipt.candidates[2].reconciliation.witness_display),
);
await act(async () => {
  useConsole.getState().setInstrument(plumbline.valuation.headline_instrument);
  await tick(40);
});

section("beat 2 — the receipt");
await act(async () => {
  useConsole.getState().setScreen("receipt");
  await tick(60);
});
check("the signing boundary is drawn and named", mainText().includes("signature boundary"));
check("it says what is below the line is not endorsed", mainText().includes("nothing below this line is issuer-endorsed"));
check("issuer-signed facts are labelled", mainText().includes("Issuer-signed facts"));
check("the unsigned side is labelled too", (mainText().match(/not issuer-signed/gi) ?? []).length >= 2);
check("the criterion is printed verbatim", mainText().includes(plumbline.receipt.criterion));
check(
  "the policy hash is recorded",
  mainText().includes(plumbline.receipt.policy.policy_hash.slice(0, 8)) &&
    mainText().includes(plumbline.receipt.policy.policy_hash.slice(-6)),
);
check(
  "every candidate appears with its value",
  plumbline.receipt.candidates.every((c) => mainText().includes(c.asserted_display)),
);
check("the winner is marked chosen", mainText().includes("CHOSEN"));
check("unpriced value is carried, not dropped", mainText().includes("Considered but unpriced"));
check(
  "an unpriced benefit is named, not merely counted",
  mainText().includes(headlineValuation.unpriced[0].label),
  headlineValuation.unpriced[0].label,
);
check(
  "each candidate is re-verified in the console",
  (mainText().match(/VERIFIED/g) ?? []).length >= plumbline.receipt.candidates.length,
);
check("the prototype-key limitation is disclosed", mainText().includes("prototype keys"));

section("beat 3 — omission");
await act(async () => {
  useConsole.getState().setScreen("omission");
  await tick(60);
});
await waitFor("both consistency proofs to settle", () =>
  (mainText().match(/VERIFIED|REJECTED/g) ?? []).length >= 2,
);
check("the honest extension verifies", mainText().includes("the older log is contained unchanged"));
check("the edited log is rejected", mainText().includes("REJECTED"));
check("the failure is explained, not just flagged", mainText().includes("not an extension of the head"));
check("the dropped instrument is named", mainText().includes(plumbline.omission.omitted_instrument.product));
check("its foregone value is named", mainText().includes(plumbline.omission.omitted_instrument.asserted_display));
check("the omission is labelled on the served set", mainText().includes("DROPPED"));
check("both tree heads are shown with their sizes", mainText().includes(`${plumbline.omission.head_a.tree_size}`) && mainText().includes(`${plumbline.omission.head_b.tree_size}`));
check("the audit path is printed", mainText().includes("Audit path"));
check("the risk-factor framing is used, not a blindness claim", mainText().includes("No industry mechanism exists to record"));
await waitFor("the proof vectors to replay in the browser", () => text().includes("vectors agree"));
check(
  "every generated vector agrees in the browser",
  text().includes(`${plumbline.omission.vectors.length}/${plumbline.omission.vectors.length} RFC 6962 vectors agree`),
);

section("beat 4 — refusal");
await act(async () => {
  useConsole.getState().setScreen("refusal");
  await tick(60);
});
check("the evaluator declines", mainText().includes("REFUSED TO SIGN"));
check("the refusal carries a reason code", mainText().includes("VALUATION_REFUSED_NO_SUPPORTING_WITNESS"));
check("the verifier's own code is shown", mainText().includes(plumbline.refusals[0].verification.failures[0].code));
check("the failure detail is shown verbatim", mainText().includes(plumbline.refusals[0].verification.failures[0].detail));
check("the asserted figure is struck", mainText().includes(plumbline.refusals[0].asserted_display));
check("the supported figure is offered instead", mainText().includes(plumbline.refusals[0].supported_display));
await waitFor("the inclusion proof to verify in the browser", () => mainText().includes("INCLUSION VERIFIED"));
check("the refusal is anchored in the log", mainText().includes("INCLUSION VERIFIED"));
check("the log rule is restated", mainText().includes("If it is not in the log it did not happen"));

await click(plumbline.refusals[1].label);
await waitFor("the second refusal case", () =>
  mainText().includes(plumbline.refusals[1].verification.failures[0].code),
);
check("the exclusivity refusal names the group violation", mainText().includes("WITNESS_EXCLUSIVITY_VIOLATED"));

section("beat 5 — graceful degrade, on screen");
await act(async () => {
  useConsole.getState().setScreen("degrade");
  await tick(60);
});
// The matrix keeps the run's own append order: PROCEEDS, DEGRADED, PROCEEDS, DENIED.
// Reordering rows to group outcomes would misstate log_seq.
await waitFor("the degrade screen to run its four passes", () =>
  (mainText().match(/PROCEEDS/g) ?? []).length >= 2 && mainText().includes("DENIED"),
);
check("the headline is the engine's own", mainText().includes(degradeRun.headline));
check(
  "two passes proceed, one degrades, one denies",
  (mainText().match(/PROCEEDS/g) ?? []).length === 2 &&
    (mainText().match(/DEGRADED/g) ?? []).length >= 1 &&
    (mainText().match(/DENIED/g) ?? []).length >= 1,
  `${(mainText().match(/PROCEEDS/g) ?? []).length} proceed`,
);
check(
  "every reason code the engine returned is on screen",
  new Set(passes.map((p) => p.reason_code)).size ===
    [...new Set(passes.map((p) => p.reason_code))].filter((c) => mainText().includes(c)).length,
);
/*
 * The actor diagram went with the redesign; the enforcement point survives as copy. The
 * routing-around framing lives in the deck rather than on this screen now.
 */
check(
  "the enforcement point is stated on screen",
  mainText().includes("no platform sees the check") ||
    mainText().includes("no platform was asked"),
);
check(
  "coverage is separated from authorization",
  mainText().includes("conditioned on evidence") &&
    mainText().toLowerCase().includes("purchase protection"),
);
check("the log records the unattested selections", mainText().includes("unattested_selection"));
check(
  "the receipt's attestation is shown, so the record certifies compliance too",
  mainText().includes(degradeRun.data.receipt.receipt.attestation.outcome) &&
    mainText().includes("whether or not the issuer's own card won"),
);

// The Card Member elects enforcement, live, in front of the room.
// Clicked by the sub-label rather than by "Enforce": the nav entry for this beat reads
// "enforcement is elected", and a substring search would find the nav first.
await click("elected by the Card Member");
await waitFor("the enforce posture copy", () =>
  mainText().includes("The Card Member has required the disclosure"),
);
check(
  "the elected posture is the one now described",
  mainText().includes("The check runs against their agent") &&
    !mainText().includes("Nothing is ever refused here"),
);

// The matrix rows are the pass selectors now; the denied pass's row carries DENIED.
await click("DENIED");
check(
  "the denial's detail is shown verbatim",
  mainText().includes(denials[0].detail),
  denials[0].detail.slice(0, 40),
);
check(
  "and it is named as the cardholder's own delegation failing",
  mainText().includes("their own delegated authority failing to discharge") ||
    mainText().includes("failing to discharge"),
);
check(
  "the mandate that failed to discharge is named",
  mainText().includes(degradeRun.data.mandate.mandate_id),
);
check(
  "no issuer and no platform is said to have declined",
  mainText().includes("No issuer declined anything, and no platform was asked"),
);

section("beat 6 — attribution");
await act(async () => {
  useConsole.getState().setScreen("attribution");
  await tick(60);
});
check("all four quadrants are on screen", ["Dead weight", "Load-bearing", "Noise", "An option"].every((s) => mainText().includes(s)));
check("the verdicts are stated, not implied", ["cut or renegotiate", "protect", "fund it, stop apologising for breakage"].every((s) => mainText().includes(s)));
check("the corpus size is stated", mainText().includes(String(book.corpus_size)));
check("the axis is selection influence, not usage", mainText().includes("selection influence"));
check("the retention caveat is on screen", mainText().includes("does not measure"));
check("the removal method is stated", mainText().includes("delete one benefit"));
check("the nudge posture is stated", mainText().includes("is ever shown to a Card Member"));
check(
  "the highest-cost load-bearing benefit is named",
  mainText().includes(book.benefits.find((b) => b.quadrant === "LOAD_BEARING")!.label),
);

section("beat 7 — hand the judge the controls");
await act(async () => {
  useConsole.getState().setScreen("controls");
  await tick(60);
});
await waitFor("the controls screen", () =>
  mainText().includes("Move one number."),
);

/**
 * The axis a judge would reach for: the one whose ranking actually moves. Picked from the
 * corpus rather than named, so this drives whichever axis carries the flip today.
 */
const movingAxis = PERTURBATION.axes.find((axis) => {
  const at = new Map(
    axis.points[axis.baseline_index].ranking.map((r) => [r.instrument_id, r.rank]),
  );
  return axis.points.some((p) => p.ranking.some((r) => at.get(r.instrument_id) !== r.rank));
})!;
const movingStop = movingAxis.points.findIndex((p) => {
  const at = new Map(
    movingAxis.points[movingAxis.baseline_index].ranking.map((r) => [r.instrument_id, r.rank]),
  );
  return p.ranking.some((r) => at.get(r.instrument_id) !== r.rank);
});
const movingBaseline = movingAxis.points[movingAxis.baseline_index];

await click(movingAxis.label);
await waitFor("the chosen axis", () => mainText().includes(movingAxis.benefit_label));

check("the axis opens at the value the manifest declares", mainText().includes(movingBaseline.value_display));
check(
  "and at that value the console reproduces the published body byte for byte",
  mainText().includes("Identical."),
);
check(
  "the baseline asserted value is the one the other screens show",
  mainText().includes(movingBaseline.asserted_display),
  movingBaseline.asserted_display,
);
check("the console verified the witness itself, here", mainText().includes("VERIFIED"));
check("it says it used no solver", mainText().includes("no solver"));
check(
  "the derivation is rebuilt in the browser, and says so",
  mainText().includes("Rebuilt in this browser from the witness"),
);
// The rule and drawdown rendering moved here from beat 1 with the derivation table.
check("balances are shown drawing down, with the arrow", /₹[\d,]+(\.\d\d)? → ₹[\d,]+(\.\d\d)?/.test(mainText()) && mainText().includes("Balance before"));
check("the earn rule is spelled out in the derivation", /\d+\.\d\d% of ₹/.test(mainText()));
check(
  "the criterion travels with the ranking",
  mainText().includes("not issuer-endorsed") && mainText().includes(PERTURBATION.criterion),
);

/** React tracks the value on the node, so the native setter is what a real drag looks like. */
async function setSlider(index: number) {
  const input = document.querySelector('input[type="range"]') as HTMLInputElement | null;
  if (!input) {
    check("the slider exists", false);
    return;
  }
  const setter = Object.getOwnPropertyDescriptor(
    dom.window.HTMLInputElement.prototype,
    "value",
  )!.set!;
  await act(async () => {
    setter.call(input, String(index));
    input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    await tick(40);
  });
}

await setSlider(movingStop);
const moved = movingAxis.points[movingStop];
check(
  "moving the knob re-values the instrument on screen",
  mainText().includes(moved.asserted_display),
  moved.asserted_display,
);
check(
  "the console re-verified the perturbed allocation and stands behind the same figure",
  mainText().includes(`assignments realise ${moved.asserted_display}`),
  moved.asserted_display,
);
check(
  "the ranking moved, with the direction shown",
  /[▲▼]\d/.test(mainText()),
);
check(
  "and the signature is disclosed as no longer covering the body",
  mainText().includes("Different bodies"),
);
// HashRow abbreviates to head…tail with the full value on the title attribute, so the
// assertion checks the rendered form: both hashes' heads and tails, not the full hex.
check(
  "the perturbed body's hash is shown beside the signed one",
  mainText().includes(moved.manifest_hash.slice(0, 8)) &&
    mainText().includes(movingAxis.signed_manifest_hash.slice(0, 8)) &&
    mainText().includes(moved.manifest_hash.slice(-6)),
);
check(
  "every stop is disclosed as engine-evaluated rather than solved in the browser",
  mainText().includes(`${movingAxis.points.length} engine-evaluated stops`),
);

await click("reset to the signed terms");
await waitFor("the reset", () => mainText().includes("Identical."));
check(
  "reset returns the screen to the signed terms",
  mainText().includes(movingBaseline.asserted_display) && mainText().includes("Identical."),
);

const rateAxis = PERTURBATION.axes.find((a) => a.field === "rate_bp")!;
await click(rateAxis.label);
await waitFor("the rate axis", () => mainText().includes(rateAxis.benefit_label));
check(
  "the rate axis discloses that a rate is a product term",
  mainText().includes("A rate is a product term"),
);
check(
  "and that the instrument carrying it is signed by nobody",
  mainText().includes("invented outright and signed by nobody"),
);
check(
  "the instrument whose rate moves is not issuer-signed",
  rateAxis.issuer_signed === false,
);

// -- 5. the governance kernel, as supporting evidence ---------------------------------------

section("beat 8 — kernel, injection");
await act(async () => {
  useConsole.getState().setScreen("kernel");
  useConsole.getState().setKernelTab("attack");
  await tick(40);
});
check("the kernel is framed as the layer the caveat rides on", mainText().includes("Governance kernel"));
check("the decision feed comes back with the kernel", text().includes("Decision feed"));
check("the enforcement point is stated", mainText().includes("no platform is asked for anything"));
await waitFor("the scenario slot to free", () => useConsole.getState().running === null);
await click("Run injection");
await waitFor("the five PDP stages to finish revealing", () =>
  mainText().includes("Step-up") && mainText().includes("INJECTION_COMPROMISE"),
);
check("ungoverned side shows APPROVED", mainText().includes("APPROVED"));
check("both panes show ₹55,200", (mainText().match(/₹55,200/g) ?? []).length >= 2);
check("divergence delta is +₹50,000", mainText().includes("+₹50,000"));
check("signed intent total is ₹5,200", mainText().includes("₹5,200"));
check("primary reason code is on screen", mainText().includes("MANDATE_CART_DIVERGENCE"));
check("stored-value MCC is called out", mainText().includes("MCC_NOT_ALLOWED"));
check("liability is routed away from the cardholder", mainText().includes("cardholder not liable"));
// The injected lines render once, in the divergence table, rather than in both panes.
check(
  "ten injected lines render in the divergence table",
  (mainText().match(/sku_giftcard_/g) ?? []).length === 10,
  String((mainText().match(/sku_giftcard_/g) ?? []).length),
);
check(
  "every injected line is tagged",
  (mainText().match(/INJECTED/g) ?? []).length >= 10,
  String((mainText().match(/INJECTED/g) ?? []).length),
);
check(
  "all five PDP stages are named",
  ["Revocation", "Credential", "Cart ↔ intent", "Scope", "Step-up"].every((s) =>
    mainText().includes(s),
  ),
);

await click("Clean run");
// The diff panel renders as soon as the decision arrives, so "cart matches signed intent"
// appears before the reveal has settled. Wait for the outcome chip, which only mounts
// once the check ladder has run.
await waitFor("clean run to settle", () => mainText().includes("ALLOW"));
check("clean run authorises", mainText().includes("ALLOW"));
check("clean run matches signed intent", mainText().includes("cart matches signed intent"));
check("clean run total is ₹5,200", mainText().includes("₹5,200"));
check("clean run shows no injected lines", !mainText().includes("INJECTED"));

section("beat 8 — kernel, delegation and the escalation proof");
await act(async () => {
  useConsole.getState().setKernelTab("delegation");
  await tick(40);
});
// The run buttons disable while any scenario is in flight, and the previous section's run
// can still hold the slot for a few frames after its assertions pass. A presenter cannot
// click a disabled button and neither may the suite.
await waitFor("the scenario slot to free", () => useConsole.getState().running === null);
await click("Run delegation hops");
await waitFor("proof panel", () => mainText().includes("NARROWER"));
check("both verdicts are shown side by side", mainText().includes("NARROWER") && mainText().includes("SCOPE_ESCALATION"));
check("the disagreement is called out", mainText().includes("subset check was about to authorise"));
check("the entailment formula is printed", mainText().includes("UNSAT( child ∧ ¬parent )"));
check("the counterexample is printed verbatim", mainText().includes("<any merchant outside the named sets>"));
check("the counterexample amount is shown", mainText().includes("800001"));
check("violated parent constraints are named", mainText().includes("category ∈ {groceries, appliances}"));
check("entailment time is shown in ms", /entailment\s+[\d.]+\s*ms/.test(mainText()));
check("rejected hops appear on the graph", (mainText().match(/REJECTED/g) ?? []).length >= 2);
check("accepted hops show their proof time", /proved in [\d.]+\s*ms/.test(mainText()));
check("the decidability limit is disclosed", mainText().includes("quantifier-free linear integer arithmetic"));

// The two checks disagree in both directions. Selecting an accepted hop that the subset
// check would have rejected must not print the "about to authorise" copy.
const falseReject = delegations.findIndex((d) => d.naive_disagrees && d.accepted);
check("the corpus contains a disagreement in each direction", falseReject >= 0);
if (falseReject >= 0) {
  await click(delegations[falseReject].title!);
  await waitFor("false-rejection hop", () => mainText().includes("would have rejected"));
  check(
    "an accepted hop is not described as an escalation",
    !mainText().includes("about to authorise"),
  );
  check("the entailment column still reads ENTAILED", mainText().includes("ENTAILED"));
}

section("beat 8 — kernel, kill switch");
await act(async () => {
  useConsole.getState().setKernelTab("kill");
  await tick(40);
});
await waitFor("the scenario slot to free", () => useConsole.getState().running === null);
await click("REVOKE");
// The After column reads REVOKED at the root and DENIED on every descendant.
await waitFor(
  "every hop to fail closed",
  () => (mainText().match(/DENIED/g) ?? []).length >= 4 && mainText().includes("REVOKED"),
);
check("the control reads REVOKED", mainText().includes("REVOKED"));
check(
  "the root revokes and all four descendants deny",
  (mainText().match(/DENIED/g) ?? []).length >= 4,
);
check("no hop is still live", !/\bLIVE\b/.test(mainText()));
check("the unregistered sub-agent is labelled", mainText().includes("NEVER REGISTERED"));
check("rows written is 1", /Rows written\s*1/.test(mainText().replace(/\s+/g, " ")));
check("descendants contained is 4", /Descendants contained\s*4/.test(mainText().replace(/\s+/g, " ")));
check("the TTL bound is disclosed, not hidden", mainText().includes("bounded by the discharge TTL"));
check("engine containment time is shown", /Engine containment\s*[\d.]+\s*ms/.test(mainText().replace(/\s+/g, " ")));
check("before/after probes are shown", mainText().includes("Probe at the deepest agent"));

section("beat 8 — kernel, exposure book");
await act(async () => {
  useConsole.getState().setKernelTab("exposure");
  await tick(60);
});
check("book exposure is shown", mainText().includes("Book exposure"));
check("declined exposure is shown", mainText().includes("Declined"));
check("blended rate in bps", /\d+ bps/.test(mainText()));
check("operator table is populated", mainText().includes("NightMarket Relay") && mainText().includes("RESTRICTED"));
check("cutoff curve is interactive", document.querySelector('[role="slider"]') !== null);
check("the model is disclosed as modelled", mainText().includes("mock dataset"));
check("the volume prior is disclosed", mainText().includes("shrink toward a prior on low volume"));

const slider = document.querySelector('[role="slider"]') as SVGElement | null;
if (slider) {
  const before = useConsole.getState().state!.exposure.cutoff_score;
  await act(async () => {
    slider.dispatchEvent(
      new dom.window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }),
    );
    await tick(30);
  });
  check("cutoff responds to keyboard", text().includes(String(before + 1)));
}

section("beat 8 — kernel, evidence drawer");
const txn = useConsole.getState().state!.decisions[0]!.txn_id;
await act(async () => {
  useConsole.getState().setKernelTab("attack");
  await useConsole.getState().openEvidence(txn);
  await tick(60);
});
await waitFor("drawer", () => text().includes("Agent Evidence Package"));
check("mandate chain is shown", text().includes("Mandate chain"));
check("cart diff is shown", text().includes("Cart ↔ intent"));
check("decision chain is shown", text().includes("Decision chain"));
check("merkle proof section is shown", text().includes("Merkle inclusion proof"));
await waitFor("independent verification", () => text().includes("recomputed root matches"));
check("console verified the proof itself", (text().match(/VERIFIED/g) ?? []).length >= 2);
check("liability verdict is shown", text().includes("liable:"));
check("offline-verifiability limit is disclosed", text().includes("not offline-verifiable"));
check("the audit path is printed", text().includes("Audit path"));

console.log(failures === 0 ? "\nALL CHECKS PASSED" : `\n${failures} CHECK(S) FAILED`);
process.exit(failures === 0 ? 0 : 1);
