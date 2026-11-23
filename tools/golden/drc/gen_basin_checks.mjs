// Golden vectors for engine/hydraulics/basin_checks.py: DRC src/hydro/checks.js basinChecks, the non-curtain
// parts (pipe and in-flight volumes, drain-back, level rise, HI 9.8 submergence, strainer mesh, turnover,
// Carrier evaporation and spray loss). The splash and drift catch width is not ported. Each scenario records
// the plan area DRC derives from the curtain ((W + 0.5) x basinDM) so the Python side can pass it in.
// Usage (repo root): node tools/golden/drc/gen_basin_checks.mjs [path-to-digital-rain-curtain]
import { patchCatalog, load, makeP, writeGolden } from './drc.mjs';

const C = await patchCatalog();
const K = await load('src/hydro/checks.js');
const M = await load('src/hydro/manifold.js');
const seg = o => ({ name: '', mat: 'pvc40', nps: '1', idMm: 26.6, lenM: 1, share: 'all', kExtra: 0, fit: {}, ...o });

const VARIANTS = [
  ['default', P => {}, { Qall: 4e-4, fallT: 0.86 }],
  ['no check valve, windy, hot', P => {
    P.plumb.supply = [seg({ nps: '1-1/2', lenM: 6, fit: { ball: 1, elbow90: 2 } })];
    P.env.windMs = 4; P.plumb.airC = 35; P.plumb.rhPct = 25; P.plumb.waterC = 28;
  }, { Qall: 1.2e-3, fallT: 1.1 }],
  ['foot valve on suction', P => { P.plumb.supply = [seg({ lenM: 3 })]; P.plumb.suction = { ...P.plumb.suction, fit: { footValve: 1, elbow90: 1 } }; }, { Qall: 6e-4, fallT: 0.5 }],
  ['lift check, half branches', P => { P.plumb.supply = [seg({ lenM: 3, share: 'half', fit: { liftCheck: 1 } })]; }, { Qall: 6e-4, fallT: 0.5 }],
  ['deep basin big flow', P => {
    P.plumb.basinDM = 1.6; P.plumb.basinWaterM = 0.4; P.plumb.basinFreeM = 0.02; P.plumb.supply = [seg({ nps: '2', lenM: 12 })];
  }, { Qall: 4e-3, fallT: 1.4 }],
  ['shallow, cold, damp', P => { P.plumb.basinWaterM = 0.06; P.plumb.airC = 10; P.plumb.rhPct = 80; P.plumb.waterC = 10; P.hw.orificeMm = 1.2; }, { Qall: 3e-4, fallT: 0.7 }],
  ['no flow', P => {}, { Qall: 0, fallT: 0 }],
];
const scenarios = [];
for (const [name, mut, ctx] of VARIANTS) {
  const P = makeP(mut), w = C.waterOf(P);
  const out = K.basinChecks(P, { ...ctx, vImp: 0, drift: 0 }, w);
  scenarios.push({
    name, ctx, supply: P.plumb.supply, suction: P.plumb.suction, planArea: out.Lb * out.Db, basinWaterM: P.plumb.basinWaterM,
    basinFreeM: P.plumb.basinFreeM, waterC: P.plumb.waterC, airC: P.plumb.airC, rhPct: P.plumb.rhPct, windMs: P.env.windMs,
    hoursPerDay: P.plumb.hoursPerDay, orificeMm: P.hw.orificeMm, manifoldVolL: M.manifoldVolumeL(P), out,
  });
}
writeGolden('basin_checks', 'digital-rain-curtain/src/hydro/checks.js basinChecks', { scenarios });
