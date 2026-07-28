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

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { SquarePanel } from "../components/plumblineUi";
import { Button, ReasonCode, Verdict } from "../components/ui";
import { humanReason, money, ms as fmtMs, signedMoney } from "../lib/format";
import { useConsole } from "../lib/store";
import {
  PDP_STAGES,
  REASON_STEP_UP_REQUIRED,
  type DecisionRecord,
  type ScenarioResult,
} from "../lib/types";

type Phase = "idle" | "intent" | "breach" | "inject" | "settle" | "done";

const INTENT_LINE_MS = 240;
const BREACH_HOLD_MS = 620;
const INJECT_LINE_MS = 95;
const SETTLE_HOLD_MS = 420;
const LADDER_STEP_MS = 110;

/** One executed line beside what the Card Member actually signed for it. */
interface DiffRow {
  sku: string;
  description: string;
  mcc: number;
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
    mcc: line.mcc,
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
      mcc: line.mcc,
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
  const scenarios = useConsole((s) => s.scenarios);
  const openEvidence = useConsole((s) => s.openEvidence);

  // Switching kernel tabs unmounts this view, so the reveal state is seeded from whatever
  // the store already holds: coming back to a run that has finished shows the finished
  // run, not a blank stage with a settled decision sitting behind it.
  const [active, setActive] = useState<"clean_purchase" | "injection">("injection");
  const [phase, setPhase] = useState<Phase>(() => (scenarios.injection ? "done" : "idle"));
  const [visible, setVisible] = useState(() => diffRows(scenarios.injection).length);
  const [ladder, setLadder] = useState(() => (scenarios.injection ? PDP_STAGES.length : 0));
  const [elapsed, setElapsed] = useState(0);
  const elapsedRef = useRef(0);
  elapsedRef.current = elapsed;

  const cancelRef = useRef(0);
  const result = scenarios[active];
  const rows = diffRows(result);
  const decision = result?.governed;
  const ungoverned = result?.ungoverned;

  // Re-anchored on each phase rather than on each tick: `elapsed` is read to continue
  // from where the previous phase left off, so depending on it would restart the clock
  // fifty times a second.
  useEffect(() => {
    if (phase === "idle" || phase === "done") return;
    const started = performance.now() - elapsedRef.current;
    const id = window.setInterval(() => setElapsed(performance.now() - started), 50);
    return () => window.clearInterval(id);
  }, [phase]);

  const start = useCallback(
    async (scenario: "clean_purchase" | "injection") => {
      const token = ++cancelRef.current;
      setActive(scenario);
      setPhase("idle");
      setVisible(0);
      setLadder(0);
      setElapsed(0);

      const fresh = await run(scenario);
      if (!fresh || cancelRef.current !== token) return;

      const all = diffRows(fresh);
      const intent = all.filter((r) => !r.injected).length;
      const injected = all.length - intent;
      const alive = () => cancelRef.current === token;
      const wait = (delay: number) => new Promise((r) => setTimeout(r, delay));

      setPhase("intent");
      for (let i = 1; i <= intent; i++) {
        await wait(INTENT_LINE_MS);
        if (!alive()) return;
        setVisible(i);
      }

      if (injected > 0) {
        setPhase("breach");
        await wait(BREACH_HOLD_MS);
        if (!alive()) return;
        setPhase("inject");
        for (let i = 1; i <= injected; i++) {
          await wait(INJECT_LINE_MS);
          if (!alive()) return;
          setVisible(intent + i);
        }
      }

      setPhase("settle");
      await wait(SETTLE_HOLD_MS);
      if (!alive()) return;

      for (let i = 1; i <= PDP_STAGES.length; i++) {
        await wait(LADDER_STEP_MS);
        if (!alive()) return;
        setLadder(i);
      }
      setPhase("done");
    },
    [run],
  );

  const reset = () => {
    cancelRef.current += 1;
    setPhase("idle");
    setVisible(0);
    setLadder(0);
    setElapsed(0);
  };

  const settled = phase === "settle" || phase === "done";
  const revealedTotal = rows
    .slice(0, visible)
    .reduce((sum, row) => sum + (row.inCart ?? 0), 0);
  const primaryReason = decision?.reason_codes[0];

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-5">
        <div className="min-w-0">
          <p className="text-[1.0625rem] leading-[1.6] font-bold text-ink">
            One agent. One injected page. Two outcomes.
          </p>
          <p className="mt-1 max-w-[62ch] text-[0.90625rem] leading-[1.55] font-normal text-ink-2">
            {result?.narrative ??
              "An injected instruction appends stored-value gift cards after the human signed intent."}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2.5">
          <span className="num text-code text-ink-4">T+{(elapsed / 1000).toFixed(1)}s</span>
          <Button
            size="md"
            tone={active === "clean_purchase" ? "primary" : "default"}
            disabled={running !== null}
            onClick={() => void start("clean_purchase")}
          >
            Clean run
          </Button>
          <Button
            size="md"
            tone="deny"
            disabled={running !== null}
            onClick={() => void start("injection")}
          >
            {running === "injection" ? "Running…" : "Run injection"}
          </Button>
          <Button size="md" tone="ghost" onClick={reset}>
            Reset
          </Button>
        </div>
      </header>

      {phase === "idle" && !result ? (
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
                    {!settled ? "PENDING" : ungoverned?.authorized ? "APPROVED" : "DECLINED"}
                  </span>
                }
              />
              <div className="h-px bg-gray-02" />
              <Readout
                label="Settled"
                value={
                  <span
                    className={`num text-[1.5rem] font-semibold ${settled ? "text-navy" : "text-ink-4"}`}
                  >
                    {money(settled ? (ungoverned?.amount ?? 0) : revealedTotal)}
                  </span>
                }
                note={
                  settled
                    ? (ungoverned?.note ?? "merchant page trusted verbatim")
                    : "cart total as the agent builds it"
                }
              />
            </OutcomePanel>

            {/* ------------------------------------------------------------ governed */}
            <OutcomePanel tone="success" title="Behind the kernel">
              <Readout
                label="Decision"
                value={
                  <span className="num text-[1.0625rem] font-semibold text-success">
                    {!settled || !decision ? "PENDING" : outcomeWord(decision)}
                  </span>
                }
              />
              <div className="h-px bg-gray-02" />
              <Readout
                label="Settled"
                value={
                  <span
                    className={`num text-[1.5rem] font-semibold ${settled ? "text-navy" : "text-ink-4"}`}
                  >
                    {money(
                      !settled || !decision
                        ? revealedTotal
                        : decision.outcome === "ALLOW"
                          ? decision.amount
                          : 0,
                    )}
                  </span>
                }
                note={
                  !settled || !decision
                    ? "cart re-validated against signed intent"
                    : decision.outcome === "ALLOW"
                      ? decision.diff.summary
                      : `${money(decision.amount)} blocked · ${primaryReason ? humanReason(primaryReason) : "outside the mandate"}`
                }
              />
            </OutcomePanel>
          </div>

          {/* ------------------------------------------------------------------ ladder */}
          <SquarePanel
            title="Policy decision point"
            right={
              <span className="num text-[0.71875rem] font-normal text-ink-4">
                {PDP_STAGES.length} checks, fail-closed order
              </span>
            }
          >
            <div className="grid grid-cols-[34px_minmax(0,140px)_minmax(0,1fr)_74px] gap-4 border-b border-gray-02 px-[22px] py-3.5">
              <span className="colhead">#</span>
              <span className="colhead">Check</span>
              <span className="colhead">Finding</span>
              <span className="colhead text-right">Result</span>
            </div>
            {ladder === 0 || !decision ? (
              <p className="px-[22px] py-4 text-[0.84375rem] text-ink-4">
                the five checks run in order and stop at the first refusal
              </p>
            ) : (
              PDP_STAGES.slice(0, ladder).map((stage, index) => {
                const hits = stage.codes.filter((code) =>
                  (decision.reason_codes as string[]).includes(code),
                );
                const failed = hits.length > 0;
                const detail = decision.violations.find((v) =>
                  (stage.codes as readonly string[]).includes(v.reason),
                );
                return (
                  <div
                    key={stage.key}
                    className={`anim-line-in grid grid-cols-[34px_minmax(0,140px)_minmax(0,1fr)_74px] items-baseline gap-4 border-b border-gray-02 px-[22px] py-3.5 last:border-b-0 ${
                      failed ? "bg-warning-row" : ""
                    }`}
                    style={{ animationDelay: `${index * 20}ms` }}
                  >
                    <span className="num text-[0.75rem] font-normal text-ink-4">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <span
                      className={`text-[0.9375rem] leading-[1.4] break-words ${failed ? "font-bold text-warning" : "text-ink"}`}
                    >
                      {stage.label}
                    </span>
                    {/* A passing stage used to print `stage.note`, restating what the check
                        does. The label names it and the result says it held; only failures
                        earn the line. */}
                    <span className="text-[0.84375rem] leading-[1.5] break-words text-ink-2">
                      {failed ? (detail?.detail ?? hits.join(", ")) : ""}
                    </span>
                    <span
                      className={`num text-right text-[0.75rem] font-medium ${failed ? "text-warning" : "text-success"}`}
                    >
                      {failed ? "FAIL" : "PASS"}
                    </span>
                  </div>
                );
              })
            )}
          </SquarePanel>

          {/* -------------------------------------------------------------- cart table */}
          <SquarePanel
            title="Cart ↔ signed intent"
            right={
              <span className="num text-[0.71875rem] font-normal text-ink-4">
                {result?.executed_cart?.merchant ?? "—"}
              </span>
            }
          >
            <div className="grid grid-cols-[minmax(0,2fr)_1fr_1fr] gap-4 border-b border-gray-02 px-[22px] py-3.5">
              <span className="colhead">Line</span>
              <span className="colhead text-right">Signed</span>
              <span className="colhead text-right">In cart</span>
            </div>

            {visible === 0 ? (
              <p className="px-[22px] py-4 font-mono text-[0.84375rem] text-ink-4">
                agent planning…
              </p>
            ) : (
              rows.slice(0, visible).map((row, index) => (
                <div
                  key={`${row.sku}-${index}`}
                  className={`anim-line-in grid grid-cols-[minmax(0,2fr)_1fr_1fr] items-baseline gap-4 border-b border-gray-02 px-[22px] py-3.5 ${
                    row.injected ? "bg-warning-row" : ""
                  }`}
                >
                  <span className="flex min-w-0 flex-col gap-1">
                    <span className="flex flex-wrap items-baseline gap-2">
                      <span
                        className={`text-[0.9375rem] leading-[1.4] break-words ${
                          row.injected ? "font-bold text-warning" : "text-ink"
                        }`}
                      >
                        {row.description}
                        {row.qty > 1 && <span className="text-ink-4"> ×{row.qty}</span>}
                      </span>
                      {row.injected && (
                        <span className="num rounded-[3px] bg-gray-02 px-[9px] py-[3px] text-[0.71875rem] text-warning">
                          INJECTED
                        </span>
                      )}
                    </span>
                    <span className="num text-[0.71875rem] break-words text-ink-4">
                      {row.sku} · mcc {row.mcc}
                    </span>
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
              ))
            )}

            {decision && (
              <>
                <div className="grid grid-cols-[minmax(0,2fr)_1fr_1fr] items-baseline gap-4 border-t border-gray-03 bg-gray-01 px-[22px] py-3.5">
                  <span className="text-[0.9375rem] font-bold text-navy">Total</span>
                  <span className="num text-right text-[0.9375rem] font-semibold text-navy">
                    {money(decision.diff.intent_total)}
                  </span>
                  <span
                    className={`num text-right text-[0.9375rem] font-semibold ${
                      decision.diff.diverged ? "text-warning" : "text-navy"
                    }`}
                  >
                    {money(decision.diff.executed_total)}
                  </span>
                </div>
                <div className="flex flex-wrap items-baseline justify-between gap-4 border-t border-gray-03 bg-gray-01 px-[22px] py-[18px]">
                  <span className="min-w-0 text-[0.90625rem] leading-[1.55] font-normal break-words text-ink-2">
                    {decision.diff.summary}
                    {decision.diff.merchant_changed &&
                      ` · merchant swapped ${decision.diff.merchant_changed[0]} → ${decision.diff.merchant_changed[1]}`}
                  </span>
                  <span
                    className={`num shrink-0 text-[1.0625rem] font-semibold ${
                      decision.diff.delta > 0 ? "text-warning" : "text-success"
                    }`}
                  >
                    {signedMoney(decision.diff.delta)}
                  </span>
                </div>
              </>
            )}
          </SquarePanel>

          {/* ------------------------------------------------------- reasons, liability */}
          {settled && decision && (
            <SquarePanel
              title="Reasons and liability"
              right={
                <span className="num text-[0.71875rem] font-normal text-ink-4">
                  decided in {fmtMs(decision.elapsed_ms)} · ledger #{decision.ledger_seq}
                </span>
              }
            >
              <div className="flex flex-col gap-4 px-[22px] py-5">
                <div className="flex flex-wrap gap-2">
                  {decision.reason_codes.length === 0 ? (
                    <ReasonCode code="NO_VIOLATIONS" tone="muted" />
                  ) : (
                    decision.reason_codes.map((code) => (
                      <ReasonCode
                        key={code}
                        code={code}
                        tone={code === REASON_STEP_UP_REQUIRED ? "stepup" : "deny"}
                      />
                    ))
                  )}
                </div>
                <div className="flex flex-wrap items-end justify-between gap-4">
                  <Verdict verdict={decision.verdict} liable={decision.liable_party} />
                  <Button size="sm" onClick={() => void openEvidence(decision.txn_id)}>
                    Evidence →
                  </Button>
                </div>
              </div>
            </SquarePanel>
          )}
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
