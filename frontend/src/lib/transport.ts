/**
 * The one seam between the console and the backend.
 *
 * THE SINGLE FLAG:  VITE_CAVEAT_TRANSPORT=live   -> talk to caveat.api
 *                   anything else (default)      -> the recorded mock transport
 *
 *   npm run dev        # mock — runs with no backend at all
 *   npm run dev:live   # live — expects uvicorn on :8000, proxied by vite
 *
 * The mode is also switchable at runtime from the header badge, because on stage the
 * failure you want is "click one thing" rather than "restart the dev server". Whichever
 * mode is active is always displayed; the console never quietly pretends mock data is
 * live data.
 */

import type {
  AppStateResponse,
  Challenge,
  EvidencePackage,
  FeedEvent,
  MandateNode,
  RevokeResponse,
  ScenarioName,
  ScenarioResult,
} from "./types";
import type { DegradeRun, PlumblineState } from "./plumbline";

export type TransportMode = "mock" | "live";

export type ConnStatus = "connecting" | "open" | "closed" | "error";

export interface Transport {
  readonly mode: TransportMode;
  getState(): Promise<AppStateResponse>;
  runScenario(name: ScenarioName): Promise<ScenarioResult>;
  revoke(rootId: string, cause: string): Promise<RevokeResponse>;
  satisfyStepUp(
    challengeId: string,
  ): Promise<{ challenge: Challenge | null; error: string | null }>;
  getEvidence(txnId: string): Promise<EvidencePackage>;
  getChain(mandateId: string): Promise<MandateNode[]>;
  /**
   * The five PLUMBLINE beats in one envelope. They are five views of one corpus, and a judge
   * switching screens must never see two screens disagree because two fetches interleaved.
   */
  getPlumblineState(): Promise<PlumblineState>;
  /**
   * The graceful-degrade matrix, from `plumbline.scenarios`. A scenario rather than part of
   * the envelope above because it is a run rather than a state: it emits log entries, and
   * on a fixed clock every run returns identical bytes — so the mock recording and the
   * live route answer the same, and re-running it on stage is a demonstration rather than
   * a risk.
   */
  runDegrade(): Promise<DegradeRun>;
  /** Subscribe to the live feed. Returns an unsubscribe. */
  connect(onEvent: (event: FeedEvent) => void, onStatus: (status: ConnStatus) => void): () => void;
}

export const DEFAULT_MODE: TransportMode =
  import.meta.env.VITE_CAVEAT_TRANSPORT === "live" ? "live" : "mock";
