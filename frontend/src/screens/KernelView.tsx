/**
 * Beat 08, the governance kernel, as supporting evidence.
 *
 * The beat number here is 08 and must stay in step with the nav in App.tsx. It read 06 for
 * a while after Controls and Attribution were reordered ahead of it, which put two
 * different numbers for this screen on the projector at once.
 *
 * These four views were the headline when this console governed payment authority. They
 * are demoted here on purpose. The disclosure argument depends on them without being about
 * them: a caveat on instrument selection is only enforceable because a mandate can be
 * narrowed and never widened, because entailment is checked at every delegation hop, and
 * because one row kills a whole subtree.
 *
 * The receipt obligation is a caveat on the mandate the Card Member issues to their own
 * agent. It is not a condition imposed on a platform, the platform is never asked for it,
 * and an agent that cannot produce a receipt fails to discharge the cardholder's own
 * delegated authority, which is architecturally identical to the spend limit denying below.
 *
 * The decision feed rides with this screen and only this screen. It used to be a permanent
 * rail beside every beat, which stole 21rem from seven screens that never referred to it.
 */

import type { ReactNode } from "react";
import {
  BeatHeader,
  BeatPage,
  BeatSplit,
  ShowsPanel,
  Takeaway,
} from "../components/plumblineUi";
import { humanReason, money, ms as fmtMs } from "../lib/format";
import { useConsole, type FeedItem, type KernelTab } from "../lib/store";
import type { ConnStatus } from "../lib/transport";
import { AttackView } from "./AttackView";
import { DelegationView } from "./DelegationView";
import { ExposureView } from "./ExposureView";
import { KillSwitchView } from "./KillSwitchView";

const TABS: { key: KernelTab; label: string }[] = [
  { key: "attack", label: "Injection" },
  { key: "delegation", label: "Delegation" },
  { key: "kill", label: "Kill switch" },
  { key: "exposure", label: "Exposure" },
];

/**
 * One pair of statements for all four tabs, because the sidebar sits outside the tab
 * switch. The second is the standing constraint the screen exists to make true: it keeps
 * the enforcement point on the cardholder's side of the wire, so it cannot vary with which
 * tab is open.
 */
const SHOWS = [
  "The receipt obligation is enforceable because the mandate underneath it is. Same machinery as a spend limit, applied to a disclosure.",
  "An agent that cannot produce a receipt fails to discharge the cardholder's own delegated authority.",
];

export function KernelView() {
  const tab = useConsole((s) => s.kernelTab);
  const setTab = useConsole((s) => s.setKernelTab);

  const sidebar = (
    <>
      <ShowsPanel points={SHOWS} />
      <DecisionFeed />
    </>
  );

  return (
    <BeatPage gap="gap-[30px]">
      <BeatHeader
        beat="08"
        label="Governance kernel"
        title="The layer the receipt obligation rides on."
      >
        A caveat the Card Member puts on their own agent. A mandate can be narrowed and never
        widened, entailment is checked at every delegation hop, and one row kills a whole
        subtree.
      </BeatHeader>

      {/* The rail's active treatment, at pill scale: one selection affordance for the
          whole console rather than a third one invented for this screen. */}
      <nav className="flex flex-wrap gap-2">
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setTab(item.key)}
            className={`rounded-pill border px-[18px] py-[9px] text-[0.84375rem] font-semibold transition-colors duration-[180ms] ${
              tab === item.key
                ? "border-blue bg-blue text-white"
                : "border-gray-03 bg-white text-ink-3 hover:border-blue hover:text-blue"
            }`}
          >
            {item.label}
          </button>
        ))}
      </nav>

      <BeatSplit aside={sidebar}>
        {tab === "attack" && <AttackView />}
        {tab === "delegation" && <DelegationView />}
        {tab === "kill" && <KillSwitchView />}
        {tab === "exposure" && <ExposureView />}
      </BeatSplit>

      <Takeaway>
        Every state-changing decision appends to the ledger before it is returned. If it is
        not in the ledger, it did not happen.
      </Takeaway>
    </BeatPage>
  );
}

// ------------------------------------------------------------------------- decision feed

const CONN_LABEL: Record<ConnStatus, string> = {
  connecting: "connecting",
  open: "live",
  closed: "offline",
  error: "error",
};

/**
 * The feed, newest first, off the same store fields the rail read. Time is relative to the
 * oldest entry still held rather than a wall clock: a judge is watching one run, and T+0.4s
 * is the readable form of "0.4 seconds after this started".
 */
function DecisionFeed() {
  const feed = useConsole((s) => s.feed);
  const conn = useConsole((s) => s.conn);

  const base = feed.length ? feed[feed.length - 1].at : 0;

  return (
    <div className="flex flex-col border border-gray-03 bg-white">
      <div className="flex items-center gap-2.5 border-b border-gray-03 px-5 py-3.5">
        {/* The dot is the transport, not decoration: it is green only while the socket the
            label names is actually open. */}
        <span
          className={`h-1.5 w-1.5 shrink-0 rounded-pill ${
            conn === "open" ? "bg-success" : "bg-gray-04"
          }`}
        />
        <span className="num text-[0.65625rem] font-semibold tracking-[0.16em] text-navy uppercase">
          Decision feed
        </span>
        <span className="num ml-auto text-[0.6875rem] text-ink-4">{CONN_LABEL[conn]}</span>
      </div>
      <div className="flex flex-col">
        {feed.slice(0, 12).map((item) => (
          <FeedRow key={item.id} item={item} base={base} />
        ))}
      </div>
    </div>
  );
}

function FeedRow({ item, base }: { item: FeedItem; base: number }) {
  const meta = describe(item);
  const stamp = `T+${((item.at - base) / 1000).toFixed(1)}s`;

  return (
    <div className="anim-line-in flex flex-col gap-1 border-b border-gray-02 px-5 py-3.5 last:border-b-0">
      <span className="flex items-baseline justify-between gap-3">
        <span className={`num text-[0.75rem] ${meta.tone}`}>{meta.verb}</span>
        <span className="num text-[0.6875rem] text-ink-4">{stamp}</span>
      </span>
      <span className="text-[0.84375rem] leading-[1.5] break-words text-ink-2">{meta.text}</span>
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

function describe(item: FeedItem): { verb: string; text: ReactNode; tone: string } {
  const { event } = item;
  switch (event.kind) {
    case "decision": {
      const decision = event.payload;
      const primary = decision.reason_codes[0];
      return {
        verb: decision.outcome === "STEP_UP" ? "STEP UP" : decision.outcome,
        tone:
          decision.outcome === "DENY"
            ? "text-warning"
            : decision.outcome === "ALLOW"
              ? "text-success"
              : "text-attention-ink",
        text: `${primary ? humanReason(primary) : "within mandate"} · ${money(decision.amount)} · ${decision.holder}`,
      };
    }
    case "delegation_accepted":
      return {
        verb: "DELEGATE",
        tone: "text-success",
        text: `${holderOf(event.payload)} accepted · entailment unsat in ${fmtMs(
          event.payload.entailment.elapsed_ms,
        )}`,
      };
    case "delegation_rejected":
      return {
        verb: "ESCALATE",
        tone: "text-warning",
        text: `${holderOf(event.payload)} rejected · scope escalation${
          event.payload.naive_disagrees ? " · subset check disagreed" : ""
        }`,
      };
    case "revocation":
      return {
        verb: "REVOKE",
        tone: "text-warning",
        text: `${event.payload.root_id.slice(0, 12)}… · ${event.payload.rows_written} row · ${
          event.payload.cause
        }`,
      };
    case "mandate_granted":
      return {
        verb: "APPEND",
        tone: "text-blue",
        text: `${event.payload.holder} · depth ${event.payload.depth} · ${event.payload.caveat_count} caveats`,
      };
    case "step_up":
      return {
        verb: "STEP UP",
        tone: "text-attention-ink",
        text: event.payload.challenge
          ? `${event.payload.challenge.satisfied ? "satisfied" : "challenged"} · cart ${event.payload.challenge.cart_hash.slice(0, 10)}…`
          : (event.payload.error ?? "challenge"),
      };
    default:
      return { verb: "EVENT", tone: "text-ink-4", text: "" };
  }
}
