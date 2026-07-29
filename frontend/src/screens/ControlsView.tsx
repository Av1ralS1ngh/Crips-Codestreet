/**
 * Beat 7 — hand the judge the controls.
 *
 * Perturb a manifest; watch the ranking and the witness move. Everything else in this
 * console is a rehearsed sequence a presenter drives. This one is a control a skeptic
 * drives, and the only thing that makes it worth anything is that the console does not
 * take the engine's word for the result:
 *
 *   1. The judge moves one field of one benefit — here, a remaining balance.
 *   2. The console applies that same one-field change to the SIGNED manifest body itself
 *      (`lib/perturb.ts`), producing the manifest it is about to check against.
 *   3. The witness the engine produced at that point is re-verified in the browser against
 *      that manifest, in linear time, with no solver (`lib/witness.ts`).
 *
 * If the engine and the console ever disagreed about what a perturbation means, step 3
 * fails and the cell says REJECTED with a code. That is the honest failure and it is left
 * switched on.
 *
 * The allocator is still not ported. Producing an allocation is a decision and the decision
 * belongs to the engine, so every point on the axis is a recorded run of `plumbline.allocate`
 * — the axis is precomputed, the verification is live, and the screen says which is which
 * rather than implying the browser did the optimising.
 *
 * ONE axis, because the design gives this beat one control and no selector. The corpus's
 * first axis is the default and the only one rendered. What may be moved, and what may not:
 * a balance or a headroom is member state — two Card Members hold the same product with
 * different balances on the same day, so moving one asserts nothing about the product's
 * terms. A rate is a product term, so the assumption sentence keys off `axis.fact` and never
 * lets a rate that is not its own stand beside a real product's name.
 *
 * Bar widths are scaled against a basis fixed across the whole axis, not against the top bar
 * at the current stop. Renormalising per stop would pin the leader at full width and the one
 * live width on the screen would stop moving, which is the whole point of the beat.
 */

import { useMemo, useState } from "react";
import {
  BarRow,
  BeatHeader,
  BeatPage,
  BeatSplit,
  ShowsPanel,
  SquarePanel,
  Takeaway,
} from "../components/plumblineUi";
import { money, ms as fmtMs, signedMoney } from "../lib/format";
import { perturbManifest } from "../lib/perturb";
import { verifyWitness } from "../lib/witness";
import { PERTURBATION as BOOK } from "../data/perturbations";
import {
  FACT_MEMBER_STATE,
  type PerturbationAxis,
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
  "what it takes to move the ranking and the witness under it.";

/** The design gives this beat one control. The corpus's first axis is that control. */
const AXIS = BOOK.axes[0];

/** Fixed across the axis, so the width is what moves rather than the scale. */
const BAR_BASIS = Math.max(
  1,
  ...AXIS.points.flatMap((p) => p.ranking.map((r) => r.asserted_minor)),
);

export function ControlsView() {
  const [index, setIndex] = useState(AXIS.baseline_index);

  const axis = AXIS;
  const point = axis.points[Math.min(index, axis.points.length - 1)];
  const baseline = axis.points[axis.baseline_index];
  const instrument = BOOK.instruments.find((i) => i.instrument_id === axis.instrument_id);

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
      >
        {STANDFIRST}
      </BeatHeader>

      <BeatSplit
        aside={
          <ShowsPanel
            points={[
              <Crossover key="crossover" axis={axis} product={instrument.product} />,
              <>
                That is the useful form of the answer. Not “the number is wrong”, but “here
                is the assumption it needs”.
              </>,
              <>
                Every re-check runs in this browser against the recorded corpus — the engine
                allocated each stop, this browser only re-adds. Nothing is fetched to make
                the verdict move.
              </>,
            ]}
          />
        }
      >
        <SquarePanel title="The knob">
          <div className="flex flex-col gap-[18px] px-6 py-7">
            <div className="flex items-baseline justify-between gap-5">
              <span className="text-[0.96875rem] leading-snug break-words text-ink">
                {axis.label}
              </span>
              <span className="num shrink-0 text-[1.625rem] leading-none font-semibold text-blue">
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
                the manifest pinned {baseline.value_display}
              </span>
              <span className="num text-code text-ink-4">
                {axis.points[axis.points.length - 1].value_display}
              </span>
            </div>
          </div>
        </SquarePanel>

        <SquarePanel title="The ranking, under the cardholder's criterion">
          <div className="flex flex-col gap-[26px] px-6 py-7">
            <div className="flex flex-col gap-[22px]">
              {point.ranking.map((entry) => (
                <RankingBar
                  key={entry.instrument_id}
                  entry={entry}
                  isSubject={entry.instrument_id === axis.instrument_id}
                  supported={local.supportsAssertion}
                />
              ))}
            </div>

            <div className="flex flex-wrap items-baseline gap-x-[14px] gap-y-2 border-l-[3px] border-navy bg-gray-01 px-[18px] py-3.5">
              <span className="colhead">Asserted</span>
              <span className="num text-[1.0625rem] font-semibold text-navy">
                {point.asserted_display}
              </span>
              <span className="ml-auto text-[0.90625rem] text-ink-3">
                the value this witness realises at this setting
              </span>
            </div>
          </div>
        </SquarePanel>

        <div className="grid grid-cols-2 gap-px border border-gray-03 bg-gray-03">
          <div className="flex min-w-0 flex-col gap-2.5 bg-white p-6">
            <span className="colhead">This browser's own check</span>
            <span
              className={`num text-[1.1875rem] font-semibold ${
                local.supportsAssertion ? "text-success" : "text-warning"
              }`}
            >
              {local.supportsAssertion ? "SUPPORTED" : "REJECTED"}
            </span>
            <span className="break-words text-[0.90625rem] leading-[1.55] text-ink-2">
              {local.supportsAssertion
                ? `${local.assignments} assignments realise ${money(local.realizedMinor)} in ${fmtMs(local.elapsedMs, 3)} · no solver`
                : (local.failures[0]?.detail ??
                  local.failures[0]?.code ??
                  "does not support the assertion")}
            </span>
          </div>
          <GapCell axis={axis} point={point} />
        </div>
      </BeatSplit>

      <Takeaway>A claim you can perturb is a claim you can trust.</Takeaway>
    </BeatPage>
  );
}

// ---------------------------------------------------------------------------------------

/**
 * What the knob is assuming, keyed to the axis's fact class. A balance moving is an
 * assumption about one account; a rate moving is a product term, and the only one in this
 * corpus belongs to an instrument nobody signed. Neither sentence may read as a published
 * term having changed.
 */
function assumptionOf(axis: PerturbationAxis): string {
  return axis.fact === FACT_MEMBER_STATE
    ? "a balance assumed for one account, not a term any issuer publishes"
    : "a rate no published source supports, on the one instrument here invented outright and signed by nobody";
}

/**
 * The stop at which the subject takes the top of the ranking. Realised-minus-asserted is
 * zero at every stop by construction — the engine asserts exactly what its own witness
 * realises — so the thing an axis can actually be said to "take" is the crossover.
 */
function Crossover({ axis, product }: { axis: PerturbationAxis; product: string }) {
  const crossover =
    axis.points.find((p) => p.ranking[0]?.instrument_id === axis.instrument_id) ?? null;

  if (!crossover) {
    return (
      <>
        No stop on this axis puts the {product} in front of the candidate set — not even at{" "}
        <strong className="font-bold">
          {axis.points[axis.points.length - 1].value_display}
        </strong>
        , which would already be {assumptionOf(axis)}.
      </>
    );
  }

  return (
    <>
      The {product} only takes the top of the ranking at{" "}
      <strong className="font-bold">{crossover.value_display}</strong> — {assumptionOf(axis)}.
    </>
  );
}

/**
 * The distance to the best rival under the cardholder's criterion. It crosses zero at the
 * stop where the subject takes the top of the ranking, which is the figure the beat is for.
 */
function GapCell({ axis, point }: { axis: PerturbationAxis; point: PerturbationPoint }) {
  const subject = point.ranking.find((r) => r.instrument_id === axis.instrument_id) ?? null;
  // The ranking arrives sorted, so the first entry that is not the subject is the best rival.
  const rival = point.ranking.find((r) => r.instrument_id !== axis.instrument_id) ?? null;
  const gap = subject && rival ? subject.asserted_minor - rival.asserted_minor : null;

  return (
    <div className="flex min-w-0 flex-col gap-2.5 bg-white p-6">
      <span className="colhead">What the perturbation costs</span>
      <span className="num text-[1.1875rem] font-semibold text-navy">
        {gap === null ? "—" : signedMoney(gap)}
      </span>
      <span className="text-[0.90625rem] leading-[1.55] text-ink-2">
        Distance between this instrument's assertion and the best rival's at this setting.
      </span>
    </div>
  );
}

function RankingBar({
  entry,
  isSubject,
  supported,
}: {
  entry: RankEntry;
  isSubject: boolean;
  supported: boolean;
}) {
  // Falls back to the id rather than asserting the join. A ranked instrument the corpus
  // does not describe is a corpus bug, and a row reading its raw id is how a reader finds
  // out; a crash on stage is not.
  const record = BOOK.instruments.find((i) => i.instrument_id === entry.instrument_id);
  const label = record?.product ?? entry.instrument_id;

  // Green is not "this one won". It is "this browser re-checked the witness behind this
  // number and it held" — so the subject's bar is the only one that carries the verdict,
  // and it drops to blue the moment the local check stops agreeing with the engine.
  const fill = isSubject ? (supported ? "bg-success" : "bg-blue") : "bg-sky";

  return (
    <BarRow
      label={isSubject ? label : <span className="font-normal text-ink">{label}</span>}
      value={entry.asserted_display}
      valueClass={isSubject ? "text-blue" : "text-navy"}
      pct={(entry.asserted_minor / BAR_BASIS) * 100}
      fill={`${fill} ${BAR_MOTION}`}
    />
  );
}
