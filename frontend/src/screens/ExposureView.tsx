/**
 * Kernel, tab 4 — the money slide.
 *
 * Amex undertook Agent Purchase Protection before publishing the machinery to adjudicate
 * it. This is that machinery priced: every operator carries a behavioural risk score
 * derived from decisions the ledger already recorded, and the cutoff is the underwriting
 * lever. Drag it and the covered book moves.
 *
 * The curve is derived from `exposure.operators` rather than the API's precomputed
 * `curve`, so the chart, the three cutoff bars and the table below can never disagree, and
 * so the cutoff stays draggable without a round trip. All arithmetic is integer minor units
 * and basis points; no floats touch a money figure.
 *
 * The share printed under the bars is coverage lost, never authorisation denied. Coverage
 * is conditioned on evidence; authorisation is not.
 */

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { BarRow, SquarePanel } from "../components/plumblineUi";
import { Caveat } from "../components/ui";
import { money, moneyCompact } from "../lib/format";
import { useConsole } from "../lib/store";
import type { ExposureBook, OperatorExposure, RiskBand } from "../lib/types";

const PERMISSIVE = 0;
const STRICT = 100;

const BAND_STYLES: Record<RiskBand, string> = {
  PRIME: "text-ink-2",
  STANDARD: "text-ink-2",
  WATCH: "text-attention-ink",
  RESTRICTED: "text-warning",
};

interface Slice {
  coveredExposure: number;
  declinedExposure: number;
  expectedLoss: number;
  blendedBps: number;
  coveredCount: number;
  declinedCount: number;
  declinedAuthorisations: number;
}

function sliceAt(operators: OperatorExposure[], cutoff: number): Slice {
  let coveredExposure = 0;
  let declinedExposure = 0;
  let expectedLoss = 0;
  let coveredCount = 0;
  let declinedAuthorisations = 0;
  for (const operator of operators) {
    if (operator.risk_score >= cutoff) {
      coveredExposure += operator.modelled_exposure;
      expectedLoss += operator.expected_loss;
      coveredCount += 1;
    } else {
      declinedExposure += operator.modelled_exposure;
      declinedAuthorisations += operator.authorized_count;
    }
  }
  return {
    coveredExposure,
    declinedExposure,
    expectedLoss,
    blendedBps: coveredExposure ? Math.floor((expectedLoss * 10_000) / coveredExposure) : 0,
    coveredCount,
    declinedCount: operators.length - coveredCount,
    declinedAuthorisations,
  };
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(el);
    setWidth(el.clientWidth);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

export function ExposureView() {
  const exposure = useConsole((s) => s.state?.exposure);
  const [cutoff, setCutoff] = useState<number | null>(null);

  if (!exposure) {
    return (
      <div className="border border-gray-03 bg-white px-[22px] py-10 text-center text-body text-ink-3">
        Exposure book unavailable.
      </div>
    );
  }

  const active = cutoff ?? exposure.cutoff_score;
  return <ExposureBody exposure={exposure} cutoff={active} onCutoff={setCutoff} />;
}

function ExposureBody({
  exposure,
  cutoff,
  onCutoff,
}: {
  exposure: ExposureBook;
  cutoff: number;
  onCutoff: (value: number) => void;
}) {
  const operators = useMemo(
    () => [...exposure.operators].sort((a, b) => b.modelled_exposure - a.modelled_exposure),
    [exposure.operators],
  );
  const slice = useMemo(() => sliceAt(operators, cutoff), [operators, cutoff]);
  const baseline = useMemo(
    () => sliceAt(operators, exposure.cutoff_score),
    [operators, exposure.cutoff_score],
  );
  const permissive = useMemo(() => sliceAt(operators, PERMISSIVE), [operators]);
  const strict = useMemo(() => sliceAt(operators, STRICT), [operators]);
  const total = exposure.total_exposure;
  const authorisations = useMemo(
    () => operators.reduce((sum, operator) => sum + operator.authorized_count, 0),
    [operators],
  );

  const share = (count: number) =>
    authorisations ? `${((count / authorisations) * 100).toFixed(1)}%` : "0%";
  const width = (value: number) => (total ? (value / total) * 100 : 0);

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-5">
        <div className="min-w-0">
          <p className="text-[1.0625rem] leading-[1.6] font-bold text-ink">Protection, priced.</p>
          <p className="mt-1 max-w-[62ch] text-[0.90625rem] leading-[1.55] font-normal text-ink-2">
            Behavioural risk per operator, derived from decisions already in the ledger.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-4">
          <div className="flex flex-col gap-1.5">
            <span className="colhead">Cutoff score</span>
            <span className="num text-[1.75rem] leading-none font-semibold text-navy">
              {cutoff}
            </span>
          </div>
          {cutoff !== exposure.cutoff_score && (
            <button
              type="button"
              onClick={() => onCutoff(exposure.cutoff_score)}
              className="rounded-pill border border-gray-03 bg-white px-4 py-2 text-[0.78125rem] font-semibold text-navy transition-colors duration-[180ms] hover:border-blue hover:text-blue"
            >
              reset to {exposure.cutoff_score}
            </button>
          )}
        </div>
      </header>

      {/* The gap is the rule: a 1px grid over the border colour, white children. */}
      <div className="grid grid-cols-5 gap-px border border-gray-03 bg-gray-03">
        <Cell label="Book exposure" value={money(total)} note={`${operators.length} operators`} />
        <Cell
          label="Covered"
          value={money(slice.coveredExposure)}
          note={`${slice.coveredCount} inside the cutoff`}
        />
        <Cell
          label="Declined"
          value={money(slice.declinedExposure)}
          note={`${slice.declinedCount} outside`}
          tone="text-warning"
        />
        <Cell
          label="Expected loss"
          value={money(slice.expectedLoss)}
          note="on the covered book"
          tone="text-blue"
        />
        <Cell
          label="Blended rate"
          value={`${slice.blendedBps} bps`}
          note={
            cutoff === exposure.cutoff_score
              ? "at the published cutoff"
              : `${slice.blendedBps - baseline.blendedBps >= 0 ? "+" : ""}${
                  slice.blendedBps - baseline.blendedBps
                } bps vs published`
          }
          tone="text-blue"
        />
      </div>

      <SquarePanel title="Exposure retained by cutoff">
        <div className="flex flex-col gap-[22px] px-6 py-[26px]">
          <BarRow
            label={`Cutoff ${PERMISSIVE} · permissive`}
            value={money(permissive.coveredExposure)}
            pct={width(permissive.coveredExposure)}
            sub={`every registered operator covered · ${share(permissive.declinedAuthorisations)} of authorisations outside coverage`}
          />
          <BarRow
            label={`Cutoff ${cutoff} · operator book`}
            value={money(slice.coveredExposure)}
            pct={width(slice.coveredExposure)}
            fill="bg-blue"
            valueClass="text-blue"
            sub={`${slice.coveredCount} of ${operators.length} operators · ${share(slice.declinedAuthorisations)} of authorisations outside coverage`}
          />
          <BarRow
            label={`Cutoff ${STRICT} · strict`}
            value={money(strict.coveredExposure)}
            pct={width(strict.coveredExposure)}
            fill="bg-sky"
            sub={`an unblemished record only · ${share(strict.declinedAuthorisations)} of authorisations outside coverage`}
          />
          <p className="text-[0.90625rem] leading-[1.6] font-normal text-ink-3">
            Strict is not free. At cutoff {cutoff} it leaves{" "}
            {share(slice.declinedAuthorisations)} of the authorisations already recorded
            outside coverage. The operator picks a point on this curve and the log records
            which one.
          </p>
        </div>
      </SquarePanel>

      <SquarePanel
        title="Cutoff curve"
        right={
          <span className="num text-[0.71875rem] font-normal text-ink-4">
            drag, or ← → to move the cutoff
          </span>
        }
      >
        <div className="px-6 py-[26px]">
          <CutoffCurve
            operators={operators}
            cutoff={cutoff}
            onCutoff={onCutoff}
            total={total}
            slice={slice}
          />
        </div>
      </SquarePanel>

      <SquarePanel
        title="Operator book"
        right={
          <span className="num text-[0.71875rem] font-normal text-ink-4">
            divergence / injection / escalation
          </span>
        }
      >
        <OperatorTable operators={operators} cutoff={cutoff} />
      </SquarePanel>

      <Caveat>
        {exposure.model}. Risk scores shrink toward a prior on low volume; loss rates are
        modelled, not observed.
      </Caveat>
    </div>
  );
}

// ---------------------------------------------------------------------------------------

function Cell({
  label,
  value,
  note,
  tone = "text-navy",
}: {
  label: string;
  value: string;
  note: string;
  tone?: string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-2 bg-white px-5 py-[18px]">
      <span className="colhead">{label}</span>
      <span className={`num text-[1.1875rem] leading-none font-semibold break-words ${tone}`}>
        {value}
      </span>
      <span className="text-[0.8125rem] leading-[1.45] break-words text-ink-4">{note}</span>
    </div>
  );
}

function CutoffCurve({
  operators,
  cutoff,
  onCutoff,
  total,
  slice,
}: {
  operators: OperatorExposure[];
  cutoff: number;
  onCutoff: (value: number) => void;
  total: number;
  slice: Slice;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const height = 260;
  const pad = { top: 18, right: 58, bottom: 30, left: 66 };
  const plotW = Math.max(10, width - pad.left - pad.right);
  const plotH = height - pad.top - pad.bottom;

  const points = useMemo(() => {
    const out: { score: number; covered: number; bps: number }[] = [];
    for (let score = 0; score <= 100; score += 1) {
      const at = sliceAt(operators, score);
      out.push({ score, covered: at.coveredExposure, bps: at.blendedBps });
    }
    return out;
  }, [operators]);

  const maxBps = Math.max(1, ...points.map((p) => p.bps));
  const x = (score: number) => pad.left + (score / 100) * plotW;
  const y = (value: number) => pad.top + plotH - (total ? (value / total) * plotH : 0);
  const yBps = (value: number) => pad.top + plotH - (value / maxBps) * plotH;

  const line = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${x(p.score)},${y(p.covered)}`)
    .join(" ");
  const area = `${line} L${x(100)},${pad.top + plotH} L${x(0)},${pad.top + plotH} Z`;
  const bpsLine = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${x(p.score)},${yBps(p.bps)}`)
    .join(" ");

  const pick = (event: React.MouseEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - rect.left - pad.left) / plotW;
    onCutoff(Math.max(0, Math.min(100, Math.round(ratio * 100))));
  };

  return (
    <div ref={ref} className="flex flex-col">
      <svg
        width={width || 10}
        height={height}
        className="cursor-col-resize"
        onMouseDown={pick}
        onMouseMove={(e) => e.buttons === 1 && pick(e)}
        role="slider"
        tabIndex={0}
        aria-label="risk score cutoff"
        aria-valuenow={cutoff}
        aria-valuemin={0}
        aria-valuemax={100}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") onCutoff(Math.max(0, cutoff - 1));
          if (e.key === "ArrowRight") onCutoff(Math.min(100, cutoff + 1));
        }}
      >
        <defs>
          <linearGradient id="coverFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-blue)" stopOpacity="0.18" />
            <stop offset="100%" stopColor="var(--color-blue)" stopOpacity="0.02" />
          </linearGradient>
        </defs>

        {[0, 0.25, 0.5, 0.75, 1].map((fraction) => (
          <g key={fraction}>
            <line
              x1={pad.left}
              x2={pad.left + plotW}
              y1={pad.top + plotH * (1 - fraction)}
              y2={pad.top + plotH * (1 - fraction)}
              stroke="var(--color-gray-03)"
              strokeWidth="1"
            />
            <text
              x={pad.left - 8}
              y={pad.top + plotH * (1 - fraction) + 3}
              textAnchor="end"
              className="num"
              fontSize="10.5"
              fill="var(--color-ink-4)"
            >
              {moneyCompact(total * fraction)}
            </text>
          </g>
        ))}

        {[0, 25, 50, 75, 100].map((score) => (
          <text
            key={score}
            x={x(score)}
            y={height - 12}
            textAnchor="middle"
            className="num"
            fontSize="10.5"
            fill="var(--color-ink-4)"
          >
            {score}
          </text>
        ))}

        <path d={area} fill="url(#coverFill)" />
        <path d={line} fill="none" stroke="var(--color-blue)" strokeWidth="1.75" />
        <path
          d={bpsLine}
          fill="none"
          stroke="var(--color-attention-ink)"
          strokeWidth="1.25"
          strokeDasharray="3 3"
          opacity="0.85"
        />

        {/* Declined band: the vertical gap between the whole book and what survives the cut. */}
        <line
          x1={x(cutoff)}
          x2={x(cutoff)}
          y1={pad.top}
          y2={pad.top + plotH}
          stroke="var(--color-warning)"
          strokeWidth="1.5"
        />
        <line
          x1={x(cutoff) - 5}
          x2={x(cutoff) + 5}
          y1={y(total)}
          y2={y(total)}
          stroke="var(--color-warning)"
          strokeWidth="1.5"
        />
        <line
          x1={x(cutoff) - 5}
          x2={x(cutoff) + 5}
          y1={y(slice.coveredExposure)}
          y2={y(slice.coveredExposure)}
          stroke="var(--color-warning)"
          strokeWidth="1.5"
        />
        <circle cx={x(cutoff)} cy={y(slice.coveredExposure)} r="4" fill="var(--color-blue)" />

        <text
          x={x(cutoff) + 9}
          y={(y(total) + y(slice.coveredExposure)) / 2 + 3}
          className="num"
          fontSize="11"
          fill="var(--color-warning)"
        >
          {slice.declinedExposure > 0 ? `declined ${moneyCompact(slice.declinedExposure)}` : ""}
        </text>

        <text
          x={pad.left + plotW + 6}
          y={yBps(points[points.length - 1].bps) + 3}
          className="num"
          fontSize="10.5"
          fill="var(--color-attention-ink)"
        >
          bps
        </text>
        <text
          x={pad.left + plotW + 6}
          y={y(points[points.length - 1].covered) + 3}
          className="num"
          fontSize="10.5"
          fill="var(--color-blue)"
        >
          book
        </text>
      </svg>

      <div className="mt-3 flex flex-wrap items-baseline gap-x-6 gap-y-1.5 text-[0.78125rem]">
        <span className="text-ink-3">
          <span className="text-blue">━</span> exposure retained
        </span>
        <span className="text-ink-3">
          <span className="text-attention-ink">╌</span> blended loss rate
        </span>
        <span className="text-ink-4">the log records which point the operator picked</span>
      </div>
    </div>
  );
}

const TABLE_COLUMNS =
  "grid-cols-[minmax(0,1.6fr)_58px_92px_64px_64px_84px_minmax(0,1fr)_56px_minmax(0,1fr)]";

function OperatorTable({
  operators,
  cutoff,
}: {
  operators: OperatorExposure[];
  cutoff: number;
}) {
  return (
    <>
      <div className={`grid ${TABLE_COLUMNS} gap-3 border-b border-gray-02 px-[22px] py-3.5`}>
        <span className="colhead">Operator</span>
        <span className="colhead text-right">Score</span>
        <span className="colhead">Band</span>
        <span className="colhead text-right">Auth</span>
        <span className="colhead text-right">Deny</span>
        <span className="colhead text-right">Signals</span>
        <span className="colhead text-right">Exposure</span>
        <span className="colhead text-right">Rate</span>
        <span className="colhead text-right">E[loss]</span>
      </div>
      {operators.map((operator) => {
        const covered = operator.risk_score >= cutoff;
        return (
          <div
            key={operator.operator_id}
            className={`grid ${TABLE_COLUMNS} items-baseline gap-3 border-b border-gray-02 px-[22px] py-3 last:border-b-0 ${
              covered ? "" : "bg-gray-01"
            }`}
          >
            <span className="flex min-w-0 items-baseline gap-2">
              <span
                className={`inline-block h-2 w-[3px] shrink-0 ${covered ? "bg-blue" : "bg-warning"}`}
              />
              <span className="min-w-0 text-[0.84375rem] leading-[1.4] break-words text-ink">
                {operator.name}
              </span>
            </span>
            <span className="num text-right text-[0.84375rem] text-navy">
              {operator.risk_score}
            </span>
            <span
              className={`num text-[0.71875rem] tracking-[0.05em] ${BAND_STYLES[operator.band]}`}
            >
              {operator.band}
            </span>
            <span className="num text-right text-[0.84375rem] text-ink-2">
              {operator.authorized_count}
            </span>
            <span className="num text-right text-[0.84375rem] text-ink-2">
              {operator.denied_count}
            </span>
            <span className="num text-right text-[0.84375rem]">
              <span className={operator.divergence_count ? "text-attention-ink" : "text-ink-4"}>
                {operator.divergence_count}
              </span>
              <span className="text-ink-4">/</span>
              <span className={operator.injection_count ? "text-warning" : "text-ink-4"}>
                {operator.injection_count}
              </span>
              <span className="text-ink-4">/</span>
              <span className={operator.escalation_attempts ? "text-warning" : "text-ink-4"}>
                {operator.escalation_attempts}
              </span>
            </span>
            <span className="num text-right text-[0.84375rem] text-navy">
              {money(operator.modelled_exposure)}
            </span>
            <span className="num text-right text-[0.84375rem] text-ink-3">
              {operator.premium_bps}
            </span>
            <span className="num text-right text-[0.84375rem] text-blue">
              {money(operator.expected_loss)}
            </span>
          </div>
        );
      })}
    </>
  );
}
