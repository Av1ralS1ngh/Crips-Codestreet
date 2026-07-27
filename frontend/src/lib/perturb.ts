/**
 * Applying a perturbation to a signed manifest body, in the browser.
 *
 * The console does not take the engine's word for what a perturbed manifest is. It holds
 * the signed body, changes exactly one field of exactly one benefit itself, and then
 * re-verifies the engine's witness against the result. If the two ever disagreed about
 * what "raise this balance to ₹5,000" means, the witness would stop verifying and the
 * screen would say so — which is the only reason a control a judge drives is worth
 * anything.
 *
 * This is not the allocator, and the allocator is deliberately still not ported. Producing
 * an allocation is a decision and the decision belongs to the engine. Changing one integer
 * in a manifest is not a decision; checking the allocation that came back is arithmetic.
 *
 * One consequence, and the screen states it: a perturbed manifest is no longer the body
 * the issuer signed. The signature travels with the bytes, so it does not survive.
 */

import { FIELD_CAPACITY, type ManifestBody, type PerturbableField } from "./plumbline";

export function perturbManifest(
  manifest: ManifestBody,
  benefitId: string,
  field: PerturbableField,
  value: number,
): ManifestBody {
  return {
    ...manifest,
    benefits: manifest.benefits.map((b) =>
      b.benefit_id === benefitId
        ? {
            ...b,
            ...(field === FIELD_CAPACITY ? { capacity_minor: value } : { rate_bp: value }),
          }
        : b,
    ),
  };
}
