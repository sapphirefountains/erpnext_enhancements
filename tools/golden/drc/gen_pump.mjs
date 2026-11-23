// Golden vectors for engine/hydraulics/pump.py: DRC src/hydro/flow.js interp, clean, pumpHeadM, pumpEff,
// npshrM, shaftPowerW, bepFlow, maxFlow, legacyPump, pumpCurve. Interpolation is matched at 1e-12;
// maxFlow, shaftPowerW (near shut-off) and pumpCurve end in a bisection and are matched at 1e-9.
// Every project is in DRC's 'system' model (pumpCurve reads the curve, not the rain-curtain quadratic).
// Usage (repo root): node tools/golden/drc/gen_pump.mjs [path-to-digital-rain-curtain]
import { patchCatalog, load, makeP, writeGolden } from './drc.mjs';

const C = await patchCatalog();
const F = await load('src/hydro/flow.js');

const P0 = makeP().plumb.pump;
const PUMPS = [
  ['drc default', {}],
  ['speed60', { speedPct: 60 }],
  ['speed0', { speedPct: 0 }],
  ['motor5', { motorEff: 5 }],
  ['no eff', { eff: [] }],
  ['no npshr', { npshr: [] }],
  ['no eff no curve (quad)', { eff: [], curve: [] }],
  ['one point (quad)', { curve: [[0, 9]] }],
  ['dirty', {
    curve: [[120, 4.2], [0, 9], [40, null], ['x', 3], [200], 'junk', [80, 6.6], [150, 1.8], [40, 8.2]],
    eff: [[80, 42], [0, 0], [150, 25], [80, 41], [40, 32], [120, 38]],
  }],
  ['end-suction 5 hp (made up, US-shaped)', {
    curve: [[0, 30.5], [200, 30], [400, 28.6], [600, 26], [800, 22], [1000, 16.5], [1100, 13]],
    eff: [[0, 0], [200, 38], [400, 60], [600, 71], [800, 74], [1000, 70], [1100, 64]],
    npshr: [[0, 1.2], [400, 1.8], [800, 3.0], [1100, 4.6]], motorEff: 88,
  }],
];
const QS = [-10, 0, 5, 20, 40, 80, 101.25, 120, 150, 200, 400, 1000].map(lpm => lpm / 60000);

const pumps = PUMPS.map(([name, mut]) => {
  const P = makeP(P => { P.plumb.model = 'system'; P.plumb.pump = { ...P0, ...mut }; if (name === 'one point (quad)') { P.hyd.pShutKPa = 120; P.hyd.qMaxLpm = 90; } });
  const w = C.waterOf(P), pump = P.plumb.pump, speed = (pump.speedPct ?? 100) / 100;
  return {
    name, pump, waterC: P.plumb.waterC, legacy: F.legacyPump(P, w),
    out: {
      clean: { curve: F.clean(pump.curve), eff: F.clean(pump.eff), npshr: F.clean(pump.npshr) },
      grid: QS.map(Q => ({
        Q, head: F.pumpHeadM(P, Q), head05: F.pumpHeadM(P, Q, 0.5), head0: F.pumpHeadM(P, Q, 0), eff: F.pumpEff(P, Q), eff05: F.pumpEff(P, Q, 0.5),
        npshr: F.npshrM(P, Q), npshr05: F.npshrM(P, Q, 0.5), shaft: F.shaftPowerW(P, Q, speed, w), shaft05: F.shaftPowerW(P, Q, 0.5, w),
      })),
      bep: [F.bepFlow(P), F.bepFlow(P, 0.5)],
      maxFlow: [F.maxFlow(P), F.maxFlow(P, 0.5)],
      pumpCurve: F.pumpCurve(P, speed, 9),
      pumpCurve05: F.pumpCurve(P, 0.5, 9),
      pumpCurveN1: F.pumpCurve(P, speed, 1),
    },
  };
});

// interp on its own (pts as given, before clean). Same cases as aquafect's gen_flow.mjs.
const INTERP = [
  [[], 10], [[[NaN, 1]], 10], [[[5, 42]], 0], [[[5, 42]], 10], [[[0, 10], [10, 20]], 15], [[[0, 10], [10, 20]], -5],
  [[[0, 10], [10, 20]], 5], [[[10, 20], [0, 10], [5, 30]], 7], [[[0, 1], [0, 2], [5, 3]], 0], [[[0, 1], [5, 2], [5, 3]], 5],
  [[[0, 1], [5, 2], [5, 3]], 7], [[[0, 1], [5, 2], [5, 3], [9, 4]], 5], [[[0, 1], [2, 3]], NaN], [[[0, 1], [2, 3]], Infinity],
  [[[0, 1], [Infinity, 2], [3, 4]], 2], [[[0, 1], [2, 3]], -Infinity], [[[0, 9], [40, 8.2], [80, 6.6], [120, 4.2], [150, 1.8]], 101.25],
  [[[0, 1], [true, 5], [4, 2]], 2], [[[0, 1], ['3', 5], [4, 2]], 2],
];
const interp = INTERP.map(([pts, x]) => ({ pts, x, y: F.interp(pts, x) }));

writeGolden('pump', 'digital-rain-curtain/src/hydro/flow.js', { pumps, interp });
