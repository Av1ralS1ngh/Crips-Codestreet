/**
 * Vocabulary shared by the five PLUMBLINE screens.
 *
 * One rule enforced here: wherever a verification result appears, the server's verdict and
 * the console's own recomputation appear together. A single green tick would be the claim
 * "you can check this yourself" made by a component that did not.
 */

import { Fragment, type ReactNode } from "react";
import { PageHeader } from "./amex";
import { Check, Empty, Panel } from "./ui";
import { ms as fmtMs, shortHash } from "../lib/format";
import type { LocalVerification } from "../lib/witness";
import { KIND_CREDIT, KIND_EARN, KIND_PROTECTION, type BenefitKind, type WitnessVerification } from "../lib/plumbline";

// -------------------------------------------------------------------------- verification

export function VerifyPair({
  server,
  local,
  serverLabel = "Evaluator",
  localLabel = "This console",
}: {
  server: WitnessVerification | null;
  local: LocalVerification | null;
  serverLabel?: string;
  localLabel?: string;
}) {
  return (
    <div className="grid grid-cols-2 gap-2">
      <VerifyBox
        label={serverLabel}
        ok={server?.supports_assertion ?? false}
        pending={server === null}
        note={
          server === null
            ? "…"
            : server.supports_assertion
              ? `witness realises ${server.realized_display}`
              : (server.failures[0]?.code ?? "no supporting witness")
        }
      />
      <VerifyBox
        label={localLabel}
        ok={local?.supportsAssertion ?? false}
        pending={local === null}
        note={
          local === null
            ? "re-adding the numbers…"
            : local.supportsAssertion
              ? `${local.assignments} assignments re-checked in ${fmtMs(local.elapsedMs, 3)} · no solver`
              : (local.failures[0]?.code ?? "does not support the assertion")
        }
      />
    </div>
  );
}

export function VerifyBox({
  label,
  ok,
  note,
  pending,
}: {
  label: ReactNode;
  ok: boolean;
  note: ReactNode;
  pending?: boolean;
}) {
  return (
    <div
      className={`flex min-w-0 flex-col gap-2 rounded-card border bg-white px-4 py-3 ${
        pending ? "border-gray-03" : ok ? "border-success" : "border-warning"
      }`}
    >
      <div className="eyebrow break-words">{label}</div>
      <div className="flex items-center gap-2">
        {!pending && <Check ok={ok} />}
        <span
          className={`num text-data font-bold ${
            pending ? "text-ink-4" : ok ? "text-success" : "text-warning"
          }`}
        >
          {pending ? "…" : ok ? "VERIFIED" : "REJECTED"}
        </span>
      </div>
      <span className="break-words text-code text-ink-4" title={String(note)}>
        {note}
      </span>
    </div>
  );
}

// ------------------------------------------------------------------------------- benefits

const KIND_STYLE: Record<string, string> = {
  [KIND_CREDIT]: "border-blue text-blue",
  [KIND_EARN]: "border-gray-03 text-navy",
  [KIND_PROTECTION]: "border-success text-success",
};

export function KindChip({ kind }: { kind: BenefitKind }) {
  return (
    <span
      className={`inline-block shrink-0 rounded-pill border bg-white px-2.5 py-1 font-mono text-pill tracking-[0.04em] uppercase ${
        KIND_STYLE[kind] ?? "border-gray-03 text-ink-2"
      }`}
    >
      {kind}
    </span>
  );
}

// ---------------------------------------------------------------------------------- misc

export function HashRow({
  label,
  value,
  tone = "proof",
}: {
  label: string;
  value: string;
  tone?: "proof" | "muted";
}) {
  // A 64-character hash rendered in full is four lines of noise nobody reads and the
  // commonest source of text spilling a container. Head and tail identify it; the full
  // value stays on the title attribute for anyone who wants to check one.
  return (
    <div className="flex min-w-0 items-baseline justify-between gap-3">
      <span className="shrink-0 text-label text-blue uppercase">{label}</span>
      <span
        className={`num shrink-0 text-code ${tone === "proof" ? "text-blue" : "text-ink-2"}`}
        title={value}
      >
        {shortHash(value, 8, 6)}
      </span>
    </div>
  );
}

/**
 * Shown when the PLUMBLINE endpoint is not answering. The console never silently substitutes
 * mock data for live data, so this names the endpoint and the switch rather than hiding.
 */
export function PlumblineUnavailable({ error }: { error: string | null }) {
  return (
    <Panel className="h-full" title="Valuation service" tone="deny">
      <Empty>
        <div className="flex max-w-[38rem] flex-col gap-3 text-left">
          <span className="num text-code text-warning">GET /api/plumbline/state</span>
          <span className="text-body text-ink">{error ?? "no valuation state loaded"}</span>
          <span className="text-body text-ink-4">
            The valuation endpoint is landing alongside this console. Its envelope is
            defined in <span className="num text-code">src/lib/plumbline.ts</span> and produced
            from the real backend by{" "}
            <span className="num text-code">scripts/gen_plumbline_fixtures.py</span>. Switch the
            header badge to MOCK to drive that recorded corpus instead. It carries real
            allocator output, not invented JSON.
          </span>
        </div>
      </Empty>
    </Panel>
  );
}

export function ScreenHeader({
  beat,
  title,
  children,
  right,
}: {
  beat: string;
  title: ReactNode;
  children?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <PageHeader
      title={
        <span className="flex items-center gap-3">
          <span className="num flex h-9 w-9 shrink-0 items-center justify-center rounded-pill bg-navy text-pill text-white">
            {beat}
          </span>
          <span className="min-w-0">{title}</span>
        </span>
      }
      standfirst={children}
      right={right}
    />
  );
}

// ======================================================================================
// The beat scaffold, from the Claude Design handoff (design_handoff_plumbline_console).
// Every screen renders the same shape — beat chip, large title, one-sentence standfirst,
// content, a "What this shows" panel, a navy takeaway bar — so a judge learns the page
// once. Panels are square; pills and chips are the only rounded things on a screen.
// ======================================================================================

/** The screen wrapper: fade-in, 34px rhythm, capped at 1280px, above the security field. */
export function BeatPage({
  children,
  gap = "gap-[34px]",
}: {
  children: ReactNode;
  gap?: string;
}) {
  return (
    <div className={`anim-screen relative z-[1] flex max-w-[1280px] flex-col ${gap}`}>
      {children}
    </div>
  );
}

/** Beat chip + mono eyebrow, 42px title, standfirst. `meta` rides right of the eyebrow. */
export function BeatHeader({
  beat,
  label,
  title,
  meta,
  children,
}: {
  beat: string;
  label: string;
  title: ReactNode;
  meta?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="flex flex-col gap-[15px]">
      <div className="flex items-center gap-[13px]">
        <span className="num flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-pill bg-navy text-pill tracking-[0.05em] text-white">
          {beat}
        </span>
        <span className="eyebrow">{label}</span>
        {meta && <span className="num ml-auto text-code text-ink-4">{meta}</span>}
      </div>
      <h1 className="max-w-[22ch] text-page-title text-balance text-navy">{title}</h1>
      {children && (
        <p className="max-w-[70ch] text-standfirst text-pretty text-ink-2">{children}</p>
      )}
    </header>
  );
}

/** Content beside the 344px sidebar. Attribution passes 312px/gap-8 per the handoff. */
export function BeatSplit({
  children,
  aside,
  asideWidth = "344px",
  gap = "gap-9",
}: {
  children: ReactNode;
  aside: ReactNode;
  asideWidth?: string;
  gap?: string;
}) {
  return (
    <div
      className={`grid items-start ${gap}`}
      style={{ gridTemplateColumns: `minmax(0,1fr) ${asideWidth}` }}
    >
      <div className="flex min-w-0 flex-col gap-[34px]">{children}</div>
      <div className="flex min-w-0 flex-col gap-[30px]">{aside}</div>
    </div>
  );
}

/** Navy strip over a #F2F4F6 body: statements separated by hairlines. */
export function ShowsPanel({
  title = "What this shows",
  points,
  footnote,
  children,
}: {
  title?: ReactNode;
  points: ReactNode[];
  footnote?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-col">
      <div className="strip bg-navy px-6 py-[15px] text-white">{title}</div>
      <div className="flex flex-col gap-[22px] bg-field px-6 py-7">
        {points.map((point, i) => (
          <Fragment key={i}>
            {i > 0 && <div className="h-px bg-gray-03" />}
            <p className="text-body text-ink">{point}</p>
          </Fragment>
        ))}
        {children}
      </div>
      {footnote && (
        <div className="border border-t-0 border-gray-03 bg-white px-6 py-[18px] text-card-title font-normal leading-relaxed text-ink-2">
          {footnote}
        </div>
      )}
    </div>
  );
}

/** The navy statement bar closing every screen. One per screen, always last. */
export function Takeaway({ children }: { children: ReactNode }) {
  return (
    <div className="bg-navy px-8 py-[26px] text-[1.1875rem] leading-[1.4] font-bold tracking-[-0.008em] text-white">
      {children}
    </div>
  );
}

/** A square bordered panel: plain gray title row, or a mono strip in navy/blue/deny. */
export function SquarePanel({
  title,
  tone = "plain",
  right,
  children,
  className = "",
}: {
  title: ReactNode;
  tone?: "plain" | "navy" | "blue" | "deny";
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const head =
    tone === "plain"
      ? "border-b border-gray-03 bg-gray-01 text-card-title text-navy"
      : `strip text-white ${tone === "navy" ? "bg-navy" : tone === "blue" ? "bg-blue" : "bg-warning"}`;
  return (
    <div className={`flex flex-col border border-gray-03 bg-white ${className}`}>
      <div className={`flex items-center justify-between gap-4 px-[22px] py-[15px] ${head}`}>
        <span className="min-w-0 break-words">{title}</span>
        {right && <span className="shrink-0">{right}</span>}
      </div>
      <div className="flex min-w-0 flex-col">{children}</div>
    </div>
  );
}

/** A bar-chart row: bold label, mono figure, 10px track, optional mono subline. */
export function BarRow({
  label,
  value,
  pct,
  fill = "bg-navy",
  valueClass = "text-navy",
  sub,
}: {
  label: ReactNode;
  value: ReactNode;
  pct: number;
  fill?: string;
  valueClass?: string;
  sub?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-[9px]">
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-[0.96875rem] font-bold text-navy">{label}</span>
        <span className={`num text-[1.1875rem] font-semibold ${valueClass}`}>{value}</span>
      </div>
      <div className="h-[10px] bg-track">
        <div
          className={`h-full ${fill}`}
          style={{ width: `${Math.max(0, Math.min(100, pct))}%` }}
        />
      </div>
      {sub && <span className="num text-pill font-normal text-ink-4">{sub}</span>}
    </div>
  );
}
