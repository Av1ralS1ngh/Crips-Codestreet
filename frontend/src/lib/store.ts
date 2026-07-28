/**
 * Console state. One store, because the whole console is one instrument: the feed, the
 * attack view and the evidence drawer are all views onto the same decision stream.
 */

import { create } from "zustand";
import { createMockTransport } from "../mock/mockClient";
import { createLiveTransport } from "./liveClient";
import { DEFAULT_MODE, type ConnStatus, type Transport, type TransportMode } from "./transport";
import type {
  AppStateResponse,
  DecisionRecord,
  EvidencePackage,
  FeedEvent,
  ScenarioName,
  ScenarioResult,
} from "./types";
import type { DegradeRun, ManifestBody, PlumblineState } from "./plumbline";

/**
 * The PLUMBLINE beats, then the governance kernel as one screen behind them. The kernel is
 * supporting evidence for the disclosure argument, not the headline — it is the reason an
 * unremovable caveat on instrument selection is enforceable at all.
 *
 * `degrade` sits immediately after `refusal` because the two answer opposite halves of the
 * same question: what happens when a claim cannot be supported, and what happens when no
 * claim arrives at all. `controls` sits last of the valuation beats, after the attribution
 * close, because it is the beat where the argument stops being ours.
 */
export type ScreenKey =
  | "landing"
  | "overstatement"
  | "receipt"
  | "omission"
  | "refusal"
  | "degrade"
  | "attribution"
  | "controls"
  | "kernel";

/** Sub-tabs inside the kernel screen. Each is a beat the kernel suite already proves. */
export type KernelTab = "attack" | "delegation" | "kill" | "exposure";

export interface FeedItem {
  id: number;
  at: number;
  event: FeedEvent;
}

export type UiScale = "s" | "m" | "l";

/**
 * The root size the whole rem scale hangs off. 16px is the default so the 0.75rem floor is
 * 12px; S and L move the entire scale together and neither lets a size fall through it.
 */
const SCALE_PX: Record<UiScale, string> = { s: "15px", m: "16px", l: "18px" };

let feedSeq = 0;

interface ConsoleState {
  mode: TransportMode;
  transport: Transport;
  conn: ConnStatus;
  disconnect: (() => void) | null;

  state: AppStateResponse | null;
  loadError: string | null;

  plumbline: PlumblineState | null;
  plumblineError: string | null;
  /** Which instrument the overstatement screen is valuing. Null means the headline one. */
  instrumentId: string | null;

  degrade: DegradeRun | null;
  degradeError: string | null;
  degradeRunning: boolean;

  feed: FeedItem[];
  screen: ScreenKey;
  kernelTab: KernelTab;
  scale: UiScale;

  scenarios: Partial<Record<ScenarioName, ScenarioResult>>;
  running: ScenarioName | null;

  evidenceTxn: string | null;
  evidence: EvidencePackage | null;
  evidenceLoading: boolean;

  init: () => Promise<void>;
  setMode: (mode: TransportMode) => Promise<void>;
  setScreen: (screen: ScreenKey) => void;
  setKernelTab: (tab: KernelTab) => void;
  setInstrument: (instrumentId: string) => void;
  setScale: (scale: UiScale) => void;
  refresh: () => Promise<void>;
  loadPlumbline: () => Promise<void>;
  runDegrade: () => Promise<void>;
  run: (name: ScenarioName) => Promise<ScenarioResult | null>;
  revoke: (rootId: string, cause: string) => Promise<number>;
  openEvidence: (txnId: string) => Promise<void>;
  closeEvidence: () => void;
  clearFeed: () => void;
}

function transportFor(mode: TransportMode): Transport {
  return mode === "live" ? createLiveTransport() : createMockTransport();
}

export const useConsole = create<ConsoleState>((set, get) => ({
  mode: DEFAULT_MODE,
  transport: transportFor(DEFAULT_MODE),
  conn: "closed",
  disconnect: null,

  state: null,
  loadError: null,

  plumbline: null,
  plumblineError: null,
  instrumentId: null,

  degrade: null,
  degradeError: null,
  degradeRunning: false,

  feed: [],
  // A judge lands on the explanation, not on beat 01 of a demo.
  screen: "landing",
  kernelTab: "attack",
  scale: "m",

  scenarios: {},
  running: null,

  evidenceTxn: null,
  evidence: null,
  evidenceLoading: false,

  async init() {
    get().disconnect?.();
    const { transport } = get();

    const stop = transport.connect(
      (event: FeedEvent) => {
        feedSeq += 1;
        set((prev) => ({
          feed: [{ id: feedSeq, at: Date.now(), event }, ...prev.feed].slice(0, 120),
        }));
        if (event.kind === "decision") {
          set((prev) =>
            prev.state
              ? {
                  state: {
                    ...prev.state,
                    decisions: [event.payload, ...prev.state.decisions].slice(0, 200),
                  },
                }
              : prev,
          );
        }
      },
      (conn: ConnStatus) => set({ conn }),
    );

    set({ disconnect: stop });
    await Promise.all([get().refresh(), get().loadPlumbline()]);
  },

  async setMode(mode: TransportMode) {
    if (mode === get().mode) return;
    get().disconnect?.();
    set({
      mode,
      transport: transportFor(mode),
      state: null,
      plumbline: null,
      plumblineError: null,
      instrumentId: null,
      degrade: null,
      degradeError: null,
      feed: [],
      scenarios: {},
      evidence: null,
      evidenceTxn: null,
      loadError: null,
      disconnect: null,
    });
    await get().init();
  },

  setScreen: (screen) => set({ screen }),

  setKernelTab: (kernelTab) => set({ kernelTab }),

  setInstrument: (instrumentId) => set({ instrumentId }),

  setScale(scale) {
    document.documentElement.style.setProperty("--ui-scale", SCALE_PX[scale]);
    set({ scale });
  },

  async refresh() {
    try {
      const next = await get().transport.getState();
      set({ state: next, loadError: null });
    } catch (err) {
      set({ loadError: err instanceof Error ? err.message : String(err) });
    }
  },

  /**
   * Kept separate from `refresh` and separately errored. The PLUMBLINE endpoint is landing in
   * parallel with this console, so a live backend that does not serve it yet must degrade
   * to a named banner on five screens rather than blanking the kernel screens too.
   */
  async loadPlumbline() {
    try {
      const next = await get().transport.getPlumblineState();
      set({ plumbline: next, plumblineError: null });
    } catch (err) {
      set({
        plumbline: null,
        plumblineError: err instanceof Error ? err.message : String(err),
      });
    }
  },

  /**
   * Runs the graceful-degrade matrix. Separately errored again, and never during `init`:
   * it appends to a transparency log, so it is a run rather than a read, and the screen
   * asks for it when a presenter arrives at that beat.
   */
  async runDegrade() {
    if (get().degradeRunning) return;
    set({ degradeRunning: true });
    try {
      const next = await get().transport.runDegrade();
      set({ degrade: next, degradeError: null });
    } catch (err) {
      set({
        degrade: null,
        degradeError: err instanceof Error ? err.message : String(err),
      });
    } finally {
      set({ degradeRunning: false });
    }
  },

  async run(name: ScenarioName) {
    set({ running: name });
    try {
      const result = await get().transport.runScenario(name);
      set((prev) => ({ scenarios: { ...prev.scenarios, [name]: result } }));
      await get().refresh();
      return result;
    } catch (err) {
      set({ loadError: err instanceof Error ? err.message : String(err) });
      return null;
    } finally {
      set({ running: null });
    }
  },

  async revoke(rootId: string, cause: string) {
    const response = await get().transport.revoke(rootId, cause);
    await get().refresh();
    return response.rows_written;
  },

  async openEvidence(txnId: string) {
    set({ evidenceTxn: txnId, evidenceLoading: true, evidence: null });
    try {
      const pkg = await get().transport.getEvidence(txnId);
      set({ evidence: pkg, evidenceLoading: false });
    } catch (err) {
      set({
        evidenceLoading: false,
        loadError: err instanceof Error ? err.message : String(err),
      });
    }
  },

  closeEvidence: () => set({ evidenceTxn: null, evidence: null, evidenceLoading: false }),

  clearFeed: () => set({ feed: [] }),
}));

/**
 * The instrument the overstatement screen is valuing. Falls back to the headline one so
 * the screen never renders empty because a stale id survived a transport switch.
 */
export function activeInstrument(
  plumbline: PlumblineState | null,
  instrumentId: string | null,
): PlumblineState["valuation"]["instruments"][number] | null {
  if (!plumbline) return null;
  const { instruments, headline_instrument } = plumbline.valuation;
  return (
    instruments.find((i) => i.instrument_id === instrumentId) ??
    instruments.find((i) => i.instrument_id === headline_instrument) ??
    instruments[0] ??
    null
  );
}

/**
 * One instrument's signed manifest body, which is what the browser verifier needs.
 *
 * The whole body rather than just its benefits: the verifier checks that a witness names
 * the manifest it is being checked against and that manifest and cart share a currency, and
 * it cannot do either from a benefit list. Returning null when the instrument is unknown
 * keeps that a visible absence rather than an empty manifest that verifies nothing and says
 * so in no way a reader would notice.
 */
export function manifestFor(
  plumbline: PlumblineState | null,
  instrumentId: string,
): ManifestBody | null {
  return plumbline?.instruments.find((i) => i.instrument_id === instrumentId)?.manifest ?? null;
}

/** Benefits alone, for display. Verification wants `manifestFor`. */
export function benefitsFor(plumbline: PlumblineState | null, instrumentId: string) {
  return manifestFor(plumbline, instrumentId)?.benefits ?? [];
}

/** Decisions only, newest first — what the feed rail and evidence drawer both want. */
export function decisionsFromFeed(feed: FeedItem[]): { item: FeedItem; decision: DecisionRecord }[] {
  const out: { item: FeedItem; decision: DecisionRecord }[] = [];
  for (const item of feed) {
    if (item.event.kind === "decision") out.push({ item, decision: item.event.payload });
  }
  return out;
}
