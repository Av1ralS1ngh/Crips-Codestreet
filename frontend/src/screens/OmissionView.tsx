/**
 * Beat 3 — omission leaves a signature.
 *
 * The attack is not a forged number. It is a card that never appears: a platform that
 * quietly drops an instrument from the candidate set, and serves one log to the issuer and
 * another to the cardholder. Nothing in a single receipt catches that, because the receipt
 * the cardholder sees is internally consistent.
 *
 * What catches it is the relationship between two tree heads. To remove a candidate from a
 * receipt that already sits under a published head, the platform has to rewrite that entry
 * — and a rewritten entry is a different leaf, so the newer log stops being an extension of
 * the head it already signed. The consistency proof is what makes that failure legible, and
 * the console runs it here rather than trusting a boolean.
 *
 * For the executive who hears "Merkle" and reaches for the word crypto: this is the
 * mechanism that has underpinned public-web TLS trust for a decade. No chain, no token, no
 * consensus.
 */

import { useEffect, useState } from "react";
import { Caveat, Check, Panel } from "../components/ui";
import { ScreenHeader, PlumblineUnavailable, VerifyBox } from "../components/plumblineUi";
import { ms as fmtMs, shortHash } from "../lib/format";
import { useConsole } from "../lib/store";
import { verifyConsistency } from "../lib/transparency";
import { useConsistency } from "../lib/useWitness";
import type { ConsistencyProof, OmissionCase, SignedTreeHead } from "../lib/plumbline";

export function OmissionView() {
  const plumbline = useConsole((s) => s.plumbline);
  const plumblineError = useConsole((s) => s.plumblineError);

  const omission = plumbline?.omission ?? null;
  const honest = useConsistency(omission?.consistency_honest ?? null);
  const edited = useConsistency(omission?.consistency_edited ?? null);
  const vectors = useVectorSelfTest(omission);

  if (!plumbline || !omission) return <PlumblineUnavailable error={plumblineError} />;

  const dropped = omission.omitted_instrument;
  // The manifest id is what the log records; the product name is what a room reads.
  const names = new Map(
    plumbline.instruments.map((i) => [i.instrument_id, `${i.issuer} ${i.product}`]),
  );

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <ScreenHeader
        beat="03"
        title="A card that never appears still leaves a signature."
        right={
          <span className="num text-pill text-ink-4">
            {vectors === null
              ? "replaying proof vectors…"
              : `${vectors.passed}/${vectors.total} RFC 6962 vectors agree`}
          </span>
        }
      >
        {omission.narrative}
      </ScreenHeader>

      {/* ---------------------------------------------------------- what was served */}
      <div className="grid shrink-0 grid-cols-2 gap-3">
        <Panel
          title="Candidate set in the published receipt"
          hint={`${omission.candidates_published.length} instruments`}
          bodyClassName="flex flex-col gap-1 p-3.5"
        >
          {omission.candidates_published.map((id) => (
            <CandidateRow key={id} id={id} name={names.get(id)} dropped={false} />
          ))}
        </Panel>
        <Panel
          title="Candidate set the platform served"
          hint={`${omission.candidates_served.length} instruments`}
          tone="deny"
          bodyClassName="flex flex-col gap-1 p-3.5"
        >
          {omission.candidates_published.map((id) => (
            <CandidateRow
              key={id}
              id={id}
              name={names.get(id)}
              dropped={!omission.candidates_served.includes(id)}
            />
          ))}
        </Panel>
      </div>

      <div className="shrink-0 rounded-md border border-deny/40 bg-deny-wash/50 px-4 py-2.5">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="eyebrow text-deny">Dropped</span>
          <span className="text-body font-medium text-ink">
            {dropped.issuer} {dropped.product}
          </span>
          <span className="num text-body text-ink-2">
            witness-backed value on this cart {dropped.asserted_display}
          </span>
          <span className="ml-auto text-pill text-ink-3">
            No industry mechanism exists to record that this instrument was considered.
          </span>
        </div>
      </div>

      {/* ------------------------------------------------------------- the two proofs */}
      <div className="grid min-h-0 flex-1 grid-cols-2 gap-3">
        <ProofColumn
          title="Honest extension"
          hint="five later receipts appended, nothing rewritten"
          headA={omission.head_a}
          headB={omission.head_b}
          proof={omission.consistency_honest}
          result={honest}
          expectOk
        />
        <ProofColumn
          title="The platform's edited log"
          hint="one receipt rewritten to drop a candidate"
          headA={omission.head_a}
          headB={omission.head_b_edited}
          proof={omission.consistency_edited}
          result={edited}
          expectOk={false}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------

function CandidateRow({
  id,
  name,
  dropped,
}: {
  id: string;
  name: string | undefined;
  dropped: boolean;
}) {
  return (
    <div
      className={`flex items-baseline gap-2.5 rounded px-2 py-1 ${
        dropped ? "bg-deny-wash/60" : ""
      }`}
    >
      <span
        className={`shrink-0 text-body ${
          dropped ? "font-medium text-deny line-through decoration-2" : "text-ink"
        }`}
      >
        {name ?? id}
      </span>
      <span
        className={`num min-w-0 break-words text-pill ${
          dropped ? "text-deny/70 line-through" : "text-ink-4"
        }`}
      >
        {id}
      </span>
      {dropped && (
        <span className="ml-auto shrink-0 rounded border border-deny/50 px-1 py-px font-mono text-pill tracking-[0.08em] text-deny uppercase">
          omitted
        </span>
      )}
    </div>
  );
}

function ProofColumn({
  title,
  hint,
  headA,
  headB,
  proof,
  result,
  expectOk,
}: {
  title: string;
  hint: string;
  headA: SignedTreeHead;
  headB: SignedTreeHead;
  proof: ConsistencyProof;
  result: ReturnType<typeof useConsistency>;
  expectOk: boolean;
}) {
  const settled = result !== null;
  const ok = result?.ok ?? false;

  return (
    <Panel
      title={title}
      hint={hint}
      tone={settled ? (ok ? "proof" : "deny") : "default"}
      className="min-h-0"
      bodyClassName="flex min-h-0 flex-col gap-3 overflow-y-auto p-3.5"
    >
      <div className="flex items-stretch gap-2">
        <HeadCard head={headA} label="Head already published" />
        <div className="flex shrink-0 items-center px-1">
          <span className={`text-lg ${settled && ok ? "text-allow" : "text-deny"}`}>→</span>
        </div>
        <HeadCard head={headB} label="Head offered now" tone={expectOk ? "default" : "deny"} />
      </div>

      <VerifyBox
        label="Consistency, recomputed in this browser"
        ok={ok}
        pending={!settled}
        note={
          !settled
            ? "rehashing the audit path…"
            : ok
              ? `the older log is contained unchanged · ${fmtMs(result.elapsedMs, 2)}`
              : result.error
                ? result.error
                : !result.firstMatches
                  ? "the rebuilt old root does not match the head already signed"
                  : "the rebuilt new root does not match the head being offered"
        }
      />

      {settled && !ok && (
        <div className="flex flex-col gap-1 rounded border border-deny/40 bg-deny-wash/40 px-3 py-2">
          <span className="eyebrow text-deny">Verdict</span>
          <p className="text-pill leading-snug text-ink-2">
            This log is not an extension of the head the platform already signed. The edit
            is detectable and attributable.
          </p>
        </div>
      )}

      <div className="flex flex-col gap-1">
        <div className="eyebrow">
          Audit path · {proof.path.length} node{proof.path.length === 1 ? "" : "s"} for{" "}
          {proof.first_size} → {proof.second_size} entries
        </div>
        <div className="flex flex-col gap-0.5">
          {proof.path.map((hash, index) => (
            <div key={`${hash}-${index}`} className="flex items-baseline gap-2">
              <span className="num w-[4.6rem] shrink-0 text-pill text-ink-4">
                {result?.steps[index]?.role ?? "—"}
              </span>
              <span className="hash min-w-0 flex-1 break-words text-pill" title={hash}>
                {hash}
              </span>
            </div>
          ))}
        </div>
      </div>

      {settled && (
        <div className="flex flex-col gap-0.5">
          <RootLine label="rebuilt old root" value={result.computedFirst} ok={result.firstMatches} />
          <RootLine label="signed old root" value={result.expectedFirst} ok={result.firstMatches} />
          <RootLine
            label="rebuilt new root"
            value={result.computedSecond}
            ok={result.secondMatches}
          />
          <RootLine
            label="signed new root"
            value={result.expectedSecond}
            ok={result.secondMatches}
          />
        </div>
      )}

      {expectOk && (
        <Caveat>
          O(log n), and no access to the rest of the log. Two heads from one witness kill
          the split-view attack.
        </Caveat>
      )}
    </Panel>
  );
}

function HeadCard({
  head,
  label,
  tone = "default",
}: {
  head: SignedTreeHead;
  label: string;
  tone?: "default" | "deny";
}) {
  return (
    <div
      className={`flex min-w-0 flex-1 flex-col gap-1 rounded border px-2.5 py-2 ${
        tone === "deny" ? "border-deny/35 bg-deny-wash/35" : "border-line bg-sunken"
      }`}
    >
      <div className="eyebrow break-words">{label}</div>
      <div className="num text-body text-ink">
        {head.tree_size} <span className="text-pill text-ink-4">entries</span>
      </div>
      <span className="hash text-pill" title={head.root_hash}>
        {shortHash(head.root_hash, 14, 8)}
      </span>
      <span className="num text-pill text-ink-4" title={head.signature}>
        sig {shortHash(head.signature, 8, 4)} · {head.key_id}
      </span>
    </div>
  );
}

function RootLine({ label, value, ok }: { label: string; value: string; ok: boolean }) {
  return (
    <div className="flex min-w-0 items-baseline gap-2">
      <span className="w-[6.8rem] shrink-0 text-pill text-ink-4">{label}</span>
      <Check ok={ok} size={10} />
      <span
        className={`num min-w-0 flex-1 break-words text-pill ${ok ? "text-proof" : "text-deny"}`}
        title={value}
      >
        {value || "—"}
      </span>
    </div>
  );
}

/**
 * Replay the cross-checked (m, n) vectors — negatives included — through the browser's own
 * verifier. Two proofs agreeing is an anecdote; the vectors are the evidence that the
 * implementation on this machine is the published algorithm.
 */
function useVectorSelfTest(
  omission: OmissionCase | null,
): { passed: number; total: number } | null {
  const [result, setResult] = useState<{ passed: number; total: number } | null>(null);

  useEffect(() => {
    let live = true;
    setResult(null);
    if (!omission) return;
    (async () => {
      let passed = 0;
      for (const vector of omission.vectors) {
        const outcome = await verifyConsistency(vector);
        if (outcome.ok === vector.expect_ok) passed += 1;
      }
      if (live) setResult({ passed, total: omission.vectors.length });
    })();
    return () => {
      live = false;
    };
  }, [omission]);

  return result;
}
