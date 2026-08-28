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
from .constants import DEFAULT_TURNOVERS_PER_HR, FT_PER_PSI, HW_C_PVC
from .feature import (
    feature_flow_category,
    nozzle_array_flow,
    nozzle_flow,
    tiered_fountain_flow,
    weir_flow,
)
from .pipe import pipe_pressure_check
from .pump import select_pump
from .safety import suction_outlet_vgb
from .tdh import segment_loss_results, total_dynamic_head


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
        # A regulated shell (e.g. an octagon spa) may carry a published volume the
        # rect/cyl geometry can't derive — honor an explicit override when given.
        override = float(b.get("volume_gal_override") or 0)
        if override > 0:
            total_gal += override
            continue
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

    # 3b) Regulated aquatic venues (health-dept pools/spas): the code minimum
    #     circulation flow is a FLOOR on the design flow, and bather-load,
    #     skimmer, turnover-time and VGB main-drain requirements come from the
    #     water-surface area + the venue fixtures. Fountains skip all of this
    #     (is_regulated is False), so their design flow is unchanged.
    venue_type = inputs.get("venue_type") or ""
    regulated = is_regulated(venue_type)
    reg: dict[str, Any] = {
        "minimum_flow_gpm": None,
        "turnover_time_min": None,
        "bather_load": None,
        "skimmer_count": None,
        "jet_flow_gpm": None,
        "main_drain_status": None,
    }
    published_flow = float(inputs.get("design_flow_published_gpm") or 0)
    if regulated:
        fam = venue_family(venue_type)
        governing_code = inputs.get("governing_code") or ""
        surface_area_sf = float(inputs.get("surface_area_sf") or 0)
        max_turnover_min = float(inputs.get("max_turnover_min") or 0)
        if total_gal:
            r = minimum_flow_rate(total_gal, max_turnover_min, venue=fam, governing_code=governing_code)
            results.append(r.to_dict())
            warnings += r.warnings
            reg["minimum_flow_gpm"] = r.value
        if surface_area_sf > 0:
            rb = bather_load(
                surface_area_sf,
                float(inputs.get("bather_sf_per_person") or 0),
                rounding=inputs.get("bather_rounding", "floor"),
                venue=fam,
                governing_code=governing_code,
            )
            results.append(rb.to_dict())
            warnings += rb.warnings
            reg["bather_load"] = rb.value
            rs = skimmer_sizing(
                surface_area_sf,
                sf_each=float(inputs.get("skimmer_sf_each") or 0),
                rated_gpm=float(inputs.get("skimmer_rated_gpm") or 0),
                venue=fam,
                governing_code=governing_code,
            )
            results.append(rs.to_dict())
            warnings += rs.warnings
            reg["skimmer_count"] = rs.value
        else:
            needed.append("surface_area_sf")
        # Therapy jets are a separate jet-pump circuit; total their flow for the
        # schedule without forcing it into the circulation design flow.
        jet_flow = sum(
            float(fx.get("rated_gpm") or 0) * max(int(fx.get("qty") or 1), 1)
            for fx in (inputs.get("venue_fixtures") or [])
            if (fx.get("fixture_type") or "").strip().lower().startswith("therapy")
        )
        reg["jet_flow_gpm"] = jet_flow or None

    if regulated:
        design_flow = max(circ_gpm or 0.0, feature_flow, reg["minimum_flow_gpm"] or 0.0, published_flow)
        if published_flow and reg["minimum_flow_gpm"] and published_flow < reg["minimum_flow_gpm"]:
            warnings.append(
                f"Published design flow {published_flow:g} GPM is BELOW the code minimum "
                f"{reg['minimum_flow_gpm']:.2f} GPM — a code violation; increase the circulation flow."
            )
    else:
        design_flow = max(circ_gpm or 0.0, feature_flow)

    # Regulated rollups that depend on the final design flow: turnover time and
    # the VGB anti-entrapment gate on each main-drain fixture.
    if regulated and design_flow:
        if total_gal:
            rt = turnover_time(total_gal, design_flow)
            results.append(rt.to_dict())
            warnings += rt.warnings
            reg["turnover_time_min"] = rt.value
            max_turnover_min = float(inputs.get("max_turnover_min") or 0)
            if max_turnover_min and rt.value and rt.value > max_turnover_min:
                warnings.append(
                    f"Turnover time {rt.value:.1f} min exceeds the {max_turnover_min:g}-min code maximum."
                )
        for fx in inputs.get("venue_fixtures") or []:
            if "drain" not in (fx.get("fixture_type") or "").strip().lower():
                continue
            open_area = float(fx.get("open_area_in2") or 0)
            if open_area > 0:
                rd = main_drain_flow(open_area, drains=max(int(fx.get("qty") or 2), 1))
                results.append(rd.to_dict())
                warnings += rd.warnings
            cl = float(fx.get("cover_length_in") or 0)
            cw = float(fx.get("cover_width_in") or 0)
            oaf = float(fx.get("open_area_fraction") or 0)
            if cl > 0 and cw > 0 and oaf > 0:
                rv = suction_outlet_vgb(design_flow, cl, cw, oaf, outlets=max(int(fx.get("qty") or 2), 1))
                results.append(rv.to_dict())
                warnings += rv.warnings
                reg["main_drain_status"] = rv.status

    # 4) Total Dynamic Head. A pipe segment with no explicit flow carries the
    #    full system (design) flow — most do. A length-bearing segment left at
    #    zero would otherwise compute ZERO friction loss and silently undersize
    #    the pump, so default it to design_flow (and warn if we can't).
    raw_segments = inputs.get("pipe_segments") or inputs.get("segments") or []
    segments = []
    for s in raw_segments:
        s = dict(s)
        if not float(s.get("flow_gpm") or 0):
            if design_flow:
                s["flow_gpm"] = design_flow
            elif float(s.get("length_ft") or 0) > 0:
                warnings.append(
                    f"Pipe segment {s.get('label') or '?'} has no flow and no design flow to "
                    "infer it from — its friction loss is zero. Enter the GPM it carries."
                )
        segments.append(s)
    hw_c = inputs.get("hazen_williams_c") or HW_C_PVC
    tdh_ft = None
    if segments:
        # Per-segment friction / fitting / component envelopes first (the full
        # working behind each run), then the rolled-up TDH that sums them. The
        # rollup already surfaces the minor/component warnings, so we don't also
        # add the per-segment envelopes' warnings (the audit cards still show
        # them; this just avoids double-counting in the warnings list).
        for s in segments:
            for env in segment_loss_results(s, c=hw_c):
                results.append(env.to_dict())
        r = total_dynamic_head(segments, static_lift_ft=inputs.get("static_lift_ft", 0.0), c=hw_c)
        results.append(r.to_dict())
        warnings += r.warnings
        tdh_ft = r.value

        # Pressure-rating check: the pump puts ~TDH ft of head (= TDH/2.31 psi) on
        # the discharge side. The velocity check can't see this, so flag any
        # discharge run whose pipe isn't rated for the system pressure.
        if tdh_ft and tdh_ft > 0:
            system_psi = tdh_ft / FT_PER_PSI
            seen_under = set()
            for s in segments:
                if not (s.get("line_type") or "Discharge").lower().startswith("dis"):
                    continue
                size = s.get("nominal_size")
                material = s.get("material", "SCH40 PVC")
                if not size or (material, size) in seen_under:
                    continue
                chk = pipe_pressure_check(material, size, system_psi)
                if chk.status and "Pressure" in chk.status:
                    seen_under.add((material, size))
                    results.append(chk.to_dict())
                    warnings += chk.warnings
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
        "venue_type": venue_type or None,
        "is_regulated": regulated,
        "minimum_flow_gpm": reg["minimum_flow_gpm"],
        "turnover_time_min": reg["turnover_time_min"],
        "bather_load": reg["bather_load"],
        "skimmer_count": reg["skimmer_count"],
        "jet_flow_gpm": reg["jet_flow_gpm"],
        "main_drain_status": reg["main_drain_status"],
        "tdh_ft": tdh_ft,
        "selected_pump": selected_pump,
        "pump_options": pump_options,
        "next_inputs_needed": needed,
        "warnings": warnings,
    }
