"""Slab and wall thickness from Sapphire's standard details, and the early
structural checks that go with them.

DOC-0119 ("Big & Beefy") governs the details: 8 in floors, stem walls with
U-bars tied into the floor, #4 bars, 2 in cover, and its wall table (8 in wall
#4 at 16 in, 10 in wall #4 at 12 in). Where DOC-0119 is silent this module
uses an ACI 318 / ACI 350 minimum and says so in the citation.

The checks are engineering aids, each in the simplest honest form:

* hydrostatic wall: fresh water to the rim, p = 62.4·h (triangular), wall a
  cantilever off the slab: V = γh²/2, M = γh³/6 per foot of wall; and the
  empty in-grade case with soil pushing in at an equivalent fluid pressure;
* flexure by the ACI rectangular stress block with Mu = load factor × ACI 350
  durability factor × M, and one-way shear against φ·2√f'c·b·d;
* shrinkage and temperature steel against ACI 350's joint-spacing table;
* uplift of the empty shell against a groundwater table, with a factor of
  safety; bearing on soil; and the floor load of a basin standing on a slab or
  a roof.

Every result carries the disclaimer: none of this is a stamped design.
"""

from __future__ import annotations

import math

from ..data.rebar import CIT_ASTM_A615
from ..data.rebar import bar as rebar
from ..envelope import CalcResult, make_input
from .defaults import (
    CIT_ACI318_COVER,
    CIT_ACI318_FLEXURE,
    CIT_ACI318_LOADS,
    CIT_ACI318_SHEAR,
    CIT_ACI318_TWO_LAYERS,
    CIT_ACI350_SD,
    CIT_ACI350_ST,
    CIT_ACI350_WALL,
    CIT_DOC0119_ANALYSIS,
    CIT_DOC0119_CONCRETE,
    CIT_DOC0119_REBAR,
    CIT_HYDROSTATIC,
    CIT_IBC_LATERAL,
    CIT_IBC_SOIL,
    CIT_USACE_FLOTATION,
    DISCLAIMER,
    merged_rules,
)
from .geometry import rim_above_grade_ft

PHI_FLEXURE = 0.90
PHI_SHEAR = 0.75


def _sv(shell):
    """Accept a ``basin_shell`` CalcResult or its ``value`` dict."""
    return shell.value if isinstance(shell, CalcResult) else shell


def detail_for_wall(wall_in: float, rules: dict | None = None) -> dict:
    """The DOC-0119 bar size and spacing for a wall of ``wall_in``: the table
    row with the largest thickness not above it; thinner walls get the
    general #4 at 18 in. Two mats above 10 in (ACI 318 §11.7.2.3)."""
    rules = merged_rules(rules)
    rows = sorted(rules["wall_details"], key=lambda r: float(r["wall_in"]))
    chosen = None
    for row in rows:
        if float(wall_in) + 1e-9 >= float(row["wall_in"]):
            chosen = row
    if chosen is None:
        out = {"bar": int(rules["general_bar"]), "spacing_in": float(rules["general_spacing_in"]),
               "source": CIT_DOC0119_REBAR + " (#4 on 18 in centers, the general recommendation)"}
    else:
        out = {"bar": int(chosen["bar"]), "spacing_in": float(chosen["spacing_in"]),
               "source": f"{CIT_DOC0119_REBAR} (wall table row {float(chosen['wall_in']):g} in)"}
    out["mats"] = 2 if float(wall_in) > float(rules["two_mats_over_in"]) + 1e-9 else 1
    return out


def effective_depth_in(thickness_in: float, bar_size, mats: int, cover_in: float) -> float:
    """d for one-way bending: a single centered mat sits at t/2; with two mats
    the tension mat is cover + half a bar from the far face."""
    db = rebar(bar_size)["diameter_in"]
    if int(mats) >= 2:
        return float(thickness_in) - float(cover_in) - db / 2.0
    return float(thickness_in) / 2.0


def hydrostatic_pressure(height_ft: float, unit_weight_pcf: float = 62.4) -> CalcResult:
    """Triangular lateral pressure on a wall retaining ``height_ft`` of fluid
    (or soil, as an equivalent fluid): base pressure, resultant and the
    cantilever moment at the base, all per foot of wall."""
    h = max(float(height_ft or 0), 0.0)
    g = float(unit_weight_pcf)
    p = g * h
    v = g * h * h / 2.0
    m = g * h ** 3 / 6.0
    return CalcResult(
        calc="structural_hydrostatic_pressure",
        value={"base_pressure_psf": p, "resultant_lb_per_ft": v, "base_moment_lb_ft_per_ft": m,
               "resultant_height_ft": h / 3.0},
        unit="psf",
        inputs={"height": make_input(h, "ft", "prior_calc"), "unit_weight": make_input(g, "pcf", "default")},
        formula="p = γh ; V = γh²/2 at h/3 ; M = γh³/6",
        steps=[f"p = {g:g} × {h:.4f} = {p:.2f} psf", f"V = {g:g} × {h:.4f}² / 2 = {v:.2f} lb/ft",
               f"M = {g:g} × {h:.4f}³ / 6 = {m:.2f} lb·ft/ft"],
        citations=[CIT_HYDROSTATIC],
        warnings=[DISCLAIMER],
    )


def wall_bending_check(
    height_ft: float,
    wall_in: float,
    bar_size,
    spacing_in: float,
    mats: int,
    rules: dict | None = None,
    unit_weight_pcf: float | None = None,
    case: str = "water",
    top: str = "free",
    groundwater_ft: float = 0.0,
) -> CalcResult:
    """Wall fixed at the slab under triangular pressure: the bar area flexure
    needs against the bar area the detail gives, and one-way shear at the base.

    ``case`` is "water" (full to the rim, tension on the water face) or "soil"
    (empty basin, backfill pushing in at ``soil_efp_pcf``). ``top`` is "free"
    (a cantilever: M = γh³/6, V = γh²/2) or "pinned" (a vault wall propped by
    its lid: M = γh³/15, V = 2γh²/5 at the base). ``groundwater_ft`` is how
    far the water table stands above the wall's base in the soil case; that
    much water pressure (62.4 pcf, triangular from the water table down) is
    added to the soil's, which keeps its full unit weight (conservative)."""
    rules = merged_rules(rules)
    g = float(unit_weight_pcf if unit_weight_pcf is not None else
              (rules["water_pcf"] if case == "water" else rules["soil_efp_pcf"]))
    h = max(float(height_ft or 0), 0.0)
    fc, fy = float(rules["fc_psi"]), float(rules["fy_psi"])
    b = 12.0
    bar = rebar(bar_size)
    d = effective_depth_in(wall_in, bar_size, mats, rules["cover_in"])
    pinned = str(top).lower() == "pinned"
    hw = min(max(float(groundwater_ft or 0), 0.0), h) if case == "soil" else 0.0
    g_w = float(rules["water_pcf"])
    m_base = (g * h ** 3 + g_w * hw ** 3) / (15.0 if pinned else 6.0)
    v_base = (g * h * h + g_w * hw * hw) * (2.0 / 5.0 if pinned else 0.5)
    factor = float(rules["load_factor"]) * float(rules["durability_factor"])
    mu = factor * m_base * 12.0  # lb·in per ft
    vu = float(rules["load_factor"]) * v_base
    steps = [f"d = {d:.3f} in ({'two mats' if mats >= 2 else 'one centered mat'})",
             f"M = (γh³ + γw·hw³)/{15 if pinned else 6} with hw = {hw:.2f} ft of groundwater = {m_base:.2f} "
             f"lb·ft/ft ({'propped by the lid' if pinned else 'cantilever'})",
             f"Mu = {rules['load_factor']:g} × {rules['durability_factor']:g} × {m_base:.2f}"
             f" × 12 = {mu:.1f} lb·in/ft"]
    warnings = [DISCLAIMER]
    as_req = 0.0
    section_ok = True
    if mu > 0 and d > 0:
        rn = mu / (PHI_FLEXURE * b * d * d)
        inner = 1.0 - 2.0 * rn / (0.85 * fc)
        if inner < 0:
            section_ok = False
            as_req = None  # no bar area can carry it; JSON has no infinity
            warnings.append("Section too thin for the moment: no bar area can carry it.")
        else:
            rho = 0.85 * fc / fy * (1.0 - math.sqrt(inner))
            as_req = rho * b * d
        steps.append(f"Rn = {rn:.2f} psi; As,req = " + ("none works (section too thin)" if as_req is None
                                                           else f"{as_req:.4f} in²/ft"))
    as_prov = bar["area_in2"] * 12.0 / float(spacing_in)
    phi_vc = PHI_SHEAR * 2.0 * math.sqrt(fc) * b * d
    steps += [f"As,prov = {bar['area_in2']:g} × 12 / {spacing_in:g} = {as_prov:.4f} in²/ft (tension mat)",
              f"Vu = {rules['load_factor']:g} × {v_base:.2f} = {vu:.1f} lb/ft; "
              f"φVc = 0.75 × 2√{fc:g} × 12 × {d:.3f} = {phi_vc:.0f} lb/ft"]
    flex_ok = section_ok and as_prov + 1e-12 >= as_req
    shear_ok = vu <= phi_vc
    if not flex_ok and section_ok:
        warnings.append(f"Flexure: needs {as_req:.3f} in²/ft, the detail gives {as_prov:.3f}.")
    if not shear_ok:
        warnings.append(f"Shear: Vu {vu:.0f} lb/ft exceeds φVc {phi_vc:.0f} lb/ft.")
    return CalcResult(
        calc="structural_wall_bending",
        value={"case": case, "top": "pinned" if pinned else "free", "height_ft": h, "groundwater_ft": hw, "unit_weight_pcf": g, "d_in": d, "mu_lb_in_per_ft": mu,
               "as_required_in2_per_ft": as_req, "as_provided_in2_per_ft": as_prov, "vu_lb_per_ft": vu,
               "phi_vc_lb_per_ft": phi_vc, "flexure_ok": flex_ok, "shear_ok": shear_ok,
               "utilization": (as_req / as_prov) if as_prov and as_req is not None else None},
        unit="in²/ft",
        inputs={"height": make_input(h, "ft", "prior_calc"), "wall": make_input(wall_in, "in", "prior_calc"),
                "bar": make_input(bar["label"], "", "standard", CIT_DOC0119_REBAR),
                "spacing": make_input(spacing_in, "in", "standard", CIT_DOC0119_REBAR),
                "fc": make_input(fc, "psi", "default"), "fy": make_input(fy, "psi", "default")},
        formula="Mu = LF·Sd·M (γh³/6 cantilever, γh³/15 propped); ρ = 0.85f'c/fy·(1 − √(1 − 2Rn/0.85f'c)); Vu ≤ φ·2√f'c·b·d",
        steps=steps,
        citations=[CIT_HYDROSTATIC, CIT_ACI318_FLEXURE, CIT_ACI318_SHEAR, CIT_ACI318_LOADS, CIT_ACI350_SD,
                   CIT_ASTM_A615] + ([CIT_IBC_LATERAL] if case == "soil" else []),
        warnings=warnings,
        status="ok" if flex_ok and shear_ok else "bad",
    )


def cover_check(thickness_in: float, bar_size, mats: int, rules: dict | None = None) -> CalcResult:
    """Does the section hold the bars with DOC-0119's cover on both faces?
    One mat needs t ≥ 2c + 2db (two crossing bars); two mats need room for
    four bar layers plus one more bar diameter between the mats."""
    rules = merged_rules(rules)
    db = rebar(bar_size)["diameter_in"]
    c = float(rules["cover_in"])
    need = 2 * c + (2 * db if int(mats) < 2 else 5 * db)
    ok = float(thickness_in) + 1e-9 >= need
    return CalcResult(
        calc="structural_cover_check",
        value={"thickness_in": float(thickness_in), "required_in": need, "ok": ok},
        unit="in",
        inputs={"cover": make_input(c, "in", "standard", CIT_DOC0119_CONCRETE)},
        formula="t ≥ 2c + 2db (one mat) or 2c + 5db (two mats)",
        steps=[f"need {need:.3f} in, have {float(thickness_in):g} in"],
        citations=[CIT_DOC0119_CONCRETE, CIT_ACI318_COVER],
        warnings=[DISCLAIMER] + ([] if ok else [
            f"A {float(thickness_in):g} in section cannot keep {c:g} in cover on both faces around "
            f"#{rebar(bar_size)['size']} bars."]),
        status="ok" if ok else "bad",
    )


def st_ratio_for_joint_spacing(joint_spacing_ft: float) -> float:
    """ACI 350-06 Table 7.12.2.1, Grade 60: minimum shrinkage and temperature
    ratio by the distance between movement joints."""
    s = float(joint_spacing_ft)
    if s < 20:
        return 0.003
    if s < 30:
        return 0.004
    return 0.005


def shrinkage_temperature_check(
    thickness_in: float, bar_size, spacing_in: float, mats: int, joint_spacing_ft: float,
    rules: dict | None = None, element: str = "wall",
) -> CalcResult:
    """The detail's steel per foot against ACI 350's shrinkage and temperature
    minimum (on the gross section, each direction) and its 12 in maximum
    spacing. DOC-0119 governs the detail; this shows where it sits."""
    rules = merged_rules(rules)
    bar = rebar(bar_size)
    rho = st_ratio_for_joint_spacing(joint_spacing_ft)
    req = rho * 12.0 * float(thickness_in)
    prov = bar["area_in2"] * 12.0 / float(spacing_in) * int(mats)
    ok = prov + 1e-12 >= req and float(spacing_in) <= 12.0 + 1e-9
    return CalcResult(
        calc="structural_shrinkage_temperature",
        value={"element": element, "ratio": rho, "required_in2_per_ft": req, "provided_in2_per_ft": prov,
               "spacing_in": float(spacing_in), "ok": ok, "joint_spacing_ft": float(joint_spacing_ft)},
        unit="in²/ft",
        inputs={"joint_spacing": make_input(joint_spacing_ft, "ft", "prior_calc")},
        formula="As,min = ρ(joint spacing) × 12 × t ; spacing ≤ 12 in",
        steps=[f"ρ = {rho:g} for joints at {float(joint_spacing_ft):.1f} ft; As,min = {req:.4f} in²/ft",
               f"provided = {bar['area_in2']:g} × 12 / {float(spacing_in):g} × {int(mats)} mat(s) = {prov:.4f} in²/ft"],
        citations=[CIT_ACI350_ST, CIT_DOC0119_REBAR],
        warnings=[DISCLAIMER],
        status="ok" if ok else "warn",
    )


def lateral_soil_heights(wall_height_ft: float, rim_above_grade_ft, groundwater_depth_ft=None) -> tuple:
    """(backfill height on the wall, water-table height above the wall's
    base) for a wall whose rim stands ``rim_above_grade_ft`` above grade
    (``None`` for a basin on a structure: no soil)."""
    h = float(wall_height_ft)
    if rim_above_grade_ft is None:
        return 0.0, 0.0
    soil_h = min(max(h - float(rim_above_grade_ft), 0.0), h)
    if groundwater_depth_ft is None:
        return soil_h, 0.0
    return soil_h, min(max(h - float(rim_above_grade_ft) - float(groundwater_depth_ft), 0.0), soil_h)


def select_thickness(basin_spec: dict, rules: dict | None = None) -> CalcResult:
    """Slab and wall thickness and their bar details for one basin.

    Explicit ``slab_in`` / ``wall_in`` on the basin win. Otherwise the slab is
    DOC-0119's 8 in, and the wall is the thinnest DOC-0119 table detail, not
    below the house default (8 in) or ACI 350's 12 in for walls 10 ft and
    taller, whose hydrostatic and soil checks pass."""
    rules = merged_rules(rules)
    spec = basin_spec
    h = float(spec.get("depth_in") or 0) / 12.0
    warnings = [DISCLAIMER]
    citations = [CIT_DOC0119_CONCRETE, CIT_DOC0119_REBAR]
    steps = []
    slab_in = float(spec.get("slab_in") or rules["slab_in"])
    slab_src = "user" if spec.get("slab_in") else "standard"
    min_wall = float(rules["wall_in"])
    if h + 1e-9 >= float(rules["aci350_tall_wall_ft"]):
        min_wall = max(min_wall, float(rules["aci350_tall_wall_min_in"]))
        citations.append(CIT_ACI350_WALL)
        steps.append(f"wall height {h:.2f} ft ≥ {rules['aci350_tall_wall_ft']:g} ft: ACI 350 minimum "
                     f"{rules['aci350_tall_wall_min_in']:g} in (DOC-0119 is silent on height)")
    lid = spec.get("lid") or {}
    lid_in = (float(lid.get("thickness_in") or slab_in)) if lid.get("type") == "concrete" else 0.0
    rim = rim_above_grade_ft(spec, h, lid_in)
    soil_h, gw_h = lateral_soil_heights(h, rim, spec.get("groundwater_depth_ft"))
    water_h = 0.0 if spec.get("kind") == "vault" else h
    top = "pinned" if (spec.get("lid") or {}).get("type") == "concrete" else "free"
    beyond = False
    if spec.get("wall_in"):
        wall_in = float(spec["wall_in"])
        wall_src = "user"
    else:
        wall_src = "standard"
        candidates = sorted({float(r["wall_in"]) for r in rules["wall_details"]} | {min_wall})
        candidates = [t for t in candidates if t + 1e-9 >= min_wall]
        wall_in = candidates[-1]
        for t in candidates:
            det = detail_for_wall(t, rules)
            water = wall_bending_check(water_h, t, det["bar"], det["spacing_in"], det["mats"], rules, case="water")
            soil = wall_bending_check(soil_h, t, det["bar"], det["spacing_in"], det["mats"], rules, case="soil",
                                      top=top, groundwater_ft=gw_h)
            fits = cover_check(t, det["bar"], det["mats"], rules).status == "ok"
            if water.status == "ok" and soil.status == "ok" and fits:
                wall_in = t
                break
        else:
            beyond = True
        steps.append(f"wall = {wall_in:g} in (thinnest standard detail ≥ {min_wall:g} in that passes)")
    det = detail_for_wall(wall_in, rules)
    max_table = max(float(r["wall_in"]) for r in rules["wall_details"])
    if beyond or wall_in > max_table + 1e-9:
        beyond = True
        warnings.append(
            f"A {wall_in:g} in wall {h:.1f} ft tall is beyond DOC-0119's standard details; "
            "DOC-0119 says to get a structural analysis for this.")
        citations.append(CIT_DOC0119_ANALYSIS)
    if det["mats"] >= 2:
        citations.append(CIT_ACI318_TWO_LAYERS)
    slab_det = {"bar": int(rules["slab_bar"]), "spacing_in": float(rules["slab_spacing_in"]),
                "mats": 2 if slab_in > float(rules["two_mats_over_in"]) + 1e-9 else 1}
    steps.append(f"slab = {slab_in:g} in, #{slab_det['bar']} at {slab_det['spacing_in']:g} in each way "
                 f"({slab_det['mats']} mat)")
    steps.append(f"wall #{det['bar']} at {det['spacing_in']:g} in each way, {det['mats']} mat(s)")
    return CalcResult(
        calc="structural_select_thickness",
        value={"slab_in": slab_in, "wall_in": wall_in, "wall_bar": det["bar"], "wall_spacing_in": det["spacing_in"],
               "wall_mats": det["mats"], "wall_detail_source": det["source"], "slab_bar": slab_det["bar"],
               "slab_spacing_in": slab_det["spacing_in"], "slab_mats": slab_det["mats"],
               "beyond_standard_details": beyond, "soil_height_ft": soil_h},
        unit="in",
        inputs={"wall_height": make_input(h, "ft", "user"),
                "slab_in": make_input(slab_in, "in", slab_src, CIT_DOC0119_CONCRETE),
                "wall_in": make_input(wall_in, "in", wall_src, CIT_DOC0119_REBAR)},
        formula="slab: DOC-0119 8 in; wall: thinnest DOC-0119 detail ≥ max(house default, ACI 350 tall-wall "
                "minimum) passing the hydrostatic and soil checks",
        steps=steps,
        citations=citations,
        warnings=warnings,
        status="warn" if beyond else "ok",
    )


def uplift_check(shell, rules: dict | None = None, groundwater_depth_ft=None) -> CalcResult:
    """Buoyancy of the empty shell against its dead weight.

    ``groundwater_depth_ft`` is the water table's depth below finished grade.
    Uplift acts on the underside of the slab: U = 62.4 × footprint × (depth of
    slab bottom − depth of water table). FS = concrete weight / U against
    ``uplift_fs``. Soil friction and backfill over a toe are ignored
    (conservative)."""
    rules = merged_rules(rules)
    s = _sv(shell)
    gw = groundwater_depth_ft if groundwater_depth_ft is not None else s.get("groundwater_depth_ft")
    calc = "structural_uplift"
    if gw is None or s.get("rim_above_grade_ft") is None:
        return CalcResult(calc=calc, value={"applies": False}, formula="needs a groundwater depth",
                          steps=["no groundwater level given (or the basin stands on a structure): not checked"],
                          citations=[CIT_USACE_FLOTATION], warnings=[DISCLAIMER], status="n/a")
    gw = float(gw)
    head = max(s["slab_bottom_depth_ft"] - gw, 0.0)
    disp = s["footprint_sqft"] * head
    u = rules["water_pcf"] * disp
    w = s["concrete_weight_lb"]
    fs = (w / u) if u > 0 else None
    need = float(rules["uplift_fs"])
    status = "ok" if fs is None or fs + 1e-12 >= need else ("bad" if fs < 1.0 else "warn")
    warnings = [DISCLAIMER]
    if status != "ok":
        warnings.append(f"Empty basin floats: FS {fs:.2f} < {need:g}.")
    return CalcResult(
        calc=calc,
        value={"applies": True, "groundwater_depth_ft": gw, "head_ft": head, "displaced_cuft": disp,
               "uplift_lb": u, "dead_weight_lb": w, "factor_of_safety": fs, "required_fs": need},
        unit="",
        inputs={"groundwater_depth": make_input(gw, "ft", "user"),
                "concrete_unit_weight": make_input(rules["concrete_pcf"], "pcf", "default")},
        formula="U = γw × footprint × (slab bottom depth − water table depth); FS = W_concrete / U",
        steps=[f"head on slab = {s['slab_bottom_depth_ft']:.4f} − {gw:g} = {head:.4f} ft",
               f"U = {rules['water_pcf']:g} × {s['footprint_sqft']:.4f} × {head:.4f} = {u:.1f} lb",
               f"W = {s['concrete_total_cuft']:.4f} ft³ × {rules['concrete_pcf']:g} = {w:.1f} lb",
               "FS = " + (f"{w:.1f} / {u:.1f} = {fs:.3f}" if fs else "no uplift (water table below the slab)")],
        citations=[CIT_USACE_FLOTATION, CIT_HYDROSTATIC],
        warnings=warnings,
        status=status,
    )


def bearing_check(shell, rules: dict | None = None, soil_bearing_psf=None) -> CalcResult:
    """Full basin (concrete + water to the rim) over its footprint against an
    allowable soil bearing pressure (IBC presumptive 1,500 psf by default)."""
    rules = merged_rules(rules)
    s = _sv(shell)
    calc = "structural_bearing"
    if s.get("rim_above_grade_ft") is None:
        return CalcResult(calc=calc, value={"applies": False}, steps=["basin stands on a structure: see floor load"],
                          citations=[CIT_IBC_SOIL], warnings=[DISCLAIMER], status="n/a")
    allow = float(soil_bearing_psf if soil_bearing_psf is not None else
                  (s.get("soil_bearing_psf") or rules["soil_bearing_psf"]))
    water = s["full_water_cuft"] * rules["water_pcf"]
    total = s["concrete_weight_lb"] + water
    q = total / s["footprint_sqft"]
    ok = q <= allow + 1e-9
    return CalcResult(
        calc=calc,
        value={"applies": True, "pressure_psf": q, "allowable_psf": allow, "total_lb": total, "water_lb": water,
               "ok": ok},
        unit="psf",
        inputs={"allowable": make_input(allow, "psf", "user" if soil_bearing_psf or s.get("soil_bearing_psf")
                                        else "standard", CIT_IBC_SOIL)},
        formula="q = (W_concrete + W_water to rim) / footprint ≤ allowable",
        steps=[f"q = ({s['concrete_weight_lb']:.1f} + {water:.1f}) / {s['footprint_sqft']:.4f} = {q:.1f} psf"],
        citations=[CIT_IBC_SOIL],
        warnings=[DISCLAIMER] + ([] if ok else [f"Bearing {q:.0f} psf exceeds {allow:.0f} psf."]),
        status="ok" if ok else "warn",
    )


def floor_load(shell, rules: dict | None = None, floor_capacity_psf=None) -> CalcResult:
    """Uniform load a full basin puts on the slab or roof it stands on, the
    rigging note's idea (weight over footprint) applied to concrete + water."""
    rules = merged_rules(rules)
    s = _sv(shell)
    water = s["full_water_cuft"] * rules["water_pcf"]
    total = s["concrete_weight_lb"] + water
    q = total / s["footprint_sqft"] if s["footprint_sqft"] else 0.0
    cap = floor_capacity_psf if floor_capacity_psf is not None else s.get("floor_capacity_psf")
    status = "n/a" if cap in (None, "") else ("ok" if q <= float(cap) + 1e-9 else "bad")
    warnings = [DISCLAIMER]
    if status == "bad":
        warnings.append(f"Floor load {q:.0f} psf exceeds the floor's {float(cap):.0f} psf.")
    if s.get("placement") == "on_structure" and cap in (None, ""):
        warnings.append("Basin on a structure with no floor capacity given: ask the building's engineer.")
    return CalcResult(
        calc="structural_floor_load",
        value={"pressure_psf": q, "total_lb": total, "water_lb": water, "concrete_lb": s["concrete_weight_lb"],
               "capacity_psf": None if cap in (None, "") else float(cap)},
        unit="psf",
        inputs={"capacity": make_input(cap, "psf", "user")},
        formula="q = (W_concrete + W_water to rim) / footprint",
        steps=[f"q = ({s['concrete_weight_lb']:.1f} + {water:.1f}) / {s['footprint_sqft']:.4f} = {q:.1f} psf"],
        citations=["Weight over footprint (Digital Rain Curtain rigging.js floor-load note)", CIT_HYDROSTATIC],
        warnings=warnings,
        status=status,
    )
