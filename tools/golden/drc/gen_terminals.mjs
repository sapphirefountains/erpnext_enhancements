// Golden vectors for engine/hydraulics/terminals.py OrificeDemand: DRC src/hydro/manifold.js demandModel,
// the real one (manifoldSolve under it), on a few curtains and valve patterns. The Python side builds an
// OrificeDemand from the recorded conductance G and offset pg and must reproduce Q(p) and pFor(Q) at
// 1e-12. The open-discharge, fixed-flow and parallel demands have no JS reference; their tests are hand
// goldens.
// Usage (repo root): node tools/golden/drc/gen_terminals.mjs [path-to-digital-rain-curtain]
import { patchCatalog, load, makeP, writeGolden } from './drc.mjs';

const C = await patchCatalog();
const M = await load('src/hydro/manifold.js');

const VARIANTS = [
  ['default', () => {}],
  ['valves32 tilt', P => { P.hw.valves = 32; P.plumb.tiltMm = 10; P.plumb.sag = false; }],
  ['nozzle below, warm', P => { P.plumb.nozzleBelowM = 0.05; P.plumb.waterC = 40; }],
  ['sharp orifice', P => { P.plumb.nozzleCc = 0.62; P.hw.cv = 0.8; }],
];
const PS = [-2000, -1, 0, 1, 250, 554, 5e3, 2e4, 1e5];

const demands = [];
for (const [name, mut] of VARIANTS) {
  const P = makeP(mut), w = C.waterOf(P), N = P.hw.valves;
  const patterns = {
    all: new Uint8Array(N).fill(1),
    one: Uint8Array.from({ length: N }, (_, i) => (i === 0 ? 1 : 0)),
    alternate: Uint8Array.from({ length: N }, (_, i) => (i % 2 ? 1 : 0)),
    none: new Uint8Array(N),
  };
  for (const [pattern, open] of Object.entries(patterns)) {
    const d = M.demandModel(P, open, w);
    const qRef = d.Qref || 1e-4;
    const QS = [-1e-4, 0, 0.1 * qRef, qRef, 3 * qRef];
    demands.push({
      name: `${name} / ${pattern}`, anyOpen: d.anyOpen, nOpen: d.nOpen, pg: d.pg, G: d.G, pRef: d.pRef, Qref: d.Qref,
      q: PS.map(p => ({ p, Q: d.Q(p) })), pFor: QS.map(Q => ({ Q, p: d.pFor(Q) })),
    });
  }
}

writeGolden('terminals', 'digital-rain-curtain/src/hydro/manifold.js', { demands });
