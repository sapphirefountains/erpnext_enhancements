# `engine/electrical/` — electrical design

Phase L of the Fountain Designer rebuild (PLAN.md section 4, v1.758.0). Nikolas, 2026-11-01:
electrical calculations as a first-class deliverable. This package replaced the single module
`engine/electrical.py`; its four functions (`motor_flc`, `total_connected_load`,
`service_main_breaker`, `control_transformer_va`) keep their names and import paths.

Stdlib only, like all of `engine/`: nothing here imports `frappe`, and nothing imports
`engine.hydraulics`. Pump power and current come from the design model or the `pumps` argument.
`tests/test_electrical_report.py` checks both rules.

**Edition.** The tables in [`../data/nec_tables.py`](../data/nec_tables.py) are the **2023
NEC** (NFPA 70). Where the 2020 numbering differs it is noted beside the table. Every result
carries a warning to verify the edition Utah has adopted, because the inspector's edition
governs.

## The correction this package exists for

The engine used to call **125% of FLA** the motor *breaker*, citing 430.52. That is wrong.

- **125% is the conductor rule.** 430.22 sets motor branch conductors at 125% of the FLC.
- **The breaker is set by Table 430.52(C)(1).** The maximum is a percent of FLC that depends on the device. For an inverse-time breaker it is 250%.
- **Exception 1** (430.52(C)(1)) allows the next standard size up.
- **Exception 2** allows more when the motor will not start: 400% for an inverse-time breaker at 100 A or less.

`pump.electrical_load` now returns the 430.52 maximum, with the 125% figure as `inputs["conductor_min_a"]`. `service_main_breaker` seeds 430.62 from that maximum.

Example: a 5 HP, 230 V, single-phase pump.

| Item | Rule | Result |
|---|---|---|
| Full-load current | Table 430.248 | 28 A |
| Conductor | 430.22: 125% of 28 A = 35 A | **#8 Cu** at 60 °C terminations (#10 carries only 30 A at 60 °C) |
| Conductor with 75 °C terminations | Table 310.16, 75 °C column | #10 Cu |
| Breaker maximum | 250% of 28 A = 70 A | **70 A** |
| Breaker under the old rule | 125% of 28 A = 35 A | 35 A, which would often trip on start |

## Modules

| Module | What it does | NEC / source |
|---|---|---|
| `__init__.py` | Re-exports, and `electrical_report(model)`: the whole chain as one JSON-ready dict for `derive()` | — |
| `loads.py` | `build_loads(model, pumps=None)`: one load row per pump, aerator, light group, valve supply, heater, UV, chem feed, control panel, extra load. `motor_flc` and `demand_summary` | 430.248, 430.250, 430.6(A)(1); Article 100 "continuous load" |
| `ocpd.py` | `motor_branch_ocpd` (430.52 maximum by device, Exceptions 1 and 2), `continuous_ocpd` (non-motor at 125%) | 430.52(C)(1) and Table 430.52(C)(1), 210.20(A), 215.3, 240.6(A) |
| `conductors.py` | `size_conductor`, `wire_for_load` (ampacity, then the voltage-drop size, then the EGC), `egc_size`, `ampacity`, corrections | Table 310.16; Tables 310.15(B)(1) and (C)(1); 110.14(C); 240.4(B), (D) and (G); 430.22; 210.19(A)(1); 215.2(A)(1); Table 250.122 and 250.122(B); 680.21(A)(1) |
| `voltage_drop.py` | `voltage_drop` (single-phase and three-phase), `size_for_drop`, `total_drop` (3% / 5%), and `low_voltage_run` (12 V lighting, 24 V valve coils) | Chapter 9 Table 8; Informational Notes to 210.19(A) and 215.2(A)(1); Article 725 Class 2 and Chapter 9 Table 11(B); the rain curtain's `coilPlan` |
| `conduit.py` | `conduit_fill`, `conduit_for_circuit` for EMT, PVC Sch 40, PVC Sch 80 and RMC | Chapter 9 Tables 1, 4 and 5 |
| `circuits.py` | `branch_circuits`: one circuit per motor unit and per other load row, with OCPD, conductor, EGC, conduit, drop, poles and GFCI | Ties the modules above together |
| `feeders.py` | `feeder_load` (in VA, so mixed voltages add), `feeder_ocpd`, `service_main_breaker`, `total_connected_load`, `panel_schedule`, `service_check` | 430.24, 430.62(A), 430.63, 215.2, Article 220 |
| `transformers.py` | `lighting_transformer` (1.25 × fixture watts, standard sizes), `primary_ocpd_max`, `control_transformer_va` | 450.3(B); 680.23(A)(2); industry report section 1.11 |
| `article680.py` | `article680_checklist` and `gfci_requirement` | Article 680 Parts I, II and V (see below) |
| `vfd.py` | `select_vfd`: drive HP, input current, reactor and filter calls | 430.122, 430.130; drive-maker guidance, labeled as such |
| `backup.py` | `generator_size`, `battery_runtime`, `backup_plan` | Table 430.7(B) code letters; the rain curtain's `backupPlan` |
| `energy.py` | `annual_energy`: kWh and cost per load | Site rate. `workbook.electric_cost` stays the DOC-0049 pump figure |
| `bom.py` | `electrical_bom`: lines in phase B's `{section: "Electrical", key, description, qty, uom, item_hint}` shape. Wire and conduit carry 10% waste | 430.102, 430.110, 680.13 for disconnects |
| `drawings.py` | `oneline_state` and `electrical_oneline` | Feeds the extended `engine/drawings.electrical_oneline_svg` |

## How results are returned

- **Every public function returns a `CalcResult`.** It carries the formula, the steps, the citations and the warnings.
- **Scalar results** (a breaker, a size or a percent) are in `value`.
- **Plan-shaped results** are a list or dict in `value`. These are `build_loads`, `branch_circuits`, `low_voltage_run`, `backup_plan`, `panel_schedule`, `article680_checklist` and `electrical_bom`.
- **The drawing helpers return SVG strings**, like `engine/drawings.py`.

## The Article 680 checklist

Each check has a status of `pass`, `fail`, `verify` or `n/a`.

- **No data means `verify`.** A check the model has no data for never reports `pass`.
- **Distances come from `electrical.site_checks`.** These are the disconnect distance, receptacle distances, overhead clearance, junction-box heights and the wiring method.

| Check | Fountain (Part V) | Pool / spa (Part II) |
|---|---|---|
| GFCI on luminaires, submersibles and pumps | 680.51(A), 680.59 | 680.21(C), 680.23(A)(3) |
| Low-voltage contact limit (15 V AC, 30 V DC) | 680.2, 680.23(A)(2) | same |
| Luminaire circuits of 150 V or less | 680.51(B) | 680.23(A)(4) |
| Lens depth | 680.51(C): lens below the water | 680.23(A)(5): 18 in, or 4 in if listed |
| Cords | 680.51(E), 680.56 | 680.21(A)(3) |
| Servicing | 680.51(F) | 680.23(B)(6) |
| Junction boxes | 680.24, 680.52 | 680.24 |
| Bonding | 680.53 / 680.54 (numbering moved in 2020) | 680.26 equipotential bonding |
| Maintenance disconnect, 5 ft and within sight | 680.13 (680.12 before 2020) | same |
| Receptacles | 680.58 | 680.22(A) |
| Overhead clearance | 680.9(A) (applies to fountains? verify) | 680.9(A) |
| Wiring methods | Chapter 3, 680.14 | 680.21(A)(1), 680.23(F)(1) |
| Over 150 V to ground: special-purpose GFCI (SPGFCI) | 680.5 (2023) | same |

## Not settled yet

These are for the designers and the inspector. They are also listed in the PR.

- **The NEC edition Utah enforces**, and any amendments.
- **24 V DC fixtures and GFCI.** They are under the 30 V DC limit, but some inspectors still ask for GFCI.
- **Splash pads.** Does the jurisdiction apply Part V alone, or Part II as well?
- **The 680.53 / 680.54 bonding numbering** in the adopted edition.
- **Overhead clearances.** Do they apply to fountains?
