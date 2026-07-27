/**
 * Beat 8, the governance kernel, as supporting evidence.
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
 * delegated authority — architecturally identical to the spend limit denying below.
 */

import { ScreenHeader } from "../components/plumblineUi";
import { AttackView } from "./AttackView";
import { DelegationView } from "./DelegationView";
import { ExposureView } from "./ExposureView";
import { KillSwitchView } from "./KillSwitchView";
import { useConsole, type KernelTab } from "../lib/store";

const TABS: { key: KernelTab; label: string; note: string }[] = [
  { key: "attack", label: "Injection", note: "theft, then no theft" },
  { key: "delegation", label: "Delegation", note: "the escalation proof" },
  { key: "kill", label: "Kill switch", note: "one row, four hops" },
  { key: "exposure", label: "Exposure", note: "protection, priced" },
];

export function KernelView() {
  const tab = useConsole((s) => s.kernelTab);
  const setTab = useConsole((s) => s.setKernelTab);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      {/*
        This screen used to build its own header out of a beat number, a rule and a
        paragraph, which made it the one route that did not look like the other seven.
        It uses the shared header now, and the tabs use the same pill the axis selector
        on Controls uses, so a judge learns one selection affordance rather than three.
      */}
      <ScreenHeader beat="08" title="Governance kernel">
        The layer the receipt obligation rides on. The Card Member caveats their own agent;
        no platform is asked for anything.
      </ScreenHeader>

      <nav className="flex shrink-0 flex-wrap gap-1.5">
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setTab(item.key)}
            title={item.note}
            className={`rounded-pill border px-3 py-1.5 text-pill transition-colors ${
              tab === item.key
                ? "border-blue bg-blue text-white"
                : "border-gray-03 text-ink-3 hover:border-blue hover:text-blue"
            }`}
          >
            {item.label}
          </button>
        ))}
      </nav>

      <div className="min-h-0 flex-1">
        {tab === "attack" && <AttackView />}
        {tab === "delegation" && <DelegationView />}
        {tab === "kill" && <KillSwitchView />}
        {tab === "exposure" && <ExposureView />}
      </div>
    </div>
  );
}
