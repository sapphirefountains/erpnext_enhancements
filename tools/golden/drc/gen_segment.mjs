// Golden vectors for engine/hydraulics/segment.py: DRC src/hydro/flow.js segmentLoss, supplyLoss,
// suctionLoss. Closed-form math (one Churchill evaluation per segment), matched at 1e-12. Every PVC and
// steel bore here is the DOC-0049 ID (drc.mjs patches DRC's catalog), so these differ from DRC's own
// tests by the 0.0–0.3 % its OD-minus-wall bores differ by.
// Usage (repo root): node tools/golden/drc/gen_segment.mjs [path-to-digital-rain-curtain]
import { patchCatalog, load, makeP, writeGolden } from './drc.mjs';

const C = await patchCatalog();
const F = await load('src/hydro/flow.js');
const seg = C.newSegment;

const SEGMENTS = [
  seg({ name: 'default' }),
  seg({ name: 'pvc40 1-1/2 elbows', nps: '1-1/2', idMm: 40.94, lenM: 12, fit: { elbow90: 4, ball: 1 } }),
  seg({ name: 'pvc40 1 half', nps: '1', lenM: 2.2, share: 'half', kExtra: 1.5, fit: { teeBranch: 1, ball: 1, elbow90: 2 } }),
  seg({ name: 'pvc80 2 strainer', mat: 'pvc80', nps: '2', lenM: 30, fit: { strainer: 1, filter: 1, check: 1 } }),
  seg({ name: 'steel 4 foot valve', mat: 'steel40', nps: '4', lenM: 6, fit: { footValve: 1, elbow90lr: 2, gate: 1 } }),
  seg({ name: 'copperK 3/4', mat: 'copperK', nps: '3/4', lenM: 9, fit: { elbow90: 6, teeRun: 2 } }),
  seg({ name: 'copperL 1', mat: 'copperL', nps: '1', lenM: 5, fit: { elbow45: 2 } }),
  seg({ name: 'pex 1/2', mat: 'pex', nps: '1/2', lenM: 15, fit: { barb: 4, quick: 1 } }),
  seg({ name: 'hose 3/4', mat: 'hose', nps: '3/4', lenM: 7.5, fit: { barb: 2 } }),
  seg({ name: 'custom 18 mm', mat: 'custom', nps: null, idMm: 18, wallMm: 2, lenM: 3, fit: { globe: 1 } }),
  seg({ name: 'pvc40 1/2 not in DOC-0049', nps: '1/2', idMm: 15.8, wallMm: 2.77, lenM: 4, fit: { elbow90: 2 } }),
  seg({ name: 'zero length', nps: '2', lenM: 0, fit: { entrance: 1 } }),
  seg({ name: 'NaN length', nps: '2', lenM: NaN, kExtra: 0.4, fit: {} }),
];
const SUCTIONS = [
  { name: 'drc default', ...makeP().plumb.suction },
  { name: 'flooded 2', mat: 'pvc40', nps: '2', idMm: 52.5, lenM: 3, liftM: -0.8, kExtra: 0.2, share: 'half', fit: { entrance: 1, elbow90: 2, ball: 1 } },
  { name: 'lift 3 foot valve', mat: 'pvc40', nps: '3', idMm: 77.9, lenM: 4.5, liftM: 1.5, kExtra: 0, fit: { footValve: 1, elbow90: 1 } },
];

const QS = [-60, 0, 1, 5, 20, 40, 80, 150, 400, 1500].map(lpm => lpm / 60000);
const WATER = [5, 20, 40];
const CLOG = [0, 0.5, 0.9];

const segments = SEGMENTS.map(s => ({
  seg: s,
  loss: WATER.map(T => { const w = C.water(T); return CLOG.map(clog => QS.map(Q => F.segmentLoss(s, Q, w, clog))); }),
}));
const supplies = [
  { name: 'drc default', supply: makeP().plumb.supply },
  { name: 'three with a half branch', supply: [SEGMENTS[1], SEGMENTS[2], SEGMENTS[3]] },
  { name: 'empty', supply: [] },
].map(({ name, supply }) => ({
  name, supply,
  loss: WATER.map(T => CLOG.map(clog => {
    const P = makeP(P => { P.plumb.supply = supply; P.plumb.waterC = T; P.plumb.clogPct = clog * 100; });
    return QS.map(Q => F.supplyLoss(P, Q, C.waterOf(P)));
  })),
}));
const suctions = SUCTIONS.map(({ name, ...suction }) => ({
  name, suction,
  loss: WATER.map(T => CLOG.map(clog => {
    const P = makeP(P => { P.plumb.suction = suction; P.plumb.waterC = T; P.plumb.clogPct = clog * 100; });
    return QS.map(Q => F.suctionLoss(P, Q, C.waterOf(P)));
  })),
}));

writeGolden('segment', 'digital-rain-curtain/src/hydro/flow.js', { QS, WATER, CLOG, segments, supplies, suctions });
