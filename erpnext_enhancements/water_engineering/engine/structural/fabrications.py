"""Fabricated feature parts: stainless weir lips and scuppers, stone and tile
veneer attachment, and pedestals and plinths.

A weir lip's sag between its supports sets how even the water sheet is: a
lip that droops more than the edge tolerance (1/16 in for a sheet wall,
the family's ``tolerance_in``) pulls the sheet to its low points. So the lip
is checked as a beam (simple, continuous or cantilevered) under its own
weight and the water standing on it, and the support spacing that keeps the
sag inside the tolerance is reported.

Veneer follows TMS 402: adhered units up to 15 psf, anchored veneer one
anchor per 2.67 ft² (75% of that in Seismic Design Category D and above),
spaced at most 32 in across and 25 in up. Pedestals and plinths are rigid
blocks: overturning and sliding factors of safety and the bearing pressure
under wind or seismic load (ASCE 7 Ch. 13 for the seismic force on a
component).

DOC-0119 governs materials: solid surface is a facade and not a water
barrier, granite slab 3 cm (2 cm on walls needs backing), tempered-glass holes
at least 1/4 in or 2–3 thicknesses from the edge.
"""

from __future__ import annotations

import math

from ..envelope import CalcResult, make_input
from .defaults import DISCLAIMER, merged_rules
from .loads import soil_properties
from .steel import MATERIALS

CIT_BEAM = ("Beam deflection: δ = c·w·L⁴/(E·I), c = 5/384 simple, 2.65/384 continuous (end span of three), "
            "1/384 fixed-fixed, 1/8 cantilever")
CIT_TOLERANCE = "Weir edge tolerance (feature input tolerance_in, 1/16 in by default)"
CIT_TMS_ADHERED = "TMS 402-16 §13.3 (adhered veneer: units ≤ 2-5/8 in thick, ≤ 15 psf)"
CIT_TMS_ANCHORED = ("TMS 402-16 §12.2.2.5 (anchored veneer: one anchor per 2.67 ft², ≤ 32 in horizontal, "
                    "≤ 25 in vertical) and §12.2.2.10 (75% of the area in SDC D and above)")
CIT_ASCE7_FP = "ASCE 7-16 §13.3.1 (Fp = 0.4·ap·SDS·Wp·(1 + 2z/h)/(Rp/Ip), 0.3 ≤ Fp/(SDS·Ip·Wp) ≤ 1.6)"
CIT_DOC0119_MATERIALS = "DOC-0119 Fountain Design Guidelines, Materials (solid surface, granite, glass)"
CIT_DENSITY = "Typical unit weights of stone and tile (ASCE 7-16 Table C3.1-2 and manufacturers' data)"

DENSITY_PCF = {
    "granite": 168.0, "marble": 170.0, "limestone": 145.0, "travertine": 155.0, "slate": 172.0,
    "sandstone": 145.0, "porcelain": 150.0, "ceramic": 125.0, "cast_stone": 140.0, "glass": 156.0,
    "solid_surface": 106.0,
}
_DEFL_COEF = {"simple": 5.0 / 384.0, "fixed": 1.0 / 384.0, "continuous": 2.65 / 384.0, "cantilever": 1.0 / 8.0}
_MOMENT_COEF = {"simple": 1 / 8.0, "fixed": 1 / 12.0, "continuous": 1 / 10.0, "cantilever": 1 / 2.0}


def weir_lip_sag(
    span_in: float, thickness_in: float, flange_in: float, lip_width_in: float, water_depth_in: float = 0.0,
    material: str = "304SS", support: str = "continuous", tolerance_in: float = 0.0625, rules: dict | None = None,
) -> CalcResult:
    """Sag of a bent-plate weir lip (a horizontal lip ``lip_width_in`` wide on
    a down-turned stiffening flange ``flange_in`` deep, both ``thickness_in``)
    between supports ``span_in`` apart, against the edge tolerance. A
    scupper is the same with ``support="cantilever"``."""
    mat = MATERIALS.get(material, MATERIALS["304SS"])
    e = mat["e_ksi"] * 1000.0
    t, hf, wl = float(thickness_in), float(flange_in), float(lip_width_in)
    # L section, lip on top: horizontal leg (wl × t) and the flange below it (t × (hf − t))
    a1, y1 = wl * t, hf - t / 2.0
    a2, y2 = t * (hf - t), (hf - t) / 2.0
    area = a1 + a2
    yc = (a1 * y1 + a2 * y2) / area
    inertia = wl * t ** 3 / 12 + a1 * (y1 - yc) ** 2 + t * (hf - t) ** 3 / 12 + a2 * (y2 - yc) ** 2
    c_far = max(yc, hf - yc)
    w = 0.0361 * float(water_depth_in) * wl + mat["pcf"] / 1728.0 * area  # lb/in
    key = support if support in _DEFL_COEF else "continuous"
    k = _DEFL_COEF[key]
    span = float(span_in)
    sag = k * w * span ** 4 / (e * inertia)
    stress = _MOMENT_COEF[key] * w * span ** 2 * c_far / inertia
    allow = 0.6 * mat["fy_ksi"] * 1000.0
    max_span = (float(tolerance_in) * e * inertia / (k * w)) ** 0.25 if w > 0 else None
    ok = sag <= float(tolerance_in) + 1e-12 and stress <= allow
    return CalcResult(
        calc="structural_weir_lip_sag",
        value={"sag_in": sag, "tolerance_in": float(tolerance_in), "max_support_spacing_in": max_span,
               "stress_psi": stress, "allowable_psi": allow, "I_in4": inertia, "w_lb_per_in": w,
               "weight_lb_per_ft": mat["pcf"] / 1728.0 * area * 12.0, "ok": ok},
        unit="in",
        inputs={"span": make_input(span, "in", "user"), "thickness": make_input(t, "in", "user"),
                "flange": make_input(hf, "in", "user"), "tolerance": make_input(tolerance_in, "in", "user",
                                                                              CIT_TOLERANCE)},
        formula="δ = c·w·L⁴/(E·I); L_max = (tol·E·I/(c·w))^¼",
        steps=[f"I = {inertia:.5f} in⁴ (L section, centroid {yc:.3f} in up); w = {w:.5f} lb/in",
               f"sag = {k:.5f} × {w:.5f} × {span:g}⁴ / ({e:g} × {inertia:.5f}) = {sag:.5f} in vs {tolerance_in:g} in",
               ("no load: any support spacing holds the tolerance" if max_span is None else
                f"supports at most {max_span:.1f} in apart for the tolerance") + f"; stress {stress:.0f} psi"],
        citations=[CIT_BEAM, CIT_TOLERANCE],
        warnings=[DISCLAIMER] + ([] if ok else [f"Lip sags {sag:.4f} in over {span:g} in, more than the "
                                               f"{tolerance_in:g} in tolerance: supports at {max_span:.0f} in or a "
                                               "deeper flange."]),
        status="ok" if ok else "bad",
    )


def veneer_attachment(
    area_sqft: float, thickness_in: float, material: str = "granite", method: str = "anchored", sdc: str = "D",
    anchor_capacity_lb: float | None = None, rules: dict | None = None,
) -> CalcResult:
    """Weight per square foot and, for anchored veneer, the anchor count and
    spacing (the TMS area rule, and the anchors' rated capacity with a factor
    of safety when it is given)."""
    rules = merged_rules(rules)
    dens = DENSITY_PCF.get(str(material).lower(), DENSITY_PCF["granite"])
    psf = dens * float(thickness_in) / 12.0
    area = float(area_sqft)
    total = psf * area
    adhered = str(method).lower() == "adhered"
    steps = [f"{material}: {dens:g} pcf × {thickness_in:g} in = {psf:.2f} psf; {total:.0f} lb over {area:g} ft²"]
    warnings = [DISCLAIMER]
    if adhered:
        ok = psf <= 15.0 + 1e-9 and float(thickness_in) <= 2.625 + 1e-9
        anchors, per_anchor_area, spacing = 0, None, None
        if not ok:
            warnings.append(f"{psf:.1f} psf is over the 15 psf adhered-veneer limit: anchor it.")
        cits = [CIT_TMS_ADHERED, CIT_DENSITY]
    else:
        per_anchor_area = 2.67 * (0.75 if str(sdc).upper() in ("D", "E", "F") else 1.0)
        n_area = math.ceil(area / per_anchor_area - 1e-9)
        n_cap = 0
        if anchor_capacity_lb:
            n_cap = math.ceil(total * float(rules["veneer_anchor_fs"]) / float(anchor_capacity_lb) - 1e-9)
        anchors = max(n_area, n_cap, 1)
        spacing = math.sqrt(area / anchors) * 12.0
        ok = True
        steps.append(f"anchors: max(ceil({area:g}/{per_anchor_area:.2f}) = {n_area}, capacity {n_cap}) = {anchors}; "
                     f"about {spacing:.1f} in each way (≤ 32 in across, ≤ 25 in up)")
        if spacing > 25.0:
            anchors = max(anchors, math.ceil(area * 144.0 / (25.0 * 25.0) - 1e-9))
            spacing = math.sqrt(area / anchors) * 12.0
            steps.append(f"spacing capped at 25 in: {anchors} anchors")
        cits = [CIT_TMS_ANCHORED, CIT_DENSITY]
    return CalcResult(
        calc="structural_veneer_attachment",
        value={"weight_psf": psf, "total_lb": total, "method": "adhered" if adhered else "anchored",
               "anchors": anchors, "area_per_anchor_sqft": per_anchor_area, "spacing_in": spacing, "ok": ok},
        unit="psf",
        inputs={"material": make_input(material, "", "user"), "thickness": make_input(thickness_in, "in", "user"),
                "sdc": make_input(sdc, "", "prior_calc")},
        formula="psf = density × t; anchors = max(area / area-per-anchor, W × FS / capacity)",
        steps=steps,
        citations=[*cits, CIT_DOC0119_MATERIALS],
        warnings=[*warnings, "Stainless anchors and clips only in wet areas."],
        status="ok" if ok else "bad",
    )


def pedestal_check(
    length_ft: float, width_ft: float, height_ft: float, unit_weight_pcf: float = 150.0, top_load_lb: float = 0.0,
    top_cg_ft: float | None = None, wind_lb: float = 0.0, wind_height_ft: float | None = None, sds: float | None = None,
    on: str = "slab", soil_class=None, bearing_psf=None, material: str = "concrete", rules: dict | None = None,
) -> CalcResult:
    """A solid pedestal or plinth (and whatever stands on it) as a rigid
    block: overturning about the narrow side, sliding, and bearing. The
    lateral load is the larger of wind and the ASCE 7 component force
    (ap 1.0, Rp 1.5, at grade)."""
    rules = merged_rules(rules)
    lng, wid, h = float(length_ft), float(width_ft), float(height_ft)
    w_block = unit_weight_pcf * lng * wid * h
    w_top = float(top_load_lb)
    w = w_block + w_top
    top_cg = float(top_cg_ft if top_cg_ft is not None else h)
    seismic = 0.0
    if sds:
        fp = 0.4 * 1.0 * float(sds) * w / 1.5
        seismic = min(max(fp, 0.3 * float(sds) * w), 1.6 * float(sds) * w)
    m_wind = float(wind_lb) * float(wind_height_ft if wind_height_ft is not None else h)
    m_seis = seismic * (w_block * h / 2.0 + w_top * top_cg) / w if w else 0.0
    use_seis = m_seis > m_wind
    lateral, mo = (seismic, m_seis) if use_seis else (float(wind_lb), m_wind)
    b = min(lng, wid)
    w_resist = w * (0.9 - 0.2 * float(sds or 0)) if use_seis else 0.9 * w  # ASCE 7: (0.9 − 0.2SDS)D / 0.9D
    mr = w_resist * b / 2.0
    fs_ot = mr / mo if mo > 0 else None
    mu = 0.6 if str(on).lower() == "slab" else soil_properties(soil_class, bearing_psf).value["friction"]
    fs_sl = mu * w_resist / lateral if lateral > 0 else None
    e = mo / w if w else 0.0
    area = lng * wid
    q = w / area * (1 + 6 * e / b) if e <= b / 6 else (2 * w / (3 * max(lng, wid) * (b / 2 - e)) if e < b / 2 else None)
    allow = None if str(on).lower() == "slab" else soil_properties(soil_class, bearing_psf).value["bearing_psf"]
    need = float(rules["wall_fs"])
    ok = q is not None and (fs_ot is None or fs_ot + 1e-9 >= need) and (fs_sl is None or fs_sl + 1e-9 >= need) and \
        (allow is None or q <= allow * (1.333 if use_seis else 1.0))
    return CalcResult(
        calc="structural_pedestal",
        value={"weight_lb": w, "lateral_lb": lateral, "governs": "seismic" if use_seis else "wind",
               "fs_overturning": fs_ot, "fs_sliding": fs_sl, "bearing_psf": q, "allowable_psf": allow,
               "concrete_cuft": lng * wid * h if str(material).lower() == "concrete" else 0.0, "ok": ok},
        unit="",
        inputs={"size": make_input(f"{lng:g} × {wid:g} × {h:g}", "ft", "user"),
                "top_load": make_input(w_top, "lb", "user"), "sds": make_input(sds, "g", "prior_calc")},
        formula="FS_OT = 0.9W·B/2 / (F·h); FS_slide = μ·0.9W/F; q = W/A(1 + 6e/B)",
        steps=[f"W = {w_block:.0f} block + {w_top:.0f} top = {w:.0f} lb; lateral {lateral:.0f} lb "
               f"({'seismic' if use_seis else 'wind'}), M = {mo:.0f} lb·ft",
               f"FS overturning {fs_ot if fs_ot is None else round(fs_ot, 2)}, sliding "
               f"{fs_sl if fs_sl is None else round(fs_sl, 2)}; " + ("it tips: the resultant leaves the base" if q is None
                                                                else f"q = {q:.0f} psf")],
        citations=[CIT_ASCE7_FP, "IBC 2021 §1807.2.3 (FS 1.5 against overturning and sliding)",
                   "ACI 318-19 Table 22.9.4.2 (μ = 0.6 concrete on hardened concrete)"],
        warnings=[DISCLAIMER] + ([] if ok else ["Pedestal tips, slides or overloads the ground: widen the base or "
                                               "anchor it."]),
        status="ok" if ok else "bad",
    )
