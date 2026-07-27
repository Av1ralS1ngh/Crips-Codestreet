/**
 * The design foundation. Every screen composes from what is in this file.
 *
 * The system in one paragraph: the page is white and colour lives in components. A card
 * carries a solid navy or bright-blue header bar with a white numbered circle in it, and a
 * very light grey body under a thin grey border. Siblings alternate navy, blue, navy, blue.
 * Short facts are pills. Small bright-blue uppercase labels are the connective tissue inside
 * a card. Every screen resolves into exactly one navy conclusion band carrying that screen's
 * single most important sentence. Full-bleed royal blue with the cream engraved band is
 * reserved for terminal moments. Nothing has a sharp corner, a shadow or a gradient.
 *
 * Colour is semantic and the semantics are not negotiable:
 *   navy      structure and authority. Card headers, page titles, primary numerals, the
 *             witness-backed figure, conclusion bands.
 *   blue      secondary structure and interaction. Alternating headers, section labels,
 *             links, selected state, focus rings.
 *   green     verified or issuer-signed, at pill and label scale only. Never a large
 *             currency figure: green money reads as profit and the witness figure is not
 *             profit. Render the figure navy with a green VERIFIED pill beside it.
 *   red       one meaning only. This value was removed, blocked or refused. Struck rows,
 *             blocked reason codes, the overstatement delta.
 *   amber     CONSIDERED_BUT_UNPRICED. A distinct third category, not an absence.
 *   ink grey  the naive figure. It is not an error, it is an inferior method, and a muted
 *             naive figure beside an authoritative navy witness figure argues the point
 *             better than two competing reds ever will.
 */

import { useEffect, useState, type ReactNode } from "react";

// ------------------------------------------------------------------------------ the card

export type CardTone = "primary" | "secondary";

const HEADER_FILL: Record<CardTone, string> = {
  primary: "bg-navy",
  secondary: "bg-blue",
};

/** The numeral inside the white circle is the header's own colour. */
const HEADER_INK: Record<CardTone, string> = {
  primary: "text-navy",
  secondary: "text-blue",
};

/**
 * `number` is rendered inside a white filled circle in the header bar. Pass `null` for a
 * card that carries no position in a sequence; the title then starts at the left edge.
 */
export function Card({
  number,
  title,
  tone = "primary",
  solves,
  right,
  children,
  className = "",
  bodyClassName = "",
}: {
  number?: ReactNode;
  title: ReactNode;
  tone?: CardTone;
  solves?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section
      className={`flex min-h-0 min-w-0 flex-col overflow-hidden rounded-card border border-gray-03 bg-gray-01 ${className}`}
    >
      <header className={`flex h-14 shrink-0 items-center gap-3 px-4 ${HEADER_FILL[tone]}`}>
        {number !== undefined && number !== null && (
          <span
            className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-pill bg-white text-pill ${HEADER_INK[tone]}`}
          >
            {number}
          </span>
        )}
        <h2 className="min-w-0 flex-1 break-words text-card-title text-white">{title}</h2>
        {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
      </header>
      <div className={`flex min-h-0 flex-1 flex-col gap-4 p-5 ${bodyClassName}`}>
        {solves && <Solves>{solves}</Solves>}
        {children}
      </div>
    </section>
  );
}

/**
 * The chip at the top of a card body naming what the card is for. Filled, unlike a metadata
 * pill: it is a caption on the card rather than a fact inside it.
 */
export function Solves({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex w-fit items-baseline gap-1.5 rounded-pill bg-gray-02 px-3.5 py-2 text-pill text-blue">
      <span className="tracking-[0.06em] uppercase">Solves:</span>
      <span>{children}</span>
    </span>
  );
}

/**
 * The alternation rule, in one place. Siblings alternate; a row of four goes navy, blue,
 * navy, blue. Call this rather than hand-setting a tone, so no call site can quietly break
 * the rhythm. `startTone` lets a second group on the same screen carry the alternation on
 * from where the first one stopped.
 */
export function toneAt(index: number, startTone: CardTone = "primary"): CardTone {
  const even = index % 2 === 0;
  if (startTone === "primary") return even ? "primary" : "secondary";
  return even ? "secondary" : "primary";
}

export interface CardSpec {
  /** Stable list key. Falls back to the index. */
  key?: string;
  /** Omit to auto-number from `startAt`. Pass `null` for an unnumbered card. */
  number?: ReactNode;
  title: ReactNode;
  solves?: ReactNode;
  right?: ReactNode;
  className?: string;
  bodyClassName?: string;
  children: ReactNode;
}

const DECK_COLUMNS: Record<number, string> = {
  1: "grid-cols-1",
  2: "grid-cols-2",
  3: "grid-cols-3",
  4: "grid-cols-4",
};

/**
 * A row of cards with the tone alternation and the numbering applied for you. This is the
 * intended way to render siblings; `Card` on its own is for the case where a screen needs
 * one card in a layout a grid cannot express.
 */
export function CardDeck({
  cards,
  columns = 2,
  startAt = 1,
  startTone = "primary",
  className = "",
}: {
  cards: CardSpec[];
  columns?: 1 | 2 | 3 | 4;
  startAt?: number;
  startTone?: CardTone;
  className?: string;
}) {
  return (
    <div className={`grid min-h-0 gap-4 ${DECK_COLUMNS[columns]} ${className}`}>
      {cards.map((card, index) => (
        <Card
          key={card.key ?? index}
          number={card.number === undefined ? startAt + index : card.number}
          title={card.title}
          tone={toneAt(index, startTone)}
          solves={card.solves}
          right={card.right}
          className={card.className}
          bodyClassName={card.bodyClassName}
        >
          {card.children}
        </Card>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------------------ the pill

export type PillVariant = "default" | "success" | "warning" | "attention";

/**
 * Variants recolour the border and the text. The fill is always white, so a row of pills
 * reads as one row of pills rather than four competing blocks of colour.
 *
 * `attention` carries the amber token on the border and a driven-down amber on the text.
 * Amber at its brand value is about 1.7:1 on white, which does not survive a projector, and
 * the floor rule is a floor: legibility wins and the hue is preserved.
 */
const PILL_VARIANT: Record<PillVariant, string> = {
  default: "border-gray-03 text-navy",
  success: "border-success text-success",
  warning: "border-warning text-warning",
  attention: "border-attention text-attention-ink",
};

/** Any short fact, count or spec. Pills are the unit of metadata in this system. */
export function Pill({
  children,
  variant = "default",
  mono = false,
  title,
  className = "",
}: {
  children: ReactNode;
  variant?: PillVariant;
  /** Set for identifiers, reason codes and MCC codes. Never for a sentence. */
  mono?: boolean;
  title?: string;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex w-fit shrink-0 items-center gap-1.5 rounded-pill border bg-white px-3.5 py-2 text-pill whitespace-nowrap ${
        mono ? "font-mono" : ""
      } ${PILL_VARIANT[variant]} ${className}`}
    >
      {children}
    </span>
  );
}

/** A horizontal run of pills. Wraps rather than scrolls, so nothing is hidden off-edge. */
export function PillRow({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={`flex flex-wrap items-center gap-2 ${className}`}>{children}</div>;
}

// --------------------------------------------------------------------- the section label

/**
 * The connective tissue inside every card: USE CASE, APPROACH, TECH SPECS, IMPACT. Bright
 * blue, uppercase, 0.75rem. A card with no section labels is a card with no rhythm.
 */
export function SectionLabel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`text-label text-blue uppercase ${className}`}>{children}</div>
  );
}

/** A labelled block: the label, then whatever it introduces. The commonest shape in a card. */
export function Section({
  label,
  children,
  className = "",
}: {
  label: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`flex min-w-0 flex-col gap-2 ${className}`}>
      <SectionLabel>{label}</SectionLabel>
      {children}
    </div>
  );
}

// -------------------------------------------------------------------- the conclusion band

/**
 * The resolution of a screen. Every screen ends with exactly one of these and it carries
 * that screen's single most important sentence. Two on one screen means neither is the
 * conclusion; none means the screen did not reach one.
 */
export function ConclusionBand({
  number,
  title,
  subline,
  emphasis,
  children,
  className = "",
}: {
  number?: ReactNode;
  title: ReactNode;
  subline?: ReactNode;
  /** The punchline. One line, light blue, and it is the last thing read. */
  emphasis?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`flex shrink-0 flex-col gap-4 rounded-card bg-navy px-6 py-5 lg:flex-row lg:items-stretch lg:gap-6 ${className}`}
    >
      <div className="flex min-w-0 shrink-0 items-start gap-3 lg:w-[20rem]">
        {number !== undefined && number !== null && (
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-pill bg-white text-pill text-navy">
            {number}
          </span>
        )}
        <div className="min-w-0">
          <h2 className="text-card-title text-white">{title}</h2>
          {subline && <p className="mt-1.5 text-pill tracking-[0.04em] text-sky">{subline}</p>}
        </div>
      </div>

      <div className="hidden w-px shrink-0 self-stretch bg-sky lg:block" />
      <div className="h-px w-full shrink-0 bg-sky lg:hidden" />

      <div className="flex min-w-0 flex-1 flex-col gap-2">
        {children && <div className="text-body text-white">{children}</div>}
        {emphasis && <p className="text-body font-bold text-sky">{emphasis}</p>}
      </div>
    </section>
  );
}

// -------------------------------------------------------------------------- page furniture

/**
 * The logo lockup clear zone: 240 x 48, reserved and left empty. The console never draws
 * the Blue Box. Rendered once by the application bar, which is present on every route, and
 * again by `TerminalScreen`, which is full-bleed and carries no bar.
 */
export function LogoClearZone({ className = "" }: { className?: string }) {
  // h-12 w-60 is 48 x 240 at the default root size.
  return (
    <div
      data-logo-clear-zone
      aria-hidden
      className={`h-12 w-60 shrink-0 ${className}`}
    />
  );
}

/**
 * The wordmark, with the colour split from the closing slide: PLUMB in the dominant colour,
 * LINE in the accent. On a blue field that is white and sky; on white it is navy and bright
 * blue. The device is the same either way, which is what makes it a wordmark.
 */
export function Wordmark({
  tone = "onWhite",
  size = "bar",
  className = "",
}: {
  tone?: "onWhite" | "onBlue";
  size?: "bar" | "hero";
  className?: string;
}) {
  const lead = tone === "onBlue" ? "text-white" : "text-navy";
  const accent = tone === "onBlue" ? "text-sky" : "text-blue";
  const dims =
    size === "hero" ? "text-hero tracking-[0.04em]" : "text-card-title tracking-[0.16em]";
  return (
    <span className={`font-bold ${dims} ${className}`}>
      <span className={lead}>PLUMB</span>
      <span className={accent}>LINE</span>
    </span>
  );
}

/**
 * A headline split across two colours, the device the closing slide uses on "Thank you".
 * Reserved for terminal screens; a content screen states its title in one navy weight.
 */
export function SplitHeadline({
  lead,
  accent,
  tone = "onBlue",
  className = "",
}: {
  lead: ReactNode;
  accent: ReactNode;
  tone?: "onWhite" | "onBlue";
  className?: string;
}) {
  const leadInk = tone === "onBlue" ? "text-white" : "text-navy";
  const accentInk = tone === "onBlue" ? "text-sky" : "text-blue";
  return (
    <h1 className={`text-hero ${className}`}>
      <span className={leadInk}>{lead}</span> <span className={accentInk}>{accent}</span>
    </h1>
  );
}

/**
 * The screen title block. White page, navy title in sentence case, one grey standfirst line
 * beneath it. There is no coloured band across the top of a screen: that is the single most
 * important structural fact in this system.
 */
export function PageHeader({
  title,
  standfirst,
  right,
  className = "",
}: {
  title: ReactNode;
  standfirst?: ReactNode;
  right?: ReactNode;
  className?: string;
}) {
  return (
    <header
      className={`flex shrink-0 flex-wrap items-start justify-between gap-x-8 gap-y-3 ${className}`}
    >
      <div className="min-w-0 max-w-[64rem]">
        <h1 className="text-page-title text-navy">{title}</h1>
        {standfirst && <p className="mt-2 max-w-[58rem] text-standfirst text-ink-2">{standfirst}</p>}
      </div>
      {right && <div className="flex shrink-0 flex-wrap items-center gap-2">{right}</div>}
    </header>
  );
}

// ---------------------------------------------------------------------- terminal treatment

/**
 * The decorative band on a blue field: cream ground, blue line art. Flowing curved strokes,
 * concentric arcs and dense parallel rules, drawn as one weave rather than three motifs, so
 * it reads as engraved paper and not as an illustration. Inline SVG, so it scales to any
 * projector and needs no asset.
 */
export function EngravedBand({ className = "" }: { className?: string }) {
  const arcsLeft = Array.from({ length: 15 }, (_, i) => 44 + i * 21);
  const arcsRight = Array.from({ length: 11 }, (_, i) => 34 + i * 19);
  const rules = Array.from({ length: 74 }, (_, i) => 566 + i * 7.2);
  const flows = Array.from({ length: 8 }, (_, i) => 62 + i * 34);

  return (
    <div className={`overflow-hidden bg-cream ${className}`} aria-hidden>
      <svg
        viewBox="0 0 1600 400"
        preserveAspectRatio="xMidYMid slice"
        className="h-full w-full"
        role="presentation"
      >
        <g fill="none" strokeLinecap="round">
          {/* concentric arcs, opening upward from the base line */}
          {arcsLeft.map((r, i) => (
            <path
              key={`al${r}`}
              d={`M ${248 - r} 400 A ${r} ${r} 0 0 1 ${248 + r} 400`}
              stroke="var(--amex-navy)"
              strokeWidth={i % 3 === 0 ? 1.6 : 1}
              opacity={0.22 + (i % 4) * 0.14}
            />
          ))}
          {arcsRight.map((r, i) => (
            <path
              key={`ar${r}`}
              d={`M ${1372 - r} 400 A ${r} ${r} 0 0 1 ${1372 + r} 400`}
              stroke="var(--amex-royal)"
              strokeWidth={i % 3 === 0 ? 1.6 : 1}
              opacity={0.24 + (i % 4) * 0.13}
            />
          ))}

          {/* dense parallel rules: the woven ground between the two arc clusters */}
          {rules.map((x, i) => {
            const height = 96 + Math.sin(i / 5.5) * 62 + (i % 3) * 12;
            return (
              <line
                key={`r${x}`}
                x1={x}
                y1={400 - height}
                x2={x}
                y2={400}
                stroke="var(--amex-royal)"
                strokeWidth={i % 6 === 0 ? 1.5 : 0.9}
                opacity={0.26 + (i % 5) * 0.1}
              />
            );
          })}

          {/* flowing curved strokes sweeping the full width, drawn over the ground */}
          {flows.map((y, i) => (
            <path
              key={`f${y}`}
              d={`M -40 ${y + 40} C 300 ${y - 66}, 620 ${y + 116}, 940 ${y + 14} S 1400 ${
                y - 52
              }, 1640 ${y + 58}`}
              stroke={i % 2 === 0 ? "var(--amex-blue)" : "var(--amex-navy)"}
              strokeWidth={i % 2 === 0 ? 1.5 : 1}
              opacity={0.32 + (i % 3) * 0.16}
            />
          ))}
        </g>
      </svg>
    </div>
  );
}

/**
 * A full-bleed royal blue screen. The opening and the close, and any state that is genuinely
 * terminal. Royal is noticeably brighter than the navy in a card header, and the two do
 * different jobs: navy is structure inside a page, royal is the page.
 *
 * Defaults to the wordmark, centred, with the colour split.
 */
export function TerminalScreen({
  headline,
  standfirst,
  children,
  band,
  className = "",
}: {
  headline?: ReactNode;
  standfirst?: ReactNode;
  children?: ReactNode;
  /**
   * The decorative band across the bottom third. Defaults to `EngravedBand`, which is
   * self-contained. Pass another field here to swap the engraving without touching the
   * layout; whatever is passed must fill its own height and carry its own cream ground.
   */
  band?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`flex h-full w-full flex-col overflow-hidden bg-royal ${className}`}>
      <div className="flex shrink-0 justify-end px-8 pt-6">
        <LogoClearZone />
      </div>

      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-6 px-10 text-center">
        {headline ?? <Wordmark tone="onBlue" size="hero" />}
        {standfirst && (
          <p className="max-w-[46rem] text-standfirst text-sky">{standfirst}</p>
        )}
        {children}
      </div>

      <div className="h-[30%] shrink-0">{band ?? <EngravedBand className="h-full" />}</div>
    </div>
  );
}

// ------------------------------------------------------------------------------ the toast

type ToastListener = (message: string) => void;
const toastListeners = new Set<ToastListener>();

/** Fire a transient confirmation. Never used to report a failure. */
export function toast(message: string) {
  for (const listener of toastListeners) listener(message);
}

/** Mounted once, at the application root. */
export function ToastHost() {
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const listener: ToastListener = (next) => {
      setMessage(next);
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => setMessage(null), 1800);
    };
    toastListeners.add(listener);
    return () => {
      toastListeners.delete(listener);
      if (timer) clearTimeout(timer);
    };
  }, []);

  if (!message) return null;
  return (
    <div
      role="status"
      className="pointer-events-none fixed inset-x-0 bottom-8 z-[100] flex justify-center"
    >
      <span className="rounded-pill bg-navy px-4 py-2 text-pill text-white">{message}</span>
    </div>
  );
}

// -------------------------------------------------------------------------------- hashes

/**
 * A hash, truncated with a middle ellipsis and click-to-copy. Never the text face, always
 * the full value on the clipboard: a truncated hash a counterparty cannot recover is a
 * decoration, not evidence.
 */
export function CopyHash({
  value,
  head = 12,
  tail = 8,
  label,
  className = "",
}: {
  value: string | null | undefined;
  head?: number;
  tail?: number;
  label?: ReactNode;
  className?: string;
}) {
  const full = value ?? "";
  const shown =
    full.length === 0
      ? "none"
      : full.length <= head + tail + 1
        ? full
        : `${full.slice(0, head)}…${full.slice(-tail)}`;

  const copy = () => {
    if (!full) return;
    const clipboard = typeof navigator === "undefined" ? undefined : navigator.clipboard;
    void clipboard?.writeText(full);
    toast("Hash copied");
  };

  return (
    <button
      type="button"
      onClick={copy}
      title={full || undefined}
      disabled={!full}
      className={`inline-flex max-w-full items-center gap-2 rounded-pill border border-gray-03 bg-white px-3.5 py-2 text-code text-navy transition-colors hover:border-blue disabled:cursor-default ${className}`}
    >
      {label && <span className="text-pill tracking-[0.06em] text-blue uppercase">{label}</span>}
      <span className="num break-words">{shown}</span>
    </button>
  );
}
