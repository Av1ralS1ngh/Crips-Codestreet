/**
 * Beat 5 — which benefits actually win transactions.
 *
 * Every signed receipt records the full candidate set, each instrument's derivation and the
 * stated criterion. So the corpus is a running, cart-level record of which benefits appear
 * in a winning derivation and which never do.
 *
 * The 2x2 is re-keyed from usage to selection influence, and that re-keying is the whole
 * point. Benefit expense is recognised largely as benefits are used, so a usage-keyed
 * dashboard rewards exactly the thing that costs money. Decisiveness is measured by
 * removal: delete one benefit from its manifest, re-run the allocator on the same cart,
 * re-apply the same criterion, and see whether the selected instrument changes.
 *
 * The caveat travels with the number and is on screen, not in a footnote: this is influence
 * at the moment of choice. It is not retention. A benefit that never appears in a winning
 * derivation may still be why the card was taken out.
 */

import { Bar, Caveat, Panel } from "../components/ui";
import { ScreenHeader, PlumblineUnavailable } from "../components/plumblineUi";
import { money } from "../lib/format";
import { useConsole } from "../lib/store";
import {
  QUADRANT_COPY,
  QUADRANT_DEAD_WEIGHT,
  QUADRANT_LOAD_BEARING,
  QUADRANT_NOISE,
  QUADRANT_OPTION,
  type AttributionBook,
  type BenefitAttribution,
  type Quadrant,
} from "../lib/plumbline";

/** Reading order of the 2x2: high cost on top, often-decisive on the right. */
const GRID: Quadrant[] = [
  QUADRANT_DEAD_WEIGHT,
  QUADRANT_LOAD_BEARING,
  QUADRANT_NOISE,
  QUADRANT_OPTION,
];

const TONE: Record<Quadrant, { border: string; text: string; bar: "deny" | "allow" | "proof" | "default" }> = {
  [QUADRANT_DEAD_WEIGHT]: { border: "border-deny/45 bg-deny-wash/35", text: "text-deny", bar: "deny" },
  [QUADRANT_LOAD_BEARING]: { border: "border-allow/40 bg-allow-wash/40", text: "text-allow", bar: "allow" },
  [QUADRANT_NOISE]: { border: "border-line", text: "text-ink-3", bar: "default" },
  [QUADRANT_OPTION]: { border: "border-proof/40 bg-proof-wash/40", text: "text-proof", bar: "proof" },
};

export function AttributionView() {
  const plumbline = useConsole((s) => s.plumbline);
  const plumblineError = useConsole((s) => s.plumblineError);

  if (!plumbline) return <PlumblineUnavailable error={plumblineError} />;

  const book = plumbline.attribution;
  const maxCost = Math.max(...book.benefits.map((b) => b.annual_cost_minor), 1);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <ScreenHeader
        beat="06"
        title="Which benefit dollars actually caused a card to be chosen."
        right={
          <div className="num flex items-baseline gap-4 text-pill text-ink-4">
            <span>
              corpus <span className="text-ink-2">{book.corpus_size}</span> receipts
            </span>
            <span>
              decisive threshold{" "}
              <span className="text-ink-2">{(book.thresholds.decisive_bp / 100).toFixed(0)}%</span>
            </span>
            <span>
              high cost above{" "}
              <span className="text-ink-2">{book.thresholds.high_cost_display}</span> / yr
            </span>
          </div>
        }
      >
        Influence at the moment of choice, not a renewal signal that takes two years to
        reach the P&amp;L.
      </ScreenHeader>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)] gap-3">
        {/* ------------------------------------------------------------------ the 2x2 */}
        <div className="flex min-h-0 flex-col gap-2">
          <div className="grid min-h-0 flex-1 grid-cols-2 grid-rows-2 gap-2">
            {GRID.map((quadrant) => (
              <QuadrantBox
                key={quadrant}
                quadrant={quadrant}
                benefits={book.benefits.filter((b) => b.quadrant === quadrant)}
                maxCost={maxCost}
              />
            ))}
          </div>
          <div className="flex shrink-0 items-center justify-between px-1 text-pill text-ink-4">
            <span>← rarely decisive</span>
            <span className="eyebrow">selection influence</span>
            <span>often decisive →</span>
          </div>
        </div>

        {/* ----------------------------------------------------------------- the table */}
        <Panel
          title="Every benefit, measured"
          hint="decisive = deleting it changes which instrument the criterion selects"
          className="min-h-0"
          bodyClassName="flex min-h-0 flex-col"
        >
          <div className="min-h-0 flex-1 overflow-y-auto">
            <BenefitTable book={book} />
          </div>
          <div className="shrink-0 border-t border-line px-3.5 py-2.5">
            <Caveat>{book.caveat}</Caveat>
            {/* The reason a nudge product is the wrong shape (breakage becomes recognised
                expense) was cut. The constraint it justifies stays on screen. */}
            <div className="mt-1.5 flex flex-col gap-0.5 text-pill text-ink-4">
              <span>Method · {book.method}.</span>
              <span>Cost · {book.cost_source}.</span>
              <span>
                Nothing here is ever shown to a Card Member. The number goes into an agent's
                ranking, not a member's inbox.
              </span>
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------

function QuadrantBox({
  quadrant,
  benefits,
  maxCost,
}: {
  quadrant: Quadrant;
  benefits: BenefitAttribution[];
  maxCost: number;
}) {
  const copy = QUADRANT_COPY[quadrant];
  const tone = TONE[quadrant];
  const ordered = [...benefits].sort((a, b) => b.annual_cost_minor - a.annual_cost_minor);

  return (
    <section className={`flex min-h-0 min-w-0 flex-col rounded-md border ${tone.border}`}>
      <header className="flex shrink-0 flex-col gap-0.5 border-b border-line/70 px-3 py-2">
        <div className="flex items-baseline justify-between gap-3">
          <h3 className={`display-wide text-[1.15rem] leading-none font-semibold ${tone.text}`}>
            {copy.title}
          </h3>
          <span className="num shrink-0 text-pill text-ink-4">
            {benefits.length} benefit{benefits.length === 1 ? "" : "s"}
          </span>
        </div>
        <div className="flex flex-wrap items-baseline justify-between gap-x-3">
          <span className="num text-pill text-ink-4">
            {copy.cost} · {copy.influence}
          </span>
          <span className={`text-pill ${tone.text}`}>{copy.verdict}</span>
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
        {ordered.length === 0 ? (
          <p className="pt-1 text-pill leading-snug text-ink-4">
            Nothing in this corpus lands here. An empty quadrant is a finding, not a gap.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {ordered.map((benefit) => (
              <li key={benefit.benefit_id} className="flex flex-col gap-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="min-w-0 break-words text-body text-ink">{benefit.label}</span>
                  <span className="num shrink-0 text-pill text-ink-2">
                    {benefit.annual_cost_display}
                  </span>
                </div>
                <Bar value={benefit.annual_cost_minor} total={maxCost} tone={tone.bar} />
                <div className="num flex items-baseline justify-between gap-2 text-pill text-ink-4">
                  <span>{benefit.product}</span>
                  <span>
                    decisive on {benefit.decisive} of {benefit.in_candidate_set} receipts
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function BenefitTable({ book }: { book: AttributionBook }) {
  const maxDecisive = Math.max(...book.benefits.map((b) => b.decisive_bp), 1);

  return (
    <table className="w-full table-fixed border-collapse text-pill">
      <colgroup>
        <col />
        <col className="w-[7.5rem]" />
        <col className="w-[8.5rem]" />
        <col className="w-[6.5rem]" />
      </colgroup>
      <thead>
        <tr className="sticky top-0 z-10 bg-panel">
          <th className="eyebrow border-b border-line px-3 py-2 text-left font-semibold">
            Benefit
          </th>
          <th className="eyebrow border-b border-line px-2 py-2 text-right font-semibold">
            In a win
          </th>
          <th className="eyebrow border-b border-line px-2 py-2 text-right font-semibold">
            Decisive
          </th>
          <th className="eyebrow border-b border-line px-3 py-2 text-right font-semibold">
            Cost / yr
          </th>
        </tr>
      </thead>
      <tbody>
        {book.benefits.map((benefit) => (
          <tr key={benefit.benefit_id} className="border-b border-line/50 hover:bg-raised/40">
            <td className="px-3 py-1.5">
              <div className="break-words text-ink-2">{benefit.label}</div>
              <span className="num text-pill text-ink-4">{benefit.product}</span>
            </td>
            <td className="num px-2 py-1.5 text-right align-middle text-ink-3">
              {benefit.in_winning_derivation}
              <span className="text-ink-4"> / {benefit.in_candidate_set}</span>
            </td>
            <td className="px-2 py-1.5 align-middle">
              <div className="flex items-center justify-end gap-2">
                <div className="w-10 shrink-0">
                  <Bar
                    value={benefit.decisive_bp}
                    total={maxDecisive}
                    tone={benefit.often_decisive ? "allow" : "default"}
                  />
                </div>
                <span
                  className={`num w-11 shrink-0 text-right text-pill ${
                    benefit.often_decisive ? "text-allow" : "text-ink-4"
                  }`}
                  title={`${benefit.decisive} of ${benefit.in_candidate_set} receipts`}
                >
                  {(benefit.decisive_bp / 100).toFixed(1)}%
                </span>
              </div>
            </td>
            <td
              className={`num px-3 py-1.5 text-right align-middle ${
                benefit.high_cost ? "text-ink" : "text-ink-4"
              }`}
              title={`${benefit.annual_cost_minor} minor units`}
            >
              {benefit.annual_cost_display}
            </td>
          </tr>
        ))}
        <tr>
          <td colSpan={3} className="border-t-2 border-line-2 px-3 py-2">
            <span className="eyebrow">Modelled annual cost, measured benefits</span>
          </td>
          <td className="num border-t-2 border-line-2 px-3 py-2 text-right text-body font-medium text-ink">
            {money(book.benefits.reduce((sum, b) => sum + b.annual_cost_minor, 0))}
          </td>
        </tr>
      </tbody>
    </table>
  );
}
