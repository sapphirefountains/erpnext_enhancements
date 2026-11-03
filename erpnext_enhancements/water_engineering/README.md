# `water_engineering/` — the Fountain Designer

Sapphire's fountain, pool and water-feature design module. A design is one JSON document, the
**schema-2 design model** (`model_json` on a **Water Feature Design**), and everything else is
worked out from it: a stdlib-only engine runs the automation ladder over the model, the guided
**Fountain Designer** page edits it live, the record stores the derive's results, the print formats
and the submittal packet read them, and three AI tools read and write the same record.

It was rebuilt in eight phases between 2026-11-01's decisions and v1.771.0. The plan is
[`docs/fountain-designer/PLAN.md`](../../docs/fountain-designer/PLAN.md), and the record of what was
decided and retired is [ADR 0018](../../decisions/adr/0018-fountain-designer-rebuild.md). Every engine
function is documented inline, with the workbook sheet, standard or source it was verified against.
This README is the map.

> **Indentation is mixed.** `engine/`, `derive.py`, `issues.py`, `calculator.py`, `prints.py`,
> `packet.py`, `setup_print_formats.py`, `catalog_reader.py` and `api/` use **4 spaces**; the doctype
> controllers, `setup.py` and `catalog_seed.py` use **tabs**. Match the file you are editing.

## The one architectural rule

**`engine/` may never import `frappe`.** It is stdlib-only, and that is enforced by
convention and by the tests. Two consequences make it worth protecting:

- The engine is unit-testable without a bench, which is why the water suites run in CI at
  all (`tests/test_water_engine.py`, the hydraulics, families, electrical, structural and
  designer suites listed under Tests).
- The Fountain Designer page, the Desk form's save and the Frappe Assistant Core MCP tools all
  call the **same** functions (`derive.derive_model`, `calculator.run`), so a designer on the page
  and an AI answering a question produce byte-identical math.

`derive.py`, `issues.py`, `calculator.py` and `prints.py` keep the rule too: they import no frappe,
and the frappe side (`api/designer.py`, `packet.py`, the controller) hands them plain dicts.

## How a design flows

```
page (public/js/fountain_designer/) --derive(model_json)--> api/designer.run_derive --> derive.derive_model
   |                                                                                         |  engine/ ladder
   +--save_design(name, model_json)--> Water Feature Design.validate --> the same derive ----+
                                          writes model_json, results_json, design_issues_json,
                                          readiness_json, the rollups and the bom rows
prints / packet (packet.we_print -> prints.build) and the AI tools (api/designer.design_status) read those fields
```

## The result envelope

Every public engine function returns a `CalcResult` (`engine/envelope.py`) rather than a
bare number: the headline `value`/`unit`, the `inputs` it used and where each came from, the
`formula`, ordered `steps`, source `citations`, `warnings`, and any A/B/C `options` the user
must still choose.

That shape is the product, not decoration. It is what lets the page's Calculator drawer, the
Engineering Calculations print and the AI show their work, and what makes the `results` stored in a
design's `results_json` an audit trail rather than a cache.

## Calculation engine — `engine/`

| File | What it computes | Verified against |
|---|---|---|
| `pipeline.py` | `run_spine(inputs)` — the schema-1 spine (Phase 1), kept as a thin adapter for any old caller: chains the calcs, rolls up headline numbers, and reports `next_inputs_needed`. The Fountain Designer's ladder is `derive.py` | — (orchestration) |
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
| `data/jurisdictions.json` + `data/jurisdictions.py` | The per-state jurisdiction table (v1.772.0, phase J): for each state its NEC, IBC, IRC, ISPSC and MAHC editions and basis, who regulates pools, spas and interactive features and how (`iwf_like_utah`, `fogger_potable`, plan review), the design city's climate (elevation, design temperatures, frost depth, hardness, winterize, default seismic category) and the licensing and permit notes, each value cited or null. The loader's `get(code)` returns None for an unknown code, never Utah; `codes()` is Utah first, then by name. `docs/fountain-designer/catalog/jurisdictions.json` is the researched source | the row's own cites |
| `jurisdiction.py` | What the site's state asks of a design (phase J): `entry_for(site)`, the code lines and AHJ notes (`code_report`, `ahj_note`), who is regulated (`spa_regulated`, `iwf_regulated`, `iwf_like_utah`, `fogger_potable`), `winterize` and `dry_climate`, and `summary(site)`, the `jurisdiction` CalcResult the derive returns and the prints read | the table |
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
| `derive.py` | The Fountain Designer's ladder (v1.768.0, rebuild phase E): `derive_model(model, catalogs, rules, *, site_lookup, prices, on_error)` runs PLAN.md 2.2 in order over a schema-2 model — site defaults, basins, the families, regulated minimums, the route template from the Pump room step, phase B's `autosize.derive` (network, pipe sizes, pump, control, bypass, surge), treatment, lighting, phase L's `electrical_report` and `controls_report`, phase S's `structural_takeoff`, winterization, then the merged and priced BOM, O&M and the charts — and returns the reply the page draws (its README, "derive"). It imports no frappe: the API hands it the catalogs, rules, prices and the address lookups as plain dicts. Since v1.772.0 (phase J) the site step keys on the state's row of `engine/data/jurisdictions.json`: `derive_site` first puts the site in the lookup's state where the designer picked none (v1.774.0, `SITE_OWNED`), then fills each site value the designer did not type from the lookups, then the state's climate, then the house rates, and records its source in `site.sources` (so a new state's climate replaces the old state's and a typed value is never touched); `regulated_site` reads the state's spa and interactive rules; the `jurisdiction` result and one `JURISDICTION_CONFIRM_AHJ` / `JURISDICTION_NO_DATA` issue say what the state enforces. A stage that fails becomes one `ENGINE_STAGE_FAILED` blocker, never an exception; `custom` sections are never written; every float is finite or None. It is also where the phases' shapes meet: a family's `design_flow_gpm` and takeoff `flow_gpm`/`head_ft` become phase B's `design_gpm` and `rated_gpm`/`rated_head_ft`, a curve named by its catalog name gets its points, and phase L/S text item hints are folded into phase B's merge. 4 spaces |
| `issues.py` | The single producer of typed `DesignIssue` records, per-section readiness and the verdict for a schema-2 design: `model_issues` / `model_readiness` / `model_verdict` map every typed check from every phase of a derive onto one `DesignIssue` dict with `step`, `path`, one-click `remedies` and `acknowledged` (from `model.acks`). Also `summarize` (the list view's counts), `fitting_schedule` (DOC-0121, from the model's segments) and `we_design_issues` (the stored issues for a print). The schema-1 producers `build_issues` / `build_readiness` went with the child tables in v1.771.0. 4 spaces |
| `calculator.py` | The stateless calculator (v1.771.0): `CATALOG`, every engine calculation with its label, group and typed inputs (each with the default the engine uses), and `run(calc, inputs, nozzle_lookup)`, the dispatch that was the old wizard's `_run_calc`. The page's Calculator drawer, `api/designer.run_calc` and the `water_calc` AI tool all go through it. No frappe. 4 spaces |
| `api/designer.py` | The Fountain Designer's API (v1.768.0): `boot`, `derive`, `get_design`, `save_design`, `push_quotation`, `push_material_request`, `catalog_search`, `acknowledge_issue`, `email_draft`, `run_calc` and `check_permission`, field for field as `public/js/fountain_designer/README.md` documents. The loaders that feed `derive.py` live here: the house rules (Water Engineering Settings), the pump catalog (`catalog_reader.pump_curve_sets` over pump Items, cached ten minutes), Item prices (valuation rate, else the default selling price list) and the site lookups (`site_lookup`, on a save only): the geocoded point and, for a United States address, its state where the design has none or the engine set it (`workforce.sites.geocode_text`, v1.774.0), Google's elevation at it (`workforce.sites.elevation_at`, the same server key) and the USGS ASCE 7-22 seismic design values (`seismic_lookup`: SDS, SD1, SDC at Risk Category II, Site Class D), each cached a month (a failure ten minutes), none raising or logging, and each skipped for a field the designer typed. `boot().options.jurisdictions` lists the table's states (v1.772.0). `save_design` returns the derived site, so the page shows what the save looked up (v1.774.0). Since v1.771.0 it also holds what outlived the old API: `item_pump_curves` (the Item form's Pump Curves panel), `pump_curves`, `nozzle_profile_params`, and the AI tools' helpers (`design_status`, `design_list`, `tool_save`, `tool_model`, `merge_legacy_payload`, `canvas_state`). `boot`, `get_design`, `catalog_search` and `run_calc` answer GET; every write is POST-only. 4 spaces |
| `api/water_design.py` | What is left of the old wizard's API (v1.771.0): the Control Panel Design reader (`control_panel_state`, the `control_panel_status` AI tool's) and deprecated re-exports of `check_permission`, `run_calc`, `item_pump_curves`, `pump_curves` and `nozzle_profile_params` for a Desk tab on an older bundle. Everything else it had (the design state reader, the schema-1 save, the issue reader, the pump and equipment candidates, the loss catalog, `run_design_spine`) had no caller once the wizard and the old form were gone. 4 spaces |
| `email_hooks.py` | An email sent from a Water Feature Design goes out in the email design system's chrome (phase G, `wrap_design_email`, after the sanitizer) |
| `setup.py` | `after_migrate` — adds the pump-spec fields on Item that the pump selector reads, the aquatic-equipment fields, and (v1.758.0) the catalog fields: listing, light, filter and aerator specs and the Spec Source block |
| `catalog_reader.py` | The catalog as plain dicts for the engine: `pump_curve_sets(item_codes)`, `nozzle_profiles(names)`, `light_specs(item_codes)`. Pump Curve records win over the Item's legacy `custom_pump_curve` table; an unpublished efficiency, NPSHr, power, spread or throw reads as None, never the stored 0; every read survives a site that has not migrated yet. 4 spaces |
| `catalog_seed.py` | The approved-seed loader. Reads `docs/fountain-designer/catalog/{pumps,nozzles,lights}.json` and loads only the rows `approved.json` lists, creating or updating Items, Pump Curves and Nozzle Profiles keyed on manufacturer + model, with provenance on each. `load_catalog(dry_run=True)` for `bench execute`, `seed_catalog` (System Manager, POST) over HTTP. Never on `after_migrate`. Tabs |
| `prints.py` | The paper outputs (v1.770.0, rebuild phase G): `build(doc, model, stored, bom, items=, issues=, sheets=)` turns a schema-2 record (`model_json`, `results_json`, the `bom` rows) into **sheets** of blocks (table, facts, drawing, list, text, calc, note) with every number already formatted and every cell styled, for the print formats and the packet. It reads what the derive stored and never recomputes the engine. Also `OUTPUTS` (the papers the Outputs step lists, with their Print Formats), `budget_summary` (BOM groups, contingency, range) and `unique_ids` (drawing ids suffixed so several share a page). No frappe. 4 spaces |
| `packet.py` | The frappe side of the prints. `we_print(doc, sheets)` loads the record and the Items it names (model number, cut-sheet link) and calls `prints.build`; never raises. `we_title_block`, `we_standard_notes`, `we_standard_details`, the stored-drawing helpers, and `assemble_packet` / `generate_packet` (HTML snapshot, Chrome PDF with the attached CAD PDFs merged, attached to the Project so Drive sync uploads it). 4 spaces |
| `setup_print_formats.py` | `after_migrate`: seeds the print formats below idempotently, each upsert guarded on its own. Every Water Feature Design format is the print design system's chrome (Design pillar) around one Jinja block macro (`BLOCK_MACRO`) over `we_print`. The Control Panel Design Submittal and Panel Schedule are unchanged. 4 spaces |
| `page/fountain_designer/` | The guided Fountain Designer (v1.761.0, rebuild phase F) at `/desk/fountain-designer`: Start, Site, Features, Basins, Pump room, Review and Outputs; quick and full modes, live derive, the verdict panel, the duty chart, the BOM, the papers and Send design, and (v1.771.0) the Calculator drawer on Review. Its code is `public/js/fountain_designer/`, whose README is the API contract `api/designer.py` honors; **Open the training demo** on Start (`?demo=1`) answers every call from a fixture. `/desk/water-engineering-wizard` lands here (`public/js/global_enhancements/retired_routes.js`) |
| `workspace/water_engineering/` | Desk workspace: the Fountain Designer shortcut, New Water Feature Design, the Design Health number cards, the Designs and Tools cards. The Design profile's Dock row opens the Fountain Designer (`setup/dock_profiles.py`) |

## The AI tools

Three Frappe Assistant Core tools (`assistant_tools/`, registered in `hooks.py` `assistant_tools`) read
and write the record the page does, through `api/designer.py` (v1.771.0, phase H):

| Tool | Gate | What it does |
|---|---|---|
| `water_calc` | read-only | One engine calculation with its math (`calculator.run`); its `calc` enum is `calculator.CALCS`, the Calculator drawer's list |
| `water_design_status` | read-only | One design as the page shows it (`design_status`): the stored verdict and rollups, the typed issues and readiness, counts, `designer_url`, the print and packet links, the canvas the Triton chat draws, and with `include_model` the schema-2 model. No design: the recent designs (`design_list`) |
| `save_water_design` | write, gated (`_gate.py`, Low) | Creates or updates through `save_design` (`tool_save`). Takes a schema-2 `model` (the whole design) or the old row lists (`basins`, `features`, `pipe_segments`, `pumps` with `global_inputs`), laid onto the stored model through `engine.model.migrate` and `merge_legacy_payload`, each list sent replacing that section and pinning it `custom`. Replies with the same state as the status tool and `ignored`, naming every input with no place in the model |

`control_panel_status` reads a Control Panel Design (`api/water_design.control_panel_state`).

## Print formats

Seeded by `setup_print_formats.ensure_water_print_formats` on every migrate. Since v1.770.0 every Water
Feature Design format reads the schema-2 record through `we_print` (`prints.py`): nothing on paper
recomputes the engine, and the nine retired child tables are not read anywhere.

Nikolas asked for "a printable report view and other views with design information for printing and
emailing" in the house style, so every view is the print design system's chrome
([`docs/print-design-system.md`](../../docs/print-design-system.md)):

- the Design pillar's violet stripe and eyebrow, and the letterhead;
- a facts row: the customer's party block, the project and site, the designer and status;
- the date, and a red DRAFT until the design is issued;
- ruled tables with the numbers right-aligned, the running line and the closing stripe;
- Letter paper, and a page break between sections.

The **Design Report** is the doctype's default format: `set_default_report` sets it on migrate once the
format exists and is enabled, and keeps a default an admin chose unless it is gone, disabled or superseded.
`budget_total` on the record is the Budget the prints show (the BOM subtotal plus the house-rule
contingency); `bom_subtotal` is the BOM's sum.

| Print Format | Sheets | What it prints |
|---|---|---|
| Water Feature Design - Design Report | `report` | For the client. What the feature does, the numbers at a glance, the circulation schematic and duty-point chart, the equipment by name (no part numbers), the budget by section with contingency and range, running costs per year, notes and assumptions |
| Water Feature Design - Engineering Calculations | `audit`, `calcs` | Every `CalcResult` grouped by the ladder's sections (value, formula, inputs with provenance, working, citations, warnings). Then the plan check: venue and vessel (the Fika spa path reads the vessel from `model.legacy`), basins, the design basis, the circulation system, and the Utah R392-302-41 and NEC 680 checklists, each line with its status |
| Water Feature Design - Schedules | `equipment`, `piping`, `fittings`, `structural`, `electrical`, `controls` | Equipment Schedule (tag, Item, model, qty, duty, electrical, cut sheet). Piping Schedule (segment, run, size, material, length, flow, velocity, head loss, end pressure, status). Fitting Schedule (`engine.bom.fitting_counts`) and the 20 ft cut list. Structural quantities and the rebar schedule. Service and feeder, panel schedule, circuit list and energy. The control panel, motor control and controls BOM |
| Water Feature Design - Design Drawings | `drawings` | Duty point, circulation schematic, electrical one-line, power schematic, control ladder |
| Water Feature Design - Budget Proposal | `budget` | The BOM by group at the Item's selling price and the house-rule labor rate, contingency and range from the house rules, and which lines have no price |
| Water Feature Design - Operations and Winterization | `om`, `winterization` | Cost per year, where the water goes, the water plan, the maintenance schedule. Then frost depth, drain points, the winterize and spring start-up checklists, the first start-up checks, and the checklist as text for a Maintenance Visit |
| Water Feature Design - Submittal Packet | cover, then `prints.PACKET_SHEETS`, standard details and notes | The health-department plan set: the cover with the title block, stamp box, sheet index and revisions, then the sheets. Never the budget or the O&M. Chrome PDF |
| Control Panel Design - Submittal | | DOC-0126 wording |
| Control Panel Design - Panel Schedule | | NEC 430.24 / 430.62 / Art. 450 |

Results, Calculation Audit and Design Calculations (`prints.SUPERSEDED`) read the retired tables and are
disabled on migrate, not deleted.

**Sending a design.** The Fountain Designer's Outputs step links each paper of a saved design to its
print view. `api/designer.boot().prints` lists only the formats the site has. **Send design** (the
Outputs step and the Desk form) opens frappe's email composer with:

- the chosen paper attached as a PDF;
- the customer's address (`print_lookup.party_details`);
- a body in the email design system's chrome (`api/designer.email_draft`, `email_style.wrap`).

The email lands on the design's timeline. On the Desk form the PDF comes from the composer's own Attach
Document Print. The page has no form for the composer to print from, so `email_draft` makes the PDF there
as a private File named `<design>-print-<paper>.pdf`; the packet's CAD merge skips those files.

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

The nine schema-1 child tables left the parent in v1.768.0, when
`patches/convert_water_feature_designs_to_model.py` read each design's stored rows straight from
their tables into `model_json` (`model.migrate`). Their DocTypes and tables went in v1.771.0; see
**Retired** below.

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
| `Control Panel Design` | Phase-4 control panel parent; phase L's control design fills its rows |
| `Control Pump`, `Control Light`, `Control Fuse`, `Control Interlock`, `Control IO Point` | Control-panel child rows |
| `Standard Note`, `Standard Detail` | The packet's code-keyed notes (SP-5) and detail drawings |
| `Submittal Revision` | The packet's revision block |

**Catalog provenance on Item** (v1.758.0, `setup.create_catalog_item_fields`): `custom_spec_source_url`,
`custom_spec_revision`, `custom_spec_verified_by`, `custom_spec_verified_on`, `custom_generic_estimate`,
`custom_catalog_id`, `custom_spec_confidence`, `custom_spec_method`, `custom_spec_error_band_pct`
(the source URL and the revision are Small Text since v1.774.3; see Seeding the catalog);
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
updated, unchanged, unapproved, approved but not in the data, and refused (with the reason), plus
`codes` (each approved row's Item code) and `renames_pending` (Items that exist under another code).

**Item codes are the manufacturer's part numbers** (v1.775.0; Nikolas, 2026-11-02). In order:

1. an `approved.json` entry's `item_code` always wins (that is how a row is attached to a Sapphire
   Item that already exists);
2. the row's `part_no`: the first entry of a list, every trailing parenthetical note removed and
   whitespace trimmed (`340030 (EQ500 1-ph)` -> `340030`), and the first of a ` / ` set of
   alternatives (`F2462038 / F2462051 (ball joint)` -> `F2462038`);
3. the model as written, commas dropped as the naming rules ask of a code (`NBS 015-090`, `RPJ-375`),
   when the row has no part number, when it names a range (`7.3JF100 to 7.3JF400`, `LED3125050 ...
   LED3125400`, `601000-601004`), or when another row holds it too.

"Holds it too" and every collision below are judged the way the Item naming guard judges codes, with
case and punctuation ignored: `SHPF.50` (a 0.5 HP Jandy pump) and `SHPF5.0` (a 5 HP one) are one code
to the guard, so both rows take their model. Codes are worked out across all three files at once
(`assign_codes`), approved rows or not, so approving a row never changes another row's code. A code
that still collides with another row's, or with an Item already on the site that is not this row's, is
the row's error, with the reason. Nothing is ever suffixed to make a code unique; set `item_code` in
`approved.json` instead. The loader finds an existing Item by its catalog id before anything but an
explicit `item_code`, so a renamed Item is found under its new name and never recreated. Item names,
groups, brand, `custom_model_no` and `custom_catalog_id` are not affected by the code. The codes pass
the naming advisor with no finding, and the naming guard is bypassed for the loader's inserts anyway.

**Renaming the Items loaded before the rule** (v1.775.0). `rename_catalog_items` (POST, System Manager,
`dry_run` defaults to 1 and only an explicit no writes, `limit` defaults to 25) plans a rename for every
Item with a `custom_catalog_id` and reports `renames` (`{old, new, kind, catalog_id, basis}`),
`unchanged`, `refused` (with the reason: an id no row has, an id two Items carry, a code that collides,
an Item whose code is not the one the old rule gave its row, which protects a Sapphire Item attached to
a row, or a new code another Item already holds), `designs` it rewrites, and `remaining`. A write
renames the first `limit` with `frappe.model.rename_doc.rename_doc` (`rebuild_search=False`, so nothing
is enqueued; the Item's global-search row is moved by hand), one savepoint per Item, committing every
five together with the design rewrite they need, and clears the designer's ten-minute pump-catalog cache.
`rename_doc` rewrites Link fields only, and a design names its pump inside its JSON, so every Water
Feature Design whose `model_json`, `results_json`, `design_issues_json` or `readiness_json` names an old
code is rewritten (any value, key, or `|`-part of a key equal to the code; text that merely contains it
is left alone), with an Info Comment on the design. The rewrite maps every catalog Item already renamed,
not only this round's, so a design saved with an old code between rounds is repaired by the next one.
A write round with nothing left to rename still runs the repair, so running the endpoint again also
fixes a design saved after the last rename. A JSON column read back already parsed is handled as well.
Every round re-plans from the site, so a round that stopped is just run again. Nothing runs on migrate.

```js
// in a Desk console, as a System Manager: dry run, then rounds, then a re-count
await frappe.xcall("erpnext_enhancements.water_engineering.catalog_seed.rename_catalog_items", {dry_run: 1})
await frappe.xcall("erpnext_enhancements.water_engineering.catalog_seed.rename_catalog_items", {dry_run: 0, limit: 5})
// read `seconds` from the first round, then raise `limit` so a round stays well under a minute
```

**Merging a row into an Item Sapphire already had** (v1.775.0; Nikolas, 2026-11-02). Suppose an
`approved.json` entry's `item_code` names an existing Item, and the loader's own Item for that row still
carries the row's catalog id under its old code. That is not a rename, so it is planned as a **merge**.
`merges` lists it as `{old, new, kind, catalog_id, basis, moves, writes, delete: true}`. `writes`
gives every field the merge changes on the existing Item as `{from, to}`, so the dry run shows exactly
what Sapphire's Item will read. A write then does four things in one savepoint:

1. It writes the **catalog's own values** for the row onto the existing Item: specs, provenance,
   `custom_catalog_id` and the equipment class. These are exactly what the loader writes on every seed,
   with Nones left out. They are never copied from the old Item. A field the catalog leaves unset sits
   on the old Item as Frappe's 0, and copying it would overwrite Sapphire's figure. Prod's `342002`
   would have lost its 1.5 HP that way (review of 57614687).
2. It re-points the old Item's Pump Curves and Nozzle Profiles (`moves`). A curve's fetched
   `item_name` is set to the existing Item's, because `db.set_value` does not re-fetch it.
3. It deletes the old Item with `frappe.delete_doc`, never forced.
4. It rewrites the designs, as a rename does.

The existing Item keeps its name, group, description and brand. The designer still lists it outside
the "Pumps" group, because `_pump_items` also reads equipment class "Pump". The load's group warning
now says so, instead of claiming the opposite.

A merge is refused when anything else links to the old Item. The refusal names every
`Doctype.field (n)` found across all of `rename_doc`'s Link-to-Item fields, Item Price and Bin
included. Those two count because `Item.on_trash` deletes them before `delete_doc` checks links.
`delete_doc`'s own message on an Item ("You can disable this Item instead") names nothing.

A merge is also refused when the existing Item is another catalog row's. Until the merge has run, a
load refuses the row. Otherwise it would stamp the id on a second Item and load a second set of curves.
After the merge, the loader finds the existing Item by its `item_code` and its curves by label.
Merges run before renames and count against `limit`.

The one framework side effect is `delete_doc`'s own `delete_dynamic_links` job, enqueued after commit.
It clears ToDos, Comments and Versions that point at the deleted Item. Nothing in this module enqueues.

On prod (2026-11-02) the plan is 377 renames, 3 merges and 0 refusals. The merges are the catalog rows
for three Pentair pumps that Sapphire already had under their part numbers:

| Old Item | Merged into | Curves moved |
| --- | --- | --- |
| `PUMP-PENTAIR-WHISPERFLOXF-VS-5-THP-COMMERCIAL-VARIABLE-SPEED` | `022035` | 4 |
| `PUMP-PENTAIR-SUPERFLO-VST-VARIABLE-SPEED-2-2-THP` | `342002` | 5 |
| `PUMP-PENTAIR-SUPERFLO-1-1-THP` | `348190` | 1 |

The three Sapphire Items stay in "Products". The old Items have no Item Price, Bin or order line.
Two designs name catalog pumps, both in WFD-2026-1328192:

- a Goulds 3656LH, in `model_json`;
- the 342002 duplicate, in a `results_json` candidate.

**What the dry run checks** (v1.774.3). Besides the curve and nozzle checks the forms apply, every
value about to be written (Item, Pump Curve and its Pump Curve Points, Nozzle Profile and its Nozzle
Performance Points) is checked against its field's type and `length` from `frappe.get_meta`, by
Frappe's own `_validate_length` rule: a Data, Link or Select field holds `length` characters, 140 when
`length` is 0, and Small Text and Text are unbounded. An overlong value is the row's error, worded as
Frappe words it, in a dry run and a write alike. Nothing is truncated. Frappe itself checks lengths only
on insert, so before this a dry run could not see them.

**Where to buy each Item** (v1.776.0; Nikolas, 2026-11-02). `purchasing.json`, beside the catalog,
gives each row a seller found on the public web: the seller's name, its own part number, the product
page and the advertised price for one unit. Its schema, and how to refresh prices, are in the catalog
README. Once the Item is found or created, and inside the same per-row savepoint, the loader does
three things:

1. **Supplier.** It finds the Supplier with exactly the seller's name, or creates one (group
   "Distributor", type Company, country United States unless the entry says `seller_country`). An
   existing Supplier is never renamed or edited. A seller that matches an existing Supplier only once
   case and spaces are ignored (`Inyo Pools` against `INYO Pools`) is refused, so Nikolas decides which
   name is right. That seller is skipped; the Item and the row's other sellers still load.
2. **Item Supplier row.** It finds the Item's row for that supplier and sets `supplier_part_no` and
   `purchase_url`, or appends the row. Rows for any other supplier, such as Conely's `PEN022035` on
   022035, are never touched. `purchase_url` is a Custom Field that exists on prod but is not in this
   app's fixtures. A site without it gets the part number and a refusal line saying links were not
   loaded.
3. **Item Price** on the buying list the file names, `Reference List 2026`. It sets the rate, the
   currency, `valid_from` (the date the price was seen), the supplier, the Item's stock UOM and a note
   giving the seller, the date and the link. An existing price is found by item, list and supplier. Its
   rate is replaced only by a sighting with a later date, so an old file never overwrites a newer price.
   The list must exist, be enabled, be a buying list and be in USD. If it is not, no price is loaded and
   the list is never created. Standard Buying and every selling list are never touched.

The summary's `purchasing` block has the same keys in a dry run and in a write:

- `suppliers_created`, `suppliers_used`;
- `supplier_rows_added` / `_updated`;
- `prices_added`, `prices_updated` and `prices_kept` (stored on the same day or later);
- `rows_with_seller` and `rows_without_seller`;
- `refused` (`{key, error}`);
- `not_in_catalog` (entries that name no row).

Each row's `changes` lists its own lines too. The block is `None` when there is no `purchasing.json`.
If a row fails, its rollback also takes the Suppliers it created. The run forgets them, so the next row
with that seller creates the Supplier again.

**Prod, 2026-11-02.** The loader had never been run on prod, so the designer's pump picker was empty.
The dry run came back clean, and the first write loaded **260 of the 380 approved rows** (72 pumps, 169
nozzles, 19 lights) and refused 120: 97 pumps and 7 nozzles whose revision (title, access date and page)
ran to 185 characters, and 16 lights whose source URL ran to 154, past Data's 140. Each refused row's
savepoint held, so nothing of it was left behind. v1.774.3 made `custom_spec_revision` and
`custom_spec_source_url` Small Text, and the `source_document` of Pump Curve and Nozzle Profile with them:
the same text reaches those (178 and 170 characters), which the prod write never got to because each
row failed on its Item first. After that deploy a second write should create the 120 and report the 260
unchanged. `tests/test_water_catalog.py` dry-runs the whole real catalog against the declared schema,
so a row added later that would overflow a field fails CI.

## Retired in v1.771.0 (rebuild phase H)

What the JSON model and the Fountain Designer replaced, removed in two steps each (the files, and a
`post_model_sync` patch for what the files leave on a site). [ADR 0018](../../decisions/adr/0018-fountain-designer-rebuild.md)
records why.

- **Nine child DocTypes**: Water Feature Basin, Water Feature Tier, Water Feature Nozzle, Water
  Feature Pipe Segment, Water Feature Pump, Water Feature Electrical Load, Water Venue Fixture, Water
  Feature Equipment and Water Feature Calc Result. `patches/retire_water_feature_child_doctypes.py`
  deletes the rows of converted (schema-2) and vanished designs from the five tables the v1.768.0
  conversion read (Basin, Nozzle, Tier, Pipe Segment, Pump) and from Calc Result (derived output),
  drops each table once it is empty (frappe's `delete_doc` never drops a table), and only then removes
  the DocType and any customization on it. Rows of a design that is still schema 1, and the rows of a
  live design in Electrical Load, Venue Fixture or Equipment (which the conversion never read), are kept with their
  table and DocType and named in the Error Log; the conversion patch reads a retired table with SQL
  once `remove_orphan_doctypes` has taken its DocType, so a kept design stays convertible.
  Production had 13 rows on 2026-11-01, every one a child of the schema-2 WFD-2026-209315.
- **The Water Engineering Wizard** (`page/water_engineering_wizard/`). Its quick calculator is the
  Calculator drawer on the Fountain Designer's Review step. `patches/retire_water_engineering_wizard.py`
  deletes the Page row, re-imports the Water Engineering and Design Hub workspaces, and points any
  other Workspace Link, Shortcut or Sidebar item still naming it at the Fountain Designer; the old
  address is sent on by `frappe.re_route` (`retired_routes.js`).
- **The schema-1 code paths**: `issues.build_issues` / `build_readiness` and their helpers, the
  `we_fitting_schedule` Jinja method, `packet.is_regulated_packet`, the controller's `legacy_inputs`
  (now inside the conversion patch, its only reader), and the old API endpoints listed under
  `api/water_design.py` above. `patches/backfill_water_design_issue_fields.py` (v1.144.0, ran on every
  site) is a no-op.
- `tests/test_water_engineering_retirement.py` fails the build if any code path names them again.

## Tests

All bench-free and all in CI:

```bash
python -m unittest \
  erpnext_enhancements.tests.test_water_engine \
  erpnext_enhancements.tests.test_water_design_controller -v
python -m unittest erpnext_enhancements.tests.test_water_engineering_retirement -v
```

The retirement suite is the phase-H guard: the sweep for the retired names, the survivors, the
redirect, and both patches run dry against an in-memory site shaped like production.

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
derive suite also runs a real reply through the page's own modules under node (skipped without it).
The API suite also covers the calculator and the AI tools' helpers against schema-2 models
(v1.771.0); the tools' own routing is in `test_assistant_tools_schema`:

```bash
python -m pytest -q erpnext_enhancements/tests/test_designer_derive.py erpnext_enhancements/tests/test_designer_api.py
```

The engine suites are **golden tests**: each formula reproduces its sheet's own worked example. When you
change an engine function, the failing assertion is telling you the result no longer matches
the workbook — check the sheet before changing the expectation.
