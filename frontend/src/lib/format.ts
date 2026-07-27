/**
 * Presentation helpers. Nothing here participates in a decision.
 *
 * `money` is a direct port of `constraints.fmt_money` — Indian digit grouping over
 * integer minor units — so the console never disagrees with a number the kernel printed.
 */

export function money(minorUnits: number, symbol = "₹"): string {
  const negative = minorUnits < 0;
  const abs = Math.abs(minorUnits);
  const whole = Math.floor(abs / 100);
  const frac = abs % 100;

  let s = String(whole);
  if (s.length > 3) {
    let head = s.slice(0, -3);
    const tail = s.slice(-3);
    const groups: string[] = [];
    while (head.length > 2) {
      groups.unshift(head.slice(-2));
      head = head.slice(0, -2);
    }
    if (head) groups.unshift(head);
    s = [...groups, tail].join(",");
  }
  const out = frac ? `${symbol}${s}.${String(frac).padStart(2, "0")}` : `${symbol}${s}`;
  return negative ? `-${out}` : out;
}

/**
 * The symbols the kernel declares, mirrored from `caveat.money.CURRENCY_SYMBOLS`. Digit
 * grouping is the same function for both, exactly as it is on the Python side: the two
 * conventions agree below one hundred thousand major units, which covers every figure in
 * this repo's demo carts.
 */
export const CURRENCY_SYMBOLS: Record<string, string> = { INR: "₹", USD: "$" };

/**
 * Money under the symbol its currency code names.
 *
 * An unknown code renders as `USD-style digits prefixed by the code` rather than falling
 * back to a rupee sign. A figure under a guessed sign is worse than no figure: every digit
 * is right and the currency is wrong, and nothing on screen says so.
 */
export function currencyMoney(minorUnits: number, currency: string): string {
  const symbol = CURRENCY_SYMBOLS[currency];
  return symbol ? money(minorUnits, symbol) : `${currency} ${money(minorUnits, "")}`;
}

/** Money with an explicit sign, for deltas. */
export function signedMoney(minorUnits: number): string {
  if (minorUnits === 0) return money(0);
  return minorUnits > 0 ? `+${money(minorUnits)}` : money(minorUnits);
}

/** Compact money for axis labels: ₹1.9L, ₹55K. Never used where an exact figure matters. */
export function moneyCompact(minorUnits: number): string {
  const rupees = Math.round(minorUnits / 100);
  const abs = Math.abs(rupees);
  if (abs >= 10_000_000) return `₹${(rupees / 10_000_000).toFixed(1)}Cr`;
  if (abs >= 100_000) return `₹${(rupees / 100_000).toFixed(1)}L`;
  if (abs >= 1_000) return `₹${Math.round(rupees / 1_000)}K`;
  return `₹${rupees}`;
}

export function ms(value: number, digits = 2): string {
  if (value < 1) return `${value.toFixed(digits)} ms`;
  if (value < 100) return `${value.toFixed(digits)} ms`;
  return `${value.toFixed(0)} ms`;
}

export function shortHash(hex: string | null | undefined, head = 8, tail = 6): string {
  if (!hex) return "none";
  if (hex.length <= head + tail + 1) return hex;
  return `${hex.slice(0, head)}…${hex.slice(-tail)}`;
}

export function clockTime(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleTimeString("en-GB", { hour12: false });
}

export function bps(value: number): string {
  return `${value} bps`;
}

export function pct(numerator: number, denominator: number): string {
  if (!denominator) return "0%";
  return `${((numerator / denominator) * 100).toFixed(1)}%`;
}

export function humanReason(code: string): string {
  return code.replaceAll("_", " ").toLowerCase();
}

/**
 * First sentence of a note, for grids that quote published card terms. The fixture notes
 * run 100-480 characters because they are the terms as the engine models them; on screen
 * the first sentence identifies the benefit and the full text belongs on the title
 * attribute, not in the layout.
 */
export function firstSentence(note: string): string {
  const match = note.match(/^.*?[.!?](?=\s|$)/);
  return match ? match[0] : note;
}
