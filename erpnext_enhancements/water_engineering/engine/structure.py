"""Pipe supports and floor loads: hanger reactions, the span check, a basin's load on a slab.

Phase B's share of structure (PLAN.md 3.1); everything else structural (shells, rebar, walls,
seismic, frames) is phase S's ``engine/structural/``.

* :func:`beam_reactions` is Digital Rain Curtain ``src/rigging.js`` ``beamReactions``: a
  continuous beam of ``n`` equal spans under a uniform load, with an overhang past each end
  support, by the three-moment equation (Thomas algorithm for the interior moments), then span by
  span. Golden file ``tests/golden/drc/structure.json`` (1e-12), plus DRC's own textbook
  coefficients (``[3/8, 5/4, 3/8]`` for two spans, ``[0.4, 1.1, 1.1, 0.4]`` for three).
* :func:`pipe_hangers` loads a water-filled run (DOC-0049 ``wet_lb_ft``) on hangers at the
  DOC-0049 maximum span (``max_span_ft``) or at a spacing the designer sets, and fails a spacing
  over the maximum. ``data/pipe_specs.py`` had carried both columns unused (audit 1.18).
* :func:`floor_load` is a basin full of water on a slab or podium (rooftop and interior
  fountains), against the slab's rated live load or the ASCE 7 table value for the occupancy.

Every result is an engineering aid, not a stamped design.
"""

from __future__ import annotations

import math

from .data.pipe_specs import get_pipe_pressure
from .envelope import CalcResult, make_input
from .hydraulics._checks import BLOCKER, INFO, WARNING, make_check
from .hydraulics.catalog import normalize_nominal, resolve_material
from .hydraulics.units import f_to_c
from .hydraulics.water import water_si

CIT_THREE_MOMENT = "Clapeyron three-moment equation, continuous beam of equal spans (Hibbeler, Structural Analysis, ch. 10)"
CIT_SPANS = "DOC-0049 / 1,2,3 - Pipe Specs (wet lb/ft, maximum support span)"
CIT_ASCE7 = "ASCE 7-22 Table 4.3-1 uniform live loads (office 50 psf, assembly 100 psf)"
CIT_AID = "Engineering aid, not a stamped design: a licensed engineer confirms supports and slabs"

FLOOR_PSF = {"office": 50.0, "assembly": 100.0, "retail": 100.0, "roof": 20.0, "plaza": 100.0}
KG_M3_TO_LB_FT3 = 0.06242796


def beam_reactions(n, span: float, w: float, a: float = 0.0) -> list[float]:
    """``beamReactions``: support reactions of ``n`` equal spans of ``span`` carrying ``w`` per unit
    length, with an overhang ``a`` past each end support. Any consistent units."""
    n = max(1, int(n))
    m = [0.0] * (n + 1)
    m[0] = m[n] = (-w * a * a) / 2
    k = n - 1  # interior supports: M[i-1] + 4 M[i] + M[i+1] = -w L^2 / 2
    if k > 0:
        c = [0.0] * k
        d = [0.0] * k
        for i in range(k):
            rhs = (-w * span * span) / 2
            if i == 0:
                rhs -= m[0]
            if i == k - 1:
                rhs -= m[n]
            den = 4 - (c[i - 1] if i > 0 else 0)
            c[i] = 1 / den
            d[i] = (rhs - (d[i - 1] if i > 0 else 0)) / den
        for i in range(k - 1, -1, -1):
            m[i + 1] = d[i] - (c[i] * m[i + 2] if i < k - 1 else 0)
    r = [0.0] * (n + 1)
    for j in range(n):
        t = (m[j + 1] - m[j]) / span
        r[j] += (w * span) / 2 + t
        r[j + 1] += (w * span) / 2 - t
    r[0] += w * a
    r[n] += w * a
    return r


def _pressure_spec(material: str, nominal: str) -> dict | None:
    mat = resolve_material(material) or material
    nps = normalize_nominal(nominal)
    return get_pipe_pressure(mat, f'{nps}"') if nps else None


def pipe_hangers(segment: dict, *, spacing_ft: float | None = None, overhang_ft: float = 0.0, point_lb: float = 0.0) -> CalcResult:
    """Hangers for one exposed run: count, spacing, the heaviest reaction (lb) and the span check.

    ``segment`` is a model-2 segment row (``material``, ``nominal_size``, ``length_ft``, ``id``);
    ``spacing_ft`` the designer's hanger spacing (default: the DOC-0049 maximum span);
    ``point_lb`` a valve or fitting weight added to the worst hanger (conservative)."""
    sid = segment.get("id") or ""
    length = float(segment.get("length_ft") or 0)
    spec = _pressure_spec(segment.get("material") or "SCH40 PVC", segment.get("nominal_size") or "")
    warnings: list[str] = []
    checks: list[dict] = []
    if not spec or not spec.get("wet_lb_ft"):
        return CalcResult(calc="pipe_hangers", unit="lb", inputs={"segment": make_input(sid, "", "user")}, citations=[CIT_SPANS],
                          warnings=[f"No DOC-0049 weight for {segment.get('material')} {segment.get('nominal_size')}: hangers not sized."])
    wet = float(spec["wet_lb_ft"])
    max_span = spec.get("max_span_ft")
    spacing = float(spacing_ft) if spacing_ft else (float(max_span) if max_span else None)
    if spacing is None:
        warnings.append("DOC-0049 gives no maximum span for this pipe: give a hanger spacing.")
        spacing = length or 1.0
    n_spans = max(1, math.ceil(length / spacing - 1e-9)) if length > 0 else 1
    span = length / n_spans if length > 0 else spacing
    reactions = beam_reactions(n_spans, span, wet, float(overhang_ft or 0))
    worst = max(reactions) + float(point_lb or 0)
    if max_span and spacing > float(max_span) + 1e-9:
        checks.append(make_check("structure.hanger_span", BLOCKER, f"Segment {sid}: hangers {spacing:g} ft apart",
                                 f"Over the DOC-0049 maximum span of {max_span:g} ft for this pipe: add hangers.", "structure", ref=sid,
                                 value={"spacing_ft": spacing, "max_span_ft": max_span}))
    elif max_span is None:
        checks.append(make_check("structure.hanger_span_unknown", INFO, f"Segment {sid}: no DOC-0049 span for this pipe",
                                 "Use the manufacturer's support spacing.", "structure", ref=sid))
    return CalcResult(
        calc="pipe_hangers",
        value={"hangers": n_spans + 1, "spacing_ft": span, "max_span_ft": max_span, "wet_lb_ft": wet, "max_load_lb": worst,
               "reactions_lb": reactions, "total_lb": wet * (length + 2 * float(overhang_ft or 0)), "checks": checks},
        unit="",
        inputs={
            "length": make_input(length, "ft", "user"),
            "wet_weight": make_input(wet, "lb/ft", "lookup", "DOC-0049 wet_lb_ft"),
            "spacing": make_input(spacing, "ft", "user" if spacing_ft else "lookup", "DOC-0049 max_span_ft"),
        },
        formula="M[i-1] + 4M[i] + M[i+1] = -wL^2/2 ; R_j = wL/2 +/- (M[j+1] - M[j])/L",
        steps=[f"{n_spans} spans of {span:.2f} ft at {wet:g} lb/ft -> {n_spans + 1} hangers, worst {worst:.1f} lb"],
        citations=[CIT_THREE_MOMENT, CIT_SPANS, CIT_AID],
        warnings=warnings + [c["title"] for c in checks if c["severity"] != INFO],
        status="Okay" if not any(c["severity"] == BLOCKER for c in checks) else "Blocked",
    )


def floor_load(depth_in: float, area_sf: float, *, slab_capacity_psf: float | None = None, occupancy: str = "assembly",
               dead_psf: float = 0.0, water_temp_f: float = 68.0) -> CalcResult:
    """A full basin on a slab: the water's load (psf), plus the basin's own dead load, against the slab's
    rated live load (``slab_capacity_psf``) or the ASCE 7 value for ``occupancy``."""
    rho_lb_ft3 = water_si(f_to_c(float(water_temp_f))).rho * KG_M3_TO_LB_FT3
    water_psf = rho_lb_ft3 * float(depth_in) / 12
    psf = water_psf + float(dead_psf or 0)
    capacity = float(slab_capacity_psf) if slab_capacity_psf else FLOOR_PSF.get((occupancy or "").lower(), FLOOR_PSF["assembly"])
    source = "the slab's rated live load" if slab_capacity_psf else f"ASCE 7 {occupancy} live load"
    checks = []
    if psf > capacity:
        checks.append(make_check("structure.floor_load", BLOCKER, f"The basin loads the floor {psf:.0f} psf",
                                 f"Over {capacity:g} psf ({source}): have a structural engineer check the slab, or make the basin shallower.",
                                 "structure", value={"psf": psf, "capacity_psf": capacity}))
    elif psf > 0.8 * capacity:
        checks.append(make_check("structure.floor_load_margin", WARNING, f"The basin uses {psf / capacity:.0%} of the floor's capacity",
                                 "Little margin for people and maintenance loads on the same slab: confirm with the structural engineer.",
                                 "structure", value={"psf": psf, "capacity_psf": capacity}))
    return CalcResult(
        calc="floor_load",
        value={"water_psf": water_psf, "total_psf": psf, "capacity_psf": capacity, "total_lb": psf * float(area_sf), "checks": checks},
        unit="",
        inputs={
            "depth": make_input(float(depth_in), "in", "user", "water depth"),
            "area": make_input(float(area_sf), "sq ft", "prior_calc"),
            "capacity": make_input(capacity, "psf", "user" if slab_capacity_psf else "standard", source),
            "dead": make_input(float(dead_psf or 0), "psf", "user", "basin shell and finish"),
        },
        formula="q = gamma_w h + q_dead ; compare with the slab's live-load capacity",
        steps=[f"{rho_lb_ft3:.2f} lb/ft^3 x {float(depth_in) / 12:.3f} ft = {water_psf:.1f} psf (+ {float(dead_psf or 0):g} dead) vs {capacity:g} psf"],
        citations=[CIT_ASCE7, CIT_AID],
        warnings=[c["title"] for c in checks],
        status="Okay" if not checks else ("Blocked" if checks[0]["severity"] == BLOCKER else "Marginal"),
    )
