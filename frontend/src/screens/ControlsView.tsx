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
 */

import { useMemo, useState } from "react";
import { Bar, Button, Caveat, Empty, Panel, Seal } from "../components/ui";
import { HashRow, KindChip, ScreenHeader, VerifyBox } from "../components/plumblineUi";
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
} from "../lib/plumbline";

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
      <Panel className="h-full" title="Perturbation corpus" tone="deny">
        <Empty>
          <div className="flex max-w-[34rem] flex-col gap-2">
            <span className="num text-deny">{axis.axis_id}</span>
            <span className="text-ink-2">
              names instrument <span className="num">{axis.instrument_id}</span>, which this
              corpus does not carry, so there is no signed body to perturb and nothing to
              verify the witness against.
            </span>
            <span className="text-ink-4">
              Regenerate with{" "}
              <span className="num">scripts/gen_plumbline_beats.py</span>; it refuses to write
              a corpus whose axes and instruments disagree.
            </span>
          </div>
        </Empty>
      </Panel>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <ScreenHeader
        beat="07"
        title="Move one number. Watch the derivation and the ranking move."
        right={
          <Button
            size="sm"
            onClick={() => setIndex(axis.baseline_index)}
            disabled={atBaseline}
          >
            reset to the signed terms
          </Button>
        }
      >
        Move one field. The engine re-ran the allocator at every stop; this browser
        re-checks the result against a manifest it perturbed itself.
      </ScreenHeader>

      {/*
        One line per axis, not two. The product and the fact class used to ride under every
        label as a second uppercase line, which turned five choices into two full rows of
        chrome above the control they select. Both facts are already on the panel below:
        the instrument names itself in the ranking, and the fact class is the knob's hint.
      */}
      <div className="flex shrink-0 flex-wrap gap-1.5">
        {BOOK.axes.map((option) => (
          <button
            key={option.axis_id}
            type="button"
            onClick={() => selectAxis(option)}
            title={`${option.product} · ${option.fact}`}
            className={`rounded-pill border px-3 py-1.5 text-pill transition-colors ${
              option.axis_id === axis.axis_id
                ? "border-blue bg-blue text-white"
                : "border-gray-03 text-ink-3 hover:border-blue hover:text-blue"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1.05fr)_minmax(0,1.35fr)] gap-3">
        {/* ------------------------------------------------------------------- the control */}
        <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
          <Panel
            title="The knob"
            hint={axis.fact}
            right={<Seal signed={atBaseline && instrument.issuer_signed} />}
            bodyClassName="flex flex-col gap-3 p-3.5"
          >
            <div className="flex items-end justify-between gap-3">
              <div className="flex min-w-0 flex-col gap-0.5">
                <span className="break-words text-body text-ink">{axis.benefit_label}</span>
                <div className="flex items-center gap-1.5">
                  <KindChip kind={axis.benefit_kind} />
                  <span className="num text-pill text-ink-4">
                    {axis.field} · {axis.benefit_window}
                  </span>
                </div>
              </div>
              <span
                className={`num display-wide shrink-0 text-[2.1rem] leading-none font-semibold ${
                  atBaseline ? "text-ink" : "text-stepup"
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
              className="w-full accent-[var(--color-stepup)]"
            />
            <div className="flex items-baseline justify-between gap-2">
              <span className="num text-pill text-ink-4">{axis.points[0].value_display}</span>
              <span className="num text-pill text-ink-4">
                {axis.points.length} engine-evaluated stops
              </span>
              <span className="num text-pill text-ink-4">
                {axis.points[axis.points.length - 1].value_display}
              </span>
            </div>

            <div className="flex items-baseline justify-between gap-2 border-t border-line pt-2">
              <span className="text-pill text-ink-3">
                signed value{" "}
                <span className="num text-ink-2">{baseline.value_display}</span>
              </span>
              <span
                className={`num text-pill ${atBaseline ? "text-ink-4" : "text-stepup"}`}
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

          </Panel>

          <SignaturePanel
            instrument={instrument}
            axis={axis}
            point={point}
            atBaseline={atBaseline}
          />

          <Panel
            title="This browser's own check"
            hint="the same verifier the other screens run"
            bodyClassName="flex flex-col gap-2 p-3.5"
          >
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
              <p key={`${failure.code}-${i}`} className="num text-pill text-deny">
                {failure.detail}
              </p>
            ))}
          </Panel>
        </div>

        {/* -------------------------------------------------------- ranking and derivation */}
        <div className="flex min-h-0 flex-col gap-3">
          <RankingPanel axis={axis} point={point} baseline={baseline} />

          <Panel
            title="The derivation at this setting"
            hint="rebuilt in the browser from the witness, not shipped as a table"
            right={
              <span className="num text-pill text-ink-4">
                {point.allocator_stats.assigned} of {point.allocator_stats.considered} pairings
                assigned
              </span>
            }
            className="min-h-0 flex-1"
            bodyClassName="min-h-0 overflow-y-auto"
          >
            <DerivationTable rows={rows} point={point} axis={axis} />
          </Panel>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------

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
    <Panel
      title="What the perturbation costs"
      hint={atBaseline ? "nothing yet" : "the signature no longer covers this body"}
      tone={atBaseline ? "proof" : "deny"}
      bodyClassName="flex flex-col gap-1.5 p-3.5"
    >
      <HashRow
        label="signed body"
        value={axis.signed_manifest_hash}
        tone={atBaseline ? "proof" : "muted"}
      />
      <HashRow label="body on screen" value={point.manifest_hash} tone={atBaseline ? "proof" : "muted"} />
      {/*
        One sentence, because the two hashes above already made the point: they either
        match or they do not. The long version of this argument — that a balance is member
        state and moving it asserts nothing about a published product term — is the axis
        caption, and repeating it here was the densest text on the screen.
      */}
      <p className="text-pill leading-snug text-ink-3">
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
    </Panel>
  );
}

function RankingPanel({
  axis,
  point,
  baseline,
}: {
  axis: PerturbationAxis;
  point: PerturbationPoint;
  baseline: PerturbationPoint;
}) {
  const baselineRank = new Map(baseline.ranking.map((r) => [r.instrument_id, r]));
  const top = point.ranking[0].asserted_minor;
  const moved = point.ranking.some(
    (r) => baselineRank.get(r.instrument_id)?.rank !== r.rank,
  );

  return (
    <Panel
      title="The ranking, under the cardholder's criterion"
      hint={moved ? "the order moved" : "the order held"}
      right={
        <span className="num text-pill text-ink-4">not issuer-endorsed</span>
      }
      bodyClassName="flex flex-col gap-1.5 p-3.5"
    >
      {point.ranking.map((entry) => {
        // Falls back to the id rather than asserting the join. A ranked instrument the
        // corpus does not describe is a corpus bug, and a row reading its raw id is how a
        // reader finds out; a crash on stage is not.
        const record = BOOK.instruments.find((i) => i.instrument_id === entry.instrument_id);
        const was = baselineRank.get(entry.instrument_id);
        const isSubject = entry.instrument_id === axis.instrument_id;
        const delta = entry.asserted_minor - (was?.asserted_minor ?? entry.asserted_minor);
        const rankDelta = (was?.rank ?? entry.rank) - entry.rank;
        return (
          <div
            key={entry.instrument_id}
            className={`flex flex-col gap-1 rounded border px-2.5 py-1.5 ${
              isSubject ? "border-stepup/45 bg-stepup-wash/50" : "border-line bg-sunken/50"
            }`}
          >
            <div className="flex items-baseline gap-2">
              <span
                className={`num w-5 shrink-0 text-pill ${
                  entry.rank === 1 ? "text-allow" : "text-ink-4"
                }`}
              >
                #{entry.rank}
              </span>
              <span className="min-w-0 flex-1 break-words text-pill text-ink-2">
                {record?.product ?? entry.instrument_id}
                {record && <span className="text-ink-4"> · {record.issuer}</span>}
              </span>
              {rankDelta !== 0 && (
                <span
                  className={`num shrink-0 text-pill ${
                    rankDelta > 0 ? "text-allow" : "text-deny"
                  }`}
                >
                  {rankDelta > 0 ? `▲${rankDelta}` : `▼${-rankDelta}`}
                </span>
              )}
              <span className="num shrink-0 text-body text-ink">
                {entry.asserted_display}
              </span>
            </div>
            <Bar
              value={entry.asserted_minor}
              total={top}
              tone={isSubject ? "proof" : entry.rank === 1 ? "allow" : "default"}
            />
            {isSubject && delta !== 0 && (
              <span className="num text-pill text-stepup">
                {signedMoney(delta)} against the signed terms
              </span>
            )}
          </div>
        );
      })}
      {/*
        The header already carries "not issuer-endorsed", which is the load-bearing half.
        What remains worth saying is whose criterion produced this order, in one line.
      */}
      <Caveat>The cardholder's criterion, not the issuer's: {BOOK.criterion}.</Caveat>
    </Panel>
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
    <table className="w-full border-collapse text-pill">
      <thead>
        <tr className="sticky top-0 z-10 bg-panel">
          <th className="eyebrow border-b border-line px-3.5 py-2 text-left font-semibold">
            Line
          </th>
          <th className="eyebrow border-b border-line px-2 py-2 text-left font-semibold">
            Benefit
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
        {rows.map((row) => {
          const touched = row.benefit_id === axis.benefit_id;
          return (
            <tr
              key={`${row.line_sku}-${row.benefit_id}`}
              className={`border-b border-line/50 ${touched ? "bg-stepup-wash/40" : ""}`}
            >
              <td className="px-3.5 py-1.5">
                <div className="break-words text-ink-2">{row.line_description}</div>
                <span className="num text-pill text-ink-4">
                  {money(row.line_amount)} · MCC {row.line_mcc}
                </span>
              </td>
              <td className="px-2 py-1.5">
                <div className="flex items-baseline gap-1.5">
                  <span className={`break-words ${touched ? "text-stepup" : "text-ink-2"}`}>
                    {row.benefit_label}
                  </span>
                  <KindChip kind={row.benefit_kind} />
                </div>
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
          );
        })}
        <tr>
          <td colSpan={3} className="border-t-2 border-line-2 px-3.5 py-2.5">
            <span className="eyebrow">Witness total at this setting</span>
          </td>
          <td className="border-t-2 border-line-2 px-2 py-2.5 text-right">
            <span className="num text-pill text-ink-4">
              {shortHash(point.witness_hash, 8, 6)}
            </span>
          </td>
          <td className="border-t-2 border-line-2 px-3.5 py-2.5 text-right">
            <span className="num display-wide text-[1.25rem] leading-none font-semibold text-allow">
              {point.asserted_display}
            </span>
          </td>
        </tr>
        <tr>
          <td colSpan={5} className="px-3.5 py-2">
            <Caveat>{BOOK.disclosure}</Caveat>
          </td>
        </tr>
      </tbody>
    </table>
  );
}
