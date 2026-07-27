/**
 * Console primitives that predate the design foundation, carried onto it.
 *
 * The foundation itself lives in `./amex.tsx`: cards, pills, section labels, the conclusion
 * band, the page header, the terminal treatment. New screen work composes from there. What
 * remains here is the domain vocabulary the kernel screens already speak, restyled onto the
 * light system so nothing renders white on white.
 *
 * Two rules everything here enforces:
 *   - money is monospace, tabular and right-aligned, always;
 *   - warning red appears only where a value was removed, blocked or refused.
 */

import type { ReactNode } from "react";
import { money, ms as fmtMs } from "../lib/format";
import type { Outcome } from "../lib/types";

// ---------------------------------------------------------------------------- surfaces

export function Panel({
  title,
  hint,
  right,
  children,
  className = "",
  bodyClassName = "",
  tone = "default",
}: {
  title?: ReactNode;
  hint?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  tone?: "default" | "deny" | "proof";
}) {
  // The filled header is the deck's card language: navy is structure, bright blue is
  // proof-adjacent, warning red is a refusal. The fill carries the tone, so the border
  // stays gray on every card. `panel-head` remaps ink utilities inside the header to
  // on-dark values (index.css) so existing right-slot content stays legible.
  const fill =
    tone === "deny" ? "bg-warning" : tone === "proof" ? "bg-blue" : "bg-navy";
  return (
    <section
      className={`flex min-h-0 min-w-0 flex-col overflow-hidden rounded-card border border-gray-03 bg-gray-01 ${className}`}
    >
      {(title || right) && (
        <header
          className={`panel-head flex shrink-0 items-center justify-between gap-3 px-4 py-2.5 ${fill}`}
        >
          <div className="flex min-w-0 items-baseline gap-2.5">
            {title && (
              <h2 className="text-pill break-words tracking-[0.06em] text-white uppercase">
                {title}
              </h2>
            )}
            {hint && <span className="text-code break-words text-white/75">{hint}</span>}
          </div>
          {right && <div className="shrink-0">{right}</div>}
        </header>
      )}
      <div className={`min-h-0 flex-1 ${bodyClassName}`}>{children}</div>
    </section>
  );
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return <div className="eyebrow">{children}</div>;
}

export function Divider() {
  return <div className="h-px bg-gray-03" />;
}

// ---------------------------------------------------------------------------- outcomes

/* Outcomes are pills: white fill, the variant on the border and the text. ALLOW is a pass,
   so green; STEP_UP is considered but not yet resolved, so amber; DENY removed the value,
   which is the one thing red means. */
const OUTCOME_STYLES: Record<Outcome, string> = {
  ALLOW: "text-success border-success",
  STEP_UP: "text-attention-ink border-attention",
  DENY: "text-warning border-warning",
};

export function OutcomeChip({
  outcome,
  size = "sm",
}: {
  outcome: Outcome;
  size?: "sm" | "lg";
}) {
  const dims = size === "lg" ? "px-4 py-2.5" : "px-3 py-1.5";
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-pill border bg-white font-mono text-pill tracking-[0.06em] uppercase ${dims} ${OUTCOME_STYLES[outcome]}`}
    >
      {outcome}
    </span>
  );
}

export function ReasonCode({
  code,
  tone = "deny",
}: {
  code: string;
  tone?: "deny" | "muted" | "stepup";
}) {
  const styles =
    tone === "deny"
      ? "border-warning text-warning"
      : tone === "stepup"
        ? "border-attention text-attention-ink"
        : "border-gray-03 text-navy";
  return (
    <span
      className={`inline-block rounded-pill border bg-white px-3 py-1.5 font-mono text-pill ${styles}`}
    >
      {code}
    </span>
  );
}

export function Verdict({ verdict, liable }: { verdict: string; liable: string }) {
  const strong = verdict !== "WITHIN_MANDATE";
  return (
    <div className="flex flex-col gap-1.5">
      <div
        className={`font-mono text-code font-medium tracking-[0.06em] ${
          strong ? "text-warning" : "text-success"
        }`}
      >
        {verdict}
      </div>
      <div className="text-code text-ink-4">
        liable: <span className="text-ink-2">{liable}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------- numbers

/**
 * `allow` renders navy, not green. Green money reads as profit and the witness-backed figure
 * is not profit; a verified figure is navy with a green VERIFIED pill beside it. `muted` is
 * the naive figure: ink grey, because it is an inferior method rather than an error, and a
 * muted naive figure next to an authoritative navy one argues the point better than two
 * competing reds. Red stays for the amount that was removed.
 */
export function Money({
  minorUnits,
  className = "",
  tone = "default",
}: {
  minorUnits: number;
  className?: string;
  tone?: "default" | "deny" | "allow" | "muted";
}) {
  const color =
    tone === "deny"
      ? "text-warning"
      : tone === "allow"
        ? "text-navy"
        : tone === "muted"
          ? "text-ink-4"
          : "text-ink";
  return (
    <span className={`num tabular-nums ${color} ${className}`}>{money(minorUnits)}</span>
  );
}

export function Stat({
  label,
  value,
  sub,
  tone = "default",
  size = "md",
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "default" | "deny" | "allow" | "proof" | "stepup";
  size?: "sm" | "md" | "lg";
}) {
  const color = {
    default: "text-navy",
    deny: "text-warning",
    allow: "text-navy",
    proof: "text-blue",
    stepup: "text-attention-ink",
  }[tone];
  const dims = { sm: "text-data", md: "text-page-title", lg: "text-hero" }[size];
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <div className="eyebrow">{label}</div>
      <div className={`num leading-none font-bold ${dims} ${color}`}>{value}</div>
      {sub && <div className="text-code text-ink-4">{sub}</div>}
    </div>
  );
}

export function Latency({ value }: { value: number }) {
  return <span className="num text-code text-ink-4">{fmtMs(value)}</span>;
}

// ---------------------------------------------------------------------------- controls

export function Button({
  children,
  onClick,
  disabled,
  tone = "default",
  size = "md",
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  tone?: "default" | "primary" | "deny" | "ghost";
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  const tones = {
    default: "border-gray-03 bg-white text-navy hover:border-blue hover:text-blue",
    primary: "border-navy bg-navy text-white hover:border-blue hover:bg-blue",
    deny: "border-warning bg-white text-warning hover:bg-warning hover:text-white",
    ghost: "border-transparent bg-transparent text-ink-4 hover:text-blue",
  }[tone];
  const dims = {
    sm: "px-3 py-1.5 text-pill",
    md: "px-3.5 py-2 text-pill",
    lg: "px-5 py-3 text-card-title",
  }[size];
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-2 rounded-pill border transition-colors duration-150 select-none disabled:cursor-not-allowed disabled:border-gray-03 disabled:bg-white disabled:text-gray-04 ${tones} ${dims} ${className}`}
    >
      {children}
    </button>
  );
}

export function Toggle({
  options,
  value,
  onChange,
}: {
  options: { value: string; label: ReactNode }[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="inline-flex rounded-pill border border-gray-03 bg-white p-1">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={`rounded-pill px-3 py-1.5 text-pill tracking-[0.06em] uppercase transition-colors ${
            option.value === value ? "bg-blue text-white" : "text-ink-4 hover:text-blue"
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------- hashes

export function Hash({
  value,
  label,
  chars = 16,
}: {
  value: string | null | undefined;
  label?: string;
  chars?: number;
}) {
  const text = value ?? "none";
  return (
    <span className="inline-flex items-baseline gap-1.5" title={text}>
      {label && <span className="text-label text-blue uppercase">{label}</span>}
      <span className="hash">
        {text.length > chars ? `${text.slice(0, chars)}…` : text}
      </span>
    </span>
  );
}

export function Check({ ok, size = 14 }: { ok: boolean; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      className={ok ? "text-success" : "text-warning"}
      aria-hidden
    >
      <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.4" opacity="0.5" />
      {ok ? (
        <path
          d="M4.6 8.3l2.2 2.2 4.6-4.9"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ) : (
        <path
          d="M5.4 5.4l5.2 5.2M10.6 5.4l-5.2 5.2"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
        />
      )}
    </svg>
  );
}

/** Disclosure of a real limitation. Used wherever a claim needs a bound attached. */
export function Caveat({ children }: { children: ReactNode }) {
  return (
    <p className="border-l-2 border-gray-03 pl-3 text-code leading-relaxed text-ink-4">
      {children}
    </p>
  );
}

// ------------------------------------------------------------------- the signing boundary
//
// The narrowness of what an issuer signs is a load-bearing part of the argument, so it is
// a visual primitive rather than a caption. Issuer-signed facts get a solid rule and a green
// seal, at pill scale, because green here means exactly "verified or issuer-signed".
// Anything the cardholder or the agent owns, meaning the valuation policy, the ranking and
// any comparison between instruments, gets a dashed grey rule and says so. No screen may
// render the two the same way.

export function Seal({ signed, keyId }: { signed: boolean; keyId?: string | null }) {
  if (!signed) {
    return (
      <span className="inline-flex shrink-0 items-center gap-1 rounded-pill border border-dashed border-gray-03 bg-white px-3 py-1.5 font-mono text-pill tracking-[0.04em] text-ink-4 uppercase">
        not issuer-signed
      </span>
    );
  }
  return (
    <span className="inline-flex shrink-0 items-center gap-1.5 rounded-pill border border-success bg-white px-3 py-1.5 font-mono text-pill tracking-[0.04em] text-success uppercase">
      <svg width="11" height="11" viewBox="0 0 16 16" fill="none" aria-hidden>
        <circle cx="8" cy="8" r="6.4" stroke="currentColor" strokeWidth="1.6" />
        <path
          d="M5.2 8.2l2 2 3.6-4"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      issuer-signed
      {keyId && <span className="text-ink-4">· {keyId}</span>}
    </span>
  );
}

/**
 * A titled region on one side of the signing boundary. `owner` names who is accountable
 * for everything inside it, which is the whole reason the region exists.
 */
export function SignedRegion({
  signed,
  title,
  owner,
  note,
  right,
  children,
  className = "",
}: {
  signed: boolean;
  title: ReactNode;
  owner: string;
  note?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`flex min-h-0 min-w-0 flex-col rounded-card border bg-gray-01 ${
        signed
          ? "border-gray-03 border-l-[3px] border-l-success"
          : "border-dashed border-gray-03 border-l-[3px] border-l-gray-04"
      } ${className}`}
    >
      <header className="flex shrink-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1.5 border-b border-gray-03 px-4 py-3">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <h2 className={`eyebrow ${signed ? "text-success" : "text-ink-4"}`}>{title}</h2>
          <span className="num text-code text-ink-4">{owner}</span>
          {note && <span className="text-code text-ink-4">{note}</span>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {right}
          <Seal signed={signed} />
        </div>
      </header>
      <div className="min-h-0 flex-1">{children}</div>
    </section>
  );
}

/** The rule between the two regions. Named, because an unnamed line is decoration. */
export function BoundaryRule({ children }: { children: ReactNode }) {
  return (
    <div className="flex shrink-0 items-center gap-3 py-1">
      <span className="h-px flex-1 bg-gray-03" />
      <span className="eyebrow shrink-0">{children}</span>
      <span className="h-px flex-1 bg-gray-03" />
    </div>
  );
}

// ----------------------------------------------------------------------------- provenance

/**
 * A figure with the conditions it was measured under attached to it. Every latency number
 * in this console renders through here: in a system whose thesis is that agents must show
 * their work, an unlabelled figure is a thematic self-refutation.
 */
export function Provenance({
  label,
  value,
  method,
  tone = "default",
  size = "md",
}: {
  label: ReactNode;
  value: ReactNode;
  method: ReactNode;
  tone?: "default" | "allow" | "deny" | "proof";
  size?: "sm" | "md" | "lg";
}) {
  const color = {
    default: "text-navy",
    allow: "text-navy",
    deny: "text-warning",
    proof: "text-blue",
  }[tone];
  const dims = { sm: "text-data", md: "text-card-title", lg: "text-page-title" }[size];
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <div className="eyebrow">{label}</div>
      <div className={`num leading-none font-bold ${dims} ${color}`}>{value}</div>
      <div className="text-code text-ink-4">{method}</div>
    </div>
  );
}

/** A horizontal bar for a share of a total. No axis, no legend — it labels itself. */
export function Bar({
  value,
  total,
  tone = "default",
}: {
  value: number;
  total: number;
  tone?: "default" | "deny" | "allow" | "proof";
}) {
  const pctWidth = total <= 0 ? 0 : Math.max(0, Math.min(100, (value / total) * 100));
  const fill = {
    default: "bg-navy",
    deny: "bg-warning",
    allow: "bg-success",
    proof: "bg-blue",
  }[tone];
  return (
    <div className="h-2 w-full overflow-hidden rounded-pill bg-gray-02">
      <div className={`h-full rounded-pill ${fill}`} style={{ width: `${pctWidth}%` }} />
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center p-8 text-center text-body text-ink-4">
      {children}
    </div>
  );
}
