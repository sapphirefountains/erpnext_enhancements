// Golden vectors for engine/hydraulics/catalog.py: DRC src/hydro/common.js MATERIALS, sizesFor, pipeSpec,
// with the DOC-0049 rows patched in (drc.mjs). Matched at 1e-12.
// Usage (repo root): node tools/golden/drc/gen_catalog.mjs [path-to-digital-rain-curtain]
import { patchCatalog, writeGolden } from './drc.mjs';

const C = await patchCatalog();

const materials = Object.fromEntries(Object.entries(C.MATERIALS).map(([k, m]) => [k, { label: m.label, epsMm: m.epsMm, eGPa: m.eGPa, density: m.density }]));
const sizes = Object.fromEntries(Object.keys(C.MATERIALS).map(k => [k, C.sizesFor(k)]));

const pipes = [];
for (const mat of Object.keys(C.MATERIALS)) {
  for (const s of C.sizesFor(mat)) pipes.push({ mat, nps: s.nps, idFb: 25, wallFb: 3, out: C.pipeSpec(mat, s.nps) });
  pipes.push({ mat, nps: 'nope', idFb: 38, wallFb: 3.5, out: C.pipeSpec(mat, 'nope', 38, 3.5) });
  pipes.push({ mat, nps: null, idFb: -2, wallFb: 0, out: C.pipeSpec(mat, null, -2, 0) });
}
pipes.push({ mat: 'unknown', nps: '1', idFb: 0, wallFb: 0, out: C.pipeSpec('unknown', '1', 0, 0) });

writeGolden('catalog', 'digital-rain-curtain/src/hydro/common.js', { materials, sizes, pipes });
