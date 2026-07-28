/**
 * Beat 7 — hand the judge the controls.
 *
 * Perturb a manifest; watch the ranking and the witness move. Everything else in this
 * console is a rehearsed sequence a presenter drives. This one is a control a skeptic
 * drives, and the only thing that makes it worth anything is that the console does not
 * take the engine's word for the result:
 *
 *   1. The judge moves one field of one benefit — a remaining balance, an annual-cap
 *      headroom, or a rate.
 *   2. The console applies that same one-field change to the SIGNED manifest body itself
 *      (`lib/perturb.ts`), producing the manifest it is about to check against.
 *   3. The witness the engine produced at that point is re-verified in the browser against
 *      that manifest, in linear time, with no solver (`lib/witness.ts`).
 *   4. The derivation table is rebuilt from the witness (`lib/derivation.ts`) rather than
 *      shipped, so what is on screen is what a counterparty could reproduce.
 *
 * If the engine and the console ever disagreed about what a perturbation means, step 3
 * fails and the screen says REJECTED with a code. That is the honest failure and it is
 * left switched on.
 *
 * The allocator is still not ported. Producing an allocation is a decision and the
 * decision belongs to the engine, so every point on every axis is a recorded run of
 * `plumbline.allocate` — the axis is precomputed, the verification is live, and the screen
 * says which is which rather than implying the browser did the optimising.
 *
 * What may be moved, and what may not: a balance or a headroom is member state — two Card
 * Members hold the same product with different balances on the same day, so moving one
 * asserts nothing about the product's terms, and on a real product the axis stops at the
 * cap that product publishes. A rate is a product term, so the only rate on this screen
 * belongs to the one instrument in the candidate set that is invented outright and signed
 * by nobody. Putting a rate that is not its own beside a real product's name is a claim
 * about that product, and we do not make it — not about the issuer's card, and not about
 * the competitor's.
 *
 * The layer above is the Claude Design handoff's beat scaffold: chip, title, standfirst,
 * content, "What this shows", takeaway. Main scrolls, so nothing here is viewport-locked
 * and the derivation renders at its natural height.
 */

import { useMemo, useState } from "react";
import { Button, Caveat, Seal } from "../components/ui";
import {
  BarRow,
  BeatHeader,
  BeatPage,
  BeatSplit,
  HashRow,
  KindChip,
  ShowsPanel,
  SquarePanel,
  Takeaway,
  VerifyBox,
} from "../components/plumblineUi";
import { derivationRows } from "../lib/derivation";
import { ruleFor } from "../lib/derive";
import { money, ms as fmtMs, shortHash, signedMoney } from "../lib/format";
import { perturbManifest } from "../lib/perturb";
import { verifyWitness } from "../lib/witness";
import { PERTURBATION as BOOK } from "../data/perturbations";
import {
  FACT_MEMBER_STATE,
  FIELD_RATE_BP,
  type PerturbationAxis,
  type PerturbationInstrument,
  type PerturbationPoint,
  type RankEntry,
} from "../lib/plumbline";

/**
 * The handoff's slider: a 3px track and a 20px blue thumb inside a 3px white ring.
 *
 * The ring needs its drop shadow to separate from the track, and it is one of the three
 * shadows the design foundation grants: `index.css` exempts `[type="range"]` from the
 * global cancel, and a universal selector never reaches a `::-webkit-slider-thumb`, so
 * both halves land without a stylesheet edit.
 */
const SLIDER =
  "w-full cursor-pointer appearance-none bg-transparent " +
  "[&::-webkit-slider-runnable-track]:h-[3px] [&::-webkit-slider-runnable-track]:bg-track " +
  "[&::-webkit-slider-thumb]:mt-[-9px] [&::-webkit-slider-thumb]:h-5 [&::-webkit-slider-thumb]:w-5 " +
  "[&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-pill " +
  "[&::-webkit-slider-thumb]:border-[3px] [&::-webkit-slider-thumb]:border-white " +
  "[&::-webkit-slider-thumb]:bg-blue [&::-webkit-slider-thumb]:cursor-grab " +
  "[&::-webkit-slider-thumb]:shadow-[0_1px_5px_rgba(0,23,90,0.35)] " +
  "[&::-moz-range-track]:h-[3px] [&::-moz-range-track]:bg-track " +
  "[&::-moz-range-thumb]:h-[14px] [&::-moz-range-thumb]:w-[14px] " +
  "[&::-moz-range-thumb]:rounded-pill [&::-moz-range-thumb]:border-[3px] " +
  "[&::-moz-range-thumb]:border-white [&::-moz-range-thumb]:bg-blue " +
  "[&::-moz-range-thumb]:cursor-grab " +
  "[&::-moz-range-thumb]:shadow-[0_1px_5px_rgba(0,23,90,0.35)]";

/** The one live width on the screen. Everything else moves on the 180ms colour ramp. */
const BAR_MOTION = "transition-[width] duration-[120ms] ease-linear";

const STANDFIRST =
  "The argument stops being ours here. Move one field of one benefit and see exactly " +
  "what it takes to move the ranking, the witness and the derivation under it.";

export function ControlsView() {
  const [axisId, setAxisId] = useState(BOOK.axes[0].axis_id);
  const [index, setIndex] = useState(BOOK.axes[0].baseline_index);

  const axis = BOOK.axes.find((a) => a.axis_id === axisId) ?? BOOK.axes[0];
  const point = axis.points[Math.min(index, axis.points.length - 1)];
  const baseline = axis.points[axis.baseline_index];
  const instrument = BOOK.instruments.find((i) => i.instrument_id === axis.instrument_id);
  const atBaseline = point.value === axis.baseline_value;

  // The manifest this console will check against: the signed body with exactly one field
  // moved, built here rather than accepted from the corpus.
  const manifest = useMemo(
    () =>
      instrument
        ? perturbManifest(instrument.manifest, axis.benefit_id, axis.field, point.value)
        : null,
    [instrument, axis.benefit_id, axis.field, point.value],
  );

  const local = useMemo(
    () =>
      manifest
        ? verifyWitness({
            witness: point.witness,
            manifest,
            cart: BOOK.cart,
            cartHash: BOOK.cart_hash,
            assertedMinor: point.asserted_minor,
          })
        : null,
    [manifest, point],
  );

  const rows = useMemo(
    () => (manifest ? derivationRows(manifest, BOOK.cart, point.witness) : []),
    [manifest, point],
  );

  const selectAxis = (next: PerturbationAxis) => {
    setAxisId(next.axis_id);
    setIndex(next.baseline_index);
  };

  // An axis naming an instrument the corpus does not carry cannot be checked against
  // anything, and a witness checked against the wrong manifest is worse than no check. Say
  // which axis and stop rather than rendering a number nobody verified.
  if (!instrument || !manifest || !local) {
    return (
      <BeatPage>
        <BeatHeader
          beat="07"
          label="Controls"
          title="Move one number. Watch the derivation move."
        >
          {STANDFIRST}
        </BeatHeader>
        <SquarePanel title="Perturbation corpus" tone="deny">
          <div className="flex max-w-[70ch] flex-col gap-3 px-6 py-7">
            <span className="num text-[1.1875rem] font-semibold text-warning">
              {axis.axis_id}
            </span>
            <p className="text-body text-ink">
              names instrument <span className="num">{axis.instrument_id}</span>, which this
              corpus does not carry, so there is no signed body to perturb and nothing to
              verify the witness against.
            </p>
            <p className="text-[0.90625rem] leading-[1.55] text-ink-3">
              Regenerate with <span className="num">scripts/gen_plumbline_beats.py</span>; it
              refuses to write a corpus whose axes and instruments disagree.
            </p>
          </div>
        </SquarePanel>
      </BeatPage>
    );
  }

  return (
    <BeatPage>
      <BeatHeader
        beat="07"
        label="Controls"
        title="Move one number. Watch the derivation move."
        meta={`${axis.points.length} engine-evaluated stops · verification live`}
      >
        {STANDFIRST}
      </BeatHeader>

      {/*
        One line per axis, not two. The product and the fact class used to ride under every
        label as a second uppercase line, which turned five choices into two full rows of
        chrome above the control they select. Both facts are already on the panels below:
        the instrument names itself in the ranking, and the fact class is the knob's hint.
      */}
      <nav className="flex flex-wrap items-center gap-2">
        {BOOK.axes.map((option) => (
          <button
            key={option.axis_id}
            type="button"
            onClick={() => selectAxis(option)}
            title={`${option.product} · ${option.fact}`}
            className={`rounded-pill border px-[18px] py-[9px] text-[0.90625rem] font-bold transition-colors duration-[180ms] ${
              option.axis_id === axis.axis_id
                ? "border-blue bg-blue text-white"
                : "border-gray-03 bg-white text-ink-2 hover:border-blue hover:text-blue"
            }`}
          >
            {option.label}
          </button>
        ))}
        <span className="ml-auto shrink-0">
          <Button size="sm" onClick={() => setIndex(axis.baseline_index)} disabled={atBaseline}>
            reset to the signed terms
          </Button>
        </span>
      </nav>

      <BeatSplit
        aside={
          <ShowsPanel
            points={[
              <>
                Every stop on this axis is a recorded run of the engine's allocator. This
                browser optimises nothing. It re-adds the numbers and checks the balances.
              </>,
              <>
                A balance is member state: two Card Members hold the same product with
                different balances on the same day, so moving one asserts nothing about the
                product's terms. A rate is a product term, which is why the one rate on this
                screen sits on the instrument invented outright and signed by nobody.
              </>,
              <>
                The re-check runs on every move and is left switched on. If the engine and
                this console ever disagreed about what a perturbation means, the verdict
                below reads <strong className="font-bold">REJECTED</strong> with a code.
              </>,
            ]}
            footnote={
              <>
                <span className="colhead block pb-2">What to watch on this axis</span>
                {axis.watch_for}
              </>
            }
          />
        }
      >
        <SquarePanel
          title="The knob"
          right={<Seal signed={atBaseline && instrument.issuer_signed} />}
        >
          <div className="flex flex-col gap-[18px] px-6 py-7">
            <div className="flex items-end justify-between gap-5">
              <div className="flex min-w-0 flex-col gap-2.5">
                <span className="text-[0.96875rem] leading-snug break-words text-ink">
                  {axis.benefit_label}
                </span>
                <div className="flex flex-wrap items-center gap-2">
                  <KindChip kind={axis.benefit_kind} />
                  <span className="num text-code text-ink-4">
                    {axis.field} · {axis.benefit_window} · {axis.fact}
                  </span>
                </div>
              </div>
              <span
                className={`num shrink-0 text-[1.625rem] leading-none font-semibold ${
                  atBaseline ? "text-navy" : "text-blue"
                }`}
              >
                {point.value_display}
              </span>
            </div>

            <input
              type="range"
              min={0}
              max={axis.points.length - 1}
              step={1}
              value={index}
              onChange={(event) => setIndex(Number(event.target.value))}
              aria-label={axis.label}
              className={SLIDER}
            />

            <div className="flex items-baseline justify-between gap-5">
              <span className="num text-code text-ink-4">{axis.points[0].value_display}</span>
              <span className="num text-code text-ink-4">
                {axis.points.length} engine-evaluated stops
              </span>
              <span className="num text-code text-ink-4">
                {axis.points[axis.points.length - 1].value_display}
              </span>
            </div>

            <div className="flex items-baseline justify-between gap-5 border-t border-gray-03 pt-[14px]">
              <span className="text-[0.90625rem] text-ink-3">
                signed value <span className="num text-ink-2">{baseline.value_display}</span>
              </span>
              <span
                className={`num text-[0.90625rem] font-semibold ${
                  atBaseline ? "text-ink-4" : "text-blue"
                }`}
              >
                {atBaseline
                  ? "unchanged"
                  : axis.field === FIELD_RATE_BP
                    ? `${point.value - axis.baseline_value >= 0 ? "+" : ""}${
                        point.value - axis.baseline_value
                      } bp`
                    : signedMoney(point.value - axis.baseline_value)}
              </span>
            </div>

            <p className="border-l-[3px] border-navy bg-gray-01 px-[18px] py-3.5 text-[0.90625rem] leading-[1.55] text-ink-2">
              {axis.what_it_is}
            </p>
          </div>
        </SquarePanel>

        <SignaturePanel
          instrument={instrument}
          axis={axis}
          point={point}
          atBaseline={atBaseline}
        />

        <RankingPanel
          axis={axis}
          point={point}
          baseline={baseline}
          supported={local.supportsAssertion}
        />

        <SquarePanel
          title="The verdict at this setting"
          right={<span className="colhead">recomputed on every move</span>}
        >
          <div className="grid grid-cols-2 gap-px bg-gray-03">
            <div className="flex flex-col gap-2.5 bg-white px-6 py-7">
              <VerifyBox
                label="Re-checked here, against the manifest this console perturbed"
                ok={local.supportsAssertion}
                note={
                  local.supportsAssertion
                    ? `${local.assignments} assignments realise ${money(local.realizedMinor)} in ${fmtMs(local.elapsedMs, 3)} · no solver`
                    : (local.failures[0]?.code ?? "does not support the assertion")
                }
              />
              {local.failures.map((failure, i) => (
                <p
                  key={`${failure.code}-${i}`}
                  className="num text-code leading-[1.45] break-words text-warning"
                >
                  {failure.detail}
                </p>
              ))}
            </div>
            <GapCell axis={axis} point={point} />
          </div>
        </SquarePanel>

        <SquarePanel
          title="The derivation at this setting"
          right={
            <span className="colhead">
              {point.allocator_stats.assigned} of {point.allocator_stats.considered} pairings
              assigned
            </span>
          }
        >
          <DerivationTable rows={rows} point={point} axis={axis} />
        </SquarePanel>
      </BeatSplit>

      <Takeaway>
        The answer is not that the number is wrong; it is the assumption the number needs.
      </Takeaway>
    </BeatPage>
  );
}

// ---------------------------------------------------------------------------------------

/**
 * The gap the handoff's verdict box carries, computed from the corpus instead of a closed
 * form. Realised-minus-asserted is zero at every stop by construction — the engine asserts
 * exactly what its own witness realises — so the figure that actually moves is the distance
 * to the best rival instrument under the cardholder's criterion, and it crosses zero at the
 * stop where the subject takes the top of the ranking.
 *
 * Every sentence below is keyed to the axis's fact class. A balance moving is an assumption
 * about one account; a rate moving is a product term, and the only one here belongs to an
 * instrument nobody signed. Neither sentence may read as a published term having changed.
 */
function GapCell({ axis, point }: { axis: PerturbationAxis; point: PerturbationPoint }) {
  const subject = point.ranking.find((r) => r.instrument_id === axis.instrument_id) ?? null;
  // The ranking arrives sorted, so the first entry that is not the subject is the best rival.
  const rival = point.ranking.find((r) => r.instrument_id !== axis.instrument_id) ?? null;
  const rivalName =
    (rival && BOOK.instruments.find((i) => i.instrument_id === rival.instrument_id)?.product) ??
    rival?.instrument_id ??
    "the rest of the candidate set";
  const crossover =
    axis.points.find((p) => p.ranking[0]?.instrument_id === axis.instrument_id) ?? null;

  const gap = subject && rival ? subject.asserted_minor - rival.asserted_minor : null;

  const assumption =
    axis.fact === FACT_MEMBER_STATE
      ? "a balance assumed for one account, not a term any issuer publishes"
      : "a rate no published source supports, on the one instrument here invented outright and signed by nobody";

  let sentence: string;
  if (gap === null) {
    sentence = "This axis names an instrument the ranking does not carry.";
  } else if (gap > 0) {
    sentence =
      `It leads ${rivalName} by ${money(gap)} at this setting. ` +
      `That takes ${point.value_display}: ${assumption}.`;
  } else if (gap === 0) {
    sentence = `It draws level with ${rivalName} at this setting.`;
  } else {
    sentence =
      `The best allocation this manifest admits falls ${money(-gap)} short of ${rivalName}. ` +
      (crossover
        ? `It leads only at ${crossover.value_display}: ${assumption}.`
        : "No stop on this axis puts it in front.");
  }

  return (
    <div className="flex flex-col gap-2.5 bg-white px-6 py-7">
      <span className="colhead">The gap at this setting</span>
      <span className="num text-[1.1875rem] font-semibold text-navy">
        {gap === null ? "—" : signedMoney(gap)}
      </span>
      <p className="text-[0.90625rem] leading-[1.55] text-ink-2">{sentence}</p>
      <span className="num text-code text-ink-4">
        against {rivalName} · not issuer-endorsed
      </span>
    </div>
  );
}

function SignaturePanel({
  instrument,
  axis,
  point,
  atBaseline,
}: {
  instrument: PerturbationInstrument;
  axis: PerturbationAxis;
  point: PerturbationPoint;
  atBaseline: boolean;
}) {
  return (
    <SquarePanel
      title="What the perturbation costs"
      tone={atBaseline ? "plain" : "deny"}
      right={
        <span className={atBaseline ? "colhead" : "strip"}>
          {atBaseline ? "nothing yet" : "signature does not cover this body"}
        </span>
      }
    >
      <div className="flex flex-col gap-3 px-6 py-7">
        <HashRow
          label="signed body"
          value={axis.signed_manifest_hash}
          tone={atBaseline ? "proof" : "muted"}
        />
        <HashRow
          label="body on screen"
          value={point.manifest_hash}
          tone={atBaseline ? "proof" : "muted"}
        />
        {/*
          One sentence, because the two hashes above already made the point: they either
          match or they do not. The long version of this argument — that a balance is member
          state and moving it asserts nothing about a published product term — is the axis
          caption, and repeating it here was the densest text on the screen.
        */}
        <p className="text-[0.90625rem] leading-[1.55] text-ink-2">
          {atBaseline
            ? "Identical. This is the body the other screens value, byte for byte."
            : instrument.issuer_signed
              ? "Different bodies. The issuer signature does not cover what is on screen."
              : "Different bodies. This instrument carried no issuer signature to begin with."}
        </p>
        {/*
          Compressed, not cut: quoting a real product with a rate that is not its own would
          be a claim about that product, so the one rate axis on this screen belongs to the
          invented instrument, and the screen must keep saying so.
        */}
        {axis.fact !== FACT_MEMBER_STATE && (
          <Caveat>
            A rate is a product term; the only one here belongs to the instrument invented
            outright and signed by nobody.
          </Caveat>
        )}
      </div>
    </SquarePanel>
  );
}

function RankingPanel({
  axis,
  point,
  baseline,
  supported,
}: {
  axis: PerturbationAxis;
  point: PerturbationPoint;
  baseline: PerturbationPoint;
  supported: boolean;
}) {
  const baselineRank = new Map(baseline.ranking.map((r) => [r.instrument_id, r]));
  const top = point.ranking[0].asserted_minor;
  const moved = point.ranking.some(
    (r) => baselineRank.get(r.instrument_id)?.rank !== r.rank,
  );

  return (
    <SquarePanel
      title="The ranking, under the cardholder's criterion"
      right={<span className="colhead">not issuer-endorsed</span>}
    >
      <div className="flex flex-col gap-[26px] px-6 py-7">
        <div className="flex flex-col gap-[22px]">
          {point.ranking.map((entry) => (
            <RankingBar
              key={entry.instrument_id}
              entry={entry}
              was={baselineRank.get(entry.instrument_id) ?? null}
              top={top}
              isSubject={entry.instrument_id === axis.instrument_id}
              supported={supported}
            />
          ))}
        </div>

        <div className="flex flex-wrap items-baseline gap-x-[14px] gap-y-2 border-l-[3px] border-navy bg-gray-01 px-[18px] py-3.5">
          <span className="colhead">Asserted</span>
          <span className="num text-[1.0625rem] font-semibold text-navy">
            {point.asserted_display}
          </span>
          <span className="ml-auto text-[0.90625rem] text-ink-3">
            the value this witness realises at this setting · the order{" "}
            {moved ? "moved" : "held"}
          </span>
        </div>

        {/*
          The header already carries "not issuer-endorsed", which is the load-bearing half.
          What remains worth saying is whose criterion produced this order, in one line.
        */}
        <Caveat>The cardholder's criterion, not the issuer's: {BOOK.criterion}.</Caveat>
      </div>
    </SquarePanel>
  );
}

function RankingBar({
  entry,
  was,
  top,
  isSubject,
  supported,
}: {
  entry: RankEntry;
  was: RankEntry | null;
  top: number;
  isSubject: boolean;
  supported: boolean;
}) {
  // Falls back to the id rather than asserting the join. A ranked instrument the corpus
  // does not describe is a corpus bug, and a row reading its raw id is how a reader finds
  // out; a crash on stage is not.
  const record = BOOK.instruments.find((i) => i.instrument_id === entry.instrument_id);
  const delta = entry.asserted_minor - (was?.asserted_minor ?? entry.asserted_minor);
  const rankDelta = (was?.rank ?? entry.rank) - entry.rank;

  // Green is not "this one won". It is "this browser re-checked the witness behind this
  // number and it held" — so the subject's bar is the only one that carries the verdict,
  // and it drops to blue the moment the local check stops agreeing with the engine.
  const fill = isSubject
    ? supported
      ? "bg-success"
      : "bg-blue"
    : entry.rank === 1
      ? "bg-navy"
      : "bg-sky";

  return (
    <BarRow
      label={
        <span className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <span className={`num ${entry.rank === 1 ? "text-success" : "text-ink-4"}`}>
            #{entry.rank}
          </span>
          <span className="break-words">{record?.product ?? entry.instrument_id}</span>
          {record && (
            <span className="text-[0.90625rem] font-normal text-ink-4">{record.issuer}</span>
          )}
          {rankDelta !== 0 && (
            <span className={`num ${rankDelta > 0 ? "text-success" : "text-warning"}`}>
              {rankDelta > 0 ? `▲${rankDelta}` : `▼${-rankDelta}`}
            </span>
          )}
        </span>
      }
      value={entry.asserted_display}
      valueClass={isSubject ? "text-blue" : "text-navy"}
      pct={top <= 0 ? 0 : (entry.asserted_minor / top) * 100}
      fill={`${fill} ${BAR_MOTION}`}
      sub={
        isSubject && delta !== 0 ? (
          <span className="text-blue">{signedMoney(delta)} against the signed terms</span>
        ) : undefined
      }
    />
  );
}

function DerivationTable({
  rows,
  point,
  axis,
}: {
  rows: ReturnType<typeof derivationRows>;
  point: PerturbationPoint;
  axis: PerturbationAxis;
}) {
  return (
    <>
      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className="colhead border-b border-gray-03 px-[22px] py-3 text-left">Line</th>
            <th className="colhead border-b border-gray-03 px-3 py-3 text-left">Benefit</th>
            <th className="colhead border-b border-gray-03 px-3 py-3 text-left">
              Rule from the manifest
            </th>
            <th className="colhead border-b border-gray-03 px-3 py-3 text-right">
              Balance before → after
            </th>
            <th className="colhead border-b border-gray-03 px-[22px] py-3 text-right">
              Value
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const touched = row.benefit_id === axis.benefit_id;
            return (
              <tr
                key={`${row.line_sku}-${row.benefit_id}`}
                className={`border-b border-gray-03 ${touched ? "bg-blue-row" : ""}`}
              >
                <td className="px-[22px] py-3">
                  <div className="text-[0.90625rem] leading-snug break-words text-ink">
                    {row.line_description}
                  </div>
                  <span className="num text-code text-ink-4">
                    {money(row.line_amount)} · MCC {row.line_mcc}
                  </span>
                </td>
                <td className="px-3 py-3">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span
                      className={`text-[0.90625rem] leading-snug break-words ${
                        touched ? "font-bold text-blue" : "text-ink"
                      }`}
                    >
                      {row.benefit_label}
                    </span>
                    <KindChip kind={row.benefit_kind} />
                  </div>
                </td>
                <td className="num px-3 py-3 text-code text-ink-3">{ruleFor(row)}</td>
                <td className="num px-3 py-3 text-right text-code text-ink-4">
                  {row.capacity_before === null ? (
                    <span>no ceiling</span>
                  ) : (
                    <span>
                      {money(row.capacity_before)} → {money(row.capacity_after ?? 0)}
                    </span>
                  )}
                </td>
                <td className="num px-[22px] py-3 text-right text-[0.90625rem] font-semibold text-navy">
                  {row.value_display}
                </td>
              </tr>
            );
          })}
          <tr className="bg-gray-01">
            <td colSpan={3} className="border-t-2 border-navy px-[22px] py-[18px]">
              <span className="colhead">Witness total at this setting</span>
            </td>
            <td className="num border-t-2 border-navy px-3 py-[18px] text-right text-code text-ink-4">
              {shortHash(point.witness_hash, 8, 6)}
            </td>
            <td className="num border-t-2 border-navy px-[22px] py-[18px] text-right text-[1.1875rem] font-semibold text-navy">
              {point.asserted_display}
            </td>
          </tr>
        </tbody>
      </table>
      <div className="flex flex-col gap-2.5 border-t border-gray-03 bg-gray-01 px-[22px] py-[18px]">
        <Caveat>
          Rebuilt in this browser from the witness, the manifest it names and the cart it
          binds. Nothing here arrives pre-rendered.
        </Caveat>
        <Caveat>{BOOK.disclosure}</Caveat>
      </div>
    </>
  );
}
