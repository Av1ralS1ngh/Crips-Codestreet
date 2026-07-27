/**
 * Beat 4, second half — the money slide.
 *
 * Amex undertook Agent Purchase Protection before publishing the machinery to adjudicate
 * it. This is that machinery priced: every operator carries a behavioural risk score
 * derived from decisions the ledger already recorded, and the cutoff is the underwriting
 * lever. Drag it and the covered book moves.
 *
 * The curve is derived from `exposure.operators` rather than the API's precomputed
 * `curve`, so the chart and the table below it can never disagree, and so the cutoff
 * stays draggable without a round trip. All arithmetic is integer minor units and basis
 * points — no floats touch a money figure.
 */

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { Caveat, Empty, Panel, Stat } from "../components/ui";
import { money, moneyCompact } from "../lib/format";
import { useConsole } from "../lib/store";
import type { ExposureBook, OperatorExposure, RiskBand } from "../lib/types";

const BAND_STYLES: Record<RiskBand, string> = {
  PRIME: "border-line-2 bg-raised text-ink-2",
  STANDARD: "border-line-2 bg-raised text-ink-2",
  WATCH: "border-stepup/40 bg-stepup-wash text-stepup",
  RESTRICTED: "border-deny/50 bg-deny-wash text-deny",
};

interface Slice {
  coveredExposure: number;
  declinedExposure: number;
  expectedLoss: number;
  blendedBps: number;
  coveredCount: number;
  declinedCount: number;
}

function sliceAt(operators: OperatorExposure[], cutoff: number): Slice {
  let coveredExposure = 0;
  let declinedExposure = 0;
  let expectedLoss = 0;
  let coveredCount = 0;
  for (const operator of operators) {
    if (operator.risk_score >= cutoff) {
      coveredExposure += operator.modelled_exposure;
      expectedLoss += operator.expected_loss;
      coveredCount += 1;
    } else {
      declinedExposure += operator.modelled_exposure;
    }
  }
  return {
    coveredExposure,
    declinedExposure,
    expectedLoss,
    blendedBps: coveredExposure
      ? Math.floor((expectedLoss * 10_000) / coveredExposure)
      : 0,
    coveredCount,
    declinedCount: operators.length - coveredCount,
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
      <Panel className="h-full">
        <Empty>Exposure book unavailable.</Empty>
      </Panel>
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
  const total = exposure.total_exposure;

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <header className="flex shrink-0 flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="display-xwide text-[1.7rem] leading-none font-semibold tracking-[-0.01em]">
            Agent Purchase Protection, priced.
          </h1>
          <p className="mt-1.5 text-body text-ink-2">
            Behavioural risk per operator, derived from decisions already in the ledger.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="eyebrow">Cutoff score</span>
          <div className="num display-wide text-[1.9rem] leading-none font-semibold">{cutoff}</div>
          {cutoff !== exposure.cutoff_score && (
            <button
              type="button"
              onClick={() => onCutoff(exposure.cutoff_score)}
              className="rounded border border-line px-2 py-1 text-pill text-ink-3 hover:border-line-2 hover:text-ink"
            >
              reset to {exposure.cutoff_score}
            </button>
          )}
        </div>
      </header>

      <Panel bodyClassName="grid grid-cols-5 gap-6 p-5">
        <Stat label="Book exposure" value={money(total)} sub={`${operators.length} operators`} />
        <Stat
          label="Covered"
          value={money(slice.coveredExposure)}
          sub={`${slice.coveredCount} inside the cutoff`}
        />
        <Stat
          label="Declined"
          value={money(slice.declinedExposure)}
          sub={`${slice.declinedCount} outside`}
          tone="deny"
        />
        <Stat
          label="Expected loss"
          value={money(slice.expectedLoss)}
          sub="on the covered book"
          tone="proof"
        />
        <Stat
          label="Blended rate"
          value={`${slice.blendedBps} bps`}
          sub={
            cutoff === exposure.cutoff_score
              ? "at the published cutoff"
              : `${slice.blendedBps - baseline.blendedBps >= 0 ? "+" : ""}${
                  slice.blendedBps - baseline.blendedBps
                } bps vs published`
          }
          tone="proof"
        />
      </Panel>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] gap-3">
        <Panel title="Cutoff curve" hint="exposure retained by cutoff" bodyClassName="p-4 min-h-0">
          <CutoffCurve
            operators={operators}
            cutoff={cutoff}
            onCutoff={onCutoff}
            total={total}
            slice={slice}
          />
        </Panel>

        {/* The hint said "risk score derived from ledgered behaviour", which the standfirst
            above now says once. */}
        <Panel title="Operator book" bodyClassName="min-h-0 overflow-y-auto">
          <OperatorTable operators={operators} cutoff={cutoff} />
        </Panel>
      </div>

      <Caveat>
        {exposure.model}. Risk scores shrink toward a prior on low volume; loss rates are
        modelled, not observed.
      </Caveat>
    </div>
  );
}

// ---------------------------------------------------------------------------------------

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

  const line = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.score)},${y(p.covered)}`).join(" ");
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
    <div ref={ref} className="flex h-full min-h-0 flex-col">
      <svg
        width={width || 10}
        height={height}
        className="shrink-0 cursor-col-resize"
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
            <stop offset="0%" stopColor="var(--color-proof)" stopOpacity="0.22" />
            <stop offset="100%" stopColor="var(--color-proof)" stopOpacity="0.02" />
          </linearGradient>
        </defs>

        {[0, 0.25, 0.5, 0.75, 1].map((fraction) => (
          <g key={fraction}>
            <line
              x1={pad.left}
              x2={pad.left + plotW}
              y1={pad.top + plotH * (1 - fraction)}
              y2={pad.top + plotH * (1 - fraction)}
              stroke="var(--color-line)"
              strokeWidth="1"
            />
            <text
              x={pad.left - 8}
              y={pad.top + plotH * (1 - fraction) + 3}
              textAnchor="end"
              className="num"
              fontSize="9"
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
            fontSize="9"
            fill="var(--color-ink-4)"
          >
            {score}
          </text>
        ))}

        <path d={area} fill="url(#coverFill)" />
        <path d={line} fill="none" stroke="var(--color-proof)" strokeWidth="1.75" />
        <path
          d={bpsLine}
          fill="none"
          stroke="var(--color-stepup)"
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
          stroke="var(--color-deny)"
          strokeWidth="1.5"
        />
        <line
          x1={x(cutoff) - 5}
          x2={x(cutoff) + 5}
          y1={y(total)}
          y2={y(total)}
          stroke="var(--color-deny)"
          strokeWidth="1.5"
        />
        <line
          x1={x(cutoff) - 5}
          x2={x(cutoff) + 5}
          y1={y(slice.coveredExposure)}
          y2={y(slice.coveredExposure)}
          stroke="var(--color-deny)"
          strokeWidth="1.5"
        />
        <circle cx={x(cutoff)} cy={y(slice.coveredExposure)} r="4" fill="var(--color-proof)" />

        <text
          x={x(cutoff) + 9}
          y={(y(total) + y(slice.coveredExposure)) / 2 + 3}
          className="num"
          fontSize="10"
          fill="var(--color-deny)"
        >
          {slice.declinedExposure > 0 ? `declined ${moneyCompact(slice.declinedExposure)}` : ""}
        </text>

        <text
          x={pad.left + plotW + 6}
          y={yBps(points[points.length - 1].bps) + 3}
          className="num"
          fontSize="9"
          fill="var(--color-stepup)"
        >
          bps
        </text>
        <text
          x={pad.left + plotW + 6}
          y={y(points[points.length - 1].covered) + 3}
          className="num"
          fontSize="9"
          fill="var(--color-proof)"
        >
          book
        </text>
      </svg>

      <div className="mt-2 flex flex-wrap items-baseline gap-x-5 gap-y-1 text-pill">
        <span className="text-ink-3">
          <span className="text-proof">━</span> exposure retained
        </span>
        <span className="text-ink-3">
          <span className="text-stepup">╌</span> blended loss rate
        </span>
        <span className="text-ink-4">drag or ← → to move the cutoff</span>
      </div>
    </div>
  );
}

function OperatorTable({
  operators,
  cutoff,
}: {
  operators: OperatorExposure[];
  cutoff: number;
}) {
  return (
    <table className="w-full border-collapse text-pill">
      <thead className="sticky top-0 z-10 bg-panel">
        <tr className="border-b border-line text-left">
          <Th className="pl-3.5">Operator</Th>
          <Th align="right">Score</Th>
          <Th>Band</Th>
          <Th align="right">Auth</Th>
          <Th align="right">Deny</Th>
          <Th align="right" title="cart divergence / injection absorbed / scope escalation">
            Signals
          </Th>
          <Th align="right">Exposure</Th>
          <Th align="right">Rate</Th>
          <Th align="right" className="pr-3.5">
            E[loss]
          </Th>
        </tr>
      </thead>
      <tbody>
        {operators.map((operator) => {
          const covered = operator.risk_score >= cutoff;
          return (
            <tr
              key={operator.operator_id}
              className={`border-b border-line/60 transition-opacity ${
                covered ? "" : "opacity-45"
              }`}
            >
              <td className="max-w-0 py-1.5 pl-3.5">
                <div className="flex items-center gap-2">
                  <span
                    className={`h-3 w-0.5 shrink-0 rounded ${covered ? "bg-proof" : "bg-deny"}`}
                  />
                  <span className="break-words">{operator.name}</span>
                </div>
              </td>
              <td className="py-1.5 text-right">
                <div className="flex items-center justify-end gap-2">
                  <span className="relative h-1 w-9 overflow-hidden rounded-full bg-line">
                    <span
                      className={`absolute inset-y-0 left-0 ${
                        operator.risk_score >= 70 ? "bg-ink-3" : "bg-deny"
                      }`}
                      style={{ width: `${operator.risk_score}%` }}
                    />
                  </span>
                  <span className="num w-6 text-right">{operator.risk_score}</span>
                </div>
              </td>
              <td className="py-1.5">
                <span
                  className={`rounded border px-1 py-px font-mono text-pill tracking-[0.05em] ${
                    BAND_STYLES[operator.band]
                  }`}
                >
                  {operator.band}
                </span>
              </td>
              <td className="num py-1.5 text-right text-ink-2">{operator.authorized_count}</td>
              <td className="num py-1.5 text-right text-ink-2">{operator.denied_count}</td>
              <td className="num py-1.5 text-right">
                <span className={operator.divergence_count ? "text-stepup" : "text-ink-4"}>
                  {operator.divergence_count}
                </span>
                <span className="text-ink-4">/</span>
                <span className={operator.injection_count ? "text-deny" : "text-ink-4"}>
                  {operator.injection_count}
                </span>
                <span className="text-ink-4">/</span>
                <span className={operator.escalation_attempts ? "text-deny" : "text-ink-4"}>
                  {operator.escalation_attempts}
                </span>
              </td>
              <td className="num py-1.5 text-right">{money(operator.modelled_exposure)}</td>
              <td className="num py-1.5 text-right text-ink-3">{operator.premium_bps}</td>
              <td className="num py-1.5 pr-3.5 text-right text-proof">
                {money(operator.expected_loss)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Th({
  children,
  align = "left",
  className = "",
  title,
}: {
  children: React.ReactNode;
  align?: "left" | "right";
  className?: string;
  title?: string;
}) {
  return (
    <th
      title={title}
      className={`eyebrow py-2 font-semibold ${align === "right" ? "text-right" : ""} ${className}`}
    >
      {children}
    </th>
  );
}
