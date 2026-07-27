/**
 * Beat 2 — the Decision Receipt.
 *
 * The full candidate set, not just the winner: every instrument the agent considered, what
 * each was worth, the witness hash behind each figure, and whether that figure survives an
 * independent check.
 *
 * The screen is built around one line, drawn across the middle of it. Above the line are
 * facts an issuer signed: earn rates, balances, caps, protections, per manifest, with the
 * content hash and the key id. Below the line is everything the issuer did not sign — the
 * valuation policy, the criterion, the ranking, the comparison. That boundary is not a
 * caption; it is the answer to "you want us to fund the commoditisation of the thing we
 * charge for". The issuer never signs "we beat them on this cart", so the corpus contains
 * no issuer-signed assertion that a competitor won.
 */

import { BoundaryRule, Caveat, Panel, Seal, SignedRegion } from "../components/ui";
import { HashRow, ScreenHeader, PlumblineUnavailable } from "../components/plumblineUi";
import { firstSentence, money, shortHash } from "../lib/format";
import { manifestFor, useConsole } from "../lib/store";
import { verifyWitness } from "../lib/witness";
import type { InstrumentValuation, UnpricedEntry, PlumblineState } from "../lib/plumbline";

export function ReceiptView() {
  const plumbline = useConsole((s) => s.plumbline);
  const plumblineError = useConsole((s) => s.plumblineError);

  if (!plumbline) return <PlumblineUnavailable error={plumblineError} />;

  const receipt = plumbline.receipt;
  const signedCount = receipt.candidates.filter((c) => c.issuer_signed).length;

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <ScreenHeader
        beat="02"
        title="The receipt names everything it considered."
        right={
          <div className="num flex items-baseline gap-4 text-pill text-ink-4">
            <span>
              cart{" "}
              <span className="text-ink-2">
                {money(receipt.cart.lines.reduce((sum, l) => sum + l.amount, 0))}
              </span>{" "}
              · {receipt.cart.lines.length} lines · {receipt.cart.merchant}
            </span>
            <span>
              ledger <span className="text-ink-2">#{receipt.ledger_seq}</span> of{" "}
              <span className="text-ink-2">{receipt.ledger_size}</span>
            </span>
            <span className="hash">{shortHash(receipt.entry_hash, 10, 6)}</span>
          </div>
        }
      >
        Every candidate, its derivation, the criterion that ranked them, and the value the
        agent refused to price.
      </ScreenHeader>

      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pr-1">
        {/* ------------------------------------------------------ above the boundary */}
        <SignedRegion
          signed
          title="Issuer-signed facts"
          owner="issuer"
          note="earn rates · balances · caps · protections · eligibility"
          className="shrink-0"
        >
          <div className="grid grid-cols-4 gap-2.5 p-3.5">
            {receipt.candidates
              .filter((c) => c.issuer_signed)
              .map((candidate) => (
                <SignedFacts key={candidate.instrument_id} candidate={candidate} />
              ))}
            {receipt.candidates
              .filter((c) => !c.issuer_signed)
              .map((candidate) => (
                <UnsignedFacts key={candidate.instrument_id} candidate={candidate} />
              ))}
          </div>
          <div className="border-t border-line px-3.5 py-2">
            {/* The HSM sentence went. Both disclosures that matter stay: which manifests
                are signed, and that the signatures are prototype-key HMAC. */}
            <Caveat>
              {signedCount} of {receipt.candidates.length} manifests are issuer-signed; the
              rest are the agent's own model. Signatures use HMAC under prototype keys.
            </Caveat>
          </div>
        </SignedRegion>

        <BoundaryRule>signature boundary; nothing below this line is issuer-endorsed</BoundaryRule>

        {/* ------------------------------------------------------ below the boundary */}
        <SignedRegion
          signed={false}
          title="Valuation policy and ranking"
          owner="cardholder"
          note="hash recorded, not endorsed"
          className="shrink-0"
        >
          <div className="flex flex-col gap-2 p-3.5">
            <div className="rounded border border-line bg-sunken px-3 py-2">
              <div className="eyebrow mb-1">Stated decision criterion</div>
              <p className="num text-pill leading-relaxed text-ink">{receipt.criterion}</p>
            </div>
            <div className="grid grid-cols-2 gap-x-6 gap-y-1">
              <HashRow label="policy id" value={receipt.policy.policy_id} tone="muted" />
              <HashRow label="policy sha256" value={receipt.policy.policy_hash} />
              <HashRow label="cart sha256" value={receipt.cart_hash} />
              <HashRow label="receipt id" value={receipt.receipt_id} tone="muted" />
            </div>
            {/* 492 characters in the fixture; the sentence that matters is the first. */}
            <span title={receipt.policy.note}>
              <Caveat>{firstSentence(receipt.policy.note)}</Caveat>
            </span>
          </div>
        </SignedRegion>

        <Panel
          title="Candidate set"
          hint="ranked under the criterion above · every figure carries its witness"
          bodyClassName="overflow-x-auto"
          className="shrink-0"
        >
          <CandidateTable plumbline={plumbline} />
        </Panel>

        <UnpricedPanel candidates={receipt.candidates} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------

function SignedFacts({ candidate }: { candidate: InstrumentValuation }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded border border-proof/25 bg-proof-wash/40 px-3 py-2">
      <span className="break-words text-body font-medium text-ink">
        {candidate.issuer} {candidate.product}
      </span>
      <Seal signed keyId={candidate.key_id} />
      <HashRow label="manifest sha256" value={candidate.manifest_hash} />
      <HashRow label="signature" value={candidate.signature ?? "—"} />
      <span className="break-words text-pill text-ink-4">{candidate.manifest_source}</span>
    </div>
  );
}

function UnsignedFacts({ candidate }: { candidate: InstrumentValuation }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded border border-dashed border-line-2 px-3 py-2">
      <span className="break-words text-body font-medium text-ink-2">
        {candidate.issuer} {candidate.product}
      </span>
      <Seal signed={false} />
      <HashRow label="manifest sha256" value={candidate.manifest_hash} tone="muted" />
      <span className="break-words text-pill text-ink-4">{candidate.manifest_source}</span>
    </div>
  );
}

function CandidateTable({ plumbline }: { plumbline: PlumblineState }) {
  const receipt = plumbline.receipt;

  return (
    <table className="w-full min-w-[54rem] border-collapse text-pill">
      <thead>
        <tr>
          <th className="eyebrow border-b border-line px-2 py-2 text-right font-semibold">#</th>
          <th className="eyebrow border-b border-line px-2 py-2 text-left font-semibold">
            Instrument
          </th>
          <th className="eyebrow border-b border-line px-2 py-2 text-left font-semibold">
            Facts
          </th>
          <th className="eyebrow border-b border-line px-2 py-2 text-right font-semibold">
            Per-line sum
          </th>
          <th className="eyebrow border-b border-line px-2 py-2 text-right font-semibold">
            Witness-backed value
          </th>
          <th className="eyebrow border-b border-line px-2 py-2 text-left font-semibold">
            Witness
          </th>
          <th className="eyebrow border-b border-line px-2 py-2 text-left font-semibold">
            Re-checked here
          </th>
          <th className="eyebrow border-b border-line px-3 py-2 text-right font-semibold">
            Unpriced
          </th>
        </tr>
      </thead>
      <tbody>
        {receipt.candidates.map((candidate) => {
          const selected = candidate.instrument_id === receipt.selected;
          // Deliberately keyed on the candidate's own instrument rather than on the
          // manifest the witness names. Those are the same thing only if the witness is
          // honest, which is the question, so letting the witness pick its own manifest
          // would make the binding check unfalsifiable.
          const manifest = manifestFor(plumbline, candidate.instrument_id);
          const local = manifest
            ? verifyWitness({
                witness: candidate.witness,
                manifest,
                cart: receipt.cart,
                cartHash: plumbline.cart_hash,
                assertedMinor: candidate.asserted_minor,
              })
            : null;
          return (
            <tr
              key={candidate.instrument_id}
              className={`border-b border-line/60 ${selected ? "bg-allow-wash/40" : ""}`}
            >
              <td className="num px-2 py-2 text-right text-ink-4">{candidate.rank ?? "—"}</td>
              <td className="px-2 py-2">
                <div className="flex items-baseline gap-2">
                  <span
                    className={`break-words font-medium ${selected ? "text-allow" : "text-ink"}`}
                  >
                    {candidate.product}
                  </span>
                  {selected && (
                    <span className="shrink-0 rounded border border-allow/45 px-1 py-px font-mono text-pill tracking-[0.08em] text-allow uppercase">
                      selected
                    </span>
                  )}
                </div>
                <span className="num text-pill text-ink-4">{candidate.issuer}</span>
              </td>
              <td className="px-2 py-2">
                <Seal signed={candidate.issuer_signed} />
              </td>
              <td className="num px-2 py-2 text-right text-body text-deny/80">
                {candidate.reconciliation.naive_display}
              </td>
              <td className="num px-2 py-2 text-right text-body font-medium text-ink">
                {candidate.asserted_display}
              </td>
              <td className="px-2 py-2">
                <span className="hash text-pill" title={candidate.witness_hash}>
                  {shortHash(candidate.witness_hash, 10, 6)}
                </span>
                <div className="num text-pill text-ink-4">
                  {candidate.witness.assignments.length} assignments
                </div>
              </td>
              <td className="px-2 py-2">
                <span
                  className={`num text-pill ${
                    local?.supportsAssertion ? "text-allow" : "text-deny"
                  }`}
                >
                  {local?.supportsAssertion ? "VERIFIED" : "REJECTED"}
                </span>
                <div className="num text-pill text-ink-4">
                  {local === null
                    ? "no manifest for this instrument"
                    : local.supportsAssertion
                      ? "no solver, linear time"
                      : (local.failures[0]?.code ?? "")}
                </div>
              </td>
              <td className="num px-3 py-2 text-right text-ink-3">{candidate.unpriced.length}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function UnpricedPanel({ candidates }: { candidates: InstrumentValuation[] }) {
  const entries: { instrument: string; entry: UnpricedEntry }[] = [];
  for (const candidate of candidates) {
    for (const entry of candidate.unpriced) {
      entries.push({ instrument: candidate.product, entry });
    }
  }

  return (
    <Panel
      title="Considered but unpriced"
      hint="the integer never claims to be the whole worth of the card"
      className="shrink-0"
      bodyClassName="flex flex-col gap-2 p-3.5"
    >
      <div className="grid grid-cols-3 gap-2">
        {entries.map(({ instrument, entry }) => (
          <div
            key={`${instrument}-${entry.benefit_id}`}
            className="flex min-w-0 flex-col gap-0.5 rounded border border-line bg-sunken px-2.5 py-1"
          >
            <div className="flex items-baseline gap-2">
              <span className="min-w-0 break-words text-pill text-ink-2">{entry.label}</span>
              <span className="num ml-auto shrink-0 text-pill text-ink-4">{instrument}</span>
            </div>
            <span className="break-words text-pill text-ink-4" title={entry.note}>
              {firstSentence(entry.note)}
            </span>
          </div>
        ))}
      </div>
      <Caveat>
        Declared CONSIDERED_BUT_UNPRICED. The receipt proves the agent saw them; no integer
        claims they are worth zero.
      </Caveat>
    </Panel>
  );
}
