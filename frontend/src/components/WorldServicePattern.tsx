/**
 * Guilloche pattern band, in the manner of engraved security printing.
 *
 * The reference is the American Express "World Service" field: globes drawn as meridian
 * hatching, wavy ribbon banners, spiral flourishes, and dense parallel rules, the whole
 * thing reading as woven or engraved rather than as an illustration.
 *
 * WHAT THIS DELIBERATELY OMITS. The reference field carries the words "AMERICAN EXPRESS"
 * and "WORLD SERVICE" inside its ribbons. This does not, and the omission is the same
 * judgement the design brief already makes about the Blue Box: reserve the clear zone,
 * never draw the mark. Reproducing the geometry places us inside their visual system.
 * Reproducing their wordmark would place their name on our product, which is a different
 * thing and not ours to do. The ribbons here are empty.
 *
 * Everything is inline SVG so the band needs no asset, scales to any width, and tiles
 * exactly. Stroke-only, no fills, so a single colour token drives the whole field.
 */

import { useId } from "react";

export type PatternTone = "onBlue" | "onCream" | "onWhite";

const TONE: Record<PatternTone, { stroke: string; opacity: number; background: string }> = {
  // Terminal screens: the darker engraving sits on the royal field, as on a card back.
  onBlue: { stroke: "#0B2E6F", opacity: 0.55, background: "var(--amex-royal, #1E4BA5)" },
  // The decorative band across the bottom third of a terminal screen.
  onCream: { stroke: "var(--amex-blue, #006FCF)", opacity: 0.42, background: "var(--amex-cream, #F6DFD2)" },
  // A whisper behind a white page. Must stay near-invisible or it competes with data.
  onWhite: { stroke: "var(--amex-navy, #00175A)", opacity: 0.06, background: "transparent" },
};

/** One tile of the field. 200x200, designed to repeat on both axes without a seam. */
function Tile({ stroke }: { stroke: string }) {
  return (
    <g fill="none" stroke={stroke} strokeWidth={1} strokeLinecap="round">
      {/* Dense parallel rules, the ground the rest of the engraving sits on. Each wave
          completes a full period across the tile so the left and right edges meet. */}
      {Array.from({ length: 13 }, (_, i) => {
        const y = 6 + i * 15.5;
        return (
          <path
            key={`rule-${i}`}
            d={`M0 ${y} C 25 ${y - 7}, 75 ${y + 7}, 100 ${y} S 175 ${y - 7}, 200 ${y}`}
            opacity={0.5}
          />
        );
      })}

      {/* Globe: a circle read as a sphere through meridian ellipses and latitude chords.
          Drawn twice per tile, offset, to break the grid the way the reference does. */}
      {[
        { cx: 50, cy: 62 },
        { cx: 150, cy: 148 },
      ].map(({ cx, cy }) => (
        <g key={`globe-${cx}-${cy}`}>
          <circle cx={cx} cy={cy} r={26} />
          {/* meridians, narrowing toward the limb */}
          {[4, 9.5, 15.5, 21].map((rx) => (
            <ellipse key={`m-${rx}`} cx={cx} cy={cy} rx={rx} ry={26} />
          ))}
          <line x1={cx} y1={cy - 26} x2={cx} y2={cy + 26} />
          {/* latitudes, chords of the circle */}
          {[-16, -8, 0, 8, 16].map((dy) => {
            const half = Math.sqrt(Math.max(26 * 26 - dy * dy, 0));
            return (
              <line
                key={`lat-${dy}`}
                x1={cx - half}
                y1={cy + dy}
                x2={cx + half}
                y2={cy + dy}
                opacity={0.75}
              />
            );
          })}
        </g>
      ))}

      {/* Ribbon banners, empty by design. A wavy band with turned-under ends, the shape a
          burin cuts. One crosses each globe so the field reads as one engraving rather
          than as ornaments scattered on a texture. */}
      {[
        { x: 50, y: 62 },
        { x: 150, y: 148 },
      ].map(({ x, y }) => (
        <g key={`ribbon-${x}-${y}`}>
          <path
            d={`M${x - 54} ${y - 6}
                C ${x - 30} ${y - 13}, ${x + 30} ${y + 1}, ${x + 54} ${y - 6}
                L ${x + 54} ${y + 8}
                C ${x + 30} ${y + 15}, ${x - 30} ${y + 1}, ${x - 54} ${y + 8} Z`}
          />
          {/* the inner rule that makes a banner look struck rather than drawn */}
          <path
            d={`M${x - 48} ${y - 2}
                C ${x - 26} ${y - 8}, ${x + 26} ${y + 4}, ${x + 48} ${y - 2}`}
            opacity={0.6}
          />
        </g>
      ))}

      {/* Spiral flourishes at the tile joins. A spiral is drawn as decreasing arcs, which
          is how the engraved original reads under magnification. */}
      {[
        { cx: 150, cy: 40 },
        { cx: 50, cy: 160 },
        { cx: 0, cy: 100 },
        { cx: 200, cy: 100 },
      ].map(({ cx, cy }) => (
        <g key={`spiral-${cx}-${cy}`} opacity={0.85}>
          {[14, 10, 6.5, 3.5].map((r, i) => (
            <path
              key={`arc-${r}`}
              d={`M ${cx - r} ${cy} A ${r} ${r} 0 1 ${i % 2} ${cx + r} ${cy}`}
            />
          ))}
        </g>
      ))}
    </g>
  );
}

/**
 * A repeating guilloche field.
 *
 * `height` is the band height in pixels. Omit it and the field fills its container, which
 * is what a full-bleed terminal screen wants.
 */
export function WorldServicePattern({
  tone = "onCream",
  height,
  className,
  ariaHidden = true,
}: {
  tone?: PatternTone;
  height?: number;
  className?: string;
  ariaHidden?: boolean;
}) {
  // useId keeps the pattern id unique when several fields render on one screen. Two
  // <pattern> elements sharing an id is a silent, maddening bug.
  const id = `ws-${useId().replace(/:/g, "")}`;
  const { stroke, opacity, background } = TONE[tone];

  return (
    <div
      className={className}
      aria-hidden={ariaHidden}
      style={{
        height: height ? `${height}px` : "100%",
        width: "100%",
        background,
        overflow: "hidden",
        pointerEvents: "none",
      }}
    >
      <svg
        width="100%"
        height="100%"
        preserveAspectRatio="xMidYMid slice"
        style={{ display: "block", opacity }}
        role="presentation"
      >
        <defs>
          <pattern id={id} width={200} height={200} patternUnits="userSpaceOnUse">
            <Tile stroke={stroke} />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill={`url(#${id})`} />
      </svg>
    </div>
  );
}

export default WorldServicePattern;
