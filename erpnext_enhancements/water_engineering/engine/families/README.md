# `engine/families/`: feature-family rules for the Fountain Designer

Phase C of the Fountain Designer rebuild (`docs/fountain-designer/PLAN.md`, section 3.1). One
module per kind of water feature, each turning the designer's inputs into a design flow, a
list of typed checks and the terminal the hydraulics layer solves against. The rules come
from the industry research (`docs/fountain-designer/research-industry-calculators.md`,
section 1); every rule cites its source URL in `citations`. A rule with no public source
reads a **Sapphire house rule** (`engine/rules.py`, mirrored on Water Engineering Settings)
and cites "Sapphire standard, 2026-11-01 (placeholder until the designers set it)".

Stdlib-only, like all of `engine/`. Nothing here imports `frappe` or `engine.hydraulics`.

Not wired yet: `run_spine`, `issues.py` and the Water Feature Design controller do not call
these modules. Phase E does that.

## The interface

### Feature families (`FAMILIES`)

| Function | Returns |
|---|---|
| `inputs()` | The input schema the page renders: a list of `{name, label, kind, unit, default, required, help}` (plus `options` for a select and `columns` for a table). `kind` is `number`, `integer`, `select`, `bool`, `text` or `table`. |
| `flow(feature, site=None, rules=None)` | A `FamilyResult`: a `CalcResult` (value, unit, inputs, formula, steps, citations, warnings, options) plus `data`, the structured numbers later steps read. `data["gpm"]` is the design flow (`None` when it cannot be known yet); `data["complete"]` says whether enough was entered. |
| `checks(feature, derived=None, site=None, rules=None)` | A list of `Check(code, severity, title, section, detail, remedy, citation, feature, value, limit, unit)`. Severity is `blocker`, `warning` or `info` (the `issues.py` vocabulary) or `pass` (an item on a pass/fail list). `section` is a schema-2 setup section key, the page section to jump to. |
| `terminal(feature, derived=None)` | `{"kind", "params"}` for the terminal layer: `open_discharge` (`lift_ft`, `design_flow_gpm`, `residual_head_ft`), `orifice` (`flow_gpm` at `head_ft`, the table `curve` of (head, flow) points, and `cd` + `area_in2` when known), `manifold` (`takeoffs`, `spread_target_pct`; interactive adds `sequence`), `fixed_flow` (`flow_gpm`; not on the pumped network). |

`feature` is a schema-2 feature dict: `{id, family, type, label, inputs, effect, product,
count, basin, node}`. `base.values()` merges `effect` over `inputs` over the input defaults.
`product` carries the picked catalog record as plain dicts: `product["points"]` (or
`product["profile"]["points"]`) are Nozzle Performance Points
(`head_ft` or `psi`, `flow_gpm`, `display_height_ft`, `display_diameter_ft`, `throw_ft`,
`depth_over_nozzle_in`), and `product["profile"]` holds the profile's own fields
(`rated_gpm`, `rated_head_ft`, `cd`, `orifice_diameter_in`, `min_depth_in`, `max_depth_in`,
`level_sensitive`, `nozzle_type`, `source_url`).

`derived` is what the orchestrator already knows. Every family reads `derived["flow"]` (its
own `flow().data`, computed when absent) and `derived["basin"]` (the schema-2 basin the
feature lands in). Extra keys, per family: weir `trough_volume_gal`; jets and foam
`manifold_spread_pct`; interactive `tank_gal`; cascade `pipe_volume_gal`.

`families.run_feature(feature, site, rules, derived)` runs all three and returns JSON-ready
dicts; `families.catalog()` is the families-and-input-schemas list for the page's `boot()`.

### System modules (`SYSTEM_MODULES`)

Lighting, winterization and heat read the whole model, so they expose `inputs()`,
`calc(model, derived=None, rules=None) -> FamilyResult` and
`checks(model, derived=None, rules=None) -> list[Check]`. `model` is the schema-2 dict.
Lighting reads `derived["flows"]` (feature id to `flow().data`); heat reads
`model["treatment"]["heat"]`.

## The families

### `weir.py`: weirs, sheet falls, waterwalls, vanishing edges, slot overflows (report 1.1)

- **Inputs:** style (waterwall, spillway, vanishing edge, slot overflow, sheet product), edge length, lip shape, look (light, average, heavy), head over the lip (optional pin), edge tolerance, end contractions, fall height, catch width, lip texture, free fall, indoor, wind control, sheet product and its gpm/ft, upper pool area, raised spa volume, busy pool, trough area.
- **Rules:**
  - Francis, through the kept `feature.weir_flow` (Q = 36·L·h^1.5 − 0.3·n·h^2.5): ¼ in ≈ 4.5 gpm/ft. [WaterShapes, Over the Edge](https://watershapes.com/over-the-edge/)
  - Square-edge planning flows: light 7 and average 10 gpm/ft at ¼ in (the two ends of the 7–10 band), heavy 36 at 1 in. Francis is the floor. [Pool & Spa Marketing](https://www.poolspamarketing.com/trade/water-feature-hydraulics/2/)
  - Vanishing edge ≈ 5 gpm/ft; slot overflow ≈ 2. [WaterShapes](https://watershapes.com/over-the-edge/)
  - Sheet-fall products: Jandy Sheer Descent ≈ 12 gpm/ft, MagicFalls 8–10. [epoolsupply](https://www.epoolsupply.com/blogs/default-blog/jandy-sheer-descent-sheer-rain-water-features-comprehensive-guide) [snippet]
  - Edge tolerance → head: head = 2 × tolerance; a rounded crest ≥ 1 in. [Pool & Spa Marketing](https://www.poolspamarketing.com/trade/water-feature-hydraulics/2/)
  - Build tolerance ±1/16 in; 20 ft minimum vanishing edge. [WaterShapes, Water in Transit](https://watershapes.com/water-in-transit/)
  - Sheet breakup past 24 in of drop on a smooth lip. [Blue Thumb](https://pondprodirect.com/products/blue-thumb-stainless-medium-formal-falls-28)
  - Landing distance: fitted through P&S's two points at a 1 m fall (6 mm → 0.3 m, 25 mm → 0.7 m), scaled with √fall; sheet thickness ≈ head. Catch width ≈ the fall, 1:1. [WaterShapes 2024](https://watershapes.com/?p=49059)
  - Vanishing-edge trough: 2 in over the upper surface (3 in for a small busy pool) + raised spa + 1 ft vortex reserve. [WaterShapes](https://watershapes.com/water-in-transit/)
  - Wind control on sheets over 3 ft, at the house-rule setpoints.
- **Checks:** head below what the tolerance needs; flow below the look band (pinned head); tolerance worse than 1/16 in; vanishing edge under 20 ft; smooth sheet over 24 in; sheet over 3 ft with no wind control; sheet lands outside the catch basin (blocker); catch narrower than the fall; trough below the surge rule. Waterwalls (water down a wall face) skip the free-sheet checks, and indoor features skip wind.
- **Terminal:** `open_discharge`, lift = fall height.

### `jets.py`: single orifices, arrays, rings, geysers (report 1.2, audit 1.8)

- **Inputs:** display height, nozzle type, launch angle, head (optional pin), orifice diameter, distance to the nearest edge, basin (ground or raised), design wind, wind control, indoor, body contact, ring diameter, spacing.
- **Rules:**
  - **Height from the nozzle's performance table** by linear interpolation (height → head, flow). Outside the table is a blocker, never an extrapolation. [OASE tables](https://cdn.shopify.com/s/files/1/2394/4493/files/Oase_Geyser_Nozzles_Specifications_Sheet_PDF.pdf)
  - Flagged fallback only: height = k × head, k 0.82 for a smooth bore ([Scribd](https://www.scribd.com/document/462312854/elementry-pdf) [weak]) and 0.39 for every other type, from Atlantic's column nozzle, 12 ft at 31 ft of head ([Atlantic](https://practicalgardenponds.com/products/atlantic-water-gardens-vertical-nozzles)). The engine's old 0.90 is not used.
  - 2-D point-mass trajectory with quadratic drag and wind, RK4. Drop size 1.89 × jet diameter (Rayleigh, as the rain-curtain app), sphere Cd 0.47, ISA air density at the site elevation. Calibrated so the still-air apex equals the table height; it then gives range, landing point and wind drift.
  - Splash setback: 1:1 of the height for ground pools, 1:1.5 for raised tables. [Safe-Rain](https://saferain.com/en/blog/tips-of-fountain-design-part-2/), [Delta](https://www.deltafountains.com/?p=772), [Crystal](https://www.slideshare.net/slideshow/water-feature-types/5145948)
  - Manifold spread target ±2%. [Safe-Rain](https://saferain.com/en/blog/tips-of-fountain-design-part-2/); the rain-curtain app's `sizeSweep`
  - Exit velocity ≤ 20 ft/s where people touch the water. [Utah R392-302-41](https://www.law.cornell.edu/regulations/utah/Utah-Admin-Code-R392-302-41)
- **Checks:** nozzle cannot make the height (blocker); height estimated from a constant; flow unknown; edge inside the splash radius; wind drift past the edge (info if wind control is fitted); manifold spread target (info) or over 2%; body-contact velocity over 20 ft/s (blocker).
- **Terminal:** `orifice` for one nozzle, `manifold` for several.

### `foam.py`: foam and aerating jets, geysers (report 1.3)

- **Inputs:** the jet inputs plus water over the nozzle, level-sensitive, check valve, drain-down accepted, autofill deadband.
- **Rules:** sized as an aerating jet (`jets`). Submergence band from the cut sheet (`min_depth_in`, `max_depth_in`), else the house rule. Level sensitivity: [OASE Cascade](https://www.oase.com/cs-CZ/fountain-technology/cascade-50-t-npt), [Crystal WMO](https://www.pool360.com/Product/1-wmo-series-shallow-water-foam-jet-pool-cft-27-210).
- **Checks:** the jet checks; band is the house rule (info); depth outside the band; no check valve and no accepted drain-down; autofill deadband wider than the band.

### `laminar.py`: laminar arcs (report 1.4)

- **Inputs:** arc height, distance to the edge beyond the landing, design wind, filtered.
- **Rules:** the product's table, or the CMP deck jet's two points (2.5 gpm @ 3.8 psi → 2 ft × 3 ft; 3.5 @ 6.5 → 4 × 6) [CMP](https://www.poolsupplyunlimited.com/Products/Manuals/169670_20187311590.pdf) [snippet]. Launch angle and speed from the arc's height and throw; the drag trajectory gives the landing in wind.
- **Checks:** outside the table (blocker); generic table; lands outside the basin (blocker); wind carries it past the edge; no filtration ahead.

### `interactive.py`: splash pads and dry-deck plazas (report 1.5)

- **Inputs:** play features (label, gpm, count, orifice, ground opening, line length, fogger), sequence steps, system volume, collector tank, filter flow, inlets, deck width and slope, drain grates, ORP/pH controller, secondary disinfection, separate loop, level controller, potable foggers, night light.
- **Rules:** feature pump = the worst sequence step, not the sum ([Vortex](https://www.vortex-intl.com/blog/how-to-design-low-impact-splashpad): 30–50% saving). Utah R392-302-41 ([text](https://www.law.cornell.edu/regulations/utah/Utah-Admin-Code-R392-302-41)): 30-minute turnover, ≥ 4 inlets, tank ≥ 3 minutes of the combined pump flow, nozzle velocity ≤ 20 ft/s, openings ≤ ½ in, drains stop a ½ in sphere, 3 ft deck, 2% slope away, ORP/pH control, secondary disinfection, separate treatment loop, level controller, potable foggers, 5 fc at night. MAHC feature-to-filter flow ≤ 3:1 ([CDC/NACCHO](https://stacks.cdc.gov/view/cdc/153389/cdc_153389_DS1.pdf) [snippet]).
- **Checks:** the Utah list as pass/fail: `pass`, or `blocker` in Utah (`warning` elsewhere); an unanswered item is `info`, never a silent pass. The 3:1 ratio fails as a warning. Lines over 30 ft are info.
- **Terminal:** `manifold`, one takeoff per play feature, plus the sequence.

### `cascade.py`: cascades, tiers, streams, ponds (report 1.6)

- **Inputs:** tiers (spillway width or rim diameter, look, pool area above the lip, fall, optional rim gpm/ft), default look, stream length/width/depth, pond volume, koi, pond size (for the liner), top spillway elevation.
- **Rules:**
  - Spillways: light 1,500 GPH/ft (= 125 GPH per inch), average 2,400 (200 GPH per inch for a 1 in sheet), heavy 3,000. [Aquascape](https://support.aquascapeinc.com/hc/en-us/articles/48506913796244-Waterfall-What-flow-rate-is-needed-for-a-waterfall), [Pond Trade](https://www.pondtrademag.com/?p=17369) [snippet]
  - Round tiers keep the DOC-0049 B rim rule `feature.tiered_fountain_flow` uses: 0.5 / 4 / 6 gpm per foot of rim.
  - The tightest tier governs (series flow).
  - Drain-back at shutdown: the sheet over each lip plus the stream volume. [WaterShapes](https://watershapes.com/water-in-transit-pond-problem/)
  - Pond turnover every 2 h (koi: 1 h). [Aquascape](https://manualslib.fr/manual/724172/Aquascape-Aquaforce-1000-Gph.html?page=4) [snippet]
  - Liner L + 2·depth + overlap (house rule). Rills ≤ 12 in (Crystal).
- **Checks:** lowest basin overflows at shutdown (blocker); freeboard not set (info with the house-rule freeboard); slow pond turnover; rill width; which tier governs.
- **Terminal:** `open_discharge`, lift = the top spillway's elevation.

### `pondless.py`: pondless waterfalls, urns, bubblers (report 1.7)

- **Inputs:** kit (stream, urn, bubbler), spillway width, stream length/width/standing depth, urn flow, drain-back volume (urns), reservoir fill, reservoir as designed.
- **Rules:** stream flow 125 GPH per inch; stream volume W × L × D × 7.48; reservoir ≥ 3× it open ([Best Planet](https://best-planet.com/pages/pump-sizing-for-pondless-systems)), 4× with water-matrix blocks, 12× gravel-filled ([Pond Trade](https://www.pondtrademag.com/sizing-gravel-filled-and-water-matrix-reservoirs-for-pondless-features/)). Kit lines: pump, reservoir storage, check valve, discharge pipe (length from the piping step), spillway, liner.
- **Checks:** reservoir smaller than its fill's multiple.

### `curtain.py`: passive rain and lace curtains, stream screens (report 1.8)

- **Inputs:** style (strand, lace, screen, double screen, digital), width, strand count or spacing, hang angle, softened water.
- **Rules:** 0.33 gpm per strand ([WaterShapes](https://watershapes.com/?p=27303)); lace 0.5 L/min per strand, ≥ 35 mm apart ([water-garden.co.uk](https://www.water-garden.co.uk/prod/mylar-lace)); screens 0.5 gpm/ft, double 1 ([WaterShapes](https://watershapes.com/?p=32451)); lace ≤ 20° off vertical. **A digital curtain only links to the Digital Rain Curtain app** (`sapphirefountains/digital-rain-curtain`, `dist/digital-rain-curtain.html`) and adds no flow.
- **Checks:** digital curtain (info, link); lace spacing; hang angle; hard water over 180 ppm without softened or RO water ([USGS](https://www.usgs.gov/special-topics/water-science-school/science/hardness-water)).

### `mist.py`: high-pressure mist and fog (report 1.9)

- **Inputs:** nozzle count, orifice (0.008 or 0.012 in), gpm per nozzle, operating pressure, supply, line rating.
- **Rules:** count × gpm per nozzle (0.020 / 0.028 gpm at ~1,000 psi) ([Advanced Misting](https://www.advancedmistingsystems.com/stainless-steel-10-24-anti-drip-cleanable-nozzle/) [weak fit]); potable supply ([Utah R392-302-41](https://www.law.cornell.edu/regulations/utah/Utah-Admin-Code-R392-302-41)).
- **Checks:** fed from the reservoir (blocker in Utah); line rated below the pump pressure (blocker); rating not entered; 0.008 in in a dry climate.
- **Terminal:** `fixed_flow`, a separate potable system.

### `aerator.py`: floating aerators and fountains (report 1.10)

- **Inputs:** surface acres (or length × width), depth, purpose, units, HP per unit, pattern diameter, shoreline setback, distance to shore power.
- **Rules:** HP per acre: aeration 1, display 2, circulation ¾ ([Kasco](https://cdn.shopify.com/s/files/1/2394/4493/files/Kasco_VFX_AF_JF_Fountain_Sizing_Chart_PDF.pdf)); water quality 2 ([Otterbine](https://sweets.construction.com/swts_content_files/49654/P302633.htm) [snippet]); several units on long narrow ponds ([MSU](https://archive.lib.msu.edu/tic/wetrt/article/1986jul54.pdf)), split at the house-rule length-to-width ratio. Cable run = shore power + half the narrow side, for the electrical phase.
- **Checks:** HP below the band; pattern reaches the shoreline setback; one unit on a long pond; cable run (info).
- **Terminal:** `fixed_flow`, self-contained. `flow()` returns horsepower (unit `HP`).

### System modules

- **`lighting.py`** (report 1.11): ≥ 2 fixtures on jets over 4 ft; narrow spot (~13°) over 10 ft, wide flood (~44°) under; transformer VA = watts × 1.25 ([Crystal](https://www.slideshare.net/slideshow/lighting-5145934/5145934)); lumens for 300 lux at the jet top ([Safe-Rain](https://saferain.com/en/faq)); NEC 680 Part V GFCI unless listed at ≤ 15 V AC / 30 V DC, luminaire circuits ≤ 150 V ([IAEI](https://iaeimagazine.org/2013/septemberoctober-2013/fountains-and-reflection-pools/)). Watts per foot of jet and weir and the default fixture size are house rules, used until fixtures are picked. Checks: over 150 V (blocker), GFCI, confirm GFCI on 24 V DC, transformer under 1.25×, sized from house rules.
- **`winterization.py`** (report 1.12): a winterize list and a spring start-up list from the pumps, segments, features and lights. Frost depth 30 in in Salt Lake City ([SLC](https://www.slc.gov/buildingservices/wp-content/uploads/sites/9/2026/07/SLC-Design-Criteria-2024-IBC_IRC.pdf)); blow-out ≤ 50 psi, ≤ 2 min, never through a pump ([Rain Bird](https://rainbird.com/media/1332)); splash pads: pull nozzles, fit caps ([Vortex](https://www.vortex-intl.com/blog/winterize-water-play-system/)). Reads optional segment keys `drain`, `slopes_to_drain`, `buried_depth_in` and pump keys `location`, `heated`. Checks: lines above frost with no drain; drainage not recorded; unheated equipment room.
- **`heat.py`** (audit 1.14): surface heat balance: Carrier evaporation (ASHRAE Applications ch. 6, I-P form), McAdams convection, Swinbank sky radiation. `treatment.heating_load` (DOC-0049) is unchanged and reported beside it. Check: the two disagree by more than 2×.

## House rules these modules read

All in `engine/rules.py` and on Water Engineering Settings (House Rules tab). Labor hours ship
as 0, meaning not set.

| Key | Default | Read by |
|---|---|---|
| `foam_submergence_min_in`, `foam_submergence_max_in` | 2, 4 in | foam |
| `autofill_deadband_in` | 0.5 in | foam |
| `freeboard_multiplier` | 1.3 | cascade (and the basin step in phase B) |
| `liner_overlap_ft` | 2 ft | cascade, pondless |
| `light_watts_per_ft_jet`, `light_watts_per_ft_weir`, `light_fixture_watts` | 5 W/ft, 4 W/ft, 35 W | lighting |
| `wind_reduce_mph`, `wind_stop_mph` | 12, 20 mph | weir, jets, laminar |
| `aerator_max_aspect` | 3 | aerator |
| `spray_loss_pct_<family>` | 0.5% each | phase B/E autofill and O&M |
| `labor_hours_<family>`, `labor_hours_per_sqft_basin`, `labor_hours_per_light_fixture` | 0 (not set) | phase G budget |
| `kwh_rate`, `water_rate_per_ccf` | $0.1049, $3.53 | phase B/G O&M (sourced: RMP, SLC) |

## Tests

`tests/test_families_weir.py`, `test_families_jets.py`, `test_families_others.py`,
`test_water_model.py`, `test_water_house_rules.py`: bench-free pytest, one CI step. The
expected values are the report's sourced figures and closed-form physics, not the code's own
output.
