/* =====================================================================================
 * MOCK PLUMBLINE TRANSPORT — NOT THE BACKEND.
 *
 * Swapped out wholesale by the single flag in lib/transport.ts, exactly like
 * mockClient.ts. Nothing outside the mock layer knows this file exists.
 *
 * What is real in `plumblineFixtures.json`: everything structural, and — this is the part that
 * matters — it is not a recording of a second implementation. The file is the output of
 * `plumbline.console.build_state`, which is the function behind `GET /api/plumbline/state`.
 * `scripts/gen_plumbline_fixtures.py` calls it and writes the bytes; the live route calls it
 * and serves them. `backend/tests/test_plumbline_console_state.py` diffs the two, so mock mode
 * and live mode cannot disagree about a card, a rate, a witness or a root. Two producers of
 * one envelope is how a console ends up showing a card the backend has never heard of.
 *
 * What is modelled, and labelled as such wherever it appears on screen: the card terms come
 * from `backend/plumbline/products.py`, read off each issuer's published terms with no live
 * Offers feed and with every remaining balance synthetic; the per-benefit annual costs that
 * key the attribution 2x2; and the 180-receipt corpus. Signatures are HMAC under prototype
 * keys.
 *
 * One instrument in the candidate set — Hypothetical Bank's Illustrative Reserve — is
 * invented outright, and its own provenance line opens by saying so. It carries the
 * statement-credit case, which the published Indian catalogue does not have, and it is the
 * only instrument whose RATE the perturbation screen will move. The alternative was to hang
 * invented credits off a real issuer's product name, which is the one thing this system may
 * never do.
 *
 * `plumblineDegrade.json` is a recording of one transport call rather than a state: it is
 * `plumbline.scenarios.graceful_degrade` verbatim, and because that scenario runs on a fixed
 * clock the recording is not an approximation of the live route's answer, it IS the live
 * route's answer. The perturbation corpus is not here — both transports use it, so it
 * lives in `src/data/` outside the mock layer.
 * ===================================================================================== */

import type { DegradeRun, PlumblineState } from "../lib/plumbline";
import raw from "./plumblineFixtures.json";
import degradeRaw from "./plumblineDegrade.json";

const fixture = raw as unknown as PlumblineState;
const degrade = degradeRaw as unknown as DegradeRun;

const sleep = (msec: number) => new Promise<void>((resolve) => setTimeout(resolve, msec));

export async function mockPlumblineState(): Promise<PlumblineState> {
  await sleep(70);
  return structuredClone(fixture);
}

/**
 * The recorded graceful-degrade run, byte-identical to what the live route returns —
 * `plumbline.scenarios` runs on a fixed clock, so this recording is not an approximation of
 * the backend's answer, it is the backend's answer.
 */
export async function mockDegrade(): Promise<DegradeRun> {
  await sleep(120);
  return structuredClone(degrade);
}
