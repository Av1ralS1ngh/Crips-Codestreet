/**
 * Beat 02 — the Decision Receipt.
 *
 * The full candidate set, not just the winner: every instrument the agent considered, what
 * each was worth, and how many assignments carried that figure.
 *
 * The criterion and the issuer-signed facts sit side by side because they are the two
 * halves of the signing boundary: the issuer signs earn rates, balances, caps and
 * protections; the criterion and the ranking below it are the cardholder's, recorded by
 * hash and endorsed by nobody.
 */

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
import { manifestFor, useConsole } from "../lib/store";
import type { InstrumentValuation } from "../lib/plumbline";

/** The three states a candidate can be in on a receipt. Never inlined at a call site. */
const STATUS_CHOSEN = "CHOSEN";
const STATUS_PRICED = "PRICED";
const STATUS_UNPRICED = "UNPRICED";

const CANDIDATE_GRID =
  "grid grid-cols-[36px_minmax(0,2fr)_1fr_1fr_96px] items-baseline gap-4 px-[22px]";

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/** UTC, not local: the same fixture must print the same date on every machine that replays it. */
function signedOn(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

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
  const signedAt = signed.reduce(
    (latest, c) => Math.max(latest, manifestFor(plumbline, c.instrument_id)?.issued_at ?? 0),
    0,
  );
  const priced = receipt.candidates.filter((c) => statusOf(c, receipt.selected) !== STATUS_UNPRICED);

  return (
    <BeatPage>
      <BeatHeader beat="02" label="Receipt" title="The receipt names everything it considered.">
        Not just the winner. Issuer-signed facts, the stated criterion, and the full candidate
        set with a realised value for each — so a loser can be audited as easily as a winner.
      </BeatHeader>

      <BeatSplit
        aside={
          <ShowsPanel
            points={[
              <>
                A receipt that lists only the winner cannot be contested. Listing losers is what
                makes the ranking falsifiable.
              </>,
              <>
                Considered-but-unpriced is recorded too. Silence about a card is itself a claim,
                and it is now on the record.
              </>,
              // The design's third statement read "Every field here is signed by the issuer
              // or derived from signed fields." That is false on this system and refutes the
              // deck's own signature boundary: the criterion and the ranking are the
              // cardholder's, hashed but endorsed by nobody. Same rhythm, true words.
              <>
                The issuer signs the facts. The criterion and the ranking are the
                cardholder's — recorded by hash, endorsed by nobody.
              </>,
            ]}
          />
        }
      >
        <div className="grid grid-cols-2 gap-px border border-gray-03 bg-gray-03">
          <div className="flex flex-col gap-2.5 bg-white px-6 py-[22px]">
            <span className="colhead">Stated decision criterion</span>
            <span className="break-words text-body text-ink">{receipt.criterion}</span>
          </div>
          <div className="flex flex-col gap-2.5 bg-white px-6 py-[22px]">
            <span className="colhead">Issuer-signed facts</span>
            <span className="break-words text-body text-ink">
              {signed.length} manifests · {signedTerms} benefit terms
              {signedAt > 0 ? ` · signed ${signedOn(signedAt)}` : ""}
            </span>
          </div>
        </div>

        <SquarePanel
          title="Candidate set"
          right={
            <span className="num text-pill font-normal text-ink-4">
              {priced.length} instruments priced · {receipt.candidates.length - priced.length}{" "}
              considered, unpriced
            </span>
          }
        >
          <div className={`${CANDIDATE_GRID} border-b border-gray-02 py-3.5`}>
            <span className="colhead">#</span>
            <span className="colhead">Instrument</span>
            <span className="colhead text-right">Realised</span>
            <span className="colhead text-right">Assignments</span>
            <span className="colhead text-right">Verdict</span>
          </div>

          {receipt.candidates.map((candidate) => (
            <CandidateRow
              key={candidate.instrument_id}
              candidate={candidate}
              selected={receipt.selected}
            />
          ))}
        </SquarePanel>

        {/* The entry hash is what the log witnessed; the candidate set is inside it, which is
            what makes an edited set detectable on beat 03. */}
        <div className="flex flex-col gap-3 border border-gray-03 bg-gray-01 px-6 py-5">
          <HashRow label="receipt entry sha256" value={receipt.entry_hash} />
          <HashRow label="log root" value={receipt.ledger_root} />
        </div>
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
  candidate,
  selected,
}: {
  candidate: InstrumentValuation;
  selected: string;
}) {
  const status = statusOf(candidate, selected);
  const chosen = status === STATUS_CHOSEN;
  const unpriced = status === STATUS_UNPRICED;

  return (
    <div
      className={`${CANDIDATE_GRID} border-b border-gray-02 py-[18px] last:border-b-0 ${
        chosen ? "bg-blue-row" : unpriced ? "bg-[#FAFBFC]" : "bg-white"
      }`}
    >
      <span className={`num text-code ${chosen ? "text-blue" : "text-ink-4"}`}>
        {candidate.rank ?? "—"}
      </span>

      <span
        className={`min-w-0 break-words text-data ${
          chosen ? "font-bold text-navy" : unpriced ? "text-ink-3" : "text-ink"
        }`}
      >
        {candidate.product}
      </span>

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
