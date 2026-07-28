/**
 * Beat 03 — omission leaves a signature.
 *
 * The attack is not a forged number. It is a card that never appears: a platform that
 * quietly drops an instrument from the candidate set, and serves one log to the issuer and
 * another to the cardholder. Nothing in a single receipt catches that, because the receipt
 * the cardholder sees is internally consistent.
 *
 * What catches it is the relationship between two tree heads. To remove a candidate from a
 * receipt that already sits under a published head, the platform has to rewrite that entry,
 * and a rewritten entry is a different leaf, so the newer log stops being an extension of
 * the head it already signed. The consistency proof is what makes that failure legible, and
 * the console runs it here rather than trusting a boolean.
 *
 * For the executive who hears "Merkle" and reaches for the word crypto: this is the
 * mechanism that has underpinned public-web TLS trust for a decade. No chain, no token, no
 * consensus.
 */

import { useEffect, useState } from "react";
import { Check } from "../components/ui";
import {
  BeatHeader,
  BeatPage,
  BeatSplit,
  PlumblineUnavailable,
  ShowsPanel,
  SquarePanel,
  Takeaway,
  VerifyBox,
} from "../components/plumblineUi";
import { ms as fmtMs, money, shortHash } from "../lib/format";
import { useConsole } from "../lib/store";
import { verifyConsistency } from "../lib/transparency";
import { useConsistency } from "../lib/useWitness";
import type {
  ConsistencyProof,
  InstrumentValuation,
  OmissionCase,
  SignedTreeHead,
} from "../lib/plumbline";

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
  const names = new Map(plumbline.instruments.map((i) => [i.instrument_id, i.product]));
  const ranked = new Map(plumbline.receipt.candidates.map((c) => [c.instrument_id, c]));

  const publishedBest = bestOf(omission.candidates_published, ranked);
  const servedBest = bestOf(omission.candidates_served, ranked);
  const costMinor = Math.max(0, (publishedBest?.asserted_minor ?? 0) - (servedBest?.asserted_minor ?? 0));

  return (
    <BeatPage>
      <BeatHeader
        beat="03"
        label="Omission"
        title="A card that never appears still leaves a signature."
        meta={
          vectors === null
            ? "replaying proof vectors…"
            : `${vectors.passed}/${vectors.total} RFC 6962 vectors agree`
        }
      >
        {omission.narrative}
      </BeatHeader>

      <BeatSplit
        aside={
          <ShowsPanel
            points={[
              <>
                The interesting failure is not a wrong number. It is a card that quietly never
                entered the comparison.
              </>,
              <>
                Because the set is hashed into an append-only log before the answer is returned,
                an edited set is arithmetic to detect.
              </>,
              <>
                Nobody has to be accused of anything. Two heads from one witness kill the
                split-view attack, and the two roots simply do not agree.
              </>,
            ]}
            footnote={
              <>
                Tree heads are signed with HMAC under prototype keys, and the log is
                demonstrated rather than deployed; there is no third-party witness in this
                repository. The consistency algorithm is RFC 6962 and is unchanged by either.
              </>
            }
          />
        }
      >
        {/* ---------------------------------------------------------- what was served */}
        <div className="grid grid-cols-2 gap-6">
          <SquarePanel
            title="Candidate set in the published receipt"
            right={
              <span className="num text-pill font-normal text-ink-4">
                {omission.candidates_published.length} instruments
              </span>
            }
          >
            <div className="flex flex-col py-2">
              {omission.candidates_published.map((id) => (
                <CandidateRow key={id} id={id} name={names.get(id)} dropped={false} />
              ))}
            </div>
            <SetHash
              label="log root · honest extension"
              value={omission.head_b.root_hash}
              tone="proof"
            />
          </SquarePanel>

          <SquarePanel
            title="Candidate set the platform served"
            right={
              <span className="num text-pill font-normal text-ink-4">
                {omission.candidates_served.length} instruments
              </span>
            }
          >
            <div className="flex flex-col py-2">
              {omission.candidates_published.map((id) => (
                <CandidateRow
                  key={id}
                  id={id}
                  name={names.get(id)}
                  dropped={!omission.candidates_served.includes(id)}
                />
              ))}
            </div>
            <SetHash
              label="log root · edited log"
              value={omission.head_b_edited.root_hash}
              tone="deny"
            />
          </SquarePanel>

          <div className="col-span-2 grid grid-cols-2 gap-px border border-gray-03 bg-gray-03">
            <div className="flex flex-col gap-2.5 bg-white p-6">
              <span className="colhead text-warning">Verdict</span>
              <span className="num text-[1.1875rem] font-semibold text-warning">SET MISMATCH</span>
              <span className="text-card-title font-normal leading-relaxed text-ink-2">
                The set the receipt published is not the set the transparency log witnessed. No
                industry mechanism exists to record that {dropped.product} was considered, so
                the log is the only place the difference can show.
              </span>
            </div>
            <div className="flex flex-col gap-2.5 bg-white p-6">
              <span className="colhead">Value withheld from the comparison</span>
              <span className="num text-[1.1875rem] font-semibold text-navy">
                {dropped.asserted_display}
              </span>
              <span className="text-card-title font-normal leading-relaxed text-ink-2">
                {dropped.issuer} {dropped.product} realises that on this cart. The published set
                is won by {publishedBest?.product ?? "no instrument"} at{" "}
                <span className="num">{publishedBest?.asserted_display ?? "—"}</span>, the served
                set by {servedBest?.product ?? "no instrument"} at{" "}
                <span className="num">{servedBest?.asserted_display ?? "—"}</span>.{" "}
                {costMinor > 0
                  ? `The omission costs the cardholder ${money(costMinor)}.`
                  : "The omission costs nothing on this cart; what changes is the record."}
              </span>
            </div>
          </div>
        </div>

        {/* ------------------------------------------------------------- the two proofs */}
        <div className="grid grid-cols-2 gap-6">
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
      </BeatSplit>

      <Takeaway>
        If it is not in the ledger it did not happen; and if it was, deleting it shows.
      </Takeaway>
    </BeatPage>
  );
}

// ---------------------------------------------------------------------------------------

/** The highest-valued instrument a set actually contains, under the receipt's ranking. */
function bestOf(
  ids: string[],
  ranked: Map<string, InstrumentValuation>,
): InstrumentValuation | null {
  let best: InstrumentValuation | null = null;
  for (const id of ids) {
    const candidate = ranked.get(id);
    if (!candidate) continue;
    if (!best || candidate.asserted_minor > best.asserted_minor) best = candidate;
  }
  return best;
}

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
      className={`flex items-baseline justify-between gap-3 px-[22px] py-3.5 ${
        dropped ? "border-l-[3px] border-warning bg-warning-row pl-[19px]" : ""
      }`}
    >
      <div className="flex min-w-0 flex-col gap-1">
        <span className={`break-words text-data ${dropped ? "font-bold text-warning" : "text-ink"}`}>
          {name ?? id}
        </span>
        <span
          className={`num break-words text-pill font-normal ${dropped ? "text-warning" : "text-ink-4"}`}
        >
          {id}
        </span>
      </div>
      {dropped && (
        <span className="num shrink-0 text-pill font-normal tracking-[0.12em] text-warning">
          DROPPED
        </span>
      )}
    </div>
  );
}

function SetHash({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "proof" | "deny";
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-t border-gray-03 px-[22px] py-4">
      <span className="colhead break-words">{label}</span>
      <span
        className={`num shrink-0 text-code ${tone === "deny" ? "text-warning" : "text-blue"}`}
        title={value}
      >
        {shortHash(value, 8, 6)}
      </span>
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
    <SquarePanel
      // The tone states which log this is, not what the verifier concluded: the right-hand
      // log is the edited one by construction, and colouring it only after the proof
      // settles would make the panel look like it changed its mind.
      title={title}
      tone={expectOk ? "navy" : "deny"}
      right={
        <span className="num text-pill font-normal text-white">
          {proof.first_size} → {proof.second_size} entries
        </span>
      }
    >
      <div className="flex flex-col gap-4 px-[22px] py-5">
        <span className="text-code text-ink-4">{hint}</span>

        <div className="flex flex-col">
          <HeadLine head={headA} label="Head already published" tone="default" />
          <HeadLine head={headB} label="Head offered now" tone={expectOk ? "default" : "deny"} />
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
          <div className="border-l-[3px] border-warning bg-warning-row px-4 py-3">
            <p className="text-card-title font-normal leading-relaxed text-ink-2">
              This log is not an extension of the head the platform already signed. The edit is
              detectable and attributable.
            </p>
          </div>
        )}

        <div className="flex flex-col gap-2">
          <span className="colhead">
            Audit path · {proof.path.length} node{proof.path.length === 1 ? "" : "s"}
          </span>
          <div className="flex flex-col gap-1">
            {proof.path.map((hash, index) => (
              <div key={`${hash}-${index}`} className="flex items-baseline justify-between gap-3">
                <span className="num shrink-0 text-pill font-normal text-ink-4">
                  {result?.steps[index]?.role ?? "—"}
                </span>
                <span className="num shrink-0 text-code text-ink-2" title={hash}>
                  {shortHash(hash, 8, 6)}
                </span>
              </div>
            ))}
          </div>
        </div>

        {settled && (
          <div className="flex flex-col gap-1 border-t border-gray-02 pt-3">
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
      </div>
    </SquarePanel>
  );
}

function HeadLine({
  head,
  label,
  tone,
}: {
  head: SignedTreeHead;
  label: string;
  tone: "default" | "deny";
}) {
  return (
    <div
      className={`flex items-baseline justify-between gap-3 border-b border-gray-02 py-2.5 last:border-b-0 ${
        tone === "deny" ? "text-warning" : "text-ink"
      }`}
    >
      <div className="flex min-w-0 flex-col gap-1">
        <span className="break-words text-card-title font-normal text-ink-2">{label}</span>
        <span className="num break-words text-pill font-normal text-ink-4" title={head.signature}>
          sig {shortHash(head.signature, 8, 4)} · {head.key_id}
        </span>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1">
        <span className="num text-data text-ink">
          {head.tree_size} <span className="text-pill font-normal text-ink-4">entries</span>
        </span>
        <span
          className={`num text-code ${tone === "deny" ? "text-warning" : "text-blue"}`}
          title={head.root_hash}
        >
          {shortHash(head.root_hash, 8, 6)}
        </span>
      </div>
    </div>
  );
}

function RootLine({ label, value, ok }: { label: string; value: string; ok: boolean }) {
  return (
    <div className="flex min-w-0 items-baseline justify-between gap-3">
      <span className="flex shrink-0 items-baseline gap-2">
        <Check ok={ok} size={10} />
        <span className="text-code text-ink-4">{label}</span>
      </span>
      <span
        className={`num shrink-0 text-code ${ok ? "text-blue" : "text-warning"}`}
        title={value}
      >
        {value ? shortHash(value, 8, 6) : "—"}
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
