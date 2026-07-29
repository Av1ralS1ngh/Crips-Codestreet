/**
 * Kernel, tab 1 — theft, then no theft.
 *
 * The same agent, the same task, the same injected merchant page, run down both sides. The
 * left pane is a receipt: it has no opinion about what it is paying for. The right pane is
 * a decision record. The two panes state their outcome and nothing else, and the single
 * cart table underneath carries the lines, so a judge tracks one divergence rather than
 * reading the same twelve rows twice.
 *
 * Nothing here asks the merchant for anything. The check runs against the intent the Card
 * Member signed, on the cardholder's own mandate.
 */

import type { ReactNode } from "react";
import { SquarePanel } from "../components/plumblineUi";
import { Button } from "../components/ui";
import { humanReason, money } from "../lib/format";
import { useConsole } from "../lib/store";
import type { DecisionRecord, ScenarioResult } from "../lib/types";

const COLUMNS = "grid-cols-[minmax(0,2fr)_1fr_1fr]";

/** One executed line beside what the Card Member actually signed for it. */
interface DiffRow {
  sku: string;
  description: string;
  qty: number;
  signed: number | null;
  inCart: number | null;
  injected: boolean;
}

function diffRows(result: ScenarioResult | undefined): DiffRow[] {
  const executed = result?.executed_cart?.lines ?? [];
  const intent = result?.intent_cart?.lines ?? [];
  const signedBySku = new Map(intent.map((line) => [line.sku, line.amount]));
  const addedSkus = new Set((result?.governed?.diff.added ?? []).map((line) => line.sku));

  const rows: DiffRow[] = executed.map((line) => ({
    sku: line.sku,
    description: line.description,
    qty: line.qty,
    signed: signedBySku.get(line.sku) ?? null,
    inCart: line.amount,
    injected: addedSkus.has(line.sku),
  }));

  // A line the agent dropped is a divergence too, and it renders on the same table rather
  // than in a second one nobody reads.
  const executedSkus = new Set(executed.map((line) => line.sku));
  for (const line of intent) {
    if (executedSkus.has(line.sku)) continue;
    rows.push({
      sku: line.sku,
      description: line.description,
      qty: line.qty,
      signed: line.amount,
      inCart: null,
      injected: false,
    });
  }
  return rows;
}

export function AttackView() {
  const run = useConsole((s) => s.run);
  const running = useConsole((s) => s.running);
  // Read straight off the store, so a tab switch away and back shows the run that already
  // happened rather than an empty stage.
  const result = useConsole((s) => s.scenarios.injection);

  const rows = diffRows(result);
  const decision = result?.governed;
  const ungoverned = result?.ungoverned;

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-5">
        <p className="min-w-0 text-[1.0625rem] leading-[1.6] font-bold text-ink">
          One agent. One injected page. Two outcomes.
        </p>
        <Button
          size="md"
          tone="deny"
          disabled={running !== null}
          onClick={() => void run("injection")}
        >
          {running === "injection" ? "Running…" : "Run injection"}
        </Button>
      </header>

      {!result ? (
        <div className="border border-gray-03 bg-white px-[22px] py-10 text-center text-body text-ink-3">
          Press <span className="num text-warning">Run injection</span> to replay the attack
          through both stacks.
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-6">
            {/* ---------------------------------------------------------- ungoverned */}
            <OutcomePanel tone="deny" title="Ungoverned agent">
              <Readout
                label="Decision"
                value={
                  <span className="num text-[1.0625rem] font-semibold text-warning">
                    {ungoverned?.authorized ? "AUTHORISED" : "DENIED"}
                  </span>
                }
              />
              <div className="h-px bg-gray-02" />
              <Readout
                label="Settled"
                value={
                  <span className="num text-[1.5rem] font-semibold text-navy">
                    {money(ungoverned?.amount ?? 0)}
                  </span>
                }
                note={ungoverned?.note}
              />
            </OutcomePanel>

            {/* ------------------------------------------------------------ governed */}
            <OutcomePanel tone="success" title="Behind the kernel">
              <Readout
                label="Decision"
                value={
                  <span className="num text-[1.0625rem] font-semibold text-success">
                    {decision ? outcomeWord(decision) : "—"}
                  </span>
                }
              />
              <div className="h-px bg-gray-02" />
              <Readout
                label="Settled"
                value={
                  <span className="num text-[1.5rem] font-semibold text-navy">
                    {money(decision?.outcome === "ALLOW" ? decision.amount : 0)}
                  </span>
                }
                note={
                  decision &&
                  (decision.outcome === "ALLOW"
                    ? decision.diff.summary
                    : // Every code the PDP returned, not the first one: the chips that used
                      // to carry the rest of them are gone.
                      decision.reason_codes.map(humanReason).join(" · "))
                }
              />
            </OutcomePanel>
          </div>

          {/* -------------------------------------------------------------- cart table */}
          <SquarePanel title="Cart ↔ signed intent">
            <div className={`grid ${COLUMNS} gap-4 border-b border-gray-02 px-[22px] py-3.5`}>
              <span className="colhead">Line</span>
              <span className="colhead text-right">Signed</span>
              <span className="colhead text-right">In cart</span>
            </div>

            {rows.map((row, index) => (
              <div
                key={`${row.sku}-${index}`}
                className={`anim-line-in grid ${COLUMNS} items-baseline gap-4 border-b border-gray-02 px-[22px] py-4 last:border-b-0 ${
                  row.injected ? "bg-warning-row" : ""
                }`}
              >
                <span
                  className={`min-w-0 text-[0.9375rem] leading-[1.4] break-words ${
                    row.injected ? "font-bold text-warning" : "text-ink"
                  }`}
                >
                  {row.description}
                  {row.qty > 1 ? ` ×${row.qty}` : ""}
                </span>
                <span className="num text-right text-[0.9375rem] text-ink-2">
                  {row.signed === null ? "—" : money(row.signed)}
                </span>
                <span
                  className={`num text-right text-[0.9375rem] ${
                    row.injected ? "font-semibold text-warning" : "text-ink-2"
                  }`}
                >
                  {row.inCart === null ? "—" : money(row.inCart)}
                </span>
              </div>
            ))}
          </SquarePanel>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------

/** DENIED is the good outcome on the right-hand pane, so the strip is green and so is it. */
function outcomeWord(decision: DecisionRecord): string {
  if (decision.outcome === "DENY") return "DENIED";
  if (decision.outcome === "ALLOW") return "ALLOWED";
  return "STEP-UP REQUIRED";
}

/**
 * A strip-headed pane. `SquarePanel` carries navy, blue and deny strips; the governed side
 * needs a success strip and a matching border, which is the one fill the shared component
 * does not offer.
 */
function OutcomePanel({
  tone,
  title,
  children,
}: {
  tone: "deny" | "success";
  title: ReactNode;
  children: ReactNode;
}) {
  const border = tone === "deny" ? "border-warning" : "border-success";
  const fill = tone === "deny" ? "bg-warning" : "bg-success";
  return (
    <div className={`flex flex-col border bg-white ${border}`}>
      <div className={`strip px-[22px] py-[15px] text-white ${fill}`}>{title}</div>
      <div className="flex flex-col gap-4 px-[22px] py-6">{children}</div>
    </div>
  );
}

function Readout({
  label,
  value,
  note,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-[5px]">
      <span className="colhead">{label}</span>
      {value}
      {note && (
        <span className="text-[0.875rem] leading-[1.5] break-words text-ink-3">{note}</span>
      )}
    </div>
  );
}
