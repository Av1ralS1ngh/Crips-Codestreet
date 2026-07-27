/**
 * RFC 6962 consistency-proof verification, in the browser.
 *
 * Inclusion proofs answer "is this receipt in that log". Consistency proofs answer the
 * question that actually matters here: "is the log I am being shown today an extension of
 * the log I was shown yesterday, or has something been rewritten." Omission is the attack
 * — an agent that drops an instrument from the candidate set entirely — and rewriting an
 * entry that already sits under a published head is what a platform has to do to hide it.
 *
 * `caveat.ledger` builds its tree level by level, promoting the odd tail. That is exactly
 * RFC 6962's MTH, so the published algorithm applies unchanged; the fixture generator
 * asserts the equality rather than assuming it, and ships cross-checked (m, n) vectors —
 * including negatives — that the smoke test replays through this verifier.
 *
 * This is the mechanism that has underpinned public-web TLS trust for a decade. No chain,
 * no token, no consensus.
 */

import type { ConsistencyProof } from "./plumbline";

function hexToBytes(hex: string): Uint8Array {
  const clean = hex.trim().toLowerCase();
  if (clean.length % 2 !== 0 || /[^0-9a-f]/.test(clean)) {
    throw new Error(`not a hex digest: ${hex}`);
  }
  const out = new Uint8Array(clean.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = Number.parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Internal nodes hash 0x01 || left || right. Domain separation from leaves is the point. */
async function nodeHash(left: string, right: string): Promise<string> {
  const l = hexToBytes(left);
  const r = hexToBytes(right);
  const buf = new Uint8Array(1 + l.length + r.length);
  buf[0] = 0x01;
  buf.set(l, 1);
  buf.set(r, 1 + l.length);
  const digest = await crypto.subtle.digest("SHA-256", buf as BufferSource);
  return bytesToHex(new Uint8Array(digest));
}

export interface ConsistencyResult {
  ok: boolean;
  /** The old root as rebuilt from the proof. Must equal the head already published. */
  computedFirst: string;
  /** The new root as rebuilt from the proof. Must equal the head being offered. */
  computedSecond: string;
  expectedFirst: string;
  expectedSecond: string;
  /** Which of the two rebuilt roots disagreed, for copy that names the failure. */
  firstMatches: boolean;
  secondMatches: boolean;
  steps: { role: "old+new" | "new only"; sibling: string }[];
  elapsedMs: number;
  error?: string;
}

/**
 * Verify that a log of `second_size` entries contains a log of `first_size` entries
 * unchanged.
 *
 * The algorithm is RFC 6962 §2.1.3: walk from the old tree's rightmost leaf up to the
 * first node the two trees share, rebuilding both roots from the same proof nodes. Nodes
 * on the shared spine feed both roots; nodes to the right of it feed only the new one.
 * If either rebuilt root misses, the newer log is not an extension of the older one.
 */
export async function verifyConsistency(proof: ConsistencyProof): Promise<ConsistencyResult> {
  const started = performance.now();
  const {
    first_size: m,
    second_size: n,
    first_root: expectedFirst,
    second_root: expectedSecond,
    path,
  } = proof;

  const steps: ConsistencyResult["steps"] = [];
  const fail = (error: string): ConsistencyResult => ({
    ok: false,
    computedFirst: "",
    computedSecond: "",
    expectedFirst,
    expectedSecond,
    firstMatches: false,
    secondMatches: false,
    steps,
    elapsedMs: performance.now() - started,
    error,
  });

  try {
    if (m > n) return fail(`first_size ${m} exceeds second_size ${n}`);
    if (m === n) {
      const ok = path.length === 0 && expectedFirst === expectedSecond;
      return {
        ok,
        computedFirst: expectedFirst,
        computedSecond: expectedSecond,
        expectedFirst,
        expectedSecond,
        firstMatches: ok,
        secondMatches: ok,
        steps,
        elapsedMs: performance.now() - started,
        error: ok ? undefined : "equal tree sizes with a non-empty proof or differing roots",
      };
    }
    if (m === 0) {
      const ok = path.length === 0;
      return {
        ok,
        computedFirst: expectedFirst,
        computedSecond: expectedSecond,
        expectedFirst,
        expectedSecond,
        firstMatches: ok,
        secondMatches: ok,
        steps,
        elapsedMs: performance.now() - started,
        error: ok ? undefined : "empty first tree with a non-empty proof",
      };
    }

    let node = m - 1;
    let last = n - 1;
    while (node % 2 === 1) {
      node = Math.floor(node / 2);
      last = Math.floor(last / 2);
    }

    const rest = [...path];
    const take = (): string => {
      const next = rest.shift();
      if (next === undefined) throw new Error("proof is shorter than the tree shape requires");
      return next;
    };

    let first: string;
    let second: string;
    if (node > 0) {
      first = second = take();
      steps.push({ role: "old+new", sibling: first });
    } else {
      first = second = expectedFirst;
    }

    while (node > 0) {
      if (node % 2 === 1) {
        const sibling = take();
        steps.push({ role: "old+new", sibling });
        first = await nodeHash(sibling, first);
        second = await nodeHash(sibling, second);
      } else if (node < last) {
        const sibling = take();
        steps.push({ role: "new only", sibling });
        second = await nodeHash(second, sibling);
      }
      node = Math.floor(node / 2);
      last = Math.floor(last / 2);
    }

    while (last > 0) {
      const sibling = take();
      steps.push({ role: "new only", sibling });
      second = await nodeHash(second, sibling);
      last = Math.floor(last / 2);
    }

    if (rest.length > 0) return fail(`${rest.length} unused proof node(s)`);

    // A mismatch is not an error: the proof was well-formed and the answer is no. The
    // caller distinguishes the two roots, because which one missed says what happened —
    // the old root missing means an entry under the published head was rewritten.
    const firstMatches = first === expectedFirst;
    const secondMatches = second === expectedSecond;
    return {
      ok: firstMatches && secondMatches,
      computedFirst: first,
      computedSecond: second,
      expectedFirst,
      expectedSecond,
      firstMatches,
      secondMatches,
      steps,
      elapsedMs: performance.now() - started,
    };
  } catch (err) {
    return fail(err instanceof Error ? err.message : String(err));
  }
}
