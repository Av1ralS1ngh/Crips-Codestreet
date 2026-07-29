/**
 * Beat 03 — omission leaves a signature.
 *
 * The attack is not a forged number. It is a card that never appears: a platform that
 * quietly drops an instrument from the candidate set, and serves one log to the issuer and
 * another to the cardholder. Nothing in a single receipt catches that, because the receipt
 * the cardholder sees is internally consistent.
 *
 * What catches it is that the two sets do not hash to the same log root. The receipt the
 * log recorded carries every candidate; the set the platform served is one short, so the
 * root it can offer is not the root the log holds.
 */

import {
  BeatHeader,
  BeatPage,
  BeatSplit,
  PlumblineUnavailable,
  ShowsPanel,
  SquarePanel,
  Takeaway,
} from "../components/plumblineUi";
import { money, shortHash } from "../lib/format";
import { useConsole } from "../lib/store";
import type { InstrumentValuation } from "../lib/plumbline";

/** Counts read as prose in a standfirst; anything larger falls back to the numeral. */
const COUNT_WORDS = [
  "zero",
  "one",
  "two",
  "three",
  "four",
  "five",
  "six",
  "seven",
  "eight",
  "nine",
  "ten",
];

function countWord(n: number): string {
  return COUNT_WORDS[n] ?? String(n);
}

export function OmissionView() {
  const plumbline = useConsole((s) => s.plumbline);
  const plumblineError = useConsole((s) => s.plumblineError);

  const omission = plumbline?.omission ?? null;

  if (!plumbline || !omission) return <PlumblineUnavailable error={plumblineError} />;

  const dropped = omission.omitted_instrument;
  // The manifest id is what the log records; the product name is what a room reads.
  const names = new Map(plumbline.instruments.map((i) => [i.instrument_id, i.product]));
  const ranked = new Map(plumbline.receipt.candidates.map((c) => [c.instrument_id, c]));

  const servedBest = bestOf(omission.candidates_served, ranked);
  // What the omission cost: the dropped card against the winner of the set that survived it.
  // Zero when the dropped card would not have won anyway — the record still changed.
  const costMinor = Math.max(0, dropped.asserted_minor - (servedBest?.asserted_minor ?? 0));

  return (
    <BeatPage>
      <BeatHeader
        beat="03"
        label="Omission"
        title="A card that never appears still leaves a signature."
      >
        The platform published {countWord(omission.candidates_published.length)} candidates and
        served {countWord(omission.candidates_served.length)}. The set hash in the receipt does
        not match the set the log recorded, and that mismatch is detectable without trusting
        either party.
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
              <>Nobody has to be accused of anything. The two hashes simply do not agree.</>,
            ]}
          />
        }
      >
        <div className="grid grid-cols-2 gap-6">
          <SquarePanel title="Candidate set in the published receipt">
            <div className="flex flex-col py-2">
              {omission.candidates_published.map((id) => (
                <CandidateRow key={id} name={names.get(id) ?? id} dropped={false} />
              ))}
            </div>
            <SetHash value={omission.head_b.root_hash} tone="proof" />
          </SquarePanel>

          <SquarePanel title="Candidate set the platform served">
            <div className="flex flex-col py-2">
              {omission.candidates_published.map((id) => (
                <CandidateRow
                  key={id}
                  name={names.get(id) ?? id}
                  dropped={!omission.candidates_served.includes(id)}
                />
              ))}
            </div>
            <SetHash value={omission.head_b_edited.root_hash} tone="deny" />
          </SquarePanel>

          <div className="col-span-2 grid grid-cols-2 gap-px border border-gray-03 bg-gray-03">
            <div className="flex flex-col gap-2.5 bg-white p-6">
              <span className="colhead text-warning">Verdict</span>
              <span className="num text-[1.1875rem] font-semibold text-warning">SET MISMATCH</span>
              <span className="text-card-title font-normal leading-relaxed text-ink-2">
                The receipt's candidate-set hash is not the hash of the set the transparency log
                witnessed.
              </span>
            </div>
            <div className="flex flex-col gap-2.5 bg-white p-6">
              <span className="colhead text-blue">Cost of the omission</span>
              <span className="num text-[1.1875rem] font-semibold text-navy">
                {money(costMinor)}
              </span>
              <span className="text-card-title font-normal leading-relaxed text-ink-2">
                The dropped card realises {dropped.asserted_display} here. The winner of the
                set that was served realises {servedBest?.asserted_display ?? "nothing"}.
              </span>
            </div>
          </div>
        </div>
      </BeatSplit>

      <Takeaway>
        If it is not in the ledger, it did not happen — and if it was, deleting it shows.
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

function CandidateRow({ name, dropped }: { name: string; dropped: boolean }) {
  if (!dropped) {
    return <div className="break-words px-[22px] py-3.5 text-data text-ink">{name}</div>;
  }
  return (
    <div className="flex items-baseline justify-between gap-3 border-l-[3px] border-warning bg-warning-row py-3.5 pr-[22px] pl-[19px]">
      <span className="min-w-0 break-words text-data font-bold text-warning">{name}</span>
      <span className="num shrink-0 text-pill font-normal tracking-[0.12em] text-warning">
        DROPPED
      </span>
    </div>
  );
}

/**
 * The log root each set can offer. Labelled for what the fixture holds — a signed tree head
 * — rather than for a standalone set digest this corpus does not carry.
 */
function SetHash({ value, tone }: { value: string; tone: "proof" | "deny" }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-t border-gray-03 px-[22px] py-4">
      <span className="colhead">Log root</span>
      <span
        className={`num shrink-0 text-code ${tone === "deny" ? "text-warning" : "text-blue"}`}
        title={value}
      >
        {shortHash(value, 8, 6)}
      </span>
    </div>
  );
}
