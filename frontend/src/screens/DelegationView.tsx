/**
 * Beat 2 — the escalation proof.
 *
 * The delegation tree on the left, the proof on the right. The argument the panel has to
 * make is not "Z3 said no"; it is that a set-containment check — the check a competent
 * engineer writes first — says YES to the same hop. So both verdicts are rendered at the
 * same weight, side by side, and the counterexample is printed exactly as the solver
 * returned it.
 */

import { useEffect, useMemo, useState } from "react";
import { Button, Caveat, Empty, Panel } from "../components/ui";
import { money, ms as fmtMs } from "../lib/format";
import { useConsole } from "../lib/store";
import type { DelegationRecord, MandateNode } from "../lib/types";

const NODE_W = 252;
const NODE_H = 138;
const GAP_X = 76;
const GAP_Y = 22;

/** A node before layout. Kept separate so `place` is not an Omit over a union. */
type NodeSeed =
  | { kind: "mandate"; id: string; depth: number; mandate: MandateNode }
  | { kind: "rejected"; id: string; depth: number; delegation: DelegationRecord };

type GraphNode = NodeSeed & { x: number; y: number };

interface Graph {
  nodes: GraphNode[];
  edges: { from: GraphNode; to: GraphNode; rejected: boolean }[];
  width: number;
  height: number;
}

function buildGraph(mandates: MandateNode[], delegations: DelegationRecord[]): Graph {
  const byDepth = new Map<number, GraphNode[]>();
  const byId = new Map<string, GraphNode>();

  const place = (node: NodeSeed): GraphNode => {
    const column = byDepth.get(node.depth) ?? [];
    const positioned: GraphNode = {
      ...node,
      x: node.depth * (NODE_W + GAP_X),
      y: column.length * (NODE_H + GAP_Y),
    };
    column.push(positioned);
    byDepth.set(node.depth, column);
    byId.set(positioned.id, positioned);
    return positioned;
  };

  for (const mandate of [...mandates].sort((a, b) => a.depth - b.depth)) {
    place({ kind: "mandate", id: mandate.mandate_id, depth: mandate.depth, mandate });
  }

  const rejected = delegations.filter((d) => !d.accepted);
  for (const [index, delegation] of rejected.entries()) {
    const parent = byId.get(delegation.parent_id);
    const depth = parent ? parent.depth + 1 : 1;
    place({
      kind: "rejected",
      id: `rejected:${delegation.parent_id}:${index}`,
      depth,
      delegation,
    });
  }

  const edges: Graph["edges"] = [];
  for (const node of byId.values()) {
    const parentId =
      node.kind === "mandate" ? node.mandate.parent_id : node.delegation.parent_id;
    if (!parentId) continue;
    const parent = byId.get(parentId);
    if (parent) edges.push({ from: parent, to: node, rejected: node.kind === "rejected" });
  }

  let width = 0;
  let height = 0;
  for (const node of byId.values()) {
    width = Math.max(width, node.x + NODE_W);
    height = Math.max(height, node.y + NODE_H);
  }
  return { nodes: [...byId.values()], edges, width, height };
}

export function DelegationView() {
  const run = useConsole((s) => s.run);
  const running = useConsole((s) => s.running);
  const result = useConsole((s) => s.scenarios.escalation);
  const [selected, setSelected] = useState<string | null>(null);

  const delegations = useMemo(() => result?.delegations ?? [], [result]);
  const mandates = useMemo(() => {
    if (result?.mandates?.length) return result.mandates;
    return result?.root ? [result.root] : [];
  }, [result]);

  const graph = useMemo(() => buildGraph(mandates, delegations), [mandates, delegations]);

  useEffect(() => {
    // Land on the hop that carries the argument: the one the naive check got wrong.
    const fooled = delegations.findIndex((d) => d.naive_disagrees);
    if (fooled >= 0) setSelected(`d:${fooled}`);
    else if (delegations.length) setSelected("d:0");
  }, [delegations]);

  const selectedDelegation = selected?.startsWith("d:")
    ? delegations[Number(selected.slice(2))]
    : undefined;

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <header className="flex shrink-0 flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="display-xwide text-[1.7rem] leading-none font-semibold tracking-[-0.01em]">
            Fewer caveats, more authority.
          </h1>
          <p className="mt-1.5 text-body text-ink-2">
            {result?.narrative ??
              "Delegation is accepted iff UNSAT(child ∧ ¬parent). Set containment is the wrong test for money."}
          </p>
        </div>
        <Button
          size="md"
          tone="primary"
          disabled={running !== null}
          onClick={() => run("escalation")}
        >
          {running === "escalation" ? "Proving…" : "Run delegation hops"}
        </Button>
      </header>

      {!result ? (
        <Panel className="flex-1">
          <Empty>
            Run the delegation hops to render the mandate tree and the entailment proofs.
          </Empty>
        </Panel>
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] gap-3">
          <Panel
            title="Delegation tree"
            hint={`${mandates.length} live · ${delegations.filter((d) => !d.accepted).length} rejected`}
            bodyClassName="overflow-auto p-5"
          >
            <div
              className="relative"
              style={{ width: graph.width, height: graph.height, minWidth: "100%" }}
            >
              <svg
                className="pointer-events-none absolute inset-0"
                width={graph.width}
                height={graph.height}
                aria-hidden
              >
                {graph.edges.map((edge, index) => {
                  const x1 = edge.from.x + NODE_W;
                  const y1 = edge.from.y + NODE_H / 2;
                  const x2 = edge.to.x;
                  const y2 = edge.to.y + NODE_H / 2;
                  const mid = x1 + (x2 - x1) / 2;
                  return (
                    <path
                      key={index}
                      d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`}
                      fill="none"
                      stroke={edge.rejected ? "var(--color-deny)" : "var(--color-line-2)"}
                      strokeWidth={edge.rejected ? 1.5 : 1.5}
                      strokeDasharray={edge.rejected ? "4 4" : undefined}
                      opacity={edge.rejected ? 0.75 : 1}
                    />
                  );
                })}
              </svg>

              {graph.nodes.map((node) => {
                const key =
                  node.kind === "rejected"
                    ? `d:${delegations.indexOf(node.delegation)}`
                    : `m:${node.id}`;
                return (
                  <div
                    key={node.id}
                    style={{ left: node.x, top: node.y, width: NODE_W, height: NODE_H }}
                    className="absolute"
                  >
                    {node.kind === "mandate" ? (
                      <MandateCard mandate={node.mandate} />
                    ) : (
                      <RejectedCard
                        delegation={node.delegation}
                        active={selected === key}
                        onSelect={() => setSelected(key)}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </Panel>

          <div className="flex min-h-0 flex-col gap-3">
            <div className="flex shrink-0 flex-wrap gap-1.5">
              {delegations.map((delegation, index) => (
                <button
                  key={index}
                  type="button"
                  onClick={() => setSelected(`d:${index}`)}
                  className={`rounded border px-2 py-1 text-pill transition-colors ${
                    selected === `d:${index}`
                      ? delegation.accepted
                        ? "border-allow/50 bg-allow-wash text-allow"
                        : "border-deny/60 bg-deny-wash text-deny"
                      : "border-line bg-panel text-ink-3 hover:border-line-2 hover:text-ink-2"
                  }`}
                >
                  {delegation.title ?? `hop ${index + 1}`}
                </button>
              ))}
            </div>
            {selectedDelegation ? (
              <ProofPanel delegation={selectedDelegation} />
            ) : (
              <Panel className="flex-1">
                <Empty>Select a delegation hop.</Empty>
              </Panel>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------

function MandateCard({ mandate }: { mandate: MandateNode }) {
  return (
    <div className="flex h-full flex-col gap-1.5 rounded-md border border-line-2 bg-raised p-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="break-words text-body font-medium">{mandate.holder}</span>
        <span className="num shrink-0 rounded border border-line-2 px-1 text-pill text-ink-3">
          d{mandate.depth}
        </span>
      </div>
      <div className="num break-words text-pill text-ink-4">{mandate.mandate_id}</div>
      <ul className="min-h-0 flex-1 overflow-hidden">
        {mandate.scope_display.slice(0, 5).map((line, index) => (
          <li key={index} className="num break-words text-pill leading-[1.45] text-ink-2">
            {line}
          </li>
        ))}
        {mandate.scope_display.length > 5 && (
          <li className="num text-pill text-ink-4">
            +{mandate.scope_display.length - 5} more
          </li>
        )}
      </ul>
      <div className="num text-pill text-ink-4">
        {mandate.caveat_count} caveats
        {mandate.entailment_ms !== null && ` · proved in ${fmtMs(mandate.entailment_ms)}`}
      </div>
    </div>
  );
}

function RejectedCard({
  delegation,
  active,
  onSelect,
}: {
  delegation: DelegationRecord;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`flex h-full w-full flex-col gap-1.5 rounded-md border border-dashed p-3 text-left transition-colors ${
        active
          ? "border-deny bg-deny-wash"
          : "border-deny/50 bg-deny-wash/40 hover:border-deny"
      }`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="break-words text-body font-medium text-deny">
          {delegation.child_holder}
        </span>
        <span className="num shrink-0 text-pill text-deny">REJECTED</span>
      </div>
      <div className="num break-words text-pill text-deny/70">SCOPE_ESCALATION</div>
      <ul className="min-h-0 flex-1 overflow-hidden">
        {delegation.entailment.child_scope.slice(0, 4).map((line, index) => (
          <li key={index} className="num break-words text-pill leading-[1.45] text-ink-2">
            {line}
          </li>
        ))}
      </ul>
      {delegation.naive_disagrees && (
        <div className="num text-pill text-stepup">subset check said: narrower</div>
      )}
    </button>
  );
}

function ProofPanel({ delegation }: { delegation: DelegationRecord }) {
  const { entailment } = delegation;
  const counterexample = entailment.counterexample;
  const missing = entailment.parent_scope.filter((c) => !entailment.child_scope.includes(c));

  return (
    <Panel
      title={delegation.title ?? "Delegation hop"}
      hint={`child ${delegation.child_holder}`}
      tone={delegation.accepted ? "default" : "deny"}
      right={
        <span className="num text-pill text-ink-3">
          entailment {fmtMs(entailment.elapsed_ms)}
        </span>
      }
      bodyClassName="min-h-0 overflow-y-auto p-4 flex flex-col gap-4"
    >
      <div className="grid grid-cols-2 gap-3">
        <VerdictColumn
          label="Naive subset check"
          verdict={delegation.naive_subset_check ? "NARROWER" : "WIDER"}
          ok={delegation.naive_subset_check}
          wrong={delegation.naive_disagrees}
          note="every caveat the child declares is present on the parent and no looser"
        />
        <VerdictColumn
          label="Z3 entailment"
          verdict={entailment.entailed ? "ENTAILED" : "SCOPE_ESCALATION"}
          ok={entailment.entailed}
          note={`solver returned ${entailment.solver_result} · ${
            entailment.entailed ? "no such transaction exists" : "a witness exists"
          }`}
        />
      </div>

      {/* The two checks can disagree in either direction, and both directions are worth
          showing: containment is unsound on a dropped constraint and incomplete on a
          chain that has accumulated two bounds of the same kind. Each branch used to carry
          a paragraph explaining that mechanism; the scope columns below already mark the
          dropped constraint and the counterexample already exhibits it, so only the verdict
          sentence is kept. */}
      {delegation.naive_disagrees && (
        <div className="anim-stamp rounded border border-stepup/50 bg-stepup-wash px-3 py-2">
          <div className="text-body font-medium text-stepup">
            {delegation.naive_subset_check
              ? "The subset check was about to authorise this hop."
              : "The subset check would have rejected this legitimate narrowing."}
          </div>
        </div>
      )}

      <div className="num rounded border border-line bg-sunken px-3 py-2 text-pill text-proof">
        accept ⟺ UNSAT( child ∧ ¬parent )
      </div>

      <div className="grid grid-cols-2 gap-3">
        <ScopeColumn label="Child declared scope" lines={entailment.child_scope} />
        <ScopeColumn
          label="Parent scope"
          lines={entailment.parent_scope}
          highlight={missing}
          highlightNote="dropped by the child"
        />
      </div>

      {counterexample ? (
        <div className="flex flex-col gap-2">
          <div className="eyebrow">Counterexample, verbatim from the solver</div>
          <pre className="overflow-x-auto rounded border border-deny/40 bg-deny-wash/60 p-3 font-mono text-pill leading-[1.6] text-ink">
            <span className="text-ink-4">
              ; {entailment.solver_result} · the child authorises it, the parent does not
            </span>
            {"\n"}
            <Field name="amount" value={String(counterexample.amount)} note={money(counterexample.amount)} />
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
          <div className="eyebrow">Parent constraints this witness breaks</div>
          <ul className="flex flex-col gap-0.5">
            {counterexample.violated.map((line, index) => (
              <li key={index} className="num text-pill text-deny">
                {line}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="rounded border border-allow/35 bg-allow-wash px-3 py-2 text-pill text-allow">
          No counterexample exists. The child's authority region is provably inside the parent's.
        </div>
      )}

      <Caveat>
        Decidable only over quantifier-free linear integer arithmetic and finite sets; `unknown`
        fails closed.
      </Caveat>
    </Panel>
  );
}

function Field({ name, value, note }: { name: string; value: string; note?: string }) {
  return (
    <>
      {"  "}
      <span className="text-proof">{name.padEnd(11)}</span>
      <span>{value}</span>
      {note && <span className="text-ink-4">{`  ; ${note}`}</span>}
      {"\n"}
    </>
  );
}

function VerdictColumn({
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
  note: string;
}) {
  const tone = wrong ? "text-stepup" : ok ? "text-allow" : "text-deny";
  const border = wrong ? "border-stepup/40" : ok ? "border-allow/35" : "border-deny/50";
  return (
    <div className={`flex flex-col gap-1 rounded border ${border} bg-sunken px-3 py-2.5`}>
      <div className="eyebrow">{label}</div>
      <div className={`num display-wide text-[1.15rem] leading-none font-semibold ${tone}`}>
        {verdict}
      </div>
      <div className="text-pill leading-snug text-ink-4">{note}</div>
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
    <div className="flex flex-col gap-1">
      <div className="eyebrow">{label}</div>
      <ul className="flex flex-col gap-0.5">
        {lines.map((line, index) => {
          const dropped = highlight.includes(line);
          return (
            <li
              key={index}
              className={`num text-pill leading-[1.5] ${dropped ? "text-deny" : "text-ink-2"}`}
            >
              {line}
              {dropped && highlightNote && (
                <span className="ml-1.5 text-pill text-deny/70">← {highlightNote}</span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
