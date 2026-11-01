# `engine/structural/`: concrete, rebar and site quantities for basins and vaults

Phase S of the Fountain Designer rebuild (ladder step 10b in
`docs/fountain-designer/PLAN.md`). It answers one question: **how much concrete, rebar, gravel and
waterproofing does a basin of this size need, and does it pass the basic checks?**

> Engineering aid for quantities and early checks, not a stamped structural design. Every
> `CalcResult` this package returns carries that sentence in its `warnings`.

Stdlib only, never imports frappe, 4-space indentation like the rest of `engine/`. It reads basins
as plain dicts in the PLAN.md §3.3 shape and does not import `engine.model`, `engine.families` or
`engine.hydraulics`.

| File | Does |
|---|---|
| `defaults.py` | `DEFAULT_RULES` (every house rule the calcs read), `RULE_SOURCES` (where each comes from), `merged_rules(rules)`, the citation strings and `DISCLAIMER` |
| `geometry.py` | `basin_spec` (flattens a model basin + site soil facts), `basin_shell` (areas, lengths, corners, water, concrete volume per element, grade levels) |
| `sizing.py` | `select_thickness` (slab and wall from the standard details), `hydrostatic_pressure`, `wall_bending_check`, `cover_check`, `shrinkage_temperature_check`, `uplift_check`, `bearing_check`, `floor_load` |
| `rebar.py` | `development_length`, `lap_length`, `hook_geometry`, `stock_sticks`, `rebar_schedule` |
| `concrete.py` | `concrete_volumes`, `mix_notes`, `gravel_base`, `excavation`, `formwork`, `joints`, `coating_area`, `penetrations`, `anchor_bolts` |
| `takeoff.py` | `structural_takeoff(model, rules)`: the whole chain for every basin and vault, one dict for phase E |
| `../data/rebar.py` | ASTM A615 bar diameters, areas and weights for #3 to #8; 20 and 40 ft stock |

## 1. What DOC-0119 states

DOC-0119 *Fountain Design Guidelines* (Word document, company Drive) was read in full on
2026-11-01. Its structural content sits under **Concrete Design**, **Equipment Rooms > Concrete
Penetrations**, **Waterproofing** and **Plumbing & Piping Design > Pipe Imbedding**. The rules below
are paraphrased; the document itself stays in Drive.

| # | Rule (paraphrased) | DOC-0119 section | Where the engine uses it |
|---|---|---|---|
| D1 | Two routes: a structural engineer's analysis (worth it on large, costly projects) or the "Big & Beefy" details, which are stout but not optimized and need no analysis | Concrete Design; Structural Analysis; Big & Beefy | `select_thickness` flags a basin beyond the details with `STRUCT_BEYOND_STANDARD` and sends it to an engineer |
| D2 | Keep at least 2 in of concrete between any outside face and anything embedded (rebar, pipe, waterstop) | Big & Beefy; Rebar | `cover_in` = 2; `cover_check`; bar lengths stop 2 in short of each face |
| D3 | Stem walls 4 to 6 in thick, tied into the floor with U-shaped bars | Big & Beefy | `stem_wall_min_in` / `stem_wall_max_in`; wall verticals are L-bars into the floor mat |
| D4 | Floors (slabs) 8 in thick | Big & Beefy | `slab_in` = 8 |
| D5 | Rebar as a tied grid on 12 to 18 in centers | Big & Beefy; Rebar | spacing range |
| D6 | Wall table: 10 in wall, #4 at 12 in; 8 in wall, #4 at 16 in | Rebar (table) | `wall_details`; `detail_for_wall` |
| D7 | Wall bar legs tie into the floor mat 24 in (figure caption: 8 in wall on a 10 in floor) | Rebar (figure) | `dowel_leg_in` = 24 |
| D8 | #4 carbon-steel bar on 18 in centers suits most Sapphire projects; epoxy-coated bar is not preferred because damaged coating traps moisture | Rebar | `slab_bar` / `slab_spacing_in`, `general_spacing_in`; uncoated bar in the development length (ψe = 1) |
| D9 | Quoting ACI 332: at least one horizontal bar in the top 24 in and one in the bottom 24 in of any wall; in seismic areas two #4 in the top 12 in | Rebar | horizontal rows are `ceil(run/s)+1`, which always puts a bar at each end of the run |
| D10 | A 12 in drainage layer of 1 in minus gravel under the slab; road base also works | Drainage Layer | `gravel_in` = 12; `gravel_base` |
| D11 | Concrete that holds water gets a crystalline waterproofing admixture (Xypex Admix C-Series, Penetron Admix) | Admixtures | `mix_notes`; BOM line `structural.admixture` on basins (not dry vaults) |
| D12 | Every cold joint gets a waterstop; hydrophilic strip, PVC ribbed, crystalline slurry and injection hose compared; drawing note calls for a rubberized-asphalt strip (Mirastop) or Henry Synkoflex SF302 | Waterstop | `joints`: waterstop at the wall-to-slab joint and every movement joint |
| D13 | The waterstop goes on the water side of the rebar so seepage meets it before the steel | Waterstop | warning text on `joints` |
| D14 | Pipes through equipment-room or surge-tank walls are sealed: stainless Link-Seal in a cored hole (or a cast-in flanged fitting), and non-shrink grout over the outside face | Equipment Rooms > Concrete Penetrations | `penetrations`; BOM `structural.link_seal.<size>` |
| D15 | Liquid-applied membranes on the water side (Gacoflex LM60, CIM 1000, Hydro Ban, AMES Blue Max, Aqua Proof, epoxy); broadcast sand into the last coat where tile or mortar goes over it | Waterproofing; Tiles > Bonding Mortar to Waterproofing | `coating_area` (inside walls + floor) |
| D16 | Polyurethane sealant (Dymonic 100) at expansion joints, coping and perimeter joints, chased cracks and pipe penetrations | Waterproofing > Joints Cracks & Gaps | `joints.sealant_ft` (inside floor-to-wall cove + movement joints + a vault lid joint) |
| D17 | Bed pipe in a 6 in saddle of sand or pea gravel, cover with the same, hand-tamp only | Pipe Imbedding | not computed here (trench bedding belongs with the piping BOM) |
| D18 | Balancing tanks may be poured concrete vaults or prefabricated units | Balancing Tanks | vaults are basins with `kind: "vault"` |
| D19 | Bond equipment with #8 solid copper | Electrical Design | phase L, not here |

**Where DOC-0119 contradicts itself or the code it cites.** Worth a conversation with the designers:

1. **D3 vs D6/D7.** "Stem walls 4 to 6 in" sits beside a wall table that starts at 8 in and a
   figure of an 8 in wall. The engine uses the table (8 in default) and keeps 4 to 6 in as the
   stem-wall range.
2. **A 4 in wall cannot meet D2.** Two crossing #4 bars plus 2 in cover on each face need 5 in;
   `cover_check` reports `STRUCT_COVER_WALL` on anything thinner.
3. **D4 vs the golden.** DOC-0119 says 8 in floors; the figure shows a 10 in floor; Nikolas's
   example basin uses a 6 in slab. The engine defaults to 8 in and takes a basin's own `slab_in`.
4. **The details are lighter than ACI 350.** #4 at 16 to 18 in in an 8 in section gives about
   0.13 to 0.15 in²/ft against ACI 350's 0.003 × gross area (0.29 in²/ft) and its 12 in maximum
   spacing for liquid-retaining concrete. DOC-0119 governs, so this is an `info` check
   (`STRUCT_ACI350_ST`), not a failure.
5. **2 in cover vs ACI 318.** ACI 318 asks 3 in where concrete is cast against and permanently
   exposed to earth. DOC-0119's 2 in is used everywhere; a slab cast on gravel is the case to
   confirm.

## 2. Rules DOC-0119 does not state (set these as house rules)

Each is a key in `DEFAULT_RULES`, editable through the `rules` dict (phase C's Settings will feed it).

| Rule | Default | Where the default comes from |
|---|---|---|
| Wall thickness by depth or height | thinnest table detail ≥ 8 in passing the checks; 12 in at 10 ft and taller | ACI 350 §14.6.2 for tall walls; the hydrostatic check otherwise |
| Footings under raised or tall walls; frost depth | none (the slab is the footing) | DOC-0119 silent; raised walls stand on the slab |
| Slab thickness by size, and two mats | 8 in; two mats only over 10 in | ACI 318 §11.7.2.3 (walls); slabs follow it here |
| Concrete strength, w/c, air | 4,500 psi, 0.45, 6% ± 1.5% | ACI 318 exposure F2; ACI 350 §4.2 |
| Lap splice | ACI Class B (1.3 ld), 24 in for #4 at 4,500 psi; `lap_rule: "40db"` gives 20 in | ACI 318 §25.5.2 / §25.4.2.3 |
| Corner bars | one L per row per corner, legs one lap each | ACI 350 / CRSI detailing practice |
| Movement joint spacing | 30 ft | ACI 224.3R and ACI 350's joint bands |
| Freeboard (for water volume) | 2 in | house rule (phase C owns the freeboard rule) |
| Raised floor of a stepped basin (`step_ledge`) | `concrete` to the step height on the deep slab; `gravel` gives a slab on compacted gravel plus riser walls | house rule |
| Concrete waste, order increment, truck | 10%, 0.25 yd³, 10 yd³ | house rule; ASTM C94 trucks |
| Gravel density and waste | 1.4 ton/yd³, 10% | house rule |
| Excavation working room, swell, slopes | 2 ft each side, 25% swell, vertical under 5 ft, 1.5H:1V deeper (or shored) | OSHA Subpart P; house rule |
| Lateral soil pressure on empty basins and vaults | 60 pcf equivalent fluid | IBC Table 1610.1 at-rest, granular soil |
| Allowable soil bearing | 1,500 psf | IBC Table 1806.2 presumptive |
| Flotation factor of safety | 1.3 | USACE EM 1110-2-2100 (usual case) |
| Load and durability factors for the wall check | 1.6 and 1.3 | ACI 318 Table 5.3.1; ACI 350-01 §9.2.6 |
| Bar supports, spacers, ties | chairs and spacers at 4 ft each way; a tie at every intersection, 8 in of 16 ga wire each | CRSI practice; house rule |
| Equipment anchors | 4 per floor-mounted pump, filter, heater, UV, chlorinator, control panel | house rule |
| Equipment pads | not modeled | DOC-0119 silent |
| Vault walls and lid | vault walls propped by a concrete lid; lid quantities only, 36 in hatch | house rule; lid span and traffic load need an engineer |
| Coating wall tops | off | house rule |
| Unit costs (concrete per yd³, rebar per lb, gravel per ton) | `None` | Sapphire's cost register has none yet |

## 3. Basin keys this package reads

From the PLAN.md §3.3 basin: `id`, `label`, `shape` (`rectangular`, `cylindrical`, `freeform`),
`length_in`, `width_in`, `depth_in` (top of slab to rim, inside), `freeboard_in`. New optional keys,
read at the basin's top level or under `basin["structure"]` (which wins), for phase C's model:

| Key | Meaning |
|---|---|
| `kind` | `"basin"` (default) or `"vault"` (dry, with a lid); `model["vaults"]` rows are vaults too |
| `diameter_in` | cylindrical basins |
| `area_sqft`, `perimeter_ft` | freeform basins (inside water area and perimeter) |
| `placement` | `"in_grade"` (rim at grade, default), `"raised"` (slab on grade, walls above), `"on_structure"` (on a slab or roof: no gravel, excavation, uplift or bearing) |
| `rim_above_grade_in` | overrides the placement's rim level (negative = below grade) |
| `steps` | `[{depth_in, area_fraction}]` for stepped floors; `riser_length_ft` optional |
| `corner_count` | corners for freeform corner bars |
| `lid` | vaults: `{type: "concrete" or "hatch", thickness_in, hatch_length_in, hatch_width_in}` |
| `slab_in`, `wall_in` | pin a thickness instead of the standard detail |
| `groundwater_depth_ft` | water table depth below grade (also read from `site`) |
| `soil_bearing_psf` | allowable bearing (also read from `site`) |
| `floor_capacity_psf` | for `on_structure` basins |

Penetrations come from `network.segments`: a segment with exactly one end at a node of this basin
(`kind: "basin"` with `basin: <id>`, or any node or pump with `vault: <id>`). Anchors come from
`network.pumps` (not submersible) and `equipment` rows in `anchored_slots`.

## 4. What `structural_takeoff` returns

A plain dict (the one function here that does not return a `CalcResult`: it aggregates them, like
`derive()`):

```jsonc
{
  "schema": 1,
  "disclaimer": "Engineering aid for quantities and early checks, not a stamped structural design",
  "basins": [{
    "basin_id": "b1", "label": "Pool", "kind": "basin", "complete": true,
    "detail":   {"slab_in", "wall_in", "wall_bar", "wall_spacing_in", "wall_mats", "slab_bar",
                 "slab_spacing_in", "slab_mats", "beyond_standard_details", ...},
    "geometry": {... basin_shell value: areas, perimeters, volumes, grade levels ...},
    "quantities": {"concrete": {"elements", "net_yd3", "with_waste_yd3", "order_yd3", "trucks"},
                   "rebar_lb", "rebar_purchase_lb", "rebar_ft", "rebar_by_size", "rebar_by_element",
                   "gravel": {...}, "excavation": {...}, "formwork_sqft", "waterstop_ft",
                   "sealant_ft", "coating_sqft", "curing_sqft", "penetrations": {...},
                   "chairs", "wall_spacers", "ties", "tie_wire_lb"},
    "checks": [/* this basin's checks */],
    "results": {"thickness", "shell", "wall_water", "wall_soil", "cover_wall", "cover_slab", "joints",
                "st_wall", "st_slab", "uplift", "bearing", "floor_load", "concrete", "gravel",
                "excavation", "formwork", "coating", "penetrations", "rebar"}  // CalcResult dicts
  }],
  "rebar_schedule": [{"basin_id", "mark", "element", "description", "bar", "size", "shape",
                      "spacing_in", "qty", "length_ft", "pieces", "total_ft", "weight_lb"}],
  "checks": [{"key": "STRUCT_UPLIFT|Pool", "code", "severity": "blocker|warning|info", "title",
              "detail", "remedy", "section": "basin", "basin", "citation"}],
  "totals": {"concrete_net_yd3", "concrete_order_yd3", "trucks", "rebar_lb", "rebar_purchase_lb",
             "gravel_tons", "excavation_yd3", "backfill_yd3", "haul_yd3", "formwork_sqft",
             "waterstop_ft", "sealant_ft", "coating_sqft", "curing_sqft", "penetrations", "anchors",
             "water_gal", "chairs", "tie_wire_lb", ...},
  "bom": [{"section": "Structural", "key": "structural.concrete", "description", "qty", "uom",
           "item_hint", "priced": false /* only on count lines that shadow a priced line */}],
  "mix": {"fc_psi", "w_cm_max", "air_content_pct", "exposure_class", "admixture", "notes": [...]},
  "anchors": {/* CalcResult */}, "rules": {/* merged */}, "warnings": [DISCLAIMER]
}
```

Check codes: `STRUCT_UPLIFT` (blocker below FS 1.0, warning below 1.3), `STRUCT_FLOOR_LOAD`
(blocker), `STRUCT_FLOOR_UNKNOWN`, `STRUCT_BEYOND_STANDARD`, `STRUCT_WALL_WATER`, `STRUCT_WALL_SOIL`,
`STRUCT_COVER_WALL`, `STRUCT_COVER_SLAB`, `STRUCT_BEARING` (warnings), `STRUCT_ACI350_ST`,
`STRUCT_EXCAVATION_DEPTH`, `STRUCT_INCOMPLETE` (info). Keys follow `issues.py`: `CODE|label`.

BOM keys: `structural.concrete`, `structural.admixture`, `structural.rebar.#4.lb`,
`structural.rebar.#4.bars20` (`priced: false`), `structural.gravel` (in pounds: short tons × 2,000), `structural.excavation`,
`structural.backfill`, `structural.haul`, `structural.formwork`, `structural.waterstop`,
`structural.sealant`, `structural.coating` (water side), `structural.coating.exterior` (a dry vault's
outside faces), `structural.curing`, `structural.chairs`,
`structural.spacers`, `structural.tie_wire`, `structural.link_seal.<size>`, `structural.anchors`.

## 5. Method notes

* **Units of measure.** BOM lines use `Cubic Yard`, `Pound`, `Nos`, `Foot` and `Square Foot`. ERPNext
  has no `Ton`, so gravel is billed in pounds. On prod (checked 2026-11-01) only `Square Foot` is
  enabled; phase B / E must enable the others (or map them) before pushing lines to a Quotation.
* **Geometry.** Inside dimensions plus wall thickness `t`: footprint = inside grown by `t`; wall
  volume = (footprint − inside) × height; slab = footprint × slab thickness. Freeform outlines grow
  by Steiner's formula, so a freeform with a circle's area and perimeter equals the cylinder.
* **Walls.** Fixed at the slab, free at the top (a vault wall under a concrete lid is propped:
  M = γh³/15). Two cases, each alone: full of water to the rim, and empty with backfill at 60 pcf
  plus water pressure below a given water table.
  Flexure by the ACI stress block with Mu = 1.6 × 1.3 × M, one-way shear against φ·2√f'c·b·d.
* **Rebar.** Counts are `ceil(run/s)+1` on an open run, `ceil(loop/s)` round a closed loop. Bars
  longer than stock are lapped; every cut is packed into 20 ft (and 40 ft) bars first-fit-decreasing,
  which is the count and purchase weight in the BOM.
* **Uplift.** U = 62.4 × footprint × head of groundwater above the slab bottom; FS = concrete
  weight / U. Soil friction and backfill over a toe are ignored (conservative).

Tests: `erpnext_enhancements/tests/test_structural_*.py` (bench-free pytest; own CI step).

## 6. The structure in general (v1.766.0)

On 2026-11-01 Nikolas widened phase S from the basin shells to "the structure in general". DOC-0119
says nothing about anything in this section beyond its materials notes (§1 D14–D18 and its Materials
section), so each module names the public standard it uses.

| File | Does | Standard |
|---|---|---|
| `loads.py` | `soil_properties` (IBC presumptive values; class 5 when the soil is unknown), `surcharge_load` (pedestrian 100 psf, plaza 250 psf; a vehicle as AASHTO's equivalent soil height), `frost_check` and `freeze_thaw_notes` (30 in, Salt Lake County), `seismic_design_values` (Fa, Fv, SDS, SD1 and SDC from Ss/S1 and the site class, with a step that says to look them up in the USGS / ASCE Hazard Tool), `hydrodynamic_wall_load` (ACI 350.3 rectangular basin: impulsive, convective and wall inertia), `wind_panel_load` (ASCE 7 Ch. 29 freestanding wall or sign, with porosity and the elevation factor) | IBC 1806.2, 1809.5; ASCE 7-16 Ch. 4, 11, 26, 29; ACI 350.3-06; AASHTO |
| `walls.py` | `cantilever_wall` (raised basin, weir and soil-retaining walls on a footing: overturning, sliding, bearing, frost and stem flexure, plus a seismic case by Westergaard or Seed-Whitman), `size_wall_footing` (the narrowest footing that passes, at the frost depth, with concrete and rebar per foot) | IBC 1807.2.3, 1610.1 |
| `vaults.py` | `lid_slab` (one-way lid for pedestrian, plaza or H-20 wheel loads), `grating` (bar grating by load class), `trench_drain` (EN 1433 class, piece count, ADA opening size) | ACI 318 Ch. 7, 22; AASHTO Std. Spec. §3.24; NAAMM MBG 531/532; EN 1433; ADA 302.3 |
| `building.py` | `deck_beam` (steel W/HSS or concrete beam over a collector tank, L/360), `building_floor_load` (against the stated capacity, or the ASCE 7 live load for the floor's use; always flagged to the engineer of record) | AISC 360; ACI 318 Table 9.3.1.1; ASCE 7 Table 4.3-1 |
| `steel.py` | `hss_section` (properties computed from the tube's geometry; they match the AISC Manual within rounding), `W_SHAPES`, `member_check` / `select_member` (flexure, shear, deflection), `column_check` / `select_column` (§E3 + §H1, drift), `water_wall_frame` (posts and header under wind and trough water) | AISC 360-16 Ch. B, E, F, G, H; AISC DG 27 for stainless |
| `fabrications.py` | `weir_lip_sag` (lip or scupper sag against the 1/16 in edge tolerance, and the support spacing that keeps it there), `veneer_attachment` (adhered up to 15 psf; anchors by the TMS area rule, 75% of that area in SDC D), `pedestal_check` (plinths as rigid blocks under wind or the ASCE 7 component force) | TMS 402-16 Ch. 12–13; ASCE 7 §13.3; ASTM C1242 |
| `rigging.py` | `beam_reactions` (the DRC three-moment solver, pinned to its JavaScript), `rig_plan` (towers, ceiling or flown truss; hoist sizing), `floor_rating`, `show_basin_floor` | DRC `src/rigging.js` (SHA 737fa80); ASCE 7 Table 4.3-1 |
| `supports.py` | `equipment_pad` (pad size and anchors: the ASCE 7 Ch. 13 force, ACI 318 Ch. 17 steel and breakout), `thrust_block`, `seismic_bracing`, `penetration_detail`, `mooring` | ASCE 7 Ch. 13; ACI 318 Ch. 17; AWWA M23 / Uni-Bell; NFPA 13 / SMACNA |
| `general.py` | `general_structure(model, basins, rules)`: runs all of the above from the model; `structural_takeoff` calls it | — |

Phase B owns pipe hanger loads and span tables (`engine/structure.py`). `supports.py` does not
duplicate them.

### 6.1 The model's `structure` block (for phase C)

Everything in the block is optional. Without it the takeoff still runs:
- the hydrodynamic wall check for every basin, when the site gives `ss`/`s1` or `sds`/`sd1`;
- a sag check of every weir feature's lip, at the house-rule section;
- a lid design for every vault with a concrete lid (`lid.load_class`);
- a waterstop ring at every penetration;
- a floor check for any basin placed `on_structure` (`floor_use`).

```jsonc
"site": {"ss": 1.5, "s1": 0.55, "site_class": "D", "soil_class": 4, "soil_bearing_psf": null,
         "frost_depth_in": 30, "wind_speed_mph": 115, "wind_exposure": "C", "elevation_ft": 4300},
"structure": {
  "walls":   [{"id", "label", "stem_height_ft", "stem_in", "retained": "water|soil", "water_depth_ft",
               "length_ft", "surcharge": "pedestrian|plaza|vehicle|<psf>", "soil_class", "bearing_psf"}],
  "frames":  [{"id", "label", "height_ft", "width_ft", "header_plf", "post_spacing_ft", "clearance_ft",
               "solid_ratio", "material": "A500C|304SS|316SS"}],
  "weir_lips": [{"id", "feature", "span_in", "thickness_in", "flange_in", "lip_width_in", "water_depth_in",
                 "support": "simple|continuous|fixed|cantilever", "tolerance_in"}],
  "veneer":  [{"id", "area_sqft", "thickness_in", "material", "method": "anchored|adhered", "anchor_capacity_lb"}],
  "pedestals": [{"id", "length_ft", "width_ft", "height_ft", "top_load_lb", "top_cg_ft", "wind_lb",
                 "wind_height_ft", "on": "slab|soil"}],
  "columns": [{"id", "height_ft", "axial_lb", "lateral_plf", "top_lb", "material"}],
  "beams":   [{"id", "span_ft", "tributary_ft", "dead_psf", "live_psf", "material": "steel|concrete", "shape": "W|HSS"}],
  "gratings": [{"id", "span_in", "area_sqft", "load_class": "pedestrian|plaza|vehicle"}],
  "trench_drains": [{"id", "length_ft", "load_class"}],
  "pads":    [{"id", "length_in", "width_in", "weight_lb", "cg_height_in", "n_anchors", "anchor_dia_in", "embed_in"}],
  "thrust_blocks": [{"id", "size", "pressure_psi", "fitting": "90|45|22.5|tee|dead_end", "count", "soil_bearing_psf"}],
  "bracing": [{"id", "size", "run_ft", "material"}],
  "moorings": [{"id", "above_water_sqft", "submerged_sqft", "current_fps", "n_anchors", "line_angle_deg"}],
  "rigging": {"hung_lb_per_ft", "hung_length_ft", "hanger_spans", "hanger_span_ft", "overhang_ft",
              "curtain_width_ft", "mount": "towers|ceiling|flown", "truss": "tri12|box12|box16",
              "header_height_ft", "picks", "riser_lb", "feed": "end|both"}
}
```

Basins gain `floor_use` (`roof`, `office`, `lobby` and the rest of `building.FLOOR_USE_PSF`) and
`lid.load_class`.

### 6.2 What the takeoff adds

**New top-level keys.**
- `structure`: `{group: [{id, label, result: CalcResult dict}]}`. The groups are `hydrodynamic`,
  `lids`, `floors`, `weir_lips`, `walls`, `frames`, `columns`, `beams`, `gratings`, `trench_drains`,
  `veneer`, `pedestals`, `pads`, `thrust_blocks`, `bracing`, `moorings` and `rigging`.
- `seismic`: the `seismic_design_values` CalcResult.
- `notes`: the freeze-thaw notes.

**New `totals` keys.** `steel_lb`, `site_concrete_cuft`, `site_rebar_lb`, `grating_sqft`,
`veneer_anchors`, `equipment_anchors`, `braces`, `mooring_blocks`, `trench_drain_pieces` and
`waterstop_ring_ft`.

**New check codes**, by `issues.py` section and severity:

| Section | Severity | Codes |
|---|---|---|
| project | info | `STRUCT_SEISMIC_INPUTS` |
| basin | warning | `STRUCT_SEISMIC_WALL`, `STRUCT_LID`, `STRUCT_FLOOR_USE`, `STRUCT_WALL_STABILITY`, `STRUCT_BEAM`, `STRUCT_GRATING` |
| basin | info | `STRUCT_SLOSH` |
| features | warning | `STRUCT_WEIR_SAG`, `STRUCT_FRAME`, `STRUCT_COLUMN`, `STRUCT_VENEER`, `STRUCT_PEDESTAL` |
| features | blocker | `STRUCT_RIGGING` |
| pump | warning | `STRUCT_ANCHORS` |

**New BOM keys.**
- By the pound: `structural.steel` and `structural.steel.<304ss|316ss>`.
- By the square foot: `structural.grating.<class>`.
- By the piece: `structural.trench_drain.<class>`, `structural.veneer_anchors`, `structural.hatch`,
  `structural.seismic_braces`, `structural.mooring_blocks`.
- By the cubic yard: `structural.concrete_site`, which covers walls, pads, pedestals, thrust and
  mooring blocks, and concrete beams.

**Merged into existing lines.**
- Wall rebar adds to `structural.rebar.#4.lb`.
- Penetration rings add to `structural.waterstop`.
- When pads are listed, their anchors replace the generic `structural.anchors` count.

### 6.3 More house rules (DOC-0119 is silent on these)

| Rule | Default |
|---|---|
| `frost_depth_in` | 30 in |
| `wind_speed_mph` | 115 mph (read the address in the Hazard Tool) |
| `wind_exposure` | C |
| `soil_pcf` | 120 pcf |
| `active_efp_pcf` | 45 pcf |
| `wall_fs` / `wall_fs_seismic` | 1.5 / 1.1 |
| `steel_load_factor` | 1.6 |
| Deflection limits | L/360 for beams, L/180 for cantilevers, H/180 for post drift |
| `frame_post_spacing_ft` | 8 ft |
| Weir lip section | 3/16 in plate, 2 in flange, 3 in lip, supports at 48 in |
| `veneer_anchor_fs` | 4 |
| Trench grate pieces | 1 m long |
| Equipment pads | 6 in past the equipment, 6 in thick |
| Thrust blocks | 1,000 psf soil bearing, FS 1.5 |
| Pipe bracing | 40 ft lateral, 80 ft longitudinal (half that for plastic pipe) |
| Moorings | 90 mph wind, μ 0.5, FS 1.5 |
