# Fountain Designer rebuild: the plan

Written 2026-11-01 by the Sapphire Manager session from two research reports in this folder:
[`research-engine-audit.md`](research-engine-audit.md) (what the three hydraulics codebases do, what to
port) and [`research-industry-calculators.md`](research-industry-calculators.md) (what the trade
calculates per feature family, the automation ladder, catalog data). Read both before building a
phase. The catalog data itself is being digitized into [`catalog/`](catalog/) by a third session.

ERPNext tracker: PRJ-00580, group task **TASK-2026-03018 "Fountain Designer rebuild"**. Vault:
`projects/fountain-designer-rebuild.md`, decision `decisions/2026-11-01-fountain-designer-rebuild.md`.

## 1. What Nikolas decided (2026-11-01)

1. **Two modes, two audiences.** *Quick* for sales: a kit, a few inputs, an auto-sized system and a
   budget. *Full* for the design team: every section open, equipment picks, schedules, BOM to a
   Quotation or Project, submittal packet.
2. **New guided page, engine kept.** A single-page designer inside ERPNext, step by step like the
   Digital Rain Curtain's guided setup, with live results. A simpler storage schema replaces most of
   the 25 DocTypes. The pure engine stays stdlib-only and is extended. The one existing design record
   (WFD-2026-209315) is dropped.
3. **All venues:** decorative fountains (all 18 types), interactive and show features, commercial
   pools and spas (the Fika regulated path stays), rentals, ponds and aeration.
4. **Catalogs from both sources:** digitized public manufacturer data (cited per row, approved by
   Nikolas before seeding) and the designers' own entries.
5. **Python in ERPNext is the canonical engine.** Aquafect becomes the simulator and renderer that
   reads the design JSON. Its "ERPNext water tools frozen until Aquafect replaces them" is superseded.
6. **Catalog lines first:** Pentair commercial (WhisperFlo XF, SuperFlo VST, EQ), Sta-Rite / Hayward /
   Jandy, industrial end-suction (Goulds, Grundfos, Wilo, Franklin).
7. **Multi-pump headers and ring mains are in version one** (a junction-balancing network solver).
8. **Physics governs:** Darcy-Weisbach with Darby 3-K fittings is the governing loss; the DOC-0049
   Hazen-Williams figure is shown beside it as the workbook cross-check, and a difference over 10%
   raises a `DesignIssue`. Safety checks (VGB, velocity, pipe pressure) run at the real operating flow
   (the larger of design and operating). NEC 430.52 / Table 430.52 breaker maxima replace the
   125%-of-FLA "breaker" rule (125% stays as the 430.22 conductor rule).

## 2. The product

### 2.1 The flow (both modes share it)

```
Start ─ pick a kit (or "blank"), name the design, link Project / Opportunity / Customer
Site  ─ address → elevation, air/water design temps, frost depth, hardness, utility rates,
        jurisdiction (regulated? Utah IWF?), venue type
Feature(s) ─ family + type, principal dimensions, the desired effect (sheet look / jet height /
        tier look / acres), nozzle or sheet-fall product (or "generic, pick later")
Basin(s) ─ shape, dims, depth, which features each basin catches; freeboard
Pump room ─ pump location (vault / room / submersible), distance and elevation to the feature,
        route template (per family), material, control (none / VFD / PRV)
Derive ─ the engine runs the automation ladder; every section marked `auto` is filled
Review ─ verdict (good / warn / bad with remedies), rollups, duty-point chart, schematic,
        acknowledgements for warnings a designer accepts
Outputs ─ BOM + budget → Quotation / Material Request; schedules; packet; O&M per year;
        winterization + start-up checklist; plan-check sheet (regulated)
```

Quick mode = the same page with a kit chosen and every section `auto`; it shows the verdict, the
headline numbers and the budget. Full mode exposes each section's rows and lets any section be set to
`custom`. Any design can move between modes.

### 2.2 The automation ladder (what derives from what)

Fixed order; each step reads only from the steps above it, so a change upstream re-derives everything
below without clobbering a `custom` section (the rain curtain's `deriveSetup` pattern):

| # | Section | Derived from | Engine |
|---|---|---|---|
| 0 | site | address, feature type | geocode → elevation; Utah lookups; `is_regulated` extended to interactive features |
| 1 | basin volume, surface, weight | dims | `basin.basin_volume` (kept) |
| 2 | feature flow | effect + dims | weir look → gpm/ft + tolerance; jets: height → nozzle table → gpm + head; cascades: GPH/ft look bands; splash pad: worst sequence step; aerators: HP/acre |
| 3 | code minimum flow and tank | regulated + volume | `aquatic.*` (kept) + Utah IWF rules (30-min turnover, 3-min tank, 20 ft/s) |
| 4 | setback and wind | heights, basin plan | splash-radius ratios → pass/fail; wind-control stages |
| 5 | basin and surge | flows + routes | water in transit + drain-back + HI 9.8 submergence + freeboard → depth / trough / reservoir; autofill from evaporation + spray loss |
| 6 | routes and pipe sizes | pump location + geometry | per-family route template → lengths and fittings; `pick_pipe` (DOC-0049 velocity limits govern, plus a friction-gradient cap); manifold sized for ≤2% spread |
| 7 | operating point and pump | network + terminals | Darcy network solve; `rank_pumps` over real curves with pass/fail reasons; NPSH at site elevation; BEP window; shutoff head + surge vs pipe rating |
| 8 | control, bypass, surge | pump vs system | none / VFD / PRV; minimum-flow bypass; arrestor when Joukowsky + Michaud surge exceeds rating |
| 9 | treatment | circulation + volume | `treatment.*`, `chemistry.*` (kept); UV for interactive; filter clean-vs-dirty operating point |
| 10 | lighting and electrical | effects + pumps | fixtures per effect, beam, VA × 1.25, NEC 680 GFCI; FLC, 430.22 conductors, 430.52 OCPD, wire gauge + voltage drop, conduit fill, feeder and service, low-voltage run drop (phase L) |
| 10b | structure | basins + site | slab and wall thickness from the standard details, rebar schedule, concrete, gravel, excavation, waterproofing quantities, hydrostatic and uplift checks (phase S) |
| 11 | winterization | routes + site | drain points, frost depth, pulled nozzles and lights |
| 12 | outputs | everything | BOM (hydraulic, electrical and structural lines), budget, O&M, schedules, charts, checklists |

### 2.3 Sapphire house rules

Rules with no public source (foam-jet submergence, freeboard multiplier, watts per foot of jet, wind
setpoints, liner allowance, spray-loss factor, labor hours per family) live as named, editable
constants on **Water Engineering Settings** (a Single: ship a backfill patch; see the Singles gotcha in
`CLAUDE.md`). Each carries a citation "Sapphire standard, <date>". The engine reads them as a dict; the
page shows them under *House rules*.

## 3. Architecture

### 3.1 Engine (`erpnext_enhancements/water_engineering/engine/`, stdlib-only, never imports frappe)

Keep: `envelope.CalcResult` (value, unit, inputs with sources, formula, steps, citations, warnings,
options), every workbook-golden function and its tests (they become cross-checks), `issues.py` as the
single producer of typed `DesignIssue`s.

Add:

| Package | Contents | Source |
|---|---|---|
| `engine/hydraulics/` | SI inside, US at the envelope boundary with exact constants. `water.py` (water(T), Churchill friction factor, wave speed), `catalog.py` (materials, sizes, pipe spec; reconciled with `data/pipe_specs.py`, DOC-0049 IDs govern), `fittings.py` (Darby 3-K + `COMPONENT_CURVES` + clog factor), `segment.py`, `pump.py` (curve interpolation, efficiency, NPSHr, BEP, affinity), `terminals.py` (`Demand` protocol: orifice, manifold, open discharge, fixed flow, parallel), `operating_point.py` (pump meets system; VFD / PRV / bypass; NPSHa with altitude), `manifold.py` (per-takeoff solve, size sweep), `network.py` (junction balancing for multi-pump headers and ring mains; one pump + N parallel terminals is the degenerate case and must match `operating_point` to 1e-9), `surge.py` (Joukowsky + Michaud, Korteweg wave speed, accumulator), `basin_checks.py` (HI 9.8, drain-back, in-flight, spray loss, Carrier evaporation) | DRC `src/hydro/*.js`, ported under golden vectors (audit §3) |
| `engine/autosize.py` | `pick_pipe`, `rank_pumps` / `eval_pump` with `fails[]`, `auto_bypass`, `auto_surge`, `auto_basin`, and `derive(model, setup)` which runs the ladder in §2.2 and writes only into `auto` sections | DRC `presets.js` pattern |
| `engine/families/` | one module per feature family with `inputs()`, `flow()`, `checks()`: `weir.py` (look bands, edge tolerance → head, catch width, trough surge), `jets.py` (nozzle tables, manifold spread, 2-D trajectory with drag, splash radius), `foam.py` (level band, check valves), `laminar.py` (ballistic arc), `interactive.py` (Utah R392-302-41 ruleset, sequence-based pump sizing, 3-min tank, UV), `cascade.py` (GPH/ft look bands, tier governs, drain-back), `pondless.py` (reservoir multipliers, kit BOM), `curtain.py` (passive strand/screen flow; the digital curtain links to the DRC app), `mist.py`, `aerator.py` (HP/acre, pattern vs pond, cable drop), `lighting.py` (fixtures per effect, beam, VA, GFCI), `winterization.py` (checklist), `heat.py` (surface heat balance; `treatment.heating_load` stays as the DOC-0049 figure) | industry report §1 |
| `engine/electrical/` (phase L) | the existing `electrical.py` functions kept, plus conductors, voltage drop, conduit fill, OCPD per 430.52, feeder and service, transformers, NEC 680 checklist, VFD, backup, energy, wire BOM, panel schedule rows | DRC `electrical.js` + NEC |
| `engine/structural/` (phase S) | basin slab and wall thickness from the standard details, rebar schedule, concrete, gravel, excavation, formwork, waterproofing quantities, hydrostatic and uplift checks | DOC-0119 standard details, ACI 318/350 as aids |
| `engine/bom.py` | `bom_rows` (pipe by size/material with 10% waste, fittings grouped, equipment, consumables, kit lines), `cut_list` (20 ft sticks), `fitting_counts`; labor assemblies from house rules | DRC `checks.bomRows`, `route.cutList` |
| `engine/upkeep.py` | `water_plan` (cycles of concentration, Legionella band, drain/refill days), `maintenance_schedule`, `om_per_year` (water, power, chemicals, parts at the Settings rates) | DRC `upkeep.js` |
| `engine/structure.py` | `beam_reactions`, pipe hanger loads from `wet_lb_ft`, span check, basin floor load on a slab (phase B) | DRC `rigging.js` |
| `engine/kits.py` | per-family kits: a bundle of presets + setup policy (e.g. "Hotel lobby sheet wall 8 ft") | DRC `KITS` |
| `engine/model.py` | the design model dataclasses + `validate(model)` + JSON (de)serialization; schema version 2 | new |

Golden vectors: `tools/golden/drc/gen_<module>.mjs` (Node, reads the DRC checkout read-only, writes
JSON ≤ 200 KB per module, records the DRC SHA and Node version) and `tests/golden/drc/*.json`
committed; `tests/drc_golden.py` helper (`close(rel)`, NaN/±Inf decoding, float32 compare for
`manifoldSolve`). Match 1e-12 for closed-form math, 1e-9 for iterative solvers. Reproduce JS semantics
(`jmax`/`jmin`, `or_`, insertion-order sums, `toFixed` ties, same bisection caps). A generalization
with no JS reference must collapse to the DRC case and carry hand goldens. Every new bench-free suite
is **pytest** and gets its own `python -m pytest` step in `ci.yml` (never appended to the unittest
list; see `CLAUDE.md`).

### 3.2 Storage

One parent DocType keeps the name **Water Feature Design** (so the AI tools, the packet and the
workspace keep their routes) but is rebuilt:

| Field | Type | Purpose |
|---|---|---|
| `design_title`, `project`, `opportunity`, `customer`, `serial_no` | Links / Data | identity (as today) |
| `mode` | Select Quick / Full | §2.1 |
| `kit`, `venue_type`, `family` | Data / Select | the starting kit and the primary family |
| `status` | Select Draft / Derived / Reviewed / Issued | submittable stays (Issued = submitted) |
| `model_json` | JSON | the whole design model (§3.3), the source of truth |
| `results_json` | JSON | the last `derive` output: CalcResults, rollups, charts |
| `issues_json`, `readiness_json` | JSON | typed issues and readiness (as today) |
| `bom` | Table → **Water Feature Design Line** (new child: `section`, `item`, `description`, `qty`, `uom`, `rate`, `amount`, `source` Auto / Manual, `catalog_source`) | the only child table; it is what pushes to a Quotation and what reports read |
| rollups | Float / Int / Link | `design_flow_gpm`, `operating_flow_gpm`, `tdh_ft`, `operating_head_ft`, `selected_pump`, `pump_kw`, `total_basin_gallons`, `budget_total`, `om_cost_per_year`, `blocker_count`, `warning_count`, `completion_percent`, `issue_ready` |
| `issue_acks` | Table → Water Design Issue Ack (kept) | acknowledgements |
| packet fields | as today | `issue_date`, `drawn_by`, `packet_document`, `revisions`, `packet_html` |

Catalog DocTypes, extended: **Nozzle Profile** gains `nozzle_type`, `connection_size`,
`level_sensitive`, min/max depth, recommended beam, provenance fields and a child **Nozzle
Performance Point** (`head_ft`, `psi`, `flow_gpm`, `display_height_ft`, `display_diameter_ft`,
`throw_ft`, `depth_over_nozzle_in`). New **Pump Curve** (parent: `item`, `impeller_dia_in`, `rpm`,
`speed_pct`, `max_flow_gpm`, `shutoff_head_ft`, `source` datasheet / digitized / rep-supplied /
generic estimate, `source_url`, `verified_by`, `verified_on`) with the existing **Pump Curve Point**
extended (`efficiency_pct`, `npshr_ft`, `power_bhp`). Item custom fields gain provenance
(`custom_spec_source_url`, `custom_spec_revision`, `custom_spec_verified_by/on`) and the light, filter
and aerator fields from the industry report §4.3. A `generic estimate` flag on every catalog record
lets a design size before a model is chosen, and `issues.py` warns when a generic record reaches a
submittal.

Kept as they are: Control Panel Design and its five children, Standard Note, Standard Detail,
Submittal Revision, Water Engineering Settings (gains the house rules), Water Design Issue Ack.

Retired (JSON model replaces them): Water Feature Basin, Water Feature Tier, Water Feature Nozzle,
Water Feature Pipe Segment, Water Feature Pump, Water Feature Electrical Load, Water Venue Fixture,
Water Feature Equipment, Water Feature Calc Result. Retirement is two steps (remove the JSON **and** a
patch calling `frappe.delete_doc`, per `fixtures/README.md`), lands in the last phase, and the patch
must never raise (a raising patch aborts `bench migrate`, which is the deploy).

### 3.3 The design model (schema 2, `model_json`)

```jsonc
{
  "schema": 2, "mode": "quick|full", "kit": "lobby-sheet-wall-8ft|null",
  "site": {"address": "", "lat": 0, "lng": 0, "elevation_ft": 4300, "air_temp_f": 95, "water_temp_f": 70,
           "frost_depth_in": 30, "hardness_ppm": 250, "kwh_rate": 0.105, "water_rate_per_ccf": 3.53,
           "jurisdiction": "UT", "venue_type": "Decorative Fountain", "regulated": false},
  "setup": {"features": "custom", "basin": "auto", "piping": "auto", "pump": "auto", "control": "auto",
            "bypass": "auto", "surge": "auto", "treatment": "auto", "lighting": "auto",
            "electrical": "auto", "winterization": "auto"},
  "features": [{"id": "f1", "family": "weir", "type": "Waterwall (sheet)", "label": "Lobby wall",
                "inputs": {"length_ft": 8, "fall_ft": 10, "lip": "square", "tolerance_in": 0.0625},
                "effect": {"look": "average"}, "product": {"item": null, "generic": true},
                "count": 1, "basin": "b1", "node": "t1"}],
  "basins": [{"id": "b1", "label": "Trough", "shape": "rectangular", "length_in": 120, "width_in": 24,
              "depth_in": 14, "freeboard_in": null, "volume_gal_override": null, "elevation_ft": 0}],
  "network": {
    "nodes": [{"id": "src", "kind": "basin", "basin": "b1", "elevation_ft": 0},
              {"id": "p1", "kind": "pump"}, {"id": "j1", "kind": "junction", "elevation_ft": 1},
              {"id": "t1", "kind": "terminal", "feature": "f1", "elevation_ft": 10}],
    "pumps": [{"id": "p1", "item": null, "curve": null, "speed_pct": 100, "control": "none|vfd|prv",
               "setpoint_psi": null, "generic_class": "end-suction"}],
    "segments": [{"id": "s1", "from": "src", "to": "p1", "line": "suction", "material": "SCH40 PVC",
                  "nominal_size": "3\"", "length_ft": 8, "fittings": [{"key": "elbow_90", "qty": 2}],
                  "components": []},
                 {"id": "s2", "from": "p1", "to": "t1", "line": "discharge", "material": "SCH40 PVC",
                  "nominal_size": null, "length_ft": 45, "fittings": [], "components": [{"key": "filter_ccp520"}]}],
    "terminals": [{"id": "t1", "kind": "open_discharge|orifice|manifold|fixed_flow", "feature": "f1",
                   "params": {}}]
  },
  "equipment": [{"slot": "filter", "item": null, "qty": 1, "auto": true}],
  "treatment": {"sanitizer": "chlorine", "uv": false}, "lighting": {"fixtures": [], "control": "switched"},
  "electrical": {"service_v": 240, "phase": 1, "panel_distance_ft": 60},
  "acks": [], "notes": ""
}
```

`nominal_size: null` on a segment means "size me" when `setup.piping` is `auto`. The engine never
mutates a `custom` section; it returns a `derived` copy of the model with `auto` sections filled, so
the page shows what it chose and the designer can pin any of it as `custom`.

### 3.4 API (`water_engineering/api/designer.py`, whitelisted, 4-space like `api/`)

| Endpoint | Does |
|---|---|
| `boot()` | kits, families and their input schemas, catalog lists (pumps with curves, nozzles, lights, filters, materials, fittings), house rules, units, permission flags |
| `derive(model_json)` | stateless: `{model (derived), results, rollups, issues, readiness, verdict, bom, charts: {duty_svg, schematic_svg, oneline_svg}}`. The page calls it debounced on every edit; the controller calls the same function on save |
| `get_design(name)` / `save_design(name, model_json)` | load / save; save re-derives server-side and writes rollups, `bom` rows and JSON fields |
| `push_quotation(name, quotation=None)` / `push_material_request(name)` | BOM lines → ERPNext documents (Items only; a line without an Item becomes a text line) |
| `catalog_search(equipment_class, q)` | typeahead over Items with the class fields |
| `acknowledge_issue(design, issue_key, note)` | kept |
| `run_calc(calc, inputs)` | the stateless calculator stays (wizard and AI tool) |

The AI tools (`assistant_tools/water_calc.py`, `save_water_design.py`, `water_design_status.py`) move
to this API in the last phase; `save_water_design` takes a model-2 payload.

### 3.5 The page (`water_engineering/page/fountain_designer/`)

Vanilla JS on the app's kits, like every other custom page here: `sf_kit` (states, calls, chips,
toasts), `form_kit` step and panel pieces where they fit, `sf_tokens` for theme (light and dark),
esbuild bundles (never raw `/assets` paths). No framework; the vendored Vue is not used. Layout: a
left step rail (Start, Site, Features, Basins, Pump room, Review, Outputs), a center stage for the
current step, a right verdict panel that is always visible (chips, top issues, rollups, duty-point
chart). Phone width works (the step rail collapses to a top bar). Back/Forward must keep working
(route per step). Every load has loading, empty, error and no-access states. The old Water
Engineering Wizard page is retired in the last phase; its quick calculator becomes a drawer on the
new page.

## 4. Phases, sessions and PRs

Each phase is one session on Opus 5.5 at high effort in its own worktree, one **draft** PR per phase
(stacked where noted), version bump + CHANGELOG per PR (MINOR for a phase), its own ERPNext task
under TASK-2026-03018 with a comment at each milestone. A Sonnet 5.5 (high) review session reads each
PR before the manager merges. Sessions never merge and never run `gh pr ready`. The manager merges one
at a time about four minutes apart and verifies each deploy.

| Phase | Scope | Files it owns | Depends on |
|---|---|---|---|
| **A** engine hydraulics core | audit modules 0–7: golden infrastructure, water, catalog, fittings, segment, pump curves, terminals, operating point; `FT_PER_PSI` 2.308966 with golden refresh | `engine/hydraulics/{__init__,water,catalog,fittings,segment,pump,terminals,operating_point}.py`, `tools/golden/drc/`, `tests/golden/drc/`, `tests/drc_golden.py`, `tests/test_hydraulics_*.py`, `ci.yml` step, `engine/constants.py` (FT_PER_PSI only), `engine/data/pipe_specs.py` (reconcile) | — |
| **B** engine hydraulics network | manifold, **network solver** (multi-pump headers, ring mains), surge, basin checks, autosize (`pick_pipe`, `rank_pumps`, auto bypass/surge/basin), bom, upkeep, pipe supports (`structure.py`: hanger loads, spans) | `engine/hydraulics/{manifold,network,surge,basin_checks}.py`, `engine/{autosize,bom,upkeep,structure}.py`, their tests | A (stack on A's branch) |
| **C** feature families | `engine/families/*` per §3.1, `engine/kits.py`, house-rule constants with their Settings fields + backfill patch, `engine/model.py` (dataclasses + validate + JSON), tests with hand goldens from the industry report's sourced rules | `engine/families/`, `engine/kits.py`, `engine/model.py`, `water_engineering_settings` (fields + patch), `tests/test_families_*.py` | — (parallel with A; `jets.py` trajectory uses its own drag math and must not import `hydraulics`) |
| **D** catalog schema + seeds | Pump Curve DocType, Pump Curve Point and Nozzle Profile extensions, Nozzle Performance Point, Item provenance / light / filter / aerator fields, a seed loader that reads `docs/fountain-designer/catalog/*.json` (only rows Nikolas approved; `after_migrate` idempotent, guarded with `has_column`), a Desk catalog editor (curve points grid + chart on Item / Nozzle Profile), `pump_curve_chart.js` reads the new shape | `water_engineering/doctype/{pump_curve,pump_curve_point,nozzle_profile,nozzle_performance_point}/`, `setup.py` (fields), `patches/`, `public/js/water_engineering/pump_curve_chart.js`, `tests/test_water_catalog.py` | — (parallel) |
| **E** storage + derive + API | Water Feature Design v2 (fields in §3.2), Water Feature Design Line, controller (`validate` → `derive`, rollups, bom rows, issues), `api/designer.py`, `derive()` orchestration wiring A–D together, `issues.py` producers for every new check and the verdict rendering, `run_spine` v2 kept as a thin adapter, charts (duty-point SVG in `drawings.py`), Quotation / Material Request push | `doctype/water_feature_design/` (json, py; js minimal), `doctype/water_feature_design_line/`, `api/designer.py`, `issues.py`, `engine/drawings.py` (duty chart), `pipeline.py`, tests | A, B, C, D merged |
| **F** the designer page | §3.5, built against the API contract in §3.4 from day one (a stub `derive` that returns a fixed fixture lets F start before E merges; F swaps to the real endpoint when E lands), quick and full modes, kits, live derive, verdict panel, duty chart, BOM panel, outputs drawer, workspace + Dock entries | `page/fountain_designer/`, `public/js/fountain_designer/` (+ bundle), `public/css/fountain_designer.bundle.*`, `workspace/`, `hooks.py` (bundle + page lines), page tests | contract only; integrates after E |
| **G** outputs | schedules v2 (Equipment, Piping, aggregated Fitting Schedule from `bom.py`), duty-point and schematic on paper, O&M per year, winterization + start-up checklist, Design Calculations with the Utah IWF and NEC 680 checklists, packet adaptation to model 2, budget from Item prices + labor assemblies | `setup_print_formats.py`, `packet.py`, templates, `engine/bom.py` (labor), tests | E |
| **L** electrical | Nikolas 2026-11-01: electrical as a first-class deliverable. `engine/electrical/` package: loads from the design (pumps 1-phase and 3-phase, lights, controls, heaters, UV, chemical feed, valves, autofill) with NEC FLC tables; conductor sizing (ampacity, 125% continuous per 430.22, temperature and conduit-fill corrections); voltage drop over the run to the panel (3% branch, 5% total) and low-voltage runs (12 V lights, 24 V valve coils); OCPD per 430.52 / Table 430.52 by starter type; feeder and service (430.24, 430.62/63, 220); conduit sizing by fill (Chapter 9 Tables 1, 4, 5); control and lighting transformer VA; NEC 680 Part V (fountains) and Part II (pools) checklist: GFCI/GFPE, equipotential bonding, disconnect location, luminaire voltage and depth, clearances; VFD selection; generator and battery backup sizing; annual energy and cost (`workbook.electric_cost` stays); wire and conduit BOM lines; the panel schedule rows the existing Control Panel Design can import; the one-line in `drawings.py` extended | `engine/electrical/` (the existing `engine/electrical.py` becomes `engine/electrical/__init__.py` with its functions kept), `engine/data/nec_tables.py`, `engine/drawings.py` (one-line only), tests | — (parallel; reads pump kW and FLA from the model, never from `hydraulics`) |
| **S** structural | Nikolas 2026-11-01: how much rebar and concrete a basin of a given size needs. `engine/structural/` package: basin geometry (rectangular, cylindrical, freeform by area and perimeter, stepped depths, raised vs in-grade walls); slab and wall thickness from Sapphire's standard details (DOC-0119: 2" cover, 4 to 6" stem walls, #4 at 18" o.c., wall thickness to spacing 10" → 12" and 8" → 16", 12" gravel base) with hydrostatic and soil checks as engineering aids (wall pressure 62.4 × h, uplift when a water table is given, bearing); rebar schedule (bars by size, spacing, each way, laps at 40 × db, dowels, corner bars; total length and weight, 0.668 lb/ft for #4), concrete volume by element with 10% waste in cubic yards and truck loads, gravel base, excavation and backfill volumes, formwork area, waterproofing and coating area, expansion and construction joints, anchors for equipment; a quantity takeoff that feeds `bom.py` and the budget; every result says "engineering aid, not a stamped design" in its warnings | `engine/structural/`, `engine/data/rebar.py`, tests, a `README.md` that maps each rule to DOC-0119 or ACI 318/350 | — (parallel; reads basins from the model) |
| **H** retire and re-point | delete the nine retired DocTypes (JSON + `delete_doc` patch), retire the wizard page, re-point the three AI tools to `api/designer.py`, README / `SOURCE_DATA_AUDIT.md` / `hooks.py` annotations, ADR | the retired folders, `assistant_tools/`, docs | E, F, G |

Start now, in parallel: **A, C, D, L, S**, and **F against the contract**. B starts when A's first PR is
up (stacked). E starts when A, B, C, D, L and S are merged. G after E. H last.

Suggested order of merges: D (independent, small), C, L, S, A, B, F (page against stub), E, G, H.

## 5. Conventions every phase follows

- `engine/` never imports frappe. US customary at the envelope boundary; SI inside `hydraulics/`.
- Every public engine function returns a `CalcResult` with formula, steps, citations and warnings.
  A rule with no public source cites "Sapphire standard, <date>" and reads from Settings.
- Match the file's indentation (`engine/`, `issues.py`, `api/` are 4 spaces; doctype controllers and
  `setup*.py` are tabs). Never normalize a file you only touch.
- Bench-free pytest suites get their own `ci.yml` step. Aggregates never as SQL strings in `get_all`.
- Defensive hooks: `getattr(doc, "field", None) or ""` and `has_column` guards stay.
- A new field on a Single needs a backfill patch; on a normal doctype the default reaches every row.
- Draft PR, version + CHANGELOG, an ERPNext task comment at each milestone, a line in the vault journal.
- American English in every label ("Canceled", "color").

## 6. Open items for the designers (Logan, Nathan)

Collected for one conversation, none blocks a phase: the exact pump models Sapphire buys; the house
rules in §2.3; whether sequenced shows with several pumps on one header are common (they are in scope
either way); three past installs with logged jet height, flow and pressure to calibrate the jet model
(audit D9); the NEC edition Utah enforces; MAHC 2023 and R392-302-35(8) wording for interactive venues.
