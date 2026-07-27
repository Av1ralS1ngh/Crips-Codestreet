/**
 * The perturbation corpus: recorded engine output, used identically in both transports.
 *
 * Not in `src/mock/`, and the distinction is load-bearing. A mock is what stands in for the
 * backend when the backend is not there. This is not that: every stop on every axis is a
 * real run of `plumbline.allocate` over a manifest with one field changed, recorded by
 * `scripts/gen_plumbline_beats.py`, and the console re-verifies each one in the browser
 * against a manifest it perturbs for itself. There is nothing for a live backend to serve
 * differently, and a control a judge is holding must not go dark because a route elsewhere
 * is serving a different corpus.
 *
 * The corpus carries its own cart and its own signed manifest bodies for the same reason: a
 * witness can only be checked against the manifest it was computed from.
 */

import type { PerturbationBook } from "../lib/plumbline";
import raw from "./plumblinePerturbations.json";

export const PERTURBATION = raw as unknown as PerturbationBook;
