/**
 * Beat 01 — the intuitive answer overstates.
 *
 * Three bars carry the argument: what per-line summation claims, what an allocation can
 * actually realise, and the difference between them. The table underneath is the receipt
 * for that difference — every benefit, what it claimed, what it was allocated.
 *
 * Two things this screen refuses to do:
 *
 *   * It does not argue with a strawman. The naive bar carries the strongest figure a
 *     per-line implementation can reach — one that reads each balance and clamps to it —
 *     and the reconciliation strip names which rule a per-line clamp can see and which it
 *     cannot.
 *   * It does not assert that the witness is valid. It re-checks it in the browser, in
 *     linear time, with no solver, and prints its own verdict beside the evaluator's.
 */

import {
  BarRow,
  BeatHeader,
  BeatPage,
  BeatSplit,
  HashRow,
  PlumblineUnavailable,
  ShowsPanel,
  SquarePanel,
  Takeaway,
  VerifyPair,
} from "../components/plumblineUi";
import { money } from "../lib/format";
import { activeInstrument, manifestFor, useConsole } from "../lib/store";
import { useLocalVerification } from "../lib/useWitness";
import { CAUSE_COPY, type Reconciliation } from "../lib/plumbline";

/** The deny bar is a wash, not the deny red: it is a quantity, not a refusal. */
const OVERSTATEMENT_FILL = "bg-[#E8B4AF]";

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
      <BeatHeader
        beat="01"
        label="Overstatement"
        title="The intuitive answer overstates."
        meta={`${instrument.allocator_stats.considered} pairings considered · ${instrument.allocator_stats.assigned} assigned`}
      >
        {plumbline.valuation.narrative}
      </BeatHeader>

      <BeatSplit
        aside={
          <>
            <SquarePanel
              title="Instrument"
              right={
                <span className="num text-pill font-normal text-ink-4">
                  {plumbline.valuation.instruments.length} valued
                </span>
              }
            >
              {plumbline.valuation.instruments.map((candidate) => {
                const active = candidate.instrument_id === instrument.instrument_id;
                return (
                  <button
                    key={candidate.instrument_id}
                    type="button"
                    onClick={() => setInstrument(candidate.instrument_id)}
                    className={`flex flex-col items-start gap-1 border-b border-gray-02 px-[22px] py-3.5 text-left transition-colors duration-[180ms] last:border-b-0 ${
                      active
                        ? "border-l-[3px] border-l-blue bg-blue-row pl-[19px]"
                        : "bg-white hover:bg-gray-01"
                    }`}
                  >
                    <span
                      className={`break-words text-data ${active ? "font-bold text-blue" : "text-ink"}`}
                    >
                      {candidate.product}
                    </span>
                    <span className="num text-pill font-normal text-ink-4">
                      {candidate.issuer} · {candidate.asserted_display}
                    </span>
                  </button>
                );
              })}
            </SquarePanel>

            <ShowsPanel
              points={[
                <>
                  Exclusivity is the whole problem. Two benefits in one group can both match a
                  line, and only one of them can be spent there.
                </>,
                <>
                  The allocator picks the assignment that maximises realised value, then exhibits
                  it. That exhibit is the witness, and it is the table on the left.
                </>,
                <>
                  Re-adding it is linear time and needs no solver, so anyone can check the number
                  without trusting us. This browser just did.
                </>,
              ]}
              footnote={
                <>
                  The allocator is conservative by construction but not optimal; the gap is
                  measured offline, never in the checkout path. Entailment is a different
                  component with a different budget.
                </>
              }
            />
          </>
        }
      >
        {/* ------------------------------------------------------------------ the two numbers */}
        <div className="flex flex-col gap-4">
          <span className="eyebrow">
            Value on a {money(basketMinor)} basket · {plumbline.cart.lines.length} lines ·{" "}
            {plumbline.cart.merchant}
          </span>

          <div className="flex flex-col gap-[22px] pt-1.5">
            {/* The steelman rides on the naive bar rather than getting a bar of its own: the
                strongest figure a per-line implementation can reach is one that reads each
                balance and clamps to it, and on some instruments that is the same number. */}
            <BarRow
              label="Per-line summation, naive"
              value={rec.naive_display}
              pct={100}
              sub={
                rec.capped_minor < rec.naive_minor
                  ? `capped to remaining balances · ${rec.capped_display}`
                  : `no pairing exceeds its own balance · ${rec.capped_display}`
              }
            />
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

          {/* Where the difference went, and which half of it a per-line clamp could ever see.
              The steelman lives here: the first cause is visible per line, the rest are not. */}
          <div className="mt-2 grid grid-cols-3 gap-px border border-gray-03 bg-gray-03">
            {rec.by_cause.map((cause) => (
              <div key={cause.cause} className="flex flex-col gap-2 bg-white px-[18px] py-4">
                <span className="colhead break-words">
                  {CAUSE_COPY[cause.cause]?.label ?? cause.cause}
                </span>
                <span className="num text-[1.0625rem] font-semibold text-navy">
                  {cause.display}
                </span>
                <span className="text-code text-ink-4">
                  {cause.visible_per_line
                    ? "a per-line clamp sees this one"
                    : "invisible to a per-line clamp"}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* -------------------------------------------------------------------- the derivation */}
        <SquarePanel
          title="Two credits, one line"
          right={
            <span className="num text-pill font-normal text-ink-4">
              {instrument.collisions.length} exclusivity collision
              {instrument.collisions.length === 1 ? "" : "s"} on this basket
            </span>
          }
        >
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
              <div className="flex min-w-0 flex-col gap-1.5">
                <span className="break-words text-data text-ink">{row.label}</span>
                {row.notes.map((note) => (
                  <span key={note} className="num break-words text-pill font-normal text-warning">
                    {note}
                  </span>
                ))}
              </div>
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
            <span
              className="num text-right text-[1.0625rem] font-semibold text-blue"
              title={`${instrument.asserted_minor} minor units`}
            >
              {instrument.asserted_display}
            </span>
          </div>
        </SquarePanel>

        {/* ------------------------------------------------------------------ checked, not claimed */}
        <div className="flex flex-col gap-4">
          <span className="eyebrow">Checked twice, on two machines</span>
          <VerifyPair server={instrument.verification} local={local} />
          <div className="border border-gray-03 bg-gray-01 px-6 py-[18px]">
            <HashRow label="witness sha256" value={instrument.witness_hash} />
          </div>
        </div>
      </BeatSplit>

      <Takeaway>A sum of benefits is not a value. An allocation is.</Takeaway>
    </BeatPage>
  );
}

// ---------------------------------------------------------------------------------------

interface ClaimRow {
  benefit_id: string;
  label: string;
  claimed_minor: number;
  allocated_minor: number;
  notes: string[];
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
        notes: [],
      };
      byBenefit.set(row.benefit_id, entry);
    }
    entry.claimed_minor += row.naive_minor;
    entry.allocated_minor += row.realized_minor;

    if (row.shortfall_minor <= 0) continue;
    const cause = CAUSE_COPY[row.cause]?.label ?? row.cause;
    const note = row.exclusivity_group ? `${cause} · ${row.exclusivity_group}` : cause;
    if (!entry.notes.includes(note)) entry.notes.push(note);
  }

  return [...byBenefit.values()].sort((a, b) => b.claimed_minor - a.claimed_minor);
}
