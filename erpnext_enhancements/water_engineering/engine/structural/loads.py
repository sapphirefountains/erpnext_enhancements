"""Site loads for the structure: soil, frost, surcharge, seismic and wind.

DOC-0119 says nothing about any of these, so every value here comes from a
public standard and says which:

* soil bearing, lateral bearing and sliding friction: IBC Table 1806.2
  presumptive values, unless a geotechnical report gives a number;
* frost: footings below the frost line (IBC 1809.5); Salt Lake County's
  design frost depth is 30 in;
* surcharge: ASCE 7 Table 4.3-1 uniform live loads (plaza trafficked by
  trucks 250 psf, pedestrian 100 psf) and AASHTO's equivalent soil height for
  vehicles next to a wall;
* seismic: ASCE 7-16 Chapter 11 site coefficients and design category from
  Ss and S1 (read them for the address from the USGS / ASCE Hazard Tool), and
  ACI 350.3's hydrodynamic loads on a rectangular basin's walls;
* wind: ASCE 7-16 Chapter 29 for a solid or partly open freestanding wall or
  sign, which is what a water wall or a screen is to the wind.

Engineering aid for quantities and early checks, not a stamped structural
design.
"""

from __future__ import annotations

import math
from itertools import pairwise

from ..envelope import CalcResult, make_input
from .defaults import DISCLAIMER, merged_rules

CIT_IBC_1806 = "IBC 2021 Table 1806.2 (presumptive load-bearing and lateral values)"
CIT_IBC_FROST = "IBC 2021 §1809.5 (footings below the frost line); Salt Lake County design frost depth 30 in"
CIT_ASCE7_LIVE = "ASCE 7-16 Table 4.3-1 (uniform live loads: sidewalks and driveways subject to trucking 250 psf)"
CIT_AASHTO_HEQ = "AASHTO LRFD Table 3.11.6.4-2 (equivalent soil height for vehicular load on retaining walls)"
CIT_ASCE7_SITE = "ASCE 7-16 Tables 11.4-1 and 11.4-2 (site coefficients Fa, Fv); §11.4.4 SDS = 2/3 Fa Ss, SD1 = 2/3 Fv S1"
CIT_ASCE7_SDC = "ASCE 7-16 Tables 11.6-1 and 11.6-2 (seismic design category)"
CIT_USGS = "USGS Seismic Design Maps / ASCE Hazard Tool (https://ascehazardtool.org): Ss, S1 for the address"
CIT_ACI3503 = "ACI 350.3-06 §5.3 and Ch. 9 (rectangular tanks: impulsive and convective components)"
CIT_ASCE7_WIND = "ASCE 7-16 §29.3 (design wind loads on solid freestanding walls and signs): F = qh G Cf As"
CIT_ASCE7_KZ = "ASCE 7-16 Table 26.10-1 and Table 26.11-1 (velocity pressure exposure coefficient Kz)"
CIT_ASCE7_CF = "ASCE 7-16 Figure 29.3-1 (Cf, Case A) with its porosity reduction (1 − (1 − ε)^1.5)"
CIT_ACI318_FREEZE = "ACI 318-19 Table 19.3.1.1 (exposure class F2: freezing and thawing with frequent water contact)"

# IBC Table 1806.2: class -> (description, vertical bearing psf, lateral bearing psf/ft, friction, cohesion psf)
PRESUMPTIVE_SOIL = {
    1: {"label": "Crystalline bedrock", "bearing_psf": 12000, "lateral_psf_per_ft": 1200, "friction": 0.70,
        "cohesion_psf": 0},
    2: {"label": "Sedimentary and foliated rock", "bearing_psf": 4000, "lateral_psf_per_ft": 400, "friction": 0.35,
        "cohesion_psf": 0},
    3: {"label": "Sandy gravel and gravel (GW, GP)", "bearing_psf": 3000, "lateral_psf_per_ft": 200, "friction": 0.35,
        "cohesion_psf": 0},
    4: {"label": "Sand, silty sand, clayey sand, silty gravel, clayey gravel (SW, SP, SM, SC, GM, GC)",
        "bearing_psf": 2000, "lateral_psf_per_ft": 150, "friction": 0.25, "cohesion_psf": 0},
    5: {"label": "Clay, sandy clay, silty clay, clayey silt, silt, sandy silt (CL, ML, MH, CH)",
        "bearing_psf": 1500, "lateral_psf_per_ft": 100, "friction": 0.0, "cohesion_psf": 130},
}

SURCHARGE_PSF = {"none": 0.0, "landscape": 0.0, "pedestrian": 100.0, "plaza": 250.0}

# ASCE 7-16 Table 11.4-1 (Fa) at Ss = 0.25, 0.5, 0.75, 1.0, 1.25, 1.5 and Table 11.4-2 (Fv) at
# S1 = 0.1 .. 0.6; linear interpolation between columns is permitted. "E" stops where the
# standard asks for a site-specific study.
_SS_COLS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5)
_S1_COLS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
FA = {"A": (0.8,) * 6, "B": (0.9,) * 6, "C": (1.3, 1.3, 1.2, 1.2, 1.2, 1.2), "D": (1.6, 1.4, 1.2, 1.1, 1.0, 1.0),
      "E": (2.4, 1.7, 1.3, None, None, None)}
FV = {"A": (0.8,) * 6, "B": (0.8,) * 6, "C": (1.5, 1.5, 1.5, 1.5, 1.5, 1.4), "D": (2.4, 2.2, 2.0, 1.9, 1.8, 1.7),
      "E": (4.2, None, None, None, None, None)}

# ASCE 7-16 Table 26.11-1: exposure -> (alpha, zg ft)
EXPOSURE = {"B": (7.0, 1200.0), "C": (9.5, 900.0), "D": (11.5, 700.0)}
# ASCE 7-16 Figure 29.3-1, Case A, row s/h = 1 (a wall standing on the ground), by aspect B/s
_CF_BS = (0.05, 0.1, 0.2, 0.5, 1, 2, 4, 5, 10, 20, 30, 45)
_CF_GROUND = (1.80, 1.70, 1.65, 1.55, 1.45, 1.40, 1.35, 1.35, 1.30, 1.30, 1.30, 1.30)
CF_ELEVATED_BOUND = 2.0  # no Case A entry exceeds it; used when the panel stands clear of the ground


def _interp(x: float, xs, ys):
    if x <= xs[0]:
        return ys[0]
    for (x0, y0), (x1, y1) in pairwise(zip(xs, ys, strict=True)):
        if x <= x1:
            if y0 is None or y1 is None:
                return None
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return ys[-1]


def soil_properties(soil_class=None, bearing_psf=None) -> CalcResult:
    """Allowable bearing, lateral bearing and sliding resistance. A
    geotechnical value (``bearing_psf``) wins; otherwise the IBC presumptive
    value for ``soil_class`` 1–5; with neither, class 5 (the IBC's lowest,
    and what an unknown Wasatch Front lot should be assumed to be)."""
    cls = int(soil_class) if soil_class not in (None, "") else 5
    row = dict(PRESUMPTIVE_SOIL.get(cls, PRESUMPTIVE_SOIL[5]))
    src = "lookup"
    if bearing_psf not in (None, ""):
        row["bearing_psf"] = float(bearing_psf)
        src = "user"
    return CalcResult(
        calc="structural_soil_properties",
        value={"soil_class": cls, **row},
        unit="psf",
        inputs={"soil_class": make_input(cls, "", "user" if soil_class else "default", CIT_IBC_1806),
                "bearing": make_input(row["bearing_psf"], "psf", src, CIT_IBC_1806)},
        formula="IBC Table 1806.2 by soil class, unless a geotechnical report gives the bearing",
        steps=[f"class {cls} ({row['label']}): bearing {row['bearing_psf']:g} psf, lateral "
               f"{row['lateral_psf_per_ft']:g} psf/ft, friction {row['friction']:g}, cohesion {row['cohesion_psf']:g} psf"],
        citations=[CIT_IBC_1806],
        warnings=[DISCLAIMER] + ([] if soil_class or bearing_psf else
                                 ["No soil class or geotechnical value given: IBC class 5 assumed."]),
    )


def surcharge_load(kind: str = "none", wall_height_ft: float = 0.0, soil_pcf: float = 120.0) -> CalcResult:
    """Uniform surcharge behind a wall: pedestrian 100 psf, plaza with truck
    traffic 250 psf (ASCE 7), or a vehicle at the wall as AASHTO's equivalent
    height of soil (5 ft for a 5 ft wall, 3.5 ft at 10 ft, 2 ft at 20 ft)."""
    key = str(kind or "none").lower()
    if key in ("vehicle", "h20", "h-20", "drive"):
        heq = _interp(max(float(wall_height_ft), 5.0), (5.0, 10.0, 20.0), (5.0, 3.5, 2.0))
        q = heq * float(soil_pcf)
        steps = [f"vehicle: heq = {heq:.2f} ft for a {wall_height_ft:g} ft wall; q = {heq:.2f} × {soil_pcf:g} = {q:.0f} psf"]
        cits = [CIT_AASHTO_HEQ]
    else:
        q = SURCHARGE_PSF.get(key, 0.0)
        steps = [f"{key}: {q:g} psf"]
        cits = [CIT_ASCE7_LIVE]
    return CalcResult(calc="structural_surcharge", value=q, unit="psf",
                      inputs={"kind": make_input(key, "", "user")}, formula="ASCE 7 live load or AASHTO heq × γs",
                      steps=steps, citations=cits, warnings=[DISCLAIMER])


def frost_check(footing_bottom_in: float, frost_depth_in: float | None = None, rules: dict | None = None) -> CalcResult:
    """Is the bottom of a footing below the frost line?"""
    rules = merged_rules(rules)
    frost = float(frost_depth_in if frost_depth_in not in (None, "") else rules["frost_depth_in"])
    ok = float(footing_bottom_in) + 1e-9 >= frost
    return CalcResult(
        calc="structural_frost_check",
        value={"footing_bottom_in": float(footing_bottom_in), "frost_depth_in": frost, "ok": ok},
        unit="in",
        inputs={"frost_depth": make_input(frost, "in", "user" if frost_depth_in else "standard", CIT_IBC_FROST)},
        formula="bottom of footing ≥ frost depth",
        steps=[f"{float(footing_bottom_in):g} in vs {frost:g} in frost depth"],
        citations=[CIT_IBC_FROST],
        warnings=[DISCLAIMER] + ([] if ok else [
            f"Footing bottom {float(footing_bottom_in):g} in is above the {frost:g} in frost line: frost heave."]),
        status="ok" if ok else "bad",
    )


def freeze_thaw_notes(frost_depth_in: float | None = None, rules: dict | None = None) -> CalcResult:
    """What Utah winters ask of a fountain's concrete and its footings."""
    rules = merged_rules(rules)
    frost = float(frost_depth_in if frost_depth_in not in (None, "") else rules["frost_depth_in"])
    notes = [
        f"Footings and wall bases bear at least {frost:g} in below finished grade, or on a frost-protected "
        "base; the 12 in gravel layer drains but does not stop heave on its own.",
        "Air-entrained concrete (exposure F2) for every surface that stays wet through a freeze.",
        "Drain or winterize basins that cannot take ice; an ice sheet bears on the walls as it expands.",
        "Keep backfill against walls free-draining (gravel and a drain) so it does not freeze and push.",
        "No deicing salt on new concrete for its first winter; seal coping and decks against salt.",
    ]
    return CalcResult(calc="structural_freeze_thaw_notes", value={"frost_depth_in": frost, "notes": notes},
                      formula="notes", steps=notes, citations=[CIT_IBC_FROST, CIT_ACI318_FREEZE], warnings=[DISCLAIMER])


def seismic_design_values(ss=None, s1=None, site_class: str = "D", sds=None, sd1=None) -> CalcResult:
    """SDS, SD1 and the seismic design category (risk category II).

    Give either the mapped Ss and S1 (from the USGS / ASCE Hazard Tool for the
    address) with a site class, or SDS and SD1 straight from the tool. With no
    geotechnical report, ASCE 7 lets Site Class D be assumed. The Wasatch Front
    is normally Seismic Design Category D."""
    calc = "structural_seismic_design_values"
    site = str(site_class or "D").upper()
    steps = ["Get Ss and S1 (or SDS and SD1) for the project address from the USGS / ASCE Hazard Tool."]
    warnings = [DISCLAIMER]
    if sds not in (None, "") and sd1 not in (None, ""):
        sds_v, sd1_v = float(sds), float(sd1)
        fa = fv = None
        s1_v = float(s1) if s1 not in (None, "") else None
        steps.append(f"SDS = {sds_v:g}, SD1 = {sd1_v:g} given")
    elif ss in (None, "") or s1 in (None, ""):
        return CalcResult(calc=calc, value=None, formula="needs Ss and S1, or SDS and SD1", steps=steps,
                          citations=[CIT_USGS, CIT_ASCE7_SITE], warnings=[*warnings, "No seismic values for the site: look up Ss and S1 for the address."],
                          status="incomplete")
    else:
        ss_v, s1_v = float(ss), float(s1)
        if site not in FA:
            return CalcResult(calc=calc, value=None, steps=steps, citations=[CIT_ASCE7_SITE],
                              warnings=[*warnings, f"Site Class {site} needs a site-specific response analysis."],
                              status="incomplete")
        fa = _interp(ss_v, _SS_COLS, FA[site])
        fv = _interp(s1_v, _S1_COLS, FV[site])
        if fa is None or fv is None:
            return CalcResult(calc=calc, value=None, steps=steps, citations=[CIT_ASCE7_SITE],
                              warnings=[*warnings, f"Site Class {site} at these Ss/S1 needs a site-specific study " "(ASCE 7-16 §11.4.8)."], status="incomplete")
        sds_v, sd1_v = 2.0 / 3.0 * fa * ss_v, 2.0 / 3.0 * fv * s1_v
        steps += [f"Fa = {fa:.3f} (Ss {ss_v:g}, Site Class {site}); Fv = {fv:.3f} (S1 {s1_v:g})",
                  f"SDS = 2/3 × {fa:.3f} × {ss_v:g} = {sds_v:.3f}; SD1 = 2/3 × {fv:.3f} × {s1_v:g} = {sd1_v:.3f}"]
        if site == "D" and s1_v >= 0.2:
            warnings.append("Site Class D with S1 ≥ 0.2: ASCE 7-16 §11.4.8 asks for a site-specific analysis "
                            "unless an exception applies; the Hazard Tool's ASCE 7-22 SDS/SD1 can be entered instead.")

    def cat_sds(v):
        return "A" if v < 0.167 else "B" if v < 0.33 else "C" if v < 0.50 else "D"

    def cat_sd1(v):
        return "A" if v < 0.067 else "B" if v < 0.133 else "C" if v < 0.20 else "D"

    sdc = max(cat_sds(sds_v), cat_sd1(sd1_v))
    if s1_v is not None and s1_v >= 0.75:
        sdc = "E"
    steps.append(f"SDC by SDS: {cat_sds(sds_v)}, by SD1: {cat_sd1(sd1_v)} → {sdc}")
    return CalcResult(
        calc=calc,
        value={"site_class": site, "fa": fa, "fv": fv, "sds": sds_v, "sd1": sd1_v, "sdc": sdc},
        unit="g",
        inputs={"ss": make_input(ss, "g", "user", CIT_USGS), "s1": make_input(s1, "g", "user", CIT_USGS),
                "site_class": make_input(site, "", "user" if site_class else "default")},
        formula="SDS = 2/3·Fa·Ss; SD1 = 2/3·Fv·S1; SDC = worse of the SDS and SD1 tables",
        steps=steps,
        citations=[CIT_USGS, CIT_ASCE7_SITE, CIT_ASCE7_SDC],
        warnings=warnings,
    )


def hydrodynamic_wall_load(
    water_depth_ft: float, length_ft: float, sds: float, sd1: float, wall_in: float = 8.0,
    wall_height_ft: float | None = None, buried: bool = True, importance: float = 1.0, rules: dict | None = None,
) -> CalcResult:
    """Earthquake load per foot of the wall facing the shaking, ACI 350.3's
    simplified method for a rectangular basin. ``length_ft`` is the inside
    length in the direction of the shaking (run it both ways).

    Impulsive water moves with the walls, convective water sloshes; each gives
    a force at its height, the wall's own mass adds an impulsive force, and
    the base moment is √((Mi + Mw)² + Mc²). Compared with the static moment of
    water to the same depth."""
    rules = merged_rules(rules)
    hl, l_ft = float(water_depth_ft), float(length_ft)
    hw = float(wall_height_ft if wall_height_ft is not None else hl)
    calc = "structural_hydrodynamic_wall"
    if hl <= 0 or l_ft <= 0:
        return CalcResult(calc=calc, value=None, steps=["no water or no length"], citations=[CIT_ACI3503],
                          warnings=[DISCLAIMER], status="n/a")
    g = 32.2
    gw = float(rules["water_pcf"])
    r = l_ft / hl
    wi = math.tanh(0.866 * r) / (0.866 * r)
    wc = 0.264 * r * math.tanh(3.16 / r)
    hi = (0.5 - 0.09375 * r) * hl if r < 1.333 else 0.375 * hl
    x = 3.16 / r
    hc = (1 - (math.cosh(x) - 1) / (x * math.sinh(x))) * hl
    lam = math.sqrt(3.16 * g * math.tanh(x))
    tc = 2 * math.pi * math.sqrt(l_ft) / lam
    ts = sd1 / sds if sds else 0.0
    ci = float(sds)
    cc = min(1.5 * sd1 / tc, 1.5 * sds) if tc <= 1.6 / max(ts, 1e-9) else 2.4 * sds / tc ** 2
    ri, rc = (3.0 if buried else 2.0), 1.0
    wl_per_ft = gw * hl * l_ft  # liquid weight per foot of wall width
    pi = ci * importance * wi * wl_per_ft / (2 * ri)
    pc = cc * importance * wc * wl_per_ft / (2 * rc)
    eps = min(0.0151 * r * r - 0.1908 * r + 1.021, 1.0)
    ww = float(rules["concrete_pcf"]) * float(wall_in) / 12.0 * hw
    pw = ci * importance * eps * ww / ri
    mi, mc, mw = pi * hi, pc * hc, pw * hw / 2.0
    mb = math.sqrt((mi + mw) ** 2 + mc ** 2)
    vb = math.sqrt((pi + pw) ** 2 + pc ** 2)
    m_static = gw * hl ** 3 / 6.0
    return CalcResult(
        calc=calc,
        value={"length_ft": l_ft, "water_depth_ft": hl, "wi_ratio": wi, "wc_ratio": wc, "hi_ft": hi, "hc_ft": hc,
               "tc_s": tc, "ci": ci, "cc": cc, "ri": ri, "rc": rc, "pi_lb_per_ft": pi, "pc_lb_per_ft": pc,
               "pw_lb_per_ft": pw, "base_shear_lb_per_ft": vb, "base_moment_lb_ft_per_ft": mb,
               "static_moment_lb_ft_per_ft": m_static, "ratio_to_static": mb / m_static if m_static else None},
        unit="lb·ft/ft",
        inputs={"sds": make_input(sds, "g", "prior_calc"), "sd1": make_input(sd1, "g", "prior_calc"),
                "length": make_input(l_ft, "ft", "user"), "depth": make_input(hl, "ft", "user")},
        formula="Wi/WL = tanh(0.866L/HL)/(0.866L/HL); Wc/WL = 0.264(L/HL)tanh(3.16HL/L); "
                "P = C·I·W/(2R) per ft; Mb = √((Mi+Mw)² + Mc²)",
        steps=[f"L/HL = {r:.3f}; Wi/WL = {wi:.4f}, Wc/WL = {wc:.4f}; hi = {hi:.3f} ft, hc = {hc:.3f} ft",
               f"Tc = {tc:.3f} s; Ci = {ci:.3f}, Cc = {cc:.4f}; Ri = {ri:g}, Rc = {rc:g}",
               f"Pi = {pi:.1f}, Pc = {pc:.1f}, Pw = {pw:.1f} lb/ft",
               f"Mb = √(({mi:.1f} + {mw:.1f})² + {mc:.1f}²) = {mb:.1f} lb·ft/ft vs static {m_static:.1f}"],
        citations=[CIT_ACI3503, CIT_ASCE7_SITE],
        warnings=[DISCLAIMER, "Simplified ACI 350.3 rectangular-tank method; check freeboard for sloshing on "
                  "large basins."],
    )


def wind_panel_load(
    height_ft: float, width_ft: float, clearance_ft: float = 0.0, speed_mph: float | None = None,
    exposure: str = "C", solid_ratio: float = 1.0, elevation_ft: float = 0.0, rules: dict | None = None,
) -> CalcResult:
    """Wind on a freestanding water wall, screen or sign-like panel
    (ASCE 7-16 §29.3, Case A). ``height_ft`` is to the top of the panel,
    ``clearance_ft`` the open gap under it, ``solid_ratio`` the solid share
    of the panel's face (a perforated screen < 1)."""
    rules = merged_rules(rules)
    v = float(speed_mph if speed_mph not in (None, "") else rules["wind_speed_mph"])
    h = float(height_ft)
    s = h - float(clearance_ft or 0)
    b = float(width_ft)
    alpha, zg = EXPOSURE.get(str(exposure).upper(), EXPOSURE["C"])
    kz = 2.01 * (max(h, 15.0) / zg) ** (2.0 / alpha)
    ke = math.exp(-0.0000362 * float(elevation_ft or 0))
    kd, kzt, gust = 0.85, 1.0, 0.85
    qh = 0.00256 * kz * kzt * kd * ke * v * v
    if s / h >= 0.99:
        cf = _interp(b / s, _CF_BS, _CF_GROUND)
        cf_note = "row s/h = 1 (panel on the ground)"
    else:
        cf = CF_ELEVATED_BOUND
        cf_note = f"s/h = {s / h:.2f}: upper bound {CF_ELEVATED_BOUND:g} (read Figure 29.3-1 to refine)"
    eps = min(max(float(solid_ratio), 0.0), 1.0)
    red = 1 - (1 - eps) ** 1.5
    p = qh * gust * cf * red
    area = b * s * eps
    force = p * b * s
    return CalcResult(
        calc="structural_wind_panel",
        value={"qh_psf": qh, "kz": kz, "ke": ke, "cf": cf, "porosity_factor": red, "pressure_psf": p,
               "force_lb": force, "net_area_sqft": area, "resultant_height_ft": h - s / 2.0, "speed_mph": v},
        unit="psf",
        inputs={"speed": make_input(v, "mph", "user" if speed_mph else "default"),
                "exposure": make_input(exposure, "", "user"), "elevation": make_input(elevation_ft, "ft", "user")},
        formula="qh = 0.00256·Kz·Kzt·Kd·Ke·V²; p = qh·G·Cf·(1 − (1 − ε)^1.5); F = p·B·s at the panel's center",
        steps=[f"Kz = 2.01 × ({max(h, 15.0):g}/{zg:g})^(2/{alpha:g}) = {kz:.4f}; Ke = {ke:.4f}",
               f"qh = 0.00256 × {kz:.4f} × 1 × 0.85 × {ke:.4f} × {v:g}² = {qh:.2f} psf",
               f"Cf = {cf:.3f} ({cf_note}); porosity factor {red:.3f}",
               f"p = {qh:.2f} × 0.85 × {cf:.3f} × {red:.3f} = {p:.2f} psf; F = {force:.0f} lb at {h - s / 2:.2f} ft"],
        citations=[CIT_ASCE7_WIND, CIT_ASCE7_KZ, CIT_ASCE7_CF],
        warnings=[DISCLAIMER, "Basic wind speed: read V for the address and risk category from the ASCE Hazard "
                  "Tool. Case C (higher pressure near the windward edge) is not checked."],
    )
