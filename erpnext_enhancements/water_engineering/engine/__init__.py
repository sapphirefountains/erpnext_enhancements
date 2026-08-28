"""Pure water-feature calculation engine (stdlib only — no frappe).

Every public function returns a :class:`~.envelope.CalcResult` so callers get
both the answer and the math that produced it (formula, ordered steps, source
citations, warnings, and any A/B/C options the user still must choose).

Invariant: nothing in this package may import ``frappe`` or
``erpnext_enhancements.assistant_tools``. That keeps the engine bench-free
unit-testable and lets both the MCP tools and the desk endpoints import it.
"""

from .aquatic import (
    bather_load,
    is_regulated,
    main_drain_flow,
    minimum_flow_rate,
    skimmer_sizing,
    turnover_time,
    venue_family,
)
from .basin import basin_volume, turnover_gpm
from .chemistry import chemistry_targets, chlorinator_feed, ozone_sidestream
from .controls import calc_lighting, calc_solenoid_relays, lighting_sizing
from .drainage import manning_drain_flow, size_drain, surge_basin_volume
from .drawings import circulation_schematic_svg, electrical_oneline_svg
from .electrical import (
    control_transformer_va,
    motor_flc,
    service_main_breaker,
    total_connected_load,
)
from .envelope import CalcOption, CalcResult, make_input
from .feature import (
    feature_flow_category,
    feature_visual_kind,
    jet_trajectory,
    nozzle_array_flow,
    nozzle_flow,
    tiered_fountain_flow,
    weir_flow,
)
from .pipe import (
    hazen_williams_loss,
    pipe_pressure_check,
    pipe_pressure_rating,
    pipe_velocity,
    size_pipe,
    velocity_status,
)
from .pipeline import run_spine
from .pump import electrical_load, head_at_flow, select_pump
from .safety import npsh_available, suction_outlet_vgb, water_hammer
from .tdh import component_loss, fitting_minor_loss, total_dynamic_head
from .treatment import (
    chemical_dose,
    evaporation_rate,
    filtration_area,
    heating_load,
    lsi_index,
    make_up_water,
    uv_dose,
)
from .workbook import (
    electric_cost,
    lazy_river_hp,
    lighting_design,
    open_channel_flow,
    overflow_check,
    program_rules,
    vertical_pipe,
)

__all__ = [
    "CalcOption",
    "CalcResult",
    "basin_volume",
    "bather_load",
    "calc_lighting",
    "calc_solenoid_relays",
    "chemical_dose",
    "chemistry_targets",
    "chlorinator_feed",
    "circulation_schematic_svg",
    "component_loss",
    "control_transformer_va",
    "electric_cost",
    "electrical_load",
    "electrical_oneline_svg",
    "evaporation_rate",
    "feature_flow_category",
    "feature_visual_kind",
    "filtration_area",
    "fitting_minor_loss",
    "hazen_williams_loss",
    "head_at_flow",
    "heating_load",
    "is_regulated",
    "jet_trajectory",
    "lazy_river_hp",
    "lighting_design",
    "lighting_sizing",
    "lsi_index",
    "main_drain_flow",
    "make_input",
    "make_up_water",
    "manning_drain_flow",
    "minimum_flow_rate",
    "motor_flc",
    "nozzle_array_flow",
    "nozzle_flow",
    "npsh_available",
    "open_channel_flow",
    "overflow_check",
    "ozone_sidestream",
    "pipe_pressure_check",
    "pipe_pressure_rating",
    "pipe_velocity",
    "program_rules",
    "run_spine",
    "select_pump",
    "service_main_breaker",
    "size_drain",
    "size_pipe",
    "skimmer_sizing",
    "suction_outlet_vgb",
    "surge_basin_volume",
    "tiered_fountain_flow",
    "total_connected_load",
    "total_dynamic_head",
    "turnover_gpm",
    "turnover_time",
    "uv_dose",
    "velocity_status",
    "venue_family",
    "vertical_pipe",
    "water_hammer",
    "weir_flow",
]
