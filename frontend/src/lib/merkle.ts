/**
 * Independent client-side verification of a Merkle inclusion proof.
 *
 * The evidence package carries the server's own `proof_verified` boolean. Taking that
 * boolean at face value would defeat the purpose of shipping an audit path at all, so
 * the console recomputes the root from the leaf and the path itself and displays its own
 * result alongside the server's.
 *
 * This mirrors `ledger.InclusionProof.verify()` exactly, including RFC-6962-style domain
 * separation: internal nodes hash 0x01 || left || right. Leaf hashes (0x00 || payload)
 * are produced server-side; the console verifies the path above the leaf, which is the
 * part an auditor needs and the part a tampered log cannot fake.
 */

import type { InclusionProof } from "./types";

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

export interface LocalProofResult {
  ok: boolean;
  computedRoot: string;
  expectedRoot: string;
  steps: { side: "left" | "right"; sibling: string; result: string }[];
  error?: string;
}

export async function verifyInclusionProof(proof: InclusionProof): Promise<LocalProofResult> {
  const steps: LocalProofResult["steps"] = [];
  try {
    let current = proof.leaf_hash;
    for (const { side, hash } of proof.path) {
      current = side === "left" ? await nodeHash(hash, current) : await nodeHash(current, hash);
      steps.push({ side, sibling: hash, result: current });
    }
    return {
      ok: current === proof.root,
      computedRoot: current,
      expectedRoot: proof.root,
      steps,
    };
  } catch (err) {
    return {
      ok: false,
      computedRoot: "",
      expectedRoot: proof.root,
      steps,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}
