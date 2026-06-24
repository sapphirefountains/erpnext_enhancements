# Water Engineering — Source-Document Data Audit & Roadmap

Audit of the 11 Sapphire design documents (DOC-0025/0028/0048/0049/0062/0092/0119/0121/0123/0126/0127)
against what the calc engine + doctypes currently use. Goal: surface every datum/rule/table the
tooling *should* use but doesn't yet, prioritized.

Status legend: ✅ done · ▶ recommended next · ◇ needs a product/engineering decision · ⏳ larger effort.

---

## Done in this pass (v1.106–1.108)
- ✅ Pipe fittings/components picked from a catalog dropdown (no hand-typed JSON); catalog = engine's
  `FITTING_K` / `COMPONENT_COEFF`.
- ✅ AI `save_water_design` is catalog-aware (valid `type` values embedded in the tool schema).
- ✅ "Show the math" now renders per-segment friction / fitting / component cards (`segment_loss_results`).
- ✅ **Weir/edge sheet-rate guidance** (DOC-0049 B): flow-per-foot + wind band + the *operate ~0.5 /
  engineer 4–6 GPM/ft* rule; under-sheeted edges warn. **Fixed mis-citation**: the 0.5 GPM/ft tier/edge
  rate is DOC-0049 B, not DOC-0119 (corrected in `feature.py` + the tier doctype).
- ✅ `Control Panel Design.product_family` → Select (Splash Wizard Basic / PLUS / MAX, DOC-0062).

---

## DOC-0049 Advanced Fluid Engineering — the core formula workbook (richest)

| # | Gap | Specifics | Plug-in | Effort | Pri |
|---|-----|-----------|---------|--------|-----|
| 1 | **Pipe pressure & weight specs** | sheets 1–3: per size OD, wall, dry/wet lb-ft, max temp, **PSI @73°F & @110°F (=½)**. e.g. 2" SCH40 = 166/83 PSI, 2" SCH80 = 255, 4" SCH40 = 133/66.5 | extend `PIPE_SPECS`; add `pipe_pressure_check(material,size,system_psi,temp)`; surface a per-segment rating status | M | ▶ HIGH — engine sizes by velocity but never checks the pipe is rated for the pressure |
| 2 | **Nonlinear component-loss curves + max-flow limits** | sheet 7: filters are convex (CCP520: 1.8→3.1 ft from 50→60 GPM), each has a hard max GPM (skimmer 75, CCP240 90…) | replace single-slope `COMPONENT_COEFF` with `(gpm,ft)` tables + `max_gpm`; interpolate in `component_loss`; warn over max | M | ▶ HIGH (changes existing TDH numbers → more accurate) |
| 3 | **Low-velocity (<0.5 FPS) settling warning** | sheets 5/6 major-loss tables blank <0.5 FPS | add a "below self-cleaning velocity" band to `velocity_status` | S | ▶ MED |
| 4 | **Slot-channel circular-crest weir** | sheet M: C=3.70 @ hd/Rs=0.33, `H=12*((Q/448.8)/(C*2πRs/12))^(2/3)`, slot n=0.02 | new `slot_channel()` in `workbook.py` | M | ◇ MED |
| 5 | **Lighting watts/SF design bands** | sheet D: shallow pond 0.25–0.75, residential 0.5–1.0, public 1.0–1.5, diving<12' 1.5–2.0, diving>12'/competition 2.0–3.0 W/SF | `lighting_design(area_sf, pool_class)` (recommend wattage vs only roll up fixtures) | S | ▶ MED |
| 6 | **Precip/overflow sizing** | sheets D/G: design rain **7.9 in/hr**; overflow pipe GPM 3"=15, 4"=29, 6"=81; autofill = 20-min refill of evap | `overflow_check(area_sf, pipe_size)` | S | ▶ MED |
| 7 | **Equivalent-pipe-length minor-loss model** | sheet 4: `L_eq = 2.018*K*Q^0.15*D^0.8655` (C=130, k=10.456) | optional `fitting_equiv_length()` cross-check | S | ◇ LOW |
| 8 | `FT_PER_PSI` is in source after all | F-Formulas F173 = **2.308966** (code comment says "not in source docs") | update value + citation; refresh any golden | S | ◇ LOW (golden-test touch) |
| 9 | Stenner feed-pump GPD table | sheet 8 setting 1–10 → 0.8–17 GPD @100 PSI | niche | S | ◇ LOW |

Notes: HW constant 10.44 (pipe) vs 10.456 (TDH) is already handled. Pump curves (sheet 8) are images — not extractable; pump rated GPM/TDH must come from manufacturer cut-sheets.

## DOC-0119 / DOC-0121 Design Guidelines

| # | Gap | Specifics | Plug-in | Effort | Pri |
|---|-----|-----------|---------|--------|-----|
| 1 | **CYA-coupled chlorine floor** | FC ≥ 7.5%×CYA and ≥2 ppm when CYA used | warn in `chemistry` when target FC below the floor | S | ▶ HIGH (sanitation correctness) |
| 2 | **Drain-capacity divergence is silent** | DOC-0119 full-pipe tables (4"@2%=196 GPM) are ~3× the engine's conservative half-full figure (engine is intentionally conservative) | `size_drain` emits an info note showing both bases | M | ▶ MED (stops "engine looks wrong") |
| 3 | Jet height → basin setback/freeboard | `jet_trajectory` advises setback but there's no field/validation | add `freeboard_in`/`setback_ft` to basin + validate ≥ jet height | M | ◇ MED |
| 4 | Sand filter cap 3 GPM/SF, chem targets | already encoded & matching ✅ | — | — | — |
| 5 | Concrete/rebar deterministic tables | 2" cover, 4–6" stem wall, #4 @18" o.c., wall→spacing (10"→12", 8"→16"), 12" gravel | new structural module/print-notes (out of Phase-1 hydraulics) | L | ⏳ |
| 6 | **Schedules (BOM) output** | DOC-0121 requires Equipment / Piping / **Fitting** Schedules, 11×17-legible | print formats from the existing child tables | M | ◇ MED-HIGH |
| 7 | Design-package completeness checklist | extend `completion_percent`/`next_inputs_needed` to a package checklist | S–M | ◇ MED |

## DOC-0028 / DOC-0062 Part Numbers & Platforms

| # | Gap | Specifics | Plug-in | Effort | Pri |
|---|-----|-----------|---------|--------|-----|
| 1 | `product_family` Select | Splash Wizard Basic/PLUS/MAX | ✅ done | S | ✅ |
| 2 | **controller_hardware mismatch** | doctype default says "Nextion HMI"; DOC-0062 EDP001/SDP001 say **LCD + 4-button UI + Power Relay**, no Nextion | reconcile the option text | S | ◇ **DECISION — which screen does the shipping panel use?** |
| 3 | `enclosure_part_no` validation | WP-40 (box 500615 / lid 500616), DC-34P (500109), backplane YH-161407 (500656) | Select/validate; keep WP-40 default | S | ▶ LOW-MED |
| 4 | Valve-channel option | sold in **6** and **9** valve variants (+ expansion) | tie to `solenoid_valve_qty` | S | ▶ MED |
| 5 | Seed catalog Items | controllers 500624–631, WP-40 box/lid 500615/616, Basic PCB 500658, pumps 500014/035/141/144/202, lights 500017/104-107 | ERPNext Item fixtures (Made-to-Spec) | M | ◇ MED |
| — | Part numbers are a flat 6-digit register (500000→501202), no smart encoding | (informational) | — | — | — |
| — | **Data bug to flag upstream**: Splash Wizard MAX is labeled `FCA002` (dup of PLUS) with empty platform cells | confirm with engineering | — | ◇ |

## DOC-0025/0123/0126/0127 Controls (Control Panel Design)

| # | Gap | Specifics | Plug-in | Effort | Pri |
|---|-----|-----------|---------|--------|-----|
| 1 | **Seed standard I/O list** | E-stop, water-level controller, wind sensor (DOC-0126 inputs) — interlocks are seeded but matching inputs aren't | `DEFAULT_IO_POINTS` mirroring `DEFAULT_INTERLOCKS` | S | ▶ HIGH |
| 2 | **`theory_of_operation` field** | DOC-0127 makes a written Theory of Operation mandatory | Long Text on CPD | S | ▶ HIGH |
| 3 | **Control Panel Submittal print format** | DOC-0126's 8 sections render from CPD (doctype already holds the data) | print format / web template | M | ◇ HIGH (deliverable) |
| 4 | Design & construction companies | Fountain Design / Electrical / Construction parties (name/phone/addr/contact/email) — feeds DOC-0025 intake + DOC-0123 O&M | small child table | S–M | ◇ HIGH (blocks O&M gen) |
| 5 | Wind two-threshold interlock | DOC-0123: medium (VFD windy speed) vs high (feature pumps off) — current single "Wind high→stop" under-models | split seed or use `threshold` | S | ▶ MED |
| 6 | Fuses child table | DOC-0123 O&M "Fuses" (qty, replacement P/N) | `Control Fuse` child | S | ◇ MED |
| 7 | IO Point voltage / output fields | DOC-0025 wants per-point voltage, + phase/HP/control-method for outputs | enrich Control IO Point | S | ◇ MED |
| 8 | source_of_confirmation + main voltage_type; 2nd control voltage | DOC-0025 power block | fields on CPD | S | ◇ LOW-MED |
| 9 | O&M manual generator | DOC-0123 section tree (needs #4 + #6 first) | print format | M | ⏳ |
| 10 | Screen tree | real set Home/Maintenance/Schedule/Status + 6 sub-screens + Run/Maintenance modes | remodel screens | M | ⏳ |
| — | DOC-0092 NCR/CAPA | company-wide quality form, **not** CPD tooling | standalone doctype if ever wanted | — | ◇ deprioritize |

## DOC-0048 Fountain Design Data — per-project entry template (thin catalog value)
- Not a manufacturer catalog: one worked example, no nozzle cut-sheets / pump curves / fitting tables.
- Reusable constants: turnover **2/hr** (already `DEFAULT_TURNOVERS_PER_HR`); evaporation min/avg/max
  **0.178 / 0.254 / 0.330 in/day** (engine already has ASHRAE `evaporation_rate`); equipment-room
  ventilation **0.48 cfm/ft²** + 3-min air change (new, but mechanical-room scope, not hydraulics).
- Two Pentair pump nameplates (22019 WhisperFlo XF 5HP, 342002 SuperFlo VST 1.5HP) → ERPNext Items, but
  no rated GPM/TDH/curve, so they feed `electrical_load` only, not `select_pump`.

---

## Recommended next batch (safe, high-value, additive)
1. Pipe pressure ratings + `pipe_pressure_check` (DOC-0049 #1) — needs careful extraction of sheets 1–3.
2. Nonlinear component-loss curves + max-flow warnings (DOC-0049 #2).
3. CYA chlorine floor warning (DOC-0119 #1).
4. `lighting_design` watts/SF + `overflow_check` (DOC-0049 #5/#6).
5. Controls quick wins: `DEFAULT_IO_POINTS`, `theory_of_operation`, wind two-threshold (DOC-0127).

## Decisions needed
- **controller_hardware**: Nextion HMI (current default) vs LCD + 4-button (DOC-0062)? Which does the
  shipping Splash Wizard actually use?
- **How far to go on deliverables**: build the Control Panel Submittal + Equipment/Piping/Fitting
  Schedule print formats? the O&M generator? a structural/concrete module? (each is its own PR)
- Confirm the DOC-0062 Splash Wizard **MAX = FCA003** (source mislabels it FCA002).
