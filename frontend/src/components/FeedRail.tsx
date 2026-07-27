/**
 * The live decision feed, newest first.
 *
 * It is a permanent rail rather than a screen: whichever beat is on the projector, every
 * ALLOW / STEP_UP / DENY is landing here with its reason codes and its latency. A judge
 * can check at any moment that the thing on screen actually went through the PDP.
 */

import { Money, OutcomeChip, ReasonCode } from "./ui";
import { clockTime, ms as fmtMs } from "../lib/format";
import { useConsole, type FeedItem } from "../lib/store";
import type { ConnStatus } from "../lib/transport";
import { REASON_STEP_UP_REQUIRED } from "../lib/types";

const CONN_LABEL: Record<ConnStatus, string> = {
  connecting: "connecting",
  open: "live",
  closed: "offline",
  error: "error",
};

export function FeedRail() {
  const feed = useConsole((s) => s.feed);
  const conn = useConsole((s) => s.conn);
  const clearFeed = useConsole((s) => s.clearFeed);

  return (
    <aside className="flex min-h-0 w-[21rem] shrink-0 flex-col border-l border-line bg-panel">
      <header className="flex shrink-0 items-center justify-between border-b border-line px-3.5 py-2.5">
        <div className="flex items-center gap-2">
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              conn === "open" ? "bg-allow" : conn === "connecting" ? "bg-stepup" : "bg-deny"
            }`}
          />
          <h2 className="eyebrow">Decision feed</h2>
          <span className="num text-pill text-ink-4">{CONN_LABEL[conn]}</span>
        </div>
        <button
          type="button"
          onClick={clearFeed}
          className="text-pill text-ink-4 transition-colors hover:text-ink-2"
        >
          clear
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {feed.length === 0 ? (
          <div className="px-3.5 py-4 text-pill leading-relaxed text-ink-4">
            No traffic yet. Every state-changing decision appends to the Merkle ledger before it
            is returned — if it is not in the ledger, it did not happen.
          </div>
        ) : (
          feed.map((item) => <FeedRow key={item.id} item={item} />)
        )}
      </div>
    </aside>
  );
}

function FeedRow({ item }: { item: FeedItem }) {
  const openEvidence = useConsole((s) => s.openEvidence);
  const evidenceTxn = useConsole((s) => s.evidenceTxn);
  const { event } = item;

  if (event.kind === "decision") {
    const decision = event.payload;
    const primary = decision.reason_codes[0];
    const selected = evidenceTxn === decision.txn_id;
    return (
      <button
        type="button"
        onClick={() => openEvidence(decision.txn_id)}
        className={`anim-line-in flex w-full flex-col gap-1 border-b border-line/70 px-3.5 py-2 text-left transition-colors ${
          selected ? "bg-raised" : "hover:bg-raised/60"
        } ${decision.outcome === "DENY" ? "anim-flash-deny" : "anim-flash-neutral"}`}
      >
        <div className="flex items-baseline gap-2">
          <OutcomeChip outcome={decision.outcome} />
          <Money
            minorUnits={decision.amount}
            tone={decision.outcome === "DENY" ? "deny" : "default"}
            className="ml-auto text-body"
          />
        </div>
        <div className="flex items-baseline gap-2">
          {primary ? (
            <ReasonCode
              code={primary}
              tone={
                decision.outcome === "ALLOW"
                  ? "muted"
                  : primary === REASON_STEP_UP_REQUIRED
                    ? "stepup"
                    : "deny"
              }
            />
          ) : (
            <span className="text-pill text-ink-4">no violations</span>
          )}
          {decision.reason_codes.length > 1 && (
            <span className="num text-pill text-ink-4">
              +{decision.reason_codes.length - 1}
            </span>
          )}
          <span className="num ml-auto shrink-0 text-pill text-ink-4">
            {fmtMs(decision.elapsed_ms)}
          </span>
        </div>
        <div className="num flex items-baseline justify-between text-pill text-ink-4">
          <span className="break-words">{decision.holder}</span>
          <span>{clockTime(decision.ts)}</span>
        </div>
      </button>
    );
  }

  const meta = describe(item);
  return (
    <div className="anim-line-in flex items-baseline gap-2 border-b border-line/70 px-3.5 py-1.5">
      <span className={`num shrink-0 text-pill tracking-[0.06em] ${meta.tone}`}>
        {meta.tag}
      </span>
      <span className="min-w-0 flex-1 break-words text-pill text-ink-2">{meta.text}</span>
      <span className="num shrink-0 text-pill text-ink-4">
        {new Date(item.at).toLocaleTimeString("en-GB", { hour12: false })}
      </span>
    </div>
  );
}

/**
 * `engine._emit` sends `DelegationOutcome.to_dict()`, which carries `mandate` but not
 * `child_holder` — that field is added by the scenario endpoint, not the event bus. Fall
 * back rather than printing "undefined" onto the projector.
 */
function holderOf(payload: { child_holder?: string; mandate: { holder: string } | null }): string {
  return payload.child_holder ?? payload.mandate?.holder ?? "child agent";
}

function describe(item: FeedItem): { tag: string; text: string; tone: string } {
  const { event } = item;
  switch (event.kind) {
    case "delegation_accepted":
      return {
        tag: "DELEGATE",
        tone: "text-allow",
        text: `${holderOf(event.payload)} accepted · Z3 unsat in ${fmtMs(
          event.payload.entailment.elapsed_ms,
        )}`,
      };
    case "delegation_rejected":
      return {
        tag: "ESCALATE",
        tone: "text-deny",
        text: `${holderOf(event.payload)} rejected · SCOPE_ESCALATION${
          event.payload.naive_disagrees ? " · subset check disagreed" : ""
        }`,
      };
    case "revocation":
      return {
        tag: "REVOKE",
        tone: "text-deny",
        text: `${event.payload.root_id.slice(0, 12)}… · ${event.payload.rows_written} row · ${
          event.payload.cause
        }`,
      };
    case "mandate_granted":
      return {
        tag: "GRANT",
        tone: "text-proof",
        text: `${event.payload.holder} · depth ${event.payload.depth} · ${event.payload.caveat_count} caveats`,
      };
    case "step_up":
      return {
        tag: "STEPUP",
        tone: "text-stepup",
        text: event.payload.challenge
          ? `${event.payload.challenge.satisfied ? "satisfied" : "challenged"} · cart ${event.payload.challenge.cart_hash.slice(0, 10)}…`
          : (event.payload.error ?? "challenge"),
      };
    default:
      return { tag: "EVENT", tone: "text-ink-4", text: "" };
  }
}
