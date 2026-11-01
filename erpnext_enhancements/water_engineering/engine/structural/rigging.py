"""Truss and rigging for rentals and shows: the Digital Rain Curtain's
``rigging.js`` (``beamReactions``, ``rigPlan`` and its floor-load rating)
ported to US units and plain inputs.

``beam_reactions`` is the reusable piece: support reactions of a continuous
beam on n equal spans with an overhang past each end support, by the
three-moment equation (M[i−1] + 4M[i] + M[i+1] = −wL²/2 for interior
supports, solved with the Thomas algorithm), then span by span. It matches
the JavaScript to machine precision (golden vectors in the tests).

``rig_plan`` keeps the DRC's three mounts (truss towers on base plates, hung
from the ceiling, truss flown on chain hoists), its truss weights, base plate,
hoist sizes and 1.25 dynamic allowance, but takes the hung load as numbers
instead of a DRC project. ``floor_rating`` is its basin floor check: office
50 psf, lobby or assembly 100 psf (ASCE 7 Table 4.3-1), else the venue's
engineer.

Planning numbers: a qualified rigger and the venue approve the rigging plan.
"""

from __future__ import annotations

import math

from ..envelope import CalcResult, make_input
from .defaults import DISCLAIMER

CIT_DRC = "Digital Rain Curtain src/rigging.js (beamReactions, rigPlan), ported"
CIT_THREE_MOMENT = "Clapeyron's three-moment equation for continuous beams"
CIT_ASCE7_FLOOR = "ASCE 7-16 Table 4.3-1 (offices 50 psf; lobbies and assembly areas 100 psf)"
CIT_RIGGER = "ANSI E1.21 / ESTA practice: a qualified rigger approves; truss maker's load tables govern"

KG_TO_LB = 2.2046226218
M_TO_FT = 1.0 / 0.3048

# DRC TRUSSES (kg/m, size m) in lb/ft and ft
TRUSSES = {
    "tri12": {"label": "12 in triangle truss", "lb_ft": 3.2 * KG_TO_LB / M_TO_FT, "size_ft": 0.29 * M_TO_FT},
    "box12": {"label": "12 in box truss", "lb_ft": 4.5 * KG_TO_LB / M_TO_FT, "size_ft": 0.29 * M_TO_FT},
    "box16": {"label": "16 in box truss", "lb_ft": 6.2 * KG_TO_LB / M_TO_FT, "size_ft": 0.39 * M_TO_FT},
}
PLATE = {"lb": 20 * KG_TO_LB, "side_ft": 0.61 * M_TO_FT}  # 24 in steel base plate
CORNER_LB = 5 * KG_TO_LB
TOWER_OUT_FT = 0.5 * M_TO_FT
HOISTS = [(500.0, "1/4 ton (500 lb)"), (1000.0, "1/2 ton (1,000 lb)"), (2000.0, "1 ton (2,000 lb)"),
          (4000.0, "2 ton (4,000 lb)")]
FLOOR_OFFICE_PSF, FLOOR_ASSEMBLY_PSF = 50.0, 100.0


def _reactions(n: int, span: float, w: float, a: float = 0.0) -> list[float]:
    n = max(1, int(n))
    m = [0.0] * (n + 1)
    m[0] = m[n] = -w * a * a / 2.0
    k = n - 1
    if k > 0:
        c, d = [0.0] * k, [0.0] * k
        for i in range(k):
            rhs = -w * span * span / 2.0
            if i == 0:
                rhs -= m[0]
            if i == k - 1:
                rhs -= m[n]
            den = 4.0 - (c[i - 1] if i > 0 else 0.0)
            c[i] = 1.0 / den
            d[i] = (rhs - (d[i - 1] if i > 0 else 0.0)) / den
        for i in range(k - 1, -1, -1):
            m[i + 1] = d[i] - (c[i] * m[i + 2] if i < k - 1 else 0.0)
    r = [0.0] * (n + 1)
    for j in range(n):
        t = (m[j + 1] - m[j]) / span
        r[j] += w * span / 2.0 + t
        r[j + 1] += w * span / 2.0 - t
    r[0] += w * a
    r[n] += w * a
    return r


def beam_reactions(n_spans: int, span: float, w: float, overhang: float = 0.0) -> CalcResult:
    """Support reactions (left to right) of a beam carrying ``w`` per unit
    length on ``n_spans`` equal spans, with ``overhang`` past each end support.
    Units are the caller's (lb and ft, or kg and m)."""
    r = _reactions(n_spans, span, w, overhang)
    return CalcResult(
        calc="structural_beam_reactions",
        value=r,
        unit="(w × length)",
        inputs={"n_spans": make_input(n_spans, "", "user"), "span": make_input(span, "", "user"),
                "w": make_input(w, "", "user"), "overhang": make_input(overhang, "", "user")},
        formula="M[i−1] + 4M[i] + M[i+1] = −wL²/2; R_j += wL/2 ± (M[j+1] − M[j])/L; end supports += w·a",
        steps=[f"reactions = {', '.join(f'{x:.4f}' for x in r)} (sum {sum(r):.4f} = w × {n_spans * span + 2 * overhang:g})"],
        citations=[CIT_THREE_MOMENT, CIT_DRC],
        warnings=[DISCLAIMER],
    )


def floor_rating(basin_psf: float) -> CalcResult:
    """The DRC's basin floor verdict: office floor, assembly floor, or the
    venue's structural engineer."""
    q = float(basin_psf)
    rating = "office" if q <= FLOOR_OFFICE_PSF else "assembly" if q <= FLOOR_ASSEMBLY_PSF else "engineer"
    text = {"office": "within an office floor's 50 psf rating",
            "assembly": "over an office floor's 50 psf: needs a lobby or assembly-rated floor (100 psf)",
            "engineer": "over an assembly floor's 100 psf: the venue's structural engineer approves it, or use "
                        "shallower water"}[rating]
    return CalcResult(
        calc="structural_floor_rating", value=rating, unit="",
        inputs={"pressure": make_input(q, "psf", "prior_calc")}, formula="≤ 50 office; ≤ 100 assembly; else engineer",
        steps=[f"{q:.1f} psf: {text}"], citations=[CIT_ASCE7_FLOOR, CIT_DRC], warnings=[DISCLAIMER],
        status={"office": "ok", "assembly": "warn", "engineer": "bad"}[rating],
    )


def rig_plan(
    hung_lb_per_ft: float, hung_length_ft: float, hanger_spans: int, hanger_span_ft: float, overhang_ft: float = 0.0,
    curtain_width_ft: float | None = None, mount: str = "towers", truss: str = "box12", header_height_ft: float = 10.0,
    picks: int = 2, riser_lb: float = 0.0, feed: str = "end", dyn: float = 1.25,
) -> CalcResult:
    """Loads on every hanger, tower or hoist for a hung manifold (or any
    uniform hung load), per the DRC's rigging plan.

    ``hung_lb_per_ft`` × ``hung_length_ft`` is the wet hung load; it hangs from
    ``hanger_spans`` equal spans (plus ``overhang_ft`` each end). Towers stand
    0.5 m (1.64 ft) past each end of ``curtain_width_ft``; the top truss spans
    between them. ``riser_lb`` is one water-filled supply riser: the right-hand
    tower carries it (each tower carries one when ``feed`` is "both"); flown,
    half of it hangs from the last pick (and the first, fed from both ends)."""
    mount = mount if mount in ("towers", "ceiling", "flown") else "towers"
    tr = TRUSSES.get(truss, TRUSSES["box12"])
    dyn = max(1.0, float(dyn))
    if not (float(hanger_span_ft or 0) > 0 and int(hanger_spans or 0) >= 1):
        return CalcResult(calc="structural_rig_plan", value=None, citations=[CIT_DRC],
                          warnings=[DISCLAIMER, "Hanger spacing must be > 0 with at least one span."],
                          status="incomplete")
    w = float(hung_lb_per_ft)
    hung_lb = w * float(hung_length_ft)
    width = float(curtain_width_ft if curtain_width_ft is not None else hung_length_ft)
    hangers = [{"x_ft": i * hanger_span_ft, "lb": lb, "design_lb": lb * dyn}
               for i, lb in enumerate(_reactions(hanger_spans, hanger_span_ft, w, overhang_ft))]
    span = width + 2 * TOWER_OUT_FT
    truss_lb = 0.0 if mount == "ceiling" else tr["lb_ft"] * span
    header = towers = pick_rows = None
    steps = [f"hung load {w:.3f} lb/ft × {hung_length_ft:g} ft = {hung_lb:.1f} lb on {hanger_spans + 1} hangers"]
    warnings = [DISCLAIMER]
    status = "ok"
    tower_h = tower_lb = 0.0
    if mount != "ceiling":
        lb = truss_lb + hung_lb
        header = {"span_ft": span, "lb": lb, "lb_ft": lb / span, "moment_lb_ft": lb * span / 8.0}
        steps.append(f"top truss {span:.3f} ft: {lb:.1f} lb, M = {lb * span / 8:.1f} lb·ft (check the maker's table)")
    if mount == "towers":
        tower_h = header_height_ft + tr["size_ft"] / 2.0
        tower_lb = tr["lb_ft"] * tower_h + PLATE["lb"] + CORNER_LB
        towers = []
        for side in ("left", "right"):
            risers = 1 if feed == "both" or side == "right" else 0
            lb = header["lb"] / 2.0 + tower_lb + risers * riser_lb
            towers.append({"side": side, "lb": lb, "psf": lb / PLATE["side_ft"] ** 2})
        steps.append(f"towers {tower_h:.2f} ft: {towers[0]['lb']:.1f} / {towers[1]['lb']:.1f} lb on 24 in plates")
    elif mount == "flown":
        k = max(2, math.floor(float(picks) + 0.5))  # Math.round, not Python's round-half-even
        sp = span / (k - 1)
        pick_rows = [{"x_ft": i * sp, "lb": lb} for i, lb in enumerate(_reactions(k - 1, sp, header["lb_ft"], 0.0))]
        pick_rows[-1]["lb"] += riser_lb / 2.0  # hose or pipe dangling from the truss end
        if feed == "both":
            pick_rows[0]["lb"] += riser_lb / 2.0
        for p in pick_rows:
            p["design_lb"] = p["lb"] * dyn
            hoist = next((h for h in HOISTS if h[0] >= p["design_lb"]), None)
            p["hoist"] = hoist[1] if hoist else None
            if hoist is None:
                status = "bad"
                warnings.append(f"A pick carries {p['design_lb']:.0f} lb with the allowance, more than a 2-ton "
                                "hoist: add picks.")
        steps.append(f"{k} picks at {sp:.3f} ft: " + ", ".join(f"{p['lb']:.1f} lb" for p in pick_rows))
    points = hangers if mount == "ceiling" else pick_rows
    heaviest = max(points, key=lambda p: p["lb"]) if points else None
    return CalcResult(
        calc="structural_rig_plan",
        value={"mount": mount, "truss": tr["label"], "dyn": dyn, "hangers": hangers, "header": header,
               "towers": towers, "picks": pick_rows, "heaviest_point": heaviest, "tower_height_ft": tower_h,
               "tower_lb": tower_lb, "frame_lb": truss_lb + (2 * tower_lb if mount == "towers" else 0.0),
               "hung_lb": hung_lb},
        unit="lb",
        inputs={"mount": make_input(mount, "", "user"), "truss": make_input(truss, "", "user"),
                "dyn": make_input(dyn, "", "default", CIT_DRC)},
        formula="hangers = beam_reactions(n, s, w, a) × dyn; header = truss + hung load, M = WL/8; "
                "tower = header/2 + tower + riser; picks = beam_reactions(K−1, span/(K−1), header w)",
        steps=steps,
        citations=[CIT_DRC, CIT_THREE_MOMENT, CIT_RIGGER],
        warnings=[*warnings, "A qualified rigger approves the rigging plan: rated hardware and a safety cable at " "every hang point."],
        status=status,
    )


def show_basin_floor(length_ft: float, width_ft: float, water_depth_ft: float, pan_psf: float = 5.12,
                     lights_lb: float = 0.0) -> CalcResult:
    """A rental or show basin on a venue floor (the DRC's basin load: water,
    pan and grate at 25 kg/m² = 5.12 psf, lights) and its floor rating."""
    area = float(length_ft) * float(width_ft)
    water = area * float(water_depth_ft) * 62.4
    pan = area * float(pan_psf)
    total = water + pan + float(lights_lb)
    psf = total / area if area else 0.0
    rating = floor_rating(psf)
    return CalcResult(
        calc="structural_show_basin_floor",
        value={"area_sqft": area, "water_lb": water, "pan_lb": pan, "total_lb": total, "psf": psf,
               "rating": rating.value},
        unit="psf",
        inputs={"water_depth": make_input(water_depth_ft, "ft", "user")},
        formula="psf = (water + pan + lights) / area",
        steps=[f"{water:.0f} + {pan:.0f} + {lights_lb:.0f} = {total:.0f} lb over {area:.1f} ft² = {psf:.1f} psf", *rating.steps],
        citations=[CIT_DRC, CIT_ASCE7_FLOOR],
        warnings=[DISCLAIMER],
        status=rating.status,
    )

