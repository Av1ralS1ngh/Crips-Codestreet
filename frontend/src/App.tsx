import { useEffect, useRef } from "react";
import { CopyHash, LogoClearZone, ToastHost } from "./components/amex";
import { EvidenceDrawer } from "./components/EvidenceDrawer";
import { Toggle } from "./components/ui";
import { useConsole, type ScreenKey, type UiScale } from "./lib/store";
import { AttributionView } from "./screens/AttributionView";
import { ControlsView } from "./screens/ControlsView";
import { DegradeView } from "./screens/DegradeView";
import { KernelView } from "./screens/KernelView";
import { LandingView } from "./screens/LandingView";
import { OmissionView } from "./screens/OmissionView";
import { OverstatementView } from "./screens/OverstatementView";
import { ReceiptView } from "./screens/ReceiptView";
import { RefusalView } from "./screens/RefusalView";

/**
 * The demo, in order, grouped the way the rail presents it: five beats on valuation, two
 * on the decision it drives, one on the kernel that makes the obligation enforceable. The
 * flat list of eight gave a judge no sense of where they were; the groups are the fix.
 */
const SCREENS: {
  key: ScreenKey;
  beat: string;
  label: string;
  note: string;
  group: "valuation" | "decision" | "enforcement";
}[] = [
  { key: "overstatement", beat: "01", label: "Overstatement", note: "the intuitive answer is wrong", group: "valuation" },
  { key: "receipt", beat: "02", label: "Receipt", note: "the full candidate set", group: "valuation" },
  { key: "omission", beat: "03", label: "Omission", note: "the card that never appeared", group: "valuation" },
  { key: "refusal", beat: "04", label: "Refusal", note: "it declines to sign", group: "valuation" },
  { key: "degrade", beat: "05", label: "Degrade", note: "enforcement is elected", group: "valuation" },
  { key: "attribution", beat: "06", label: "Attribution", note: "which benefits to cut", group: "decision" },
  { key: "controls", beat: "07", label: "Controls", note: "perturb it yourself", group: "decision" },
  { key: "kernel", beat: "08", label: "Kernel", note: "mandates, kill switch, injection", group: "enforcement" },
];

const GROUPS: { key: (typeof SCREENS)[number]["group"]; label: string }[] = [
  { key: "valuation", label: "Valuation" },
  { key: "decision", label: "Decision" },
  { key: "enforcement", label: "Enforcement" },
];

export default function App() {
  const init = useConsole((s) => s.init);
  const screen = useConsole((s) => s.screen);
  const setScreen = useConsole((s) => s.setScreen);
  const mode = useConsole((s) => s.mode);
  const setMode = useConsole((s) => s.setMode);
  const scale = useConsole((s) => s.scale);
  const setScale = useConsole((s) => s.setScale);
  const state = useConsole((s) => s.state);
  const loadError = useConsole((s) => s.loadError);
  const mainRef = useRef<HTMLElement>(null);

  useEffect(() => {
    void init();
    return () => useConsole.getState().disconnect?.();
  }, [init]);

  // Every navigation scrolls the main pane back to the top on the next frame; without it
  // a judge lands mid-screen on every beat after the first scroll.
  const go = (key: ScreenKey) => {
    setScreen(key);
    requestAnimationFrame(() => {
      mainRef.current?.scrollTo(0, 0);
      if (key === "landing") window.scrollTo(0, 0);
    });
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const index = Number(event.key) - 1;
      if (index >= 0 && index < SCREENS.length) {
        setScreen(SCREENS[index].key);
        requestAnimationFrame(() => mainRef.current?.scrollTo(0, 0));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setScreen]);

  if (screen === "landing") {
    return (
      <div className="h-full overflow-y-auto bg-white">
        <LandingView />
        <ToastHost />
      </div>
    );
  }

  const current = SCREENS.findIndex((s) => s.key === screen);
  const prev = current > 0 ? SCREENS[current - 1] : null;
  const next = current >= 0 && current < SCREENS.length - 1 ? SCREENS[current + 1] : null;

  return (
    <div className="flex h-full flex-col overflow-hidden bg-white">
      {/*
        60px, white, one hairline. The wordmark returns to the landing page. The clear zone
        at the right end stays reserved and empty on every route; the console never draws
        the Blue Box.
      */}
      <header className="flex h-[60px] shrink-0 items-center gap-6 border-b border-gray-03 bg-white px-7">
        <button
          type="button"
          onClick={() => go("landing")}
          className="flex items-baseline gap-3 text-left"
          title="back to the overview"
        >
          <span className="text-[0.96875rem] font-bold tracking-[0.16em] text-navy">PLUMBLINE</span>
          <span className="num text-label font-normal tracking-normal text-ink-4 normal-case">
            value, declared and contestable
          </span>
        </button>

        <div className="ml-auto flex items-center gap-3">
          <CopyHash value={state?.ledger_root} head={12} tail={8} label="log root" />
          <Toggle
            value={scale}
            onChange={(value) => setScale(value as UiScale)}
            options={[
              { value: "s", label: "S" },
              { value: "m", label: "M" },
              { value: "l", label: "L" },
            ]}
          />
          {/*
            RECORDED is fixtures the engine produced; LIVE is a socket to a running kernel.
            The dot and border carry the state; the click still switches transport.
          */}
          <button
            type="button"
            onClick={() => void setMode(mode === "mock" ? "live" : "mock")}
            title={
              mode === "live"
                ? "connected to a running kernel; click for recorded engine output"
                : "recorded engine output; click to connect to a running kernel"
            }
            className={`num inline-flex items-center gap-2 rounded-pill border bg-white px-3.5 py-[7px] text-label tracking-[0.1em] transition-colors duration-[180ms] ${
              mode === "live" ? "border-blue text-blue" : "border-success text-success"
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-pill ${mode === "live" ? "bg-blue" : "bg-success"}`}
            />
            {mode === "live" ? "LIVE" : "RECORDED"}
          </button>
          <button
            type="button"
            onClick={() => go("landing")}
            className="rounded-pill border border-gray-03 bg-white px-4 py-2 text-[0.78125rem] font-semibold text-navy transition-colors duration-[180ms] hover:border-blue hover:text-blue"
          >
            Overview
          </button>
        </div>

        <LogoClearZone />
      </header>

      {loadError && (
        <div className="flex shrink-0 items-center gap-3 border-b border-gray-03 bg-white px-7 py-2.5">
          <span className="num text-pill tracking-[0.06em] text-warning">TRANSPORT</span>
          <span className="min-w-0 flex-1 break-words text-code text-ink-2">{loadError}</span>
          {mode === "live" && (
            <button
              type="button"
              onClick={() => void setMode("mock")}
              className="shrink-0 rounded-pill border border-gray-03 bg-white px-3 py-1.5 text-pill text-navy hover:border-blue hover:text-blue"
            >
              switch to recorded
            </button>
          )}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* The rail: three labelled groups, so a judge always knows where they are. */}
        <nav className="flex w-[268px] shrink-0 flex-col gap-1 overflow-y-auto border-r border-gray-03 bg-white px-3.5 py-[22px]">
          {GROUPS.map((group, gi) => (
            <div key={group.key} className="flex flex-col gap-1">
              <span
                className={`colhead px-3.5 tracking-[0.18em] ${gi === 0 ? "pt-2" : "pt-5"} pb-1.5`}
              >
                {group.label}
              </span>
              {SCREENS.filter((s) => s.group === group.key).map((item) => {
                const active = screen === item.key;
                return (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() => go(item.key)}
                    className={`ring-rail flex w-full flex-col gap-[5px] rounded-[6px] px-3.5 py-[11px] text-left transition-[background-color,box-shadow] duration-[180ms] hover:[box-shadow:inset_0_0_0_1px_#006FCF] ${
                      active ? "bg-blue" : "bg-transparent"
                    }`}
                  >
                    <span className="flex items-center gap-[11px]">
                      <span
                        className={`num flex h-6 w-6 shrink-0 items-center justify-center rounded-pill text-[0.65625rem] ${
                          active ? "bg-white text-blue" : "bg-gray-02 text-navy"
                        }`}
                      >
                        {item.beat}
                      </span>
                      <span
                        className={`text-card-title ${active ? "text-white" : "text-navy"}`}
                      >
                        {item.label}
                      </span>
                    </span>
                    <span
                      className={`pl-[35px] text-[0.78125rem] leading-[1.4] ${
                        active ? "text-[#BFDCF5]" : "text-ink-4"
                      }`}
                    >
                      {item.note}
                    </span>
                  </button>
                );
              })}
            </div>
          ))}

          <p className="mt-auto border-t border-gray-02 px-3.5 pt-6 pb-2 text-[0.8125rem] leading-[1.6] text-ink-4">
            No value is asserted without an allocation that realises it.
          </p>
        </nav>

        {/*
          The main pane scrolls; screens are documents, not viewport-locked dashboards.
          The security field must be a child of the scroll CONTENT: as a child of the
          scroll container, inset-0 resolves to the scrollport and the field stops after
          one viewport height.
        */}
        <main ref={mainRef} className="relative min-w-0 flex-1 overflow-y-auto bg-white">
          <div className="relative min-h-full px-12 pt-11 pb-[72px]">
            <div aria-hidden className="security-field pointer-events-none absolute inset-0 z-0" />
            {screen === "overstatement" && <OverstatementView />}
            {screen === "receipt" && <ReceiptView />}
            {screen === "omission" && <OmissionView />}
            {screen === "refusal" && <RefusalView />}
            {screen === "degrade" && <DegradeView />}
            {screen === "attribution" && <AttributionView />}
            {screen === "controls" && <ControlsView />}
            {screen === "kernel" && <KernelView />}

            {/* Pager: past either end returns to the landing page. */}
            <div className="relative z-[1] mt-[34px] flex max-w-[1280px] items-center justify-between gap-6 border-t border-gray-03 pt-5">
              <button
                type="button"
                onClick={() => go(prev ? prev.key : "landing")}
                className="rounded-pill px-2 py-1 text-card-title text-ink-3 transition-colors duration-[180ms] hover:text-blue"
              >
                ← {prev ? prev.label : "Overview"}
              </button>
              <span className="num text-pill font-normal tracking-[0.1em] text-ink-4 uppercase">
                Beat {SCREENS[current]?.beat ?? "—"} of 08
              </span>
              <button
                type="button"
                onClick={() => go(next ? next.key : "landing")}
                className="rounded-pill px-2 py-1 text-card-title text-ink-3 transition-colors duration-[180ms] hover:text-blue"
              >
                {next ? next.label : "Overview"} →
              </button>
            </div>
          </div>
        </main>
      </div>

      <EvidenceDrawer />
      <ToastHost />
    </div>
  );
}
