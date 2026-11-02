# `water_engineering/` — fountain hydraulic design

The largest module in the app (~6.5k LOC). It turns Sapphire's two design workbooks —
**DOC-0048** (basin) and **DOC-0049** (the hydraulic spine) — into a calculation engine, a
submittable **Water Feature Design** document, a desk wizard, and a set of print formats.

Every function is documented inline, and each one names the workbook sheet and cell its
formula was verified against. This README is the map.

> **Indentation is mixed.** `engine/`, `issues.py`, and `api/water_design.py` use
> **4 spaces**; the doctype controllers and `setup*.py` use **tabs**. Match the file you are
> editing.

## The one architectural rule

**`engine/` may never import `frappe`.** It is stdlib-only, and that is enforced by
convention and by the tests. Two consequences make it worth protecting:

- The engine is unit-testable without a bench, which is why the water suites run in CI at
  all (`tests/test_water_engine.py`, `test_water_design_issues.py`,
  `test_water_design_controller.py`).
- The desk endpoints and the Frappe Assistant Core MCP tools call the **same** functions, so
  a designer working in the form and an AI answering a question produce byte-identical math.

Anything needing `frappe` goes in `api/`, `issues.py`, or a doctype controller — never in
`engine/`.

## The result envelope

Every public engine function returns a `CalcResult` (`engine/envelope.py`) rather than a
bare number: the headline `value`/`unit`, the `inputs` it used and where each came from, the
`formula`, ordered `steps`, source `citations`, `warnings`, and any A/B/C `options` the user
must still choose.

That shape is the product, not decoration. It is what lets the wizard and the AI show their
work, and what makes a design's `calc_results` an audit trail rather than a cache.

## Calculation engine — `engine/`

| File | What it computes | Verified against |
|---|---|---|
| `pipeline.py` | `run_spine(inputs)` — chains the whole Phase-1 spine, rolls up headline numbers, and reports `next_inputs_needed` so the wizard and the AI know what to ask next. Tolerant of partial input | — (orchestration) |
| `basin.py` | Basin geometry → volume & weight; turnover → circulation GPM | DOC-0048 `Basin` |
| `feature.py` | Feature flow: weirs/slots (Francis), nozzle arrays, orifice nozzles from the Nozzle Profile catalog | DOC-0049 `I - Weir` |
| `pipe.py` | Velocity, velocity-status banding, Hazen-Williams friction loss, and a size-walker that picks the smallest pipe within limits | DOC-0049 `A - Pipe Size` |
| `tdh.py` | Total Dynamic Head: minor (fitting) loss, component loss, per-segment sum | DOC-0049 `H - TDH` |
| `pump.py` | Pump selection by catalog match, plus the pump's branch breaker (`electrical_load`: the NEC 430.52 maximum since v1.763.0, with the 430.22 125% conductor figure beside it) | DOC-0049 + engineering standard (see below) |
| `electrical/` | The electrical design (phase L, v1.763.0): load list from the design model, branch circuits (430.52 breakers, 310.16 conductors with 310.15 corrections, 250.122 EGC, voltage drop, conduit fill), feeder and service (430.24, 430.62/63), 12 V lighting and 24 V coil runs, lighting and control transformers, the Article 680 checklist, VFD, generator and battery, annual energy, BOM lines and the one-line. `electrical_report(model)` runs the chain. Module-by-article map in [`engine/electrical/README.md`](engine/electrical/README.md) | NEC 2023 tables; Digital Rain Curtain `electrical.js` (parity tests) |
| `safety.py` | VGB / ANSI-APSP-16 suction-outlet anti-entrapment, NPSH cavitation check, Joukowsky water hammer | DOC-0049 `P - Suction Outlets`; HI standards |
| `drainage.py` | Gravity drainage (Manning's) and surge-basin sizing (Phase 3) | DOC-0049 `10 - Gravity`, `G - Gravity`, `B - Surge Basin` |
| `chemistry.py` | Chlorinator feed and chemical rate advisory (Phase 2) | DOC-0049 `C - Chemicals`, DOC-0119 |
| `treatment.py` | LSI, ASHRAE evaporation, make-up water, heating load, chemical dose, UV dose, filtration area | DOC-0049 `O` sheet + standards |
| `controls/` | Control-panel sizing: lighting relays, currents (Phase 4, the package's `__init__.py`), and since v1.764.0 the control design: per-pump topology (starter, soft starter, VFD, VFD with manual bypass and its interlock logic), drive frames with altitude and ambient derates, Splash Wizard selection with the I/O list, relays and 24 V DC supply, panel design (control transformer, enclosure, heat, SCCR, disconnect), panel BOM, Control Panel Design rows, Theory of Operation, power schematic and control ladder. `controls_report(model)` runs it. See [`engine/controls/README.md`](engine/controls/README.md) | DOC-0126, DOC-0028, DOC-0062, NEMA ICS 2, NEC 430, UL 508A SB |
| `workbook.py` | Electric cost, vertical pipe, open-channel flow, lazy-river horsepower, programmatic planning | DOC-0049 `E`, `K`, `J`, `L`, `D` |
| `structural/` | Basin and vault structure (Fountain Designer phase S): slab and wall thickness from the standard details, rebar schedule, concrete, gravel, excavation, formwork, joints, waterproofing, penetrations, anchors, and hydrostatic, uplift, bearing and floor-load checks as engineering aids. `structural_takeoff(model, rules)` returns one dict with quantities, checks and BOM lines. Its [`README.md`](engine/structural/README.md) lists every DOC-0119 rule and the gaps filled from ACI 318/350 | DOC-0119 `Concrete Design`; ACI 318/350 where DOC-0119 is silent |
| `envelope.py` | `CalcResult` — the standard return envelope | — |
| `families/` | Fountain Designer (schema 2): one rule module per feature family (weir, jets, foam, laminar, interactive, cascade, pondless, curtain, mist, aerator) plus the lighting, winterization and heat system modules. See [`engine/families/README.md`](engine/families/README.md) | industry report section 1, per-rule URLs |
| `model.py` | The schema-2 design model: dataclasses, lossless JSON round-trip, `validate`, and `migrate` from the old child-table shape | `docs/fountain-designer/PLAN.md` section 3.3 |
| `kits.py` | Whole-design starting points (feature presets + a setup policy per section); `apply_kit`, `match_kit` | — (data) |
| `rules.py` | The Sapphire house rules and utility rates as a plain dict (`DEFAULTS`, `resolve`); the same values are fields on Water Engineering Settings | "Sapphire standard" placeholders; SLC and RMP rates |
| `constants.py` | Physical constants, read from the workbooks' formula cells with openpyxl rather than a textbook | the workbooks |
| `units.py` | Unit conversions | — |
| `data/pipe_specs.py` | Pipe ID + velocity-limit table | DOC-0049 `SUPPORT` → `PipeType` |
| `data/fittings.py` | Minor-loss K-factors and component head-loss coefficients | DOC-0049 `H - TDH` |
| `data/drainage.py` | Gravity-drain pipe table (ID + Manning's n) | DOC-0049 `SUPPORT` → `GravityPipes` |
| `data/rebar.py` | Rebar diameters, areas and weights (#3 to #8) and stock lengths | ASTM A615 Table 1 |
| `data/chemistry.py` | Ozone contact-tank catalog and water-balance target ranges | DOC-0049 `SUPPORT` → `ContactTanks`, DOC-0119 |
| `data/nec_tables.py` | NEC tables as data: 310.16 ampacity, 310.15 corrections, 250.122, Chapter 9 Tables 1/4/5/8, 240.6(A), 430.248/430.250 FLC, 430.52(C)(1), 430.7(B), 450.3(B), Article 680 distances | NFPA 70 2023 (2020 numbering noted) |
| `hydraulics/` | The governing solver (Fountain Designer rebuild, phases A and B): water properties, Darcy-Weisbach + Churchill friction, Darby 3-K fittings, pump curves with affinity laws, terminal demand curves, the operating point (VFD, PRV, bypass, NPSHa at site elevation), the per-takeoff manifold solve, the **network solver** (several pumps on a header, ring mains), surge and arrestor sizing, and the basin checks (HI 9.8 submergence, drain-back, freeboard, evaporation and spray loss). Darcy governs; the `tdh.py` Hazen-Williams figure is its workbook cross-check. Not yet wired into `run_spine`. See [`hydraulics/README.md`](engine/hydraulics/README.md) | Digital Rain Curtain `src/hydro` JS, golden vectors in `tests/golden/drc/` (1e-12 / 1e-9) |
| `autosize.py` | Fountain Designer phase B: `pick_pipe` (DOC-0049 velocity limits + a 0.04 ft/ft friction-gradient cap), `eval_pump` / `rank_pumps` through the network solve with a fails list, `auto_bypass`, `auto_surge`, `auto_basin`, and `derive` (PLAN.md 2.2 steps 5-8, writing only `auto` sections) | DOC-0049 `SUPPORT`; DRC `presets.js`; HI 9.6.3 |
| `bom.py` | The BOM: pipe by size with 10% waste in 20 ft sticks, grouped fittings, valves, nozzles, equipment, consumables, labor from the house rules; `cut_list`, `fitting_counts`, `merge_sections` for every phase's `{section, key, description, qty, uom, item_hint}` lines | DRC `checks.js` `bomRows`, `route.js` `cutList` |
| `upkeep.py` | `water_plan` (cycles of concentration, drain-and-refill days, the Legionella band), `maintenance_schedule`, `om_per_year` (water, power, chemicals, parts) | DRC `upkeep.js`; ASHRAE 188; `chemistry.py` and `workbook.electric_cost` govern |
| `structure.py` | `beam_reactions` (three-moment continuous beam), pipe hanger loads and the DOC-0049 span check, a basin's load on a slab. Everything else structural is `structural/` (phase S) | DRC `rigging.js`; DOC-0049 pipe specs; ASCE 7 |

### Where the workbook stops and engineering standard starts

Several things the engine needs are simply not in Sapphire's sheets: the orifice discharge
coefficient, pump-curve behaviour, breaker rules, VFD-vs-starter logic, NPSH, water hammer.
Those are computed from published standards, and **the divergence is always recorded** — in
`constants.py`, and in each result's `citations` and `warnings`.

Where a workbook value diverges from the standard form, both are recorded and the engine
reproduces **the spreadsheet**, so results match what Sapphire's designers already expect.

The safety functions carry a caveat you must not strip: `suction_outlet_vgb` reproduces the
DOC-0049 worked example to the cell, but it is an engineering aid and never a substitute for
a listed cover's stamped flow rating. That sentence lives in `warnings` on every result.

## Frappe layer

| File | Purpose |
|---|---|
| `derive.py` | The Fountain Designer's ladder (v1.768.0, rebuild phase E): `derive_model(model, catalogs, rules, *, site_lookup, prices, on_error)` runs PLAN.md 2.2 in order over a schema-2 model — site defaults, basins, the families, regulated minimums, the route template from the Pump room step, phase B's `autosize.derive` (network, pipe sizes, pump, control, bypass, surge), treatment, lighting, phase L's `electrical_report` and `controls_report`, phase S's `structural_takeoff`, winterization, then the merged and priced BOM, O&M and the charts — and returns the reply the page draws (its README, "derive"). It imports no frappe: the API hands it the catalogs, rules, prices and geocoded point as plain dicts. A stage that fails becomes one `ENGINE_STAGE_FAILED` blocker, never an exception; `custom` sections are never written; every float is finite or None. It is also where the phases' shapes meet: a family's `design_flow_gpm` and takeoff `flow_gpm`/`head_ft` become phase B's `design_gpm` and `rated_gpm`/`rated_head_ft`, a curve named by its catalog name gets its points, and phase L/S text item hints are folded into phase B's merge. 4 spaces |
| `issues.py` | The single producer of typed `DesignIssue` records and per-section readiness. The engine speaks in free-form status strings and warning sentences — good for the audit trail, useless to a designer who needs to know *what is wrong and where*. Two generations: `build_issues` / `build_readiness` read a v1 record's child rows (the old wizard path and the print fallback); `model_issues` / `model_readiness` / `model_verdict` (v1.768.0) map every typed check from every phase of a schema-2 derive onto the same `DesignIssue` dict plus `step`, `path`, one-click `remedies` and `acknowledged` (from `model.acks`) |
| `api/designer.py` | The Fountain Designer page's endpoints (v1.768.0): `boot`, `derive`, `get_design`, `save_design`, `push_quotation`, `push_material_request`, `catalog_search`, `acknowledge_issue`, `run_calc`, field for field as `public/js/fountain_designer/README.md` documents. The loaders that feed `derive.py` live here: the house rules (Water Engineering Settings), the pump catalog (`catalog_reader.pump_curve_sets` over pump Items, cached ten minutes), Item prices (valuation rate, else the default selling price list) and the geocoded site (`workforce.sites.geocode_text`, cached a month per address; the app has no elevation lookup, so elevation is the jurisdiction default). `boot`, `get_design` and `catalog_search` answer GET; every write is POST-only. 4 spaces |
| `api/water_design.py` | Whitelisted desk endpoints for the old wizard — thin adapters over `engine/` — kept until phase H. `get_design_state` / `design_state` read the v1.768.0 record (rollups, model counts, issues, the fitting schedule from the model, the canvas); `_save_design` (the `save_water_design` AI tool) turns its schema-1 payload into a model through `engine.model.migrate`. `preview_design` went with the old form's live preview. Every endpoint gates on the `Water Feature Design` doctype, and `doc.save()` enforces document-level permission for the mutation itself |
| `setup.py` | `after_migrate` — adds the pump-spec fields on Item that the pump selector reads, the aquatic-equipment fields, and (v1.758.0) the catalog fields: listing, light, filter and aerator specs and the Spec Source block |
| `catalog_reader.py` | The catalog as plain dicts for the engine: `pump_curve_sets(item_codes)`, `nozzle_profiles(names)`, `light_specs(item_codes)`. Pump Curve records win over the Item's legacy `custom_pump_curve` table; an unpublished efficiency, NPSHr, power, spread or throw reads as None, never the stored 0; every read survives a site that has not migrated yet. 4 spaces |
| `catalog_seed.py` | The approved-seed loader. Reads `docs/fountain-designer/catalog/{pumps,nozzles,lights}.json` and loads only the rows `approved.json` lists, creating or updating Items, Pump Curves and Nozzle Profiles keyed on manufacturer + model, with provenance on each. `load_catalog(dry_run=True)` for `bench execute`, `seed_catalog` (System Manager, POST) over HTTP. Never on `after_migrate`. Tabs |
| `prints.py` | The paper outputs (v1.770.0, rebuild phase G): `build(doc, model, stored, bom, items=, issues=, sheets=)` turns a schema-2 record (`model_json`, `results_json`, the `bom` rows) into **sheets** of blocks (table, facts, drawing, list, text, calc, note) with every number already formatted and every cell styled, for the print formats and the packet. It reads what the derive stored and never recomputes the engine. Also `OUTPUTS` (the papers the Outputs step lists, with their Print Formats), `budget_summary` (BOM groups, contingency, range) and `unique_ids` (drawing ids suffixed so several share a page). No frappe. 4 spaces |
| `packet.py` | The frappe side of the prints. `we_print(doc, sheets)` loads the record and the Items it names (model number, cut-sheet link) and calls `prints.build`; never raises. `we_title_block`, `we_standard_notes`, `we_standard_details`, the stored-drawing helpers, and `assemble_packet` / `generate_packet` (HTML snapshot, Chrome PDF with the attached CAD PDFs merged, attached to the Project so Drive sync uploads it). 4 spaces |
| `setup_print_formats.py` | `after_migrate`: seeds the print formats below idempotently, each upsert guarded on its own. Every Water Feature Design format is the print design system's chrome (Design pillar) around one Jinja block macro (`BLOCK_MACRO`) over `we_print`. The Control Panel Design Submittal and Panel Schedule are unchanged. 4 spaces |
| `page/water_engineering_wizard/` | The desk wizard UI |
| `page/fountain_designer/` | The guided Fountain Designer (v1.761.0, rebuild phase F): quick and full modes, live derive, the verdict panel. Its code is `public/js/fountain_designer/`, whose README is the API contract `api/designer.py` honors (v1.768.0); `?demo=1` stays as the labeled training demo |
| `workspace/water_engineering/` | Desk workspace |

## Print formats

Seeded by `setup_print_formats.ensure_water_print_formats` on every migrate. Since v1.770.0 every
Water Feature Design format reads the schema-2 record through `we_print` (`prints.py`): nothing on
paper recomputes the engine, and the nine retired child tables are not read anywhere.

| Print Format | Sheets | What it prints |
|---|---|---|
| Water Feature Design - Results | `results` | Verdict, flows, head, pump, power, water, budget, O&M, completion; open issues; signed-off warnings |
| Water Feature Design - Calculation Audit | `audit` | Every `CalcResult` in `results_json`: value, formula, inputs with provenance, working, citations, warnings |
| Water Feature Design - Schedules | `equipment`, `piping`, `fittings` | Equipment Schedule (tag, Item, model, qty, duty, electrical, cut sheet); Piping Schedule (segment, run, size, material, length, flow, velocity, head loss, end pressure, status); Fitting Schedule (`engine.bom.fitting_counts`, totals per type, size and material); cut list in 20 ft sticks |
| Water Feature Design - Electrical Schedule | `electrical` | Service and feeder, panel schedule, circuit list (breaker basis, GFCI device), energy per year (phase L) |
| Water Feature Design - Controls BOM | `controls` | Control panel, motor control per pump, controls BOM with part numbers, draft Theory of Operation |
| Water Feature Design - Structural Takeoff | `structural` | Quantities (concrete, rebar, gravel, earthwork, formwork, waterproofing, anchors) and the rebar schedule (phase S) |
| Water Feature Design - Design Drawings | `drawings` | Duty point (when #1400's chart is in), circulation schematic, electrical one-line, power schematic, control ladder |
| Water Feature Design - Design Calculations | `calcs` | The plan-check sheet: venue and vessel (the Fika spa path reads the vessel from `model.legacy`), basins, design basis (code minimum circulation, turnover, filter area, chlorinator), circulation system, the Utah R392-302-41 checklist and the NEC 680 checklist, each line with its status |
| Water Feature Design - Operations and Maintenance | `om` | Cost per year (power, water, chemicals, parts), where the water goes (evaporation, spray, drain and refill, backwash not yet modeled), the water plan (refresh interval, cycles, Legionella band, doses), maintenance schedule |
| Water Feature Design - Winterization | `winterization` | Frost depth, equipment, drain points; the winterize and spring start-up checklists; first start-up checks; the checklist as text for a Maintenance Visit |
| Water Feature Design - Budget | `budget` | BOM rows by group (hydraulic, treatment, lighting, electrical, controls, structural, labor, other) at the Item's selling price and the house-rule labor rate, contingency and range from house rules, and which lines have no price |
| Water Feature Design - Submittal Packet | cover, then `prints.PACKET_SHEETS`, standard details and notes | The health-department plan set; never the budget or the O&M. Chrome PDF |
| Control Panel Design - Submittal | | DOC-0126 wording |
| Control Panel Design - Panel Schedule | | NEC 430.24 / 430.62 / Art. 450 |

The Fountain Designer's Outputs step links each paper of a saved design to its format through
`api/designer.boot().prints`, which lists only the formats the site has.

## DocTypes

**`Water Feature Design`** is the submittable design record, rebuilt in v1.768.0 (PLAN.md 3.2):
identity links (`project`, `opportunity`, `customer`, `serial_no`, `design_title`), `mode`
(Quick / Full), `kit`, `family`, `venue_type`, `status` (Draft / Derived / Reviewed / Issued;
Issued = submitted), `model_json` (the schema-2 model, the source of truth), `results_json`,
`design_issues_json`, `readiness_json`, the rollups (`design_flow_gpm`, `operating_flow_gpm`,
`computed_tdh_ft` — the API's `tdh_ft`, kept in its v1 column for the dashboard and packet —
`operating_head_ft`, `selected_pump`, `pump_kw`, `total_basin_gallons`, `budget_total`,
`om_cost_per_year`, the issue counts, `completion_percent`, `issue_ready`), `bom`, `issue_acks`
and the packet fields. Its controller's single frappe↔engine bridge is `recompute()`: run
`api/designer.run_derive` over `model_json` and write everything back (`write_derive`); `Auto`
BOM lines are rewritten on every save and `Manual` ones kept. `before_submit` refuses blockers,
unsigned warnings and a package that is not `issue_ready`. The Desk form is thin — Design Health
chips, the rollups, the BOM grid, the acknowledgements, and **Open in Designer**.

The nine schema-1 child tables left the parent in v1.768.0;
`patches/convert_water_feature_designs_to_model.py` read each design's stored rows straight from
their tables into `model_json` (`model.migrate`) and deleted nothing. Their DocTypes (Water Feature
Basin, Tier, Nozzle, Pipe Segment, Pump, Electrical Load, Venue Fixture, Equipment, Calc Result)
stay until phase H removes them with a `delete_doc` patch.

Child tables and catalogs:

| DocType | Role |
|---|---|
| `Water Feature Design Line` | The design's BOM (`bom`): `section`, `item`, `description`, `qty`, `uom`, `rate`, `amount`, `source` (Auto / Manual), `catalog_source`, `line_key`. What `push_quotation` and `push_material_request` read; a line with no Item goes as the Text-line Item set on Water Engineering Settings, or (unset) in the Quotation's terms |
| `Water Design Issue Ack` | Designer acknowledgement of a typed issue (mirrored in `model.acks`) |
| `Nozzle Profile` | Nozzle catalog: `nozzle_type` (Orifice Jet, Foam / Aerating, Laminar, Cascade / Fan, Geyser, Bubbler, Spray Ring, Water Screen, Sheet Fall, Other), `item`, `connection_size`, `level_sensitive`, min/max water over the nozzle, `recommended_beam_deg`, the Cd and rated-GPM methods, provenance (`source_url`, `source_document`, `source_page`, `verified_by`, `verified_on`, `read_method`, `error_band_pct`, `catalog_id`), `is_generic_estimate`, and the `performance` table. The controller fills head from PSI (or the reverse), sorts the rows, and warns when flow falls as head rises |
| `Nozzle Performance Point` | Child of Nozzle Profile: `head_ft`, `psi`, `flow_gpm`, `display_height_ft`, `display_diameter_ft`, `throw_ft`, `depth_over_nozzle_in` |
| `Pump Curve` | One curve of a pump Item at one impeller / speed (v1.758.0): `item`, `curve_label`, `impeller_dia_in`, `rpm`, `speed_pct`, `max_flow_gpm`, `shutoff_head_ft`, `source` (Datasheet / Digitized / Rep-supplied / Generic estimate), `source_url`, `source_document`, `source_page`, `verified_by`, `verified_on`, `read_method`, `error_band_pct`, `catalog_id`, `notes`, `points`. The controller refuses a curve whose flow is not strictly rising or whose head rises again after its peak (beyond 1 % reading noise); a drooping curve (head climbing from shutoff to a peak) and a missing shutoff point or a gap only warn |
| `Pump Curve Point` | Curve points: `flow_gpm`, `head_ft`, `efficiency_pct`, `npshr_ft`, `power_bhp`. Child of Pump Curve, and of Item as the legacy `custom_pump_curve` table the readers fall back to |
| `Water Engineering Settings` | Single: engineer of record, contractor, the Fountain Designer's Text-line Item (v1.768.0), and (House Rules tab, v1.759.0) the Sapphire house rules and utility rates that `engine/rules.py` mirrors |

**Catalog provenance on Item** (v1.758.0, `setup.create_catalog_item_fields`): `custom_spec_source_url`,
`custom_spec_revision`, `custom_spec_verified_by`, `custom_spec_verified_on`, `custom_generic_estimate`,
`custom_catalog_id`, `custom_spec_confidence`, `custom_spec_method`, `custom_spec_error_band_pct`;
lights `custom_lumens`, `custom_beam_deg`, `custom_light_control`, `custom_max_depth_ft`, `custom_ip_rating`
(watts and volts are the shared `custom_elec_*` fields); filters `custom_filter_design_rate_gpm_sqft`,
`custom_filter_backwash_gpm`; aerators `custom_aerator_pattern_height_ft`, `custom_aerator_pattern_diameter_ft`,
`custom_aerator_min_depth_ft`, `custom_aerator_cord_lengths`; `custom_ul_listing`. `custom_equipment_class`
gained Pump, Nozzle, Light, Aerator, Valve, Controller and Fitting kit, appended after the aquatic classes.
Every Item field created in code must also be in `scripts/layout/code_owned_fields.json` and the Item
layout spec (`tests/test_layout_field_order_coverage.py`).

### Seeding the catalog

The data and the approval list live in `docs/fountain-designer/catalog/`. `approved.json` names the
rows Nikolas approved by `"Manufacturer|Model"` (case and spacing ignored) or by the row's `id`; an entry
may be an object (`key` or `id`) with `item_code` to attach the row to an Item that already exists, or
`item_name` to name the new one. Rows sharing a manufacturer + model (several part numbers of one model)
are told apart by their first part number and must be approved by `id`. Floating aerators in
`nozzles.json` (no pressure in their rows) load as Aerator Items, without a Nozzle Profile.
The loader keys every record on the row's `id` (stamped as `custom_catalog_id` / `catalog_id`), and
the `approved.json` id list is the gate: a row added to the data later stays unseeded until its id is
appended. Dry run first, then write:

```bash
bench --site <site> execute erpnext_enhancements.water_engineering.catalog_seed.load_catalog
bench --site <site> execute erpnext_enhancements.water_engineering.catalog_seed.load_catalog --kwargs "{'dry_run': 0}"
```

On an existing Item the loader writes specs and provenance only; the name, group, description and
brand are filled when blank and never overwritten. The summary lists, per kind, what was created,
updated, unchanged, unapproved, approved but not in the data, and refused (with the reason).
| `Control Panel Design` | Phase-4 control panel parent |
| `Control Pump`, `Control Light`, `Control Fuse`, `Control Interlock`, `Control IO Point` | Control-panel child rows |

## Tests

All bench-free and all in CI:

```bash
python -m unittest \
  erpnext_enhancements.tests.test_water_engine \
  erpnext_enhancements.tests.test_water_design_issues \
  erpnext_enhancements.tests.test_water_design_controller -v
```

The `hydraulics/` suites and phase B's autosize, BOM, upkeep and structure suites are pytest and
run in their own CI steps:

```bash
python -m pytest erpnext_enhancements/tests/test_hydraulics_*.py erpnext_enhancements/tests/test_autosize.py \
  erpnext_enhancements/tests/test_bom.py erpnext_enhancements/tests/test_structure_upkeep.py -q
```

The catalog suite is plain pytest with its own in-memory frappe, so it runs on its own:

```bash
python -m pytest erpnext_enhancements/tests/test_water_catalog.py -q
```

The electrical package's suites are plain pytest functions and run in their own CI step:

```bash
python -m pytest erpnext_enhancements/tests/test_electrical_sizing.py erpnext_enhancements/tests/test_electrical_report.py erpnext_enhancements/tests/test_electrical_drc_parity.py erpnext_enhancements/tests/test_controls_design.py -q
```

The Fountain Designer's families, model, kits and house rules are pytest suites with their
own CI step (`python -m unittest` collects none of them):

```bash
python -m pytest -q erpnext_enhancements/tests/test_families_weir.py \
  erpnext_enhancements/tests/test_families_jets.py erpnext_enhancements/tests/test_families_others.py \
  erpnext_enhancements/tests/test_water_model.py erpnext_enhancements/tests/test_water_house_rules.py
```

The Fountain Designer's derive and API (v1.768.0) are pytest suites with their own CI step; the
derive suite also runs a real reply through the page's own modules under node (skipped without it):

```bash
python -m pytest -q erpnext_enhancements/tests/test_designer_derive.py erpnext_enhancements/tests/test_designer_api.py
```

The engine suites are **golden tests**: each formula reproduces its sheet's own worked example. When you
change an engine function, the failing assertion is telling you the result no longer matches
the workbook — check the sheet before changing the expectation.
