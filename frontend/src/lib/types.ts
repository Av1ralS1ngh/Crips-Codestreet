/**
 * The wire contract between the console and `caveat.api`.
 *
 * Everything below `ScenarioResult` mirrors a `to_dict()` on the Python side, so these
 * shapes are generated-from-reality rather than guessed: `src/mock/fixtures.json` is
 * produced by driving the real `CaveatEngine`. If a field here disagrees with the
 * backend, the backend is right and this file is stale.
 *
 * Money is always an integer count of minor units (paise). The console never does
 * arithmetic on a formatted string and never introduces a float into a money path.
 */

// ---------------------------------------------------------------------------------------
// Reason codes and verdicts. Closed vocabularies mirrored from constraints.py / pdp.py.
// Never inline one of these as a string literal at a call site.
// ---------------------------------------------------------------------------------------

export const REASON_AMOUNT_EXCEEDED = "AMOUNT_EXCEEDED";
export const REASON_CUMULATIVE_EXCEEDED = "CUMULATIVE_BUDGET_EXCEEDED";
export const REASON_VELOCITY_EXCEEDED = "VELOCITY_EXCEEDED";
export const REASON_MERCHANT_NOT_ALLOWED = "MERCHANT_NOT_ALLOWED";
export const REASON_MCC_NOT_ALLOWED = "MCC_NOT_ALLOWED";
export const REASON_CATEGORY_NOT_ALLOWED = "CATEGORY_NOT_ALLOWED";
export const REASON_GEO_NOT_ALLOWED = "GEO_NOT_ALLOWED";
export const REASON_MANDATE_EXPIRED = "MANDATE_EXPIRED";
export const REASON_MANDATE_NOT_YET_VALID = "MANDATE_NOT_YET_VALID";
export const REASON_STEP_UP_REQUIRED = "STEP_UP_REQUIRED";
export const REASON_MANDATE_REVOKED = "MANDATE_REVOKED";
export const REASON_CREDENTIAL_INVALID = "CREDENTIAL_INVALID";
export const REASON_CART_DIVERGENCE = "MANDATE_CART_DIVERGENCE";
export const REASON_MERCHANT_SWAPPED = "MERCHANT_SWAPPED_AFTER_INTENT";
export const REASON_SCOPE_ESCALATION = "SCOPE_ESCALATION";

export const VERDICT_WITHIN_MANDATE = "WITHIN_MANDATE";
export const VERDICT_AGENT_ERROR = "AGENT_ERROR";
export const VERDICT_INJECTION_COMPROMISE = "INJECTION_COMPROMISE";
export const VERDICT_MERCHANT_MISREPRESENTATION = "MERCHANT_MISREPRESENTATION";

export type Outcome = "ALLOW" | "STEP_UP" | "DENY";

/** The PDP runs these in fail-closed order; the console shows them in the same order. */
export const PDP_STAGES = [
  {
    key: "revocation",
    label: "Revocation",
    note: "is the root still live",
    codes: [REASON_MANDATE_REVOKED],
  },
  {
    key: "credential",
    label: "Credential",
    note: "does the HMAC caveat chain verify",
    codes: [REASON_CREDENTIAL_INVALID],
  },
  {
    key: "divergence",
    label: "Cart ↔ intent",
    note: "does the executed cart still match signed intent",
    codes: [REASON_CART_DIVERGENCE, REASON_MERCHANT_SWAPPED],
  },
  {
    key: "scope",
    label: "Scope",
    note: "every line and the aggregate against the accumulated caveats",
    codes: [
      REASON_AMOUNT_EXCEEDED,
      REASON_CUMULATIVE_EXCEEDED,
      REASON_VELOCITY_EXCEEDED,
      REASON_MERCHANT_NOT_ALLOWED,
      REASON_MCC_NOT_ALLOWED,
      REASON_CATEGORY_NOT_ALLOWED,
      REASON_GEO_NOT_ALLOWED,
      REASON_MANDATE_EXPIRED,
      REASON_MANDATE_NOT_YET_VALID,
    ],
  },
  {
    key: "stepup",
    label: "Step-up",
    note: "is a human required for this amount",
    codes: [REASON_STEP_UP_REQUIRED],
  },
] as const;

export type PdpStageKey = (typeof PDP_STAGES)[number]["key"];

// ---------------------------------------------------------------------------------------
// Core objects — one per `to_dict()` in the kernel.
// ---------------------------------------------------------------------------------------

/** constraints.py :: Constraint.to_dict() */
export interface ConstraintDict {
  type: string;
  value?: number;
  values?: (string | number)[];
  count?: number;
  window_s?: number;
}

/** cart.py :: CartLine.to_dict() */
export interface CartLine {
  sku: string;
  description: string;
  amount: number;
  mcc: number;
  category: string;
  qty: number;
}

/** cart.py :: Cart.to_dict() */
export interface Cart {
  merchant: string;
  currency: string;
  lines: CartLine[];
}

/** cart.py :: CartDiff.to_dict() */
export interface CartDiff {
  diverged: boolean;
  added: CartLine[];
  removed: CartLine[];
  modified: { before: CartLine; after: CartLine }[];
  merchant_changed: [string, string] | null;
  intent_total: number;
  executed_total: number;
  delta: number;
  added_value: number;
  summary: string;
}

/** constraints.py :: Violation */
export interface Violation {
  reason: string;
  detail: string;
  constraint: ConstraintDict;
}

/** pdp.py :: Decision.to_dict() */
export interface DecisionRecord {
  txn_id: string;
  outcome: Outcome;
  headline: string;
  reason_codes: string[];
  violations: Violation[];
  verdict: string;
  liable_party: string;
  mandate_id: string;
  root_id: string;
  holder: string;
  intent_hash: string;
  cart_hash: string;
  diff: CartDiff;
  amount: number;
  amount_display: string;
  elapsed_ms: number;
  ledger_seq: number;
  ts: number;
  step_up_challenge_id: string | null;
  notes: string[];
}

/** mandate.py :: Mandate.to_dict(include_serialized=False) */
export interface MandateNode {
  mandate_id: string;
  root_id: string;
  holder: string;
  parent_id: string | null;
  depth: number;
  scope: ConstraintDict[];
  scope_display: string[];
  created_at: number;
  entailment_ms: number | null;
  caveat_count: number;
  serialized?: string;
}

/** entailment.py :: Counterexample.to_dict() */
export interface Counterexample {
  amount: number;
  amount_display: string;
  merchant: string;
  mcc: string;
  category: string;
  geo: string;
  ts: number;
  cumulative: number;
  violated: string[];
}

/** entailment.py :: EntailmentResult.to_dict() */
export interface EntailmentResult {
  entailed: boolean;
  elapsed_ms: number;
  solver_result: "sat" | "unsat" | "unknown";
  counterexample: Counterexample | null;
  child_scope: string[];
  parent_scope: string[];
}

/**
 * engine.py :: DelegationOutcome.to_dict() — exactly what the WebSocket feed emits.
 *
 * Note that a rejected hop has `mandate: null`, so the event alone does not name the
 * child. The feed degrades to "child agent"; enrich the event if you want the holder.
 */
export interface DelegationOutcomeEvent {
  accepted: boolean;
  entailment: EntailmentResult;
  naive_subset_check: boolean;
  naive_disagrees: boolean;
  mandate: MandateNode | null;
  title?: string;
  child_holder?: string;
  parent_id?: string;
}

/**
 * The enriched form the escalation scenario must return. `parent_id` and `child_holder`
 * are what let the console place a *rejected* hop on the graph — it has no mandate of its
 * own to read them from — so the scenario endpoint has to add them.
 */
export interface DelegationRecord extends DelegationOutcomeEvent {
  child_holder: string;
  parent_id: string;
}

/** revocation.py :: RevocationRecord.to_dict() */
export interface RevocationRecord {
  root_id: string;
  revoked_at: number;
  cause: string;
  actor: string;
  rows_written: number;
}

/** stepup.py :: Challenge.to_dict() */
export interface Challenge {
  challenge_id: string;
  root_id: string;
  mandate_id: string;
  cart_hash: string;
  amount: number;
  created_at: number;
  expires_at: number;
  satisfied: boolean;
  satisfied_at: number | null;
  method: string | null;
}

/** ledger.py :: LedgerEntry.to_dict() */
export interface LedgerEntry {
  seq: number;
  kind: string;
  payload: Record<string, unknown>;
  prev_hash: string;
  entry_hash: string;
  leaf_hash: string;
  ts: number;
}

/** ledger.py :: InclusionProof.to_dict() */
export interface InclusionProof {
  index: number;
  leaf_hash: string;
  root: string;
  tree_size: number;
  path: { side: "left" | "right"; hash: string }[];
}

// ---------------------------------------------------------------------------------------
// Underwriting. registry.py + exposure.py are the backend owners of these shapes.
// ---------------------------------------------------------------------------------------

export type RiskBand = "PRIME" | "STANDARD" | "WATCH" | "RESTRICTED";

export interface OperatorExposure {
  operator_id: string;
  name: string;
  registered_at: number;
  risk_score: number; // 0..100, higher is safer
  band: RiskBand;
  premium_bps: number;
  authorized_count: number;
  denied_count: number;
  step_up_count: number;
  divergence_count: number;
  injection_count: number;
  escalation_attempts: number;
  authorized_volume: number;
  modelled_exposure: number;
  expected_loss: number;
  covered: boolean;
}

export interface CurvePoint {
  score: number;
  covered_exposure: number;
  expected_loss: number;
  loss_rate_bps: number;
  operators: number;
}

export interface ExposureBook {
  as_of: number;
  model: string;
  cutoff_score: number;
  total_exposure: number;
  covered_exposure: number;
  declined_exposure: number;
  expected_loss: number;
  blended_loss_bps: number;
  band_premium_bps: Record<RiskBand, number>;
  operators: OperatorExposure[];
  /**
   * Optional. The console derives the cutoff curve from `operators` so the chart and the
   * table can never disagree, and so the cutoff stays draggable without a round trip.
   */
  curve?: CurvePoint[];
}

// ---------------------------------------------------------------------------------------
// Endpoint envelopes.
// ---------------------------------------------------------------------------------------

/** GET /api/state */
export interface AppStateResponse {
  mandates: MandateNode[];
  operators: OperatorExposure[];
  decisions: DecisionRecord[];
  ledger_root: string;
  ledger_size: number;
  revocations: RevocationRecord[];
  exposure: ExposureBook;
}

export const SCENARIOS = [
  "clean_purchase",
  "injection",
  "escalation",
  "kill_switch",
  "step_up",
] as const;
export type ScenarioName = (typeof SCENARIOS)[number];

/** Extra fields on the kill-switch scenario beyond RevocationRecord. */
export interface KillSwitchResult extends RevocationRecord {
  descendants_killed: number;
  deepest_depth: number;
  /** Measured wall clock from the revoke call to the deepest agent failing closed. */
  containment_ms: number;
  /** Honest bound: effect is guaranteed within this many seconds, not instantly. */
  discharge_ttl_s: number;
  before: DecisionRecord;
  after: DecisionRecord;
}

export interface StepUpResult {
  challenge: Challenge | null;
  first: DecisionRecord;
  second: DecisionRecord | null;
}

/**
 * POST /api/scenario/{name}
 *
 * One envelope, mostly-optional fields; which fields are populated is a function of the
 * scenario:
 *
 *   clean_purchase  intent_cart, executed_cart, governed, ungoverned, mandate
 *   injection       intent_cart, executed_cart, governed, ungoverned, mandate
 *   escalation      root, delegations, mandates
 *   kill_switch     chain, revocation
 *   step_up         intent_cart, executed_cart, step_up, governed, mandate
 */
export interface ScenarioResult {
  scenario: ScenarioName;
  label: string;
  narrative: string;
  ledger_root: string;
  ledger_size: number;

  intent_cart?: Cart;
  executed_cart?: Cart;
  governed?: DecisionRecord;
  ungoverned?: UngovernedRun;
  mandate?: MandateNode;

  root?: MandateNode;
  delegations?: DelegationRecord[];
  mandates?: MandateNode[];

  chain?: MandateNode[];
  revocation?: KillSwitchResult;

  step_up?: StepUpResult;
}

/** The counterfactual left-hand pane: what happens with no governance layer at all. */
export interface UngovernedRun {
  authorized: boolean;
  amount: number;
  amount_display: string;
  cart: Cart;
  note: string;
}

/** GET /api/evidence/{txn_id} — the Agent Evidence Package. */
export interface EvidencePackage {
  txn_id: string;
  issued_at: number;
  decision: DecisionRecord;
  mandate_chain: MandateNode[];
  intent_cart: Cart;
  executed_cart: Cart;
  diff: CartDiff;
  decision_chain: LedgerEntry[];
  inclusion_proof: InclusionProof | null;
  /** The server's own verification. The console recomputes it independently anyway. */
  proof_verified: boolean;
  chain_verified: boolean;
  chain_error: string | null;
  ledger_root: string;
  ledger_size: number;
  verdict: string;
  liable_party: string;
}

/** POST /api/revoke */
export interface RevokeResponse {
  rows_written: number;
  revoked_at: number;
  root_id?: string;
  cause?: string;
  actor?: string;
}

// ---------------------------------------------------------------------------------------
// WS /ws — messages are { kind, payload }.
// ---------------------------------------------------------------------------------------

export type FeedEvent =
  | { kind: "decision"; payload: DecisionRecord }
  | { kind: "delegation_accepted"; payload: DelegationOutcomeEvent }
  | { kind: "delegation_rejected"; payload: DelegationOutcomeEvent }
  | { kind: "revocation"; payload: RevocationRecord }
  | { kind: "mandate_granted"; payload: MandateNode }
  | { kind: "step_up"; payload: { challenge: Challenge | null; error: string | null } };

export type FeedKind = FeedEvent["kind"];
