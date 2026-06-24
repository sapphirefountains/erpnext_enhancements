"""Chain the whole Phase-1 hydraulic spine in one call.

``run_spine(inputs)`` runs every step it has enough data for, collects the
:class:`~.envelope.CalcResult` envelopes, rolls up the headline numbers, and
reports ``next_inputs_needed`` (what's still missing) so the desk wizard and the
AI know what to ask next. It is tolerant of partial input — give it a basin and
it computes volume + turnover; add features, segments, and a pump catalog and it
goes all the way to a pump recommendation.
"""

from __future__ import annotations

from typing import Any

from .basin import basin_volume, turnover_gpm
from .constants import DEFAULT_TURNOVERS_PER_HR, HW_C_PVC
from .feature import (
    feature_flow_category,
    nozzle_array_flow,
    nozzle_flow,
    tiered_fountain_flow,
    weir_flow,
)
from .pump import select_pump
from .tdh import total_dynamic_head


def _feature_flow(feature: dict):
    category = feature_flow_category(feature.get("feature_type") or "weir")
    if category == "tiered":
        return tiered_fountain_flow(feature.get("tiers"), feature.get("gpm_per_ft", 0.5))
    if category == "weir":
        return weir_flow(
            feature.get("weir_length_ft", 0),
            feature.get("head_in", 0),
            feature.get("contractions", 2),
        )
    if category == "array":
        return nozzle_array_flow(feature.get("nozzle_count", 0), feature.get("gpm_each", 0))
    return nozzle_flow(
        feature.get("supply_head_ft", 0),
        cd=feature.get("cd"),
        orifice_area_in2=feature.get("orifice_area_in2"),
        orifice_diameter_in=feature.get("orifice_diameter_in"),
        rated_gpm=feature.get("rated_gpm"),
        rated_head_ft=feature.get("rated_head_ft"),
        nozzle_profile=feature.get("nozzle_profile", ""),
    )


def run_spine(inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    inputs = inputs or {}
    results: list[dict] = []
    warnings: list[str] = []
    needed: list[str] = []

    # 1) Basin volume(s) -> total gallons
    basins = inputs.get("basins") or []
    total_gal = 0.0
    for b in basins:
        r = basin_volume(
            b.get("shape", "rectangular"),
            length_in=b.get("length_in", 0),
            width_in=b.get("width_in", 0),
            height_in=b.get("height_in", 0),
            diameter_in=b.get("diameter_in", 0),
        )
        results.append(r.to_dict())
        warnings += r.warnings
        if r.value:
            total_gal += r.value
    if not basins:
        needed.append("basins")

    # 2) Turnover -> required circulation GPM
    circ_gpm = None
    if total_gal:
        r = turnover_gpm(total_gal, inputs.get("turnovers_per_hr", DEFAULT_TURNOVERS_PER_HR))
        results.append(r.to_dict())
        circ_gpm = r.value

    # 3) Feature / weir flow
    features = inputs.get("features") or []
    feature_flow = 0.0
    for f in features:
        r = _feature_flow(f)
        results.append(r.to_dict())
        warnings += r.warnings
        if r.value:
            feature_flow += r.value
    if not features:
        needed.append("features")

    design_flow = max(circ_gpm or 0.0, feature_flow)

    # 4) Total Dynamic Head
    segments = inputs.get("pipe_segments") or inputs.get("segments") or []
    hw_c = inputs.get("hazen_williams_c") or HW_C_PVC
    tdh_ft = None
    if segments:
        r = total_dynamic_head(segments, static_lift_ft=inputs.get("static_lift_ft", 0.0), c=hw_c)
        results.append(r.to_dict())
        warnings += r.warnings
        tdh_ft = r.value
    else:
        needed.append("pipe_segments")

    # 5) Pump selection
    pump_options: list[dict] = []
    selected_pump = None
    if design_flow and tdh_ft is not None:
        r = select_pump(design_flow, tdh_ft, inputs.get("pump_candidates"))
        results.append(r.to_dict())
        warnings += r.warnings
        pump_options = [o.to_dict() for o in r.options]
        selected_pump = r.value
        if not inputs.get("pump_candidates"):
            needed.append("pump_candidates")
    else:
        needed.append("pump sizing (needs design flow + TDH)")

    return {
        "results": results,
        "total_basin_gallons": total_gal or None,
        "required_circulation_gpm": circ_gpm,
        "feature_flow_gpm": feature_flow or None,
        "design_flow_gpm": design_flow or None,
        "tdh_ft": tdh_ft,
        "selected_pump": selected_pump,
        "pump_options": pump_options,
        "next_inputs_needed": needed,
        "warnings": warnings,
    }
