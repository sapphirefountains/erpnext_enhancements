// Golden vectors for engine/hydraulics/operating_point.py: DRC src/hydro/flow.js operatingPoint (system
// model only: the 'simple' quadratic-at-the-manifold model is a rain-curtain legacy and is not ported),
// systemCurve, bypassFlow, npshaM. Bisection results are matched at 1e-9, the rest at 1e-12.
//
// Scenarios follow aquafect's gen_flow.mjs list (minus the simple model). Each records its inputs in the
// shape the Python kernel takes (water temperature, pump, suction, supply, static head, control, set
// pressure, bypass, clog) so the Python test needs no curtain geometry. Demands are equivalent orifices
// { anyOpen, pg, G }, the interface operatingPoint takes. Every PVC bore is the DOC-0049 ID (drc.mjs).
//
// NPSHa here is DRC's sea-level figure (PATM = 101325 Pa, Antoine vapor pressure); the Python envelope
// evaluates it at the site elevation instead, which the kernel takes as an argument.
// Usage (repo root): node tools/golden/drc/gen_operating_point.mjs [path-to-digital-rain-curtain]
import { patchCatalog, load, makeP, writeGolden } from './drc.mjs';

const C = await patchCatalog();
const F = await load('src/hydro/flow.js');

const demandOf = ({ anyOpen, pg, G }) => ({
  anyOpen, pg,
  Q: p => (anyOpen && p + pg > 0 ? G * Math.sqrt(p + pg) : 0),
  pFor: Q => (Q <= 0 ? -pg : anyOpen ? (Q / G) ** 2 - pg : Infinity),
});
function lumped(name, P, open, w) {
  if (!open.length) return { name, anyOpen: false, pg: 0, G: 0 };
  const dz = C.elevationProfile(P, w);
  let sum = 0;
  for (const i of open) sum += dz[i];
  const pg = w.rho * C.G * ((P.plumb.nozzleBelowM || 0) + sum / open.length);
  return { name, anyOpen: true, pg, G: open.length / Math.sqrt(C.branchK(P, w).k) };
}
const GC24 = 24 / 60000 / Math.sqrt(554);

const sys = (control, mut) => P => { P.plumb.model = 'system'; P.plumb.control = control; if (mut) mut(P); };
const seg = C.newSegment;
const SCENARIOS = [
  ...['none', 'prv', 'vfd'].flatMap(c => [100, 60].map(s => [`${c}-speed${s}`, sys(c, P => { P.plumb.pump.speedPct = s; })])),
  ['none-clog50', sys('none', P => { P.plumb.clogPct = 50; })],
  ['none-clog90', sys('none', P => { P.plumb.clogPct = 90; })],
  ['prv-water5', sys('prv', P => { P.plumb.waterC = 5; })],
  ['prv-water40', sys('prv', P => { P.plumb.waterC = 40; })],
  ['prv-valves32', sys('prv', P => { P.hw.valves = 32; })],
  ['none-supply1', sys('none', P => {
    P.plumb.supply = [seg({ name: 'Run', nps: '1-1/2', idMm: 40.94, lenM: 12, fit: { elbow90: 4, ball: 1 } })];
  })],
  ['none-supply3half', sys('none', P => {
    P.plumb.supply = [
      seg({ name: 'Discharge', nps: '1-1/4', idMm: 35.08, lenM: 1.5, fit: { check: 1, ball: 1, elbow90: 1 } }),
      seg({ name: 'Header', nps: '1-1/4', idMm: 35.08, lenM: 3, fit: { teeBranch: 1 } }),
      seg({ name: 'Branch', nps: '1', idMm: 26.6, lenM: 2.2, share: 'half', kExtra: 1.5, fit: { teeBranch: 1, ball: 1, elbow90: 2 } }),
    ];
  })],
  ['none-lift1.5', sys('none', P => { P.plumb.suction.liftM = 1.5; })],
  ['none-flooded0.8', sys('none', P => { P.plumb.suction.liftM = -0.8; })],
  ...['none', 'prv', 'vfd'].map(c => [`${c}-bypass6`, sys(c, P => { P.plumb.bypassMm = 6; })]),
  ['prv-tiltMinus5-sag-below', sys('prv', P => { P.plumb.tiltMm = -5; P.plumb.nozzleBelowM = 0.05; })],
  ['none-cannotLift', sys('none', P => { P.hw.dropM = 20; })],
  ['prv-cannotLift-bypass', sys('prv', P => { P.hw.dropM = 20; P.plumb.bypassMm = 6; })],
  ['none-runout', sys('none', P => { P.hw.dropM = 0.3; })],
  ['none-quadPump', sys('none', P => { P.plumb.pump.curve = []; })],
  ['prv-quadPumpExplicit', sys('prv', P => { P.plumb.pump.curve = [[0, 9]]; P.hyd.pShutKPa = 120; P.hyd.qMaxLpm = 90; })],
  ['none-noEff', sys('none', P => { P.plumb.pump.eff = []; })],
  ['none-noNpshr', sys('none', P => { P.plumb.pump.npshr = []; })],
  ['prv-inactive', sys('prv', P => { P.plumb.setKPa = 200; })],
  ['vfd-inactive', sys('vfd', P => { P.plumb.setKPa = 200; })],
  ['vfd-lowSet', sys('vfd', P => { P.plumb.setKPa = 0.05; })],
  ['vfd-floor', sys('vfd', P => { P.plumb.setKPa = 0.05; P.hw.dropM = 0.1; })], // still over the set pressure at 15 % speed
  ['vfd-bypass-lift', sys('vfd', P => { P.plumb.bypassMm = 4; P.plumb.suction.liftM = 0.8; P.plumb.clogPct = 30; })],
  ['none-speed0', sys('none', P => { P.plumb.pump.speedPct = 0; })],
  ['none-motor5', sys('none', P => { P.plumb.pump.motorEff = 5; })],
];

const PDS = [-100, 0, 1e3, 1e4, 5e4, 1e5];
const OP_KEYS = ['Q', 'Qpump', 'qBypass', 'pIn', 'pDischarge', 'staticPa', 'lossSupplyPa', 'prvDropPa', 'headM', 'speed', 'control', 'setPa',
  'eff', 'powerHydW', 'powerShaftW', 'powerElecW', 'npsha', 'npshr', 'npshMargin', 'deadHead', 'cannotLift'];
const pick = op => Object.fromEntries(OP_KEYS.map(k => [k, op[k]]));

const scenarios = SCENARIOS.map(([name, mut]) => {
  const P = makeP(mut), w = C.waterOf(P), N = P.hw.valves;
  const demands = [
    lumped('all', P, [...Array(N).keys()], w),
    lumped('one', P, [0], w),
    lumped('none', P, [], w),
    { name: 'stub', anyOpen: true, pg: 0, G: GC24 },
  ];
  if (name === 'none-runout') demands.push({ name: 'runout', anyOpen: true, pg: 0, G: 1e-3 });
  const all = demandOf(demands[0]), none = demandOf(demands[2]);
  const setPa = P.plumb.setKPa > 0 ? P.plumb.setKPa * 1000 : C.designPressures(P, w).pManifold;
  return {
    name,
    in: {
      waterC: P.plumb.waterC, pump: P.plumb.pump, legacy: F.legacyPump(P, w), suction: P.plumb.suction, supply: P.plumb.supply,
      staticM: C.staticHead(P), control: P.plumb.control, setPa, bypassMm: P.plumb.bypassMm, clogPct: P.plumb.clogPct,
    },
    demands,
    out: {
      bypass: PDS.map(pd => ({ pd, q: F.bypassFlow(P, pd, w) })),
      npsha: [0, 40, 120, 300].map(lpm => ({ Q: lpm / 60000, h: F.npshaM(P, lpm / 60000, w) })),
      ops: demands.map(d => pick(F.operatingPoint(P, demandOf(d), { w }))),
      opSpeed80: pick(F.operatingPoint(P, all, { w, speed: 0.8 })),
      systemCurve: F.systemCurve(P, all, 8),
      systemCurveQ30: F.systemCurve(P, all, 8, 30 / 60000),
      systemCurveNone: F.systemCurve(P, none, 8),
    },
  };
});

writeGolden('operating_point', 'digital-rain-curtain/src/hydro/flow.js', { scenarios });
