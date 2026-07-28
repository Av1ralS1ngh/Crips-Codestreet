/**
 * Kernel, tab 3 — the kill switch.
 *
 * The claim is not "revocation is fast". It is that containment does not scale with the
 * number of agents that inherited the mandate: one row is written, and every descendant
 * credential in flight, including sub-agents nobody enumerated, fails closed at its next
 * discharge fetch. So the panel puts the row count next to the descendant count and leaves
 * them there.
 *
 * Two clocks are shown and they are labelled differently on purpose. The engine number is
 * what the backend measured around the revoke call. The wall clock is what the room just
 * watched. Neither is "sub-second" marketing: the guaranteed bound is the discharge TTL,
 * and that is printed underneath.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { SquarePanel } from "../components/plumblineUi";
import { Button, Caveat, ReasonCode } from "../components/ui";
import { money, ms as fmtMs } from "../lib/format";
import { useConsole } from "../lib/store";
import type { DecisionRecord } from "../lib/types";

type Stage = "idle" | "running" | "done";

const FLIP_MS = 90;
const COLUMNS = "grid-cols-[64px_minmax(0,2fr)_minmax(0,1fr)_104px]";

export function KillSwitchView() {
  const run = useConsole((s) => s.run);
  const running = useConsole((s) => s.running);
  const result = useConsole((s) => s.scenarios.kill_switch);
  const openEvidence = useConsole((s) => s.openEvidence);

  // Seeded from the store for the same reason as the injection tab: a tab switch unmounts
  // this view, and a revoked chain must still read as revoked when a judge comes back.
  const [stage, setStage] = useState<Stage>(() => (result ? "done" : "idle"));
  const [elapsed, setElapsed] = useState(0);
  const [killed, setKilled] = useState(() => result?.chain?.length ?? 0);
  const frame = useRef<number>(0);

  const chain = result?.chain ?? [];
  const revocation = result?.revocation;
  const probeAmount = revocation?.before.amount ?? null;

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
    const rows = fresh?.chain?.length ?? 5;

    for (let i = 1; i <= rows; i++) {
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

  const done = stage === "done";

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-5">
        <div className="min-w-0">
          <p className="text-[1.0625rem] leading-[1.6] font-bold text-ink">
            One row, four hops.
          </p>
          <p className="mt-1 max-w-[62ch] text-[0.90625rem] leading-[1.55] font-normal text-ink-2">
            {result?.narrative ??
              "Four hops deep, mid-checkout. No token sweep, no session registry to walk."}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2.5">
          <button
            type="button"
            onClick={() => void trip()}
            disabled={running !== null || stage === "running"}
            className={`num border px-6 py-[11px] text-[0.84375rem] font-semibold tracking-[0.08em] transition-colors duration-[180ms] disabled:cursor-not-allowed ${
              done
                ? "border-warning bg-warning-row text-warning"
                : "border-warning bg-white text-warning hover:bg-warning hover:text-white"
            }`}
          >
            {stage === "running" ? "REVOKING" : done ? "REVOKED" : "REVOKE"}
          </button>
          {stage !== "idle" && (
            <Button size="md" tone="ghost" onClick={reset}>
              Reset
            </Button>
          )}
        </div>
      </header>

      {chain.length === 0 ? (
        <div className="border border-gray-03 bg-white px-[22px] py-10 text-center text-body text-ink-3">
          Arm the kill switch to build a four-hop chain and revoke it at the root.
        </div>
      ) : (
        <>
          <SquarePanel
            title="Revocation at the root"
            right={
              <span className="num text-[0.71875rem] font-normal text-ink-4">
                depth 0 → {chain.length - 1} · one row in the revocations table
              </span>
            }
          >
            <div className={`grid ${COLUMNS} gap-4 border-b border-gray-02 px-[22px] py-3.5`}>
              <span className="colhead">Hop</span>
              <span className="colhead">Holder</span>
              <span className="colhead">Probe</span>
              <span className="colhead text-right">After</span>
            </div>

            {chain.map((node, index) => {
              const dead = stage !== "idle" && index < killed;
              const deepest = index === chain.length - 1;
              return (
                <div
                  key={node.mandate_id}
                  className={`grid ${COLUMNS} items-baseline gap-4 border-b border-gray-02 px-[22px] py-4 transition-colors duration-[180ms] ${
                    dead ? "bg-warning-row" : ""
                  }`}
                >
                  <span className="num text-[0.8125rem] text-ink-4">{index}</span>
                  <span className="flex min-w-0 flex-col gap-1">
                    <span className="flex flex-wrap items-baseline gap-2">
                      <span
                        className={`text-[0.9375rem] leading-[1.4] break-words ${
                          dead ? "font-bold text-warning" : "text-ink"
                        }`}
                      >
                        {node.holder}
                      </span>
                      {deepest && (
                        <span className="num rounded-[3px] bg-gray-02 px-[9px] py-[3px] text-[0.71875rem] text-ink-3">
                          NEVER REGISTERED
                        </span>
                      )}
                    </span>
                    <span className="num text-[0.71875rem] break-words text-ink-4">
                      {node.mandate_id} · {node.caveat_count} caveats
                    </span>
                  </span>
                  <span className="num text-[0.8125rem] break-words text-ink-3">
                    {index === 0
                      ? "root"
                      : probeAmount === null
                        ? "authorise"
                        : `authorise ${money(probeAmount)}`}
                  </span>
                  <span
                    className={`num text-right text-[0.75rem] font-medium tracking-[0.06em] ${
                      dead ? "text-warning" : "text-success"
                    }`}
                  >
                    {dead ? (index === 0 ? "REVOKED" : "DENIED") : "LIVE"}
                  </span>
                </div>
              );
            })}

            <div className="flex flex-wrap items-baseline justify-between gap-4 border-t border-gray-03 bg-gray-01 px-[22px] py-[18px]">
              <span className="text-[0.90625rem] font-normal text-ink-2">
                Propagation to the deepest agent
              </span>
              <span className="num text-[1.0625rem] font-semibold text-navy">
                {revocation && done ? fmtMs(revocation.containment_ms) : "—"}
              </span>
            </div>
          </SquarePanel>

          <Caveat>
            Latency is bounded by the discharge TTL, currently{" "}
            {revocation?.discharge_ttl_s ?? "—"}s. "Sub-second" means sub-TTL.
          </Caveat>

          <div className="grid grid-cols-2 gap-6">
            <SquarePanel title="Containment">
              <div className="grid grid-cols-2 gap-x-6 gap-y-6 px-[22px] py-5">
                <Figure
                  label="Rows written"
                  value={done ? (revocation?.rows_written ?? 1) : "—"}
                  note="in the revocations table"
                  tone="text-warning"
                />
                <Figure
                  label="Descendants contained"
                  value={done ? (revocation?.descendants_killed ?? 0) : "—"}
                  note="none of them were notified"
                />
                <Figure
                  label="Engine containment"
                  value={revocation && done ? fmtMs(revocation.containment_ms) : "—"}
                  note="measured around the revoke call"
                  tone="text-blue"
                />
                <Figure
                  label="Tap to fail closed"
                  value={elapsed > 0 ? `${(elapsed / 1000).toFixed(2)}s` : "—"}
                  note="console wall clock"
                />
              </div>
            </SquarePanel>

            <SquarePanel title="Probe at the deepest agent">
              <div className="flex flex-col gap-3 px-[22px] py-5">
                {revocation && done ? (
                  <>
                    <ProbeRow
                      label="Before"
                      decision={revocation.before}
                      onOpen={() => void openEvidence(revocation.before.txn_id)}
                    />
                    <ProbeRow
                      label="After"
                      decision={revocation.after}
                      onOpen={() => void openEvidence(revocation.after.txn_id)}
                    />
                  </>
                ) : (
                  <p className="text-[0.90625rem] leading-[1.55] font-normal text-ink-3">
                    Identical cart, probed before and after the row is written.
                  </p>
                )}
              </div>
            </SquarePanel>
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------

function Figure({
  label,
  value,
  note,
  tone = "text-navy",
}: {
  label: string;
  value: ReactNode;
  note: string;
  tone?: string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <span className="colhead">{label}</span>
      <span className={`num text-[1.75rem] leading-none font-semibold ${tone}`}>{value}</span>
      <span className="text-[0.8125rem] leading-[1.45] break-words text-ink-4">{note}</span>
    </div>
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
  const denied = decision.outcome === "DENY";
  return (
    <button
      type="button"
      onClick={onOpen}
      className={`flex flex-wrap items-center gap-3 border px-[18px] py-3.5 text-left transition-colors duration-[180ms] ${
        denied ? "border-warning bg-warning-row" : "border-gray-03 bg-white hover:border-blue"
      }`}
    >
      <span className="colhead w-14 shrink-0">{label}</span>
      <span
        className={`num shrink-0 text-[0.9375rem] font-semibold ${denied ? "text-warning" : "text-success"}`}
      >
        {decision.outcome}
      </span>
      {decision.reason_codes[0] ? (
        <ReasonCode code={decision.reason_codes[0]} />
      ) : (
        <span className="text-[0.84375rem] text-ink-3">no violations</span>
      )}
      <span className="num ml-auto shrink-0 text-[0.71875rem] text-ink-4">
        {fmtMs(decision.elapsed_ms)}
      </span>
    </button>
  );
}
