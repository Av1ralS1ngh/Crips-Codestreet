/**
 * Beat 4 — the evaluator declines to sign.
 *
 * Refusal is a typed output, not an error path. An agent asserts a value; the witness it
 * offers does not support it; the evaluator emits a refusal with a reason code and anchors
 * it in the transparency log. Nothing is signed, and the fact that nothing was signed is
 * itself a signed, provable event.
 *
 * Both cases on this screen are built on the headline card's own published terms, not on an
 * invented one: the first claims more fuel earn than the published rate yields on the line
 * and than the monthly cap has left, the second adds the base rate to the overseas rate on
 * one line when the terms say the higher rate replaces the base rather than adding to it.
 * A refusal demonstrated against a term nobody published would prove nothing about the card
 * on screen. Two independent implementations of the same rules — this browser's and the
 * kernel's — is why the verdicts here can be compared rather than trusted.
 *
 * Every figure is the fixture's own: the asserted and supported displays are the kernel's
 * formatter, the failing clauses are the verifier's codes and details verbatim, and the
 * re-check latency is measured in this browser on this run.
 */

import { ReasonCode } from "../components/ui";
import {
  BeatHeader,
  BeatPage,
  BeatSplit,
  HashRow,
  PlumblineUnavailable,
  ShowsPanel,
  SquarePanel,
  Takeaway,
  VerifyPair,
} from "../components/plumblineUi";
import { currencyMoney, ms as fmtMs } from "../lib/format";
import { verifyInclusionProof, type LocalProofResult } from "../lib/merkle";
import { manifestFor, useConsole } from "../lib/store";
import { useLocalVerification } from "../lib/useWitness";
import { REFUSED_NO_WITNESS, type RefusalCase } from "../lib/plumbline";
import { useEffect, useState } from "react";

export function RefusalView() {
  const plumbline = useConsole((s) => s.plumbline);
  const plumblineError = useConsole((s) => s.plumblineError);
  const [caseIndex, setCaseIndex] = useState(0);

  const refusals = plumbline?.refusals ?? [];
  const active: RefusalCase | null = refusals[Math.min(caseIndex, refusals.length - 1)] ?? null;

  // The manifest is looked up by the id the witness itself names, so a mismatch cannot
  // arise on this screen. It is the receipt path, where a candidate's manifest is fixed by
  // the mandate rather than by the witness, that the binding check is there to catch.
  const local = useLocalVerification(
    active?.witness ?? null,
    manifestFor(plumbline, active?.witness.manifest_id ?? ""),
    plumbline?.cart ?? null,
    active?.asserted_minor ?? 0,
    plumbline?.cart_hash,
  );

  if (!plumbline || !active) return <PlumblineUnavailable error={plumblineError} />;

  const currency = plumbline.cart.currency;
  const basket = plumbline.cart.lines.reduce((total, line) => total + line.amount, 0);
  const unsupported = active.asserted_minor - active.supported_minor;
  const instrument = plumbline.instruments.find(
    (record) => record.instrument_id === active.witness.manifest_id,
  );
  const failures = active.verification.failures;

  return (
    <BeatPage>
      <BeatHeader
        beat="04"
        label="Refusal"
        title="It refuses to sign, and says why."
        meta={
          instrument ? `${instrument.product} · ${active.witness.manifest_id}` : active.witness.manifest_id
        }
      >
        {active.narrative}
      </BeatHeader>

      <BeatSplit
        aside={
          <ShowsPanel
            points={[
              "A refusal is a product feature. An agent that will sign anything is worth nothing as a witness.",
              "The evaluator's verdict and this console's own recomputation are shown together. One verdict alone would be a claim, not a check.",
              "The rejection is appended to the log before it is returned, so a refusal cannot be quietly retried away. If it is not in the log it did not happen, and a decision not to make a claim is no exception.",
            ]}
            footnote={
              <>
                <span className="num">{REFUSED_NO_WITNESS}</span> is the evaluator's own code.
                The clauses beside it name which rule of the manifest the offered allocation
                broke; the verifier enforces the manifest's declared vocabulary and nothing
                outside it.
              </>
            }
          />
        }
      >
        {/* The case is the screen's subject, so the selector sits above the two-up rather
            than in the header, where two full sentences would crowd the title. */}
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="colhead shrink-0">Refusal case</span>
          {refusals.map((refusal, index) => (
            <button
              key={refusal.case_id}
              type="button"
              aria-pressed={index === caseIndex}
              onClick={() => setCaseIndex(index)}
              className={`rounded-pill border px-4 py-[7px] text-[0.78125rem] font-semibold transition-colors duration-[180ms] ${
                index === caseIndex
                  ? "border-warning bg-warning-wash text-warning"
                  : "border-gray-03 bg-white text-ink-3 hover:border-blue hover:text-blue"
              }`}
            >
              {refusal.label}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-2 gap-6">
          <SquarePanel tone="deny" title="Asserted by the agent" right="REFUSED TO SIGN">
            <div className="flex flex-col gap-2 px-[22px] py-[26px]">
              <span className="num text-[2.125rem] leading-none font-semibold tracking-[-0.01em] text-navy">
                {active.asserted_display}
              </span>
              <span className="text-card-title font-normal leading-[1.55] text-ink-2">
                Claimed on the {currencyMoney(basket, currency)} basket.{" "}
                {currencyMoney(unsupported, currency)} of it has no allocation behind it.
              </span>
            </div>
            <div className="border-t border-gray-03 bg-gray-01 px-[22px] py-4">
              <ReasonCode code={active.reason_code} />
            </div>
          </SquarePanel>

          <SquarePanel title="The offered witness">
            <div className="flex flex-col gap-2 px-[22px] py-[26px]">
              <span className="num text-[2.125rem] leading-none font-semibold tracking-[-0.01em] text-blue">
                {active.verification.realized_display}
              </span>
              <span className="text-card-title font-normal leading-[1.55] text-ink-2">
                {active.witness.assignments.length} assignments,{" "}
                {local
                  ? `re-checked in ${fmtMs(local.elapsedMs, 3)} by this browser`
                  : "re-adding the numbers in this browser"}
                . No solver.
              </span>
            </div>
            <div className="flex flex-col gap-2 border-t border-gray-03 bg-gray-01 px-[22px] py-4">
              <div className="flex items-baseline justify-between gap-4">
                <span className="colhead">What it would have signed</span>
                <span className="num text-[0.9375rem] font-semibold text-navy">
                  {active.supported_display}
                </span>
              </div>
              <HashRow label="witness sha256" value={active.witness_hash} />
            </div>
          </SquarePanel>
        </div>

        <SquarePanel
          title="Verifier output"
          right={
            <span className="num text-code text-ink-4">
              {failures.length} failure{failures.length === 1 ? "" : "s"}
            </span>
          }
        >
          {/* The rule this screen exists to keep: the evaluator's verdict and this
              browser's own recomputation always render together. */}
          <div className="p-[22px]">
            <VerifyPair server={active.verification} local={local} />
          </div>

          <div className="flex flex-col gap-3.5 border-t border-gray-03 px-[22px] py-[22px]">
            <span className="colhead text-warning">Failing clauses</span>
            <div className="flex flex-col gap-2">
              {failures.map((failure, index) => (
                <div
                  key={`${failure.code}-${index}`}
                  className="flex flex-wrap items-baseline gap-x-3.5 gap-y-1.5 border-l-[3px] border-warning bg-gray-01 px-[18px] py-3.5"
                >
                  <span className="num shrink-0 text-code text-warning">{failure.code}</span>
                  <span className="min-w-0 flex-1 text-data font-normal leading-[1.5] text-ink">
                    {failure.detail}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <AnchorFooter refusal={active} />
        </SquarePanel>
      </BeatSplit>

      <Takeaway>
        A refusal is signed, coded, and anchored; absence of a claim is as provable as a
        claim.
      </Takeaway>
    </BeatPage>
  );
}

// ---------------------------------------------------------------------------------------

/**
 * The log anchor, recomputed here. The audit path is replayed in this browser rather than
 * displaying the server's own boolean, for the same reason the witness is.
 */
function AnchorFooter({ refusal }: { refusal: RefusalCase }) {
  const [proof, setProof] = useState<LocalProofResult | null>(null);

  useEffect(() => {
    let live = true;
    setProof(null);
    verifyInclusionProof(refusal.inclusion_proof).then((result) => {
      if (live) setProof(result);
    });
    return () => {
      live = false;
    };
  }, [refusal]);

  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 border-t border-gray-03 bg-gray-01 px-[22px] py-4">
      <div className="flex flex-wrap items-baseline gap-x-3.5 gap-y-1.5">
        <span className="colhead shrink-0">Anchored in the log</span>
        <span
          className={`num text-code font-medium ${
            proof === null ? "text-ink-4" : proof.ok ? "text-success" : "text-warning"
          }`}
        >
          {proof === null ? "…" : proof.ok ? "INCLUSION VERIFIED" : "INCLUSION FAILED"}
        </span>
        <span className="text-code text-ink-4">
          {proof === null
            ? "recomputing the root in this browser…"
            : proof.ok
              ? `${proof.steps.length}-step audit path recomputed here`
              : (proof.error ?? "recomputed root differs")}
        </span>
      </div>
      <div className="flex shrink-0 items-baseline gap-5">
        <span className="num text-code text-ink-4">
          entry {refusal.ledger_seq} of {refusal.ledger_size}
        </span>
        <HashRow label="entry hash" value={refusal.entry_hash} />
      </div>
    </div>
  );
}
