# PLUMBLINE console — design foundation

Everything on screen composes from this file's components. Read it before writing a screen, so
the decisions below are not re-derived per route.

The reference is an internal American Express Technology deck. The test every decision has to
survive: would this sit unremarked inside that deck, and can a judge read it from three metres
away in a lit room?

---

## The system in six sentences

1. **The page is white.** There is no coloured band across the top of a screen. This is the
   single most important structural fact and the thing most likely to be got wrong.
2. **Colour lives in components, never in page chrome.** White page, coloured cards.
3. **Hierarchy is carried by a coloured header bar plus a numbered circle**, not by type size
   alone.
4. **Two blues, doing two jobs.** Navy is primary and structural. Bright blue is secondary and
   alternating.
5. **Pills are the unit of metadata.** Any short fact, count or spec becomes a pill.
6. **Every screen resolves into exactly one navy conclusion band** carrying that screen's single
   most important sentence.

Everything is rounded. Nothing has a shadow, a gradient or a glass effect.

---

## Files

| File | What is in it |
|---|---|
| `src/index.css` | Tokens, the type scale, the reset, the utilities, the no-shadow guard. |
| `src/components/amex.tsx` | The foundation: Card, CardDeck, Pill, SectionLabel, ConclusionBand, PageHeader, TerminalScreen, Wordmark, CopyHash, toast. |
| `src/components/ui.tsx` | Domain vocabulary that predates the foundation, carried onto it: Panel, OutcomeChip, ReasonCode, Money, Stat, Provenance, Seal, SignedRegion, Bar. |
| `src/components/plumblineUi.tsx` | Valuation vocabulary: VerifyPair, KindChip, HashRow, ScreenHeader. |

New screen work composes from `amex.tsx`. `ui.tsx` and `plumblineUi.tsx` are the existing
vocabulary restyled onto the light system; take from them where the domain meaning already
exists, rather than re-inventing a seal or an outcome chip.

---

## Tokens

The canonical block lives at the top of `src/index.css` and is reproduced verbatim from the
brief. Tailwind's theme points at it, so `bg-navy` resolves through `--color-navy` to
`--amex-navy`. Change a token in one place and every utility follows.

```
--amex-navy      #00175A   primary card headers, titles, key data, bands
--amex-blue      #006FCF   alternating card headers, labels, links, focus
--amex-royal     #1E4BA5   full-bleed hero and terminal screens only
--amex-sky       #6BA4E0   accent word on blue fields, sublines in bands
--amex-white     #FFFFFF   page
--amex-gray-01   #F7F8F9   card body
--amex-gray-02   #ECEDEE   SOLVES pill, table zebra, progress track
--amex-gray-03   #C8C9C7   borders, rules, pill outlines
--amex-gray-04   #97999B   disabled only
--amex-cream     #F6DFD2   decorative band on blue fields
--amex-ink       #4D4F53   body copy
--amex-success   #008767   verified, issuer-signed, passed
--amex-warning   #B42C01   struck, blocked, refused, the overstatement
--amex-attention #FDB92D   considered but unpriced
--radius-card    8px
--radius-pill    999px
```

`--amex-royal` and `--amex-cream` are sampled from a photograph and are approximations. They are
single tokens so official values drop in with one edit each.

A second block holds **derived** values. None of them introduces a new hue; each exists so the
palette holds at projector gamma.

```
--amex-ink-strong     #2B2D31   primary sentences and table data
--amex-ink-muted      #75777A   captions and provenance lines, ~4.6:1 on white
--amex-attention-ink  #8A5A00   amber driven down to text contrast
--amex-*-wash                   flat tints for a row highlight, never a gradient
```

**Why `--amex-attention-ink` exists.** `#FDB92D` on white is about 1.7:1, which does not survive
a projector. Amber is therefore carried on the **pill border** and the text is the same hue at a
legible value. `CONSIDERED_BUT_UNPRICED` still reads amber; it also reads.

### Tailwind names

`bg-navy` `bg-blue` `bg-royal` `bg-sky` `bg-cream` `bg-gray-01…04` `text-success` `text-warning`
`text-attention` `text-attention-ink` `rounded-card` `rounded-pill`, and the same names for
`text-`, `border-`, `fill-`.

The surface and ink names the console already spoke (`bg-panel`, `text-ink-3`, `border-line`,
`text-deny`, `text-allow`, `text-proof`) are re-pointed at the light system, so a screen that has
not been rebuilt yet still renders legibly rather than white on white. **New work should use the
palette names, not the legacy aliases.**

---

## Colour semantics, hold absolutely

| Colour | Means | Where |
|---|---|---|
| Navy | Structure and authority | Card headers, page titles, primary numerals, the witness-backed figure, conclusion bands |
| Bright blue | Secondary structure and interaction | Alternating card headers, section labels, links, selected state, focus rings |
| Warning red | **One thing only**: this value was removed, blocked or refused | Struck rows, blocked reason codes, the overstatement delta |
| Success green | Verified or issuer-signed, **at pill and label scale only** | The `VERIFIED` pill, the issuer seal, a passing check |
| Attention amber | `CONSIDERED_BUT_UNPRICED` | A pill beside a candidate the engine saw and declined to price |
| Ink grey | The naive figure | The per-line summation, beside the navy witness figure |

Two rules that are easy to break and expensive to break:

- **Never colour a large currency figure green.** Green money reads as profit and the witness
  figure is not profit. Render it navy with a green `VERIFIED` pill beside it. `Money`'s `allow`
  tone renders navy for exactly this reason.
- **Render the naive figure in ink grey, not red.** It is not an error, it is an inferior method.
  A muted naive figure beside an authoritative navy one argues the point better than two
  competing reds ever will. Red is reserved for the delta, because that number is the amount
  removed, so it earns the colour.

`CONSIDERED_BUT_UNPRICED` is a **distinct third category, not an absence**. It is what answers
"why is AmEx ranked third here?" and it must be visible as its own state, never as a blank.

---

## Type

Two families, used strictly.

- **Text face**: `-apple-system, BlinkMacSystemFont, "Segoe UI", Arial, Helvetica, sans-serif`.
  Every sentence, label, heading, table header and pill.
- **Monospace**: `"JetBrains Mono", "SF Mono", Consolas, monospace`. Only hashes, reason codes,
  identifiers (`in_hotel`, `amex_in_plat:earn`), MCC codes and manifest rule expressions.

Never set an explanatory sentence in monospace. Never set a hash in the text face.

| Utility | Size | Weight | Use |
|---|---|---|---|
| `text-hero` | 3rem | 700 | The three headline currency figures |
| `text-page-title` | 1.75rem | 700 | Screen title, sentence case, navy |
| `text-standfirst` | 1rem | 400 | One grey line under the title |
| `text-card-title` | 1rem | 700 | White, inside a coloured header bar |
| `text-label` | 0.75rem | 700 | UPPERCASE, 0.06em, bright blue, inside cards |
| `text-body` | 0.9375rem | 400 | Paragraphs and bullets, 1.55 line-height |
| `text-pill` | 0.75rem | 700 | Outlined metadata chips |
| `text-data` | 0.9375rem | 500 | Table cells |
| `text-code` | 0.8125rem | 400 | Hashes, reason codes, rules |

### The 0.75rem floor

**Nothing renders below 0.75rem anywhere.** If content will not fit, cut content or widen the
container. Never go below the floor.

The floor is enforced, not requested:

```
npm run typefloor --prefix frontend
```

fails the moment any `text-[…rem]` or `text-[…px]` below 0.75rem / 12px appears in `src`. Run it
after every screen pass. The root font size is 16px so 0.75rem is 12px; the S/M/L lever in the
application bar moves the whole scale together (15 / 16 / 18px) and never lets a size fall
through.

### Utilities

- `.eyebrow` resolves to the section label: 0.75rem, 700, uppercase, 0.06em, bright blue. It is
  the class the console already used in sixty places, so it picks the system up for free.
- `.num` — monospace, tabular figures, slashed zero. Any figure that must line up column to
  column.
- `.money-cell` — `.num` plus right alignment and no wrapping. Currency in a table.
- `.hash` — monospace at code size, bright blue, wraps anywhere.

---

## Components

### `Card`

```tsx
<Card number={1} title="Witness allocation" tone="primary" solves="double counting">
  <Section label="Use case">…</Section>
</Card>
```

- Header bar: full card width, 56px (`h-14`), rounded top corners matching the card radius, solid
  navy (`primary`) or bright blue (`secondary`).
- Inside the bar at the left, a **white filled circle** holding the card number **in the header's
  own colour**, then the card title in bold white.
- Body: `gray-01` fill, 1px `gray-03` border, 8px radius.
- Optional `SOLVES:` pill at the top of the body: `gray-02` fill, fully rounded, small bold blue.
  Filled, unlike a metadata pill, because it is a caption on the card rather than a fact in it.

Props: `number`, `title`, `tone`, `solves`, `right`, `children`.

### The alternation rule

**When cards appear as siblings they alternate.** A row of four goes navy, blue, navy, blue.

Do not hand-set `tone` at a call site. Pass a list to `CardDeck`, which applies the alternation
and the numbering for you:

```tsx
<CardDeck
  columns={2}
  cards={[
    { key: "a", title: "The naive sum", solves: "…", children: <>…</> },
    { key: "b", title: "The witness",   solves: "…", children: <>…</> },
  ]}
/>
```

`CardDeck` props: `cards`, `columns` (1–4), `startAt` (first number, default 1), `startTone`
(default `primary`), so a second deck on the same screen carries the alternation on from where
the first one stopped.

For a layout a grid cannot express, use `toneAt(index, startTone)` and render `Card` directly.
That helper is the only place the rule is written down.

### `Pill` and `PillRow`

White fill, 1px `gray-03` border, fully rounded, 0.75rem bold, 8px/14px padding.

Variants recolour **border and text only, never the fill**: `default` navy, `success` green,
`warning` red, `attention` amber. A row of pills should read as one row of pills, not as four
competing blocks of colour.

`mono` switches the face for identifiers, reason codes and MCC codes. Never for a sentence.

Any short fact, count or spec is a pill. Latency, an MCC, a benefit count, an instrument id, a
`VERIFIED` stamp, a spec in a `TECH SPECS` row.

### `SectionLabel` and `Section`

0.75rem, 700, uppercase, 0.06em, bright blue. `USE CASE`, `APPROACH`, `TECH SPECS`, `IMPACT`.

This is the connective tissue inside every card. A card with no section labels is a card with no
rhythm. `Section` bundles the label with the block it introduces, which is the commonest shape.

### `ConclusionBand`

```tsx
<ConclusionBand
  number={3}
  title="The witness is the derivation"
  subline="SOLVES: an unverifiable total"
  emphasis="Re-add the column. No solver, linear time, anyone can run it."
>
  The engine asserts a value only if it can exhibit an allocation realising it.
</ConclusionBand>
```

Full width, navy, 8px radius. Left: optional white numbered circle, bold white title, optional
light-blue subline. A 1px light-blue vertical rule. Right: white body copy plus one optional
light-blue emphasis line carrying the punchline.

**Every screen ends with exactly one of these.** Two on a screen means neither is the conclusion.
None means the screen never reached one.

### `PageHeader`

Navy title top-left in **sentence case**, one grey standfirst line beneath. No coloured band, no
rule, no eyebrow above the title.

`ScreenHeader` in `plumblineUi.tsx` wraps it and prefixes the demo beat as a navy numbered
circle, which is the same device as a card numeral.

### The application bar and the clear zone

The top bar is **white with navy text and a bottom hairline**. It is not a coloured band. It
carries the wordmark, the log-root and ledger metadata as pills, the S/M/L lever, the transport
badge, and then the clear zone.

`LogoClearZone` reserves **240 x 48 and stays empty**. The console never draws the Blue Box. It
is rendered once by the application bar, which is present on every route, and again by
`TerminalScreen`, which is full-bleed and carries no bar. Do not put anything in it, and do not
let a screen's `right` slot grow into it.

### `TerminalScreen`, `Wordmark`, `SplitHeadline`, `EngravedBand`

Full-bleed **royal** blue, brighter and more saturated than the navy in a card header. Two
distinct blues exist and they do different jobs: navy is structure inside a page, royal is the
page. Reserved for terminal moments, the opening and the close.

Wordmark centred with the colour split: **PLUMB** white, **LINE** sky. On white the same device
is navy and bright blue. `SplitHeadline` generalises it for a closing line.

Across the bottom third, `EngravedBand`: a cream ground carrying blue line art. Flowing curved
strokes, concentric arcs and dense parallel rules, drawn as one weave so it reads as engraved
paper rather than as an illustration. Inline SVG, no asset.

`TerminalScreen` takes a `band` slot, so the engraving can be swapped without touching the
layout. See the open question at the end of this file.

### `CopyHash` and `toast`

Hashes truncate with a **middle** ellipsis and are click-to-copy with a toast. The full value
always reaches the clipboard: a truncated hash a counterparty cannot recover is a decoration, not
evidence. `ToastHost` is mounted once at the application root.

---

## Numeric and copy rules

- Currency: symbol, thousands separators, right-aligned, monospace tabular. Use `.money-cell` in
  a table.
- **One deliberate exception to "exactly two decimals".** `format.money` is a direct port of the
  kernel's `fmt_money` and the smoke suite compares every rendered figure against the string the
  kernel wrote. Whole amounts therefore print without a decimal part, as `₹1,234`. Changing that
  would put the console into disagreement with a signed artifact, which is a worse failure than
  an inconsistent decimal. Do not "fix" it in the presentation layer.
- Latency always carries its unit: `1.83 ms`.
- Every figure carries a provenance caption at code size: measured here, published benchmark, not
  run here, issuer-signed fact, derived by us. `Provenance` in `ui.tsx` does this.
- **No em dashes in interface copy.** Use a full stop and a new sentence, or a comma. Code
  comments are not interface copy.
- Refusal is never styled as an error. A refusal is the system working.

---

## The signature boundary

The narrowness of what an issuer signs is load-bearing, so it is a visual primitive rather than a
caption. `SignedRegion` and `Seal` in `ui.tsx`:

- **Issuer-signed facts** (earn rates, balances, caps, protections): solid border, green left
  rule, green `issuer-signed` seal at pill scale.
- **Everything the cardholder or the agent owns** (the valuation policy, the ranking, any
  comparison between instruments): dashed grey border, grey rule, and it says
  `not issuer-signed`.

No screen may render the two the same way.

---

## What was removed

- The near-black background and the dark palette. Light is what loads.
- Every shadow, gradient and glass effect. `index.css` carries a base-layer guard that cancels
  `box-shadow`, `text-shadow` and `backdrop-filter` globally, so a reintroduced shadow anywhere
  in the codebase has no effect. Focus is an `outline`, so nothing legitimate is lost.
- The display width axis. The text face is a neutral grotesque with no width axis, so
  `display-wide`, `display-xwide` and `display-narrow` are inert. They are kept, not deleted, so
  the class names in screens that have not been rebuilt do nothing rather than something
  off-system. Delete them from a screen when you rebuild it.
- The pulsing ring on the kill switch, which was an animated glow.

---

## Open items for the screen pass

1. **Em dashes remain in screen copy.** They were removed from the foundation files. Sweep
   `src/screens/` as each screen is rebuilt.
2. **`text-[0.76rem]` … `text-[0.95rem]` remain in screens.** All are above the floor, so
   `typefloor` passes, but they should resolve to `text-code`, `text-body` or `text-data` as each
   screen is rebuilt.
3. **`CONSIDERED_BUT_UNPRICED` has no renderer yet.** The colour and the pill variant exist
   (`<Pill variant="attention">`); the state itself is still missing from the data surface on
   screen. It is a real gap and it is what answers "why is AmEx ranked third here?".
4. **Two engraved-pattern components exist.** `EngravedBand` in `amex.tsx` (self-contained,
   the `TerminalScreen` default) and `WorldServicePattern.tsx` (a richer tiling guilloche, not
   yet imported anywhere). They should be consolidated onto one. `TerminalScreen`'s `band` prop
   exists so the swap is one line.
