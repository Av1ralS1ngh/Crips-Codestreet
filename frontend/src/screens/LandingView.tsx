/**
 * The landing page, from the Claude Design handoff. A judge lands on the problem, the
 * proof, and the shape of the demo before touching it — not on beat 01 of a demo.
 *
 * Copy here is the deck's copy, and the figures are the deck's figures with their periods
 * attached ($150.00 / $67.18 / $41.40 are the fixture corpus's own numbers as of bef840e;
 * the Q2 2026 lines are from the earnings release of 24 Jul 2026, and the $7.8B
 * annualisation is ours and says so). The demo screens render from the corpus; this page
 * frames them.
 */

import { useConsole } from "../lib/store";
import { BarRow, Takeaway } from "../components/plumblineUi";

const BEATS = [
  { key: "overstatement", beat: "01", label: "Overstatement", note: "the intuitive answer is wrong" },
  { key: "receipt", beat: "02", label: "Receipt", note: "the full candidate set" },
  { key: "omission", beat: "03", label: "Omission", note: "the card that never appeared" },
  { key: "refusal", beat: "04", label: "Refusal", note: "it declines to sign" },
  { key: "degrade", beat: "05", label: "Degrade", note: "enforcement is elected" },
  { key: "attribution", beat: "06", label: "Attribution", note: "which benefits to cut" },
  { key: "controls", beat: "07", label: "Controls", note: "perturb it yourself" },
  { key: "kernel", beat: "08", label: "Kernel", note: "mandates, kill switch, injection" },
] as const;

function Eyebrow({ children, sky = false }: { children: React.ReactNode; sky?: boolean }) {
  return <span className={`eyebrow ${sky ? "text-sky" : ""}`}>{children}</span>;
}

function SectionTitle({ children, maxCh }: { children: React.ReactNode; maxCh?: number }) {
  return (
    <h2
      className="text-section text-balance text-navy"
      style={maxCh ? { maxWidth: `${maxCh}ch` } : undefined}
    >
      {children}
    </h2>
  );
}

export function LandingView() {
  const setScreen = useConsole((s) => s.setScreen);
  const openConsole = () => setScreen("overstatement");
  const goProof = () => {
    const el = document.getElementById("proof");
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="min-h-full bg-white">
      {/* ------------------------------------------------------------------ sticky header */}
      <header className="landing-blur sticky top-0 z-20 flex h-16 items-center gap-6 border-b border-gray-03 bg-white/92 px-10 backdrop-blur-[8px]">
        <div className="flex items-baseline gap-3.5">
          <span className="text-[1.0625rem] font-bold tracking-[0.16em] text-navy">PLUMBLINE</span>
          <span className="num text-[0.71875rem] text-ink-4">value, declared and contestable</span>
        </div>
        <div className="ml-auto flex items-center gap-5">
          <span className="num text-[0.71875rem] tracking-[0.04em] text-ink-3">
            American Express CodeStreet 2026 · PS #5
          </span>
          <button
            type="button"
            onClick={openConsole}
            className="rounded-pill bg-blue px-5 py-2.5 text-[0.84375rem] font-semibold text-white transition-colors duration-[180ms] hover:bg-navy"
          >
            Open the console
          </button>
        </div>
      </header>

      {/* --------------------------------------------------------------------------- hero */}
      <section className="relative overflow-hidden bg-navy text-white">
        <div aria-hidden className="security-field-navy pointer-events-none absolute inset-0" />
        <div className="relative mx-auto grid max-w-[1400px] grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)] items-center gap-[72px] px-10 pt-[104px] pb-[108px]">
          <div className="anim-rise flex flex-col gap-7">
            <Eyebrow sky>A governance layer for financial agents</Eyebrow>
            <h1 className="max-w-[15ch] text-hero text-balance">Proving what a card is worth.</h1>
            <p className="max-w-[52ch] text-[1.25rem] leading-[1.5] text-pretty text-[#C9D8E8]">
              When an agent picks a card on your behalf, it states a number. Plumbline makes
              that number checkable; a receipt that names every candidate it considered and
              an allocation anyone can re-add by hand.
            </p>
            <div className="flex items-center gap-4 pt-1.5">
              <button
                type="button"
                onClick={openConsole}
                className="rounded-pill bg-blue px-[30px] py-[15px] text-data font-semibold text-white transition-colors duration-[180ms] hover:bg-blue-bright"
              >
                Walk the eight beats
              </button>
              <button
                type="button"
                onClick={goProof}
                className="rounded-pill border border-white/34 px-[30px] py-[15px] text-data font-semibold text-white transition-colors duration-[180ms] hover:border-white hover:bg-white/8"
              >
                See the proof
              </button>
            </div>
          </div>

          {/* The one shadow in the whole design. */}
          <div className="anim-rise-late shadow-proof-card flex flex-col bg-white text-ink [box-shadow:0_24px_60px_rgba(0,10,40,0.34)]">
            <div className="strip bg-blue px-[26px] py-4 text-white">One basket, two answers</div>
            <div className="flex flex-col gap-[26px] px-[26px] pt-[30px] pb-[26px]">
              <BarRow
                label="Value the control agent claimed"
                value="$150.00"
                pct={100}
                sub="marketing copy only · nothing to check it against"
              />
              <BarRow
                label="What the winning card actually realises"
                value="$67.18"
                pct={44.8}
                fill="bg-blue"
                valueClass="text-blue"
                sub="witness-backed allocation on a $1,078 basket"
              />
              <div className="grid grid-cols-2 gap-px border-t border-gray-03 bg-gray-03">
                <div className="bg-white pt-[18px] pr-1 pb-1">
                  <div className="colhead tracking-[0.14em]">Control run</div>
                  <div className="mt-[7px] text-[1.1875rem] font-bold text-navy">Amex Platinum</div>
                  <div className="num mt-[5px] text-pill font-normal text-warning">
                    DEVIATES · $41.40 left
                  </div>
                </div>
                <div className="bg-white pt-[18px] pb-1 pl-[18px]">
                  <div className="colhead tracking-[0.14em]">Derived run</div>
                  <div className="mt-[7px] text-[1.1875rem] font-bold text-blue">Amex Gold</div>
                  <div className="num mt-[5px] text-pill font-normal text-success">
                    FAITHFUL · none left
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ------------------------------------------------------------------- the problem */}
      <section className="mx-auto max-w-[1400px] px-10 pt-[104px]">
        <div className="grid grid-cols-2 items-start gap-20">
          <div className="flex flex-col gap-5">
            <Eyebrow>The problem</Eyebrow>
            <SectionTitle maxCh={17}>Benefit cost is rising. Nothing records the cause.</SectionTitle>
            <p className="max-w-[54ch] text-standfirst text-pretty text-ink-2">
              Card Member Services expense grew far faster than the card fees it is meant to
              justify. A member draws a credit, the expense lands on the P&L, and no
              instrument anywhere states which benefit caused that card to be chosen.
            </p>
            <div className="flex flex-col gap-[26px] pt-[22px]">
              <BarRow label="Card Member Services expense" value="+50%" pct={100} sub="$1,949M · Q2 2026" />
              <BarRow
                label="Net card fees"
                value="+15%"
                pct={30}
                fill="bg-blue"
                valueClass="text-blue"
                sub="$2,862M · Q2 2026"
              />
              <BarRow
                label="Total revenues, net of interest"
                value="+10%"
                pct={20}
                fill="bg-sky"
                valueClass="text-ink-2"
                sub="$19,637M · Q2 2026"
              />
            </div>
          </div>

          <div className="mt-16 flex flex-col">
            <div className="strip bg-navy px-7 py-4 text-white">What this shows</div>
            <div className="flex flex-col gap-[26px] bg-field px-7 py-[34px]">
              <p className="text-[1.125rem] leading-[1.55] text-ink">
                Three straight quarters of the same shape: +53%, +49%, +50%.
              </p>
              <div className="h-px bg-gray-03" />
              <p className="text-[1.125rem] leading-[1.55] text-ink">
                A step change in level, not a run-rate. It decays from Q4 2026.
              </p>
              <div className="h-px bg-gray-03" />
              <p className="text-[1.125rem] leading-[1.55] text-ink">
                Annualised, roughly <strong>$7.8B</strong>; our derivation, not an Amex figure.
              </p>
            </div>
            <div className="border border-t-0 border-gray-03 bg-white px-7 py-[22px] text-card-title font-normal leading-relaxed text-ink-2">
              A nudge product converts breakage into recognised expense. Without a record of
              causation, you cannot tell which of those dollars bought a decision.
            </div>
          </div>
        </div>
      </section>

      {/* ------------------------------------------------------------------ how it works */}
      <section className="mx-auto max-w-[1400px] px-10 pt-[104px]">
        <div className="flex max-w-[60ch] flex-col gap-[18px]">
          <Eyebrow>How it works</Eyebrow>
          <SectionTitle>Three obligations, in order.</SectionTitle>
        </div>
        <div className="mt-11 grid grid-cols-3 gap-px border border-gray-03 bg-gray-03">
          {[
            {
              n: "01",
              title: "Declare the candidate set",
              body: "The receipt names every instrument the agent considered, not only the one it picked. A card that never appears still leaves a signature in the log.",
            },
            {
              n: "02",
              title: "Exhibit the allocation",
              body: "No value is asserted without a concrete allocation that realises it. The witness is the derivation, and checking one is arithmetic; no solver, linear time.",
            },
            {
              n: "03",
              title: "Caveat your own agent",
              body: "The receipt obligation is a condition the Card Member puts on their own agent. No platform is asked for anything, and no platform sees the check.",
            },
          ].map((card) => (
            <div
              key={card.n}
              className="flex flex-col gap-4 bg-white px-[34px] py-10 transition-colors duration-200 hover:bg-gray-01"
            >
              <span className="num text-pill font-semibold tracking-[0.14em] text-blue">{card.n}</span>
              <h3 className="text-[1.5rem] leading-[1.2] font-bold tracking-[-0.01em] text-navy">
                {card.title}
              </h3>
              <p className="text-[0.96875rem] leading-[1.62] text-ink-2">{card.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* --------------------------------------------------------------------- the proof */}
      <section id="proof" className="mx-auto max-w-[1400px] scroll-mt-20 px-10 pt-[104px]">
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,0.82fr)] items-start gap-[72px]">
          <div className="flex flex-col gap-5">
            <Eyebrow>The proof</Eyebrow>
            <SectionTitle maxCh={18}>Same model, same basket, two answers.</SectionTitle>
            <p className="max-w-[56ch] text-standfirst text-pretty text-ink-2">
              One run had only marketing copy. The other could check its own numbers. It chose
              a different card and left no money on the table.
            </p>

            <div className="mt-6 flex flex-col border-t-2 border-navy">
              <div className="grid grid-cols-[minmax(0,1.5fr)_1fr_1fr] gap-5 border-b border-gray-03 py-3.5">
                <span />
                <span className="colhead tracking-[0.14em] text-right">Control</span>
                <span className="colhead tracking-[0.14em] text-right">Derived</span>
              </div>
              <div className="grid grid-cols-[minmax(0,1.5fr)_1fr_1fr] items-baseline gap-5 border-b border-gray-03 py-5">
                <span className="text-body text-ink">Card chosen</span>
                <span className="text-right text-[1.0625rem] font-bold text-navy">Amex Platinum</span>
                <span className="text-right text-[1.0625rem] font-bold text-blue">Amex Gold</span>
              </div>
              <div className="grid grid-cols-[minmax(0,1.5fr)_1fr_1fr] items-baseline gap-5 border-b border-gray-03 py-5">
                <span className="text-body text-ink">Money left on the table</span>
                <span className="num text-right text-[1.0625rem] font-semibold text-navy">$41.40</span>
                <span className="num text-right text-[1.0625rem] font-semibold text-blue">none</span>
              </div>
              <div className="grid grid-cols-[minmax(0,1.5fr)_1fr_1fr] items-baseline gap-5 py-5">
                <span className="text-body text-ink">Receipt verdict</span>
                <span className="num text-right text-data font-semibold tracking-[0.05em] text-warning">
                  DEVIATES
                </span>
                <span className="num text-right text-data font-semibold tracking-[0.05em] text-success">
                  FAITHFUL
                </span>
              </div>
            </div>
          </div>

          <div className="flex flex-col">
            <div className="strip bg-blue px-[26px] py-4 text-white">Why Platinum lost</div>
            <div className="flex flex-col gap-5 bg-field px-[26px] py-[30px]">
              <p className="text-[0.96875rem] leading-[1.6] text-ink-2">
                All four headline benefits match no line in this basket:
              </p>
              <div className="flex flex-col gap-2">
                {["5× on prepaid hotels", "3× on flights", "$100 hotel credit", "$50 Resy credit"].map(
                  (benefit) => (
                    <div
                      key={benefit}
                      className="flex items-center justify-between gap-4 border-l-[3px] border-navy bg-white px-[18px] py-[15px]"
                    >
                      <span className="text-[0.96875rem] font-bold text-navy">{benefit}</span>
                      <span className="num text-label tracking-[0.12em] text-ink-4 uppercase">
                        NO LINE
                      </span>
                    </div>
                  ),
                )}
              </div>
              <p className="text-[0.96875rem] leading-[1.6] text-ink-2">
                Platinum is a travel card. Marketing describes a card in general. A purchase
                happens specifically.
              </p>
              <p className="text-[0.96875rem] leading-[1.6] font-bold text-navy">
                Ranked here: Gold $67.18, Chase Sapphire $31.70, Platinum $25.78.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ------------------------------------------------------------------- the console */}
      <section className="mx-auto max-w-[1400px] px-10 pt-[104px]">
        <div className="flex flex-wrap items-end justify-between gap-10">
          <div className="flex max-w-[56ch] flex-col gap-[18px]">
            <Eyebrow>The console</Eyebrow>
            <SectionTitle>Eight beats, in order.</SectionTitle>
          </div>
          <p className="max-w-[40ch] text-[0.96875rem] leading-[1.6] text-ink-3">
            Five on valuation, two on the decision it drives, one on the kernel that makes the
            obligation enforceable. Pick any beat to open it.
          </p>
        </div>
        <div className="mt-11 grid grid-cols-4 gap-px border border-gray-03 bg-gray-03">
          {BEATS.map((beat) => (
            <button
              key={beat.key}
              type="button"
              onClick={() => setScreen(beat.key)}
              className="flex flex-col items-start gap-3 bg-white px-[26px] pt-8 pb-[34px] text-left transition-colors duration-200 hover:bg-field"
            >
              <span className="num text-[0.71875rem] font-semibold tracking-[0.14em] text-blue">
                {beat.beat}
              </span>
              <span className="text-[1.3125rem] font-bold tracking-[-0.01em] text-navy">
                {beat.label}
              </span>
              <span className="text-card-title font-normal leading-normal text-ink-3">
                {beat.note}
              </span>
            </button>
          ))}
        </div>
      </section>

      {/* ----------------------------------------------------------- statement and footer */}
      <section className="mx-auto max-w-[1400px] px-10 pt-24">
        <Takeaway>
          The receipt corpus is that missing record. It is the first instrument that can say
          which benefits to cut.
        </Takeaway>
      </section>

      <footer className="mx-auto flex max-w-[1400px] flex-wrap items-center justify-between gap-8 px-10 pt-10 pb-[72px]">
        <span className="num text-pill font-normal text-ink-4">
          Source Amex Q2 2026 earnings release, 24 Jul 2026 · $7.8B annualisation is ours
        </span>
        <span className="num text-pill font-normal text-ink-4">plumbline-console.vercel.app</span>
      </footer>
    </div>
  );
}
