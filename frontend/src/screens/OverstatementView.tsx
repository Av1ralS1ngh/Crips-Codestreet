/**
 * Beat 01 — the intuitive answer overstates.
 *
 * Three bars carry the argument: what per-line summation claims, what an allocation can
 * actually realise, and the difference between them. The table underneath is the receipt
 * for that difference — every benefit, what it claimed, what it was allocated.
 *
 * Every figure on this screen is re-grouped from the reconciliation the evaluator emitted.
 * Nothing here recomputes a value.
 */

import {
  BarRow,
  BeatHeader,
  BeatPage,
  BeatSplit,
  PlumblineUnavailable,
  ShowsPanel,
  SquarePanel,
  Takeaway,
} from "../components/plumblineUi";
import { money } from "../lib/format";
import { activeInstrument, useConsole } from "../lib/store";
import type { Collision, Reconciliation } from "../lib/plumbline";

/** The deny bar is a wash, not the deny red: it is a quantity, not a refusal. */
const OVERSTATEMENT_FILL = "bg-[#E8B4AF]";

export function OverstatementView() {
  const plumbline = useConsole((s) => s.plumbline);
  const plumblineError = useConsole((s) => s.plumblineError);
  const instrumentId = useConsole((s) => s.instrumentId);

  const instrument = activeInstrument(plumbline, instrumentId);

  if (!plumbline || !instrument) return <PlumblineUnavailable error={plumblineError} />;

  const rec = instrument.reconciliation;
  const basketMinor = plumbline.cart.lines.reduce((sum, line) => sum + line.amount, 0);
  const share = (minor: number) => (rec.naive_minor > 0 ? (minor / rec.naive_minor) * 100 : 0);
  // Naive over provable. Guarded because a card that realises nothing has no ratio, and a
  // divide-by-zero rendered as "Infinity×" would be the one wrong number that refutes the
  // whole screen.
  const factor = rec.witness_minor > 0 ? rec.naive_minor / rec.witness_minor : null;
  const claims = claimRows(rec);

  return (
    <BeatPage>
      <BeatHeader beat="01" label="Overstatement" title="The intuitive answer overstates.">
        Add up every benefit that touches a line of this basket and you get {rec.naive_display}.
        Two credits cannot both be spent on the same line, so the honest number is the best
        allocation that actually exists.
      </BeatHeader>

      <BeatSplit
        aside={
          <ShowsPanel
            points={[
              exclusivityPoint(instrument.collisions),
              <>
                The allocator picks the assignment that maximises realised value, then exhibits
                it. That exhibit is the witness.
              </>,
              <>
                Re-adding it is linear time and needs no solver, so anyone can check the number
                without trusting us.
              </>,
            ]}
          />
        }
      >
        {/* ------------------------------------------------------------------ the two numbers */}
        <div className="flex flex-col gap-4">
          <span className="eyebrow">Value on a {money(basketMinor)} basket</span>

          <div className="flex flex-col gap-[22px] pt-1.5">
            <BarRow label="Per-line summation, naive" value={rec.naive_display} pct={100} />
            <BarRow
              label="Witness-backed allocation"
              value={rec.witness_display}
              pct={share(rec.witness_minor)}
              fill="bg-blue"
              valueClass="text-blue"
            />
            <BarRow
              label="Overstated by"
              value={rec.overstatement_display}
              pct={share(rec.overstatement_minor)}
              fill={OVERSTATEMENT_FILL}
              valueClass="text-warning"
              sub={
                factor === null
                  ? "no allocation realises anything on this basket"
                  : `${factor.toFixed(2)}× the provable figure`
              }
            />
          </div>
        </div>

        {/* -------------------------------------------------------------------- the derivation */}
        <SquarePanel title="Two credits, one line">
          <div className="grid grid-cols-[minmax(0,2fr)_1fr_1fr] items-baseline gap-4 border-b border-gray-02 px-[22px] py-4">
            <span className="colhead">Benefit</span>
            <span className="colhead text-right">Claimed</span>
            <span className="colhead text-right">Allocated</span>
          </div>

          {claims.map((row) => (
            <div
              key={row.benefit_id}
              className="grid grid-cols-[minmax(0,2fr)_1fr_1fr] items-baseline gap-4 border-b border-gray-02 px-[22px] py-4"
            >
              <span className="min-w-0 break-words text-data text-ink">{row.label}</span>
              <span className="num text-right text-data font-normal text-ink-2">
                {money(row.claimed_minor)}
              </span>
              <span
                className={`num text-right text-data ${
                  row.allocated_minor > 0 ? "font-semibold text-blue" : "text-ink-4"
                }`}
              >
                {row.allocated_minor > 0 ? money(row.allocated_minor) : "—"}
              </span>
            </div>
          ))}

          <div className="grid grid-cols-[minmax(0,2fr)_1fr_1fr] items-baseline gap-4 border-t border-gray-03 bg-gray-01 px-[22px] py-[17px]">
            <span className="text-card-title text-navy">Witness total, the asserted value</span>
            <span className="num text-right text-data font-normal text-ink-4">
              {rec.naive_display}
            </span>
            <span className="num text-right text-[1.0625rem] font-semibold text-blue">
              {instrument.asserted_display}
            </span>
          </div>
        </SquarePanel>
      </BeatSplit>

      <Takeaway>A sum of benefits is not a value. An allocation is.</Takeaway>
    </BeatPage>
  );
}

// ---------------------------------------------------------------------------------------

/**
 * The exclusivity statement names the collision that accounts for the most struck value on
 * this basket, so the sentence points at the bar rather than at an incidental pairing. With
 * no collision on the active instrument there is nothing to name, and the general rule is
 * stated instead — never an invented pairing.
 */
function exclusivityPoint(collisions: Collision[]) {
  const worst = [...collisions].sort((a, b) => struckMinor(b) - struckMinor(a))[0];
  const struck = worst?.struck[0];
  if (!worst || !struck) {
    return (
      <>
        Exclusivity is the whole problem. Two benefits in one group can both match a line, and
        only one of them can be spent there.
      </>
    );
  }
  return (
    <>
      Exclusivity is the whole problem. The {worst.winner.label} and the {struck.label} both
      match the same {money(worst.line_amount)} of {worst.line_description}; only one can be
      spent there.
    </>
  );
}

function struckMinor(collision: Collision): number {
  return collision.struck.reduce((sum, entry) => sum + entry.value_minor, 0);
}

interface ClaimRow {
  benefit_id: string;
  label: string;
  claimed_minor: number;
  allocated_minor: number;
}

/**
 * The reconciliation is keyed by (line, benefit); this table is keyed by benefit, because
 * the claim a reader is checking is per benefit: this credit was advertised at X and
 * delivered Y. Summing across lines is what makes the two columns reconcile to the two
 * figures in the bars above — nothing here recomputes a value, it only re-groups one.
 */
function claimRows(rec: Reconciliation): ClaimRow[] {
  const byBenefit = new Map<string, ClaimRow>();

  for (const row of rec.rows) {
    let entry = byBenefit.get(row.benefit_id);
    if (!entry) {
      entry = {
        benefit_id: row.benefit_id,
        label: row.benefit_label,
        claimed_minor: 0,
        allocated_minor: 0,
      };
      byBenefit.set(row.benefit_id, entry);
    }
    entry.claimed_minor += row.naive_minor;
    entry.allocated_minor += row.realized_minor;
  }

  return [...byBenefit.values()].sort((a, b) => b.claimed_minor - a.claimed_minor);
}
