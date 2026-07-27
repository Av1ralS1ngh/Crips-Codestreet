/**
 * Beat 1 — the intuitive answer overstates.
 *
 * Same cart, same card, two columns. On the left, per-line summation. On the right, a value
 * backed by a concrete allocation that satisfies every balance and every exclusivity group.
 * The right number is lower, and it is the only one of the two that can actually be
 * delivered.
 *
 * Two things this screen refuses to do:
 *
 *   * It does not argue with a strawman. The left column carries both the literal per-line
 *     sum and the strongest figure a per-line implementation can reach — one that reads
 *     each balance and clamps to it. The witness still comes in below that, and the
 *     reconciliation names which rule cost what.
 *   * It does not assert that the witness is valid. It re-checks it in the browser, in
 *     linear time, with no solver, and prints its own verdict beside the evaluator's.
 */

import { Bar, Caveat, Panel, Provenance, Toggle } from "../components/ui";
import {
  HashRow,
  KindChip,
  ScreenHeader,
  PlumblineUnavailable,
  VerifyPair,
} from "../components/plumblineUi";
import { ruleFor } from "../lib/derive";
import { money, ms as fmtMs } from "../lib/format";
import { activeInstrument, manifestFor, useConsole } from "../lib/store";
import { useLocalVerification } from "../lib/useWitness";
import {
  CAUSE_COPY,
  type Collision,
  type DerivationRow,
  type LatencyTable,
} from "../lib/plumbline";
import type { Cart, CartLine } from "../lib/types";

export function OverstatementView() {
  const plumbline = useConsole((s) => s.plumbline);
  const plumblineError = useConsole((s) => s.plumblineError);
  const instrumentId = useConsole((s) => s.instrumentId);
  const setInstrument = useConsole((s) => s.setInstrument);

  const instrument = activeInstrument(plumbline, instrumentId);
  const manifest = manifestFor(plumbline, instrument?.instrument_id ?? "");
  const local = useLocalVerification(
    instrument?.witness ?? null,
    manifest,
    plumbline?.cart ?? null,
    instrument?.asserted_minor ?? 0,
    plumbline?.cart_hash,
  );

  if (!plumbline || !instrument) return <PlumblineUnavailable error={plumblineError} />;

  const { reconciliation: rec } = instrument;
  const cart = plumbline.cart;

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <ScreenHeader
        beat="01"
        title="The intuitive answer overstates."
        right={
          <Toggle
            value={instrument.instrument_id}
            onChange={setInstrument}
            options={plumbline.valuation.instruments.map((i) => ({
              value: i.instrument_id,
              label: i.product,
            }))}
          />
        }
      >
        {plumbline.valuation.narrative}
      </ScreenHeader>

      {/* ------------------------------------------------------------------ the two numbers */}
      <div className="grid shrink-0 grid-cols-[minmax(0,1fr)_minmax(0,0.85fr)_minmax(0,1fr)] gap-3">
        <Panel
          title="Per-line summation"
          hint="every eligible benefit against every line it admits"
          tone="deny"
          bodyClassName="flex flex-col gap-2 p-4"
        >
          <div className="num display-wide text-[2.9rem] leading-none font-semibold text-deny">
            {rec.naive_display}
          </div>
          <div className="flex items-baseline justify-between gap-3 border-t border-line pt-2">
            <span className="text-pill text-ink-3">
              same sum, each pairing capped at its remaining balance
            </span>
            <span className="num shrink-0 text-body font-medium text-deny/85">
              {rec.capped_display}
            </span>
          </div>
          {/* Was three sentences on why a per-line clamp is still blind across lines. The
              two figures above it make the point without the prose. */}
          <Caveat>
            The strongest a per-line valuation can reach. It still cannot see one balance
            spent twice across two lines.
          </Caveat>
        </Panel>

        <Panel
          title="Overstated by"
          hint="reconciled to the rupee"
          bodyClassName="flex flex-col gap-2.5 p-4"
        >
          <div className="num display-wide text-[2.9rem] leading-none font-semibold text-deny">
            {rec.overstatement_display}
          </div>
          <div className="flex flex-col gap-1.5">
            {rec.by_cause.map((cause) => (
              <div key={cause.cause} className="flex flex-col gap-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="min-w-0 break-words text-pill text-ink-2">
                    {CAUSE_COPY[cause.cause]?.label ?? cause.cause}
                  </span>
                  <span className="num shrink-0 text-pill text-ink">{cause.display}</span>
                </div>
                <Bar value={cause.minor} total={rec.overstatement_minor} tone="deny" />
              </div>
            ))}
          </div>
        </Panel>

        <Panel
          title="Witness-backed"
          hint="a concrete allocation realises this much"
          bodyClassName="flex flex-col gap-2 p-4"
        >
          <div className="num display-wide text-[2.9rem] leading-none font-semibold text-allow">
            {rec.witness_display}
          </div>
          <VerifyPair server={instrument.verification} local={local} />
          <HashRow label="witness sha256" value={instrument.witness_hash} />
        </Panel>
      </div>

      {/* -------------------------------------------------------------------- the derivation */}
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,2.05fr)_minmax(0,1fr)] gap-3">
        <Panel
          title="The allocation"
          hint="re-add the column and you have the assertion"
          right={
            <span className="num text-pill text-ink-4">
              {instrument.allocator_stats.considered} pairings considered ·{" "}
              {instrument.allocator_stats.assigned} assigned
            </span>
          }
          bodyClassName="min-h-0 overflow-y-auto"
        >
          <AllocationTable
            cart={cart}
            rows={instrument.derivation}
            collisions={instrument.collisions}
            totalMinor={instrument.asserted_minor}
            totalDisplay={instrument.asserted_display}
          />
        </Panel>

        <div className="flex min-h-0 flex-col gap-3">
          <CollisionPanel collisions={instrument.collisions} />
          <LatencyPanel latency={plumbline.valuation.latency} localMs={local?.elapsedMs ?? null} />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------

interface Grouped {
  line: CartLine;
  assigned: DerivationRow[];
  struck: {
    label: string;
    group: string;
    valueMinor: number;
    winnerLabel: string;
  }[];
}

/**
 * Grouped by cart line rather than flat, because the claim being made is per line: this
 * dinner was paid for once. A flat list of assignments hides exactly the collision the
 * screen exists to show.
 */
function group(cart: Cart, rows: DerivationRow[], collisions: Collision[]): Grouped[] {
  return cart.lines.map((line) => {
    const struck: Grouped["struck"] = [];
    for (const collision of collisions) {
      if (collision.line_sku !== line.sku) continue;
      for (const loser of collision.struck) {
        // What it would actually have delivered had it won — not its uncapped headline,
        // which would overstate the loser exactly as the left column overstates the card.
        const deliverable =
          loser.capacity_minor === null
            ? loser.value_minor
            : Math.min(loser.value_minor, loser.capacity_minor);
        struck.push({
          label: loser.label,
          group: collision.exclusivity_group,
          valueMinor: deliverable,
          winnerLabel: collision.winner.label,
        });
      }
    }
    return {
      line,
      assigned: rows.filter((r) => r.line_sku === line.sku),
      struck,
    };
  });
}

function AllocationTable({
  cart,
  rows,
  collisions,
  totalMinor,
  totalDisplay,
}: {
  cart: Cart;
  rows: DerivationRow[];
  collisions: Collision[];
  totalMinor: number;
  totalDisplay: string;
}) {
  const groups = group(cart, rows, collisions);

  return (
    <table className="w-full border-collapse text-pill">
      <thead>
        <tr className="sticky top-0 z-10 bg-panel">
          <th className="eyebrow border-b border-line px-3.5 py-2 text-left font-semibold">
            Line · benefit
          </th>
          <th className="eyebrow border-b border-line px-2 py-2 text-left font-semibold">
            Kind
          </th>
          <th className="eyebrow border-b border-line px-2 py-2 text-left font-semibold">
            Rule from the manifest
          </th>
          <th className="eyebrow border-b border-line px-2 py-2 text-right font-semibold">
            Balance before → after
          </th>
          <th className="eyebrow border-b border-line px-3.5 py-2 text-right font-semibold">
            Value
          </th>
        </tr>
      </thead>
      <tbody>
        {groups.map((g) => (
          <LineGroup key={g.line.sku} group={g} />
        ))}
        <tr>
          <td colSpan={3} className="border-t-2 border-line-2 px-3.5 py-3">
            <span className="eyebrow">Witness total, the asserted value</span>
          </td>
          <td className="border-t-2 border-line-2 px-2 py-3 text-right">
            <span className="num text-pill text-ink-4">{rows.length} assignments</span>
          </td>
          <td className="border-t-2 border-line-2 px-3.5 py-3 text-right">
            <span
              className="num display-wide text-[1.35rem] leading-none font-semibold text-allow"
              title={`${totalMinor} minor units`}
            >
              {totalDisplay}
            </span>
          </td>
        </tr>
      </tbody>
    </table>
  );
}

function LineGroup({ group: g }: { group: Grouped }) {
  return (
    <>
      <tr className="bg-sunken/70">
        <td className="border-t border-line px-3.5 py-1.5" colSpan={3}>
          <div className="flex items-baseline gap-2.5">
            <span className="num shrink-0 text-pill text-ink-4">{g.line.sku}</span>
            <span className="min-w-0 break-words font-medium text-ink">{g.line.description}</span>
          </div>
        </td>
        <td className="border-t border-line px-2 py-1.5 text-right">
          <span className="num text-pill text-ink-4">MCC {g.line.mcc}</span>
        </td>
        <td className="border-t border-line px-3.5 py-1.5 text-right">
          <span className="num text-body text-ink-2">{money(g.line.amount)}</span>
        </td>
      </tr>

      {g.assigned.map((row) => (
        <tr key={`${row.line_sku}-${row.benefit_id}`} className="hover:bg-raised/40">
          <td className="px-3.5 py-1.5 pl-7">
            <span className="min-w-0 break-words text-ink-2">{row.benefit_label}</span>
          </td>
          <td className="px-2 py-1.5">
            <KindChip kind={row.benefit_kind} />
          </td>
          <td className="num px-2 py-1.5 text-pill text-ink-3">{ruleFor(row)}</td>
          <td className="num px-2 py-1.5 text-right text-pill text-ink-4">
            {row.capacity_before === null ? (
              <span>no ceiling</span>
            ) : (
              <span>
                {money(row.capacity_before)} → {money(row.capacity_after ?? 0)}
              </span>
            )}
          </td>
          <td className="num px-3.5 py-1.5 text-right text-body text-ink">
            {row.value_display}
          </td>
        </tr>
      ))}

      {g.struck.map((row) => (
        <tr key={`${g.line.sku}-struck-${row.label}`} className="bg-deny-wash/25">
          <td className="px-3.5 py-1.5 pl-7">
            <span className="break-words text-deny/80 line-through decoration-deny/70">
              {row.label}
            </span>
          </td>
          <td className="px-2 py-1.5">
            <span className="inline-block rounded border border-deny/45 px-1 py-px font-mono text-pill tracking-[0.08em] text-deny uppercase">
              struck
            </span>
          </td>
          <td className="num px-2 py-1.5 text-pill text-deny/85" colSpan={2}>
            exclusivity group {row.group}, claimed by {row.winnerLabel}
          </td>
          <td className="num px-3.5 py-1.5 text-right text-body text-deny/70 line-through decoration-deny/60">
            {money(row.valueMinor)}
          </td>
        </tr>
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------------------

function CollisionPanel({ collisions }: { collisions: Collision[] }) {
  if (collisions.length === 0) {
    return (
      <Panel title="Exclusivity" bodyClassName="p-4">
        <span className="text-pill text-ink-4">
          No two benefits on this card competed for the same line in this cart.
        </span>
      </Panel>
    );
  }
  return (
    <Panel
      title="Two credits, one line"
      hint="what per-line summation cannot express"
      tone="deny"
      bodyClassName="flex flex-col gap-3 p-4"
    >
      {collisions.map((collision) => {
        const struckTotal = collision.struck.reduce(
          (sum, s) =>
            sum +
            (s.capacity_minor === null ? s.value_minor : Math.min(s.value_minor, s.capacity_minor)),
          0,
        );
        return (
          <div key={`${collision.line_sku}-${collision.exclusivity_group}`} className="flex flex-col gap-2">
            <div className="flex items-baseline justify-between gap-2">
              <span className="min-w-0 break-words text-body text-ink">
                {collision.line_description}
              </span>
              <span className="num shrink-0 text-pill text-ink-3">
                {money(collision.line_amount)}
              </span>
            </div>
            <div className="num rounded border border-deny/35 bg-deny-wash/60 px-2 py-1 text-pill text-deny">
              {collision.exclusivity_group}
            </div>
            <div className="flex flex-col gap-1">
              <div className="flex items-baseline justify-between gap-2">
                <span className="min-w-0 break-words text-pill text-allow">
                  {collision.winner.label}
                </span>
                <span className="num shrink-0 text-pill text-allow">
                  {collision.winner.value_display}
                </span>
              </div>
              {collision.struck.map((loser) => (
                <div key={loser.benefit_id} className="flex items-baseline justify-between gap-2">
                  <span className="min-w-0 break-words text-pill text-deny/80 line-through decoration-deny/70">
                    {loser.label}
                  </span>
                  <span className="num shrink-0 text-pill text-deny/70 line-through decoration-deny/60">
                    {money(
                      loser.capacity_minor === null
                        ? loser.value_minor
                        : Math.min(loser.value_minor, loser.capacity_minor),
                    )}
                  </span>
                </div>
              ))}
            </div>
            <Caveat>
              A per-line sum adds {money(struckTotal)} the card will never pay.
            </Caveat>
          </div>
        );
      })}
    </Panel>
  );
}

function LatencyPanel({
  latency,
  localMs,
}: {
  latency: LatencyTable;
  localMs: number | null;
}) {
  const { bench, bench_verify: verify, solver } = latency;
  return (
    <Panel
      title="Latency at the same problem size"
      hint={bench.problem_size}
      tone="proof"
      className="min-h-0"
      bodyClassName="flex flex-col gap-3 p-4 overflow-y-auto min-h-0"
    >
      <div className="grid grid-cols-2 gap-3">
        <Provenance
          label="Greedy allocator"
          value={`${bench.p50_ms.toFixed(2)} ms`}
          tone="allow"
          method={`p50 over ${bench.runs} runs · p99 ${bench.p99_ms.toFixed(2)} ms · measured`}
        />
        <Provenance
          label="MaxSMT alternative"
          value={`${solver.lo_ms}–${solver.hi_ms} ms`}
          tone="deny"
          method={`${solver.source} · not measured here`}
        />
      </div>

      <div className="flex flex-col gap-1.5 border-t border-line pt-2.5">
        <div className="flex items-baseline justify-between gap-2 text-pill">
          <span className="text-ink-3">Linear-time verification, no solver</span>
          <span className="num text-allow">{verify.p50_ms.toFixed(2)} ms</span>
        </div>
        {localMs !== null && (
          <div className="flex items-baseline justify-between gap-2 text-pill">
            <span className="text-ink-3">…and in this browser, this run</span>
            <span className="num text-allow">{fmtMs(localMs, 3)}</span>
          </div>
        )}
      </div>

      {/* Both caveats ran to three sentences. Cut to the two disclosures that have to
          survive: the solver's failure mode, and that greedy is not optimal. The
          NP-hardness argument for why there is no DP fallback is spoken, not rendered. */}
      <Caveat>{solver.failure_mode}.</Caveat>
      <Caveat>
        The allocator is conservative by construction but not optimal; gap measured offline.
        Entailment is a different component.
      </Caveat>
    </Panel>
  );
}
