/**
 * React bindings for the two independent checks the console performs for itself.
 *
 * Both are deliberately client-side. The witness check is linear-time arithmetic over the
 * manifest and needs no solver; the consistency check is the published RFC 6962 algorithm.
 * Displaying the server's own boolean for either would turn an argument about verifiability
 * into an assertion about it.
 */

import { useEffect, useMemo, useState } from "react";
import { verifyConsistency, type ConsistencyResult } from "./transparency";
import { verifyWitness, type LocalVerification } from "./witness";
import type { ConsistencyProof, ManifestBody, WitnessDict } from "./plumbline";
import type { Cart } from "./types";

/** Synchronous: the verifier is arithmetic, so there is nothing to await. */
export function useLocalVerification(
  witness: WitnessDict | null,
  manifest: ManifestBody | null,
  cart: Cart | null,
  assertedMinor: number,
  cartHash?: string,
): LocalVerification | null {
  return useMemo(() => {
    if (!witness || !cart || !manifest) return null;
    return verifyWitness({ witness, manifest, cart, cartHash, assertedMinor });
  }, [witness, manifest, cart, cartHash, assertedMinor]);
}

/** Asynchronous: SHA-256 lives behind SubtleCrypto, which is promise-based. */
export function useConsistency(proof: ConsistencyProof | null): ConsistencyResult | null {
  const [result, setResult] = useState<ConsistencyResult | null>(null);

  useEffect(() => {
    let live = true;
    setResult(null);
    if (!proof) return;
    verifyConsistency(proof).then((next) => {
      if (live) setResult(next);
    });
    return () => {
      live = false;
    };
  }, [proof]);

  return result;
}
