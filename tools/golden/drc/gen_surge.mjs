// Golden vectors for engine/hydraulics/surge.py: DRC src/hydro/transient.js supplyWave, manifoldWave, surgeFor,
// accumulatorGasL, and presets.js stdGasL. Each scenario records the supply segments and the manifold inputs (as
// gen_manifold.mjs does) so the Python side can rebuild the same line.
// Usage (repo root): node tools/golden/drc/gen_surge.mjs [path-to-digital-rain-curtain]
import { patchCatalog, load, makeP, writeGolden } from './drc.mjs';

const C = await patchCatalog();
const T = await load('src/hydro/transient.js');
const S = await load('src/hydro/presets.js');

const mani = P => ({
  valves: P.hw.valves, pitchMm: P.hw.pitchMm, orificeMm: P.hw.orificeMm, cv: P.hw.cv, v0: P.hw.v0,
  nozzleCc: P.plumb.nozzleCc, valveKv: P.plumb.valveKv, tubeIdMm: P.plumb.tubeIdMm, tubeLenM: P.plumb.tubeLenM,
  tiltMm: P.plumb.tiltMm, sag: P.plumb.sag, hangerM: P.plumb.hangerM, nozzleBelowM: P.plumb.nozzleBelowM,
  regain: P.plumb.regain, feed: P.hyd.feed, waterC: P.plumb.waterC, manMat: P.plumb.manMat, manNps: P.plumb.manNps,
  pipeIdMm: P.hyd.pipeIdMm, manWallMm: P.plumb.manWallMm,
});
const seg = o => ({ name: '', mat: 'pvc40', nps: '1', idMm: 26.6, lenM: 1, share: 'all', kExtra: 0, fit: {}, ...o });
const VARIANTS = [
  ['default system', P => { P.plumb.model = 'system'; }],
  ['simple model (manifold is the trunk)', P => { P.plumb.model = 'simple'; }],
  ['long 2in trunk', P => { P.plumb.model = 'system'; P.plumb.supply = [seg({ nps: '2', lenM: 30 }), seg({ nps: '1-1/2', lenM: 6 })]; }],
  ['half branches only', P => { P.plumb.model = 'system'; P.plumb.supply = [seg({ lenM: 4, share: 'half' }), seg({ lenM: 3, share: 'half' })]; }],
  ['mixed with half', P => { P.plumb.model = 'system'; P.plumb.supply = [seg({ nps: '1-1/4', lenM: 8 }), seg({ lenM: 3, share: 'half' })]; }],
  ['hose', P => { P.plumb.model = 'system'; P.plumb.supply = [seg({ mat: 'hose', lenM: 10 })]; }],
  ['copper + steel', P => { P.plumb.model = 'system'; P.plumb.supply = [seg({ mat: 'copperL', lenM: 5 }), seg({ mat: 'steel40', nps: '1-1/4', lenM: 12 })]; }],
  ['zero-length supply', P => { P.plumb.model = 'system'; P.plumb.supply = [seg({ lenM: 0 })]; }],
  ['warm water small manifold', P => { P.plumb.model = 'system'; P.plumb.waterC = 40; P.hw.valves = 32; P.hyd.pipeIdMm = 20; }],
];
const scenarios = [];
for (const [name, mut] of VARIANTS) {
  const P = makeP(mut), w = C.waterOf(P);
  const surge = [];
  for (const dQ of [-1, 0, 1e-4, 1e-3, 4e-3]) for (const tc of [0.001, 0.005, 0.05, 0.5]) surge.push({ dQ, tc, out: T.surgeFor(P, dQ, w, tc) });
  const acc = [];
  for (const [dQ, pOp, allow] of [[1e-3, 5e4, 5e4], [4e-3, 2e5, 2e4], [2e-3, 0, 1e5], [0, 1e5, 1e5], [1e-3, 1e5, 0], [1e-3, -2e5, 5e4]]) {
    acc.push({ dQ, pOp, allow, L: T.accumulatorGasL(P, dQ, pOp, allow, w) });
  }
  scenarios.push({ name, model: P.plumb.model, supply: P.plumb.supply, manifold: mani(P), waterC: P.plumb.waterC,
    supplyWave: T.supplyWave(P, w), manifoldWave: T.manifoldWave(P, w), surge, acc });
}
const gas = [-1, 0, 0.05, 0.1, 0.11, 0.3, 1, 1.0000000001, 7.9, 23.99, 99, 100, 100.2, 137, 260].map(need => ({ need, L: S.stdGasL(need) }));
writeGolden('surge', 'digital-rain-curtain/src/hydro/transient.js + presets.js stdGasL', { scenarios, gas });
