# 0018. Rebuild water engineering as the Fountain Designer: one JSON design model, a guided page, the engine kept

- **Status:** Accepted
- **Date:** 2026-11-01 (decided); v1.771.0 (the last phase, H, retires what it replaced)

## Context

The `water_engineering/` module was built between June and September 2026 (v1.91 to v1.362): a
stdlib-only engine golden-tested against Sapphire's own workbooks (DOC-0048, DOC-0049), a submittable
**Water Feature Design** record with nine child tables (Water Feature Basin, Tier, Nozzle, Pipe
Segment, Pump, Electrical Load, Equipment, Calc Result and Water Venue Fixture), a Desk form, the
**Water Engineering Wizard** page, print formats, a submittal packet and three AI tools.

It was barely used. On 2026-11-01 production held one design record (WFD-2026-209315), created by
Nikolas; the design team never adopted the form. The engine was the trustworthy part, and the flow
was the part nobody used. The nine child tables were the wrong shape for what a design is: a
network of features, basins, pipes and pumps whose sections are either worked out or the
designer's own, which rows in nine flat tables could not say. The Digital Rain Curtain app and
aquafect already showed the shape people did use, for one feature type: presets, automatic sizing,
a verdict, a BOM.

Two research reports (`docs/fountain-designer/research-engine-audit.md` and
`research-industry-calculators.md`) and a plan (`docs/fountain-designer/PLAN.md`, PR #1389) preceded
the build. Options weighed: refactor the Desk form (rejected: the form is what nobody used); rewrite
the engine too (rejected: it is golden-tested and feeds the AI tools); a new guided page over the kept
engine (chosen).

## Decision

Nikolas decided on 2026-11-01 (the vault's `decisions/2026-11-01-fountain-designer-rebuild.md` is the
full list):

1. **Two modes.** Quick, for sales: a kit, a few inputs, an auto-sized system and a budget. Full, for
   the design team: every section open, equipment picks, schedules, the BOM to a Quotation or
   Material Request, the submittal packet. Any design moves between them.
2. **A new guided page over the kept engine.** `/desk/fountain-designer`, step by step with live
   results. The design is one JSON document, the **schema-2 model** in `model_json`, with each
   section `auto` (worked out) or `custom` (the designer's). It replaces the nine child DocTypes;
   **Water Feature Design** keeps its name and becomes the model plus the derive's stored results,
   rollups and one child table, the BOM (**Water Feature Design Line**).
3. **All venues stay in scope:** decorative fountains, interactive and show features, regulated pools
   and spas (the Fika path), rentals, ponds and aeration.
4. **Catalogs from both sources:** digitized public manufacturer data, cited per row and seeded only
   once approved (Nikolas approved all 380 cited rows), and the designers' own entries.
5. **Python in ERPNext is the canonical engine.** Aquafect becomes the simulator and renderer that
   reads the design JSON.
6. **Physics governs:** Darcy-Weisbach with Darby 3-K fittings is the governing loss, with the
   workbook's Hazen-Williams beside it as the cross-check; safety checks run at the real operating
   flow; NEC 430.52 breaker maxima replace the 125%-of-FLA rule. Multi-pump headers and ring mains
   are in the first version (a network solver).
7. **Electrical and structural are deliverables of their own** (phases L and S), widened the same
   day to the controller and pump control scheme, and to the structure in general.
8. **The old wizard and the nine child DocTypes retire in the last phase**, in the two steps the
   repo requires (the files, and a `post_model_sync` patch that never raises), and the AI tools move
   onto the new API.

How it was built: one session per phase, one draft PR each, a review session per PR, the manager
merging in order (tracker TASK-2026-03018 on PRJ-00580).

| Phase | What | PR | Version |
|---|---|---|---|
| plan | research reports and `PLAN.md` | #1389 | 1.757.1 |
| D | catalog schema (Pump Curve, Nozzle Profile points, Item provenance) and the approved-seed loader | #1390 | 1.758.0 |
| C | feature families, kits, the schema-2 model, house rules | #1393 | 1.759.0 |
| A | hydraulics core: water, Darcy, fittings, segments, pump curves, terminals, the operating point | #1391 | 1.760.0 |
| F | the Fountain Designer page, against a stub contract | #1392 | 1.761.0 |
| L | electrical (NEC 2023), then controls and panel design | #1395, #1397 | 1.763.0, 1.764.0 |
| S | basin and vault shells, then the structure in general | #1394, #1396 | 1.765.0, 1.766.0 |
| B | network solver, surge, basin checks, autosize, BOM, upkeep | #1398 | 1.767.0 |
| E | the record rebuilt on `model_json`, `derive.py`, `api/designer.py`, then the charts | #1399, #1400 | 1.768.0, 1.769.0 |
| G | seven print formats, Send design, the packet on the new model | #1401 | 1.770.0 |
| H | retirement and re-pointing (this record) | this release | 1.771.0 |

What phase H retired, and why each could go:

- **The nine child DocTypes.** The parent stopped declaring them in v1.768.0, when
  `convert_water_feature_designs_to_model` copied each design's rows from five of them (Basin, Nozzle,
  Tier, Pipe Segment, Pump) into `model_json`; Calc Result was the old engine's output, and Electrical
  Load, Venue Fixture and Equipment were never read. No print, page or tool read any of them after
  phase G. `retire_water_feature_child_doctypes` deletes rows only where they are copied or derived
  (schema-2 designs, or designs that no longer exist, in those six tables), drops each table once empty
  (frappe's `delete_doc` never drops one), removes the DocType only once its table is gone, and keeps
  every other row with its table and DocType, named in the Error Log. Production had 13 rows, all of
  the schema-2 WFD-2026-209315 and all in the six tables, so all nine went.
- **The Water Engineering Wizard.** The page replaced it; its quick calculator is the Calculator
  drawer on the Review step (`water_engineering/calculator.py`, the same dispatch). The Page row goes,
  the workspaces are re-imported, a leftover sidebar row is repointed, and `frappe.re_route` sends
  `/desk/water-engineering-wizard` to the Fountain Designer so Back still works.
- **The old API** (`api/water_design.py`): its design reader, schema-1 save, issue reader and
  candidate lists had no caller left. The Control Panel Design reader stays; the endpoints that moved
  to `api/designer.py` are re-exported there, deprecated, for a Desk tab on an older bundle.
- **The schema-1 issue producers** (`issues.build_issues`, `build_readiness`) and the
  `we_fitting_schedule` Jinja method.

The AI tools now read and write what the page does: `water_design_status` returns the stored verdict,
rollups, issues, readiness and links; `save_water_design` saves through `save_design` from a schema-2
model or the old tables (merged, pinned `custom`, with what it ignored named); `water_calc` lists
exactly the calculator's calcs.

## Consequences

- **One storage model.** A design is `model_json`, and everything on the record beside it is derived
  on save. A new design input is a model field (PLAN.md 3.3, `engine/model.py`), never a child table.
  A section a person edits is `custom` and no derive overwrites it.
- **One engine behind every surface.** The page, the Desk save, the prints and the AI tools call
  `derive.derive_model` and `calculator.run`; the engine, `derive.py`, `issues.py`, `calculator.py` and
  `prints.py` import no frappe and stay bench-free tested.
- **The retirement is guarded.** `tests/test_water_engineering_retirement.py` fails the build if any
  code path names a retired DocType or the wizard again, and runs both patches dry against a
  production-shaped site.
- **What it costs.** Schema-1 designs can no longer be opened anywhere but as a blocker on the record
  (none exist on production). The old `save_water_design` payload still works, but only the tables
  with a place in the model survive it. Triton's prompt still describes the wizard flow by tool name,
  which is unchanged.
- **Revisit** if a second storage shape is ever wanted (for example, a design shared with Aquafect
  as something other than the model JSON), or if the designers adopt the Desk form instead of the
  page.
