/**
 * Independent, client-side verification of an allocation witness.
 *
 * This is a direct port of `valorem/witness.py :: verify_witness`, and porting it is the
 * point rather than a convenience. The correctness argument is that an asserted value is
 * sound because a concrete allocation realising it can be exhibited, and that checking
 * such an allocation is linear-time arithmetic — re-add the numbers, check the balances —
 * needing no solver and no trust in the issuer's tooling. A console that displayed the
 * server's `ok` boolean would be asserting that claim rather than demonstrating it.
 *
 * So: the browser re-reads the manifest, re-derives what each assignment should yield, and
 * reports its own verdict beside the server's. Same rule as `merkle.ts`.
 *
 * Deliberately not ported: the allocator. Producing an allocation is a decision, and the
 * decision belongs to the engine. The console only ever checks one it was handed.
 */

import {
  ERR_CAPACITY,
  ERR_CART_MISMATCH,
  ERR_CONSUMPTION_MISMATCH,
  ERR_CREDIT_OVER_LINE,
  ERR_CURRENCY_MISMATCH,
  ERR_DOUBLE_ASSIGNED,
  ERR_DUPLICATE_SKU,
  ERR_EXCLUSIVITY,
  ERR_INELIGIBLE,
  ERR_LINE_OVER_OFFSET,
  ERR_MANIFEST_MISMATCH,
  ERR_NEGATIVE_AMOUNT,
  ERR_OVERSTATED,
  ERR_UNAVAILABLE,
  ERR_UNKNOWN_BENEFIT,
  ERR_UNKNOWN_LINE,
  ERR_UNPRICED,
  ERR_VALUE_MISMATCH,
  KIND_CREDIT,
  KIND_EARN,
  KIND_PROTECTION,
  type BenefitDict,
  type ManifestBody,
  type WitnessDict,
  type WitnessFailure,
} from "./plumbline";
import { money } from "./format";
import type { Cart, CartLine } from "./types";

export interface LocalVerification {
  ok: boolean;
  supportsAssertion: boolean;
  /**
   * What this verifier will stand behind: the total over assignments that survived every
   * check, with over-capacity benefits and credits on over-offset lines dropped. Dropping
   * assignments cannot create a violation, so what remains is achievable and this is a
   * sound lower bound — never an upper one. Equals `claimedMinor` on a clean pass.
   */
  realizedMinor: number;
  /** What the witness itself claimed, before any check. */
  claimedMinor: number;
  assertedMinor: number;
  failures: WitnessFailure[];
  /** Wall clock for the check itself, measured in this browser on this run. */
  elapsedMs: number;
  assignments: number;
}

function admits(benefit: BenefitDict, line: CartLine, merchant: string): boolean {
  const e = benefit.eligibility;
  if (e.mccs.length > 0 && !e.mccs.includes(line.mcc)) return false;
  if (e.merchants.length > 0 && !e.merchants.includes(merchant)) return false;
  if (e.categories.length > 0 && !e.categories.includes(line.category)) return false;
  return true;
}

function available(benefit: BenefitDict): boolean {
  if (benefit.requires_enrollment && !benefit.enrolled) return false;
  if (benefit.capacity_minor !== null && benefit.capacity_minor <= 0) return false;
  return true;
}

/**
 * Check one assignment's declared value AND its declared capacity draw, per kind.
 *
 * Mirrors `witness.py :: _check_effect`, and checking the draw as well as the value is
 * load-bearing rather than defensive. Capacity is value-denominated and the cap test
 * downstream sums `consumed_minor`, so a witness that declares a smaller draw than the
 * benefit actually takes slips past a cap it has already blown. An earn benefit with one
 * unit of annual headroom, declaring `consumed_minor = 0` on two large lines, would
 * otherwise verify at the full value of both.
 *
 * Integer arithmetic throughout, and `Math.trunc` rather than `Math.floor` would differ on
 * negatives — rates and amounts are non-negative here, but the kernel floors, so this
 * floors.
 */
function checkEffect(
  benefit: BenefitDict,
  line: CartLine,
  a: { benefit_id: string; line_sku: string; consumed_minor: number; value_minor: number },
): WitnessFailure | null {
  if (a.consumed_minor < 0 || a.value_minor < 0 || line.amount < 0) {
    return {
      code: ERR_NEGATIVE_AMOUNT,
      detail:
        `${a.benefit_id} on ${a.line_sku} declares negative amounts ` +
        `(consumed ${a.consumed_minor}, value ${a.value_minor}, line ${line.amount})`,
    };
  }

  if (benefit.kind === KIND_CREDIT) {
    // The per-line aggregate bound downstream would also catch a single credit larger than
    // its line. This runs first so that case gets its own code rather than being reported
    // as a collective over-offset, which reads as the wrong diagnosis.
    if (a.consumed_minor > line.amount) {
      return {
        code: ERR_CREDIT_OVER_LINE,
        detail:
          `${a.benefit_id} offsets ${money(a.consumed_minor)} against ${a.line_sku}, ` +
          `a line of only ${money(line.amount)}`,
      };
    }
    if (a.value_minor !== a.consumed_minor) {
      return {
        code: ERR_VALUE_MISMATCH,
        detail:
          `${a.benefit_id} on ${a.line_sku} claims ${money(a.value_minor)} while ` +
          `offsetting ${money(a.consumed_minor)}; a credit yields exactly what it offsets`,
      };
    }
    return null;
  }

  let expected: number;
  if (benefit.kind === KIND_EARN) {
    expected = Math.floor((line.amount * benefit.rate_bp) / 10_000);
  } else if (benefit.kind === KIND_PROTECTION) {
    expected = benefit.flat_minor;
  } else {
    return {
      code: ERR_UNPRICED,
      detail: `${a.benefit_id} is kind '${benefit.kind}' and yields no priced value`,
    };
  }

  if (a.value_minor !== expected) {
    return {
      code: ERR_VALUE_MISMATCH,
      detail:
        `${a.benefit_id} on ${a.line_sku} claims ${money(a.value_minor)}, ` +
        `manifest yields ${money(expected)}`,
    };
  }
  if (a.consumed_minor !== expected) {
    return {
      code: ERR_CONSUMPTION_MISMATCH,
      detail:
        `${a.benefit_id} on ${a.line_sku} yields ${money(expected)} but declares ` +
        `a draw of ${money(a.consumed_minor)} against its cap`,
    };
  }
  return null;
}

/**
 * Check a witness against the manifest and cart it claims to describe.
 *
 * Checks run in the same order as the kernel's, and a failing assignment is skipped rather
 * than aborting the pass, so a counterparty sees every problem at once instead of one per
 * round trip.
 */
export function verifyWitness({
  witness,
  manifest,
  cart,
  cartHash,
  assertedMinor,
}: {
  witness: WitnessDict;
  manifest: ManifestBody;
  cart: Cart;
  /**
   * The hash the console holds for `cart`, compared against the one the witness binds.
   * Omitted means the console was given no independent hash to compare, and the binding
   * check is skipped rather than faked — recomputing it here needs SubtleCrypto and this
   * verifier is synchronous by design.
   */
  cartHash?: string;
  assertedMinor: number;
}): LocalVerification {
  const started = performance.now();
  const failures: WitnessFailure[] = [];
  const claimedMinor = witness.assignments.reduce((s, a) => s + a.value_minor, 0);

  const bail = (failure: WitnessFailure): LocalVerification => ({
    ok: false,
    supportsAssertion: false,
    realizedMinor: 0,
    claimedMinor,
    assertedMinor,
    failures: [failure],
    elapsedMs: performance.now() - started,
    assignments: witness.assignments.length,
  });

  // Does this witness even describe the manifest and cart it was handed alongside? Without
  // this the verifier answers "are these numbers right for the cart you gave me", when the
  // question a counterparty is asking is "does this witness support this receipt's
  // assertion about this cart". The arithmetic cannot tell them apart: a witness computed
  // against a smaller cart re-checks clean against a larger one and simply understates.
  //
  // Bail rather than accumulate. Once the witness is known to describe a different pair, an
  // arithmetic verdict about it answers a question nobody asked.
  if (witness.manifest_id !== manifest.manifest_id) {
    return bail({
      code: ERR_MANIFEST_MISMATCH,
      detail:
        `witness was computed against manifest '${witness.manifest_id}' but is being ` +
        `checked against '${manifest.manifest_id}'; verify it against the manifest it ` +
        `names, or re-run the allocator against this one`,
    });
  }
  if (cartHash !== undefined && witness.cart_hash !== cartHash) {
    return bail({
      code: ERR_CART_MISMATCH,
      detail:
        `witness binds cart ${witness.cart_hash.slice(0, 16)}… but is being checked ` +
        `against cart ${cartHash.slice(0, 16)}…; a witness proves a value for the cart it ` +
        `was computed from and says nothing about any other`,
    });
  }
  // Every amount here is an integer minor unit with no unit attached, so a manifest in one
  // currency and a cart in another are added as if a cent were a paisa. There is no
  // exchange rate in the decision path and there must not be one: an FX rate is a market
  // price, not an issuer-signed fact.
  if (manifest.currency !== cart.currency) {
    return bail({
      code: ERR_CURRENCY_MISMATCH,
      detail:
        `manifest is denominated in ${manifest.currency} and the cart in ${cart.currency}; ` +
        `minor units carry no unit, so these amounts cannot be compared or added — supply ` +
        `a manifest in the cart's currency`,
    });
  }

  const byId = new Map(manifest.benefits.map((b) => [b.benefit_id, b]));

  // SKUs address lines, so they must be unique. Building the index with `new Map` over the
  // lines would silently keep the last of a repeated SKU, and then one assignment names two
  // different line amounts at once while the verifier checks against whichever it indexed
  // last — which can be the larger. Bail rather than accumulate: with an ambiguous SKU every
  // downstream number is computed against an arbitrarily chosen line.
  const bySku = new Map<string, CartLine>();
  for (const line of cart.lines) {
    if (bySku.has(line.sku)) {
      return bail({
        code: ERR_DUPLICATE_SKU,
        detail:
          `cart names SKU '${line.sku}' more than once, so an assignment to it is ` +
          `ambiguous; fold duplicate SKUs into a single line before valuing the cart`,
      });
    }
    bySku.set(line.sku, line);
  }

  const groupClaims = new Map<string, string>();
  const seenPairs = new Set<string>();
  const surviving: { a: (typeof witness.assignments)[number]; benefit: BenefitDict }[] = [];

  for (const a of witness.assignments) {
    const benefit = byId.get(a.benefit_id);
    const line = bySku.get(a.line_sku);

    if (!benefit) {
      failures.push({
        code: ERR_UNKNOWN_BENEFIT,
        detail: `no benefit '${a.benefit_id}' in manifest`,
      });
      continue;
    }
    if (!line) {
      failures.push({ code: ERR_UNKNOWN_LINE, detail: `no line '${a.line_sku}' in cart` });
      continue;
    }
    if (!available(benefit)) {
      failures.push({
        code: ERR_UNAVAILABLE,
        detail: `${benefit.benefit_id} is unenrolled or exhausted`,
      });
      continue;
    }

    const pair = `${a.line_sku} ${a.benefit_id}`;
    if (seenPairs.has(pair)) {
      failures.push({
        code: ERR_DOUBLE_ASSIGNED,
        detail: `${a.benefit_id} attached twice to ${a.line_sku}`,
      });
      continue;
    }
    seenPairs.add(pair);

    if (!admits(benefit, line, cart.merchant)) {
      failures.push({
        code: ERR_INELIGIBLE,
        detail:
          `${benefit.benefit_id} does not admit ${line.sku} ` +
          `(mcc ${line.mcc}, category '${line.category}', merchant '${cart.merchant}')`,
      });
      continue;
    }

    if (benefit.exclusivity_group) {
      const key = `${a.line_sku} ${benefit.exclusivity_group}`;
      const holder = groupClaims.get(key);
      if (holder !== undefined && holder !== a.benefit_id) {
        failures.push({
          code: ERR_EXCLUSIVITY,
          detail:
            `${holder} and ${a.benefit_id} both claim ${a.line_sku} ` +
            `in group '${benefit.exclusivity_group}'`,
        });
        continue;
      }
      groupClaims.set(key, a.benefit_id);
    }

    const effectFailure = checkEffect(benefit, line, a);
    if (effectFailure) {
      failures.push(effectFailure);
      continue;
    }

    surviving.push({ a, benefit });
  }

  // Capacity is value-denominated and cumulative, so it can only be judged once every
  // per-assignment check has run.
  const consumed = new Map<string, number>();
  for (const { a } of surviving) {
    consumed.set(a.benefit_id, (consumed.get(a.benefit_id) ?? 0) + a.consumed_minor);
  }
  const overCapacity = new Set<string>();
  for (const benefitId of [...consumed.keys()].sort()) {
    const benefit = byId.get(benefitId);
    const used = consumed.get(benefitId)!;
    if (!benefit || benefit.capacity_minor === null) continue;
    if (used > benefit.capacity_minor) {
      overCapacity.add(benefitId);
      failures.push({
        code: ERR_CAPACITY,
        detail: `${benefitId} consumed ${money(used)} of ${money(benefit.capacity_minor)} available`,
      });
    }
  }

  // The credits on one line cannot together offset more than that line costs. Per-assignment
  // bounds are not enough: two credits, each individually no larger than the dinner and with
  // no declared exclusivity group, would otherwise verify at twice the dinner. Exclusivity
  // groups are a manifest-authoring convenience; this bound is structural and holds whether
  // or not an author remembered to declare one.
  //
  // Only credits count. An earn multiplier is a rebate on spend rather than an offset of it,
  // and a base rate stacking with a category bonus is ordinary. A protection pays out
  // against a loss, not against the line.
  const offset = new Map<string, number>();
  for (const { a, benefit } of surviving) {
    if (benefit.kind !== KIND_CREDIT) continue;
    offset.set(a.line_sku, (offset.get(a.line_sku) ?? 0) + a.value_minor);
  }
  const overOffset = new Set<string>();
  for (const sku of [...offset.keys()].sort()) {
    const total = offset.get(sku)!;
    const amount = bySku.get(sku)!.amount;
    if (total > amount) {
      overOffset.add(sku);
      failures.push({
        code: ERR_LINE_OVER_OFFSET,
        detail:
          `statement credits offset ${money(total)} against ${sku}, a line costing ` +
          `${money(amount)}; a credit cannot offset spend that is not on the line`,
      });
    }
  }

  // Assignments that failed a per-assignment check are already absent. Dropping every
  // assignment of an over-capacity benefit, and every credit on an over-offset line, leaves
  // a set satisfying all the same constraints, so what remains is achievable and its total
  // is a lower bound. On a clean pass nothing is dropped and this equals the witness's own
  // total, which is why there is one code path rather than a success and a failure case.
  let realized = 0;
  for (const { a, benefit } of surviving) {
    if (overCapacity.has(a.benefit_id)) continue;
    if (benefit.kind === KIND_CREDIT && overOffset.has(a.line_sku)) continue;
    realized += a.value_minor;
  }

  if (failures.length === 0 && realized < assertedMinor) {
    failures.push({
      code: ERR_OVERSTATED,
      detail: `witness realizes ${money(realized)}, assertion claims ${money(assertedMinor)}`,
    });
  }

  const ok = failures.length === 0;
  return {
    ok,
    supportsAssertion: ok && realized >= assertedMinor,
    realizedMinor: realized,
    claimedMinor,
    assertedMinor,
    failures,
    elapsedMs: performance.now() - started,
    assignments: witness.assignments.length,
  };
}
