"""Total Dynamic Head: minor (fitting) loss, component loss, and the per-segment
sum that sizes the pump.

Verified against DOC-0049 ``H - TDH``:
    minor      E54 = SUMPRODUCT(K_i, count_i) * V^2 / (2 * 32.2)
    component  E75 = SUMPRODUCT(coeff_i, count_i) * Q
    total      E77 = major(Hazen-Williams) + minor + component + static
"""

from __future__ import annotations

from .constants import CIT_TDH, GRAVITY_FT_S2, HW_C_PVC, HW_CONSTANT, VELOCITY_COEFF
from .data.fittings import COMPONENT_COEFF, COMPONENT_CURVES, FITTING_K
from .data.pipe_specs import get_pipe_id
from .envelope import CalcResult, make_input
from .pipe import hazen_williams_loss


def _interp_curve(points: list, gpm: float) -> float:
    """Head loss (ft) at ``gpm`` on a ``[(gpm, ft), ...]`` curve (ascending).
    Below the first point we scale linearly from the origin; above the last we
    extrapolate on the final segment's slope (and the caller warns past max)."""
    if not points:
        return 0.0
    if gpm <= points[0][0]:
        g0, h0 = points[0]
        return h0 * (gpm / g0) if g0 else 0.0
    for (g0, h0), (g1, h1) in zip(points, points[1:]):
        if gpm <= g1:
            return h0 + (h1 - h0) * (gpm - g0) / (g1 - g0) if g1 != g0 else h1
    (g0, h0), (g1, h1) = points[-2], points[-1]
    slope = (h1 - h0) / (g1 - g0) if g1 != g0 else 0.0
    return h1 + slope * (gpm - g1)


def _qty(row: dict) -> float:
    return float(row.get("qty", row.get("count", 1)) or 0)


def fitting_minor_loss(velocity_fps: float, fittings: list[dict]) -> CalcResult:
    """Minor loss (ft) from fittings/valves via the K-factor velocity-head method."""
    velocity_fps = float(velocity_fps)
    sum_k = 0.0
    unknown: list[str] = []
    parts: list[str] = []
    for row in fittings or []:
        name = row.get("type")
        k = FITTING_K.get(name)
        if k is None:
            unknown.append(str(name))
            continue
        qty = _qty(row)
        sum_k += k * qty
        parts.append(f"{qty}x{name}(K={k})")
    minor = sum_k * velocity_fps**2 / (2 * GRAVITY_FT_S2)
    warnings = [f"Unknown fitting type(s) ignored: {unknown}"] if unknown else []
    return CalcResult(
        calc="fitting_minor_loss",
        value=minor,
        unit="ft",
        inputs={
            "velocity": make_input(velocity_fps, "FPS", "prior_calc"),
            "sum_k": make_input(round(sum_k, 4), "", "lookup", "H - TDH fitting K table"),
        },
        formula="minor_ft = SUMPRODUCT(K, count) * V^2 / (2 * 32.2)",
        steps=[
            f"sum_K = {' + '.join(parts) if parts else '0'} = {sum_k:g}",
            f"minor = {sum_k:g} * {velocity_fps}^2 / (2 * 32.2) = {minor:.4f} ft",
        ],
        citations=[CIT_TDH],
        warnings=warnings,
    )


def component_loss(flow_gpm: float, components: list[dict]) -> CalcResult:
    """Component loss (ft) from filters/skimmers/heaters at the system flow.

    Uses the real (often nonlinear) manufacturer head-loss curves from DOC-0049
    sheet 7 (``COMPONENT_CURVES``), interpolated at ``flow_gpm`` — a single
    ft/GPM coefficient mis-states a convex filter across its range. Falls back to
    the linear ``COMPONENT_COEFF`` for any component without a curve, and warns
    when a component runs past its rated ``max_gpm``."""
    flow_gpm = float(flow_gpm)
    loss = 0.0
    unknown: list[str] = []
    over_max: list[str] = []
    parts: list[str] = []
    for row in components or []:
        name = row.get("type")
        qty = _qty(row)
        curve = COMPONENT_CURVES.get(name)
        if curve:
            per = _interp_curve(curve["points"], flow_gpm)
            loss += per * qty
            parts.append(f"{qty}x{name}({per:.2f} ft @ {flow_gpm:g} GPM)")
            max_gpm = curve.get("max_gpm")
            if max_gpm and flow_gpm > max_gpm:
                over_max.append(f"{name} is rated to {max_gpm:g} GPM but carries {flow_gpm:g} GPM")
        elif name in COMPONENT_COEFF:
            per = COMPONENT_COEFF[name] * flow_gpm
            loss += per * qty
            parts.append(f"{qty}x{name}({per:.2f} ft, linear)")
        else:
            unknown.append(str(name))
    warnings = []
    if unknown:
        warnings.append(f"Unknown component type(s) ignored: {unknown}")
    for o in over_max:
        warnings.append(f"Over rated flow: {o} — split flow or size up.")
    return CalcResult(
        calc="component_loss",
        value=loss,
        unit="ft",
        inputs={
            "flow": make_input(flow_gpm, "GPM", "prior_calc"),
            "components": make_input(len(components or []), "rows", "user"),
        },
        formula="component_ft = SUM over components of headloss_curve(type, Q) * count",
        steps=[
            f"per component @ {flow_gpm:g} GPM: {' + '.join(parts) if parts else '0'}",
            f"component total = {loss:.4f} ft",
        ],
        citations=[CIT_TDH],
        warnings=warnings,
    )


def _segment_id(segment: dict) -> float | None:
    """Inside diameter for a TDH segment: explicit ``id_in`` wins, else look it
    up from ``nominal_size`` + ``material``."""
    if segment.get("id_in"):
        return float(segment["id_in"])
    size = segment.get("nominal_size")
    if size:
        return get_pipe_id(segment.get("material", "SCH40 PVC"), size)
    return None


def segment_loss_results(
    segment: dict,
    c: float = HW_C_PVC,
    constant: float = HW_CONSTANT,
) -> list[CalcResult]:
    """The full per-segment head-loss math as individual envelopes: friction
    (Hazen-Williams major loss), fittings (K-factor minor loss), and components
    (equipment loss). ``total_dynamic_head`` only emits a single rolled-up
    envelope whose steps are one-liners — these break each segment out so the
    audit trail and the form's "show the math" can render every segment's (and
    fitting's) working, formula, inputs, and citation. ``[]`` if the segment has
    no resolvable pipe diameter (nothing to compute)."""
    id_in = _segment_id(segment)
    if not id_in:
        return []
    label = segment.get("label") or segment.get("segment_label") or "segment"
    flow = float(segment.get("flow_gpm", 0) or 0)
    length_ft = float(segment.get("length_ft", 0) or 0)
    velocity = flow * VELOCITY_COEFF / id_in**2

    results: list[CalcResult] = []
    major = hazen_williams_loss(flow, length_ft, id_in, c, constant)
    major.calc = f"Pipe friction — {label}"
    results.append(major)

    fittings = segment.get("fittings") or []
    if fittings:
        minor = fitting_minor_loss(velocity, fittings)
        minor.calc = f"Fitting loss — {label}"
        results.append(minor)

    components = segment.get("components") or []
    if components:
        comp = component_loss(flow, components)
        comp.calc = f"Component loss — {label}"
        results.append(comp)
    return results


def total_dynamic_head(
    segments: list[dict],
    static_lift_ft: float = 0.0,
    c: float = HW_C_PVC,
    constant: float = HW_CONSTANT,
) -> CalcResult:
    """Sum static lift + per-segment (major + minor + component) losses (ft).

    Each segment: ``{flow_gpm, id_in | nominal_size(+material), length_ft,
    fittings:[{type,qty}], components:[{type,qty}]}``.
    """
    static_lift_ft = float(static_lift_ft)
    total = static_lift_ft
    steps = [f"static_lift = {static_lift_ft} ft"]
    warnings: list[str] = []

    for i, seg in enumerate(segments or []):
        flow = float(seg.get("flow_gpm", 0) or 0)
        id_in = _segment_id(seg)
        if not id_in:
            warnings.append(f"segment[{i}] has no pipe diameter; skipped.")
            continue
        length_ft = float(seg.get("length_ft", 0) or 0)
        velocity = flow * 0.4085 / id_in**2
        major = hazen_williams_loss(flow, length_ft, id_in, c, constant).value
        minor_r = fitting_minor_loss(velocity, seg.get("fittings") or [])
        comp_r = component_loss(flow, seg.get("components") or [])
        warnings += minor_r.warnings + comp_r.warnings
        seg_loss = major + minor_r.value + comp_r.value
        total += seg_loss
        steps.append(
            f"seg[{i}] {seg.get('label', '')}: major={major:.3f} + minor={minor_r.value:.3f} "
            f"+ component={comp_r.value:.3f} = {seg_loss:.3f} ft"
        )

    steps.append(f"TDH = {total:.4f} ft")
    return CalcResult(
        calc="total_dynamic_head",
        value=total,
        unit="ft",
        inputs={
            "static_lift": make_input(static_lift_ft, "ft", "user"),
            "segments": make_input(len(segments or []), "count", "user"),
        },
        formula="TDH = static_lift + Sum(major + minor + component) per segment",
        steps=steps,
        citations=[CIT_TDH],
        warnings=warnings,
    )
