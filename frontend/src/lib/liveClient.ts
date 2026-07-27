/**
 * Live transport: the real `caveat.api` over same-origin HTTP + WebSocket.
 *
 * Vite proxies /api and /ws to the backend in dev, so nothing here needs an absolute
 * URL, CORS, or a configured host.
 */

import type { Transport } from "./transport";
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

/** scenarios.py names it; the route takes the same name. Never inlined at the call site. */
const SCENARIO_GRACEFUL_DEGRADE = "graceful_degrade";

async function json<T>(input: string, init?: RequestInit): Promise<T> {
  const res = await fetch(input, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${init?.method ?? "GET"} ${input} -> ${res.status} ${body.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

const RECONNECT_MS = 1_500;

export function createLiveTransport(): Transport {
  return {
    mode: "live",

    getState: () => json<AppStateResponse>("/api/state"),

    runScenario: (name: ScenarioName) =>
      json<ScenarioResult>(`/api/scenario/${name}`, { method: "POST" }),

    revoke: (rootId: string, cause: string) =>
      json<RevokeResponse>("/api/revoke", {
        method: "POST",
        body: JSON.stringify({ root_id: rootId, cause }),
      }),

    satisfyStepUp: (challengeId: string) =>
      json<{ challenge: Challenge | null; error: string | null }>(
        `/api/stepup/${challengeId}`,
        { method: "POST" },
      ),

    getEvidence: (txnId: string) => json<EvidencePackage>(`/api/evidence/${txnId}`),

    getChain: (mandateId: string) => json<MandateNode[]>(`/api/mandates/${mandateId}/chain`),

    // Served by `caveat.api :: plumbline_state`, which returns `plumbline.console.build_state` —
    // the same function that produced `src/mock/plumblineFixtures.json`. Mock and live are one
    // builder and two transports, and a backend test diffs the two payloads, so switching
    // this toggle mid-demo cannot change a card, a rate, a witness or a root.
    getPlumblineState: () => json<PlumblineState>("/api/plumbline/state"),

    // The scenario route exists today. Its response wraps the run, and the wrapper is
    // unwrapped here rather than in the store so both transports hand the console the
    // same object.
    async runDegrade() {
      const body = await json<{ scenario: string; result: DegradeRun }>(
        `/api/plumbline/scenario/${SCENARIO_GRACEFUL_DEGRADE}`,
        { method: "POST" },
      );
      return body.result;
    },

    connect(onEvent, onStatus) {
      let socket: WebSocket | null = null;
      let retry: ReturnType<typeof setTimeout> | null = null;
      let closed = false;

      const open = () => {
        if (closed) return;
        onStatus("connecting");
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        socket = new WebSocket(`${proto}//${location.host}/ws`);

        socket.onopen = () => onStatus("open");
        socket.onmessage = (event) => {
          try {
            const parsed = JSON.parse(event.data as string) as FeedEvent;
            if (parsed && typeof parsed.kind === "string") onEvent(parsed);
          } catch {
            // A malformed frame is a backend bug, not a reason to tear down the feed.
          }
        };
        socket.onerror = () => onStatus("error");
        socket.onclose = () => {
          onStatus("closed");
          if (!closed) retry = setTimeout(open, RECONNECT_MS);
        };
      };

      open();

      return () => {
        closed = true;
        if (retry) clearTimeout(retry);
        socket?.close();
      };
    },
  };
}
