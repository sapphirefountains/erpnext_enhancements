// Golden vectors for engine/hydraulics/manifold.py: DRC src/hydro/manifold.js manifoldSolve, manifoldRuns,
// demandModel, sizeSweep, manifoldVolumeL, and the common.js helpers the rain-curtain manifold is built from
// (branchK, designPressures, elevationProfile, manifoldBeam). Each scenario records the DRC inputs the Python
// side needs to rebuild the same manifold as a per-takeoff list (curtain_manifold) and the full solution.
// manifoldSolve returns v as a Float32Array: the Python suite compares it through float32.
// Usage (repo root): node tools/golden/drc/gen_manifold.mjs [path-to-digital-rain-curtain]
import { patchCatalog, load, makeP, writeGolden } from './drc.mjs';

const C = await patchCatalog();
const M = await load('src/hydro/manifold.js');

const catalogBore = (mat, nps) => C.sizesFor(mat).find(s => s.nps === nps).idMm;
const inputs = P => ({
  valves: P.hw.valves, pitchMm: P.hw.pitchMm, orificeMm: P.hw.orificeMm, cv: P.hw.cv, v0: P.hw.v0,
  nozzleCc: P.plumb.nozzleCc, valveKv: P.plumb.valveKv, tubeIdMm: P.plumb.tubeIdMm, tubeLenM: P.plumb.tubeLenM,
  tiltMm: P.plumb.tiltMm, sag: P.plumb.sag, hangerM: P.plumb.hangerM, nozzleBelowM: P.plumb.nozzleBelowM,
  regain: P.plumb.regain, feed: P.hyd.feed, waterC: P.plumb.waterC, manMat: P.plumb.manMat, manNps: P.plumb.manNps,
  pipeIdMm: P.hyd.pipeIdMm, manWallMm: P.plumb.manWallMm,
});
const pattern = (N, kind) => Uint8Array.from({ length: N }, (_, i) =>
  kind === 'all' ? 1 : kind === 'one' ? (i === 0 ? 1 : 0) : kind === 'alternate' ? (i % 2 ? 1 : 0) : kind === 'firsthalf' ? (i < N / 2 ? 1 : 0) : 0);

const VARIANTS = [
  ['default 128', P => {}, 'all', 1],
  ['default 128 x3 pressure', P => {}, 'all', 3],
  ['32 tilt no sag', P => { P.hw.valves = 32; P.plumb.tiltMm = 10; P.plumb.sag = false; }, 'all', 1],
  ['48 center feed', P => { P.hw.valves = 48; P.hyd.feed = 'center'; }, 'all', 1],
  ['48 both ends', P => { P.hw.valves = 48; P.hyd.feed = 'both'; }, 'all', 1],
  ['33 center odd', P => { P.hw.valves = 33; P.hyd.feed = 'center'; }, 'all', 1],
  ['32 no regain', P => { P.hw.valves = 32; P.plumb.regain = false; }, 'all', 1],
  ['32 nozzle below warm', P => { P.hw.valves = 32; P.plumb.nozzleBelowM = 0.05; P.plumb.waterC = 40; }, 'all', 1],
  ['48 small bore', P => { P.hw.valves = 48; P.hyd.pipeIdMm = 12; P.plumb.manWallMm = 2; }, 'all', 1],
  ['64 pvc40 1-1/2', P => { P.hw.valves = 64; P.plumb.manMat = 'pvc40'; P.plumb.manNps = '1-1/2'; P.hyd.pipeIdMm = catalogBore('pvc40', '1-1/2'); }, 'all', 1],
  ['32 steel 1', P => { P.hw.valves = 32; P.plumb.manMat = 'steel40'; P.plumb.manNps = '1'; P.hyd.pipeIdMm = catalogBore('steel40', '1'); P.hw.orificeMm = 3; }, 'all', 1],
  ['32 valve kv + tube', P => { P.hw.valves = 32; P.plumb.valveKv = 0.5; P.plumb.tubeIdMm = 4; P.plumb.tubeLenM = 0.3; }, 'all', 1],
  ['32 sharp orifice', P => { P.hw.valves = 32; P.plumb.nozzleCc = 0.62; P.hw.cv = 0.8; }, 'all', 1],
  ['32 alternate', P => { P.hw.valves = 32; }, 'alternate', 1],
  ['32 first half, center', P => { P.hw.valves = 32; P.hyd.feed = 'center'; }, 'firsthalf', 1],
  ['32 one open', P => { P.hw.valves = 32; }, 'one', 1],
  ['32 none open', P => { P.hw.valves = 32; }, 'none', 1],
  ['32 suction pressure, nozzles below', P => { P.hw.valves = 32; P.plumb.nozzleBelowM = 0.1; }, 'all', -1],
  ['24 fast jets big orifice', P => { P.hw.valves = 24; P.hw.orificeMm = 4; P.hw.v0 = 3; P.hyd.pipeIdMm = 20; }, 'all', 1],
];

const solves = [];
for (const [name, mut, kind, mult] of VARIANTS) {
  const P = makeP(mut), w = C.waterOf(P), N = P.hw.valves;
  const dp = C.designPressures(P, w);
  const pIn = mult > 0 ? mult * dp.pManifold : -2000;
  const open = pattern(N, kind);
  const sol = M.manifoldSolve(P, open, pIn, w);
  solves.push({
    name, P: inputs(P), pattern: kind, pIn, design: dp, branch: C.branchK(P, w), spec: C.manifoldSpec(P),
    dz: C.elevationProfile(P, w), beam: C.manifoldBeam(P, w), volumeL: M.manifoldVolumeL(P),
    out: { q: sol.q, p: sol.p, v: sol.v, Q: sol.Q, iters: sol.iters, converged: sol.converged },
  });
}

const demands = [];
for (const [name, mut, kind] of [['default 128', P => {}, 'all'], ['32 tilt', P => { P.hw.valves = 32; P.plumb.tiltMm = 15; }, 'alternate'],
  ['32 nozzle below', P => { P.hw.valves = 32; P.plumb.nozzleBelowM = 0.05; }, 'all']]) {
  const P = makeP(mut), w = C.waterOf(P), N = P.hw.valves, open = pattern(N, kind);
  const d = M.demandModel(P, open, w);
  demands.push({ name, P: inputs(P), pattern: kind, pg: d.pg, pRef: d.pRef, Qref: d.Qref, G: d.G, nOpen: d.nOpen,
    vAt: [0.5, 1, 2].map(s => ({ p: s * d.pRef, v: Array.from(d.vAt(s * d.pRef)) })) });
}

const sweeps = [];
for (const [name, mut, mat, target] of [['64 pvc40 2%', P => { P.hw.valves = 64; }, 'pvc40', 2], ['64 pvc80 5%', P => { P.hw.valves = 64; }, 'pvc80', 5],
  ['48 copperL 2% center', P => { P.hw.valves = 48; P.hyd.feed = 'center'; }, 'copperL', 2]]) {
  const P = makeP(mut), w = C.waterOf(P);
  sweeps.push({ name, P: inputs(P), mat, target, rows: M.sizeSweep(P, mat, target, w) });
}

const runs = [];
for (const n of [2, 3, 7, 8, 128]) for (const feed of ['end', 'center', 'both']) runs.push({ n, feed, runs: M.manifoldRuns(n, feed) });

writeGolden('manifold', 'digital-rain-curtain/src/hydro/manifold.js', { solves, demands, sweeps, runs });
