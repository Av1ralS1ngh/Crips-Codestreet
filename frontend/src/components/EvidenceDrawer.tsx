/**
 * Beat 4 — the handoff. The Agent Evidence Package for one decision.
 *
 * The package carries the server's own `proof_verified` boolean. Displaying that alone
 * would make the audit path decorative, so the drawer recomputes the Merkle root from the
 * leaf and the audit path in the browser and shows its own verdict next to the server's.
 * A dispute reviewer verifies one decision without being handed the log.
 */

import { useEffect, useState } from "react";
import {
  Caveat,
  Check,
  Divider,
  Eyebrow,
  Money,
  OutcomeChip,
  Panel,
  ReasonCode,
  Verdict,
} from "./ui";
import { clockTime, ms as fmtMs, signedMoney } from "../lib/format";
import { verifyInclusionProof, type LocalProofResult } from "../lib/merkle";
import { useConsole } from "../lib/store";
import type { EvidencePackage, InclusionProof, MandateNode } from "../lib/types";

export function EvidenceDrawer() {
  const txnId = useConsole((s) => s.evidenceTxn);
  const evidence = useConsole((s) => s.evidence);
  const loading = useConsole((s) => s.evidenceLoading);
  const close = useConsole((s) => s.closeEvidence);

  useEffect(() => {
    if (!txnId) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [txnId, close]);

  if (!txnId) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button
        type="button"
        aria-label="close evidence"
        onClick={close}
        className="flex-1 bg-navy/40"
      />
      <div className="flex w-[47rem] max-w-[92vw] flex-col border-l border-gray-03 bg-white">
        <header className="flex shrink-0 items-baseline justify-between gap-4 border-b border-line px-5 py-3.5">
          <div className="min-w-0">
            <h2 className="display-wide text-[1.05rem] font-semibold">Agent Evidence Package</h2>
            <div className="num break-words text-pill text-ink-4">{txnId}</div>
          </div>
          <button
            type="button"
            onClick={close}
            className="shrink-0 text-pill text-ink-3 transition-colors hover:text-ink"
          >
            close ⎋
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          {loading || !evidence ? (
            <div className="py-10 text-center text-body text-ink-4">
              assembling package…
            </div>
          ) : (
            <EvidenceBody evidence={evidence} />
          )}
        </div>
      </div>
    </div>
  );
}

function EvidenceBody({ evidence }: { evidence: EvidencePackage }) {
  const { decision, diff } = evidence;

  return (
    <div className="flex flex-col gap-4">
      <Panel bodyClassName="flex items-start justify-between gap-6 p-4">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2.5">
            <OutcomeChip outcome={decision.outcome} size="lg" />
            <span className="num text-[0.78rem] text-ink-2">{decision.headline}</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {decision.reason_codes.map((code) => (
              <ReasonCode key={code} code={code} />
            ))}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <Money
            minorUnits={decision.amount}
            tone={decision.outcome === "DENY" ? "deny" : "default"}
            className="display-wide text-[1.7rem] leading-none font-semibold"
          />
          <div className="num mt-1 text-pill text-ink-4">
            {clockTime(decision.ts)} · {fmtMs(decision.elapsed_ms)} · ledger #{decision.ledger_seq}
          </div>
        </div>
      </Panel>

      <Panel title="Liability" bodyClassName="p-4">
        <Verdict verdict={evidence.verdict} liable={evidence.liable_party} />
        {decision.notes.length > 0 && (
          <ul className="mt-2.5 flex flex-col gap-1">
            {decision.notes.map((note, index) => (
              <li key={index} className="text-pill text-ink-3">
                {note}
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel title="Mandate chain" hint={`${evidence.mandate_chain.length} hops`} bodyClassName="p-4">
        <div className="flex flex-col gap-2">
          {evidence.mandate_chain.map((node, index) => (
            <ChainNode key={node.mandate_id} node={node} last={index === evidence.mandate_chain.length - 1} />
          ))}
        </div>
      </Panel>

      <Panel
        title="Cart ↔ intent"
        hint={diff.summary}
        tone={diff.diverged ? "deny" : "default"}
        bodyClassName="p-4"
      >
        <div className="mb-3 grid grid-cols-3 gap-4">
          <Figure label="Signed intent" value={diff.intent_total} />
          <Figure label="Executed" value={diff.executed_total} tone={diff.diverged ? "deny" : "default"} />
          <div>
            <Eyebrow>Delta</Eyebrow>
            <div
              className={`num display-wide mt-1 text-[1.35rem] leading-none font-semibold ${
                diff.delta > 0 ? "text-deny" : "text-allow"
              }`}
            >
              {signedMoney(diff.delta)}
            </div>
          </div>
        </div>
        <Divider />
        <div className="mt-3 flex flex-col gap-1">
          {diff.added.length === 0 && diff.removed.length === 0 && diff.modified.length === 0 ? (
            <span className="text-pill text-allow">cart matches signed intent</span>
          ) : (
            <>
              {diff.added.map((line, index) => (
                <LineRow key={`a${index}`} sign="+" line={line} tone="deny" />
              ))}
              {diff.removed.map((line, index) => (
                <LineRow key={`r${index}`} sign="−" line={line} tone="muted" />
              ))}
              {diff.modified.map((change, index) => (
                <div key={`m${index}`} className="num text-pill text-stepup">
                  ~ {change.before.description}: {change.before.amount} → {change.after.amount}
                </div>
              ))}
            </>
          )}
        </div>
        <div className="num mt-3 flex flex-col gap-0.5 text-pill text-ink-4">
          <span>intent hash {decision.intent_hash}</span>
          <span>cart hash&nbsp;&nbsp; {decision.cart_hash}</span>
        </div>
      </Panel>

      <ProofPanel proof={evidence.inclusion_proof} evidence={evidence} />

      <Panel title="Decision chain" hint={`${evidence.decision_chain.length} ledger entries`} bodyClassName="p-4">
        <div className="flex flex-col gap-1">
          {evidence.decision_chain.slice(-8).map((entry) => (
            <div key={entry.seq} className="flex items-baseline gap-2.5 text-pill">
              <span className="num w-8 shrink-0 text-right text-ink-4">#{entry.seq}</span>
              <span className="num w-24 shrink-0 break-words text-ink-2">{entry.kind}</span>
              <span className="hash min-w-0 flex-1 break-words text-pill">
                {entry.entry_hash}
              </span>
            </div>
          ))}
        </div>
        <Caveat>
          Cumulative-budget and velocity caveats are evaluated against the store at decision time
          and are not offline-verifiable. Only structural caveats — amount, merchant, MCC,
          category, geo, expiry — verify from the credential alone.
        </Caveat>
      </Panel>
    </div>
  );
}

// ---------------------------------------------------------------------------------------

function ProofPanel({
  proof,
  evidence,
}: {
  proof: InclusionProof | null;
  evidence: EvidencePackage;
}) {
  const [local, setLocal] = useState<LocalProofResult | null>(null);

  useEffect(() => {
    let live = true;
    setLocal(null);
    if (proof) {
      verifyInclusionProof(proof).then((result) => {
        if (live) setLocal(result);
      });
    }
    return () => {
      live = false;
    };
  }, [proof]);

  if (!proof) {
    return (
      <Panel title="Merkle inclusion proof" bodyClassName="p-4">
        <span className="text-pill text-ink-4">no proof available for this entry</span>
      </Panel>
    );
  }

  return (
    <Panel
      title="Merkle inclusion proof"
      hint={`leaf ${proof.index} of ${proof.tree_size}`}
      tone="proof"
      bodyClassName="p-4 flex flex-col gap-3"
    >
      <div className="grid grid-cols-2 gap-3">
        <VerifyBox
          label="Server"
          ok={evidence.proof_verified}
          note={evidence.chain_verified ? "hash chain intact" : (evidence.chain_error ?? "chain broken")}
        />
        <VerifyBox
          label="This console, independently"
          ok={local?.ok ?? false}
          pending={local === null}
          note={
            local === null
              ? "recomputing root…"
              : local.ok
                ? "recomputed root matches"
                : (local.error ?? "recomputed root differs")
          }
        />
      </div>

      <div className="flex flex-col gap-1">
        <Eyebrow>Audit path</Eyebrow>
        <div className="num flex flex-col gap-0.5 text-pill">
          <div className="flex gap-2">
            <span className="w-12 shrink-0 text-ink-4">leaf</span>
            <span className="hash min-w-0 flex-1 break-words">{proof.leaf_hash}</span>
          </div>
          {proof.path.map((step, index) => (
            <div key={index} className="flex gap-2">
              <span className="w-12 shrink-0 text-ink-4">{step.side}</span>
              <span className="hash min-w-0 flex-1 break-words">{step.hash}</span>
            </div>
          ))}
          <div className="flex gap-2 pt-1">
            <span className="w-12 shrink-0 text-ink-4">root</span>
            <span className="hash min-w-0 flex-1 break-words">{proof.root}</span>
          </div>
        </div>
      </div>
      <Caveat>
        O(log n) audit path. Verifying one decision does not require access to the rest of the
        log, which is what makes this shippable inside a dispute packet.
      </Caveat>
    </Panel>
  );
}

function VerifyBox({
  label,
  ok,
  note,
  pending,
}: {
  label: string;
  ok: boolean;
  note: string;
  pending?: boolean;
}) {
  return (
    <div
      className={`flex flex-col gap-1 rounded border px-3 py-2 ${
        pending ? "border-line" : ok ? "border-allow/35 bg-allow-wash" : "border-deny/50 bg-deny-wash"
      }`}
    >
      <Eyebrow>{label}</Eyebrow>
      <div className="flex items-center gap-2">
        {!pending && <Check ok={ok} />}
        <span
          className={`num text-[0.78rem] font-medium ${
            pending ? "text-ink-3" : ok ? "text-allow" : "text-deny"
          }`}
        >
          {pending ? "…" : ok ? "VERIFIED" : "FAILED"}
        </span>
      </div>
      <span className="text-pill text-ink-4">{note}</span>
    </div>
  );
}

function ChainNode({ node, last }: { node: MandateNode; last: boolean }) {
  return (
    <div className="flex gap-3">
      <div className="flex w-5 shrink-0 flex-col items-center">
        <span className="num flex h-5 w-5 items-center justify-center rounded-full border border-line-2 text-pill text-ink-3">
          {node.depth}
        </span>
        {!last && <span className="w-px flex-1 bg-line-2" />}
      </div>
      <div className="min-w-0 flex-1 pb-2">
        <div className="flex items-baseline gap-2">
          <span className="break-words text-body font-medium">{node.holder}</span>
          <span className="num text-pill text-ink-4">{node.mandate_id}</span>
        </div>
        <div className="num text-pill leading-[1.5] text-ink-2">
          {node.scope_display.join("  ·  ")}
        </div>
      </div>
    </div>
  );
}

function Figure({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: number;
  tone?: "default" | "deny";
}) {
  return (
    <div>
      <Eyebrow>{label}</Eyebrow>
      <Money
        minorUnits={value}
        tone={tone}
        className="display-wide mt-1 block text-[1.35rem] leading-none font-semibold"
      />
    </div>
  );
}

function LineRow({
  sign,
  line,
  tone,
}: {
  sign: string;
  line: { description: string; amount: number; mcc: number };
  tone: "deny" | "muted";
}) {
  return (
    <div className="flex items-baseline gap-2 text-pill">
      <span className={`num w-3 shrink-0 ${tone === "deny" ? "text-deny" : "text-ink-4"}`}>
        {sign}
      </span>
      <span className={`min-w-0 flex-1 break-words ${tone === "deny" ? "text-deny" : "text-ink-3"}`}>
        {line.description}
      </span>
      <span className="num shrink-0 text-pill text-ink-4">MCC {line.mcc}</span>
      <Money
        minorUnits={line.amount}
        tone={tone === "deny" ? "deny" : "muted"}
        className="w-24 shrink-0 text-right text-pill"
      />
    </div>
  );
}
