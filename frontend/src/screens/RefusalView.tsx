/**
 * Beat 04 — the evaluator declines to sign.
 *
 * Refusal is a typed output, not an error path. An agent asserts a value, the witness it
 * offers does not realise it, and the evaluator emits a coded refusal that is appended to
 * the log before it is returned. Nothing is signed, and the fact that nothing was signed is
 * itself a recorded event.
 *
 * The rule this screen exists to keep: the evaluator's verdict and this browser's own
 * recomputation always render together. One verdict alone would be a claim rather than a
 * check.
 *
 * Every figure is the fixture's own — the displays are the kernel's formatter, the clauses
 * are the verifier's codes and details verbatim, the anchor is the ledger's sequence and
 * entry hash, and the re-check latency is measured in this browser on this run.
 */

import type { ReactNode } from "react";
import {
  BeatHeader,
  BeatPage,
  BeatSplit,
  PlumblineUnavailable,
  ShowsPanel,
  SquarePanel,
  Takeaway,
} from "../components/plumblineUi";
import { currencyMoney, ms as fmtMs, shortHash } from "../lib/format";
import { manifestFor, useConsole } from "../lib/store";
import { useLocalVerification } from "../lib/useWitness";

export function RefusalView() {
  const plumbline = useConsole((s) => s.plumbline);
  const plumblineError = useConsole((s) => s.plumblineError);

  const active = plumbline?.refusals[0] ?? null;

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
  // Against the offered allocation, not against the better one the evaluator could have
  // built: the two figures on screen are the asserted total and what this witness realises,
  // and the shortfall quoted here has to be the difference a reader can take between them.
  const unrealised = active.asserted_minor - active.verification.realized_minor;
  const failures = active.verification.failures;

  return (
    <BeatPage>
      <BeatHeader beat="04" label="Refusal" title="It refuses to sign, and says why.">
        The agent asserted {active.asserted_display} and offered a witness that realises{" "}
        {active.verification.realized_display}. The evaluator does not round, negotiate, or
        warn. It rejects, names the failing clause, and writes the rejection to the log.
      </BeatHeader>

      <BeatSplit
        aside={
          <ShowsPanel
            points={[
              "A refusal is a product feature. An agent that will sign anything is worth nothing as a witness.",
              "The server's verdict and the browser's own recomputation are shown together. One green tick alone would be a claim, not a check.",
              "The rejection is appended to the ledger before it is returned, so a refusal cannot be quietly retried away.",
            ]}
          />
        }
      >
        {/* One child, so the column rhythm here is 24px rather than BeatSplit's 34px. */}
        <div className="flex min-w-0 flex-col gap-6">
          <div className="grid grid-cols-2 gap-6">
            <SquarePanel tone="deny" title="Asserted by the agent">
              <div className="flex flex-col gap-2 px-[22px] py-[26px]">
                <span className="num text-[2.125rem] leading-none font-semibold tracking-[-0.01em] text-navy">
                  {active.asserted_display}
                </span>
                <span className="text-card-title font-normal leading-[1.55] text-ink-2">
                  Claimed total value on the {currencyMoney(basket, currency)} basket.{" "}
                  {currencyMoney(unrealised, currency)} of it has no allocation behind it.
                </span>
              </div>
            </SquarePanel>

            {/* The pair carries two strips, one deny and one gray; SquarePanel's plain head
                is the 14.5px title the verifier panel below uses, so this one is local. */}
            <div className="flex min-w-0 flex-col border border-gray-03 bg-white">
              <div className="strip border-b border-gray-03 bg-gray-01 px-[22px] py-[15px] text-navy">
                The offered witness
              </div>
              <div className="flex flex-col gap-2 px-[22px] py-[26px]">
                <span className="num text-[2.125rem] leading-none font-semibold tracking-[-0.01em] text-blue">
                  {active.verification.realized_display}
                </span>
                <span className="text-card-title font-normal leading-[1.55] text-ink-2">
                  {active.witness.assignments.length} assignments,{" "}
                  {local
                    ? `re-checked in ${fmtMs(local.elapsedMs, 3)} by this browser`
                    : "re-adding the numbers in this browser"}{" "}
                  — no solver.
                </span>
              </div>
            </div>
          </div>

          <SquarePanel title="Verifier output">
            <div className="grid grid-cols-2 gap-px bg-gray-03">
              <VerdictCell
                label="Evaluator"
                ok={active.verification.supports_assertion}
                note={active.reason_code}
              />
              <VerdictCell
                label="This console"
                ok={local?.supportsAssertion ?? false}
                pending={local === null}
                note={
                  local === null
                    ? "re-adding the numbers…"
                    : `${local.assignments} assignments re-checked · ${fmtMs(local.elapsedMs, 3)}`
                }
              />
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

            <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 border-t border-gray-03 bg-gray-01 px-[22px] py-4">
              <span className="colhead">Anchored in the log</span>
              <span className="num shrink-0 text-code text-blue" title={active.entry_hash}>
                entry {active.ledger_seq} · {shortHash(active.entry_hash, 8, 6)}
              </span>
            </div>
          </SquarePanel>
        </div>
      </BeatSplit>

      <Takeaway>
        Declining to sign is the only honest answer to an unsupportable number.
      </Takeaway>
    </BeatPage>
  );
}

// ---------------------------------------------------------------------------------------

/** One side of the two-up. Both sides always render; neither is ever shown alone. */
function VerdictCell({
  label,
  ok,
  note,
  pending = false,
}: {
  label: string;
  ok: boolean;
  note: ReactNode;
  pending?: boolean;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-[9px] bg-white p-[22px]">
      <span className="colhead">{label}</span>
      <span
        className={`num text-[1.0625rem] font-semibold ${
          pending ? "text-ink-4" : ok ? "text-success" : "text-warning"
        }`}
      >
        {pending ? "…" : ok ? "VERIFIED" : "REJECTED"}
      </span>
      <span className="num min-w-0 break-words text-code text-ink-3" title={String(note)}>
        {note}
      </span>
    </div>
  );
}
