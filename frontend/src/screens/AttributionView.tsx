/**
 * Beat 06 — which benefit dollars actually caused a card to be chosen.
 *
 * Every signed receipt records the full candidate set, each instrument's derivation and the
 * stated criterion, so the corpus is a cart-level record of which benefits appear in a
 * winning derivation and which never do.
 *
 * The 2x2 is re-keyed from usage to selection influence, and that re-keying is the whole
 * point. Benefit expense is recognised largely as benefits are used, so a usage-keyed
 * dashboard rewards exactly the thing that costs money.
 *
 * Two constraints this screen may never break. Nothing here is ever surfaced to a Card
 * Member: the number goes into an agent's ranking, not a member's inbox, because a nudge
 * converts breakage into recognised expense. And the caveat travels with the number rather
 * than sitting in a footnote — this is influence at the moment of choice, not retention.
 */

import { useState, type ReactNode } from "react";
import {
  BeatHeader,
  BeatPage,
  BeatSplit,
  PlumblineUnavailable,
  ShowsPanel,
  SquarePanel,
  Takeaway,
} from "../components/plumblineUi";
import { money } from "../lib/format";
import { useConsole } from "../lib/store";
import {
  QUADRANT_COPY,
  QUADRANT_DEAD_WEIGHT,
  QUADRANT_LOAD_BEARING,
  QUADRANT_NOISE,
  QUADRANT_OPTION,
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

/** Ten rows was too long to scan; five is the argument. The rest are one click away. */
const TOP_N = 5;

// #6E7A85 (tertiary ink) and #C3CCD5 (the neutral rule) are written out because Tailwind
// scans source text: a class assembled from a constant is never emitted. The console's own
// `ink-3` is a colder grey, so it is not a substitute here.
const TONE: Record<Quadrant, { top: string; text: string; pill: string; fill: string }> = {
  [QUADRANT_DEAD_WEIGHT]: {
    top: "border-t-deny",
    text: "text-deny",
    pill: "bg-deny-wash text-deny",
    fill: "bg-deny",
  },
  [QUADRANT_LOAD_BEARING]: {
    top: "border-t-success",
    text: "text-success",
    pill: "bg-success-wash text-success",
    fill: "bg-success",
  },
  [QUADRANT_NOISE]: {
    top: "border-t-[#C3CCD5]",
    text: "text-[#6E7A85]",
    pill: "bg-gray-02 text-[#6E7A85]",
    fill: "bg-[#6E7A85]",
  },
  [QUADRANT_OPTION]: {
    top: "border-t-blue",
    text: "text-blue",
    pill: "bg-blue-wash text-blue",
    fill: "bg-blue",
  },
};

/**
 * A share of the most decisive benefit in the corpus, with no floor: a benefit that never
 * moved a decision renders an empty track. A stub bar under the caption "decisive on 0 of
 * 159 receipts" would be the chart contradicting its own caption.
 */
function barWidth(bp: number, maxBp: number): number {
  return Math.max(0, Math.min(100, (bp / maxBp) * 100));
}

/**
 * The table bar carries one fact beyond its width: whether the benefit cleared the decisive
 * threshold the header states. Nothing else is encoded, because nothing else is measured.
 */
function decisiveFill(bp: number, thresholdBp: number): string {
  return bp >= thresholdBp ? "bg-success" : "bg-gray-03";
}

export function AttributionView() {
  const plumbline = useConsole((s) => s.plumbline);
  const plumblineError = useConsole((s) => s.plumblineError);
  const [showAll, setShowAll] = useState(false);

  if (!plumbline) return <PlumblineUnavailable error={plumblineError} />;

  const book = plumbline.attribution;
  const total = book.benefits.length;
  const maxDecisiveBp = Math.max(...book.benefits.map((b) => b.decisive_bp), 1);
  const ranked = [...book.benefits].sort(
    (a, b) => b.decisive_bp - a.decisive_bp || b.annual_cost_minor - a.annual_cost_minor,
  );
  const rows = showAll ? ranked : ranked.slice(0, TOP_N);
  const totalCostMinor = book.benefits.reduce((sum, b) => sum + b.annual_cost_minor, 0);
  const deadWeightMinor = book.benefits
    .filter((b) => b.quadrant === QUADRANT_DEAD_WEIGHT)
    .reduce((sum, b) => sum + b.annual_cost_minor, 0);

  return (
    <BeatPage>
      <BeatHeader
        beat="06"
        label="Attribution"
        title="Which benefit dollars actually caused a card to be chosen."
      >
        Delete one benefit from its manifest, re-run the allocator, re-apply the stated
        criterion. If the selected instrument changes, that benefit was decisive. Across a
        corpus of {book.corpus_size} receipts, that produces a cut list.
        {/* The handoff puts this strip under the standfirst. The shared header's own `meta`
            slot rides the eyebrow row, which is a different place, so it is not used here. */}
        <span className="num mt-[19px] flex items-center gap-[26px] text-[0.75rem] text-[#6E7A85]">
          <span>
            corpus <Figure>{book.corpus_size}</Figure> receipts
          </span>
          <span>
            decisive threshold <Figure>{(book.thresholds.decisive_bp / 100).toFixed(0)}%</Figure>
          </span>
          <span>
            high cost above <Figure>{book.thresholds.high_cost_display}</Figure>/yr
          </span>
        </span>
      </BeatHeader>

      <BeatSplit
        asideWidth="312px"
        gap="gap-8"
        aside={
          <div className="flex flex-col gap-6">
            <ShowsPanel
              points={[
                "This measures selection influence at the moment of choice — not retention, incremental spend, or renewal.",
                "A benefit that never appears in a winning derivation may still be why the card was taken out. The quadrant is a starting point for a conversation, not a mandate.",
                "Cost is a modelled annual programme figure — an input, not a measurement.",
              ]}
            />
            <div className="border border-gray-03 bg-white px-6 py-5 text-card-title font-normal leading-relaxed text-ink-2">
              Nothing on this screen is ever shown to a Card Member. It goes into an agent's
              ranking, not a member's inbox.
            </div>
          </div>
        }
      >
        <div className="flex flex-col gap-6">
          <div className="grid grid-cols-2 gap-6">
            {GRID.map((quadrant) => (
              <QuadrantCard
                key={quadrant}
                quadrant={quadrant}
                benefits={book.benefits.filter((b) => b.quadrant === quadrant)}
                maxDecisiveBp={maxDecisiveBp}
              />
            ))}
          </div>

          <div className="flex items-center justify-between gap-5 border border-gray-03 bg-gray-01 px-[22px] py-3.5">
            <span className="num text-[0.71875rem] text-ink-4">&larr; rarely decisive</span>
            {/* Lower-cased in the DOM and uppercased by `colhead`: the axis is the claim. */}
            <span className="colhead tracking-[0.16em] text-navy">selection influence</span>
            <span className="num text-[0.71875rem] text-ink-4">often decisive &rarr;</span>
          </div>

          <SquarePanel
            title="Every benefit, measured"
            right={
              total > TOP_N ? (
                <button
                  type="button"
                  onClick={() => setShowAll((v) => !v)}
                  className="rounded-pill border border-gray-03 bg-white px-3.5 py-1.5 text-[0.75rem] font-semibold text-blue transition-colors duration-[180ms] hover:border-blue"
                >
                  {showAll ? `Show top ${TOP_N}` : `Show all ${total}`}
                </button>
              ) : null
            }
          >
            <div className="grid grid-cols-[minmax(0,1fr)_76px_106px_90px] items-center gap-4 border-b border-gray-02 px-[22px] py-[11px]">
              <span className="colhead">Benefit</span>
              <span className="colhead text-right">In a win</span>
              <span className="colhead text-right">Decisive</span>
              <span className="colhead text-right">Cost / yr</span>
            </div>

            {rows.map((benefit) => (
              <div
                key={benefit.benefit_id}
                className="grid grid-cols-[minmax(0,1fr)_76px_106px_90px] items-center gap-4 border-b border-gray-02 px-[22px] py-3"
              >
                {/* The benefit name never truncates. It wraps, and the instrument rides
                    after it as a mono suffix, because clipping either one hides the two
                    facts the row exists to state. */}
                <div className="flex min-w-0 flex-wrap items-baseline gap-x-[9px] gap-y-1">
                  <span className="text-[0.90625rem] text-ink">{benefit.label}</span>
                  <span className="num text-[0.71875rem] whitespace-nowrap text-ink-4">
                    {benefit.product}
                  </span>
                </div>
                <span className="num text-right text-[0.8125rem] text-ink-2">
                  {benefit.in_winning_derivation} / {benefit.in_candidate_set}
                </span>
                <div className="flex items-center justify-end gap-[9px]">
                  <div className="h-[5px] w-[46px] shrink-0 bg-gray-02">
                    <div
                      className={`h-full ${decisiveFill(benefit.decisive_bp, book.thresholds.decisive_bp)}`}
                      style={{ width: `${barWidth(benefit.decisive_bp, maxDecisiveBp)}%` }}
                    />
                  </div>
                  <span className="num w-[42px] text-right text-[0.8125rem] text-navy">
                    {(benefit.decisive_bp / 100).toFixed(1)}%
                  </span>
                </div>
                <span className="num text-right text-[0.8125rem] text-navy">
                  {benefit.annual_cost_display}
                </span>
              </div>
            ))}

            <div className="flex items-baseline justify-between gap-4 border-t border-gray-03 bg-gray-01 px-[22px] py-4">
              <span className="colhead tracking-[0.14em] text-navy">
                Modelled annual cost, all {total} benefit{total === 1 ? "" : "s"}
              </span>
              <span className="num text-[1.1875rem] font-semibold text-navy">
                {money(totalCostMinor)}
              </span>
            </div>

            <div className="border-t border-gray-02 px-[22px] py-3">
              <span className="num text-[0.71875rem] leading-normal text-ink-4">
                decisive = deleting the benefit changes which instrument the criterion selects
              </span>
            </div>
          </SquarePanel>
        </div>
      </BeatSplit>

      <Takeaway>
        {money(deadWeightMinor)} a year sits in the dead-weight quadrant. That is a decision
        you can now defend.
      </Takeaway>
    </BeatPage>
  );
}

// ---------------------------------------------------------------------------------------

function Figure({ children }: { children: ReactNode }) {
  return <span className="font-semibold text-navy">{children}</span>;
}

function QuadrantCard({
  quadrant,
  benefits,
  maxDecisiveBp,
}: {
  quadrant: Quadrant;
  benefits: BenefitAttribution[];
  maxDecisiveBp: number;
}) {
  const copy = QUADRANT_COPY[quadrant];
  const tone = TONE[quadrant];
  // The pill is the verdict's first clause: both header rows are nowrap, and the option
  // quadrant's full verdict cannot ride nowrap in a half-width card. It is restated whole
  // in the closing note below, so the screen never shows a shortened verdict on its own.
  const action = copy.verdict.split(",")[0];
  // Noise gets a compact list: no bar, no per-receipt caption. Spending ink on the quadrant
  // whose verdict is "leave it alone" is the mistake the 2x2 exists to prevent.
  const compact = quadrant === QUADRANT_NOISE;
  const ordered = [...benefits].sort((a, b) => b.annual_cost_minor - a.annual_cost_minor);

  return (
    <section className={`flex flex-col border border-t-[3px] border-gray-03 bg-white ${tone.top}`}>
      <div className="flex flex-col gap-[11px] px-[22px] pt-5 pb-4">
        <div className="flex items-center justify-between gap-3.5">
          <span
            className={`text-[1.3125rem] leading-none font-bold tracking-[-0.012em] whitespace-nowrap ${tone.text}`}
          >
            {copy.title}
          </span>
          <span
            className={`shrink-0 rounded-pill px-3 py-[5px] text-[0.75rem] font-bold whitespace-nowrap ${tone.pill}`}
          >
            {action}
          </span>
        </div>
        <div className="num flex items-baseline justify-between gap-3 text-[0.71875rem] text-ink-4">
          <span className="whitespace-nowrap">
            {copy.cost} &middot; {copy.influence}
          </span>
          <span className="whitespace-nowrap">
            {benefits.length} benefit{benefits.length === 1 ? "" : "s"}
          </span>
        </div>
      </div>

      {compact ? (
        <div className="flex flex-col gap-[11px] border-t border-gray-02 px-[22px] py-4">
          {ordered.map((benefit) => (
            <div key={benefit.benefit_id} className="flex items-baseline justify-between gap-3">
              <span className="text-[0.90625rem] text-ink-2">{benefit.label}</span>
              <span className="num shrink-0 text-[0.875rem] text-ink-2">
                {benefit.annual_cost_display}
              </span>
            </div>
          ))}
        </div>
      ) : (
        ordered.map((benefit) => (
          <div
            key={benefit.benefit_id}
            className="flex flex-col gap-[7px] border-t border-gray-02 px-[22px] py-4"
          >
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-[0.9375rem] text-ink">{benefit.label}</span>
              <span className="num shrink-0 text-[0.9375rem] text-navy">
                {benefit.annual_cost_display}
              </span>
            </div>
            <div className="h-[5px] bg-gray-02">
              <div
                className={`h-full ${tone.fill}`}
                style={{ width: `${barWidth(benefit.decisive_bp, maxDecisiveBp)}%` }}
              />
            </div>
            <span className="num text-[0.71875rem] text-ink-4">
              decisive on {benefit.decisive} of {benefit.in_candidate_set} receipts
            </span>
          </div>
        ))
      )}

      {quadrant === QUADRANT_OPTION && (
        <div className="mt-auto border-t border-gray-02 px-[22px] py-4">
          <p className="text-[0.875rem] leading-relaxed text-[#6E7A85]">
            Low cost and often decisive: {copy.verdict}.
          </p>
        </div>
      )}
    </section>
  );
}
