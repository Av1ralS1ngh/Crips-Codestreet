/**
 * Kernel, tab 2 — a mandate can be narrowed and never widened.
 *
 * The tree is indented by position rather than by the `depth` field, so a rejected hop
 * hangs off the mandate it tried to widen. A rejected hop never became a mandate, which is
 * the whole point: it has no row in the chain, only an attempt in the log.
 *
 * The clauses under the tree are printed exactly as the solver returned them, against the
 * hop the naive subset check got wrong — the one the argument rests on.
 */

import { useMemo } from "react";
import { SquarePanel } from "../components/plumblineUi";
import { Button } from "../components/ui";
import { money } from "../lib/format";
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
  | { kind: "rejected"; key: string; depth: number; delegation: DelegationRecord };

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
      out.push({ kind: "rejected", key: `rejected:${index}`, depth: depth + 1, delegation });
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

  const delegations = useMemo(() => result?.delegations ?? [], [result]);
  const mandates = useMemo(() => {
    if (result?.mandates?.length) return result.mandates;
    return result?.root ? [result.root] : [];
  }, [result]);

  const rows = useMemo(() => treeRows(mandates, delegations), [mandates, delegations]);

  // The hop that carries the argument: refused, with a witness, and preferring the one the
  // naive check got wrong.
  const witness = useMemo(() => {
    const refused = delegations.filter((d) => !d.accepted && d.entailment.counterexample);
    return refused.find((d) => d.naive_disagrees) ?? refused[0] ?? null;
  }, [delegations]);

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-5">
        <p className="min-w-0 text-[1.0625rem] leading-[1.6] font-bold text-ink">
          A mandate can be narrowed and never widened.
        </p>
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
          Run the delegation hops to render the mandate tree.
        </div>
      ) : (
        <SquarePanel title="Delegation tree">
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
                  <RejectedRow delegation={row.delegation} depth={row.depth} />
                )}
              </div>
            ))}
          </div>

          {witness?.entailment.counterexample && (
            <div className="flex flex-col gap-3 border-t border-gray-03 px-6 py-[22px]">
              <span className="num text-[0.65625rem] font-semibold tracking-[0.14em] text-warning uppercase">
                Parent constraints this witness breaks
              </span>
              {witness.entailment.counterexample.violated.map((line, i) => (
                <div
                  key={i}
                  className="flex items-baseline gap-3.5 border-l-[3px] border-warning bg-gray-01 px-[18px] py-3.5"
                >
                  <span className="num shrink-0 text-[0.78125rem] text-warning">
                    {REASON_SCOPE_ESCALATION}
                  </span>
                  <span className="num min-w-0 text-[0.9375rem] leading-[1.5] break-words text-ink">
                    {line}
                  </span>
                </div>
              ))}
            </div>
          )}
        </SquarePanel>
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
      <span
        className={`min-w-0 flex-1 text-[0.96875rem] leading-[1.3] break-words ${
          depth === 0 ? "font-bold text-navy" : "text-ink"
        }`}
      >
        {node.holder}
      </span>
      {cap && <span className="num shrink-0 text-[0.84375rem] text-navy">{cap}</span>}
    </div>
  );
}

function RejectedRow({ delegation, depth }: { delegation: DelegationRecord; depth: number }) {
  const cap = declaredCap(delegation.entailment.child_scope);
  return (
    <div
      style={{ marginLeft: depth * INDENT_PX }}
      className="flex items-center gap-4 border border-warning bg-warning-row px-[18px] py-4"
    >
      <span className="num w-14 shrink-0 text-[0.6875rem] text-warning">HOP {depth}</span>
      <span className="min-w-0 flex-1 text-[0.96875rem] leading-[1.3] font-bold break-words text-warning">
        {delegation.child_holder}
      </span>
      {cap && <span className="num shrink-0 text-[0.84375rem] text-warning">{cap}</span>}
    </div>
  );
}
