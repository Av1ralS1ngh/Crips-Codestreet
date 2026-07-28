/**
 * Beat 02 — the Decision Receipt.
 *
 * The full candidate set, not just the winner: every instrument the agent considered, what
 * each was worth, how many assignments carried that figure, and whether it survives an
 * independent check in this browser.
 *
 * The screen is built around one line drawn across the middle of it. Above the line are
 * facts an issuer signed: earn rates, balances, caps, protections, per manifest. Below the
 * line is everything the issuer did not sign — the valuation policy, the criterion, the
 * ranking, the comparison. That boundary is not a caption; it is the answer to "you want us
 * to fund the commoditisation of the thing we charge for". The issuer never signs "we beat
 * them on this cart", so the corpus contains no issuer-signed assertion that a competitor
 * won.
 */

import { BoundaryRule, Seal } from "../components/ui";
import {
  BeatHeader,
  BeatPage,
  BeatSplit,
  HashRow,
  PlumblineUnavailable,
  ShowsPanel,
  SquarePanel,
  Takeaway,
} from "../components/plumblineUi";
import { firstSentence, money } from "../lib/format";
import { manifestFor, useConsole } from "../lib/store";
import { verifyWitness } from "../lib/witness";
import type { InstrumentValuation, PlumblineState, UnpricedEntry } from "../lib/plumbline";

/** The three states a candidate can be in on a receipt. Never inlined at a call site. */
const STATUS_CHOSEN = "CHOSEN";
const STATUS_PRICED = "PRICED";
const STATUS_UNPRICED = "UNPRICED";

const CANDIDATE_GRID =
  "grid grid-cols-[36px_minmax(0,2fr)_1fr_1fr_minmax(0,132px)_96px] items-baseline gap-4 px-[22px]";

export function ReceiptView() {
  const plumbline = useConsole((s) => s.plumbline);
  const plumblineError = useConsole((s) => s.plumblineError);

  if (!plumbline) return <PlumblineUnavailable error={plumblineError} />;

  const receipt = plumbline.receipt;
  const signed = receipt.candidates.filter((c) => c.issuer_signed);
  const signedTerms = signed.reduce(
    (sum, c) => sum + (manifestFor(plumbline, c.instrument_id)?.benefits.length ?? 0),
    0,
  );
  const keyIds = [...new Set(signed.map((c) => c.key_id).filter(Boolean))];
  const cartMinor = receipt.cart.lines.reduce((sum, line) => sum + line.amount, 0);
  const priced = receipt.candidates.filter((c) => statusOf(c, receipt.selected) !== STATUS_UNPRICED);

  return (
    <BeatPage>
      <BeatHeader
        beat="02"
        label="Receipt"
        title="The receipt names everything it considered."
        meta={`ledger #${receipt.ledger_seq} of ${receipt.ledger_size} · cart ${money(cartMinor)} · ${receipt.cart.lines.length} lines`}
      >
        Not just the winner. Issuer-signed facts, the stated criterion, and the full candidate
        set with a realised value for each, so a loser can be audited as easily as a winner.
      </BeatHeader>

      <BeatSplit
        aside={
          <ShowsPanel
            points={[
              <>
                A receipt that lists only the winner cannot be contested. Listing the losers is
                what makes the ranking falsifiable.
              </>,
              <>
                Considered-but-unpriced is recorded too. Silence about a benefit is itself a
                claim, and it is now on the record.
              </>,
              <>
                The issuer signs facts and never signs a comparison. The valuation policy and
                the ranking are the cardholder's; their hash is recorded, not endorsed.
              </>,
            ]}
            footnote={
              <>
                Signatures are HMAC-SHA256 under prototype keys; production signs with the
                issuer's HSM key. The canonicalisation and the verification flow are the part
                the argument rests on, and they are unchanged.
              </>
            }
          />
        }
      >
        {/* ------------------------------------------------------ above the boundary */}
        <div className="grid grid-cols-2 gap-px border border-gray-03 bg-gray-03">
          <div className="flex flex-col gap-2.5 bg-white px-6 py-[22px]">
            <span className="colhead">Issuer-signed facts</span>
            <span className="text-body text-ink">
              {signed.length} of {receipt.candidates.length} manifests issuer-signed ·{" "}
              {signedTerms} benefit terms
            </span>
            <div className="flex flex-wrap items-center gap-2 pt-0.5">
              <Seal signed keyId={keyIds[0] ?? null} />
            </div>
            <span className="break-words text-code text-ink-4">
              Earn rates, balances, caps, protections and eligibility. The rest are the agent's
              own model of published terms and carry no issuer signature.
            </span>
          </div>
          <div className="flex flex-col gap-2.5 bg-white px-6 py-[22px]">
            <span className="colhead">Stated decision criterion</span>
            <span className="break-words text-body text-ink">{receipt.criterion}</span>
            <div className="flex flex-wrap items-center gap-2 pt-0.5">
              <Seal signed={false} />
            </div>
            <span className="break-words text-code text-ink-4" title={receipt.policy.note}>
              {firstSentence(receipt.policy.note)}
            </span>
          </div>
        </div>

        <BoundaryRule>signature boundary; nothing below this line is issuer-endorsed</BoundaryRule>

        {/* ------------------------------------------------------ below the boundary */}
        <SquarePanel
          title="Candidate set"
          right={
            <span className="num text-pill font-normal text-ink-4">
              {priced.length} instruments priced ·{" "}
              {receipt.candidates.length - priced.length} considered, unpriced
            </span>
          }
        >
          <div className={`${CANDIDATE_GRID} border-b border-gray-02 py-3.5`}>
            <span className="colhead">#</span>
            <span className="colhead">Instrument</span>
            <span className="colhead text-right">Realised</span>
            <span className="colhead text-right">Assignments</span>
            <span className="colhead text-right">Re-checked</span>
            <span className="colhead text-right">Verdict</span>
          </div>

          {receipt.candidates.map((candidate) => (
            <CandidateRow key={candidate.instrument_id} plumbline={plumbline} candidate={candidate} />
          ))}
        </SquarePanel>

        {/* Hashes, so the object above can be pointed at rather than described. The entry
            hash is what the log witnessed; the candidate set is inside it, which is what
            makes an edited set detectable on beat 03. */}
        <div className="flex flex-col gap-4 border border-gray-03 bg-gray-01 px-6 py-5">
          <div className="grid grid-cols-2 gap-x-10 gap-y-3">
            <HashRow label="receipt entry sha256" value={receipt.entry_hash} />
            <HashRow label="log root" value={receipt.ledger_root} />
            <HashRow label="cart sha256" value={receipt.cart_hash} />
            <HashRow label="policy sha256" value={receipt.policy.policy_hash} />
          </div>
          {/* Identifiers, not digests: an id abbreviated head…tail is unrecoverable and
              therefore useless to a counterparty, so these render whole. */}
          <div className="num flex flex-wrap gap-x-8 gap-y-1.5 border-t border-gray-03 pt-3.5 text-code text-ink-4">
            <span className="break-words">receipt {receipt.receipt_id}</span>
            <span className="break-words">policy {receipt.policy.policy_id}</span>
          </div>
        </div>

        <UnpricedPanel candidates={receipt.candidates} />
      </BeatSplit>

      <Takeaway>If the losers are not named, the winner cannot be checked.</Takeaway>
    </BeatPage>
  );
}

// ---------------------------------------------------------------------------------------

/**
 * A candidate is UNPRICED when the agent declined to put an integer on it at all — no
 * witness, no assignments. That is a state the receipt records rather than hides: silence
 * about an instrument is a claim, and an unpriced row says which claim it was.
 */
function statusOf(candidate: InstrumentValuation, selected: string): string {
  if (candidate.instrument_id === selected) return STATUS_CHOSEN;
  if (candidate.witness.assignments.length === 0 && candidate.asserted_minor === 0) {
    return STATUS_UNPRICED;
  }
  return STATUS_PRICED;
}

function CandidateRow({
  plumbline,
  candidate,
}: {
  plumbline: PlumblineState;
  candidate: InstrumentValuation;
}) {
  const receipt = plumbline.receipt;
  const status = statusOf(candidate, receipt.selected);
  const chosen = status === STATUS_CHOSEN;
  const unpriced = status === STATUS_UNPRICED;

  // Deliberately keyed on the candidate's own instrument rather than on the manifest the
  // witness names. Those are the same thing only if the witness is honest, which is the
  // question, so letting the witness pick its own manifest would make the binding check
  // unfalsifiable.
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
    <div
      className={`${CANDIDATE_GRID} border-b border-gray-02 py-[18px] last:border-b-0 ${
        chosen ? "bg-blue-row" : unpriced ? "bg-[#FAFBFC]" : "bg-white"
      }`}
    >
      <span className={`num text-code ${chosen ? "text-blue" : "text-ink-4"}`}>
        {candidate.rank ?? "—"}
      </span>

      <div className="flex min-w-0 flex-col gap-1">
        <span
          className={`break-words text-data ${
            chosen ? "font-bold text-navy" : unpriced ? "text-ink-3" : "text-ink"
          }`}
        >
          {candidate.product}
        </span>
        <span className="num break-words text-pill font-normal text-ink-4">
          {candidate.issuer} · {candidate.issuer_signed ? "issuer-signed" : "not issuer-signed"}
        </span>
      </div>

      <span
        className={`num text-right text-body ${
          chosen ? "font-semibold text-blue" : unpriced ? "text-ink-4" : "text-navy"
        }`}
      >
        {unpriced ? "—" : candidate.asserted_display}
      </span>

      <span className="num text-right text-code text-ink-2">
        {candidate.witness.assignments.length}
      </span>

      {/* The VerifyPair rule, at table scale: the evaluator's verdict and this browser's own
          recomputation always render together. A single tick would be the claim "you can
          check this yourself" made by a cell that did not. */}
      <div className="flex flex-col items-end gap-1">
        <span className="num text-pill font-normal text-ink-4">
          evaluator{" "}
          <span className={candidate.verification.supports_assertion ? "text-success" : "text-warning"}>
            {candidate.verification.supports_assertion ? "VERIFIED" : "REJECTED"}
          </span>
        </span>
        <span className="num break-words text-pill font-normal text-ink-4">
          this console{" "}
          <span className={local?.supportsAssertion ? "text-success" : "text-warning"}>
            {local === null ? "NO MANIFEST" : local.supportsAssertion ? "VERIFIED" : "REJECTED"}
          </span>
        </span>
      </div>

      <span
        className={`num text-right text-pill font-normal tracking-[0.06em] ${
          chosen ? "text-success" : "text-ink-4"
        }`}
      >
        {status}
      </span>
    </div>
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
    <SquarePanel
      title="Considered but unpriced"
      right={
        <span className="num text-pill font-normal text-ink-4">
          CONSIDERED_BUT_UNPRICED · {entries.length} entries
        </span>
      }
    >
      <div className="grid grid-cols-2 gap-px bg-gray-03">
        {entries.map(({ instrument, entry }) => (
          <div
            key={`${instrument}-${entry.benefit_id}`}
            className="flex min-w-0 flex-col gap-1.5 bg-white px-[22px] py-4"
          >
            {/* Stacked, not side-by-side: a non-shrinking mono product name in a half-width
                cell starves the label column until break-words chops it one letter a line. */}
            <div className="flex min-w-0 flex-col gap-0.5">
              <span className="break-words text-data text-ink">{entry.label}</span>
              <span className="num text-pill font-normal text-ink-4">{instrument}</span>
            </div>
            {/* The fixture notes run 100-480 characters because they are the terms as the
                engine models them. The first sentence identifies the benefit; the full text
                stays on the title attribute rather than in the layout. */}
            <span className="break-words text-code text-ink-4" title={entry.note}>
              {firstSentence(entry.note)}
            </span>
          </div>
        ))}
      </div>
      <div className="border-t border-gray-03 bg-gray-01 px-[22px] py-4 text-code text-ink-4">
        The integer never claims to be the whole worth of the card. The receipt proves the
        agent saw these; no number claims they are worth zero.
      </div>
    </SquarePanel>
  );
}
