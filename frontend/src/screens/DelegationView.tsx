/**
 * Kernel, tab 2 — the escalation proof.
 *
 * The argument the panel has to make is not "Z3 said no"; it is that a set-containment
 * check, the check a competent engineer writes first, says YES to the same hop. So both
 * verdicts render at the same weight, side by side, and the counterexample is printed
 * exactly as the solver returned it.
 *
 * The tree is indented by position rather than by the `depth` field, so a rejected hop
 * hangs off the mandate it tried to widen. A rejected hop never became a mandate, which is
 * the whole point: it has no row in the chain, only an attempt in the log.
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { SquarePanel } from "../components/plumblineUi";
import { Button, Caveat, ReasonCode } from "../components/ui";
import { money, ms as fmtMs } from "../lib/format";
import { useConsole } from "../lib/store";
import {
  REASON_SCOPE_ESCALATION,
  type ConstraintDict,
  type DelegationRecord,
  type MandateNode,
} from "../lib/types";

const INDENT_PX = 34;

type TreeRow =
  | { kind: "mandate"; key: string; depth: number; node: MandateNode }
  | { kind: "rejected"; key: string; depth: number; index: number; delegation: DelegationRecord };

/**
 * A pre-order walk: every mandate, then the hops that were refused before they could
 * become one. Accepted children come first so the live chain reads top to bottom and the
 * refusals land at the bottom of the branch they attacked.
 */
function treeRows(mandates: MandateNode[], delegations: DelegationRecord[]): TreeRow[] {
  const out: TreeRow[] = [];
  const known = new Set(mandates.map((m) => m.mandate_id));
  const roots = mandates.filter((m) => !m.parent_id || !known.has(m.parent_id));

  const walk = (node: MandateNode, depth: number) => {
    out.push({ kind: "mandate", key: node.mandate_id, depth, node });
    for (const child of mandates.filter((m) => m.parent_id === node.mandate_id)) {
      walk(child, depth + 1);
    }
    for (const [index, delegation] of delegations.entries()) {
      if (delegation.accepted || delegation.parent_id !== node.mandate_id) continue;
      out.push({
        kind: "rejected",
        key: `rejected:${index}`,
        depth: depth + 1,
        index,
        delegation,
      });
    }
  };

  for (const root of roots) walk(root, 0);
  return out;
}

/**
 * The tightest amount bound a mandate carries, rebuilt from the constraints rather than
 * matched out of `scope_display`: a chain accumulates several `amount_max` lines and the
 * first one printed is the loosest, not the one that binds.
 */
function capOf(scope: ConstraintDict[]): string | null {
  const caps = scope.filter((c) => c.type === "amount_max" && typeof c.value === "number");
  if (!caps.length) return null;
  return `amount ≤ ${money(Math.min(...caps.map((c) => c.value as number)))}`;
}

/** A refused hop has no constraint list of its own, only the scope it declared. */
function declaredCap(lines: string[]): string | null {
  return lines.find((line) => line.startsWith("amount ≤")) ?? null;
}

export function DelegationView() {
  const run = useConsole((s) => s.run);
  const running = useConsole((s) => s.running);
  const result = useConsole((s) => s.scenarios.escalation);
  const [selected, setSelected] = useState<number | null>(null);

  const delegations = useMemo(() => result?.delegations ?? [], [result]);
  const mandates = useMemo(() => {
    if (result?.mandates?.length) return result.mandates;
    return result?.root ? [result.root] : [];
  }, [result]);

  const rows = useMemo(() => treeRows(mandates, delegations), [mandates, delegations]);

  useEffect(() => {
    // Land on the hop that carries the argument: the one the naive check got wrong.
    const fooled = delegations.findIndex((d) => d.naive_disagrees);
    if (fooled >= 0) setSelected(fooled);
    else if (delegations.length) setSelected(0);
  }, [delegations]);

  const hop = selected === null ? undefined : delegations[selected];

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-5">
        <div className="min-w-0">
          <p className="text-[1.0625rem] leading-[1.6] font-bold text-ink">
            A mandate can be narrowed and never widened.
          </p>
          <p className="mt-1 max-w-[62ch] text-[0.90625rem] leading-[1.55] font-normal text-ink-2">
            {result?.narrative ??
              "Delegation is accepted iff UNSAT(child ∧ ¬parent). Set containment is the wrong test for money."}
          </p>
        </div>
        <Button
          size="md"
          tone="primary"
          disabled={running !== null}
          onClick={() => void run("escalation")}
        >
          {running === "escalation" ? "Proving…" : "Run delegation hops"}
        </Button>
      </header>

      {!result ? (
        <div className="border border-gray-03 bg-white px-[22px] py-10 text-center text-body text-ink-3">
          Run the delegation hops to render the mandate tree and the entailment proofs.
        </div>
      ) : (
        <>
          <SquarePanel
            title="Delegation tree"
            right={
              <span className="num text-[0.71875rem] font-normal text-ink-4">
                {mandates.length} live · {delegations.filter((d) => !d.accepted).length} refused
              </span>
            }
          >
            <div className="flex flex-col px-6 py-[26px]">
              {rows.map((row, index) => (
                <div key={row.key} className="flex flex-col">
                  {index > 0 && (
                    <div
                      className="h-[18px] w-px bg-gray-03"
                      style={{ marginLeft: row.depth * INDENT_PX }}
                    />
                  )}
                  {row.kind === "mandate" ? (
                    <MandateRow node={row.node} depth={row.depth} />
                  ) : (
                    <RejectedRow
                      delegation={row.delegation}
                      depth={row.depth}
                      active={selected === row.index}
                      onSelect={() => setSelected(row.index)}
                    />
                  )}
                </div>
              ))}
            </div>

            {hop && (
              <div className="flex flex-col gap-3 border-t border-gray-03 px-6 py-[22px]">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <span
                    className={`num text-[0.65625rem] font-semibold tracking-[0.14em] uppercase ${
                      hop.entailment.counterexample ? "text-warning" : "text-success"
                    }`}
                  >
                    {hop.entailment.counterexample
                      ? "Parent constraints this witness breaks"
                      : "No counterexample exists"}
                  </span>
                  {hop.entailment.counterexample && (
                    <ReasonCode code={REASON_SCOPE_ESCALATION} />
                  )}
                </div>
                {hop.entailment.counterexample ? (
                  hop.entailment.counterexample.violated.map((line, i) => (
                    <div
                      key={i}
                      className="flex items-baseline gap-3.5 border-l-[3px] border-warning bg-gray-01 px-[18px] py-3.5"
                    >
                      <span className="num shrink-0 text-[0.78125rem] text-warning">
                        {String(i + 1).padStart(2, "0")}
                      </span>
                      <span className="num min-w-0 text-[0.84375rem] leading-[1.5] break-words text-ink">
                        {line}
                      </span>
                    </div>
                  ))
                ) : (
                  <div className="border-l-[3px] border-success bg-gray-01 px-[18px] py-3.5 text-[0.90625rem] leading-[1.55] font-normal text-ink">
                    The child's authority region is provably inside the parent's.
                  </div>
                )}
              </div>
            )}
          </SquarePanel>

          <div className="flex flex-wrap gap-2">
            {delegations.map((delegation, index) => (
              <button
                key={index}
                type="button"
                onClick={() => setSelected(index)}
                className={`rounded-pill border px-[15px] py-[7px] text-[0.78125rem] font-semibold transition-colors duration-[180ms] ${
                  selected === index
                    ? delegation.accepted
                      ? "border-success bg-success text-white"
                      : "border-warning bg-warning text-white"
                    : "border-gray-03 bg-white text-ink-3 hover:border-blue hover:text-blue"
                }`}
              >
                {delegation.title ?? `hop ${index + 1}`}
              </button>
            ))}
          </div>

          {hop && <ProofPanel delegation={hop} />}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------

function MandateRow({ node, depth }: { node: MandateNode; depth: number }) {
  const cap = capOf(node.scope);
  return (
    <div
      className="flex items-center gap-4 border border-gray-03 bg-white px-[18px] py-4"
      style={{ marginLeft: depth * INDENT_PX }}
    >
      <span className="num w-14 shrink-0 text-[0.6875rem] text-ink-4">HOP {depth}</span>
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-[0.96875rem] leading-[1.3] font-bold break-words text-navy">
          {node.holder}
        </span>
        <span className="num text-[0.71875rem] break-words text-ink-4">
          {node.mandate_id} · {node.caveat_count} caveats
          {node.entailment_ms !== null && ` · proved in ${fmtMs(node.entailment_ms)}`}
        </span>
      </span>
      {cap && <span className="num shrink-0 text-[0.84375rem] text-navy">{cap}</span>}
    </div>
  );
}

function RejectedRow({
  delegation,
  depth,
  active,
  onSelect,
}: {
  delegation: DelegationRecord;
  depth: number;
  active: boolean;
  onSelect: () => void;
}) {
  const cap = declaredCap(delegation.entailment.child_scope);
  return (
    <button
      type="button"
      onClick={onSelect}
      style={{ marginLeft: depth * INDENT_PX }}
      className={`flex items-center gap-4 border bg-warning-row px-[18px] py-4 text-left transition-colors duration-[180ms] ${
        active ? "border-warning" : "border-warning/50 hover:border-warning"
      }`}
    >
      <span className="num w-14 shrink-0 text-[0.6875rem] text-warning">HOP {depth}</span>
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-[0.96875rem] leading-[1.3] font-bold break-words text-warning">
          {delegation.child_holder}
        </span>
        <span className="num text-[0.71875rem] break-words text-ink-4">
          {delegation.title ?? "delegation hop"} · declares{" "}
          {delegation.entailment.child_scope.length} caveats
        </span>
      </span>
      {cap && <span className="num shrink-0 text-[0.84375rem] text-warning">{cap}</span>}
      <span className="num shrink-0 rounded-[3px] bg-gray-02 px-[9px] py-[3px] text-[0.71875rem] text-warning">
        REJECTED
      </span>
    </button>
  );
}

function ProofPanel({ delegation }: { delegation: DelegationRecord }) {
  const { entailment } = delegation;
  const counterexample = entailment.counterexample;
  const missing = entailment.parent_scope.filter((c) => !entailment.child_scope.includes(c));

  return (
    <SquarePanel
      title={delegation.title ?? "Delegation hop"}
      right={
        <span className="num text-[0.71875rem] font-normal text-ink-4">
          child {delegation.child_holder} · entailment {fmtMs(entailment.elapsed_ms)}
        </span>
      }
    >
      <div className="flex flex-col gap-5 px-[22px] py-5">
        {/* The gap is the rule: a 1px grid over the border colour, white children. */}
        <div className="grid grid-cols-2 gap-px border border-gray-03 bg-gray-03">
          <VerdictCell
            label="Naive subset check"
            verdict={delegation.naive_subset_check ? "NARROWER" : "WIDER"}
            ok={delegation.naive_subset_check}
            wrong={delegation.naive_disagrees}
            note="every caveat the child declares is present on the parent and no looser"
          />
          <VerdictCell
            label="Z3 entailment"
            verdict={entailment.entailed ? "ENTAILED" : REASON_SCOPE_ESCALATION}
            ok={entailment.entailed}
            note={`solver returned ${entailment.solver_result} · ${
              entailment.entailed ? "no such transaction exists" : "a witness exists"
            }`}
          />
        </div>

        {/* The two checks can disagree in either direction, and both directions are worth
            showing: containment is unsound on a dropped constraint and incomplete on a
            chain that has accumulated two bounds of the same kind. */}
        {delegation.naive_disagrees && (
          <p className="border-l-[3px] border-attention bg-attention-wash px-[18px] py-3.5 text-[0.90625rem] leading-[1.55] font-normal text-attention-ink">
            {delegation.naive_subset_check
              ? "The subset check was about to authorise this hop."
              : "The subset check would have rejected this legitimate narrowing."}
          </p>
        )}

        <p className="num border border-gray-03 bg-gray-01 px-[18px] py-3.5 text-[0.84375rem] text-blue">
          accept ⟺ UNSAT( child ∧ ¬parent )
        </p>

        <div className="grid grid-cols-2 gap-6">
          <ScopeColumn label="Child declared scope" lines={entailment.child_scope} />
          <ScopeColumn
            label="Parent scope"
            lines={entailment.parent_scope}
            highlight={missing}
            highlightNote="dropped by the child"
          />
        </div>

        {counterexample && (
          <div className="flex flex-col gap-2.5">
            <span className="colhead">Counterexample, verbatim from the solver</span>
            <pre className="overflow-x-auto border border-gray-03 bg-gray-01 px-[18px] py-3.5 font-mono text-[0.8125rem] leading-[1.6] text-ink">
              <span className="text-ink-4">
                ; {entailment.solver_result} · the child authorises it, the parent does not
              </span>
              {"\n"}
              <Field
                name="amount"
                value={String(counterexample.amount)}
                note={money(counterexample.amount)}
              />
              <Field name="merchant" value={counterexample.merchant} />
              <Field name="mcc" value={counterexample.mcc} />
              <Field name="category" value={counterexample.category} />
              <Field name="geo" value={counterexample.geo} />
              <Field name="ts" value={String(counterexample.ts)} />
              <Field
                name="cumulative"
                value={String(counterexample.cumulative)}
                note={money(counterexample.cumulative)}
              />
            </pre>
          </div>
        )}

        <Caveat>
          Decidable only over quantifier-free linear integer arithmetic and finite sets;
          `unknown` fails closed.
        </Caveat>
      </div>
    </SquarePanel>
  );
}

function Field({ name, value, note }: { name: string; value: string; note?: string }) {
  return (
    <>
      {"  "}
      <span className="text-blue">{name.padEnd(11)}</span>
      <span>{value}</span>
      {note && <span className="text-ink-4">{`  ; ${note}`}</span>}
      {"\n"}
    </>
  );
}

function VerdictCell({
  label,
  verdict,
  ok,
  wrong,
  note,
}: {
  label: string;
  verdict: string;
  ok: boolean;
  wrong?: boolean;
  note: ReactNode;
}) {
  const tone = wrong ? "text-attention-ink" : ok ? "text-success" : "text-warning";
  return (
    <div className="flex min-w-0 flex-col gap-2 bg-white px-[18px] py-4">
      <span className="colhead">{label}</span>
      <span className={`num text-[1.1875rem] leading-none font-semibold break-words ${tone}`}>
        {verdict}
      </span>
      <span className="text-[0.84375rem] leading-[1.5] break-words text-ink-3">{note}</span>
    </div>
  );
}

function ScopeColumn({
  label,
  lines,
  highlight = [],
  highlightNote,
}: {
  label: string;
  lines: string[];
  highlight?: string[];
  highlightNote?: string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <span className="colhead">{label}</span>
      <ul className="flex flex-col gap-1">
        {lines.map((line, index) => {
          const dropped = highlight.includes(line);
          return (
            <li
              key={index}
              className={`num text-[0.84375rem] leading-[1.5] break-words ${
                dropped ? "text-warning" : "text-ink-2"
              }`}
            >
              {line}
              {dropped && highlightNote && (
                <span className="text-warning"> ← {highlightNote}</span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
