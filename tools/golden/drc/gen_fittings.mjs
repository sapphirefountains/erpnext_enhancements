// Golden vectors for engine/hydraulics/fittings.py: DRC src/hydro/common.js FITTINGS, fittingK, fitSumK.
// Closed-form math, matched at 1e-12. fitSumK adds in Object.entries order, so the maps below are
// written in the order the sum must be taken.
// Usage (repo root): node tools/golden/drc/gen_fittings.mjs [path-to-digital-rain-curtain]
import { patchCatalog, writeGolden } from './drc.mjs';

const C = await patchCatalog();

const fittings = [];
for (const key of [...Object.keys(C.FITTINGS), 'nope'])
  for (const Re of [NaN, 0.5, 500, 2e4, 1e6]) for (const idMm of [0.5, 12, 26.6446, 52.5018, 102.2604]) fittings.push({ key, Re, idMm, k: C.fittingK(key, Re, idMm) });

const maps = [
  { elbow90: 2, check: 1, ball: 1 }, { strainer: 1, filter: 1, entrance: 1 }, { footValve: 1, teeBranch: 3, nope: 2 },
  { coupling: 0, gate: -1 }, { check: 1, ball: 1, elbow90: 2 }, { elbow90: 2, teeBranch: 1 }, { entrance: 1, strainer: 1, elbow90: 1 },
  { elbow45: 1.5, globe: 1, liftCheck: 1, quick: 2, barb: 4, reducer: 1, expander: 1, bend180: 1, teeRun: 2, butterfly: 1, elbow90lr: 3 },
];
const fitSums = [];
for (const fit of maps)
  for (const clog of [0, 0.3, 0.5, 0.9, 0.99, -0.2])
    for (const [Re, idMm] of [[2e4, 26.6446], [800, 15], [3e5, 102.2604]]) fitSums.push({ fit, Re, idMm, clog, k: C.fitSumK(fit, Re, idMm, clog) });

writeGolden('fittings', 'digital-rain-curtain/src/hydro/common.js', { keys: Object.keys(C.FITTINGS), fittings, fitSums });
