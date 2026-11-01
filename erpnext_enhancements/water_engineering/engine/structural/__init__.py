"""Structural quantities and early checks for basins and vaults (phase S).

Answers "how much concrete, rebar, gravel and waterproofing does a basin of
this size need, and does it pass the basic checks". Sapphire's DOC-0119
standard details govern; ACI 318 / 350 fill its gaps. See README.md for every
rule and its source. Stdlib only, never imports frappe; reads basins as plain
dicts in the PLAN.md §3.3 shape.

Engineering aid for quantities and early checks, not a stamped structural
design.
"""

from .building import FLOOR_USE_PSF, building_floor_load, deck_beam
from .concrete import (
    anchor_bolts,
    coating_area,
    concrete_volumes,
    excavation,
    formwork,
    gravel_base,
    joints,
    mix_notes,
    penetrations,
)
from .defaults import DEFAULT_RULES, DISCLAIMER, RULE_SOURCES, merged_rules
from .fabrications import DENSITY_PCF, pedestal_check, veneer_attachment, weir_lip_sag
from .general import general_structure
from .geometry import basin_shell, basin_spec
from .loads import (
    PRESUMPTIVE_SOIL,
    freeze_thaw_notes,
    frost_check,
    hydrodynamic_wall_load,
    seismic_design_values,
    soil_properties,
    surcharge_load,
    wind_panel_load,
)
from .rebar import development_length, hook_geometry, lap_length, rebar_schedule, stock_sticks
from .rigging import beam_reactions, floor_rating, rig_plan, show_basin_floor
from .sizing import (
    bearing_check,
    cover_check,
    detail_for_wall,
    floor_load,
    hydrostatic_pressure,
    lateral_soil_heights,
    select_thickness,
    shrinkage_temperature_check,
    uplift_check,
    wall_bending_check,
)
from .steel import (
    HSS_SIZES,
    MATERIALS,
    W_SHAPES,
    column_check,
    hss_section,
    member_check,
    select_column,
    select_member,
    w_section,
    water_wall_frame,
)
from .supports import equipment_pad, mooring, nominal_size, penetration_detail, seismic_bracing, thrust_block
from .takeoff import structural_takeoff
from .vaults import GRATING_BARS, grating, lid_slab, trench_drain
from .walls import cantilever_wall, size_wall_footing

__all__ = [
    "DEFAULT_RULES",
    "DENSITY_PCF",
    "DISCLAIMER",
    "FLOOR_USE_PSF",
    "GRATING_BARS",
    "HSS_SIZES",
    "MATERIALS",
    "PRESUMPTIVE_SOIL",
    "RULE_SOURCES",
    "W_SHAPES",
    "anchor_bolts",
    "basin_shell",
    "basin_spec",
    "beam_reactions",
    "bearing_check",
    "building_floor_load",
    "cantilever_wall",
    "coating_area",
    "column_check",
    "concrete_volumes",
    "cover_check",
    "deck_beam",
    "detail_for_wall",
    "development_length",
    "equipment_pad",
    "excavation",
    "floor_load",
    "floor_rating",
    "formwork",
    "freeze_thaw_notes",
    "frost_check",
    "general_structure",
    "grating",
    "gravel_base",
    "hook_geometry",
    "hss_section",
    "hydrodynamic_wall_load",
    "hydrostatic_pressure",
    "joints",
    "lap_length",
    "lateral_soil_heights",
    "lid_slab",
    "member_check",
    "merged_rules",
    "mix_notes",
    "mooring",
    "nominal_size",
    "pedestal_check",
    "penetration_detail",
    "penetrations",
    "rebar_schedule",
    "rig_plan",
    "seismic_bracing",
    "seismic_design_values",
    "select_column",
    "select_member",
    "select_thickness",
    "show_basin_floor",
    "shrinkage_temperature_check",
    "size_wall_footing",
    "soil_properties",
    "stock_sticks",
    "structural_takeoff",
    "surcharge_load",
    "thrust_block",
    "trench_drain",
    "uplift_check",
    "veneer_attachment",
    "w_section",
    "wall_bending_check",
    "water_wall_frame",
    "weir_lip_sag",
    "wind_panel_load",
]
