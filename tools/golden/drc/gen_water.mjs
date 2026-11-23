// Golden vectors for engine/hydraulics/water.py: DRC src/hydro/common.js water, frictionFactor, areaOf,
// waveSpeed. Closed-form math, matched at 1e-12.
// Usage (repo root): node tools/golden/drc/gen_water.mjs [path-to-digital-rain-curtain]
import { patchCatalog, writeGolden } from './drc.mjs';

const C = await patchCatalog();

const temps = [-5, 0, 4, 10, 15, 20, 21.1, 25, 30, 40, 50, 60, 80, 100, 120, NaN];
const water = temps.map(T => ({ T, out: C.water(T) }));

const res = [-1, 0, 1e-7, 1, 100, 1000, 2000, 2300, 3000, 4000, 1e4, 1e5, 1e6, 1e7, NaN];
const rrs = [0, 1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 0.05];
const friction = [];
for (const Re of res) for (const rr of rrs) friction.push({ Re, rr, f: C.frictionFactor(Re, rr) });

const areas = [0, 0.001, 0.0266446, 0.0525018, 0.3032252].map(d => ({ d, a: C.areaOf(d) }));

const waves = [];
for (const T of [10, 20, 40])
  for (const [idM, wallM, eGPa] of [[0.0409, 0.00368, 2.9], [0.0266446, 0.0033782, 2.9], [0.0254, 0.00127, 117], [0.0254, 0, 0.05], [0.1022604, 0.0060198, 200]])
    waves.push({ T, idM, wallM, eGPa, a: C.waveSpeed(C.water(T), idM, wallM, eGPa) });

writeGolden('water', 'digital-rain-curtain/src/hydro/common.js', { G: C.G, PATM: C.PATM, water, friction, areas, waves });
