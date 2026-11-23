// Golden vectors for engine/autosize.py's DRC parts: src/hydro/presets.js pickPipe (DRC's own velocity and
// friction-gradient limits, which drc_pick_pipe reproduces before DOC-0049's limits are layered on) and
// autoBypassMm (15% of the best-efficiency flow at shut-off head, ceilTo 0.5 mm).
// Usage (repo root): node tools/golden/drc/gen_autosize.mjs [path-to-digital-rain-curtain]
import { patchCatalog, load, makeP, writeGolden } from './drc.mjs';

const C = await patchCatalog();
const S = await load('src/hydro/presets.js');

const picks = [];
for (const mat of ['pvc40', 'pvc80', 'copperL', 'steel40', 'pex', 'hose']) {
  for (const Q of [1e-5, 2e-4, 1e-3, 2.5e-3, 6e-3, 2e-2, 1]) {
    for (const opt of [{}, { vMax: 0.9, gradMax: 0.03 }, { vMax: 0.9, gradMax: 0.03, minIdMm: 40 }]) {
      for (const T of [20, 40]) {
        const s = S.pickPipe(mat, Q, opt, C.water(T));
        picks.push({ mat, Q, opt, T, nps: s?.nps ?? null, idMm: s?.idMm ?? null });
      }
    }
  }
}
const bypass = [];
for (const [name, mut] of [
  ['default prv', P => { P.plumb.control = 'prv'; }],
  ['vfd -> none', P => { P.plumb.control = 'vfd'; }],
  ['80% speed', P => { P.plumb.control = 'none'; P.plumb.pump.speedPct = 80; }],
  ['no eff curve', P => { P.plumb.control = 'none'; P.plumb.pump.eff = []; }],
  ['big pump', P => {
    P.plumb.control = 'none';
    P.plumb.pump.curve = [[0, 30], [200, 25], [400, 12], [480, 2]];
    P.plumb.pump.eff = [[0, 0], [200, 55], [320, 70], [480, 40]];
  }],
]) {
  const P = makeP(mut);
  bypass.push({ name, pump: P.plumb.pump, control: P.plumb.control, mm: S.autoBypassMm(P) });
}
writeGolden('autosize', 'digital-rain-curtain/src/hydro/presets.js pickPipe, autoBypassMm', { picks, bypass });
