"""Steady-state hydraulics: the governing pump-and-pipe solver (stdlib only, no frappe).

Ported from the Digital Rain Curtain's ``src/hydro`` JavaScript under golden vectors
(``tests/golden/drc``, generators in ``tools/golden/drc``); see ``README.md`` here for
the module map. SI inside (m, s, Pa, m^3/s, kg/m^3), US customary at the envelope
boundary with the exact constants in :mod:`.units`.

Two layers, one rule each:

* ``*_si`` kernels are the port. SI in and out, plain floats and small dataclasses,
  the JavaScript's arithmetic in the JavaScript's order. Solvers (the operating point
  here, the network and manifold solvers in phase B) call these thousands of times.
* Functions without the suffix are envelopes: US units in, a :class:`CalcResult`
  out with formula, steps, citations and warnings, like the rest of ``engine/``.

Darcy-Weisbach with Darby 3-K fittings governs; the DOC-0049 Hazen-Williams figure
stays in ``engine/tdh.py`` and is shown beside it as the workbook cross-check
(:func:`.segment.segment_loss`), per Nikolas's decision of 2026-11-01.
"""

from .catalog import MATERIALS, PipeSpec, pipe_spec, pipe_spec_si, sizes_for
from .fittings import FITTING_K_TO_3K, FITTINGS, fit_sum_k, fit_sum_k_si, fitting_k, fitting_k_si
from .operating_point import (
    OpPoint,
    System,
    build_system,
    bypass_flow,
    bypass_flow_si,
    npsha_si,
    operating_point,
    operating_point_si,
    pump_curve,
    system_curve,
    system_curve_si,
)
from .pump import (
    PumpCurve,
    affinity,
    bep_flow,
    bep_flow_si,
    clean,
    interp,
    max_flow,
    max_flow_si,
    npshr,
    npshr_si,
    pump_curve_si,
    pump_eff,
    pump_eff_si,
    pump_head,
    pump_head_si,
    shaft_power,
    shaft_power_si,
)
from .segment import (
    Segment,
    SegmentLoss,
    segment_loss,
    segment_loss_si,
    segment_parts_si,
    suction_loss,
    suction_loss_si,
    supply_loss,
    supply_loss_si,
)
from .terminals import (
    Demand,
    FixedFlowDemand,
    OpenDischargeDemand,
    OrificeDemand,
    ParallelDemand,
    build_demand,
    terminal_demand,
)
from .water import (
    Water,
    area_of,
    area_of_si,
    friction_factor,
    friction_factor_si,
    water,
    water_si,
    wave_speed,
    wave_speed_si,
)

__all__ = [
    "FITTINGS",
    "FITTING_K_TO_3K",
    "MATERIALS",
    "Demand",
    "FixedFlowDemand",
    "OpPoint",
    "OpenDischargeDemand",
    "OrificeDemand",
    "ParallelDemand",
    "PipeSpec",
    "PumpCurve",
    "Segment",
    "SegmentLoss",
    "System",
    "Water",
    "affinity",
    "area_of",
    "area_of_si",
    "bep_flow",
    "bep_flow_si",
    "build_demand",
    "build_system",
    "bypass_flow",
    "bypass_flow_si",
    "clean",
    "fit_sum_k",
    "fit_sum_k_si",
    "fitting_k",
    "fitting_k_si",
    "friction_factor",
    "friction_factor_si",
    "interp",
    "max_flow",
    "max_flow_si",
    "npsha_si",
    "npshr",
    "npshr_si",
    "operating_point",
    "operating_point_si",
    "pipe_spec",
    "pipe_spec_si",
    "pump_curve",
    "pump_curve_si",
    "pump_eff",
    "pump_eff_si",
    "pump_head",
    "pump_head_si",
    "segment_loss",
    "segment_loss_si",
    "segment_parts_si",
    "shaft_power",
    "shaft_power_si",
    "sizes_for",
    "suction_loss",
    "suction_loss_si",
    "supply_loss",
    "supply_loss_si",
    "system_curve",
    "system_curve_si",
    "terminal_demand",
    "water",
    "water_si",
    "wave_speed",
    "wave_speed_si",
]
