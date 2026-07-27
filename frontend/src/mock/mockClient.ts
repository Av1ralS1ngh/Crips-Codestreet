/* =====================================================================================
 * MOCK TRANSPORT — NOT THE BACKEND.
 *
 * Swapped out wholesale by the single flag in lib/transport.ts. Nothing else in the
 * console knows this file exists.
 *
 * What is real in here: every decision, entailment result, counterexample, mandate,
 * ledger entry and Merkle inclusion proof in `fixtures.json` was produced by running the
 * actual `CaveatEngine` (see scratchpad/gen_fixtures.py). They are recorded kernel
 * output, not invented JSON, so the shapes cannot drift from the API and the inclusion
 * proof genuinely verifies in the browser.
 *
 * What is synthetic: the nine-operator underwriting book, and the ledger roots minted
 * after the first scenario run (hashed forward from the recorded root so the chain still
 * behaves like a chain). Round 2 permits mock datasets; the console labels this mode
 * MOCK in the header at all times so nobody can mistake it for live.
 * ===================================================================================== */

import type { ConnStatus, Transport } from "../lib/transport";
import type {
  AppStateResponse,
  Challenge,
  DecisionRecord,
  EvidencePackage,
  FeedEvent,
  InclusionProof,
  LedgerEntry,
  MandateNode,
  RevokeResponse,
  ScenarioName,
  ScenarioResult,
} from "../lib/types";
import { mockDegrade, mockPlumblineState } from "./plumblineMock";
import raw from "./fixtures.json";

interface Fixtures {
  clean_purchase: ScenarioResult;
  injection: ScenarioResult;
  escalation: ScenarioResult;
  kill_switch: ScenarioResult;
  step_up: ScenarioResult;
  evidence_sample: EvidencePackage & { ledger_entries: LedgerEntry[] };
  ledger_entry_sample: LedgerEntry[];
  mandate_tree_sample: MandateNode[];
  exposure: AppStateResponse["exposure"];
  operators: AppStateResponse["operators"];
}

const fx = raw as unknown as Fixtures;

const clone = <T>(value: T): T => structuredClone(value);

const sleep = (msec: number) => new Promise<void>((resolve) => setTimeout(resolve, msec));

/** Deterministic forward-hash so the mock's ledger root moves like a real one. */
async function advanceRoot(previous: string, salt: string): Promise<string> {
  const bytes = new TextEncoder().encode(`${previous}:${salt}`);
  const digest = await crypto.subtle.digest("SHA-256", bytes as BufferSource);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

let txnCounter = 0;
function freshTxnId(): string {
  txnCounter += 1;
  return `txn_${(0x1a2b3c + txnCounter * 0x9e37).toString(16).slice(-12)}`;
}

export function createMockTransport(): Transport {
  const listeners = new Set<(event: FeedEvent) => void>();

  const state: AppStateResponse = {
    mandates: clone(fx.mandate_tree_sample),
    operators: clone(fx.operators),
    decisions: [],
    ledger_root: fx.evidence_sample.ledger_root,
    ledger_size: fx.mandate_tree_sample.length + 1,
    revocations: [],
    exposure: clone(fx.exposure),
  };

  const decisionsById = new Map<string, DecisionRecord>();

  const emit = (event: FeedEvent) => {
    for (const listener of listeners) listener(event);
  };

  /** Give every replayed decision a fresh id and wall-clock so the feed reads as live. */
  const stamp = (decision: DecisionRecord): DecisionRecord => {
    const next: DecisionRecord = {
      ...clone(decision),
      txn_id: freshTxnId(),
      ts: Math.floor(Date.now() / 1000),
    };
    decisionsById.set(next.txn_id, next);
    state.decisions = [next, ...state.decisions].slice(0, 200);
    return next;
  };

  const bumpLedger = async (salt: string, entries: number) => {
    state.ledger_root = await advanceRoot(state.ledger_root, salt);
    state.ledger_size += entries;
  };

  const applyOperatorSignal = (decision: DecisionRecord) => {
    const operator = state.operators.find((o) => o.operator_id === decision.holder);
    if (!operator) return;
    if (decision.outcome === "ALLOW") operator.authorized_count += 1;
    if (decision.outcome === "DENY") operator.denied_count += 1;
    if (decision.outcome === "STEP_UP") operator.step_up_count += 1;
    if (decision.diff.diverged) operator.divergence_count += 1;
    if (decision.verdict === "INJECTION_COMPROMISE") operator.injection_count += 1;
  };

  return {
    mode: "mock",

    async getState() {
      await sleep(60);
      return clone(state);
    },

    async runScenario(name: ScenarioName): Promise<ScenarioResult> {
      const fixture = clone(fx[name]) as ScenarioResult;
      await sleep(90);

      if (name === "escalation") {
        for (const delegation of fixture.delegations ?? []) {
          if (delegation.mandate) state.mandates.push(delegation.mandate);
          emit({
            kind: delegation.accepted ? "delegation_accepted" : "delegation_rejected",
            payload: delegation,
          });
          await sleep(140);
        }
        await bumpLedger("escalation", fixture.delegations?.length ?? 0);
      } else if (name === "kill_switch") {
        const revocation = fixture.revocation;
        if (revocation) {
          const before = stamp(revocation.before);
          emit({ kind: "decision", payload: before });
          await sleep(160);
          state.revocations = [
            {
              root_id: revocation.root_id,
              revoked_at: Math.floor(Date.now() / 1000),
              cause: revocation.cause,
              actor: revocation.actor,
              rows_written: revocation.rows_written,
            },
            ...state.revocations,
          ];
          emit({ kind: "revocation", payload: state.revocations[0] });
          await sleep(120);
          const after = stamp(revocation.after);
          applyOperatorSignal(after);
          emit({ kind: "decision", payload: after });
          revocation.before = before;
          revocation.after = after;
          await bumpLedger("kill_switch", 3);
        }
      } else if (name === "step_up" && fixture.step_up) {
        const first = stamp(fixture.step_up.first);
        fixture.step_up.first = first;
        applyOperatorSignal(first);
        emit({ kind: "decision", payload: first });
        await sleep(150);
        if (fixture.step_up.challenge) {
          emit({ kind: "step_up", payload: { challenge: fixture.step_up.challenge, error: null } });
        }
        await sleep(150);
        if (fixture.step_up.second) {
          const second = stamp(fixture.step_up.second);
          fixture.step_up.second = second;
          fixture.governed = second;
          applyOperatorSignal(second);
          emit({ kind: "decision", payload: second });
        }
        await bumpLedger("step_up", 3);
      } else if (fixture.governed) {
        const decision = stamp(fixture.governed);
        fixture.governed = decision;
        applyOperatorSignal(decision);
        emit({ kind: "decision", payload: decision });
        await bumpLedger(name, 1);
      }

      fixture.ledger_root = state.ledger_root;
      fixture.ledger_size = state.ledger_size;
      return fixture;
    },

    async revoke(rootId: string, cause: string): Promise<RevokeResponse> {
      await sleep(40);
      const record = {
        root_id: rootId,
        revoked_at: Math.floor(Date.now() / 1000),
        cause,
        actor: "cardholder",
        // The number this whole panel exists to show. It is 1, and it stays 1.
        rows_written: 1,
      };
      state.revocations = [record, ...state.revocations];
      await bumpLedger("revocation", 1);
      emit({ kind: "revocation", payload: record });
      return record;
    },

    async satisfyStepUp(challengeId: string) {
      await sleep(80);
      const source = fx.step_up.step_up?.challenge ?? null;
      const challenge: Challenge | null = source
        ? {
            ...clone(source),
            challenge_id: challengeId,
            satisfied: true,
            satisfied_at: Math.floor(Date.now() / 1000),
            method: "passkey",
          }
        : null;
      emit({ kind: "step_up", payload: { challenge, error: null } });
      return { challenge, error: null };
    },

    async getEvidence(txnId: string): Promise<EvidencePackage> {
      await sleep(110);
      const sample = clone(fx.evidence_sample);
      const decision = decisionsById.get(txnId);
      if (!decision) return { ...sample, txn_id: txnId };

      const chain: MandateNode[] = [];
      let cursor: MandateNode | undefined = state.mandates.find(
        (m) => m.mandate_id === decision.mandate_id,
      );
      while (cursor) {
        chain.unshift(cursor);
        const parentId: string | null = cursor.parent_id;
        cursor = parentId
          ? state.mandates.find((m) => m.mandate_id === parentId)
          : undefined;
      }

      return {
        ...sample,
        txn_id: txnId,
        issued_at: Math.floor(Date.now() / 1000),
        decision,
        diff: decision.diff,
        verdict: decision.verdict,
        liable_party: decision.liable_party,
        mandate_chain: chain.length ? chain : sample.mandate_chain,
        // A real, recorded inclusion proof: it verifies in the browser, which is the
        // point of showing it. It attests the recorded ledger, not this replayed txn.
        inclusion_proof: sample.inclusion_proof as InclusionProof | null,
        ledger_root: sample.ledger_root,
      };
    },

    async getChain(mandateId: string): Promise<MandateNode[]> {
      await sleep(50);
      const chain: MandateNode[] = [];
      let cursor: MandateNode | undefined = state.mandates.find(
        (m) => m.mandate_id === mandateId,
      );
      while (cursor) {
        chain.unshift(cursor);
        const parentId: string | null = cursor.parent_id;
        cursor = parentId ? state.mandates.find((m) => m.mandate_id === parentId) : undefined;
      }
      return chain;
    },

    getPlumblineState: mockPlumblineState,

    runDegrade: mockDegrade,

    connect(onEvent: (event: FeedEvent) => void, onStatus: (status: ConnStatus) => void) {
      listeners.add(onEvent);
      onStatus("open");
      return () => {
        listeners.delete(onEvent);
        onStatus("closed");
      };
    },
  };
}
