/**
 * Kernel, tab 4 — protection, priced.
 *
 * Amex undertook Agent Purchase Protection before publishing the machinery to adjudicate
 * it. This is that machinery priced: every operator carries a behavioural risk score
 * derived from decisions the ledger already recorded, and the cutoff is the underwriting
 * lever.
 *
 * The three bars are derived from `exposure.operators` rather than the API's precomputed
 * `curve`, so the permissive, published and strict points can never disagree with the book
 * they are drawn from. All arithmetic is integer minor units; no floats touch a money
 * figure.
 *
 * The share in the closing sentence is coverage lost, never authorisation denied. Coverage
 * is conditioned on evidence; authorisation is not, and the difference is the whole reason
 * this console is not a decline product.
 */

import { BarRow, SquarePanel } from "../components/plumblineUi";
import { money } from "../lib/format";
import { useConsole } from "../lib/store";
import type { OperatorExposure } from "../lib/types";

const PERMISSIVE = 0;
const STRICT = 100;

interface Slice {
  coveredExposure: number;
  declinedAuthorisations: number;
}

function sliceAt(operators: OperatorExposure[], cutoff: number): Slice {
  let coveredExposure = 0;
  let declinedAuthorisations = 0;
  for (const operator of operators) {
    if (operator.risk_score >= cutoff) coveredExposure += operator.modelled_exposure;
    else declinedAuthorisations += operator.authorized_count;
  }
  return { coveredExposure, declinedAuthorisations };
}

export function ExposureView() {
  const exposure = useConsole((s) => s.state?.exposure);

  if (!exposure) {
    return (
      <div className="border border-gray-03 bg-white px-[22px] py-10 text-center text-body text-ink-3">
        Exposure book unavailable.
      </div>
    );
  }

  const operators = exposure.operators;
  const total = exposure.total_exposure;
  const permissive = sliceAt(operators, PERMISSIVE);
  const book = sliceAt(operators, exposure.cutoff_score);
  const strict = sliceAt(operators, STRICT);

  const authorisations = operators.reduce((sum, o) => sum + o.authorized_count, 0);
  const share = (count: number) =>
    authorisations ? `${((count / authorisations) * 100).toFixed(1)}%` : "0%";
  const width = (value: number) => (total ? (value / total) * 100 : 0);

  return (
    <div className="flex flex-col gap-6">
      <p className="text-[1.0625rem] leading-[1.6] font-bold text-ink">Protection, priced.</p>

      <SquarePanel title="Exposure retained by cutoff">
        <div className="flex flex-col gap-[22px] px-6 py-[26px]">
          <BarRow
            label={`Cutoff ${PERMISSIVE} — permissive`}
            value={money(permissive.coveredExposure)}
            pct={width(permissive.coveredExposure)}
          />
          <BarRow
            label={`Cutoff ${exposure.cutoff_score} — operator book`}
            value={money(book.coveredExposure)}
            pct={width(book.coveredExposure)}
            fill="bg-blue"
            valueClass="text-blue"
          />
          <BarRow
            label={`Cutoff ${STRICT} — strict`}
            value={money(strict.coveredExposure)}
            pct={width(strict.coveredExposure)}
            fill="bg-sky"
          />
          <p className="text-[0.90625rem] leading-[1.6] font-normal text-ink-3">
            Strict is not free: it leaves {share(strict.declinedAuthorisations)} of the
            authorisations already recorded outside coverage. The operator picks a point on
            this curve and the log records which one.
          </p>
        </div>
      </SquarePanel>
    </div>
  );
}
