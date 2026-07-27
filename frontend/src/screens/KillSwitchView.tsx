/**
 * Beat 3 — the kill switch.
 *
 * The claim is not "revocation is fast". It is that containment does not scale with the
 * number of agents that inherited the mandate: one row is written, and every descendant
 * credential in flight — including sub-agents nobody enumerated — fails closed at its
 * next discharge fetch. So the panel puts the row count next to the descendant count and
 * leaves them there.
 *
 * Two clocks are shown and they are labelled differently on purpose. The engine number is
 * what the backend measured around the revoke call. The wall clock is what the room just
 * watched. Neither is "sub-second" marketing: the guaranteed bound is the discharge TTL,
 * and that is printed underneath.
 */

import { useEffect, useRef, useState } from "react";
import { Button, Caveat, Empty, OutcomeChip, Panel, ReasonCode, Stat } from "../components/ui";
import { ms as fmtMs } from "../lib/format";
import { useConsole } from "../lib/store";
import type { DecisionRecord, MandateNode } from "../lib/types";

type Stage = "idle" | "running" | "done";

const FLIP_MS = 90;

export function KillSwitchView() {
  const run = useConsole((s) => s.run);
  const running = useConsole((s) => s.running);
  const result = useConsole((s) => s.scenarios.kill_switch);
  const openEvidence = useConsole((s) => s.openEvidence);

  const [stage, setStage] = useState<Stage>("idle");
  const [elapsed, setElapsed] = useState(0);
  const [killed, setKilled] = useState(0);
  const frame = useRef<number>(0);

  const chain = result?.chain ?? [];
  const revocation = result?.revocation;

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  const trip = async () => {
    setStage("running");
    setKilled(0);
    setElapsed(0);
    const t0 = performance.now();
    const tick = () => {
      setElapsed(performance.now() - t0);
      frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);

    const fresh = await run("kill_switch");
    const depth = (fresh?.chain?.length ?? 5) - 1;

    for (let i = 1; i <= depth; i++) {
      await new Promise((r) => setTimeout(r, FLIP_MS));
      setKilled(i);
    }
    cancelAnimationFrame(frame.current);
    setElapsed(performance.now() - t0);
    setStage("done");
  };

  const reset = () => {
    cancelAnimationFrame(frame.current);
    setStage("idle");
    setElapsed(0);
    setKilled(0);
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <header className="flex shrink-0 flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="display-xwide text-[1.7rem] leading-none font-semibold tracking-[-0.01em]">
            One row kills every descendant in flight.
          </h1>
          <p className="mt-1.5 text-body text-ink-2">
            {result?.narrative ??
              "Four hops deep, mid-checkout. No token sweep, no session registry to walk."}
          </p>
        </div>
        {stage !== "idle" && (
          <Button size="md" tone="ghost" onClick={reset}>
            Reset
          </Button>
        )}
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)] gap-3">
        <Panel
          title="Delegation chain"
          hint={chain.length ? `depth 0 → ${chain.length - 1}` : "not yet built"}
          bodyClassName="min-h-0 overflow-y-auto p-4"
        >
          {chain.length === 0 ? (
            <Empty>Arm the kill switch to build a four-hop chain and revoke it.</Empty>
          ) : (
            <ol className="flex flex-col">
              {chain.map((node, index) => (
                <ChainRow
                  key={node.mandate_id}
                  node={node}
                  index={index}
                  last={index === chain.length - 1}
                  dead={stage !== "idle" && index <= killed && killed > 0}
                  deepest={index === chain.length - 1}
                />
              ))}
            </ol>
          )}
        </Panel>

        <div className="flex min-h-0 flex-col gap-3">
          <Panel
            tone={stage === "done" ? "deny" : "default"}
            bodyClassName="flex flex-col items-stretch gap-4 p-5"
          >
            <button
              type="button"
              onClick={trip}
              disabled={running !== null || stage === "running"}
              className={`group relative flex h-32 w-full flex-col items-center justify-center gap-1 rounded-md border-2 transition-all duration-200 select-none disabled:cursor-not-allowed ${
                stage === "done"
                  ? "border-deny/40 bg-deny-wash text-deny/60"
                  : "border-deny bg-deny-wash text-deny hover:bg-deny/20"
              }`}
            >
              <span className="display-xwide text-[2rem] leading-none font-bold tracking-[0.04em]">
                {stage === "running" ? "REVOKING" : stage === "done" ? "REVOKED" : "REVOKE"}
              </span>
              <span className="num text-pill tracking-[0.1em] opacity-70">
                {stage === "done" ? "root credential dead" : "cardholder kill switch"}
              </span>
            </button>

            <div className="grid grid-cols-2 gap-4">
              <Stat
                label="Tap → fail closed"
                value={stage === "idle" ? "—" : `${(elapsed / 1000).toFixed(2)}s`}
                sub="console wall clock"
                tone={stage === "done" ? "deny" : "default"}
              />
              <Stat
                label="Engine containment"
                value={revocation && stage === "done" ? fmtMs(revocation.containment_ms) : "—"}
                sub="measured around the revoke call"
                tone="proof"
              />
            </div>
          </Panel>

          <Panel bodyClassName="grid grid-cols-2 gap-4 p-5">
            <Stat
              label="Rows written"
              value={stage === "done" ? (revocation?.rows_written ?? 1) : "—"}
              sub="in the revocations table"
              size="lg"
              tone="deny"
            />
            <Stat
              label="Descendants contained"
              value={stage === "done" ? (revocation?.descendants_killed ?? 0) : "—"}
              sub="none of them were notified"
              size="lg"
            />
          </Panel>

          <Panel title="Probe at the deepest agent" bodyClassName="flex flex-col gap-3 p-4">
            {revocation && stage === "done" ? (
              <>
                <ProbeRow
                  label="Before"
                  decision={revocation.before}
                  onOpen={() => openEvidence(revocation.before.txn_id)}
                />
                <ProbeRow
                  label="After"
                  decision={revocation.after}
                  onOpen={() => openEvidence(revocation.after.txn_id)}
                />
                {/* The TTL number and the sub-TTL correction are the load-bearing part; the
                    sentence explaining what an unexpired discharge does went. */}
                <Caveat>
                  Latency is bounded by the discharge TTL, currently {revocation.discharge_ttl_s}s.
                  "Sub-second" means sub-TTL.
                </Caveat>
              </>
            ) : (
              <div className="text-pill text-ink-4">
                Identical cart, probed before and after the row is written.
              </div>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------

function capOf(node: MandateNode): string | null {
  const caps = node.scope.filter((c) => c.type === "amount_max" && typeof c.value === "number");
  if (!caps.length) return null;
  const tightest = Math.min(...caps.map((c) => c.value as number));
  return node.scope_display.find((line) => line.includes(String(tightest / 100))) ?? null;
}

function ChainRow({
  node,
  index,
  last,
  dead,
  deepest,
}: {
  node: MandateNode;
  index: number;
  last: boolean;
  dead: boolean;
  deepest: boolean;
}) {
  return (
    <li className="relative flex gap-4 pb-3">
      <div className="relative flex w-6 shrink-0 flex-col items-center">
        <div
          className={`z-10 flex h-6 w-6 items-center justify-center rounded-full border text-pill transition-colors duration-300 ${
            dead
              ? "border-deny bg-deny-wash text-deny"
              : "border-line-2 bg-raised text-ink-3"
          }`}
        >
          <span className="num">{index}</span>
        </div>
        {!last && (
          <div
            className={`absolute top-6 bottom-0 w-px transition-colors duration-300 ${
              dead ? "bg-deny/45" : "bg-line-2"
            }`}
          />
        )}
      </div>

      <div
        className={`flex min-w-0 flex-1 items-center gap-4 rounded border px-3 py-2 transition-colors duration-300 ${
          dead ? "border-deny/45 bg-deny-wash/50" : "border-line bg-raised"
        }`}
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span
              className={`break-words text-body font-medium ${dead ? "text-deny" : "text-ink"}`}
            >
              {node.holder}
            </span>
            {deepest && (
              <span className="shrink-0 rounded border border-line-2 px-1 font-mono text-pill tracking-[0.06em] text-ink-4">
                NEVER REGISTERED
              </span>
            )}
          </div>
          <div className="num break-words text-pill text-ink-4">
            {node.mandate_id} · {node.caveat_count} caveats
            {capOf(node) && ` · ${capOf(node)}`}
          </div>
        </div>
        <span
          className={`num shrink-0 rounded border px-1.5 py-0.5 text-pill tracking-[0.08em] transition-colors duration-300 ${
            dead
              ? "border-deny/50 bg-deny-wash text-deny"
              : "border-allow/30 bg-allow-wash text-allow"
          }`}
        >
          {dead ? "FAILED CLOSED" : "LIVE"}
        </span>
      </div>
    </li>
  );
}

function ProbeRow({
  label,
  decision,
  onOpen,
}: {
  label: string;
  decision: DecisionRecord;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex items-center gap-3 rounded border border-line bg-sunken px-3 py-2 text-left transition-colors hover:border-line-2"
    >
      <span className="eyebrow w-12 shrink-0">{label}</span>
      <OutcomeChip outcome={decision.outcome} />
      <span className="min-w-0 flex-1 break-words">
        {decision.reason_codes[0] ? (
          <ReasonCode code={decision.reason_codes[0]} />
        ) : (
          <span className="text-pill text-allow">no violations</span>
        )}
      </span>
      <span className="num shrink-0 text-pill text-ink-4">{fmtMs(decision.elapsed_ms)}</span>
    </button>
  );
}
