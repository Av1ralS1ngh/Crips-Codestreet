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
 *     OWN AGENT. The platform is asked for nothing and never sees the check. The actor
 *     column exists to make that unmissable, and the third box is deliberately empty.
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
 * recompute anything, because nothing here is the console's to decide.
 */

import { useEffect, useState, type ReactNode } from "react";
import { Button, Caveat, Empty, Panel, ReasonCode } from "../components/ui";
import { HashRow, ScreenHeader } from "../components/plumblineUi";
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
      <Panel className="h-full" title="Graceful degrade" tone={degradeError ? "deny" : "default"}>
        <Empty>
          <div className="flex max-w-[34rem] flex-col gap-2">
            <span className="num text-ink-3">POST /api/plumbline/scenario/graceful_degrade</span>
            <span className="text-ink-2">
              {degradeError ?? (running ? "running the four passes…" : "not run yet")}
            </span>
            {degradeError && (
              <span className="text-ink-4">
                The scenario runs on a fixed clock, so it can be re-run without changing a
                byte. Switch the header badge to MOCK to drive the recorded run instead.
              </span>
            )}
          </div>
        </Empty>
      </Panel>
    );
  }

  const data = degrade.data;
  const currency = data.cart.currency;
  const passes = data.passes;
  const denied = passes.filter((p) => !p.proceeds);
  const rowPasses = passes.filter((p) => p.posture === posture);
  const active =
    passes.find((p) => passKey(p) === selected) ??
    rowPasses.find((p) => !p.counterpart_receipt) ??
    passes[0];

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <ScreenHeader
        beat="05"
        title="No receipt, and it still proceeds."
        right={
          <div className="flex items-center gap-2">
            <span className="num text-pill text-ink-4">
              {mode === "live" ? "live scenario" : "recorded run"} · clock {degrade.clock}
            </span>
            <Button onClick={() => void run()} disabled={running} size="sm">
              {running ? "running…" : "run again"}
            </Button>
          </div>
        }
      >
        {degrade.headline}
      </ScreenHeader>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)] gap-3">
        {/* ------------------------------------------------------- the posture and matrix */}
        <div className="flex min-h-0 flex-col gap-3">
          <PostureControl posture={posture} onChange={setPosture} />

          <Panel
            title="Four passes, the whole matrix"
            hint="one cart, one mandate, both postures, receipt present and absent"
            right={
              <span className="num text-pill text-ink-4">
                {passes.length - denied.length} proceed · {denied.length} deny
              </span>
            }
            bodyClassName="grid grid-cols-2 gap-2 p-3"
          >
            {passes.map((pass) => (
              <MatrixCell
                key={passKey(pass)}
                pass={pass}
                dimmed={pass.posture !== posture}
                active={passKey(pass) === passKey(active)}
                onSelect={() => setSelected(passKey(pass))}
              />
            ))}
          </Panel>

          <PassDetail pass={active} data={data} />
        </div>

        {/* --------------------------------------------------------- where the check lives */}
        <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
          <EnforcementPoint data={data} denied={denied.length} />
          <CoveragePanel passes={passes} />
          <LogPanel data={data} currency={currency} />
          <Panel title="Read into the record" bodyClassName="flex flex-col gap-2 p-3.5">
            {degrade.notes.map((note) => (
              <Caveat key={note}>{note}</Caveat>
            ))}
          </Panel>
        </div>
      </div>
    </div>
  );
}

const passKey = (pass: DegradePass) => `${pass.posture}:${pass.counterpart_receipt}`;

// ---------------------------------------------------------------------------------------

function PostureControl({
  posture,
  onChange,
}: {
  posture: Posture;
  onChange: (next: Posture) => void;
}) {
  return (
    <Panel
      title="Posture"
      hint="the Card Member's election, on their own mandate"
      bodyClassName="flex flex-col gap-2 p-3"
    >
      <div className="grid grid-cols-2 gap-2">
        {([POSTURE_OBSERVE_ONLY, POSTURE_ENFORCE] as Posture[]).map((option) => {
          const on = option === posture;
          const copy = POSTURE_COPY[option];
          return (
            <button
              key={option}
              type="button"
              onClick={() => onChange(option)}
              className={`flex flex-col items-start gap-0.5 rounded border px-3 py-2 text-left transition-colors ${
                on
                  ? option === POSTURE_ENFORCE
                    ? "border-deny/55 bg-deny-wash text-deny"
                    : "border-allow/45 bg-allow-wash text-allow"
                  : "border-line-2 text-ink-3 hover:text-ink-2"
              }`}
            >
              <span className="display-wide text-[1.05rem] leading-none font-semibold">
                {copy.title}
              </span>
              <span className="num text-pill tracking-[0.06em] uppercase opacity-80">
                {copy.sub}
              </span>
              <span className="num text-pill text-ink-4">{option}</span>
            </button>
          );
        })}
      </div>
      <p className="text-pill leading-snug text-ink-2">{POSTURE_COPY[posture].elected}</p>
    </Panel>
  );
}

function MatrixCell({
  pass,
  dimmed,
  active,
  onSelect,
}: {
  pass: DegradePass;
  dimmed: boolean;
  active: boolean;
  onSelect: () => void;
}) {
  const proceeds = pass.proceeds;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`flex flex-col gap-1.5 rounded border px-3 py-2.5 text-left transition-colors ${
        proceeds ? "border-allow/35 bg-allow-wash/60" : "border-deny/55 bg-deny-wash"
      } ${dimmed ? "opacity-45" : ""} ${active ? "outline outline-2 outline-blue" : ""}`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="num text-pill tracking-[0.08em] text-ink-3 uppercase">
          {pass.posture}
        </span>
        <span className="num text-pill text-ink-4">seq {pass.log_seq}</span>
      </div>
      <div className="text-pill text-ink-2">
        counterpart receipt{" "}
        <span className={pass.counterpart_receipt ? "text-ink" : "text-deny"}>
          {pass.counterpart_receipt ? "present" : "absent"}
        </span>
      </div>
      <div
        className={`display-wide text-[1.15rem] leading-none font-semibold ${
          proceeds ? "text-allow" : "text-deny"
        }`}
      >
        {proceeds ? "PROCEEDS" : "DOES NOT DISCHARGE"}
      </div>
      <ReasonCode code={pass.reason_code} tone={proceeds ? "muted" : "deny"} />
    </button>
  );
}

function PassDetail({ pass, data }: { pass: DegradePass; data: DegradeData }) {
  const record = pass.assessment.record;
  return (
    <Panel
      title="What the engine returned for that pass"
      hint={REASON_COPY[pass.reason_code] ?? ""}
      tone={pass.proceeds ? "default" : "deny"}
      className="min-h-0 flex-1"
      bodyClassName="flex min-h-0 flex-col gap-2.5 overflow-y-auto p-3.5"
    >
      <p className="num text-pill leading-relaxed text-ink-2">{pass.detail}</p>

      {!pass.proceeds && (
        <div className="flex flex-col gap-1 rounded border border-deny/40 bg-deny-wash/50 px-3 py-2">
          <span className="eyebrow text-deny">What actually failed</span>
          {/* This used to restate the engine's own detail, rendered verbatim two lines
              above, and then draw the spend-limit analogy. Both went. What is left is the
              only part the detail does not already say. */}
          <p className="text-pill leading-snug text-ink-2">
            Their own delegated authority failing to discharge, on{" "}
            <span className="num text-ink">{data.mandate.mandate_id}</span>. No issuer
            declined anything, and no platform was asked.
          </p>
        </div>
      )}

      <div className="flex flex-col gap-1.5 border-t border-line pt-2.5">
        <span className="eyebrow">The record that landed in the log</span>
        {Object.entries(record).map(([key, value]) => (
          <div key={key} className="flex min-w-0 items-baseline gap-2">
            <span className="num w-[8.5rem] shrink-0 text-pill text-ink-4">{key}</span>
            <span
              className={`num min-w-0 flex-1 break-words text-pill ${
                /^[0-9a-f]{32,}$/.test(String(value)) ? "text-proof" : "text-ink-2"
              }`}
              title={String(value)}
            >
              {String(value)}
            </span>
          </div>
        ))}
      </div>
      <Caveat>
        Hashes and identifiers only. The log never carries the cart, and never a credential.
      </Caveat>
    </Panel>
  );
}

function EnforcementPoint({ data, denied }: { data: DegradeData; denied: number }) {
  return (
    <Panel
      title="Where the check lives"
      hint="and where it does not"
      bodyClassName="flex flex-col gap-2 p-3.5"
    >
      <Actor
        role="Card Member"
        detail={
          <>
            issues <span className="num text-ink-2">{data.mandate.mandate_id}</span> to their
            own agent, carrying a disclosure caveat
          </>
        }
        tone="ink"
      />
      <Connector label="the caveat travels with the delegation" />
      <Actor
        role="Their shopping agent"
        detail={
          <>
            must produce a receipt to discharge it. Under enforcement this is the party that
            fails, {denied} of {data.passes.length} passes.
          </>
        }
        tone="deny"
      />
      <Connector label="nothing crosses this line" struck />
      <Actor
        role="The third-party checkout"
        detail="asked for nothing. Sees no check, holds no obligation, is not a party to the caveat."
        tone="absent"
      />
      <Caveat>
        A credential that hard-fails in someone else's checkout gets routed around. So the
        enforcement point moved; it was not softened.
      </Caveat>
    </Panel>
  );
}

function Actor({
  role,
  detail,
  tone,
}: {
  role: string;
  detail: ReactNode;
  tone: "ink" | "deny" | "absent";
}) {
  const styles = {
    ink: "border-line-2 bg-raised",
    deny: "border-deny/40 bg-deny-wash/40",
    absent: "border-dashed border-line-2 bg-transparent",
  }[tone];
  return (
    <div className={`flex flex-col gap-0.5 rounded border px-3 py-2 ${styles}`}>
      <span
        className={`text-body font-medium ${tone === "absent" ? "text-ink-3" : "text-ink"}`}
      >
        {role}
      </span>
      <span
        className={`text-pill leading-snug ${tone === "absent" ? "text-ink-4" : "text-ink-3"}`}
      >
        {detail}
      </span>
    </div>
  );
}

function Connector({ label, struck }: { label: string; struck?: boolean }) {
  return (
    <div className="flex items-center gap-2 pl-3">
      <span className={`h-3 w-px ${struck ? "bg-deny/50" : "bg-line-2"}`} />
      <span
        className={`text-pill ${struck ? "text-deny/70 line-through decoration-deny/50" : "text-ink-4"}`}
      >
        {label}
      </span>
    </div>
  );
}

function CoveragePanel({ passes }: { passes: DegradePass[] }) {
  const covered = passes.filter((p) => p.coverage_eligible).length;
  return (
    <Panel
      title="What is withheld instead"
      hint="coverage, never authorization"
      tone="proof"
      bodyClassName="flex flex-col gap-2 p-3.5"
    >
      <div className="grid grid-cols-2 gap-2">
        <div className="flex flex-col gap-0.5">
          <span className="eyebrow">Authorization</span>
          <span className="num display-wide text-[1.3rem] leading-none font-semibold text-allow">
            {passes.filter((p) => p.proceeds).length}/{passes.length}
          </span>
          <span className="text-pill text-ink-4">proceed</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="eyebrow">Purchase protection</span>
          <span className="num display-wide text-[1.3rem] leading-none font-semibold text-stepup">
            {covered}/{passes.length}
          </span>
          <span className="text-pill text-ink-4">covered</span>
        </div>
      </div>
      {/* The carrot-and-stick gloss was cut. The two counters above already say it. */}
      <p className="text-pill leading-snug text-ink-2">
        No receipt, no Agent Purchase Protection. Coverage is conditioned on evidence;
        authorization is not.
      </p>
    </Panel>
  );
}

function LogPanel({ data, currency }: { data: DegradeData; currency: string }) {
  const receipt = data.receipt.receipt;
  const chosen = receipt.candidate_set.candidates.find(
    (c) => c.instrument_id === receipt.selection.instrument_id,
  );
  return (
    <Panel
      title="The log, after four passes"
      hint={`tree size ${data.signed_tree_head.tree_size}`}
      tone="proof"
      bodyClassName="flex flex-col gap-2 p-3.5"
    >
      <div className="flex flex-col gap-1">
        {Object.entries(data.log_entry_kinds).map(([kind, count]) => (
          <div key={kind} className="flex items-baseline justify-between gap-2">
            <span className="num text-pill text-ink-3">{kind}</span>
            <span className="num text-pill text-ink">{count}</span>
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-1 border-t border-line pt-2">
        {Object.entries(data.reason_code_counts).map(([code, count]) => (
          <div key={code} className="flex items-baseline justify-between gap-2">
            <span className="num text-pill text-ink-3">{code}</span>
            <span className="num text-pill text-ink">{count}</span>
          </div>
        ))}
      </div>
      <HashRow label="tree root" value={data.signed_tree_head.root_hash} />
      <span className="num text-pill text-ink-4">
        head signed {shortHash(data.signed_tree_head.signature.value, 10, 6)} ·{" "}
        {data.signed_tree_head.signature.alg}
      </span>
      <div className="flex flex-col gap-1 border-t border-line pt-2">
        <span className="eyebrow">The selection under assessment</span>
        <div className="flex items-baseline justify-between gap-2">
          <span className="min-w-0 break-words text-pill text-ink-2">
            {chosen ? `${chosen.issuer} · ${chosen.product}` : receipt.selection.instrument_id}
          </span>
          <span className="num shrink-0 text-pill text-ink">
            {chosen?.asserted_value_display ?? currencyMoney(data.cart_total_minor, currency)}
          </span>
        </div>
        <div className="flex items-baseline gap-2">
          <ReasonCode code={receipt.attestation.outcome} tone="muted" />
          <span className="text-pill text-ink-4">
            {receipt.attestation.faithful
              ? "certifies compliance whether or not the issuer's own card won"
              : "the attestation found the ranking unfaithful"}
          </span>
        </div>
        {/* The compliance-symmetry sentence was cut, not lost: the attestation line
            directly above now carries it against the outcome code. What has to survive
            here is the narrow signing scope, so that is all this says. */}
        <Caveat>
          Signed {data.receipt.signature.role}-side. No issuer signature covers a ranking.
          Cart {currencyMoney(data.cart_total_minor, currency)} at{" "}
          <span className="num">{data.cart.merchant}</span>.
        </Caveat>
      </div>
    </Panel>
  );
}
