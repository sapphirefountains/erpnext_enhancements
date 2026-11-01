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
| `pump.py` | Pump selection by catalog match, plus electrical/breaker sizing | DOC-0049 + engineering standard (see below) |
| `safety.py` | VGB / ANSI-APSP-16 suction-outlet anti-entrapment, NPSH cavitation check, Joukowsky water hammer | DOC-0049 `P - Suction Outlets`; HI standards |
| `drainage.py` | Gravity drainage (Manning's) and surge-basin sizing (Phase 3) | DOC-0049 `10 - Gravity`, `G - Gravity`, `B - Surge Basin` |
| `chemistry.py` | Chlorinator feed and chemical rate advisory (Phase 2) | DOC-0049 `C - Chemicals`, DOC-0119 |
| `treatment.py` | LSI, ASHRAE evaporation, make-up water, heating load, chemical dose, UV dose, filtration area | DOC-0049 `O` sheet + standards |
| `controls.py` | Control-panel sizing: lighting relays, currents (Phase 4) | DOC-0126 |
| `workbook.py` | Electric cost, vertical pipe, open-channel flow, lazy-river horsepower, programmatic planning | DOC-0049 `E`, `K`, `J`, `L`, `D` |
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
| `data/chemistry.py` | Ozone contact-tank catalog and water-balance target ranges | DOC-0049 `SUPPORT` → `ContactTanks`, DOC-0119 |
| `hydraulics/` | The governing solver (Fountain Designer rebuild, phase A): water properties, Darcy-Weisbach + Churchill friction, Darby 3-K fittings, pump curves with affinity laws, terminal demand curves, and the operating point where the pump meets the system (VFD, PRV, bypass, NPSHa at site elevation). Darcy governs; the `tdh.py` Hazen-Williams figure is its workbook cross-check. Not yet wired into `run_spine`. See [`hydraulics/README.md`](engine/hydraulics/README.md) | Digital Rain Curtain `src/hydro` JS, golden vectors in `tests/golden/drc/` (1e-12 / 1e-9) |

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
| `issues.py` | The single producer of typed `DesignIssue` records and per-section readiness. The engine speaks in free-form status strings and warning sentences — good for the audit trail, useless to a designer who needs to know *what is wrong and where*. The form, wizard, list view, print formats and Triton all consume this one derived structure |
| `api/water_design.py` | Whitelisted desk endpoints for the wizard and form JS — thin adapters over `engine/`. `save_inputs` and `get_design_state` expose the `_save_design` / `design_state` helpers the MCP tools reuse, so both surfaces share one implementation. Every endpoint gates on the `Water Feature Design` doctype, and `doc.save()` enforces document-level permission for the mutation itself |
| `setup.py` | `after_migrate` — adds the pump-spec fields on Item that the pump selector reads, the aquatic-equipment fields, and (v1.758.0) the catalog fields: listing, light, filter and aerator specs and the Spec Source block |
| `catalog_reader.py` | The catalog as plain dicts for the engine: `pump_curve_sets(item_codes)`, `nozzle_profiles(names)`, `light_specs(item_codes)`. Pump Curve records win over the Item's legacy `custom_pump_curve` table; an unpublished efficiency, NPSHr, power, spread or throw reads as None, never the stored 0; every read survives a site that has not migrated yet. 4 spaces |
| `catalog_seed.py` | The approved-seed loader. Reads `docs/fountain-designer/catalog/{pumps,nozzles,lights}.json` and loads only the rows `approved.json` lists, creating or updating Items, Pump Curves and Nozzle Profiles keyed on manufacturer + model, with provenance on each. `load_catalog(dry_run=True)` for `bench execute`, `seed_catalog` (System Manager, POST) over HTTP. Never on `after_migrate`. Tabs |
| `setup_print_formats.py` | `after_migrate` — ships the server-rendered (Jinja) print formats over the persisted rollups and `calc_results` audit trail: Results, Calculation Audit, Schedules, the multi-sheet Submittal Packet, and **Design Calculations** — a single-sheet plan-check calc sheet for regulated venues, grouped by hydraulic system (circulation vs. therapy-jet loop, keyed off each fixture / pump / pipe segment's `system` tag) |
| `page/water_engineering_wizard/` | The desk wizard UI |
| `workspace/water_engineering/` | Desk workspace |

## DocTypes

**`Water Feature Design`** is the submittable parent that accumulates a fountain's Phase-1
design. Its controller's single frappe↔engine bridge is `recompute()`: convert child rows to
plain dicts, run the pure spine, write back the headline rollups, the per-row computed
columns, and the `calc_results` audit trail. Keep that the only crossing point.

Child tables and catalogs:

| DocType | Role |
|---|---|
| `Water Feature Basin` | Basin geometry |
| `Water Feature Tier` | Tier definitions |
| `Water Feature Nozzle` | Nozzle rows on a design |
| `Water Feature Pipe Segment` | Piping segments |
| `Water Feature Pump` | Selected pump rows |
| `Water Feature Electrical Load` | Electrical load rows |
| `Water Feature Calc Result` | Persisted `CalcResult` audit trail |
| `Water Design Issue Ack` | Designer acknowledgement of a typed issue |
| `Nozzle Profile` | Nozzle catalog: `nozzle_type` (Orifice Jet, Foam / Aerating, Laminar, Cascade / Fan, Geyser, Bubbler, Spray Ring, Water Screen, Sheet Fall, Other), `item`, `connection_size`, `level_sensitive`, min/max water over the nozzle, `recommended_beam_deg`, the Cd and rated-GPM methods, provenance (`source_url`, `source_document`, `source_page`, `verified_by`, `verified_on`, `read_method`, `error_band_pct`, `catalog_id`), `is_generic_estimate`, and the `performance` table. The controller fills head from PSI (or the reverse), sorts the rows, and warns when flow falls as head rises |
| `Nozzle Performance Point` | Child of Nozzle Profile: `head_ft`, `psi`, `flow_gpm`, `display_height_ft`, `display_diameter_ft`, `throw_ft`, `depth_over_nozzle_in` |
| `Pump Curve` | One curve of a pump Item at one impeller / speed (v1.758.0): `item`, `curve_label`, `impeller_dia_in`, `rpm`, `speed_pct`, `max_flow_gpm`, `shutoff_head_ft`, `source` (Datasheet / Digitized / Rep-supplied / Generic estimate), `source_url`, `source_document`, `source_page`, `verified_by`, `verified_on`, `read_method`, `error_band_pct`, `catalog_id`, `notes`, `points`. The controller refuses a curve whose flow is not strictly rising or whose head rises again after its peak (beyond 1 % reading noise); a drooping curve (head climbing from shutoff to a peak) and a missing shutoff point or a gap only warn |
| `Pump Curve Point` | Curve points: `flow_gpm`, `head_ft`, `efficiency_pct`, `npshr_ft`, `power_bhp`. Child of Pump Curve, and of Item as the legacy `custom_pump_curve` table the readers fall back to |
| `Water Engineering Settings` | Single: engineer of record, contractor, and (House Rules tab, v1.759.0) the Sapphire house rules and utility rates that `engine/rules.py` mirrors |

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

The `hydraulics/` suites are pytest and run in their own CI step:

```bash
python -m pytest erpnext_enhancements/tests/test_hydraulics_*.py -q
```

The catalog suite is plain pytest with its own in-memory frappe, so it runs on its own:

```bash
python -m pytest erpnext_enhancements/tests/test_water_catalog.py -q
```

The Fountain Designer's families, model, kits and house rules are pytest suites with their
own CI step (`python -m unittest` collects none of them):

```bash
python -m pytest -q erpnext_enhancements/tests/test_families_weir.py \
  erpnext_enhancements/tests/test_families_jets.py erpnext_enhancements/tests/test_families_others.py \
  erpnext_enhancements/tests/test_water_model.py erpnext_enhancements/tests/test_water_house_rules.py
```

The engine suites are **golden tests**: each formula reproduces its sheet's own worked example. When you
change an engine function, the failing assertion is telling you the result no longer matches
the workbook — check the sheet before changing the expectation.
