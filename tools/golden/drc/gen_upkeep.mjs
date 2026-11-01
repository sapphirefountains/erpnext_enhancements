// Golden vectors for engine/upkeep.py water_plan: DRC src/upkeep.js waterPlan (cycles of concentration with the
// level held, drain-and-refill days, the Legionella band, chlorine doses). Each scenario records the basin
// figures waterPlan reads (volumes, evaporation and spray loss) and whether DRC calls the site outdoor.
// Usage (repo root): node tools/golden/drc/gen_upkeep.mjs [path-to-digital-rain-curtain]
import { load, makeP, writeGolden } from './drc.mjs';

const U = await load('src/upkeep.js');
const PR = await load('src/hydro/presets.js');

const basins = [
  { basinL: 400, manifoldVolL: 3, supplyVolL: 5, suctionVolL: 1, evapLph: 0.4, evapSprayLph: 2 },
  { basinL: 1200, manifoldVolL: 0, supplyVolL: 20, suctionVolL: 4, evapLph: 3.5, evapSprayLph: 0.2 },
  { basinL: 900, manifoldVolL: 2, supplyVolL: 8, suctionVolL: 2, evapLph: 1.2, evapSprayLph: 0 },
  { basinL: 300, manifoldVolL: 1, supplyVolL: 2, suctionVolL: 1, evapLph: 0.05, evapSprayLph: 5 },
  { basinL: 0, manifoldVolL: 0, supplyVolL: 0, suctionVolL: 0, evapLph: 0, evapSprayLph: 0 },
];
const VARIANTS = [
  ['indoor chlorine', P => {}],
  ['hot outdoor bleach', P => { Object.assign(P.plumb, PR.CLIMATES.hot.plumb); P.upkeep = { treatment: 'bleach' }; }],
  ['bromine warm', P => { P.plumb.waterC = 30; P.upkeep = { treatment: 'bromine' }; }],
  ['custom strength and residual', P => { P.upkeep = { treatment: 'chlorine', pct: 10, ppm: 2.5, lossPpm: 1.5, maxCycles: 4 }; P.plumb.hoursPerDay = 12; }],
  ['no treatment, 24 h', P => { P.upkeep = { treatment: 'none' }; P.plumb.hoursPerDay = 24; }],
  ['cycles 1.1 clamps to 1.2', P => { P.upkeep = { maxCycles: 1.1 }; }],
];
const scenarios = [];
for (const [name, mut] of VARIANTS) {
  for (const basin of basins) {
    const P = makeP(mut);
    const r = U.waterPlan(P, { basin });
    const outdoor = ['mild', 'hot', 'cool'].includes(PR.matchPreset(P, PR.CLIMATES));
    const { treatment, ...out } = r;
    scenarios.push({ name, basin, upkeep: U.upkeepOf(P), hoursPerDay: P.plumb.hoursPerDay, waterC: P.plumb.waterC, outdoor, kind: treatment.kind, out });
  }
}
writeGolden('upkeep', 'digital-rain-curtain/src/upkeep.js waterPlan', { scenarios });
