"""Pure water-feature calculation engine (stdlib only — no frappe).

Every public function returns a :class:`~.envelope.CalcResult` so callers get
both the answer and the math that produced it (formula, ordered steps, source
citations, warnings, and any A/B/C options the user still must choose).

Invariant: nothing in this package may import ``frappe`` or
``erpnext_enhancements.assistant_tools``. That keeps the engine bench-free
unit-testable and lets both the MCP tools and the desk endpoints import it.
"""

from .basin import basin_volume, turnover_gpm
from .chemistry import chemistry_targets, chlorinator_feed, ozone_sidestream
from .controls import calc_lighting, calc_solenoid_relays, lighting_sizing
from .drainage import manning_drain_flow, size_drain, surge_basin_volume
from .envelope import CalcOption, CalcResult, make_input
from .feature import nozzle_array_flow, nozzle_flow, weir_flow
from .pipe import hazen_williams_loss, pipe_velocity, size_pipe, velocity_status
from .pipeline import run_spine
from .pump import electrical_load, select_pump
from .tdh import component_loss, fitting_minor_loss, total_dynamic_head

__all__ = [
    "CalcOption",
    "CalcResult",
    "basin_volume",
    "calc_lighting",
    "calc_solenoid_relays",
    "chemistry_targets",
    "chlorinator_feed",
    "component_loss",
    "electrical_load",
    "fitting_minor_loss",
    "hazen_williams_loss",
    "lighting_sizing",
    "make_input",
    "manning_drain_flow",
    "nozzle_array_flow",
    "nozzle_flow",
    "ozone_sidestream",
    "pipe_velocity",
    "run_spine",
    "select_pump",
    "size_drain",
    "size_pipe",
    "surge_basin_volume",
    "total_dynamic_head",
    "turnover_gpm",
    "velocity_status",
    "weir_flow",
]
