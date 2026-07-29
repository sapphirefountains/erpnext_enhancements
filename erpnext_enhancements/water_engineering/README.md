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
| `constants.py` | Physical constants, read from the workbooks' formula cells with openpyxl rather than a textbook | the workbooks |
| `units.py` | Unit conversions | — |
| `data/pipe_specs.py` | Pipe ID + velocity-limit table | DOC-0049 `SUPPORT` → `PipeType` |
| `data/fittings.py` | Minor-loss K-factors and component head-loss coefficients | DOC-0049 `H - TDH` |
| `data/drainage.py` | Gravity-drain pipe table (ID + Manning's n) | DOC-0049 `SUPPORT` → `GravityPipes` |
| `data/chemistry.py` | Ozone contact-tank catalog and water-balance target ranges | DOC-0049 `SUPPORT` → `ContactTanks`, DOC-0119 |

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
| `setup.py` | `after_migrate` — adds the pump-spec fields on Item that the pump selector reads |
| `setup_print_formats.py` | `after_migrate` — ships two server-rendered (Jinja) print formats over the persisted rollups and `calc_results` audit trail |
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
| `Nozzle Profile` | Nozzle catalog (orifice sizing input) |
| `Pump Curve Point` | Pump curve catalog points |
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

They are **golden tests**: each formula reproduces its sheet's own worked example. When you
change an engine function, the failing assertion is telling you the result no longer matches
the workbook — check the sheet before changing the expectation.
