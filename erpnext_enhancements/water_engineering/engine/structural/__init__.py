"""Structural quantities and early checks for basins and vaults (phase S).

Answers "how much concrete, rebar, gravel and waterproofing does a basin of
this size need, and does it pass the basic checks". Sapphire's DOC-0119
standard details govern; ACI 318 / 350 fill its gaps. See README.md for every
rule and its source. Stdlib only, never imports frappe; reads basins as plain
dicts in the PLAN.md §3.3 shape.

Engineering aid for quantities and early checks, not a stamped structural
design.
"""

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
from .geometry import basin_shell, basin_spec
from .rebar import development_length, hook_geometry, lap_length, rebar_schedule, stock_sticks
from .sizing import (
    bearing_check,
    cover_check,
    detail_for_wall,
    floor_load,
    hydrostatic_pressure,
    select_thickness,
    shrinkage_temperature_check,
    uplift_check,
    wall_bending_check,
)
from .takeoff import structural_takeoff

__all__ = [
    "DEFAULT_RULES",
    "DISCLAIMER",
    "RULE_SOURCES",
    "anchor_bolts",
    "basin_shell",
    "basin_spec",
    "bearing_check",
    "coating_area",
    "concrete_volumes",
    "cover_check",
    "detail_for_wall",
    "development_length",
    "excavation",
    "floor_load",
    "formwork",
    "gravel_base",
    "hook_geometry",
    "hydrostatic_pressure",
    "joints",
    "lap_length",
    "merged_rules",
    "mix_notes",
    "penetrations",
    "rebar_schedule",
    "select_thickness",
    "shrinkage_temperature_check",
    "stock_sticks",
    "structural_takeoff",
    "uplift_check",
    "wall_bending_check",
]
