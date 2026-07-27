/**
 * The allocation table, rebuilt in the browser from the witness.
 *
 * A direct port of `scripts/gen_plumbline_fixtures.py :: derivation_rows`, which is what
 * produced the `derivation` arrays the other screens render. Porting it means the
 * perturbation screen can show a full derivation for an allocation nobody shipped a table
 * for — and, more to the point, that the table on screen is reconstructed from the same
 * three objects a counterparty holds: the witness, the manifest it names, and the cart it
 * binds. A table that arrives pre-rendered asks to be believed. This one can be redone.
 *
 * Presentation only. Nothing here recomputes a value or makes a decision: every rupee in
 * the output is copied from an assignment the engine emitted. The running balance is
 * arithmetic over those same assignments, in the witness's own order, so a reader can
 * re-add the column and land on the same remaining balance.
 *
 * `src/mock/plumblineFixtures.json` carries the engine's own rows for the same witnesses,
 * and the smoke test asserts this function reproduces them field for field.
 */

import { money } from "./format";
import type { BenefitDict, DerivationRow, ManifestBody, WitnessDict } from "./plumbline";
import type { Cart, CartLine } from "./types";

export function derivationRows(
  manifest: ManifestBody,
  cart: Cart,
  witness: WitnessDict,
): DerivationRow[] {
  const byId = new Map<string, BenefitDict>(manifest.benefits.map((b) => [b.benefit_id, b]));
  const bySku = new Map<string, CartLine>(cart.lines.map((l) => [l.sku, l]));
  const running = new Map<string, number>();
  const rows: DerivationRow[] = [];

  for (const a of witness.assignments) {
    const benefit = byId.get(a.benefit_id);
    const line = bySku.get(a.line_sku);
    // An assignment naming a benefit or a line that is not there is a verification
    // failure, and the verifier reports it by code. Skipping here keeps the table from
    // inventing a row for it; it never suppresses the failure, which is rendered beside.
    if (!benefit || !line) continue;

    const used = running.get(a.benefit_id) ?? 0;
    const before = benefit.capacity_minor === null ? null : benefit.capacity_minor - used;
    running.set(a.benefit_id, used + a.consumed_minor);

    rows.push({
      line_sku: line.sku,
      line_description: line.description,
      line_amount: line.amount,
      line_mcc: line.mcc,
      benefit_id: benefit.benefit_id,
      benefit_label: benefit.label,
      benefit_kind: benefit.kind,
      rate_bp: benefit.rate_bp,
      window: benefit.window,
      exclusivity_group: benefit.exclusivity_group,
      consumed_minor: a.consumed_minor,
      value_minor: a.value_minor,
      value_display: money(a.value_minor),
      capacity_before: before,
      capacity_after: before === null ? null : before - a.consumed_minor,
    });
  }
  return rows;
}
