// Golden vectors for engine/structure.py beam_reactions: DRC src/rigging.js beamReactions (three-moment
// equation, Thomas algorithm), over spans, loads and overhangs beyond the textbook cases its own tests check.
// Usage (repo root): node tools/golden/drc/gen_structure.mjs [path-to-digital-rain-curtain]
import { load, writeGolden } from './drc.mjs';

const R = await load('src/rigging.js');
const cases = [];
for (const n of [0, 1, 2, 3, 4, 5, 7, 12]) {
  for (const L of [0.5, 1, 2.44]) for (const w of [1, 37.5]) for (const a of [0, 0.12, 0.6]) cases.push({ n, L, w, a, R: R.beamReactions(n, L, w, a) });
}
cases.push({ n: 3.7, L: 1, w: 1, a: 0, R: R.beamReactions(3.7, 1, 1, 0) });
writeGolden('structure', 'digital-rain-curtain/src/rigging.js beamReactions', { cases });
