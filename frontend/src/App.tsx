import { useEffect } from "react";
import { CopyHash, LogoClearZone, ToastHost, Wordmark } from "./components/amex";
import { WorldServicePattern } from "./components/WorldServicePattern";
import { EvidenceDrawer } from "./components/EvidenceDrawer";
import { FeedRail } from "./components/FeedRail";
import { Toggle } from "./components/ui";
import { useConsole, type ScreenKey, type UiScale } from "./lib/store";
import { AttributionView } from "./screens/AttributionView";
import { ControlsView } from "./screens/ControlsView";
import { DegradeView } from "./screens/DegradeView";
import { KernelView } from "./screens/KernelView";
import { OmissionView } from "./screens/OmissionView";
import { OverstatementView } from "./screens/OverstatementView";
import { ReceiptView } from "./screens/ReceiptView";
import { RefusalView } from "./screens/RefusalView";

/**
 * The demo, in order. Seven beats about value disclosure, then the governance kernel that
 * makes the disclosure obligation enforceable — supporting evidence rather than headline.
 *
 * Degrade follows refusal because the two answer opposite halves of one question: what
 * happens when a claim cannot be supported, and what happens when no claim arrives at all.
 * Controls comes after the attribution close, because it is the beat where the argument
 * stops being ours and a skeptic drives it instead.
 */
const SCREENS: { key: ScreenKey; beat: string; label: string; note: string }[] = [
  { key: "overstatement", beat: "01", label: "Overstatement", note: "the intuitive answer is wrong" },
  { key: "receipt", beat: "02", label: "Receipt", note: "the full candidate set" },
  { key: "omission", beat: "03", label: "Omission", note: "the card that never appeared" },
  { key: "refusal", beat: "04", label: "Refusal", note: "it declines to sign" },
  { key: "degrade", beat: "05", label: "Degrade", note: "it proceeds; enforcement is elected" },
  { key: "attribution", beat: "06", label: "Attribution", note: "which benefits to cut" },
  { key: "controls", beat: "07", label: "Controls", note: "perturb it yourself" },
  { key: "kernel", beat: "08", label: "Kernel", note: "mandates, kill switch, injection" },
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

  useEffect(() => {
    void init();
    return () => useConsole.getState().disconnect?.();
  }, [init]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const index = Number(event.key) - 1;
      if (index >= 0 && index < SCREENS.length) setScreen(SCREENS[index].key);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setScreen]);

  return (
    <div className="flex h-full flex-col overflow-hidden bg-white">
      {/*
        The application bar is white with navy text and a bottom hairline. It is not a
        coloured band: in this system colour lives in components, never in page chrome. The
        clear zone at the right end is 240 x 48, reserved on every route and left empty. The
        console never draws the Blue Box.
      */}
      <header className="flex shrink-0 items-center gap-6 border-b border-gray-03 bg-white px-6 py-3">
        <div className="flex items-baseline gap-3">
          <Wordmark tone="onWhite" />
          <span className="text-code text-ink-4">value, declared and contestable</span>
        </div>

        {/*
          One pill, not three. The log root is the only header fact that carries argument:
          it says there is an append-only log and this is where it stands. Entry and
          instrument counts are inventory, they belong on the screens that use them, and a
          count pill reading zero because a transport is down reads as a broken product.
        */}
        <div className="ml-auto flex items-center gap-2">
          <CopyHash value={state?.ledger_root} head={8} tail={6} label="log root" />
        </div>

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
          "RECORDED", not "MOCK". On the hosted build there is no Python process to talk
          to, so the console reads fixtures — but those fixtures are output the real engine
          produced, not numbers written by hand, and "MOCK" invites a judge to assume the
          opposite. LIVE still means a socket to a running kernel.
        */}
        <button
          type="button"
          onClick={() => void setMode(mode === "mock" ? "live" : "mock")}
          title={
            mode === "live"
              ? "connected to a running kernel; click for recorded engine output"
              : "recorded engine output; click to connect to a running kernel"
          }
          className={`rounded-pill border bg-white px-3.5 py-2 font-mono text-pill tracking-[0.06em] transition-colors ${
            mode === "live" ? "border-success text-success" : "border-gray-03 text-ink-3 hover:border-blue hover:text-blue"
          }`}
        >
          {mode === "live" ? "LIVE" : "RECORDED"}
        </button>

        <LogoClearZone />
      </header>

      {loadError && (
        <div className="flex shrink-0 items-center gap-3 border-b border-gray-03 bg-white px-6 py-2.5">
          <span className="num text-pill tracking-[0.06em] text-warning">TRANSPORT</span>
          <span className="min-w-0 flex-1 break-words text-code text-ink-2">{loadError}</span>
          {mode === "live" && (
            <button
              type="button"
              onClick={() => void setMode("mock")}
              className="shrink-0 rounded-pill border border-gray-03 bg-white px-3 py-1.5 text-pill text-navy hover:border-blue hover:text-blue"
            >
              switch to mock
            </button>
          )}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* Selected is bright blue: in this system blue carries interaction and selection. */}
        <nav className="flex w-[15.5rem] shrink-0 flex-col gap-1 overflow-y-auto border-r border-gray-03 bg-white p-3">
          {SCREENS.map((item) => {
            const active = screen === item.key;
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => setScreen(item.key)}
                className={`flex flex-col gap-1 rounded-card px-3 py-2.5 text-left transition-colors ${
                  active ? "bg-blue" : "hover:bg-gray-01"
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <span
                    className={`num flex h-6 w-6 shrink-0 items-center justify-center rounded-pill text-pill ${
                      active ? "bg-white text-blue" : "bg-gray-02 text-navy"
                    }`}
                  >
                    {item.beat}
                  </span>
                  <span
                    className={`text-data font-bold ${active ? "text-white" : "text-navy"}`}
                  >
                    {item.label}
                  </span>
                </div>
                <span className={`text-code ${active ? "text-sky" : "text-ink-4"}`}>
                  {item.note}
                </span>
              </button>
            );
          })}

          {/*
            The thesis, in one line. It used to be two paragraphs here, which is a wall of
            small text a presenter never reads aloud and a judge never finishes. The long
            form belongs on Overstatement, where it is the point of the screen.
          */}
          <p className="mt-auto px-3 py-3 text-code leading-relaxed text-ink-4">
            No value is asserted without an allocation that realises it.
          </p>
        </nav>

        {/*
          The engraving sits behind the content at onWhite opacity, the way security paper
          carries its field: visible only in the gaps between panels, never under text.
          Panels are opaque, so data always reads against a solid surface.
        */}
        <main className="relative min-w-0 flex-1 overflow-hidden bg-white">
          <WorldServicePattern tone="onWhite" className="absolute inset-0" />
          <div className="relative h-full min-h-0 p-6">
            {screen === "overstatement" && <OverstatementView />}
            {screen === "receipt" && <ReceiptView />}
            {screen === "omission" && <OmissionView />}
            {screen === "refusal" && <RefusalView />}
            {screen === "degrade" && <DegradeView />}
            {screen === "attribution" && <AttributionView />}
            {screen === "controls" && <ControlsView />}
            {screen === "kernel" && <KernelView />}
          </div>
        </main>

        {/*
          The decision feed rides with the kernel and only with it. Nothing on the five
          valuation screens passes through the PDP, so carrying an empty rail across them
          would spend a fifth of the projector on a panel that says "no traffic" — and would
          quietly teach a judge that the rail means nothing.
        */}
        {screen === "kernel" && <FeedRail />}
      </div>

      <EvidenceDrawer />
      <ToastHost />
    </div>
  );
}
