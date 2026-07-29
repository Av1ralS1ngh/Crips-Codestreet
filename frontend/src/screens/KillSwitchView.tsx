/**
 * Kernel, tab 3 — one row, four hops.
 *
 * The claim is not "revocation is fast". It is that containment does not scale with the
 * number of agents that inherited the mandate: one row is written, and every descendant
 * credential in flight, including sub-agents nobody enumerated, fails closed at its next
 * discharge fetch.
 *
 * The figure under the table is the engine's own measurement around the revoke call, not
 * the console's wall clock, and it renders only once the run has settled.
 */

import { useState } from "react";
import { SquarePanel } from "../components/plumblineUi";
import { Button } from "../components/ui";
import { money, ms as fmtMs } from "../lib/format";
import { useConsole } from "../lib/store";

type Stage = "idle" | "running" | "done";

const FLIP_MS = 90;
const COLUMNS = "grid-cols-[72px_minmax(0,2fr)_minmax(0,1fr)_120px]";

export function KillSwitchView() {
  const run = useConsole((s) => s.run);
  const running = useConsole((s) => s.running);
  const result = useConsole((s) => s.scenarios.kill_switch);

  // Seeded from the store: a tab switch unmounts this view, and a revoked chain must still
  // read as revoked when a judge comes back.
  const [stage, setStage] = useState<Stage>(() => (result ? "done" : "idle"));
  const [killed, setKilled] = useState(() => result?.chain?.length ?? 0);

  const chain = result?.chain ?? [];
  const revocation = result?.revocation;
  const probeAmount = revocation?.before.amount ?? null;

  const trip = async () => {
    setStage("running");
    setKilled(0);

    const fresh = await run("kill_switch");
    const rows = fresh?.chain?.length ?? 5;

    for (let i = 1; i <= rows; i++) {
      await new Promise((r) => setTimeout(r, FLIP_MS));
      setKilled(i);
    }
    setStage("done");
  };

  const done = stage === "done";

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-5">
        <p className="min-w-0 text-[1.0625rem] leading-[1.6] font-bold text-ink">
          One row, four hops.
        </p>
        <Button
          size="md"
          tone="deny"
          disabled={running !== null || stage === "running"}
          onClick={() => void trip()}
        >
          {stage === "running" ? "REVOKING" : done ? "REVOKED" : "REVOKE"}
        </Button>
      </header>

      {chain.length === 0 ? (
        <div className="border border-gray-03 bg-white px-[22px] py-10 text-center text-body text-ink-3">
          Arm the kill switch to build a four-hop chain and revoke it at the root.
        </div>
      ) : (
        <SquarePanel title="Revocation at the root">
          <div className={`grid ${COLUMNS} gap-4 border-b border-gray-02 px-[22px] py-3.5`}>
            <span className="colhead">Hop</span>
            <span className="colhead">Holder</span>
            <span className="colhead">Probe</span>
            <span className="colhead text-right">After</span>
          </div>

          {chain.map((node, index) => {
            const dead = stage !== "idle" && index < killed;
            // The footer carries the rule under the last row, so the row does not draw one
            // too: `last:` cannot help here, the footer is the last child.
            const rule = index === chain.length - 1 ? "" : "border-b border-gray-02";
            return (
              <div
                key={node.mandate_id}
                className={`grid ${COLUMNS} items-baseline gap-4 px-[22px] py-4 transition-colors duration-[180ms] ${rule} ${
                  dead ? "bg-warning-row" : ""
                }`}
              >
                <span className="num text-[0.8125rem] text-ink-4">{index}</span>
                <span
                  className={`min-w-0 text-[0.9375rem] leading-[1.4] break-words ${
                    dead ? "font-bold text-warning" : "text-ink"
                  }`}
                >
                  {node.holder}
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
      )}
    </div>
  );
}
