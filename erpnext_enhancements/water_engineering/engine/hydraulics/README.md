# `engine/hydraulics/`: the governing pump-and-pipe solver

Phase A of the Fountain Designer rebuild (plan: `docs/fountain-designer/PLAN.md`, PR #1389; audit
§3.2 modules 0–7). A stdlib-only Python port of the Digital Rain Curtain's (DRC) `src/hydro`
JavaScript, held to it by golden vectors. Nothing here imports frappe, and nothing is wired into
`run_spine` or the DocTypes yet: phase E does that.

## Why this exists

The workbook engine (`pipe.py`, `tdh.py`, `pump.py`) reproduces DOC-0049 to the cell, but it has no
operating point. `select_pump` only checks `head(design flow) >= TDH`, so it cannot say what flow a
pump actually delivers, what it draws, its NPSH margin, or whether an oversized pump runs past the
VGB cover rating. This package computes all of that.

**Darcy governs** (Nikolas, 2026-11-01). The governing loss is Darcy-Weisbach with the Churchill
friction factor (water properties from temperature) and Darby 3-K fittings. The DOC-0049
Hazen-Williams figure stays in `tdh.py`, and `segment.segment_loss` shows it beside the Darcy figure
as the workbook cross-check. A difference over 10% marks that card `Diverges`, which `issues.py`
(phase E) turns into a `DesignIssue`.

## Two layers

| Layer | Names | Units | Returns |
|---|---|---|---|
| kernels (the port) | `*_si` | SI: m, s, Pa, m³/s, kg/m³; curve flows in L/min as DRC | floats, small dataclasses |
| envelopes | no suffix | US: gpm, ft, psi, in, °F, hp | `CalcResult` with formula, steps, citations, warnings |

`interp`, `clean`, `resolve_material`, `resolve_fitting`, `build_demand` and the dataclasses
(`Water`, `PipeSpec`, `Segment`, `PumpCurve`, `System`, `OpPoint`) are helpers, not calculations.
Unit constants are exact and live in `units.py`: 0.3048 m/ft, 3.785411784e-3 m³/gal,
6894.757 Pa/psi, g = 9.80665. `_js.py` holds the JavaScript number semantics the port must
reproduce (`jmax`/`jmin` that propagate NaN, `or_` for `x || d`, `to_fixed` with ties away from
zero, `jpow` that overflows to Infinity).

## Module map

| Module | Ports (DRC) | Golden file (`tests/golden/drc/`) | Tolerance | Suite |
|---|---|---|---|---|
| `water.py` | `common.js` `water`, `frictionFactor`, `areaOf`, `waveSpeed` | `water.json` | 1e-12 | `test_hydraulics_water.py` |
| `catalog.py` | `common.js` `MATERIALS`, `sizesFor`, `pipeSpec` | `catalog.json` | 1e-12 | `test_hydraulics_catalog.py` |
| `fittings.py` | `common.js` `FITTINGS`, `fittingK`, `fitSumK` | `fittings.json` | 1e-12 | `test_hydraulics_fittings.py` |
| `segment.py` | `flow.js` `segmentLoss`, `supplyLoss`, `suctionLoss` | `segment.json` | 1e-12 | `test_hydraulics_segment.py` |
| `pump.py` | `flow.js` `clean`, `interp`, `pumpHeadM`, `pumpEff`, `npshrM`, `bepFlow`, `maxFlow`, `shaftPowerW`, `pumpCurve` | `pump.json` | 1e-12, bisections 1e-9 | `test_hydraulics_pump.py` |
| `terminals.py` | `manifold.js` `demandModel` (the `{Q, pFor, anyOpen, pg}` contract) | `terminals.json` (orifice) + hand goldens | 1e-12 | `test_hydraulics_terminals.py` |
| `operating_point.py` | `flow.js` `operatingPoint` (system model), `systemCurve`, `bypassFlow`, `npshaM` | `operating_point.json` + hand goldens | 1e-9 | `test_hydraulics_operating_point.py` |

All seven suites are bench-free pytest and run in one `ci.yml` step. Run them with
`python -m pytest erpnext_enhancements/tests/test_hydraulics_*.py -q`.

## Where the port departs from DRC, on purpose

- **Pipe bores come from DOC-0049.** DRC builds each bore as NPS OD minus two walls from its own
  metric table (Sch 40 2" = 52.48 mm). This package takes OD, wall and ID from DOC-0049 "1, 2, 3 -
  Pipe Specs" (`data/pipe_specs.py`; Sch 40 2" = 2.067" = 52.5018 mm). That covers Sch 40 PVC,
  Sch 80 PVC, Type K copper, and galvanized steel Sch 40 (same OD and wall as Sch 40). The
  generators patch the same rows into DRC's in-memory catalog before running (`drc.mjs`), so **every
  golden that touches those materials was regenerated with the DOC-0049 ID**. That is all of
  `segment.json` and `operating_point.json`, and the DOC-0049 rows of `catalog.json`.
  Copper type L, PEX and hose keep DRC's tables. PVC 1/4"–1/2" is not in DOC-0049 and is offered
  only as a custom bore.
- **DOC-0049 velocity limits and PSI ratings stay the authority.** `PipeSpec` carries them where
  DOC-0049 has them and leaves `None` where it does not: steel ratings, PEX and hose. Type L copper
  takes copper's velocity limits.
- **Fittings** use snake_case keys (`elbow_90`). DRC's keys and every workbook `FITTING_K` name
  resolve to them (`FITTING_K_TO_3K`). DOC-0049 fittings with no 3-K form keep their fixed K:
  mitered elbows, entrance shapes, exit, reducers and increasers.
- **Equipment curves** (`data/fittings.py` `COMPONENT_CURVES`) sit on a segment as `components`.
  They are evaluated at the branch flow. DRC's clog factor, `1/(1-clog)²`, applies to filters.
- **`branches`** generalizes DRC's `share: 'half'` to N identical parallel runs.
- **The pump's quadratic fallback** is `PumpCurve.quad = (shutoff_head_m, q_max_m3s)` rather than
  DRC's curtain-derived `legacyPump`. The arithmetic is the same, and it suits a generic-estimate
  pump that has only a shut-off head and a maximum flow on file.
- **The `simple` model is not ported.** That is DRC's quadratic pump acting directly at the
  manifold, a legacy rain-curtain mode.
- **NPSHa uses the site elevation.** DRC fixes `PATM = 101325` at sea level, which overstates NPSHa
  by about 1.5 m in Salt Lake City (audit E5). The kernel takes `atm_pa` and `pv_pa`, which default
  to DRC's values for the goldens. The envelope passes `safety._atm_head_ft(elevation)` and
  `safety._vapor_pressure_psia(temp)`.
- **New terminals** have no JS reference and carry hand goldens: open discharge, fixed flow and
  parallel. An open discharge has an infinite conductance, so the operating point bisects on flow
  at the pinned overflow pressure instead of on pressure. One pump with N parallel orifices equals
  one lumped orifice to 1e-12. Phase B's network solver must reproduce that degenerate case.

## Regenerating the goldens

The JSON is committed, so CI needs no Node. To regenerate, check out DRC beside this repository and
run from the repo root:

```bash
for m in water catalog fittings segment pump terminals operating_point; do node tools/golden/drc/gen_$m.mjs ../digital-rain-curtain; done
```

Each file records `drc_sha` (the DRC commit, `-dirty` if `src/` had local changes) and `node` (V8's
`Math.pow` moved some values by 1 ulp between Node versions, which is why the tolerance is 1e-12 and
not equality). Each file must stay under 200 KB, because this repository is public. The current files
are from DRC `737fa80f` on Node v24.22.1.

## What phase B builds on

- `segment_loss_si` / `segment_parts_si` for every pipe; `Segment.from_us` reads the design-model
  segment row.
- `pump_head_si` and friends with an explicit `speed`. `PumpCurve.from_us` reads Pump Curve Point
  rows (`flow_gpm`, `head_ft`, `efficiency_pct`, `npshr_ft`).
- The `Demand` contract (`q`, `p_for`, `any_open`, `pg`, `p_fixed`). A manifold terminal is one
  more implementation, and `build_demand` raises `NotImplementedError` for `kind: manifold` until
  then.
- `operating_point_si(System, demand)`. The network solver's single-pump, single-node case must
  match it to 1e-9.
- `water_si(T).K`, `wave_speed_si` and `PipeSpec.e_gpa` / `wall_mm` for surge.
- `drc_golden.py` (`close`, `close_f32`, `seg_from_drc`, `pump_from_drc`) and `tools/golden/drc/drc.mjs`
  for the manifold goldens.
