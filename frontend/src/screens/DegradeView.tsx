/**
 * Beat 05 — graceful degrade. The objection this answers is the one that ends the pitch if
 * it is left standing: "you have made Amex the only credential in a third-party checkout
 * that hard-fails." A platform facing a credential it cannot discharge does not comply, it
 * routes around — and routing around is the risk this system exists to reduce, self
 * administered.
 *
 * The answer is architectural rather than a setting, and the matrix is where it shows:
 * default posture is observe-only, a missing counterpart receipt is recorded and the
 * transaction proceeds, and the one denial is elected enforcement failing to discharge the
 * cardholder's OWN delegated authority. What is withheld instead of authorization is
 * coverage — no receipt, no Agent Purchase Protection. Coverage is conditioned on evidence;
 * authorization is not.
 *
 * Every row comes from `plumbline.scenarios.graceful_degrade`, in the run's own append
 * order. Reordering rows to group outcomes would misstate log_seq. Both matrix columns that
 * state an outcome are derived from the two booleans the engine returns — `proceeds` and
 * `coverage_eligible` — never from a string this console invented for a row. The columns are
 * fixed rather than fractional: the five cells collided at every fractional split.
 */

import { useEffect } from "react";
import { Button } from "../components/ui";
import {
  BeatHeader,
  BeatPage,
  BeatSplit,
  ShowsPanel,
  SquarePanel,
  Takeaway,
} from "../components/plumblineUi";
import { shortHash } from "../lib/format";
import { useConsole } from "../lib/store";
import {
  POSTURE_ENFORCE,
  REASON_SELECTION_ATTESTED,
  type DegradePass,
  type Posture,
} from "../lib/plumbline";

const OUTCOME_PROCEEDS = "PROCEEDS";
const OUTCOME_DEGRADED = "DEGRADED";
const OUTCOME_DENIED = "DENIED";

const WITHHELD_NOTHING = "nothing";
const WITHHELD_COVERAGE = "purchase protection";
const WITHHELD_AUTHORISATION = "authorisation";

const RECEIPT_VERIFIED = "present, verified";
const RECEIPT_PRESENT = "present";
const RECEIPT_ABSENT = "absent";

/** Fixed columns. Fractional ones collided: pass / receipt / posture / withheld / outcome. */
const MATRIX_COLS = "grid grid-cols-[52px_148px_104px_minmax(0,1fr)_112px] gap-5";

/** Two postures across four passes; the count is the run's, not a figure of speech. */
const STANDFIRST =
  "Enforcement is elected, not assumed. The same purchase runs four times against the two " +
  "postures a Card Member can elect, and what is withheld changes each time — but the log " +
  "records the absence either way.";

function outcomeOf(pass: DegradePass) {
  if (!pass.proceeds) {
    return {
      label: OUTCOME_DENIED,
      ink: "text-warning",
      row: "bg-warning-row",
      withheld: WITHHELD_AUTHORISATION,
    };
  }
  if (!pass.coverage_eligible) {
    return {
      label: OUTCOME_DEGRADED,
      ink: "text-attention-ink",
      row: "bg-attention-wash",
      withheld: WITHHELD_COVERAGE,
    };
  }
  return {
    label: OUTCOME_PROCEEDS,
    ink: "text-success",
    row: "bg-white",
    withheld: WITHHELD_NOTHING,
  };
}

function receiptOf(pass: DegradePass): string {
  if (!pass.counterpart_receipt) return RECEIPT_ABSENT;
  return pass.reason_code === REASON_SELECTION_ATTESTED ? RECEIPT_VERIFIED : RECEIPT_PRESENT;
}

export function DegradeView() {
  const degrade = useConsole((s) => s.degrade);
  const degradeError = useConsole((s) => s.degradeError);
  const running = useConsole((s) => s.degradeRunning);
  const run = useConsole((s) => s.runDegrade);

  useEffect(() => {
    if (!degrade && !running) void run();
  }, [degrade, running, run]);

  if (!degrade) {
    return (
      <BeatPage>
        <BeatHeader beat="05" label="Degrade" title="No receipt, and it still proceeds.">
          {STANDFIRST}
        </BeatHeader>
        <SquarePanel
          title="Graceful degrade"
          tone={degradeError ? "deny" : "plain"}
          right={
            <Button onClick={() => void run()} disabled={running} size="sm">
              {running ? "running…" : "run the four passes"}
            </Button>
          }
        >
          <div className="flex flex-col gap-2.5 px-[22px] py-[26px]">
            <span className="num text-code text-ink-4">
              POST /api/plumbline/scenario/graceful_degrade
            </span>
            <span className="text-body text-ink">
              {degradeError ?? (running ? "running the four passes…" : "not run yet")}
            </span>
            {degradeError && (
              <span className="text-card-title font-normal leading-[1.55] text-ink-2">
                The scenario runs on a fixed clock, so it can be re-run without changing a
                byte. Switch the header badge to RECORDED to drive the recorded run instead.
              </span>
            )}
          </div>
        </SquarePanel>
      </BeatPage>
    );
  }

  const data = degrade.data;
  const passes = data.passes;
  const denials = passes.filter((pass) => !pass.proceeds).length;
  const degradations = passes.filter(
    (pass) => pass.proceeds && !pass.coverage_eligible,
  ).length;

  return (
    <BeatPage>
      <BeatHeader beat="05" label="Degrade" title="No receipt, and it still proceeds.">
        {STANDFIRST}
      </BeatHeader>

      <BeatSplit
        aside={
          <ShowsPanel
            points={[
              "A governance layer that only works when everyone opts in is a governance layer nobody ships.",
              <>
                The Card Member elects the posture.{" "}
                <span className="num">observe_only</span> still records;{" "}
                <span className="num">enforce</span> is what turns the record into a
                condition.
              </>,
              "Degradation withholds the benefits that depend on a checkable claim — protection first, authority last.",
            ]}
          />
        }
      >
        {/* One child, so the column rhythm here is 24px rather than BeatSplit's 34px. */}
        <div className="flex min-w-0 flex-col gap-6">
          <SquarePanel title="Four passes, the whole matrix">
            <div
              className={`${MATRIX_COLS} items-baseline border-b border-gray-02 px-[22px] py-3.5`}
            >
              <span className="colhead">Pass</span>
              <span className="colhead">Receipt</span>
              <span className="colhead">Posture</span>
              <span className="colhead whitespace-nowrap">Withheld</span>
              <span className="colhead text-right">Outcome</span>
            </div>

            {passes.map((pass, index) => {
              const outcome = outcomeOf(pass);
              return (
                <div
                  key={pass.log_seq}
                  className={`${MATRIX_COLS} items-baseline px-[22px] py-[18px] ${
                    index < passes.length - 1 ? "border-b border-gray-02" : ""
                  } ${outcome.row}`}
                >
                  <span className="num text-code text-ink-4">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <span className="text-data font-normal whitespace-nowrap text-ink">
                    {receiptOf(pass)}
                  </span>
                  <PostureChip posture={pass.posture} />
                  <span className="text-data font-normal text-ink-3">{outcome.withheld}</span>
                  <span
                    className={`num text-right text-[0.71875rem] tracking-[0.06em] ${outcome.ink}`}
                  >
                    {outcome.label}
                  </span>
                </div>
              );
            })}
          </SquarePanel>

          <div className="grid grid-cols-2 gap-6">
            {/* "the two" is the run's own shape: two of the four passes carry no
                counterpart receipt. A run with a different shape needs this sentence
                changed, not the matrix. */}
            <div className="flex min-w-0 flex-col gap-3 border border-gray-03 bg-white p-6">
              <span className="colhead text-blue">Read into the record</span>
              <p className="text-data font-normal leading-[1.6] text-ink">
                Every pass appends an entry before it returns, including the two where no
                receipt existed. Absence is a fact the log carries.
              </p>
            </div>

            <div className="flex min-w-0 flex-col gap-3 border border-gray-03 bg-white p-6">
              <span className="colhead text-blue">The log, after four passes</span>
              <p className="num min-w-0 break-words text-code leading-[1.7] text-ink-2">
                entries {passes.length} · denials {denials} · degradations {degradations}
                <br />
                <span title={data.signed_tree_head.root_hash}>
                  root {shortHash(data.signed_tree_head.root_hash, 8, 6)}
                </span>
              </p>
            </div>
          </div>
        </div>
      </BeatSplit>

      <Takeaway>Enforcement is elected. The record is not.</Takeaway>
    </BeatPage>
  );
}

// ---------------------------------------------------------------------------------------

/** The posture, as the engine spells it. 3px radius: the one non-square, non-pill shape. */
function PostureChip({ posture }: { posture: Posture }) {
  return (
    <span
      className={`num justify-self-start rounded-[3px] bg-gray-02 px-[9px] py-[3px] text-[0.71875rem] whitespace-nowrap uppercase ${
        posture === POSTURE_ENFORCE ? "text-navy" : "text-ink-3"
      }`}
    >
      {posture}
    </span>
  );
}
