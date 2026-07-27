/**
 * Reading a derivation row back into the rule that produced it.
 *
 * The money column carries the result; a reader checking the arithmetic needs the rule
 * that yielded it, stated in the manifest's own terms. Presentation only — nothing here
 * participates in a decision, and nothing here recomputes a value.
 */

import { money } from "./format";
import { KIND_CREDIT, KIND_EARN, KIND_PROTECTION, type BenefitKind } from "./plumbline";

export function ruleFor(row: {
  benefit_kind: BenefitKind;
  rate_bp: number;
  line_amount: number;
  consumed_minor: number;
}): string {
  if (row.benefit_kind === KIND_EARN) {
    return `${(row.rate_bp / 100).toFixed(2)}% of ${money(row.line_amount)}`;
  }
  if (row.benefit_kind === KIND_CREDIT) {
    return `offsets ${money(row.consumed_minor)} of spend`;
  }
  if (row.benefit_kind === KIND_PROTECTION) {
    return "flat cover, per qualifying line";
  }
  return "declared, not priced";
}
