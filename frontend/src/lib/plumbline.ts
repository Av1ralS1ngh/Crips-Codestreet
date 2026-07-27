/**
 * The wire contract for the PLUMBLINE artifacts — manifests, witnesses, receipts, tree
 * heads, attribution.
 *
 * Same rule as `types.ts`: every shape here mirrors a `to_dict()` on the Python side, and
 * `src/mock/plumblineFixtures.json` is produced by driving the real `plumbline` package (see
 * `scripts/gen_plumbline_fixtures.py`). If a field here disagrees with the backend, the
 * backend is right and this file is stale.
 *
 * Money is an integer count of minor units. Every figure that carries a rendered string
 * carries it as `<name>_display`, produced by the kernel's own formatter, and the console's
 * `format.money` is checked against those strings in the smoke test.
 */

import type { Cart, InclusionProof } from "./types";

// ---------------------------------------------------------------------------------------
// Closed vocabularies. Mirrored from plumbline/manifest.py and plumbline/witness.py. Never
// inline one of these as a string literal at a call site.
// ---------------------------------------------------------------------------------------

export const KIND_EARN = "earn";
export const KIND_CREDIT = "credit";
export const KIND_PROTECTION = "protection";
export const KIND_UNPRICED = "unpriced";

export type BenefitKind =
  | typeof KIND_EARN
  | typeof KIND_CREDIT
  | typeof KIND_PROTECTION
  | typeof KIND_UNPRICED;

/** witness.py failure codes. A verifier returns these; nothing else may. */
export const ERR_UNKNOWN_BENEFIT = "WITNESS_UNKNOWN_BENEFIT";
export const ERR_UNKNOWN_LINE = "WITNESS_UNKNOWN_LINE";
export const ERR_INELIGIBLE = "WITNESS_INELIGIBLE_ASSIGNMENT";
export const ERR_CAPACITY = "WITNESS_CAPACITY_EXCEEDED";
export const ERR_EXCLUSIVITY = "WITNESS_EXCLUSIVITY_VIOLATED";
export const ERR_VALUE_MISMATCH = "WITNESS_VALUE_MISMATCH";
export const ERR_OVERSTATED = "WITNESS_DOES_NOT_SUPPORT_ASSERTION";
export const ERR_UNAVAILABLE = "WITNESS_BENEFIT_UNAVAILABLE";
export const ERR_DOUBLE_ASSIGNED = "WITNESS_LINE_DOUBLE_ASSIGNED_SAME_BENEFIT";
export const ERR_CONSUMPTION_MISMATCH = "WITNESS_CONSUMPTION_MISMATCH";
export const ERR_CREDIT_OVER_LINE = "WITNESS_CREDIT_EXCEEDS_LINE_AMOUNT";
export const ERR_LINE_OVER_OFFSET = "WITNESS_LINE_OFFSET_EXCEEDS_LINE_AMOUNT";
export const ERR_NEGATIVE_AMOUNT = "WITNESS_NEGATIVE_AMOUNT";
export const ERR_UNPRICED = "WITNESS_UNPRICED_BENEFIT_ASSIGNED";
export const ERR_DUPLICATE_SKU = "WITNESS_CART_DUPLICATE_SKU";
export const ERR_MANIFEST_MISMATCH = "WITNESS_MANIFEST_MISMATCH";
export const ERR_CART_MISMATCH = "WITNESS_CART_MISMATCH";
export const ERR_CURRENCY_MISMATCH = "WITNESS_CURRENCY_MISMATCH";

/**
 * The closed vocabulary, mirroring `witness.py :: FAILURE_CODES`. Exported so a conformance
 * check can assert the two verifiers share one vocabulary rather than drifting apart
 * silently — a console verifier missing a code is a console that passes forgeries the
 * evaluator refuses, which is worse than having no console verifier at all.
 */
export const WITNESS_FAILURE_CODES = [
  ERR_UNKNOWN_BENEFIT,
  ERR_UNKNOWN_LINE,
  ERR_INELIGIBLE,
  ERR_CAPACITY,
  ERR_EXCLUSIVITY,
  ERR_VALUE_MISMATCH,
  ERR_OVERSTATED,
  ERR_UNAVAILABLE,
  ERR_DOUBLE_ASSIGNED,
  ERR_CONSUMPTION_MISMATCH,
  ERR_CREDIT_OVER_LINE,
  ERR_LINE_OVER_OFFSET,
  ERR_NEGATIVE_AMOUNT,
  ERR_UNPRICED,
  ERR_DUPLICATE_SKU,
  ERR_MANIFEST_MISMATCH,
  ERR_CART_MISMATCH,
  ERR_CURRENCY_MISMATCH,
] as const;

/**
 * The evaluator's own refusal code. It sits above the verifier codes: the verifier reports
 * why a witness is invalid, this reports what the evaluator did about it.
 *
 * Owned by the console's mock layer until `plumbline/receipt.py` lands, at which point the
 * backend becomes the authority for this string exactly as it is for the codes above.
 */
export const REFUSED_NO_WITNESS = "VALUATION_REFUSED_NO_SUPPORTING_WITNESS";

/** Where a rupee went between a per-line sum and the witness. */
export const CAUSE_ASSIGNED = "ASSIGNED";
export const CAUSE_BALANCE = "OVER_REMAINING_BALANCE";
export const CAUSE_EXCLUSIVITY = "STRUCK_EXCLUSIVITY_GROUP";
export const CAUSE_EXHAUSTED = "BALANCE_ALREADY_CONSUMED";

export type ShortfallCause =
  | typeof CAUSE_ASSIGNED
  | typeof CAUSE_BALANCE
  | typeof CAUSE_EXCLUSIVITY
  | typeof CAUSE_EXHAUSTED;

/** Human copy for each cause. Kept beside the constant so the two cannot drift. */
export const CAUSE_COPY: Record<string, { label: string; why: string }> = {
  [CAUSE_BALANCE]: {
    label: "Over the remaining balance",
    why: "the credit is smaller than the line it was applied to",
  },
  [CAUSE_EXCLUSIVITY]: {
    label: "Struck by an exclusivity group",
    why: "two benefits in one group both claimed the same line",
  },
  [CAUSE_EXHAUSTED]: {
    label: "Balance already consumed",
    why: "an earlier line in the same cart had already spent it",
  },
  [CAUSE_ASSIGNED]: { label: "Assigned in full", why: "the pairing survived intact" },
};

export const QUADRANT_DEAD_WEIGHT = "DEAD_WEIGHT";
export const QUADRANT_LOAD_BEARING = "LOAD_BEARING";
export const QUADRANT_NOISE = "NOISE";
export const QUADRANT_OPTION = "OPTION";

export type Quadrant =
  | typeof QUADRANT_DEAD_WEIGHT
  | typeof QUADRANT_LOAD_BEARING
  | typeof QUADRANT_NOISE
  | typeof QUADRANT_OPTION;

// ---------------------------------------------------------------------------------------
// manifest.py
// ---------------------------------------------------------------------------------------

export interface Eligibility {
  mccs: number[];
  merchants: string[];
  categories: string[];
}

export interface BenefitDict {
  benefit_id: string;
  kind: BenefitKind;
  label: string;
  eligibility: Eligibility;
  rate_bp: number;
  capacity_minor: number | null;
  flat_minor: number;
  exclusivity_group: string | null;
  window: string;
  requires_enrollment: boolean;
  enrolled: boolean;
  note: string;
}

/** manifest.py :: Manifest.body() — the exact object an issuer signs. */
export interface ManifestBody {
  version: string;
  manifest_id: string;
  issuer: string;
  product: string;
  currency: string;
  issued_at: number;
  source: string;
  benefits: BenefitDict[];
}

export interface InstrumentRecord {
  instrument_id: string;
  issuer: string;
  product: string;
  issuer_signed: boolean;
  source: string;
  manifest: ManifestBody;
}

// ---------------------------------------------------------------------------------------
// witness.py
// ---------------------------------------------------------------------------------------

export interface AssignmentDict {
  line_sku: string;
  benefit_id: string;
  consumed_minor: number;
  value_minor: number;
}

export interface WitnessDict {
  manifest_id: string;
  cart_hash: string;
  assignments: AssignmentDict[];
  realized_minor: number;
  realized_display: string;
}

export interface WitnessFailure {
  code: string;
  detail: string;
}

/**
 * witness.py :: Verification.to_dict()
 *
 * `claimed_minor` is what the witness said; `realized_minor` is what the verifier will
 * stand behind after dropping every assignment that failed a check. On a clean
 * verification the two are equal. On a failed one the difference localises the
 * overstatement, and `realized_minor` is still achievable — a sound lower bound, never an
 * upper one — which is why a failed verification can be shown with a number beside it.
 */
export interface WitnessVerification {
  ok: boolean;
  supports_assertion: boolean;
  realized_minor: number;
  realized_display: string;
  claimed_minor: number;
  claimed_display: string;
  asserted_minor: number;
  asserted_display: string;
  failures: WitnessFailure[];
}

// ---------------------------------------------------------------------------------------
// The valuation of one cart on one instrument.
// ---------------------------------------------------------------------------------------

/** One row of the allocation table. This table IS the derivation. */
export interface DerivationRow {
  line_sku: string;
  line_description: string;
  line_amount: number;
  line_mcc: number;
  benefit_id: string;
  benefit_label: string;
  benefit_kind: BenefitKind;
  rate_bp: number;
  window: string;
  exclusivity_group: string | null;
  consumed_minor: number;
  value_minor: number;
  value_display: string;
  /** null when the benefit declares no ceiling. */
  capacity_before: number | null;
  capacity_after: number | null;
}

export interface ReconciliationRow {
  line_sku: string;
  line_description: string;
  benefit_id: string;
  benefit_label: string;
  benefit_kind: BenefitKind;
  exclusivity_group: string | null;
  naive_minor: number;
  capped_minor: number;
  realized_minor: number;
  shortfall_minor: number;
  cause: ShortfallCause;
  struck_by: string | null;
}

/**
 * Two baselines, not one. `naive_minor` is the literal per-line sum; `capped_minor` is the
 * strongest figure a per-line implementation can reach — it reads each balance and clamps
 * to it. Showing only the first would be arguing with a weaker opponent than exists.
 */
export interface Reconciliation {
  naive_minor: number;
  naive_display: string;
  capped_minor: number;
  capped_display: string;
  witness_minor: number;
  witness_display: string;
  overstatement_minor: number;
  overstatement_display: string;
  capped_overstatement_minor: number;
  capped_overstatement_display: string;
  by_cause: {
    cause: ShortfallCause;
    minor: number;
    display: string;
    visible_per_line: boolean;
  }[];
  rows: ReconciliationRow[];
}

export interface Collision {
  line_sku: string;
  line_description: string;
  line_amount: number;
  exclusivity_group: string;
  winner: {
    benefit_id: string;
    label: string;
    value_minor: number;
    value_display: string;
    capacity_minor: number | null;
  };
  struck: {
    benefit_id: string;
    label: string;
    value_minor: number;
    value_display: string;
    capacity_minor: number | null;
  }[];
}

export interface UnpricedEntry {
  benefit_id: string;
  label: string;
  note: string;
}

export interface InstrumentValuation {
  instrument_id: string;
  issuer: string;
  product: string;
  manifest_id: string;
  manifest_hash: string;
  manifest_source: string;
  /** The signing boundary. False means the agent modelled published terms itself. */
  issuer_signed: boolean;
  signature: string | null;
  key_id: string | null;
  asserted_minor: number;
  asserted_display: string;
  witness: WitnessDict;
  witness_hash: string;
  /** The server's own verification. The console re-runs it and shows both. */
  verification: WitnessVerification;
  derivation: DerivationRow[];
  reconciliation: Reconciliation;
  collisions: Collision[];
  unpriced: UnpricedEntry[];
  allocator_stats: {
    considered: number;
    assigned: number;
    skipped_capacity: number;
    skipped_exclusivity: number;
  };
  /** Assigned by the receipt, under the cardholder's criterion. Absent before ranking. */
  rank?: number;
}

// ---------------------------------------------------------------------------------------
// Latency. Every figure carries the conditions it was measured under; a bare number is
// exactly the thing this project exists to argue against.
// ---------------------------------------------------------------------------------------

export interface LatencySample {
  p50_ms: number;
  p99_ms: number;
  min_ms: number;
  max_ms: number;
  runs: number;
  problem_size: string;
  method: string;
}

export interface SolverBenchmark {
  lo_ms: number;
  hi_ms: number;
  variance_x: number;
  timeout_ms: number;
  problem_size: string;
  source: string;
  /** Always false. These are the adversarial panel's figures, not ours. */
  measured_here: boolean;
  failure_mode: string;
}

export interface LatencyTable {
  demo: LatencySample;
  demo_verify: LatencySample;
  bench: LatencySample;
  bench_verify: LatencySample;
  solver: SolverBenchmark;
  host: { python: string; note: string };
}

export interface ValuationRun {
  scenario_id: string;
  label: string;
  narrative: string;
  headline_instrument: string;
  instruments: InstrumentValuation[];
  latency: LatencyTable;
}

// ---------------------------------------------------------------------------------------
// The receipt. The signing boundary runs straight through the middle of this object:
// `candidates[].signature` is the issuer's, `policy` is the cardholder's.
// ---------------------------------------------------------------------------------------

export interface ValuationPolicy {
  policy_id: string;
  owner: string;
  criterion: string;
  /** Always false. The issuer signs facts and never signs a comparison. */
  endorsed_by_issuer: boolean;
  note: string;
  policy_hash: string;
}

export interface DecisionReceipt {
  receipt_id: string;
  issued_at: number;
  cart: Cart;
  cart_hash: string;
  policy: ValuationPolicy;
  criterion: string;
  candidates: InstrumentValuation[];
  selected: string;
  ledger_seq: number;
  entry_hash: string;
  leaf_hash: string;
  inclusion_proof: InclusionProof;
  ledger_root: string;
  ledger_size: number;
}

// ---------------------------------------------------------------------------------------
// Refusal — a typed, first-class output.
// ---------------------------------------------------------------------------------------

export interface RefusalCase {
  case_id: string;
  label: string;
  narrative: string;
  asserted_minor: number;
  asserted_display: string;
  /** What the evaluator would have signed, had it been asked for that instead. */
  supported_minor: number;
  supported_display: string;
  witness: WitnessDict;
  witness_hash: string;
  verification: WitnessVerification;
  reason_code: string;
  ledger_seq: number;
  entry_hash: string;
  leaf_hash: string;
  inclusion_proof: InclusionProof;
  ledger_root: string;
  ledger_size: number;
}

// ---------------------------------------------------------------------------------------
// Transparency. RFC 6962 signed tree heads and consistency proofs.
// ---------------------------------------------------------------------------------------

export interface SignedTreeHead {
  tree_size: number;
  root_hash: string;
  signed_at: number;
  key_id: string;
  signature: string;
}

export interface ConsistencyProof {
  first_size: number;
  second_size: number;
  first_root: string;
  second_root: string;
  path: string[];
}

/** Cross-checked (m, n) pairs, including negatives, so the browser verifier is tested. */
export interface ConsistencyVector extends ConsistencyProof {
  expect_ok: boolean;
}

export interface OmissionCase {
  narrative: string;
  omitted_instrument: {
    instrument_id: string;
    issuer: string;
    product: string;
    asserted_minor: number;
    asserted_display: string;
  };
  head_a: SignedTreeHead;
  head_b: SignedTreeHead;
  head_b_edited: SignedTreeHead;
  consistency_honest: ConsistencyProof;
  consistency_edited: ConsistencyProof;
  receipt_seq: number;
  candidates_published: string[];
  candidates_served: string[];
  vectors: ConsistencyVector[];
}

// ---------------------------------------------------------------------------------------
// Attribution. Selection influence, not retention — the caveat travels with the number.
// ---------------------------------------------------------------------------------------

export interface BenefitAttribution {
  benefit_id: string;
  label: string;
  kind: BenefitKind;
  issuer: string;
  product: string;
  manifest_id: string;
  /** Receipts where the benefit was eligible for at least one line. */
  in_candidate_set: number;
  in_winning_derivation: number;
  /** Receipts where deleting it changes which instrument the criterion selects. */
  decisive: number;
  decisive_bp: number;
  value_delivered_minor: number;
  value_delivered_display: string;
  annual_cost_minor: number;
  annual_cost_display: string;
  high_cost: boolean;
  often_decisive: boolean;
  quadrant: Quadrant;
}

export interface AttributionBook {
  as_of: number;
  corpus_size: number;
  method: string;
  cost_source: string;
  thresholds: {
    high_cost_minor: number;
    high_cost_display: string;
    decisive_bp: number;
  };
  caveat: string;
  wins: { manifest_id: string; wins: number }[];
  benefits: BenefitAttribution[];
}

// ---------------------------------------------------------------------------------------
// Endpoint envelope. One GET, because the five beats are five views of one corpus and a
// judge switching screens must never see two screens disagree.
// ---------------------------------------------------------------------------------------

/** GET /api/plumbline/state */
export interface PlumblineState {
  generated_at: number;
  disclosure: string;
  cart: Cart;
  cart_hash: string;
  instruments: InstrumentRecord[];
  valuation: ValuationRun;
  receipt: DecisionReceipt;
  refusals: RefusalCase[];
  omission: OmissionCase;
  attribution: AttributionBook;
}

// ---------------------------------------------------------------------------------------
// Graceful degrade. Mirrored from receipt.py — the postures a Card Member may elect and
// what the presence or absence of a counterpart receipt means under each.
//
// The reason codes are the whole beat. UNATTESTED_SELECTION is a record, not a decline;
// DISCLOSURE_CAVEAT_UNDISCHARGED is a denial, and it is the Card Member's own mandate
// failing to discharge, never an issuer refusing a platform's credential.
// ---------------------------------------------------------------------------------------

export const POSTURE_OBSERVE_ONLY = "observe_only";
export const POSTURE_ENFORCE = "enforce";

export type Posture = typeof POSTURE_OBSERVE_ONLY | typeof POSTURE_ENFORCE;

export const REASON_SELECTION_ATTESTED = "SELECTION_ATTESTED";
export const REASON_UNATTESTED_SELECTION = "UNATTESTED_SELECTION";
export const REASON_DISCLOSURE_CAVEAT_UNDISCHARGED = "DISCLOSURE_CAVEAT_UNDISCHARGED";

export const COUNTERPART_REASON_CODES = [
  REASON_SELECTION_ATTESTED,
  REASON_UNATTESTED_SELECTION,
  REASON_DISCLOSURE_CAVEAT_UNDISCHARGED,
] as const;

/** receipt.py :: CounterpartAssessment.to_dict() */
export interface CounterpartAssessment {
  posture: Posture;
  proceeds: boolean;
  reason_code: string;
  detail: string;
  coverage_eligible: boolean;
  /** What lands in the log. Hashes and ids only — never a cart, never a pan. */
  record: Record<string, string | number>;
}

export interface DegradePass {
  posture: Posture;
  counterpart_receipt: boolean;
  proceeds: boolean;
  reason_code: string;
  detail: string;
  coverage_eligible: boolean;
  log_seq: number;
  assessment: CounterpartAssessment;
}

export interface MandateBinding {
  mandate_id: string;
  authorized_instrument_ids: string[];
  /** The caveat the Card Member put on their own agent. Not a platform obligation. */
  disclosure_caveat: boolean;
}

export interface PlumblineTreeHead {
  version: string;
  log_id: string;
  tree_size: number;
  root_hash: string;
  timestamp: number;
  signature: { alg: string; key_id: string; value: string };
}

/**
 * The receipt the scenario emitted, typed only where this console reads it. The rest of
 * the body is carried verbatim rather than reshaped: the mock transport serves exactly
 * what `POST /api/plumbline/scenario/graceful_degrade` returns, so a field this console does
 * not read today is still the field the backend will send tomorrow.
 */
export interface DegradeReceipt {
  receipt: {
    receipt_id: string;
    posture: Posture;
    candidate_set: {
      size: number;
      instrument_ids: string[];
      candidates: {
        instrument_id: string;
        issuer: string;
        product: string;
        asserted_value_minor: number | null;
        asserted_value_display: string | null;
        status: string;
        witness_status: string;
      }[];
    };
    selection: { instrument_id: string; manifest_id: string; criterion: string };
    attestation: {
      outcome: string;
      faithful: boolean;
      findings: { code: string; detail: string }[];
      checked_against_mandate: boolean;
    };
    ranking: { issuer_endorsed: boolean; note: string } | null;
  };
  signature: { alg: string; key_id: string; role: string; value: string; covers: string };
}

export interface DegradeData {
  cart: Cart;
  cart_total_minor: number;
  cart_total_display: string;
  mandate: MandateBinding;
  chosen_instrument_id: string;
  passes: DegradePass[];
  log_entry_kinds: Record<string, number>;
  reason_code_counts: Record<string, number>;
  signed_tree_head: PlumblineTreeHead;
  receipt: DegradeReceipt;
}

/** scenarios.py :: ScenarioResult.to_dict() */
export interface PlumblineScenarioRun<D> {
  name: string;
  title: string;
  headline: string;
  clock: number;
  notes: string[];
  data: D;
}

export type DegradeRun = PlumblineScenarioRun<DegradeData>;

// ---------------------------------------------------------------------------------------
// Perturbation. One field of one benefit, moved; the allocator re-run at every stop.
//
// `field` is a closed vocabulary because a perturbation the console cannot apply for
// itself is a perturbation it cannot check. Both spellings name a field on BenefitDict.
// ---------------------------------------------------------------------------------------

export const FIELD_CAPACITY = "capacity_minor";
export const FIELD_RATE_BP = "rate_bp";

export type PerturbableField = typeof FIELD_CAPACITY | typeof FIELD_RATE_BP;

/**
 * What kind of fact the knob moves, which decides what may be said about it out loud.
 * A balance is member state: two Card Members differ on it on the same day, and on a real
 * product the axis stops at the published cap. A rate is a product term, so it is moved
 * only on the instrument that is invented outright and signed by nobody.
 */
export const FACT_MEMBER_STATE = "member state";
export const FACT_INVENTED_TERM = "invented term";

export interface RankEntry {
  instrument_id: string;
  asserted_minor: number;
  asserted_display: string;
  rank: number;
}

export interface PerturbationPoint {
  value: number;
  value_display: string;
  /** Of the PERTURBED manifest. Differs from the signed hash everywhere but baseline. */
  manifest_hash: string;
  asserted_minor: number;
  asserted_display: string;
  witness: WitnessDict;
  witness_hash: string;
  ranking: RankEntry[];
  selected: string;
  allocator_stats: {
    considered: number;
    assigned: number;
    skipped_capacity: number;
    skipped_exclusivity: number;
  };
}

export interface PerturbationAxis {
  axis_id: string;
  instrument_id: string;
  issuer: string;
  product: string;
  issuer_signed: boolean;
  benefit_id: string;
  benefit_label: string;
  benefit_kind: BenefitKind;
  benefit_window: string;
  benefit_note: string;
  exclusivity_group: string | null;
  field: PerturbableField;
  unit: "money" | "bp";
  fact: typeof FACT_MEMBER_STATE | typeof FACT_INVENTED_TERM;
  label: string;
  what_it_is: string;
  watch_for: string;
  baseline_value: number;
  baseline_index: number;
  signed_manifest_hash: string;
  points: PerturbationPoint[];
}

/** Same shape as InstrumentRecord, plus the signature the seal renders. */
export interface PerturbationInstrument extends InstrumentRecord {
  manifest_hash: string;
  signature: string | null;
  key_id: string | null;
}

export interface PerturbationBook {
  generated_at: number;
  disclosure: string;
  cart: Cart;
  cart_hash: string;
  criterion: string;
  policy_hash: string;
  method: string;
  instruments: PerturbationInstrument[];
  axes: PerturbationAxis[];
}

/** The quadrant copy. Verdicts, not descriptions — the 2x2 exists to produce a decision. */
export const QUADRANT_COPY: Record<
  Quadrant,
  { title: string; verdict: string; cost: string; influence: string }
> = {
  [QUADRANT_DEAD_WEIGHT]: {
    title: "Dead weight",
    verdict: "cut or renegotiate",
    cost: "high cost",
    influence: "rarely decisive",
  },
  [QUADRANT_LOAD_BEARING]: {
    title: "Load-bearing",
    verdict: "protect",
    cost: "high cost",
    influence: "often decisive",
  },
  [QUADRANT_NOISE]: {
    title: "Noise",
    verdict: "leave it alone",
    cost: "low cost",
    influence: "rarely decisive",
  },
  [QUADRANT_OPTION]: {
    title: "An option",
    verdict: "fund it, stop apologising for breakage",
    cost: "low cost",
    influence: "often decisive",
  },
};
