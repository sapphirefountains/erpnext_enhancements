"""House rules for the structural package, with their sources.

Every rule the structural calcs read lives in ``DEFAULT_RULES``. Phase C's
Water Engineering Settings will feed a ``rules`` dict later; until then the
caller passes overrides and ``merged_rules`` fills the rest. ``RULE_SOURCES``
says where each default comes from, so the page can show it under *House
rules* and a designer can tell a DOC-0119 rule from a gap we filled.

Source tags:
  DOC-0119  Sapphire's Fountain Design Guidelines, section "Concrete Design"
            (sub-sections Big & Beefy, Rebar, Drainage Layer, Admixtures,
            Waterstop) and "Concrete Penetrations".
  ACI / IBC / OSHA / ASTM  the public standard named, used where DOC-0119 is
            silent.
  house rule  no published source; a Sapphire default for the designers to
            confirm (listed in README.md "Rules DOC-0119 does not state").
"""

from __future__ import annotations

DISCLAIMER = "Engineering aid for quantities and early checks, not a stamped structural design"

# --- citations --------------------------------------------------------------
CIT_DOC0119_CONCRETE = "DOC-0119 Fountain Design Guidelines, Concrete Design > Big & Beefy"
CIT_DOC0119_REBAR = "DOC-0119 Fountain Design Guidelines, Concrete Design > Rebar"
CIT_DOC0119_GRAVEL = "DOC-0119 Fountain Design Guidelines, Concrete Design > Drainage Layer"
CIT_DOC0119_ADMIX = "DOC-0119 Fountain Design Guidelines, Concrete Design > Admixtures"
CIT_DOC0119_WATERSTOP = "DOC-0119 Fountain Design Guidelines, Concrete Design > Waterstop"
CIT_DOC0119_PENETRATIONS = "DOC-0119 Fountain Design Guidelines, Equipment Rooms > Concrete Penetrations"
CIT_DOC0119_WATERPROOFING = "DOC-0119 Fountain Design Guidelines, Waterproofing (surface materials; joints)"
CIT_DOC0119_ANALYSIS = "DOC-0119 Fountain Design Guidelines, Concrete Design > Structural Analysis"
CIT_ACI318_DEV = "ACI 318-19 §25.4.2.3 (simplified development length of straight bars in tension)"
CIT_ACI318_LAP = "ACI 318-19 §25.5.2.1 (Class B tension lap splice = 1.3 ld, not less than 12 in)"
CIT_ACI318_HOOK = "ACI 318-19 Table 25.3.1 (standard hook geometry)"
CIT_ACI318_COVER = "ACI 318-19 Table 20.5.1.3.1 (specified cover; 3 in for concrete cast against earth)"
CIT_ACI318_TWO_LAYERS = "ACI 318-19 §11.7.2.3 (walls thicker than 10 in need two layers of reinforcement)"
CIT_ACI318_FLEXURE = "ACI 318-19 §22.2 (rectangular stress block), φ = 0.90 per Table 21.2.2"
CIT_ACI318_SHEAR = "ACI 318-19 §22.5.5.1 (Vc = 2 λ √f'c b d), φ = 0.75"
CIT_ACI318_LOADS = "ACI 318-19 Table 5.3.1 (factored load combinations; 1.6 used for lateral fluid and soil)"
CIT_ACI318_EXPOSURE = "ACI 318-19 Table 19.3.2.1 and Table 19.3.3.1 (exposure class F2: w/cm and air content)"
CIT_ACI350_ST = "ACI 350-06 §7.12.2.1 (shrinkage and temperature reinforcement ratio by joint spacing; max 12 in o.c.)"
CIT_ACI350_SD = "ACI 350-01 §9.2.6 (environmental durability coefficient 1.3 for flexure)"
CIT_ACI350_WALL = "ACI 350-06 §14.6.2 (walls 10 ft and taller in contact with liquid: 12 in minimum)"
CIT_ACI350_MIX = "ACI 350-06 §4.2 (environmental concrete: w/cm 0.45, f'c 4,000 psi minimum)"
CIT_ACI224_JOINTS = "ACI 224.3R-95 (joints in concrete construction) with ACI 350 Table 7.12.2.1 joint bands"
CIT_ACI332_HORIZ = "ACI 332 (as quoted in DOC-0119): one horizontal bar in the top 24 in and one in the bottom 24 in"
CIT_IBC_SOIL = "IBC 2021 Table 1806.2 (presumptive load-bearing values; 1,500 psf for class 5 soils)"
CIT_IBC_LATERAL = "IBC 2021 Table 1610.1 (lateral soil load; 60 pcf at-rest for well-drained granular soils)"
CIT_OSHA_SLOPE = "OSHA 29 CFR 1926.652 and Subpart P App. B (no sloping under 5 ft; Type C soil 1.5H:1V)"
CIT_USACE_FLOTATION = "USACE EM 1110-2-2100 Table 3-2 (flotation factor of safety 1.3 usual)"
CIT_HYDROSTATIC = "Hydrostatic pressure p = γ h, γ = 62.4 lb/ft³ for fresh water (triangular distribution)"
CIT_CRSI = "CRSI Manual of Standard Practice (bar supports at about 4 ft each way)"
CIT_ASTM_C94 = "ASTM C94 (ready-mixed concrete; typical 10 yd³ truck)"

DEFAULT_RULES: dict = {
    # DOC-0119 Big & Beefy / Rebar
    "cover_in": 2.0,
    "slab_in": 8.0,
    "wall_in": 8.0,
    "stem_wall_min_in": 4.0,
    "stem_wall_max_in": 6.0,
    "wall_details": [
        {"wall_in": 8.0, "bar": 4, "spacing_in": 16.0},
        {"wall_in": 10.0, "bar": 4, "spacing_in": 12.0},
    ],
    "slab_bar": 4,
    "slab_spacing_in": 18.0,
    "general_bar": 4,
    "general_spacing_in": 18.0,
    "dowel_leg_in": 24.0,
    "gravel_in": 12.0,
    # ACI 318 / 350 as aids where DOC-0119 is silent
    "fc_psi": 4500.0,
    "fy_psi": 60000.0,
    "w_cm_max": 0.45,
    "air_content_pct": 6.0,
    "max_aggregate_in": 0.75,
    "two_mats_over_in": 10.0,
    "aci350_tall_wall_ft": 10.0,
    "aci350_tall_wall_min_in": 12.0,
    "load_factor": 1.6,
    "durability_factor": 1.3,
    "lap_rule": "aci_class_b",  # or "40db"
    "lap_db_fallback": 40.0,
    "concrete_pcf": 150.0,
    "water_pcf": 62.4,
    "soil_efp_pcf": 60.0,
    "soil_bearing_psf": 1500.0,
    "uplift_fs": 1.3,
    "max_joint_spacing_ft": 30.0,
    "excavation_slope_free_ft": 5.0,
    "side_slope_h_per_v": 1.5,
    # house rules (DOC-0119 silent, no single public source)
    "default_freeboard_in": 2.0,
    "waste_factor": 0.10,
    "order_increment_yd3": 0.25,
    "truck_yd3": 10.0,
    "gravel_tons_per_yd3": 1.4,
    "gravel_waste": 0.10,
    "work_space_ft": 2.0,
    "soil_swell": 0.25,
    "stock_length_ft": 20.0,
    "chair_spacing_ft": 4.0,
    "spacer_spacing_ft": 4.0,
    "tie_every": 1,
    "tie_wire_in_per_tie": 8.0,
    "tie_wire_lb_per_ft": 0.0104,
    "anchors_per_equipment": 4,
    "anchored_slots": ["pump", "filter", "heater", "uv", "chlorinator", "control_panel"],
    "coat_wall_tops": False,
    "vault_hatch_in": 36.0,
    "shoring": False,
    # unit costs: Sapphire's cost register has none yet (2026-11-01)
    "concrete_cost_per_yd3": None,
    "rebar_cost_per_lb": None,
    "gravel_cost_per_ton": None,
}

RULE_SOURCES: dict = {
    "cover_in": CIT_DOC0119_CONCRETE,
    "slab_in": CIT_DOC0119_CONCRETE,
    "wall_in": CIT_DOC0119_REBAR + " (the 8 in row of the wall table)",
    "stem_wall_min_in": CIT_DOC0119_CONCRETE,
    "stem_wall_max_in": CIT_DOC0119_CONCRETE,
    "wall_details": CIT_DOC0119_REBAR,
    "slab_bar": CIT_DOC0119_REBAR,
    "slab_spacing_in": CIT_DOC0119_REBAR + " (#4 carbon steel on 18 in centers for most projects)",
    "general_bar": CIT_DOC0119_REBAR,
    "general_spacing_in": CIT_DOC0119_REBAR,
    "dowel_leg_in": CIT_DOC0119_REBAR + " (wall bar legs tied into the floor mat 24 in)",
    "gravel_in": CIT_DOC0119_GRAVEL,
    "fc_psi": CIT_ACI318_EXPOSURE,
    "fy_psi": "ASTM A615 Grade 60",
    "w_cm_max": CIT_ACI318_EXPOSURE + "; " + CIT_ACI350_MIX,
    "air_content_pct": CIT_ACI318_EXPOSURE,
    "max_aggregate_in": "house rule",
    "two_mats_over_in": CIT_ACI318_TWO_LAYERS,
    "aci350_tall_wall_ft": CIT_ACI350_WALL,
    "aci350_tall_wall_min_in": CIT_ACI350_WALL,
    "load_factor": CIT_ACI318_LOADS,
    "durability_factor": CIT_ACI350_SD,
    "lap_rule": CIT_ACI318_LAP,
    "lap_db_fallback": "Rule of thumb 40 bar diameters (CRSI field practice for Grade 60 #3–#6)",
    "concrete_pcf": "Normal-weight reinforced concrete, 150 lb/ft³",
    "water_pcf": CIT_HYDROSTATIC,
    "soil_efp_pcf": CIT_IBC_LATERAL,
    "soil_bearing_psf": CIT_IBC_SOIL,
    "uplift_fs": CIT_USACE_FLOTATION,
    "max_joint_spacing_ft": CIT_ACI224_JOINTS,
    "excavation_slope_free_ft": CIT_OSHA_SLOPE,
    "side_slope_h_per_v": CIT_OSHA_SLOPE,
}

for _key in DEFAULT_RULES:
    RULE_SOURCES.setdefault(_key, "house rule")


def merged_rules(rules: dict | None = None) -> dict:
    """``DEFAULT_RULES`` overlaid with the caller's rules (``None`` values in
    ``rules`` fall back to the default, so a blank Settings field never zeroes
    a rule)."""
    out = dict(DEFAULT_RULES)
    for key, value in (rules or {}).items():
        if value is not None:
            out[key] = value
    return out
