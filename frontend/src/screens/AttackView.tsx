/**
 * Beat 1 — theft, then no theft.
 *
 * The same agent, the same task, the same injected merchant page, run down both sides of
 * a split screen. The left pane is a receipt: it has no opinion about what it is paying
 * for. The right pane is a decision record. The injected lines are rendered identically
 * on both sides, so the only difference a judge has to track is what happened at the
 * bottom of each column.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Button,
  Caveat,
  Check,
  Empty,
  Money,
  OutcomeChip,
  Panel,
  ReasonCode,
  Verdict,
} from "../components/ui";
import { money, ms as fmtMs, signedMoney } from "../lib/format";
import { useConsole } from "../lib/store";
import {
  PDP_STAGES,
  REASON_STEP_UP_REQUIRED,
  type CartLine,
  type DecisionRecord,
  type ScenarioResult,
} from "../lib/types";

type Phase = "idle" | "intent" | "breach" | "inject" | "settle" | "done";

const INTENT_LINE_MS = 240;
const BREACH_HOLD_MS = 620;
const INJECT_LINE_MS = 95;
const SETTLE_HOLD_MS = 420;
const LADDER_STEP_MS = 110;

interface Row {
  line: CartLine;
  injected: boolean;
}

function rowsFor(result: ScenarioResult | undefined): Row[] {
  if (!result?.executed_cart) return [];
  const addedSkus = new Set((result.governed?.diff.added ?? []).map((l) => l.sku));
  return result.executed_cart.lines.map((line) => ({
    line,
    injected: addedSkus.has(line.sku),
  }));
}

export function AttackView() {
  const run = useConsole((s) => s.run);
  const running = useConsole((s) => s.running);
  const scenarios = useConsole((s) => s.scenarios);
  const openEvidence = useConsole((s) => s.openEvidence);

  const [active, setActive] = useState<"clean_purchase" | "injection">("injection");
  const [phase, setPhase] = useState<Phase>("idle");
  const [visible, setVisible] = useState(0);
  const [ladder, setLadder] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const elapsedRef = useRef(0);
  elapsedRef.current = elapsed;

  const cancelRef = useRef(0);
  const result = scenarios[active];
  const rows = rowsFor(result);
  const intentCount = rows.filter((r) => !r.injected).length;
  const decision = result?.governed;

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

      const all = rowsFor(fresh);
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
  const executedTotal = result?.executed_cart
    ? result.executed_cart.lines.reduce((sum, l) => sum + l.amount, 0)
    : 0;
  const revealedTotal = rows.slice(0, visible).reduce((sum, r) => sum + r.line.amount, 0);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <header className="flex shrink-0 flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="display-xwide text-[1.7rem] leading-none font-semibold tracking-[-0.01em]">
            One agent. One injected page. Two outcomes.
          </h1>
          <p className="mt-1.5 text-body text-ink-2">
            {result?.narrative ??
              "An injected instruction appends stored-value gift cards after the human signed intent."}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <div className="num mr-2 text-pill text-ink-3">
            T+{(elapsed / 1000).toFixed(1)}s
          </div>
          <Button
            size="md"
            tone={active === "clean_purchase" ? "primary" : "default"}
            disabled={running !== null}
            onClick={() => start("clean_purchase")}
          >
            Clean run
          </Button>
          <Button
            size="md"
            tone="deny"
            disabled={running !== null}
            onClick={() => start("injection")}
          >
            {running === "injection" ? "Running…" : "Run injection"}
          </Button>
          <Button size="md" tone="ghost" onClick={reset}>
            Reset
          </Button>
        </div>
      </header>

      {phase === "idle" && !result ? (
        <Panel className="flex-1">
          <Empty>
            Press <span className="mx-1 font-mono text-deny">Run injection</span> to replay the
            attack through both stacks.
          </Empty>
        </Panel>
      ) : (
        <>
          <div className="grid min-h-0 flex-1 grid-cols-2 gap-3">
            {/* ---------------------------------------------------------- ungoverned */}
            <Panel
              title="Ungoverned agent"
              hint="merchant page trusted verbatim"
              right={
                <span className="num text-pill text-ink-4">
                  {result?.executed_cart?.merchant ?? "—"}
                </span>
              }
              bodyClassName="flex flex-col min-h-0"
            >
              <CartLines rows={rows} visible={visible} />
              <footer className="shrink-0 border-t border-line px-4 py-3.5">
                {settled ? (
                  <div className="anim-stamp flex items-end justify-between gap-4">
                    <div>
                      <div className="eyebrow">Authorisation</div>
                      <div className="mt-1 inline-flex items-center gap-2">
                        <span className="rounded border border-allow/35 bg-allow-wash px-1.5 py-0.5 font-mono text-pill tracking-[0.08em] text-allow">
                          APPROVED
                        </span>
                        <span className="num text-pill text-ink-4">
                          auth 054829 · settled
                        </span>
                      </div>
                    </div>
                    <div className="num display-wide text-[2.6rem] leading-none font-semibold">
                      {money(executedTotal)}
                    </div>
                  </div>
                ) : (
                  <PendingFooter total={revealedTotal} label="Cart total" />
                )}
              </footer>
            </Panel>

            {/* ------------------------------------------------------------ governed */}
            <Panel
              title="Behind CAVEAT"
              hint="re-validated against signed intent"
              tone={settled && decision?.outcome === "DENY" ? "deny" : "default"}
              right={
                decision && settled ? <OutcomeChip outcome={decision.outcome} size="lg" /> : null
              }
              bodyClassName="flex flex-col min-h-0"
            >
              <CartLines rows={rows} visible={visible} />
              <footer className="shrink-0 border-t border-line">
                {settled && decision ? (
                  <div className="flex flex-col gap-3 px-4 py-3.5">
                    <CheckLadder decision={decision} revealed={ladder} />
                    <div className="anim-stamp flex items-end justify-between gap-4">
                      <div className="min-w-0">
                        <div className="eyebrow">Decision</div>
                        <div className="mt-1 font-mono text-body text-deny">
                          {decision.outcome === "ALLOW" ? (
                            <span className="text-allow">{decision.headline}</span>
                          ) : (
                            decision.headline
                          )}
                        </div>
                      </div>
                      <div
                        className={`num display-wide text-[2.6rem] leading-none font-semibold ${
                          decision.outcome === "ALLOW"
                            ? "text-allow"
                            : "text-deny line-through decoration-2"
                        }`}
                      >
                        {money(decision.amount)}
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="px-4 py-3.5">
                    <PendingFooter total={revealedTotal} label="Cart under evaluation" />
                  </div>
                )}
              </footer>
            </Panel>
          </div>

          {/* ------------------------------------------------------------------ diff */}
          <div className="grid shrink-0 grid-cols-[minmax(0,2fr)_minmax(0,1fr)] gap-3">
            <Panel
              title="Cart ↔ signed intent"
              hint={decision?.diff.summary}
              tone={decision?.diff.diverged ? "deny" : "default"}
              bodyClassName="p-4"
            >
              {decision ? (
                <DiffBlock decision={decision} intentCount={intentCount} settled={settled} />
              ) : (
                <div className="text-pill text-ink-4">awaiting execution…</div>
              )}
            </Panel>

            <Panel title="Reasons & liability" bodyClassName="flex flex-col gap-3 p-4">
              {settled && decision ? (
                <>
                  <div className="flex flex-wrap gap-1.5">
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
                  <Verdict verdict={decision.verdict} liable={decision.liable_party} />
                  <div className="mt-auto flex items-center justify-between gap-2 pt-1">
                    <span className="num text-pill text-ink-4">
                      decided in {fmtMs(decision.elapsed_ms)} · ledger #{decision.ledger_seq}
                    </span>
                    <Button size="sm" onClick={() => openEvidence(decision.txn_id)}>
                      Evidence →
                    </Button>
                  </div>
                </>
              ) : (
                <div className="text-pill text-ink-4">awaiting decision…</div>
              )}
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------

function PendingFooter({ total, label }: { total: number; label: string }) {
  return (
    <div className="flex items-end justify-between gap-4">
      <div className="eyebrow">{label}</div>
      <div className="num display-wide text-[2.6rem] leading-none font-semibold text-ink-2">
        {money(total)}
      </div>
    </div>
  );
}

function CartLines({ rows, visible }: { rows: Row[]; visible: number }) {
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [visible]);

  return (
    <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto px-1 py-1.5">
      {rows.slice(0, visible).map((row, index) => (
        <div
          key={`${row.line.sku}-${index}`}
          className={`anim-line-in flex items-baseline gap-3 border-l-2 px-3 py-1.5 ${
            row.injected
              ? "anim-flash-deny border-deny bg-deny-wash/45"
              : "border-transparent"
          }`}
        >
          <span className="num w-[9.5rem] shrink-0 break-words text-pill text-ink-4">
            {row.line.sku}
          </span>
          <span
            className={`min-w-0 flex-1 break-words text-body ${
              row.injected ? "text-deny" : "text-ink"
            }`}
          >
            {row.line.description}
            {row.line.qty > 1 && <span className="ml-1.5 text-ink-4">×{row.line.qty}</span>}
          </span>
          {row.injected && (
            <span className="shrink-0 rounded border border-deny/50 px-1 py-px font-mono text-pill tracking-[0.08em] text-deny">
              INJECTED
            </span>
          )}
          <span className="num w-14 shrink-0 text-right text-pill text-ink-4">
            {row.line.mcc}
          </span>
          <Money
            minorUnits={row.line.amount}
            tone={row.injected ? "deny" : "default"}
            className="w-28 shrink-0 text-right text-body"
          />
        </div>
      ))}
      {visible === 0 && (
        <div className="px-3 py-2 font-mono text-pill text-ink-4">
          agent planning…
        </div>
      )}
    </div>
  );
}

function CheckLadder({ decision, revealed }: { decision: DecisionRecord; revealed: number }) {
  return (
    <ul className="flex flex-col gap-1">
      {PDP_STAGES.slice(0, revealed).map((stage, index) => {
        const hits = stage.codes.filter((code) =>
          (decision.reason_codes as string[]).includes(code),
        );
        const failed = hits.length > 0;
        const detail = decision.violations.find((v) =>
          (stage.codes as readonly string[]).includes(v.reason),
        );
        return (
          <li
            key={stage.key}
            className="anim-line-in flex items-baseline gap-2.5 text-pill"
            style={{ animationDelay: `${index * 20}ms` }}
          >
            <span className="num w-4 shrink-0 text-ink-4">{index + 1}</span>
            <Check ok={!failed} size={12} />
            <span className={`w-24 shrink-0 ${failed ? "text-deny" : "text-ink-2"}`}>
              {stage.label}
            </span>
            {/* A passing stage used to print `stage.note`, restating what the check does.
                The label names it and the tick says it held; only failures earn the line. */}
            {failed && (
              <span className="min-w-0 flex-1 break-words text-ink-4">
                {detail?.detail ?? hits.join(", ")}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function DiffBlock({
  decision,
  intentCount,
  settled,
}: {
  decision: DecisionRecord;
  intentCount: number;
  settled: boolean;
}) {
  const diff = decision.diff;
  return (
    <div className="grid h-full grid-cols-[repeat(3,minmax(0,1fr))_minmax(0,1.3fr)] gap-4">
      <TotalBlock
        label="Signed intent"
        value={diff.intent_total}
        note={`${intentCount} line${intentCount === 1 ? "" : "s"} · hash ${decision.intent_hash.slice(0, 8)}`}
      />
      <TotalBlock
        label="Executed cart"
        value={diff.executed_total}
        note={`${intentCount + diff.added.length} lines · hash ${decision.cart_hash.slice(0, 8)}`}
        tone={diff.diverged ? "deny" : "default"}
      />
      <div className="flex min-w-0 flex-col gap-1">
        <div className="eyebrow">Divergence</div>
        <div
          className={`num display-wide text-[2rem] leading-none font-semibold ${
            diff.delta > 0 ? "text-deny" : "text-allow"
          } ${settled ? "anim-stamp" : ""}`}
        >
          {signedMoney(diff.delta)}
        </div>
        <div className="text-pill text-ink-3">
          {diff.added.length} line{diff.added.length === 1 ? "" : "s"} after signature
        </div>
      </div>

      <div className="flex min-w-0 flex-col gap-1.5">
        <div className="eyebrow">Added after signing</div>
        <div className="max-h-24 min-h-0 overflow-y-auto pr-1">
          {diff.added.length === 0 ? (
            <div className="text-pill text-allow">cart matches signed intent</div>
          ) : (
            diff.added.map((line, index) => (
              <div
                key={`${line.sku}-${index}`}
                className="flex items-baseline justify-between gap-3 py-px text-pill"
              >
                <span className="break-words text-deny">{line.description}</span>
                <Money minorUnits={line.amount} tone="deny" className="shrink-0 text-pill" />
              </div>
            ))
          )}
        </div>
        {diff.merchant_changed && (
          <Caveat>
            merchant swapped {diff.merchant_changed[0]} → {diff.merchant_changed[1]}
          </Caveat>
        )}
      </div>
    </div>
  );
}

function TotalBlock({
  label,
  value,
  note,
  tone = "default",
}: {
  label: string;
  value: number;
  note: string;
  tone?: "default" | "deny";
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="eyebrow">{label}</div>
      <Money
        minorUnits={value}
        tone={tone === "deny" ? "deny" : "default"}
        className="display-wide text-[2rem] leading-none font-semibold"
      />
      <div className="num break-words text-pill text-ink-4">{note}</div>
    </div>
  );
}
