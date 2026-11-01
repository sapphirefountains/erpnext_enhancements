# Fountain Designer rebuild: engine audit of three codebases

Research note, 2026-11-01. Read-only audit; nothing outside this file was changed.

This note compares three codebases:

- **Frappe**: the `erpnext_enhancements` engine, `erpnext_enhancements/water_engineering/engine/`. It is Python, stdlib-only, US customary units, and golden-tested against the DOC-0048 and DOC-0049 workbooks.
- **DRC**: the Digital Rain Curtain hydraulics, `digital-rain-curtain/src/hydro/*.js`, plus `src/electrical.js`, `src/upkeep.js` and `src/rigging.js`. It is JavaScript and works in SI units.
- **Aquafect**: the Rust port of DRC, `aquafect/crates/aquafect-core/src/hydro/` and `src/site/`. It matches DRC by golden vectors and adds `circulation.rs` and `pumpset.rs`.

Paths below are relative to each repository's root.

Production facts were checked read-only through the ERPNext MCP on 2026-11-01:

| Record | Count |
|---|---|
| `tabPump Curve Point` rows | **0** |
| Items with `custom_rated_gpm > 0` | **7** |
| ...of which have `custom_rated_tdh_ft` | **1** |
| `tabNozzle Profile` | 5 |
| `tabWater Feature Design` | **1** |
| `tabControl Panel Design` | 2 |

---

## 0. Top recommendations (ranked)

1. **Replace the steady-state hydraulic core: port DRC's friction model and operating-point solver.** That means Darcy-Weisbach with the Churchill friction factor, temperature-dependent water properties, Darby 3-K fittings, and pump curve meets system curve. The Frappe engine has no operating point. `pump.select_pump` only checks that a pump's head at the design flow is at least the TDH. As a result the engine can never say:
   - what flow the chosen pump actually delivers;
   - what power it draws;
   - its NPSH margin at the operating point;
   - whether an oversized pump runs out past the VGB suction-cover rating.

   That last gap is a safety blind spot, because `safety.suction_outlet_vgb` is checked at the *design* flow, not the *operating* flow.
2. **Keep the Frappe shell: the `CalcResult` envelope, `run_spine`'s partial-input contract, `issues.py`, and every workbook-golden function.** Keep Hazen-Williams as a *workbook-reproduction cross-check* next to Darcy, not as the governing loss.
3. **Generalize DRC's "demand" interface (`manifold.demandModel`, `{Q(p), pFor(Q)}`) into one terminal-device abstraction.** Orifice nozzles, nozzle arrays and manifolds, weir troughs, open discharges and fixed-flow features all become a pressure-to-flow relation that `operatingPoint` can solve against. This is what turns rain-curtain code into fountain-designer code.
4. **Port DRC's manifold solver (`manifold.manifoldSolve`) as the nozzle-array / header-distribution engine.** It is the most valuable piece that Frappe lacks entirely: Frappe's `feature.nozzle_array_flow` is `count × gpm_each`, with no pressure dependence and no end-to-end spread.
5. **Make the pump catalog real before anything else depends on it.** Prod has 0 curve points, and only 1 of 7 rated pumps has a head rating. DRC's `presets.PUMPS` curves are labelled "(typical)" and are invented shapes. A solver is only as good as the curves it is fed. This is a data task, and it gates recommendation 1.
6. **Add electrical conductor sizing and voltage drop from DRC (`electrical.js` `WIRES`, `dropPct`, `circuitPlan`).** Merge it with Frappe's stronger motor and feeder logic (3-phase FLC tables, NEC 430.24 and 430.62 in `engine/electrical.py`). Each codebase has half of what is needed.
7. **Port DRC's auto-sizing and verdict pattern (`presets.pickPipe`, `rankPumps`/`evalPump` with a `fails[]` list, `autoBypassMm`, `autoSurge`, `autoBasin`, `verdict`).** Re-express it as rules over the generalized model. Map the verdicts into `issues.py` `DesignIssue`s rather than a second verdict system.
8. **Reuse the Aquafect golden-vector method, not its vector files.**
   - The method: a Node generator imports the DRC JS read-only and writes JSON. Python then matches it to 1e-12 relative for closed-form math and 1e-9 for iterative solvers.
   - The files: Aquafect's `tests/golden/*.json` total about 59 MB. `erpnext_enhancements` is a **public** repo, so the generators must be trimmed to small vector sets (≤200 KB per module).
9. **Resolve the strategic conflict before porting.** Aquafect's `docs/plan/05-rain-curtain-handoff.md` (step 3, "v0.1 hydraulic core") states "the ERPNext water tools are frozen until Aquafect replaces them". It also proposes a general network solve (EPANET GGA) in Rust. A Python rebuild in Frappe and a Rust v0.1 in Aquafect would build the same thing twice. One must be canonical (§5, decision D1).
10. **Close the cheap, still-open `SOURCE_DATA_AUDIT.md` items as part of the rebuild, and drop the stale ones** (§6). The low-velocity band is already done in code (`engine/pipe.py` `STATUS_SETTLING`), though the audit still lists it as open.

---

## 1. Capability matrix

Legend for rigor: **A** = physically complete and verified against an external reference. **B** = sound physics, verified only by self-consistency or hand calcs. **C** = empirical rule or simplified model. **—** = absent.

"Aquafect" means a byte-for-byte port of DRC unless a row says otherwise. Every DRC module listed in `aquafect-core/src/hydro/mod.rs` is marked "done", golden-matched to the JS at 1e-12 or 1e-9 (`tools/golden/gen_*.mjs`).

### 1.1 Pipe friction model

- **Frappe** (`engine/pipe.py` `hazen_williams_loss`, `size_pipe`; constants in `engine/constants.py`):
  - Uses Hazen-Williams `hf = 10.44·L·Q^1.85/(C^1.85·D^4.8655)`, with C=130 for PVC. The TDH variant uses 10.456 (`HW_CONSTANT_TDH`).
  - Verified to the workbook cell, DOC-0049 `A - Pipe Size!G7` (`tests/test_water_engine.py` `test_hazen_williams_*`).
  - Rigor C: no temperature or viscosity term.
- **DRC** (`src/hydro/common.js` `frictionFactor`, `water`; `src/hydro/flow.js` `segmentLoss`):
  - Darcy-Weisbach with the Churchill (1977) friction factor, valid for laminar, transition and turbulent flow.
  - Absolute roughness per material (`MATERIALS`: PVC 0.0015 mm, PEX 0.007, galvanized steel 0.15).
  - Viscosity and density from temperature (`water(T)`: Vogel viscosity, Thiesen density, Antoine vapor pressure, IAPWS surface tension).
  - Rigor B: `tests/hydro/common.test.mjs` checks the water properties at 20 °C against reference values; `flow.test.mjs` (a) checks `segmentLoss` against a hand Darcy calc.
- **Aquafect**: the same, in `hydro/common.rs` and `flow.rs`.
- **Gaps and errors**:
  - **Frappe is weaker.** Hazen-Williams is an empirical fit for water at about 40–75 °F in turbulent flow and larger pipe. It is unreliable in two places this business operates:
    - small bores (¾"–1½" nozzle feeds and therapy-jet loops);
    - warm water: spas at 104 °F, where viscosity is about 40% lower than at 60 °F.
  - The exponent on C is 1.85, which reproduces the workbook; the textbook value is 1.852. That is documented, not a bug.
  - DRC has no Hazen-Williams option. The DOC-0049 goldens would be lost if Frappe simply switched.

### 1.2 Fittings and minor losses

- **Frappe** (`engine/data/fittings.py` `FITTING_K`, `COMPONENT_CURVES`; `engine/tdh.py` `fitting_minor_loss`, `component_loss`):
  - Fixed K per fitting, from DOC-0049 `H - TDH!B26:C53`, used as `ΣK·V²/2g`.
  - Equipment losses come from real nonlinear manufacturer curves (DOC-0049 sheet 7: filters, skimmer, multiport, heater), interpolated, with a warning past `max_gpm`.
  - Rigor C for fittings; A for equipment curves (manufacturer data).
- **DRC** (`src/hydro/common.js` `FITTINGS`, `fittingK`, `fitSumK`):
  - Darby 3-K, `K = K1/Re + Ki(1+Kd/D^0.3)`, so K varies with size and Reynolds number. Crane L/D with f_T for the foot valve.
  - Strainer and filter K scale with clogging, `/(1-clog)²`.
  - The cartridge filter is a single "approx." K=10. There are no manufacturer equipment curves.
  - Rigor B.
- **Aquafect**: the same (`common.rs` `fitting_k`, `fit_sum_k`).
- **Gaps and errors**:
  - Frappe's fixed K (90° elbow 0.81) matches roughly ¾"–1" pipe. It **overstates** large-pipe losses: Darby gives about 0.5 for 4". That is conservative, but it inflates pump sizes on large features.
  - DRC lacks Frappe's equipment curves.
  - **Best of both:** Darby 3-K for fittings, plus Frappe's `COMPONENT_CURVES` for equipment, plus DRC's clog factor applied to filter curves (clean-to-dirty ΔP).

### 1.3 Pump curves and operating point

- **Frappe** (`engine/pump.py` `head_at_flow`, `select_pump`; `api/water_design.py` `pump_curves` reads Item `custom_pump_curve`):
  - Linear interpolation of a curve. The selection test is `head(design_flow) ≥ TDH`, falling back to the rated GPM/TDH envelope, or to "flow-only".
  - **No system curve, no intersection, no efficiency, no power.**
  - Rigor C. Prod has **0 curve points**, so in practice every match is rated-only or flow-only.
- **DRC** (`src/hydro/flow.js` `pumpHeadM`, `pumpEff`, `npshrM`, `bepFlow`, `maxFlow`, `shaftPowerW`, `operatingPoint`, `systemCurve`, `pumpCurve`):
  - Full curve set: head, efficiency and NPSHr against flow, with linear extrapolation.
  - `operatingPoint` bisects on manifold-inlet pressure until pump head equals static plus supply loss plus demand. It includes bypass flow, the "cannot lift" condition and dead-head detection.
  - Outputs hydraulic, shaft and electrical power.
  - Rigor B: `flow.test.mjs`, 14 tests, internal consistency.
- **Aquafect**: the same (`flow.rs` `operating_point`). The CLI `aquafect op` prints the result in gpm, ft, psi and hp.
- **Gaps and errors**:
  - **The largest Frappe gap.** It cannot compute actual delivered flow. An oversized pump "passes" and is then never checked at its real operating point, so velocity limits, VGB cover flow and the pipe pressure rating are all evaluated at design flow.
  - DRC's pump catalog is fictional ("typical"; `src/hydro/presets.js` `PUMPS`). The solver is real, but its data is not.

### 1.4 Affinity laws and VFD

- **Frappe**: none. `engine/pump.py` docstring: "no ... VFD-vs-starter logic in the sheets". `controls.py` only mentions VFDs in wind-interlock text (`DEFAULT_INTERLOCKS`).
- **DRC** (`flow.js` `pumpHeadM`: `H = s²·H(Q/s)`, efficiency at `Q/s`, NPSHr scaled by s²; `operatingPoint` VFD branch):
  - The VFD branch bisects speed so the manifold holds a set pressure, with a 15% floor.
  - The PRV branch throttles to the set point.
  - `autoBypassMm` sizes a minimum-flow bypass at 15% of BEP flow.
  - Rigor B.
- **Aquafect**: the same.
- **Gaps and errors**:
  - DRC scales NPSHr by s², a common approximation. Real NPSHr scaling is closer to s^1.5–2, and it is not guaranteed at low speed.
  - The efficiency curve is assumed speed-invariant apart from the flow shift. That is acceptable at 60–100% speed.

### 1.5 NPSH

- **Frappe** (`engine/safety.py` `npsh_available`, `_atm_head_ft`, `_vapor_pressure_psia`):
  - `Ha + Hz − Hf − Hvp`, with **altitude** (barometric lapse) and a vapor-pressure table from 40–120 °F.
  - Status bands use a 3 ft margin. NPSHr is typed in by hand.
  - It is **not called by `run_spine`**: only by the desk endpoint and the AI tool (`assistant_tools/water_calc.py`).
  - Rigor B, golden-checked against a hand-computed lift case.
- **DRC** (`flow.js` `npshaM`):
  - `(PATM − pv)/ρg − lift − suction loss`, with NPSHr from the pump curve, evaluated at the operating point (`operatingPoint.npshMargin`). Gated at 0.5 m in `presets.evalPump`.
  - Rigor B.
- **Aquafect**: the same.
- **Gaps and errors**:
  - **DRC error for Utah:** `PATM = 101325` is fixed at sea level (`common.js`), and there is no site elevation anywhere in `src/hydro/`. At Salt Lake City (about 4,300 ft) atmospheric head is about 4.9 ft lower. DRC therefore *overstates* NPSHa by about 1.5 m, which is three times its own 0.5 m margin.
  - Frappe has the right altitude model, but no NPSHr curve and no wiring into the spine.
  - **Port DRC's operating-point NPSH with Frappe's `_atm_head_ft`.**

### 1.6 Water hammer and transients

- **Frappe** (`engine/safety.py` `water_hammer`):
  - Joukowsky `a·ΔV/g`, using representative wave speeds (`WAVE_SPEED_FPS`: PVC 1300 ft/s, copper 4270, steel 4500).
  - Slow closure is scaled by `2L/a ÷ tc`. Peak (static plus surge) is compared with the pipe rating.
  - Standalone; not in the spine.
  - Rigor C.
- **DRC**:
  - `transient.js` `surgeFor`: Joukowsky with the Michaud reduction, using a Korteweg wave speed from pipe ID, wall and modulus (`common.js` `waveSpeed`).
  - `accumulatorGasL`: arrestor gas volume from kinetic energy, isothermal.
  - `createTransient`: a lumped water-column stepper.
  - `hammer.js` `hammerWave`: a full method-of-characteristics solve of the supply line. It covers the pump curve and check valve at one end, valve closure plus arrestor plus pipe compliance at the other, Brunone unsteady friction (KB=0.1) and column-separation flagging.
  - Rigor B, with `analysis.test.mjs` cases.
- **Aquafect**: the same (`transient.rs`, `hammer.rs`).
- **Gaps and errors**:
  - Frappe's fixed 1300 ft/s for PVC Sch40 is roughly right for 2–4". It is not computed from the pipe, and it ignores SDR and thin-wall flexible pipe: hose comes out around 100 ft/s in DRC.
  - DRC's MOC solve is single-pipe only; the supply line is lumped into one equivalent pipe (`transient.supplyWave`).
  - Fountains with solenoid-choreographed jets (interactive pads, digital curtains, choreographed nozzle arrays) need this. Static basins do not.

### 1.7 Manifold and branch balancing

- **Frappe**: none. `feature.nozzle_array_flow` returns `count × gpm_each`, and `run_spine` sums feature flows.
- **DRC** (`src/hydro/manifold.js` `manifoldSolve`, `demandModel`, `sizeSweep`, `manifoldRuns`):
  - An exact shooting solve along the manifold, using Illinois regula falsi on the inlet flow. It covers per-takeoff orifice flow, Darcy friction between takeoffs, velocity-head pressure regain (γ=0.9), and sag and tilt elevation (`common.js` `elevationProfile`).
  - Supports end, center and both-ends feed.
  - `sizeSweep` finds the smallest bore with a jet-speed spread under the target.
  - Rigor B (`manifold.test.mjs`, 13 tests).
- **Aquafect**: the same.
- **Gaps and errors**:
  - DRC assumes uniform pitch and identical branches (one `branchK` for all valves) along a straight pipe. Generalizing needs a per-takeoff K and spacing; the solver's structure allows it.
  - Frappe has nothing here, so nozzle rings, splash-pad headers and weir spray bars cannot be checked for evenness.

### 1.8 Nozzle and jet physics

- **Frappe** (`engine/feature.py` `nozzle_flow`, `jet_trajectory`, `JET_EFFICIENCY` in constants):
  - Orifice flow `Q = Cd·A·√(2gh)`, or `rated × √(h/h_rated)`, from a Nozzle Profile (5 on prod).
  - Jet height is `k × head`, with k=0.9 for solid and 0.6 for aerated. Vertical only; no drag, no angle, no range.
  - Rigor C. The k values are unsourced ("engineering standard").
- **DRC**:
  - `common.js` `nozzle`, `branchK`: the nozzle as a quadratic resistance with Cc and Cv, plus valve Kv and drop tube.
  - `checks.js` `jetChecks`: jet breakup length (Grant & Middleman 1966), drop size (Rayleigh 1.89·d), and Weber and Ohnesorge numbers.
  - `core.js` `physOf`: closed-form quadratic drag for falling drops (terminal velocity from drop size). This is downward fall only.
  - Rigor B.
- **Aquafect**: the same (`checks.rs` `jet_checks`; `curtain/phys.rs`).
- **Gaps and errors**:
  - None of the three can do arcing or vertical jets with drag: laminar arcs, interactive jets, geysers. That is a **new module** (2-D point-mass trajectory with quadratic drag, up and down; see §3).
  - Frappe's k=0.9 vertical-height rule overstates tall jets, because drag losses grow with height.
  - DRC's breakup and drop-size model generalizes to any free jet (laminars, sheer descents).

### 1.9 Weirs and sheet falls

- **Frappe** (`engine/feature.py` `weir_flow`, `tiered_fountain_flow`, `edge_sheet_guidance`; `WEIR_*` constants):
  - Francis `Q = 36·L·h^1.5 − 0.3·n·h^2.5` in GPM per ft and inches. It reproduces DOC-0049 `I - Weir!C16`, and the constants check out against Francis in cfs (3.33·448.8/12^1.5 = 35.95).
  - Edge guidance: wind bands from DOC-0049 B, and the "operate at 0.5, engineer for 4–6 GPM/ft" rule.
  - Tiered fountains are governed by the largest rim.
  - Rigor A to the workbook; C physically.
- **DRC**: none. A curtain is discrete jets.
- **Aquafect**: none.
- **Gaps and errors**:
  - Francis is calibrated for heads of about 0.1–2 ft. The ⅛"–¼" decorative heads used here are dominated by surface tension; the empirical GPM/ft bands partly cover this.
  - There is no nappe trajectory, so no catch-basin width for a sheet fall, and no water-in-transit for a fall. The slot-channel circular-crest weir (DOC-0049 M) is still open.
  - DRC's in-flight volume, `Q × fallT` in `checks.basinChecks`, transfers directly to sheet falls once a nappe fall time exists.

### 1.10 Basin, surge and water-in-transit

- **Frappe**:
  - `engine/basin.py` `basin_volume`: rectangle or cylinder only. `turnover_gpm` also lives there.
  - `engine/drainage.py` `surge_basin_volume`: DOC-0049 B allowances for overflow, freeboard, swimmer displacement, precipitation, evaporation and vortex.
  - Rigor A to the workbook.
- **DRC** (`checks.js` `basinChecks`):
  - Pipe volumes, in-flight water, and drain-back with no check valve, giving level rise against freeboard.
  - Intake submergence `S = D(1+2.3·Fr)` per ANSI/HI 9.8.
  - Splash and wind-drift catch width, strainer mesh from orifice size, and turnover minutes.
  - `presets.autoBasin` sizes the basin from these.
  - Rigor B for HI 9.8; C for splash (`vh = 0.2·vImp·0.6` is an unsourced heuristic).
- **Aquafect**: the same, plus **`hydro/circulation.rs`**: a time-domain loop covering basin level, water in the air, pipe storage, drain-back, the float-valve makeup and overflow, with the level fed back into static head and NPSH. Several of its constants are labelled assumptions (`MAKEUP_M3S`, `DRAIN_S`, `FLOAT_BELOW_M`).
- **Gaps and errors**:
  - Frappe has no intake-submergence (vortex) check beyond the fixed 12" vortex allowance (`SURGE_VORTEX_IN`), no drain-back volume, and no in-flight volume.
  - DRC's `basinChecks` is the generalizable core. It should be fed with the feature's own fall time and footprint, not the curtain's.

### 1.11 Drainage and overflow

- **Frappe** (`engine/drainage.py` `manning_drain_flow`, `size_drain`; `engine/workbook.py` `overflow_check`, `open_channel_flow`, `vertical_pipe`):
  - Manning half-full (deliberately conservative relative to DOC-0119), with a slope band.
  - 7.9 in/hr design rain, the overflow standpipe table, Froude and Reynolds regimes for runnels.
  - Rigor A to the workbook.
- **DRC**: none (drain-back only).
- **Aquafect**: none.
- **Gaps and errors**: Frappe is strongest here. Keep it unchanged.

### 1.12 Evaporation and makeup

- **Frappe** (`engine/treatment.py` `evaporation_rate`, `make_up_water`): ASHRAE Applications Ch. 6, `0.1·A·AF·(Pw−Pa)`, plus the auto-fill valve table (DOC-0049 D). Rigor B.
- **DRC**:
  - `checks.basinChecks`: the Carrier form `A·(pw−pa)(0.089+0.0782V)/latent`, with wind, plus spray loss at 0.5% of flow.
  - `upkeep.js` `waterPlan`: cycles of concentration `C(t) = Css − (Css−1)e^(−spray·t/V)` to set drain-and-refill days, plus the Legionella range of 25–45 °C (ASHRAE 188, CDC).
  - Rigor B.
- **Aquafect**: the same (`site/upkeep.rs`); circulation adds the makeup over time.
- **Gaps and errors**:
  - The two evaporation formulas are of the same family. Frappe's 0.5% spray loss is missing; it matters for jets, sprays and splash pads.
  - DRC's mineral-cycles and Legionella logic generalizes to every decorative fountain and is absent from Frappe.

### 1.13 Chemistry

- **Frappe** (`engine/chemistry.py` `chlorinator_feed`, `chemistry_targets` with the CYA floor, `ozone_sidestream`; `engine/treatment.py` `lsi_index`, `chemical_dose`, `uv_dose`):
  - IBC 3133B.1 chlorinator minimum, DOC-0119 targets, USEPA ozone CT, LSI, and manufacturer dose tables.
  - Rigor A/B for regulated venues.
- **DRC** (`upkeep.js` `TREATMENTS`, `waterPlan`): hand-dose millilitres of hypochlorite for fill and daily burn-off.
- **Aquafect**: the same as DRC.
- **Gaps and errors**: Frappe is far ahead. Take only DRC's "daily dose in measurable units" presentation.

### 1.14 Heating

- **Frappe** (`engine/treatment.py` `heating_load`):
  - DOC-0049 O-sheet: `water_wt × cover × 1.15 × wind × ΔF` BTU/day, plus a warm-up rate.
  - Rigor A to the workbook; **physically questionable**. Heat loss scales with *surface area* (evaporation, convection, radiation), not volume, so the sheet's volume multiplier misstates shallow, wide fountains.
- **DRC**: none.
- **Aquafect**: none.
- **Gaps and errors**: keep it as a workbook reproduction. Add a surface-area heat balance as the governing calc (§3, "add").

### 1.15 Filtration

- **Frappe** (`engine/treatment.py` `filtration_area`; `FILTER_MAX_RATE`): area from the Utah R392-302 / NSF 50 rate by media, plus backwash. Equipment ΔP comes from `COMPONENT_CURVES`. Rigor A.
- **DRC**: strainer mesh from orifice size (`basinChecks.meshNo`), and clog factor on K.
- **Aquafect**: the same as DRC.
- **Gaps and errors**: combine Frappe's sizing with DRC's clog scaling, so the operating point can be computed both clean and dirty.

### 1.16 Electrical sizing

- **Frappe**:
  - `engine/pump.py` `electrical_load`: breaker at 125% of FLA, rounded up.
  - `engine/electrical.py` `motor_flc`: NEC 430.248 (1-phase) and **430.250 (3-phase)**.
  - `total_connected_load` (430.24), `service_main_breaker` (430.62, rounds **down**), `control_transformer_va`.
  - **No conductor sizing, no voltage drop.**
  - Rigor B.
- **DRC** (`src/electrical.js`):
  - `motorFLC` (1-phase only); `WIRES` (NEC Ch. 9 Table 8 resistance, Table 310.16 60 °C ampacity, 240.4(D)).
  - `dropPct` with a 3% branch limit; `circuitPlan` packs loads into 15/20 A circuits (210.23(A)).
  - Class 2 24 V coil supply with fused branches and the coil-wire drop at the farthest valve (`coilPlan`).
  - Generator and battery sizing (`backupPlan`); GFCI and Article 680 bonding note.
  - Rigor B (`electrical.test.mjs`, 17 tests).
- **Aquafect**: the same (`site/electrical.rs`).
- **Gaps and errors**:
  - **Both label the motor breaker as 125% of FLA.** NEC 430.22 sets *conductors* at 125%. Motor *OCPD* is set by 430.52 and Table 430.52: up to 250% for an inverse-time breaker.
    - So `pump.electrical_load` cites 430.52 for a rule that is really 430.22. Its "breaker" is a minimum that may nuisance-trip on start.
    - `service_main_breaker` seeds 430.62 from that undersized branch value.
    - This needs an engineer's decision (D6), not a silent change.
  - DRC is 1-phase only and stops at 100 A and #2 AWG.
  - Frappe is missing the conductor and drop half.

### 1.17 Lighting

- **Frappe**:
  - `engine/workbook.py` `lighting_design`: W/SF bands from DOC-0049 D.
  - `engine/controls.py` `calc_lighting`: fused SSR count at 60 W per 12 VDC relay, per DOC-0126.
  - Rigor A to source.
- **DRC**: `electrical.js` `LIGHTS`, `lightingW` (W/m of basin strip or wash bar); `fixtures.js` (lumens, beams, DMX looks; rendering-side).
- **Aquafect**: the same (`curtain/fixtures.rs`; `site/electrical.rs`).
- **Gaps and errors**: Frappe's DOC-0049 bands are the design authority. DRC's per-metre load is just another load row. The 12 V low-voltage drop to fixtures is missing in Frappe; DRC's `coilPlan` drop logic transfers to it.

### 1.18 Structural and rigging loads

- **Frappe**: none. `SOURCE_DATA_AUDIT.md` DOC-0119 #5 (concrete and rebar) is parked. `data/pipe_specs.py` holds `wet_lb_ft` and `max_span_ft` (DOC-0049 sheets 1–3), but nothing uses them.
- **DRC** (`src/rigging.js`):
  - `beamReactions`: continuous beam by the three-moment equation with Thomas algorithm, tested against textbook coefficients.
  - `manifoldLoad`, `rigPlan`: truss, hoist and base-plate loads, and floor load against ASCE 7 office and assembly live loads.
  - `common.js` `manifoldBeam`: sag `5wL⁴/384EI`.
  - Rigor B.
- **Aquafect**: the same (`site/rigging.rs`).
- **Gaps and errors**:
  - `beamReactions` plus Frappe's `wet_lb_ft`/`max_span_ft` would give pipe hanger loads and a span check for any exposed pipe run. That generalizes.
  - Truss and hoist logic is curtain and event specific.
  - Basin floor load (water depth times area against slab rating) generalizes to rooftop and podium fountains.

### 1.19 BOM and cut lists

- **Frappe**:
  - Print formats only. `setup_print_formats.py` produces Schedules: Equipment plus Piping, listing fittings per run.
  - The aggregated Fitting Schedule is open in `SOURCE_DATA_AUDIT.md`.
  - The engine has no BOM function.
- **DRC**:
  - `checks.js` `bomRows`, `bomCSV`: pipe with 10% waste, fittings grouped by size, valves and nozzles, manifold, equipment.
  - `route.js` `cutList`, `fittingCounts`: 20 ft sticks (`STICK_M`) with couplings.
  - `electrical.js` `elecBomRows`.
  - Rigor B.
- **Aquafect**: the same (`route.rs` `cut_list`).
- **Gaps and errors**:
  - DRC's grouping logic (`bomRows`) generalizes and solves the open "aggregated Fitting Schedule" item, as an engine function rather than Jinja.
  - `route.js` `pipeRoute` geometry is curtain-specific: the pump sits past the +x end, with a fixed riser height.

### 1.20 Auto-sizing and preset rules

- **Frappe**:
  - `pipe.size_pipe`: the smallest size within the velocity limit, from DOC-0049 SUPPORT limits (suction 4.5 fps; discharge 6.5 PVC / 4.9 copper; legal 8.0).
  - `drainage.size_drain`.
  - `select_pump`: the smallest adequate pump.
  - `run_spine` `next_inputs_needed`.
- **DRC** (`src/hydro/presets.js`):
  - `pickPipe`: velocity ≤1.5 m/s (4.9 fps) **and** friction gradient ≤0.04 m/m.
  - `pipingLayout`: suction one size up at ≤0.9 m/s.
  - `rankPumps`/`evalPump`: every catalog pump run through the full model, with a reasons list (weakest jet ≥97%, ≥8% head margin, ≤106% overspeed, NPSH margin ≥0.5 m).
  - `autoManifold` (≤2% spread), `autoBypassMm`, `autoSurge` (stock arrestor sizes), `autoBasin`, `deriveSetup` (ordered derivation), `KITS`.
  - Rigor B (`presets.test.mjs`, 15 tests).
- **Aquafect**: the same (`presets.rs`, 1,946 lines).
- **Gaps and errors**:
  - DRC's *pattern* generalizes: evaluate each candidate through the real model, then list why it fails. Its *thresholds* are curtain-specific (jet-speed uniformity).
  - Frappe's velocity limits are Sapphire's own (DOC-0049) and must win. DRC's friction-gradient cap is a good addition (decision D3).

### 1.21 Verdicts and checks

- **Frappe** (`issues.py`, the typed `DesignIssue` per section, with readiness and acknowledgement through `Water Design Issue Ack`):
  - `CalcResult.status` and `warnings`.
  - Safety caveats are mandatory in `warnings` (VGB "engineering aid only").
- **DRC** (`presets.verdict`, `electrical.elecVerdict`, `rigging.rigVerdict`, `upkeep.upkeepVerdict`): plain-language `{lvl: good|warn|bad|info, text}` lists.
- **Aquafect**: the same (`presets.rs` `verdict`, `VerdictLine`).
- **Gaps and errors**: Frappe's `issues.py` is the better architecture: typed, located and acknowledgeable. Port DRC's verdict *content* (the checks and their wording) as `DesignIssue` producers.

### 1.22 Regulated aquatics (pools and spas)

- **Frappe** (`engine/aquatic.py` `turnover_time`, `minimum_flow_rate`, `bather_load`, `skimmer_sizing`, `main_drain_flow`; `safety.suction_outlet_vgb`):
  - MAHC and state code, verified against the Fika spa submittal (SLCo Health Department 2023).
  - The VGB calculation reproduces DOC-0049 `P` to the cell.
  - Rigor A.
- **DRC**: none.
- **Aquafect**: none.
- **Gaps and errors**: Frappe only. Keep it unchanged, but evaluate VGB at the *operating* flow once one exists.

---

## 2. What generalizes beyond a rain curtain

| DRC / Aquafect piece | Generalizes to | Curtain-specific parts to strip |
|---|---|---|
| `common.js` `water`, `frictionFactor`, `MATERIALS`, `pipeSpec`, `FITTINGS`/`fittingK` | **Everything.** Every feature with a pipe | The `hose` material and `custom` bore are fine to keep |
| `flow.js` pump model, `operatingPoint`, `systemCurve`, PRV/VFD/bypass | Every pumped feature: weirs, cascades, nozzle arrays, jets, ponds, spas | The `simple` / `legacyPump` mode (a quadratic pump at the manifold) exists for old curtain projects. Drop it |
| `manifold.js` `manifoldSolve`, `demandModel`, `sizeSweep` | Nozzle arrays, splash-pad headers, nozzle rings, spray bars, perforated weir distribution pipes, multi-jet interactive pads | Uniform `pitchMm`/`valves` and one shared `branchK`. Generalize to a per-takeoff list `[{x, K, z, open}]` |
| `demandModel`'s `{Q(p), pFor(Q)}` contract | **The key abstraction.** Any terminal: orifice (`Q = G√p`), a fixed-flow weir trough (`pFor(Q) = ρg·h_static`, flow set by the weir head), an open pipe discharge, a valve plus nozzle | Its construction from curtain geometry |
| `transient.js` `surgeFor`, `accumulatorGasL`, `waveSpeed` | Any solenoid- or valve-switched system: interactive jets, choreographed arrays, digital curtains, auto-fill solenoids | — |
| `hammer.js` `hammerWave` (MOC) | Choreographed shows and long supply lines | One-pipe lumping; manifold-end boundary condition |
| `transient.js` `createTransient`, `analyzeShow` | Choreographed shows only | Show timeline coupling. **Skip** for the designer's first version |
| `checks.js` `basinChecks`: HI 9.8 submergence, drain-back, in-flight volume, Carrier evaporation, spray loss | Every basin: weirs (in-flight = nappe time × Q), cascades (per-tier storage), jets (plume volume), ponds | Splash heuristic; `Lb = W + 0.5` geometry; mesh-from-orifice |
| `checks.js` `jetChecks` (breakup, drop size) | Laminar jets, sheer descents, rain features, mist sizing | Pixel-fill (`pixelFill`) is curtain-only |
| `checks.js` `bomRows`, `route.js` `cutList` / `fittingCounts` / `STICK_M` | Every design's BOM and cut list | `pipeRoute` geometry is curtain-specific |
| `presets.js` `pickPipe`, `evalPump`/`rankPumps` pattern, `autoBypassMm`, `autoSurge`, `deriveSetup` ordering | Every design: "derive the auto sections, keep custom ones" | `CURTAINS`, `JETS`, `NOZZLES`, `VALVES`, `KITS`, jet-uniformity thresholds |
| `presets.js` `PUMPS` | **Nothing**: invented "typical" curves | Replace with the real catalog |
| `electrical.js` `WIRES`, `dropPct`, `wireForBreaker`, `breakerFor`, `circuitPlan`, `backupPlan` | Every panel and every rental or event feature | `coilPlan` (24 V valve coils) generalizes to any solenoid bank or low-voltage lighting run; TPIC driver choice is controller-specific |
| `upkeep.js` `waterPlan` (cycles, Legionella, hygiene days), `maintenanceSchedule` | Every decorative feature (ASHRAE 188 applies to all of them) | `valveLife` from show cycles applies to solenoid features only |
| `rigging.js` `beamReactions`, floor load | Pipe hangers and spans, rooftop and podium basins | Truss, tower and hoist tables |
| Aquafect `circulation.rs` | Weir and cascade water-in-transit and surge behaviour over time, auto-fill dynamics, start-up overflow | Its assumed constants (`MAKEUP_M3S`, `DRAIN_S`) need real values; Rust-only, no JS reference |
| Aquafect `pumpset.rs` | Controller interlock logic (low-water trip, run-on) | Firmware-specific timings |

**Weirs, cascades and ponds need one more idea DRC never needed: an open (non-pressurized) terminal.** A weir trough or spillway is fed until it overflows. Its demand is "deliver Q at static lift h" (`pFor(Q) = ρg·h_lift`, a vertical system curve offset). Q is chosen by the weir head from `feature.weir_flow` inverted, or by the GPM/ft rule. `operatingPoint` already handles this case through `demand.pFor`; it just needs a demand type with constant `pg` and infinite conductance.

---

## 3. Porting plan

### 3.1 Architecture of the rebuilt engine

Keep `engine/` stdlib-only and keep the `CalcResult` envelope (`engine/envelope.py`). Add a new subpackage `engine/hydraulics/`. It is the port, with SI internally to match DRC, which is what makes the 1e-9 parity possible. Convert to US units at the envelope boundary with exact constants (0.3048 m/ft; 3.785411784e-3 m³/gal; 6894.757 Pa/psi).

The existing `pipe.py`, `tdh.py`, `basin.py`, `feature.py`, `drainage.py`, `aquatic.py`, `chemistry.py`, `treatment.py` and `workbook.py` stay as **workbook-reproduction functions** with their current goldens. The new solver becomes the governing calculation. `run_spine` reports both: for example "TDH (DOC-0049 Hazen-Williams) 41.2 ft; operating point (Darcy) 118 GPM @ 38.7 ft". A divergence of more than 10% raises a `DesignIssue` instead of hiding one method.

The proposed model is a small graph that is still a list, which suits today's child tables:

```text
source (basin, level, elevation) → suction segment(s) → pump (curve, speed, control)
  → supply segment(s) → terminal(s)
terminal := orifice | manifold(takeoffs[]) | open_discharge(lift) | fixed_flow
```

A tree or loop network (EPANET GGA) is deliberately **not** in the first cut. Every current Sapphire layout is one pump to N terminals in parallel, and that can be solved as parallel demand curves summed (`Q_total(p) = Σ Q_i(p)`) at one node.

### 3.2 Module order, sources and effort

Estimates are in engineer-days for a Claude session. Each includes envelope wrapping, goldens and the CI step. They assume decisions D1–D4 are settled first.

| # | New Python module | Port from (DRC → Aquafect check) | Keeps from Frappe | Days |
|---|---|---|---|---|
| 0 | `tests/golden/drc/` generator plus `tests/drc_golden.py` helper (`close(rel)`, NaN/Inf decoding, float32 compare) | `aquafect/tools/golden/drc.mjs` pattern | test layout | **1.5** |
| 1 | `hydraulics/water.py`: `water(T)`, `friction_factor(Re, rr)`, `area_of`, `wave_speed` | `common.js` `water`, `frictionFactor`, `areaOf`, `waveSpeed` | — | **1** |
| 2 | `hydraulics/catalog.py`: `MATERIALS`, `sizes_for`, `pipe_spec`; merge with `data/pipe_specs.py` (velocity limits, PSI ratings, `wet_lb_ft`) | `common.js` catalog | DOC-0049 pipe specs and limits | **1.5** (reconcile DRC NPS OD/wall with DOC-0049 IDs; decision D2) |
| 3 | `hydraulics/fittings.py`: Darby 3-K `fitting_k`, `fit_sum_k` (with clog), mapping table from `FITTING_K` keys to 3-K keys, `COMPONENT_CURVES` kept | `common.js` `FITTINGS`, `fittingK`, `fitSumK` | `data/fittings.py` curves and keys | **1** |
| 4 | `hydraulics/segment.py`: `segment_loss` (Darcy plus fittings plus equipment curve) | `flow.js` `segmentLoss`, `supplyLoss`, `suctionLoss` | `tdh.segment_loss_results` audit-card shape | **1** |
| 5 | `hydraulics/pump.py`: `interp`, `clean`, `pump_head`, `pump_eff`, `npshr`, `bep_flow`, `max_flow`, `shaft_power`, affinity | `flow.js` same names | `pump.head_at_flow` semantics (replace it) | **1.5** |
| 6 | `hydraulics/terminals.py`: `Demand` protocol `{q(p), p_for(q), any_open, pg}`; `OrificeDemand`, `OpenDischargeDemand`, `FixedFlowDemand`, `ParallelDemand` | `manifold.js` `demandModel` (contract only) | `feature.nozzle_flow` (Cd, A), `weir_flow` | **1.5** (new code, no JS golden for the open types: hand goldens) |
| 7 | `hydraulics/operating_point.py`: `operating_point`, `system_curve`, `pump_curve`, `bypass_flow`, PRV and VFD branches, NPSHa **with altitude** | `flow.js` `operatingPoint`, `systemCurve`, `pumpCurve`, `npshaM`, `bypassFlow` | `safety._atm_head_ft`, `_vapor_pressure_psia` cross-check | **2.5** |
| 8 | `hydraulics/manifold.py`: `manifold_solve` generalized to per-takeoff `{x, K, dz}`, `size_sweep` | `manifold.js` `manifoldSolve`, `manifoldRuns`, `sizeSweep`, `manifoldVolumeL` | — | **3** (uniform case golden-matched; non-uniform hand-checked) |
| 9 | `hydraulics/surge.py`: `surge_for` (Joukowsky plus Michaud, Korteweg `a`), `accumulator_gas_l`, `std_gas_l` | `transient.js` `surgeFor`, `supplyWave`, `accumulatorGasL`; `presets.js` `stdGasL` | `safety.water_hammer` (keep as the simple form, cross-check) | **1** |
| 10 | `hydraulics/basin_checks.py`: HI 9.8 submergence, drain-back, in-flight volume, spray loss, Carrier evaporation | `checks.js` `basinChecks` (minus curtain geometry) | `drainage.surge_basin_volume`, `treatment.evaporation_rate` | **2** |
| 11 | `hydraulics/autosize.py`: `pick_pipe` (velocity plus gradient), `rank_pumps`/`eval_pump` with `fails[]`, `auto_bypass`, `auto_surge`, `derive` ordering | `presets.js` `pickPipe`, `evalPump`, `rankPumps`, `autoBypassMm`, `autoSurge`, `deriveSetup` | `pipe.size_pipe` velocity limits govern | **2.5** |
| 12 | `electrical.py` additions: `WIRES`, `drop_pct`, `wire_for_breaker`, `circuit_plan`, low-voltage run drop (from `coilPlan`), `backup_plan` | `electrical.js` | all of `engine/electrical.py` (3-phase, 430.24/430.62, control transformer) | **2** |
| 13 | `upkeep.py`: `water_plan` (cycles, Legionella, hygiene), `maintenance_schedule` | `upkeep.js` `waterPlan`, `maintenanceSchedule` | `chemistry.py` doses govern | **1** |
| 14 | `bom.py`: `bom_rows`, `cut_list` (20 ft sticks), `fitting_counts` → feeds the aggregated Fitting Schedule | `checks.js` `bomRows`, `route.js` `cutList`, `fittingCounts` | Schedules print format | **1.5** |
| 15 | `structure.py`: `beam_reactions`, hanger loads from `wet_lb_ft`, span check vs `max_span_ft`, basin floor load | `rigging.js` `beamReactions`, floor-load part of `rigPlan` | `data/pipe_specs.py` | **1** |
| 16 | `jets.py` (**new; no source**): 2-D trajectory with quadratic drag (arc and vertical), plume height and range, wind drift, breakup | `checks.js` `jetChecks` for breakup; `core.js` `physOf` drag closed form as a check | `feature.jet_trajectory` (keep k-rule as the workbook-style estimate) | **2.5** |
| 17 | `heat.py` (**new**): surface heat balance (evaporative from module 10, convective, radiative) | — | `treatment.heating_load` stays (DOC-0049 O) | **1.5** |
| 18 | `run_spine` v2 and `issues.py` producers for every new check | `presets.verdict`, `elecVerdict`, `rigVerdict`, `upkeepVerdict` wording | `run_spine` contract, `next_inputs_needed` | **2.5** |
| — | **MOC water hammer** (`hammer.js` `hammerWave`) | | | 3 (defer; only for choreographed shows) |
| — | `transient.createTransient` / `analyzeShow`, `live.js`, `analysis.js` `gradeLine`/`lossBudget` | | | defer (show-time and 3D-view specific; `lossBudget` is a nice print aid, 1 day, later) |

- **Critical path** (modules 0–8 plus 18): about 17 days to a working operating-point Fountain Designer.
- **Full list without the deferred rows**: about 33 days.
- **Order rationale**: 0 → 1 → 2 → 3 → 4 → 5 → 7 follows the JS import graph, which is also Aquafect's order (`docs/plan/01-hydro-port.md` "Module order"). Module 6 is the first divergence from DRC, so write it before 7 even though 7 can be tested on DRC's lumped orifice demand first, as `gen_flow.mjs` does.

### 3.3 Golden vectors: matching the JS to 1e-9

Reuse Aquafect's method exactly; its documented traps are the expensive part:

1. **Generator.** Write `tools/golden/drc/gen_<module>.mjs`, a copy of `aquafect/tools/golden/drc.mjs`. It takes the DRC checkout path as `argv[2]` or `$DRC_PATH`, imports `src/core.js` `defaultProject` and the module under test read-only, runs a scenario list, and writes `{source, drc_sha, node: process.version, scenarios:[{name, P, out}]}`.
   - Start from Aquafect's scenario lists (`gen_flow.mjs` `SCENARIOS`: simple and system; none, PRV and VFD; speed 60 and 100; clog 0/50/90; water 5/20/40 °C; 32/128/157 valves; 1- and 3-segment supply with `share:'half'`; lift ±; bypass; tilt and sag; cannot-lift; runout; dirty curves).
   - **Trim each to ≤200 KB.** Aquafect's equivalents are 0.7–2.6 MB each, about 59 MB in total, and this repo is public.
   - Record the DRC commit SHA; Aquafect records only the Node version.
2. **Python helper** (`tests/drc_golden.py`), the counterpart of `aquafect-core/tests/support/mod.rs`:
   - `close(got, want, rel)`, relative to the larger magnitude; NaN equals NaN; ±inf exact.
   - `REL = 1e-12` for closed-form math, `SOLVE = 1e-9` for bisection and regula-falsi results.
   - `close_f32`: **`manifoldSolve` returns `v` as a `Float32Array`**, so Python must round through `struct.pack('f')` (or `array('f')`) and compare within 1 ulp.
   - Decode `"NaN"`, `"Infinity"` and `"-Infinity"` strings.
3. **JS semantics to reproduce**, learned the hard way in `aquafect/docs/plan/01-hydro-port.md` "Decisions made while porting":
   - `Math.max`/`min` propagate NaN; Python's `max()` does not. Write `jmax`/`jmin`.
   - `x || d` treats 0, NaN and '' as missing. Write `or_(x, d)`, never `x or d` on floats (Python's `or` keeps NaN).
   - **Summation order:** `fitSumK` iterates `Object.entries` in insertion order. Keep fittings as an ordered list of pairs, never a sorted dict.
   - `toFixed` rounds ties away from zero, unlike Python's `round()` (banker's) and `f"{:.Nf}"`. This matters for `autoBypassMm` (`ceilTo`) and any `+x.toFixed()` in presets.
   - V8 `Math.pow` changed 10 `common.json` values by 1 ulp between Node versions. Pin Node in the generator header, and accept 1e-12, not equality.
   - Bisection loops (`for it < 60 && hi-lo >= 1e-6`) must use the *same* iteration caps and tolerances. Otherwise results agree only to the tolerance, not to 1e-9.
4. **Generalized code with no JS reference** (non-uniform manifolds, open terminals, the trajectory module): add a regression test that the generalization collapses to the DRC case. For example, a uniform takeoff list must equal `manifoldSolve` to 1e-12. Pair it with hand-calc goldens in the existing `test_water_engine.py` style.
5. **CI.** A new **pytest** suite must be added to a `python -m pytest` step in `ci.yml`, not to the unittest list (repo `CLAUDE.md`, "Bench-free suites split..."). CI needs no Node, because the JSON is committed. Regeneration is a manual step with the DRC checkout present.
6. **A cross-check against Aquafect** comes free: Aquafect's goldens come from the same JS, so a scenario in both is a three-way check (JS, Rust, Python).

---

## 4. Known errors and weaknesses found

| # | Where | Finding | Severity |
|---|---|---|---|
| E1 | Frappe `pump.select_pump`, `pipeline.run_spine` | No operating point. Velocity (`size_pipe`), VGB (`suction_outlet_vgb`) and pipe-pressure (`pipe_pressure_check`) checks all run at design flow, but an oversized pump delivers more | **High** (safety for VGB) |
| E2 | Frappe `pipeline.run_spine` | `npsh_available` and `water_hammer` exist but are never run on a design; they are desk and AI calculators only (`api/water_design.py`, `assistant_tools/water_calc.py`) | Medium |
| E3 | Frappe `pipeline.run_spine` pressure check | System psi is taken as `TDH/2.31`. The worst-case pipe pressure is pump **shutoff** head plus surge, which needs a curve | Medium |
| E4 | Frappe `pump.electrical_load`, `constants.BREAKER_CONTINUOUS_FACTOR`; DRC `electrical.circuitPlan` | 125% of FLA is labelled a breaker rule citing 430.52; it is the 430.22 conductor rule. `service_main_breaker` builds on it | Medium (decision D6) |
| E5 | DRC `common.PATM`, `flow.npshaM` | Sea-level atmosphere with no elevation input. Overstates NPSHa by about 1.5 m in Salt Lake City | Medium for any Utah port |
| E6 | DRC `presets.PUMPS` | Curves are invented "typical" shapes; `rankPumps` ranks fiction | High if ported as data |
| E7 | Frappe `feature.nozzle_array_flow` | Array flow ignores supply pressure and distribution | Medium |
| E8 | Frappe `feature.jet_trajectory` | `k × head`, vertical only; unsourced k; no drag or range | Low–Medium |
| E9 | Frappe `treatment.heating_load` | Volume-proportional heat loss (workbook form). Physically it is area-driven | Low (keep, add a governing calc) |
| E10 | Frappe `data/fittings.py` `FITTING_K` | Size-independent K overstates large-pipe losses | Low (conservative) |
| E11 | DRC `checks.basinChecks` splash | `vh = 0.2·vImp·0.6` is an unsourced heuristic | Low |
| E12 | Frappe `constants.FT_PER_PSI` | 2.31 while DOC-0049 F173 has 2.308966; the docstring says "not in source docs", which is stale | Low |
| E13 | `SOURCE_DATA_AUDIT.md` | Lists the <0.5 FPS settling band as open; it is done (`constants.SELF_CLEANING_MIN_FPS`, `pipe.STATUS_SETTLING`, `test_size_pipe_tiny_flow_falls_back_to_smallest_settling_size`) | Doc staleness |
| E14 | Aquafect `circulation.rs` | `MAKEUP_M3S`, `DRAIN_S`, `FLOAT_BELOW_M`, `SPIN_UP_S`, `RUN_DOWN_S` are self-labelled assumptions with no JS reference and no field data | Note before reuse |

---

## 5. Decisions that need a human

- **D1. Which engine is canonical?**
  - Aquafect's handoff (`docs/plan/05-rain-curtain-handoff.md` step 3) freezes the ERPNext water tools "until Aquafect replaces them" and plans a Rust network solve.
  - A Frappe Python rebuild duplicates that. The managed host cannot pip-install, so Frappe cannot easily call a Rust binary. The options are:
    - (a) Python in Frappe is the design engine, and Aquafect stays a simulator and renderer that reads the design JSON;
    - (b) Aquafect is the engine and Frappe stores its outputs;
    - (c) both, held to parity by shared goldens. This doubles maintenance.
  - Recommendation: (a). Re-scope Aquafect's v0.1 to consume the Frappe model.
- **D2. Units and pipe dimensions.**
  - Keep US customary at the boundary and SI inside the new solver (recommended)?
  - Which pipe ID table governs: DOC-0049 SUPPORT IDs (Sch40 2" = 2.067") or DRC's NPS OD-minus-2×wall (60.3 − 2×3.91 = 52.48 mm = 2.066")? They differ in the third decimal, and every golden depends on the choice.
- **D3. Conservative workbook tables or published physics?**
  - Hazen-Williams with fixed K (the workbook) or Darcy with Darby 3-K, as the *governing* TDH?
  - Recommended: Darcy governs; Hazen-Williams is shown as the DOC-0049 cross-check; flag when they differ by more than 10%. The same question applies to:
    - Manning half-full against DOC-0119 full-pipe (currently conservative by choice);
    - DRC's friction-gradient cap of 0.04 m/m on top of DOC-0049 velocity limits;
    - the 125% versus 430.52 breaker rule.
- **D4. Which pump catalog?**
  - Prod has 0 curve points, and 1 of 7 rated pumps has a TDH.
  - Pick the manufacturers and lines Sapphire actually buys (DOC-0048 lists Pentair WhisperFlo XF 5HP and SuperFlo VST 1.5HP; DOC-0028 has part numbers 500014/035/141/144/202).
  - Decide who digitizes the head, efficiency and NPSHr curves from cut sheets. DOC-0049 sheet 8's curves are images (`SOURCE_DATA_AUDIT.md`), so this is manual.
  - Without this, recommendation 1 produces numbers on fictional data.
- **D5. Standards edition and jurisdiction.**
  - NEC edition (2020 or 2023) and Article 680 scope for decorative fountains.
  - ANSI/APSP-16 edition; MAHC versus Utah R392-302 for regulated venues.
  - ANSI/HI 9.8 for intake submergence; ASHRAE 188 for the water-management program.
  - ASCE 7 floor live loads for rooftop features.
- **D6. Motor OCPD rule.** Keep the 125%-of-FLA "minimum breaker" (conservative for conductors, may nuisance-trip), or implement Table 430.52 maxima with the starter type?
- **D7. Safety-check flow basis.** Should VGB, velocity and pipe-pressure checks gate on the operating-point flow? Recommended: yes, the maximum of design and operating flow. Does shutoff head plus surge gate the pipe pressure rating?
- **D8. Network scope.** Is "one pump, N parallel terminals" enough for the first version, or do real Sapphire layouts include looped or multi-pump headers that need GGA? An answer from the designers decides whether module 8 is enough.
- **D9. Jet model calibration.** Who measures a few real installs (jet height and flow at a logged pressure)? This is Aquafect's own exit criterion: ±10% on three past installs. It would replace the unsourced `JET_EFFICIENCY` k values and validate the drag model.
- **D10. Data on assumptions.** Should Aquafect's assumed circulation constants (makeup flow, drain-back time) be replaced with Sapphire field defaults before any reuse?

---

## 6. Still-open items in `SOURCE_DATA_AUDIT.md`

Taken from its "Still open (recommended next)" list, plus the remaining ▶ and ◇ rows.

| Item | Status now | Still worth doing in the rebuild? |
|---|---|---|
| Pipe pressure as a per-segment status field | Warning plus audit card only (`pipeline.run_spine` → `pipe_pressure_check`) | **Yes**, and do it against shutoff head plus surge once the solver exists (E3). Small |
| Slot-channel circular-crest weir (DOC-0049 M, C=3.70) | Not coded; needs exact-cell verification | **Yes, low priority.** It is another terminal type in module 6; verify the cell first |
| Low-velocity (<0.5 FPS) settling band | **Done** (`pipe.velocity_status` → `STATUS_SETTLING`) | No. Strike it from the audit (E13) |
| Aggregated Fitting Schedule | Per-run only in print | **Yes.** Solved by port module 14 (`bom.py`) as engine output, not Jinja |
| O&M manual print format (DOC-0123) | Unblocked, not built | Yes, but it is a print-format task outside the engine. Add DRC's `maintenanceSchedule` and `waterPlan` content to it |
| Structural/concrete/rebar (DOC-0119 #5) | Parked | **Partly.** Port module 15 covers pipe supports and floor load. Concrete and rebar stay out of scope unless designers ask (deterministic tables, not calcs) |
| Catalog Item seeds (DOC-0028) | Not done | **Yes, and promote it.** It is the same work as D4: pumps with curves first; controllers, lights and enclosures next |
| `FT_PER_PSI` = 2.308966 | Not done (still 2.31) | **Yes**, in the rebuild. The new SI solver uses exact ρg anyway. Update the constant and refresh the touched goldens in the same PR |
| DOC-0049 #7 equivalent-length minor-loss cross-check | Not done | **No.** Darby 3-K supersedes it |
| DOC-0049 #9 Stenner feed-pump GPD table | Not done | No (niche). Keep it as catalog data if a chemistry submittal ever needs it |
| DOC-0119 #2 drain-capacity divergence note | Done (`test_manning_shows_full_pipe_divergence_basis`) | No |
| DOC-0119 #3 jet height → basin setback/freeboard | Advisory only (`jet_trajectory` steps) | **Yes.** Port module 16 (trajectory with drag plus wind drift) and module 10 (catch width) make it a real validation |
| DOC-0119 #7 design-package completeness checklist | `next_inputs_needed` only | Yes, cheap. Fold it into `run_spine` v2 (module 18) |
| DOC-0062/0028 controller_hardware, MAX=FCA003, enclosure and valve-channel items | Decisions pending | Not engine work. Leave them with the controls owner |

---

## Appendix: file and function index used

- **Frappe:**
  - `engine/envelope.py` `CalcResult`, `CalcOption`, `make_input`
  - `engine/pipeline.py` `run_spine`, `_feature_flow`
  - `engine/pipe.py` `pipe_velocity`, `velocity_status`, `hazen_williams_loss`, `size_pipe`, `pipe_pressure_rating`, `pipe_pressure_check`
  - `engine/tdh.py` `fitting_minor_loss`, `component_loss`, `segment_loss_results`, `total_dynamic_head`
  - `engine/pump.py` `head_at_flow`, `select_pump`, `electrical_load`
  - `engine/safety.py` `suction_outlet_vgb`, `npsh_available`, `water_hammer`
  - `engine/feature.py` `weir_flow`, `tiered_fountain_flow`, `nozzle_array_flow`, `nozzle_flow`, `jet_trajectory`
  - `engine/basin.py`, `engine/drainage.py`, `engine/aquatic.py`, `engine/chemistry.py`, `engine/treatment.py`, `engine/workbook.py`, `engine/controls.py`, `engine/electrical.py`, `engine/drawings.py`, `engine/constants.py`, `engine/data/*.py`
  - `tests/test_water_engine.py` (120 tests, 78 `assertAlmostEqual` goldens at 1e-1 to 1e-6 places)
- **DRC:**
  - `src/hydro/common.js`, `flow.js`, `manifold.js`, `transient.js`, `hammer.js`, `checks.js`, `presets.js`, `route.js`, `engine.js`, `live.js`, `analysis.js`
  - `src/electrical.js`, `src/upkeep.js`, `src/rigging.js`, `src/wizard.js` (`STEPS`, `openSetup`), `src/setupui.js` (`card`, preset tiles), `src/core.js` `physOf`
  - `tests/hydro/*.test.mjs`
- **Aquafect:**
  - `crates/aquafect-core/src/hydro/mod.rs` (port status table), `circulation.rs`, `pumpset.rs`, `src/site/{electrical,rigging,upkeep}.rs`
  - `tests/support/mod.rs` (`close`, `close_f32`, `walk`), `tests/golden_flow.rs` (`REL = 1e-12`, `SOLVE = 1e-9`)
  - `tools/golden/drc.mjs`, `gen_flow.mjs`
  - `docs/plan/01-hydro-port.md`, `05-rain-curtain-handoff.md`, `07-app-modules.md`
