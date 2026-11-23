// Shared by the DRC golden generators. Loads the Digital Rain Curtain's reference JS read-only and
// writes vectors the bench-free pytest suites (erpnext_enhancements/tests/test_hydraulics_*.py) read.
// Copied from aquafect/tools/golden/drc.mjs, plus: the DRC commit SHA in every file, compact JSON (this
// repo is public and each file must stay under 200 KB), and the DOC-0049 pipe table patched into DRC's
// in-memory catalog (see DOC0049 below).
//
// The reference checkout is the first command-line argument, else $DRC_PATH, else
// ../digital-rain-curtain beside this repository. Nothing in the checkout is written.
import fs from 'fs';
import path from 'path';
import { execSync } from 'child_process';
import { fileURLToPath, pathToFileURL } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '../../..');
export const drc = path.resolve(process.argv[2] || process.env.DRC_PATH || path.join(repo, '../digital-rain-curtain'));

// Import a module of the reference implementation, e.g. load('src/hydro/flow.js')
export const load = rel => import(pathToFileURL(path.join(drc, rel)).href);

function drcSha() {
  try {
    const sha = execSync('git rev-parse HEAD', { cwd: drc }).toString().trim();
    const dirty = execSync('git status --porcelain -- src', { cwd: drc }).toString().trim();
    return dirty ? `${sha}-dirty` : sha;
  } catch {
    return 'unknown';
  }
}
export const DRC_SHA = drcSha();

const core = await load('src/core.js');
export const defaultProject = core.defaultProject;

// Same as DRC tests/hydro/fixture.mjs: the default project with the pressure model on
export function makeP(mut) {
  const P = defaultProject();
  P.hyd.on = true;
  if (mut) mut(P);
  return P;
}

// ---------------------------------------------------------------------------
// DOC-0049 governs pipe dimensions (Nikolas, 2026-11-01, audit decision D2). DRC derives its bores as
// NPS OD minus two walls from its own metric table (Sch 40 2" = 52.48 mm); the Python port uses the
// DOC-0049 "1, 2, 3 - Pipe Specs" OD / wall / ID (Sch 40 2" = 2.067" = 52.5018 mm). So before any
// generator runs, DRC's pvc40 / pvc80 / steel40 size lists are replaced IN MEMORY with the DOC-0049
// rows, and copperK (DOC-0049 Type K) is added. Every golden that touches one of those materials is
// therefore regenerated with the DOC-0049 ID, not DRC's. Steel Sch 40 takes the Sch 40 rows because
// ASME B36.10 Sch 40 and ASTM D1785 Sch 40 share OD and wall. Copper type L, PEX and hose keep DRC's
// tables (DOC-0049 has no row for them). The Python tests check this table against
// engine/data/pipe_specs.py PIPE_PRESSURE_SPECS so the two cannot drift.
// [nominal, od_in, wall_in, id_in]
// ---------------------------------------------------------------------------
export const DOC0049 = {
  'SCH40 PVC': [
    ['3/4', 1.05, 0.113, 0.824], ['1', 1.315, 0.133, 1.049], ['1-1/4', 1.66, 0.14, 1.38], ['1-1/2', 1.9, 0.145, 1.61],
    ['2', 2.375, 0.154, 2.067], ['2-1/2', 2.875, 0.203, 2.469], ['3', 3.5, 0.216, 3.068], ['4', 4.5, 0.237, 4.026],
    ['6', 6.625, 0.28, 6.065], ['8', 8.625, 0.322, 7.981], ['10', 10.75, 0.365, 10.02], ['12', 12.75, 0.406, 11.938],
  ],
  'SCH80 PVC': [
    ['3/4', 1.05, 0.154, 0.742], ['1', 1.315, 0.179, 0.957], ['1-1/4', 1.66, 0.191, 1.278], ['1-1/2', 1.9, 0.2, 1.5],
    ['2', 2.375, 0.218, 1.939], ['2-1/2', 2.875, 0.276, 2.323], ['3', 3.5, 0.3, 2.9], ['4', 4.5, 0.337, 3.826],
    ['6', 6.625, 0.432, 5.761], ['8', 8.625, 0.5, 7.625], ['10', 10.75, 0.593, 9.564], ['12', 12.75, 0.687, 11.376],
  ],
  'Type K Copper': [
    ['1/2', 0.625, 0.049, 0.527], ['3/4', 0.875, 0.065, 0.745], ['1', 1.125, 0.065, 0.995], ['1-1/4', 1.375, 0.065, 1.245],
    ['1-1/2', 1.625, 0.072, 1.481], ['2', 2.125, 0.083, 1.959], ['2-1/2', 2.625, 0.095, 2.435], ['3', 3.125, 0.109, 2.907],
    ['4', 4.125, 0.134, 3.857], ['6', 6.125, 0.192, 5.741],
  ],
};
// DRC wants [nominal, od_mm, wall_mm]; its pipeSpec then takes id = od - 2 * wall, which equals the
// DOC-0049 ID times 25.4 to within an ulp or two (checked here).
const mmRows = rows => rows.map(([n, od, wall, id]) => {
  const odMm = od * 25.4, wallMm = wall * 25.4;
  if (Math.abs(odMm - 2 * wallMm - id * 25.4) > 1e-9) throw new Error(`DOC-0049 row ${n}: od - 2 wall != id`);
  return [n, odMm, wallMm];
});
export async function patchCatalog() {
  const C = await load('src/hydro/common.js');
  C.MATERIALS.pvc40.sizes = mmRows(DOC0049['SCH40 PVC']);
  C.MATERIALS.pvc80.sizes = mmRows(DOC0049['SCH80 PVC']);
  C.MATERIALS.steel40.sizes = mmRows(DOC0049['SCH40 PVC']);
  C.MATERIALS.copperK = { label: 'Copper type K', epsMm: 0.0015, eGPa: 117, density: 8940, sizes: mmRows(DOC0049['Type K Copper']) };
  return C;
}

// JSON with NaN / ±Infinity as strings ("NaN", "Infinity", "-Infinity") and typed arrays as arrays.
// Compact (no indent): the size cap is per file.
const replacer = (k, v) => (ArrayBuffer.isView(v) ? Array.from(v) : typeof v === 'number' && !Number.isFinite(v) ? String(v) : v);
export function writeGolden(name, source, body) {
  const dest = path.join(repo, 'erpnext_enhancements/tests/golden/drc', `${name}.json`);
  const text = JSON.stringify({ source, drc_sha: DRC_SHA, node: process.version, doc0049: DOC0049, ...body }, replacer);
  if (text.length > 200 * 1024) throw new Error(`${name}.json is ${text.length} bytes; the cap is 200 KB, trim the scenarios`);
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, text + '\n');
  console.log(`wrote ${path.relative(repo, dest)} (${(text.length / 1024).toFixed(1)} KB)`);
}
