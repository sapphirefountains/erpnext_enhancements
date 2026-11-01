# `engine/hydraulics/`: the governing pump-and-pipe solver

Phases A and B of the Fountain Designer rebuild (plan: `docs/fountain-designer/PLAN.md`, PR #1389;
audit §3.2 modules 0–10, with autosize, BOM, upkeep and supports one level up in `engine/`). A
stdlib-only Python port of the Digital Rain Curtain's (DRC) `src/hydro`
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
| `manifold.py` | `manifold.js` `manifoldSolve`, `manifoldRuns`, `demandModel`, `sizeSweep`, `manifoldVolumeL` (+ `common.js` `branchK`, `designPressures`, `elevationProfile`, `manifoldBeam`) | `manifold.json` + hand goldens | 1e-12, velocities through float32 | `test_hydraulics_manifold.py` |
| `network.py` | none (new: multi-pump headers, ring mains) | held to `operating_point` at 1e-9 + hand goldens | 1e-9 | `test_hydraulics_network.py` |
| `surge.py` | `transient.js` `supplyWave`, `manifoldWave`, `surgeFor`, `accumulatorGasL`; `presets.js` `stdGasL` | `surge.json` | 1e-12 | `test_hydraulics_surge.py` |
| `basin_checks.py` | `checks.js` `basinChecks` without the curtain geometry | `basin_checks.json` + hand goldens | 1e-12 | `test_hydraulics_basin_checks.py` |

The phase A suites run in one `ci.yml` step and phase B's in another (with the engine-level
`test_autosize.py`, `test_bom.py` and `test_structure_upkeep.py`, whose goldens `autosize.json`,
`upkeep.json` and `structure.json` sit in the same folder). All are bench-free pytest:
`python -m pytest erpnext_enhancements/tests/test_hydraulics_*.py -q`.

## Where the port departs from DRC, on purpose

- **Pipe bores come from DOC-0049.** DRC builds each bore as NPS OD minus two walls from its own
  metric table (Sch 40 2" = 52.48 mm). This package takes OD, wall and ID from DOC-0049 "1, 2, 3 -
  Pipe Specs" (`data/pipe_specs.py`; Sch 40 2" = 2.067" = 52.5018 mm). That covers Sch 40 PVC,
  Sch 80 PVC, Type K copper, and galvanized steel Sch 40 (same OD and wall as Sch 40). The
  generators patch the same rows into DRC's in-memory catalog before running (`drc.mjs`), so **every
  golden that touches those materials was regenerated with the DOC-0049 ID**. That is all of
  `segment.json` and `operating_point.json`, and the DOC-0049 rows of `catalog.json`.
  Copper type L, PEX and hose keep DRC's tables. PVC 1/4"–1/2" is not in DOC-0049, so give its bore.
  A run with neither a catalog size nor a bore falls back to DRC's 25 mm default, and that is never
  silent: `PipeSpec.defaulted` is set, and every card resting on it carries status `Default Bore`.
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
  by about 1.5 m in Salt Lake City (audit E5).
  - The kernel therefore has **no default atmosphere**. `System.atm_pa` must be given, and
    `operating_point_si` / `npsha_si` raise `ValueError` without it.
  - For a design, pass `site_atmosphere(elevation_ft, water_temp_f)[0]`, which is what
    `build_system` and the envelope do. It takes the barometric figure from
    `safety._atm_head_ft(elevation)` and the vapor pressure from
    `safety._vapor_pressure_psia(temp)`, with the Antoine value outside that table's 40–120 F.
  - To reproduce DRC, pass `units.PATM` explicitly, as the goldens do.
- **A PRV or VFD set below an open terminal's overflow** shuts the weir off: Q = 0, as DRC's
  arithmetic gives. `OpPoint.control_below_overflow` flags it, and the envelope reports it as status
  `Control Below Overflow`. A VFD that holds a flow is not built yet.
- **New terminals** have no JS reference and carry hand goldens: open discharge, fixed flow and
  parallel. An open discharge has an infinite conductance, so the operating point bisects on flow
  at the pinned overflow pressure instead of on pressure. One pump with N parallel orifices equals
  one lumped orifice to 1e-12. Phase B's network solver must reproduce that degenerate case.

## Regenerating the goldens

The JSON is committed, so CI needs no Node. To regenerate, check out DRC beside this repository and
run from the repo root:

```bash
for m in water catalog fittings segment pump terminals operating_point manifold surge basin_checks autosize upkeep structure; do node tools/golden/drc/gen_$m.mjs ../digital-rain-curtain; done
```

Each file records `drc_sha` (the DRC commit, `-dirty` if `src/` had local changes) and `node` (V8's
`Math.pow` moved some values by 1 ulp between Node versions, which is why the tolerance is 1e-12 and
not equality). Each file must stay under 200 KB, because this repository is public. The current files
are from DRC `737fa80f` on Node v24.22.1.

## Phase B: where it departs from DRC, on purpose

- **The manifold is a takeoff list**, `[Takeoff(x, k, dz, open)]`, not a uniform pitch with one
  branch resistance. Friction between neighbours uses their actual spacing. `curtain_manifold`
  builds DRC's rain-curtain manifold as such a list, and a hand-written uniform list reproduces
  `manifoldSolve` to 1e-12. DRC's one-valve center or both-ends feed builds an index -1; a single
  takeoff is one run here.
- **`ManifoldDemand`** is DRC's lumped `demandModel` by default and an exact per-call solve with
  `exact=True`. Its reference pressure is the design pressure, from the curtain builder or the
  takeoffs' `q_design`; with neither it is 1 bar and `note` says so.
- **The network solver counts suction friction in the head balance.** DRC's `operatingPoint` uses
  the suction only for NPSHa. A pump with no suction segment draws straight from its `source`
  basin, which is DRC's case and the one the 1e-9 comparison uses.
- **The surge arrestor uses the site's barometric pressure** for the gas's absolute pressure; DRC
  adds sea-level `PATM`. The kernel defaults to `PATM` for the goldens.
- **Basin checks take the plan area and each feature's flow and fall time** from the caller; DRC
  builds them from the curtain. DRC's splash and drift catch width is not ported (audit E11):
  setback is phase C's.
- **Spray loss** defaults to DRC's 0.5% of flow, overridable per family by the house rule
  `spray_loss_pct_<family>`.

## The network solver

`network.py` is the junction-balancing solver Nikolas asked for on 2026-11-01 (multi-pump headers
and ring mains in version one). It uses the global gradient algorithm of Todini and Pilati, the
method EPANET uses: Newton on every link flow and every free node head together. Pumps, segments,
terminals, the bypass and a PRV are links with a head drop `h(Q)`. The one-way links (a pump
with a check valve, terminals, bypass, PRV) open and close between steps. VFD and PRV controls are
outer bisections with DRC's caps. The module docstring has the details. Each solved network
reports:

- per segment: flow, velocity, Reynolds number, friction factor, loss, and the DOC-0049 velocity
  status;
- per node: head and pressure;
- per pump: operating point, efficiency, power and NPSH margin, plus the `backflow`, `check_closed`,
  `dead_head`, `runout` and `past_curve` flags;
- per terminal: flow, pressure, design flow and state (`open`, `closed` or `starved`).

Every finding is a typed check (see below).

### The `network` dict (PLAN.md 3.3, US units)

```jsonc
{"nodes": [{"id": "src", "kind": "basin", "basin": "b1", "elevation_ft": 0},   // basin: the water surface
           {"id": "p1", "kind": "pump", "elevation_ft": -1},                    // pump centerline
           {"id": "j1", "kind": "junction", "elevation_ft": 1},
           {"id": "t1", "kind": "terminal", "feature": "f1", "elevation_ft": 10}],
 "pumps": [{"id": "p1", "item": null, "curve": [{"flow_gpm", "head_ft", "efficiency_pct", "npshr_ft"}]
                                          /* or {"points", "shutoff_head_ft", "max_flow_gpm", "motor_eff_pct"} */,
            "speed_pct": 100, "control": "none|vfd|prv", "setpoint_psi": null,
            "control_node": null,      // default: the only terminal node unless it is an open discharge, else the pump discharge
            "check_valve": true, "bypass_in": 0, "running": true,
            "source": "src",           // only when the pump has no suction segment (draws straight from the basin)
            "generic_class": null}],
 "segments": [{"id": "s1", "from": "src", "to": "p1", "line": "suction|discharge", "material": "SCH40 PVC",
               "nominal_size": "3\"" /* null = size me */, "id_in": null, "length_ft": 8, "branches": 1,
               "fittings": [{"key": "elbow_90", "qty": 2}], "components": [{"key": "filter_ccp520"}],
               "exposed": false, "hanger_spacing_ft": null}],
 "terminals": [{"id": "t1", "kind": "orifice|open_discharge|fixed_flow|manifold|parallel", "feature": "f1",
                "node": null,              // default: the node of the same id, else the node carrying its feature
                "params": {"design_gpm": null, "switched": false, ...}}]}   // params per kind: terminals.build_demand
```

A segment that ends at a pump feeds its inlet, and one that starts at a pump leaves its discharge.
So pumps in series are simply two pumps joined by a segment. Each terminal kind takes the params
that `terminals.build_demand` documents. A `manifold` terminal takes `manifold.manifold_from_us`:
either a `takeoffs` list or a uniform `{count, pitch_in, ...}` row.

### `derive(model, setup, catalogs, rules)`

`autosize.derive` runs PLAN.md 2.2 steps 5 to 8 in order:

- 5: basin and surge;
- 6: pipe sizes;
- 7: operating point and pump;
- 8: control, bypass and surge.

It writes only into sections whose `setup` entry is `auto`, and never mutates its input. Step 5
needs pipe volumes, so it uses the sizes step 6 is about to choose (same rule, same design flows).
Design flows per segment come from a design-flow solve: pumps act as ideal sources and terminals as
fixed flows at their design flow, so loops split by resistance. It returns:

```jsonc
{"model": { /* the derived model-2 dict: sizes, pump item + curve, control + setpoint_psi + control_node,
               bypass_in, basins[].depth_in / freeboard_in, and "surge": {"arrestors": [{pump, gas_l,
               precharge_psi}], "closure_ms"} */ },
 "results": [ /* CalcResult.to_dict() in ladder order: basin_checks, pick_pipe (one per sized segment),
                 rank_pumps, auto_bypass, auto_surge, network_operating_point */ ],
 "checks": [ /* every typed check, de-duplicated by key */ ],
 "network": { /* NetworkResult.to_dict(): converged, iterations, solves, nodes, segments, pumps,
                 terminals, totals, checks */ },
 "ranking": [ /* eval_pump per catalog pump: name, item, ok, fails [{code, ref, text}], reasons,
                 control, setpoints, flow_gpm, design_gpm, margin_pct, power_kw, npsh_margin_ft, hp, miss */ ]}
```

`catalogs` is `{"pumps": [{item, name, points | curve, shutoff_head_ft, max_flow_gpm,
motor_eff_pct, hp, generic_class}]}`. Phase D's Pump Curve rows map onto this shape. `rules` is the
house-rules dict. It uses `engine/rules.py`'s keys (`freeboard_multiplier`,
`spray_loss_pct_<family>`, `labor_hours_<family>`, `labor_hours_per_sqft_basin`, `kwh_rate`,
`water_rate_per_ccf`) and adds its own, with defaults in `autosize.RULE_DEFAULTS`: `head_margin_pct`,
`npsh_margin_ft`, `bep_min` and `bep_max`, `grad_max`, `over_ratio`, `vfd_min_hp`, `valve_close_ms`,
`pump_stop_ms` and `surge_allow_psi`.

### Typed checks (for phase E's `issues.py`)

Every phase B check is the same dict, built by `_checks.make_check`:

```jsonc
{"key": "pump.runout|p1", "code": "pump.runout", "severity": "blocker|warning|info",
 "title": "...", "remedy": "...", "section": "basin|piping|pump|control|bypass|surge|treatment|structure|outputs",
 "ref": "p1", "value": {...}}
```

`severity` uses `issues.py`'s vocabulary. `section` is the design model's `setup` key to reopen.
`key` stays the same across re-derives, so an acknowledgement survives. Phase E maps these onto
`DesignIssue`; phase B writes no `issues.py` producers.

## What phase E builds on

- `autosize.derive(model, setup, catalogs, rules)` for PLAN.md 2.2 steps 5 to 8.
- `network.solve_network(network, water_temp_f=, elevation_ft=)` for any re-solve, for example a
  duty-point chart at another speed.
- `bom.bom_rows(model, rules=)` and `bom.merge_sections(*phase_lines)` for the Water Feature Design
  Line rows.
- `upkeep.water_plan`, `maintenance_schedule` and `om_per_year` for the O&M outputs (PLAN.md 2.2 step
  12).
- `structure.pipe_hangers` and `floor_load` for supports and rooftop or podium basins.
