/**
 * Vocabulary shared by the five PLUMBLINE screens.
 *
 * One rule enforced here: wherever a verification result appears, the server's verdict and
 * the console's own recomputation appear together. A single green tick would be the claim
 * "you can check this yourself" made by a component that did not.
 */

import type { ReactNode } from "react";
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
