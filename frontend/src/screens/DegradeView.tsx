/**
 * Beat 5 — graceful degrade. The objection this answers is the one that ends the pitch if
 * it is left standing: "you have made Amex the only credential in a third-party checkout
 * that hard-fails." A platform facing a credential it cannot discharge does not comply, it
 * routes around — and routing around is the risk this system exists to reduce, self
 * administered.
 *
 * The answer is architectural rather than a setting, and the screen has to carry the
 * architecture, not the setting:
 *
 *   * The receipt obligation is a caveat on the mandate the Card Member issues to THEIR
 *     OWN AGENT. The platform is asked for nothing and never sees the check.
 *   * Default posture is observe-only. A missing counterpart receipt is recorded as an
 *     unattested selection and the transaction proceeds. A corpus with no record of its
 *     own holes cannot be used to reason about coverage.
 *   * The one denial in the matrix is elected enforcement, and what fails is the agent
 *     failing to discharge the cardholder's own delegated authority — architecturally
 *     identical to a spend limit denying, which is exactly what a governance layer is for.
 *
 * What is withheld instead of authorization is coverage: no receipt, no Agent Purchase
 * Protection. Coverage is conditioned on evidence; authorization is not.
 *
 * Every string on this screen comes from `plumbline.scenarios.graceful_degrade`. The
 * posture control re-points the screen at rows the backend already computed; it does not
 * recompute anything, because nothing here is the console's to decide. The matrix columns
 * are fixed rather than fractional: the five cells collided at every fractional split the
 * handoff tried.
 */

import { useEffect, useState } from "react";
import { Button, ReasonCode } from "../components/ui";
import {
  BeatHeader,
  BeatPage,
  BeatSplit,
  HashRow,
  ShowsPanel,
  SquarePanel,
  Takeaway,
} from "../components/plumblineUi";
import { currencyMoney, shortHash } from "../lib/format";
import { useConsole } from "../lib/store";
import {
  POSTURE_ENFORCE,
  POSTURE_OBSERVE_ONLY,
  REASON_DISCLOSURE_CAVEAT_UNDISCHARGED,
  REASON_SELECTION_ATTESTED,
  REASON_UNATTESTED_SELECTION,
  type DegradeData,
  type DegradePass,
  type Posture,
} from "../lib/plumbline";

const POSTURE_COPY: Record<Posture, { title: string; sub: string; elected: string }> = {
  [POSTURE_OBSERVE_ONLY]: {
    title: "Observe only",
    sub: "the default",
    elected: "The evaluator computes, signs and records. Nothing is ever refused here.",
  },
  [POSTURE_ENFORCE]: {
    title: "Enforce",
    sub: "elected by the Card Member",
    elected:
      "The Card Member has required the disclosure their own mandate asks for. The check " +
      "runs against their agent.",
  },
};

/** What each outcome is, in one line, keyed on the code rather than on a boolean. */
const REASON_COPY: Record<string, string> = {
  [REASON_SELECTION_ATTESTED]: "the receipt discharges the caveat",
  [REASON_UNATTESTED_SELECTION]: "recorded as a hole in the corpus, not as a decline",
  [REASON_DISCLOSURE_CAVEAT_UNDISCHARGED]: "the cardholder's own delegation did not discharge",
};

/**
 * The matrix vocabulary. Both columns are derived from the two booleans the engine returns
 * — `proceeds` and `coverage_eligible` — never from a string the console invented for a
 * row. A pass that proceeds without coverage is the whole point of the beat: the
 * transaction lands, and what is withheld is the protection that depends on evidence.
 */
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

const passKey = (pass: DegradePass) => `${pass.posture}:${pass.counterpart_receipt}`;

export function DegradeView() {
  const degrade = useConsole((s) => s.degrade);
  const degradeError = useConsole((s) => s.degradeError);
  const running = useConsole((s) => s.degradeRunning);
  const run = useConsole((s) => s.runDegrade);
  const mode = useConsole((s) => s.mode);

  const [posture, setPosture] = useState<Posture>(POSTURE_OBSERVE_ONLY);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    if (!degrade && !running) void run();
  }, [degrade, running, run]);

  if (!degrade) {
    return (
      <BeatPage>
        <BeatHeader beat="05" label="Degrade" title="No receipt, and it still proceeds.">
          Enforcement is elected, not assumed. The same purchase runs four times against the
          postures a Card Member can elect, and what is withheld changes each time; the log
          records the absence either way.
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
  const denied = passes.filter((pass) => !pass.proceeds);
  const rowPasses = passes.filter((pass) => pass.posture === posture);
  const active =
    passes.find((pass) => passKey(pass) === selected) ??
    rowPasses.find((pass) => !pass.counterpart_receipt) ??
    passes[0];

  return (
    <BeatPage>
      <BeatHeader
        beat="05"
        label="Degrade"
        title="No receipt, and it still proceeds."
        meta={
          <span className="inline-flex items-center gap-3">
            {mode === "live" ? "live scenario" : "recorded run"} · clock {degrade.clock}
            <Button onClick={() => void run()} disabled={running} size="sm">
              {running ? "running…" : "run again"}
            </Button>
          </span>
        }
      >
        {degrade.headline}
      </BeatHeader>

      <BeatSplit
        aside={
          <ShowsPanel
            points={degrade.notes}
            footnote={
              <>
                The caveat rides on <span className="num">{data.mandate.mandate_id}</span>,
                the mandate the Card Member issued to their own agent. No platform is asked
                for anything, and no platform sees the check.
              </>
            }
          />
        }
      >
        <SquarePanel
          title="Posture"
          right={
            <span className="num text-code font-normal text-ink-4">
              the Card Member's own election
            </span>
          }
        >
          {/* The gap is the rule: adjacent cells on a hairline, white children. */}
          <div className="grid grid-cols-2 gap-px bg-gray-03">
            {([POSTURE_OBSERVE_ONLY, POSTURE_ENFORCE] as Posture[]).map((option) => {
              const on = option === posture;
              const copy = POSTURE_COPY[option];
              return (
                <button
                  key={option}
                  type="button"
                  aria-pressed={on}
                  onClick={() => setPosture(option)}
                  className={`flex flex-col items-start gap-2.5 border-l-[3px] px-[22px] py-[18px] text-left transition-colors duration-[180ms] ${
                    on
                      ? "border-l-blue bg-blue-row"
                      : "border-l-transparent bg-white hover:bg-gray-01"
                  }`}
                >
                  <span className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
                    <span
                      className={`text-[1.0625rem] font-bold ${on ? "text-navy" : "text-ink-3"}`}
                    >
                      {copy.title}
                    </span>
                    <span className="num text-pill font-normal text-ink-4">{copy.sub}</span>
                  </span>
                  <PostureChip posture={option} />
                </button>
              );
            })}
          </div>
          <p className="border-t border-gray-03 bg-gray-01 px-[22px] py-4 text-card-title font-normal leading-[1.55] text-ink-2">
            {POSTURE_COPY[posture].elected}
          </p>
        </SquarePanel>

        <SquarePanel
          title="Four passes, the whole matrix"
          right={
            <span className="num text-code text-ink-4">
              {passes.length - denied.length} proceed · {denied.length} deny
            </span>
          }
        >
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
            const elected = pass.posture === posture;
            const shown = passKey(pass) === passKey(active);
            return (
              <button
                key={passKey(pass)}
                type="button"
                aria-pressed={shown}
                onClick={() => setSelected(passKey(pass))}
                className={`${MATRIX_COLS} w-full items-baseline border-l-[3px] px-[22px] py-[18px] text-left transition-colors duration-[180ms] ${
                  index < passes.length - 1 ? "border-b border-b-gray-02" : ""
                } ${elected ? "border-l-blue" : "border-l-transparent"} ${outcome.row}`}
              >
                <span
                  className={`num text-code ${shown ? "font-semibold text-blue" : "text-ink-4"}`}
                >
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
              </button>
            );
          })}
        </SquarePanel>

        <div className="grid grid-cols-2 gap-6">
          <RecordCard pass={active} index={passes.indexOf(active)} data={data} />
          <LogCard data={data} />
        </div>
      </BeatSplit>

      <Takeaway>No receipt is a recorded fact before it is ever a denial.</Takeaway>
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

/**
 * The entry the selected pass appended, verbatim. Every pass appends before it returns,
 * including the two where no receipt existed — absence is a fact the log carries, which is
 * what makes the corpus usable for reasoning about its own holes.
 */
function RecordCard({
  pass,
  index,
  data,
}: {
  pass: DegradePass;
  index: number;
  data: DegradeData;
}) {
  return (
    <SquarePanel
      title="Read into the record"
      right={
        <span className="num text-code text-ink-4">
          pass {String(index + 1).padStart(2, "0")} · log entry {pass.log_seq}
        </span>
      }
    >
      <div className="flex flex-col gap-3.5 px-[22px] py-[22px]">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-2">
          <ReasonCode code={pass.reason_code} tone={pass.proceeds ? "muted" : "deny"} />
          <span className="text-card-title font-normal leading-[1.55] text-ink-3">
            {REASON_COPY[pass.reason_code] ?? ""}
          </span>
        </div>

        <p className="text-data font-normal leading-[1.6] text-ink">{pass.detail}</p>

        {!pass.proceeds && (
          <p className="border-l-[3px] border-warning bg-gray-01 px-[18px] py-3.5 text-data font-normal leading-[1.5] text-ink">
            Their own delegated authority failing to discharge, on{" "}
            <span className="num">{data.mandate.mandate_id}</span>. No issuer declined
            anything, and no platform was asked.
          </p>
        )}

        <div className="flex flex-col gap-2 border-t border-gray-03 pt-3.5">
          <span className="colhead">The entry the pass appended</span>
          {Object.entries(pass.assessment.record).map(([key, value]) => {
            const text = String(value);
            const isHash = /^[0-9a-f]{32,}$/.test(text);
            return (
              <div key={key} className="flex min-w-0 items-baseline gap-3">
                <span className="num w-[7.5rem] shrink-0 text-[0.71875rem] text-ink-4">
                  {key}
                </span>
                <span
                  className={`num min-w-0 flex-1 break-words text-code ${
                    isHash ? "text-blue" : "text-ink-2"
                  }`}
                  title={text}
                >
                  {isHash ? shortHash(text, 10, 8) : text}
                </span>
              </div>
            );
          })}
        </div>

        <p className="text-code leading-relaxed text-ink-4">
          Hashes and identifiers only. The log never carries the cart, and never a
          credential.
        </p>
      </div>
    </SquarePanel>
  );
}

/** The log state after the four passes, and what the receipt under assessment attests. */
function LogCard({ data }: { data: DegradeData }) {
  const head = data.signed_tree_head;
  const receipt = data.receipt.receipt;
  const chosen = receipt.candidate_set.candidates.find(
    (candidate) => candidate.instrument_id === receipt.selection.instrument_id,
  );
  return (
    <SquarePanel
      title="The log, after four passes"
      right={<span className="num text-code text-ink-4">tree size {head.tree_size}</span>}
    >
      <div className="flex flex-col gap-3.5 px-[22px] py-[22px]">
        <div className="flex flex-col gap-2">
          {Object.entries(data.log_entry_kinds).map(([kind, count]) => (
            <div key={kind} className="flex items-baseline justify-between gap-3">
              <span className="num text-code text-ink-2">{kind}</span>
              <span className="num text-code font-semibold text-navy">{count}</span>
            </div>
          ))}
        </div>

        <div className="flex flex-col gap-2 border-t border-gray-03 pt-3.5">
          {Object.entries(data.reason_code_counts).map(([code, count]) => (
            <div key={code} className="flex items-baseline justify-between gap-3">
              <span className="num min-w-0 break-words text-code text-ink-2">{code}</span>
              <span className="num shrink-0 text-code font-semibold text-navy">{count}</span>
            </div>
          ))}
        </div>

        <div className="flex flex-col gap-2 border-t border-gray-03 pt-3.5">
          <HashRow label="tree root" value={head.root_hash} />
          <span className="num text-code text-ink-4">
            head signed {shortHash(head.signature.value, 10, 6)} · {head.signature.alg}
          </span>
        </div>

        <div className="flex flex-col gap-2 border-t border-gray-03 pt-3.5">
          <span className="colhead">The selection under assessment</span>
          <div className="flex items-baseline justify-between gap-3">
            <span className="min-w-0 break-words text-data font-normal text-ink-2">
              {chosen
                ? `${chosen.issuer} · ${chosen.product}`
                : receipt.selection.instrument_id}
            </span>
            <span className="num shrink-0 text-code font-semibold text-navy">
              {chosen?.asserted_value_display ??
                currencyMoney(data.cart_total_minor, data.cart.currency)}
            </span>
          </div>
          <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
            <span
              className={`num text-code font-medium ${
                receipt.attestation.faithful ? "text-success" : "text-warning"
              }`}
            >
              {receipt.attestation.outcome}
            </span>
            <span className="text-code text-ink-4">
              {receipt.attestation.faithful
                ? "certifies compliance whether or not the issuer's own card won"
                : "the attestation found the ranking unfaithful"}
            </span>
          </div>
          <span className="text-code leading-relaxed text-ink-4">
            Signed {data.receipt.signature.role}-side. No issuer signature covers a ranking.
            Cart {currencyMoney(data.cart_total_minor, data.cart.currency)} at{" "}
            <span className="num">{data.cart.merchant}</span>.
          </span>
        </div>
      </div>
    </SquarePanel>
  );
}
