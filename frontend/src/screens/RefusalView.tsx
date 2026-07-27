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
 */

import { Caveat, Panel, ReasonCode } from "../components/ui";
import {
  HashRow,
  KindChip,
  ScreenHeader,
  PlumblineUnavailable,
  VerifyPair,
} from "../components/plumblineUi";
import { money, shortHash } from "../lib/format";
import { verifyInclusionProof, type LocalProofResult } from "../lib/merkle";
import { benefitsFor, manifestFor, useConsole } from "../lib/store";
import { useLocalVerification } from "../lib/useWitness";
import { REFUSED_NO_WITNESS, type RefusalCase, type PlumblineState } from "../lib/plumbline";
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

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <ScreenHeader
        beat="04"
        title="It refuses to sign, and says why."
        right={
          <div className="flex gap-1.5">
            {refusals.map((refusal, index) => (
              <button
                key={refusal.case_id}
                type="button"
                onClick={() => setCaseIndex(index)}
                className={`rounded border px-2.5 py-1 text-pill transition-colors ${
                  index === caseIndex
                    ? "border-deny/60 bg-deny-wash text-deny"
                    : "border-line-2 text-ink-3 hover:text-ink-2"
                }`}
              >
                {refusal.label}
              </button>
            ))}
          </div>
        }
      >
        {active.narrative}
      </ScreenHeader>

      <div className="grid shrink-0 grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)_minmax(0,1fr)] gap-3">
        <Panel title="Asserted by the agent" tone="deny" bodyClassName="flex flex-col gap-1.5 p-4">
          <div className="num display-wide text-[2.6rem] leading-none font-semibold text-deny line-through decoration-2">
            {active.asserted_display}
          </div>
          <span className="text-pill text-ink-3">
            offered with a witness naming {active.witness.assignments.length} assignments
          </span>
        </Panel>

        <Panel title="Evaluator" bodyClassName="flex flex-col items-start gap-2 p-4">
          <div className="anim-stamp display-xwide text-[2.1rem] leading-none font-bold tracking-[0.02em] text-deny">
            REFUSED TO SIGN
          </div>
          <ReasonCode code={active.reason_code} />
          {/* The "absence of a claim is as provable as a claim" line lived here and in the
              anchor panel below. Kept once, next to the inclusion proof that shows it. */}
          <span className="text-pill leading-snug text-ink-3">
            No signature was produced. The refusal is appended to the log.
          </span>
        </Panel>

        <Panel
          title="What it would have signed"
          hint="the largest value a valid witness supports"
          bodyClassName="flex flex-col gap-1.5 p-4"
        >
          <div className="num display-wide text-[2.6rem] leading-none font-semibold text-allow">
            {active.supported_display}
          </div>
          <span className="text-pill text-ink-3">
            {money(active.asserted_minor - active.supported_minor)} of the assertion has no
            allocation behind it
          </span>
        </Panel>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)] gap-3">
        <Panel
          title="The offered witness"
          hint="checked line by line against the manifest, no solver"
          bodyClassName="min-h-0 overflow-y-auto"
        >
          <WitnessTable refusal={active} plumbline={plumbline} />
        </Panel>

        <div className="flex min-h-0 flex-col gap-3">
          <Panel
            title="Verifier output"
            hint={`${active.verification.failures.length} failure${
              active.verification.failures.length === 1 ? "" : "s"
            }`}
            tone="deny"
            bodyClassName="flex min-h-0 flex-col gap-2 overflow-y-auto p-3.5"
          >
            <VerifyPair server={active.verification} local={local} />
            {active.verification.failures.map((failure, index) => (
              <div
                key={`${failure.code}-${index}`}
                className="flex flex-col gap-1 rounded border border-deny/30 bg-deny-wash/40 px-2.5 py-2"
              >
                <ReasonCode code={failure.code} />
                <p className="num text-pill leading-relaxed text-ink-2">{failure.detail}</p>
              </div>
            ))}
            <Caveat>
              {REFUSED_NO_WITNESS} is the evaluator's own code; the codes above it name
              which rule the offered allocation broke.
            </Caveat>
          </Panel>

          <AnchorPanel refusal={active} />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------

function WitnessTable({ refusal, plumbline }: { refusal: RefusalCase; plumbline: PlumblineState }) {
  const benefits = benefitsFor(plumbline, refusal.witness.manifest_id);
  const byId = new Map(benefits.map((b) => [b.benefit_id, b]));
  const bySku = new Map(plumbline.cart.lines.map((l) => [l.sku, l]));
  const flagged = new Set<string>();
  for (const failure of refusal.verification.failures) {
    for (const assignment of refusal.witness.assignments) {
      if (
        failure.detail.includes(assignment.benefit_id) &&
        failure.detail.includes(assignment.line_sku)
      ) {
        flagged.add(`${assignment.line_sku} ${assignment.benefit_id}`);
      }
    }
  }

  return (
    <table className="w-full table-fixed border-collapse text-pill">
      <colgroup>
        <col className="w-[36%]" />
        <col />
        <col className="w-[6.5rem]" />
        <col className="w-[6.5rem]" />
      </colgroup>
      <thead>
        <tr className="sticky top-0 z-10 bg-panel">
          <th className="eyebrow border-b border-line px-3.5 py-2 text-left font-semibold">
            Line
          </th>
          <th className="eyebrow border-b border-line px-2 py-2 text-left font-semibold">
            Benefit claimed
          </th>
          <th className="eyebrow border-b border-line px-2 py-2 text-right font-semibold">
            Balance / used
          </th>
          <th className="eyebrow border-b border-line px-3.5 py-2 text-right font-semibold">
            Value claimed
          </th>
        </tr>
      </thead>
      <tbody>
        {refusal.witness.assignments.map((assignment) => {
          const benefit = byId.get(assignment.benefit_id);
          const line = bySku.get(assignment.line_sku);
          const bad = flagged.has(`${assignment.line_sku} ${assignment.benefit_id}`);
          return (
            <tr
              key={`${assignment.line_sku}-${assignment.benefit_id}`}
              className={`border-b border-line/50 ${bad ? "bg-deny-wash/50" : ""}`}
            >
              <td className="px-3.5 py-1.5">
                <div className="break-words text-ink-2">{line?.description ?? assignment.line_sku}</div>
                <span className="num text-pill text-ink-4">
                  {money(line?.amount ?? 0)} · MCC {line?.mcc ?? "—"}
                </span>
              </td>
              <td className="px-2 py-1.5">
                <div className="flex items-baseline gap-2">
                  <span className={`break-words ${bad ? "text-deny" : "text-ink-2"}`}>
                    {benefit?.label ?? assignment.benefit_id}
                  </span>
                  {benefit && <KindChip kind={benefit.kind} />}
                </div>
                {benefit?.exclusivity_group && (
                  <span className="num text-pill text-ink-4">
                    group {benefit.exclusivity_group}
                  </span>
                )}
              </td>
              <td className="num px-2 py-1.5 text-right text-pill text-ink-4">
                <div>
                  {benefit?.capacity_minor === null || benefit === undefined
                    ? "no ceiling"
                    : money(benefit.capacity_minor)}
                </div>
                <div className="text-ink-3">used {money(assignment.consumed_minor)}</div>
              </td>
              <td
                className={`num px-3.5 py-1.5 text-right text-body ${
                  bad ? "text-deny" : "text-ink"
                }`}
              >
                {money(assignment.value_minor)}
              </td>
            </tr>
          );
        })}
        <tr>
          <td colSpan={3} className="border-t-2 border-line-2 px-3.5 py-2.5">
            <span className="eyebrow">Claimed total</span>
          </td>
          <td className="border-t-2 border-line-2 px-3.5 py-2.5 text-right">
            <span className="num display-wide text-[1.2rem] leading-none font-semibold text-deny line-through decoration-2">
              {refusal.asserted_display}
            </span>
          </td>
        </tr>
      </tbody>
    </table>
  );
}

function AnchorPanel({ refusal }: { refusal: RefusalCase }) {
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
    <Panel
      title="Anchored in the log"
      hint={`entry ${refusal.ledger_seq} of ${refusal.ledger_size}`}
      tone="proof"
      bodyClassName="flex flex-col gap-2 p-3.5"
    >
      <div className="flex items-baseline gap-2.5">
        <span
          className={`num text-body font-medium ${
            proof === null ? "text-ink-3" : proof.ok ? "text-allow" : "text-deny"
          }`}
        >
          {proof === null ? "…" : proof.ok ? "INCLUSION VERIFIED" : "INCLUSION FAILED"}
        </span>
        <span className="text-pill text-ink-4">
          {proof === null
            ? "recomputing the root in this browser…"
            : proof.ok
              ? `${proof.steps.length}-step audit path recomputed here`
              : (proof.error ?? "recomputed root differs")}
        </span>
      </div>
      <HashRow label="entry hash" value={refusal.entry_hash} />
      <HashRow label="leaf hash" value={refusal.leaf_hash} />
      <HashRow label="tree root" value={refusal.ledger_root} />
      <span className="num text-pill text-ink-4">
        witness sha256 {shortHash(refusal.witness_hash, 12, 8)}
      </span>
      <Caveat>
        If it is not in the log it did not happen; a decision not to make a claim is no
        exception.
      </Caveat>
    </Panel>
  );
}
