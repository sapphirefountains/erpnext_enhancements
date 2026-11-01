"""Freestanding walls on their own footings: raised basin walls, weir walls
and walls that retain soil beside a plaza or a drive.

The wall is a cantilever stem on a spread footing. The footing's heel runs
under the retained side (water or soil), its toe out the other. Per foot of
wall, about the toe:

    overturning   FS = M_resisting / M_overturning      ≥ 1.5 (1.1 seismic)
    sliding       FS = (μ·ΣV + c·B) / ΣH                 ≥ 1.5 (1.1 seismic)
    bearing       e = B/2 − (MR − MO)/ΣV; q = ΣV/B·(1 + 6e/B), or
                  2ΣV / (3(B/2 − e)) when the resultant leaves the middle third
    frost         footing bottom below the frost line

Lateral loads: water γw·h²/2 at h/3; soil at an active equivalent fluid
pressure (IBC Table 1610.1) over the full height to the footing bottom;
surcharge as Ka·q (Ka = EFP / γsoil); under seismic, the Seed-Whitman soil
increment 0.375·kh·γs·H² at 0.6H or Westergaard's 7/12·kh·γw·h² at 0.4h for
water, kh = SDS / 2.5. Passive resistance in front of the toe is ignored
(conservative). The stem's own flexure reuses ``sizing.wall_bending_check``.

DOC-0119 has no freestanding-wall rule beyond its stem-wall detail; the
factors of safety are IBC §1807.2.3's.
"""

from __future__ import annotations

import math

from ..data.rebar import bar as rebar
from ..envelope import CalcResult, make_input
from .defaults import CIT_DOC0119_REBAR, DISCLAIMER, merged_rules
from .loads import CIT_IBC_1806, CIT_IBC_FROST, frost_check, soil_properties
from .sizing import detail_for_wall, wall_bending_check

CIT_IBC_1807 = "IBC 2021 §1807.2.3 (retaining walls: FS 1.5 against sliding and overturning; 1.1 with seismic)"
CIT_IBC_1610 = "IBC 2021 Table 1610.1 (active lateral soil load, e.g. 45 pcf for silty sand SM and silt ML)"
CIT_SEED_WHITMAN = "Seed & Whitman (1970) seismic earth-pressure increment 0.375·kh·γ·H² at 0.6H; kh = SDS/2.5"
CIT_WESTERGAARD = "Westergaard (1933) hydrodynamic pressure on a rigid wall, 7/12·kh·γw·h² at 0.4h"


def cantilever_wall(
    stem_height_ft: float,
    stem_in: float,
    footing_width_ft: float,
    toe_ft: float,
    footing_in: float = 12.0,
    retained: str = "water",
    water_depth_ft: float | None = None,
    surcharge_psf: float = 0.0,
    soil_class=None,
    bearing_psf=None,
    footing_bottom_in: float | None = None,
    frost_depth_in: float | None = None,
    sds: float | None = None,
    rules: dict | None = None,
) -> CalcResult:
    """Stability of one foot of cantilever wall: overturning, sliding,
    bearing and frost, static and (when ``sds`` is given) seismic.

    ``retained`` is "water" (a raised basin or weir wall, water to
    ``water_depth_ft`` on the heel side) or "soil" (backfill to the top of the
    stem, plus ``surcharge_psf``)."""
    rules = merged_rules(rules)
    h, t = float(stem_height_ft), float(stem_in) / 12.0
    b, toe, tf = float(footing_width_ft), float(toe_ft), float(footing_in) / 12.0
    heel = b - toe - t
    calc = "structural_cantilever_wall"
    if h <= 0 or b <= 0 or heel < 0:
        return CalcResult(calc=calc, value=None, steps=["footing narrower than toe + stem"], warnings=[DISCLAIMER],
                          status="incomplete")
    soil = soil_properties(soil_class, bearing_psf).value
    gc, gw, gs = float(rules["concrete_pcf"]), float(rules["water_pcf"]), float(rules["soil_pcf"])
    efp = float(rules["active_efp_pcf"])
    ka = efp / gs
    bottom = float(footing_bottom_in if footing_bottom_in is not None else footing_in)
    bury = max(bottom / 12.0 - tf, 0.0)  # stem below grade down to the footing's top
    stem_len = h + bury
    ht = stem_len + tf  # retained height to the footing bottom
    is_water = str(retained).lower() == "water"
    hw = min(float(water_depth_ft if water_depth_ft is not None else h), h) if is_water else 0.0

    horiz, vert = [], []  # (name, force lb/ft, arm ft about the toe)
    if is_water:
        horiz.append(("water", gw * hw * hw / 2.0, tf + bury + hw / 3.0))
        vert.append(("water over heel", gw * hw * heel, toe + t + heel / 2.0))
        if bury:
            vert.append(("soil over heel", gs * bury * heel, toe + t + heel / 2.0))
    else:
        horiz.append(("soil", efp * ht * ht / 2.0, ht / 3.0))
        vert.append(("soil over heel", gs * stem_len * heel, toe + t + heel / 2.0))
        if surcharge_psf:
            horiz.append(("surcharge", ka * float(surcharge_psf) * ht, ht / 2.0))
    vert += [("stem", gc * t * stem_len, toe + t / 2.0), ("footing", gc * b * tf, b / 2.0)]

    def stability(horiz_rows, fs_need, is_seismic=False):
        sum_h = sum(f for _, f, _ in horiz_rows)
        sum_v = sum(f for _, f, _ in vert)
        mo = sum(f * a for _, f, a in horiz_rows)
        mr = sum(f * a for _, f, a in vert)
        fs_ot = mr / mo if mo > 0 else None
        resist = soil["friction"] * sum_v + min(soil["cohesion_psf"] * b, 0.5 * sum_v)  # IBC 1806.3.2 cap
        fs_sl = resist / sum_h if sum_h > 0 else None
        e = b / 2.0 - (mr - mo) / sum_v
        if abs(e) <= b / 6.0:
            q = sum_v / b * (1 + 6 * abs(e) / b)
        elif b / 2.0 - abs(e) > 0:
            q = 2 * sum_v / (3 * (b / 2.0 - abs(e)))
        else:
            q = None  # the resultant falls outside the footing: it tips
        ok = q is not None and ((fs_ot is None or fs_ot + 1e-9 >= fs_need) and (fs_sl is None or fs_sl + 1e-9 >= fs_need)
              and q <= soil["bearing_psf"] * (1.333 if is_seismic else 1.0) + 1e-9)
        return {"sum_h_lb": sum_h, "sum_v_lb": sum_v, "mo_lb_ft": mo, "mr_lb_ft": mr, "fs_overturning": fs_ot,
                "fs_sliding": fs_sl, "eccentricity_ft": e, "bearing_psf": q, "required_fs": fs_need, "ok": ok}

    static = stability(horiz, float(rules["wall_fs"]))
    steps = [f"{n}: H = {f:.1f} lb/ft at {a:.3f} ft" for n, f, a in horiz]
    steps += [f"{n}: V = {f:.1f} lb/ft at {a:.3f} ft" for n, f, a in vert]
    steps.append(f"static: FS overturning {static['fs_overturning'] or 0:.2f}, sliding {static['fs_sliding'] or 0:.2f}, "
                 f"e = {static['eccentricity_ft']:.3f} ft, q = {static['bearing_psf'] or 0:.0f} psf "
                 f"(allowable {soil['bearing_psf']:g})")
    seismic = None
    citations = [CIT_IBC_1807, CIT_IBC_1806, CIT_IBC_1610 if not is_water else CIT_IBC_1807]
    if sds:
        kh = float(sds) / 2.5
        extra = (("seismic water", 7.0 / 12.0 * kh * gw * hw * hw, tf + bury + 0.4 * hw) if is_water else
                 ("seismic soil", 0.375 * kh * gs * ht * ht, 0.6 * ht))
        seismic = stability([*horiz, extra], float(rules["wall_fs_seismic"]), is_seismic=True)
        seismic["kh"] = kh
        steps.append(f"seismic (kh = {kh:.3f}): + {extra[1]:.1f} lb/ft; FS overturning "
                     f"{seismic['fs_overturning'] or 0:.2f}, sliding {seismic['fs_sliding'] or 0:.2f}, "
                     f"q = {seismic['bearing_psf'] or 0:.0f} psf (allowable × 1.33)")
        citations.append(CIT_WESTERGAARD if is_water else CIT_SEED_WHITMAN)
    frost = frost_check(bottom, frost_depth_in, rules)
    det = detail_for_wall(float(stem_in), rules)
    stem = wall_bending_check(hw if is_water else stem_len, float(stem_in), det["bar"], det["spacing_in"], det["mats"],
                              rules, unit_weight_pcf=None if is_water else efp, case="water" if is_water else "soil")
    ok = static["ok"] and (seismic is None or seismic["ok"]) and frost.status == "ok" and stem.status == "ok"
    warnings = [DISCLAIMER]
    if not static["ok"]:
        warnings.append("Wall fails a static stability check: widen the footing or lengthen the heel.")
    if seismic is not None and not seismic["ok"]:
        warnings.append("Wall fails a seismic stability check.")
    if frost.status != "ok":
        warnings.append(frost.warnings[-1])
    if stem.status != "ok":
        warnings.append("Stem needs more steel than its DOC-0119 detail.")
    concrete_cuft = t * stem_len + b * tf
    return CalcResult(
        calc=calc,
        value={"retained": "water" if is_water else "soil", "stem_height_ft": h, "stem_in": float(stem_in),
               "footing_width_ft": b, "toe_ft": toe, "heel_ft": heel, "footing_in": float(footing_in),
               "stem_below_grade_ft": bury,
               "static": static, "seismic": seismic, "frost": frost.value, "stem_check": stem.value,
               "stem_detail": det, "concrete_cuft_per_ft": concrete_cuft, "soil": soil, "ok": ok},
        unit="",
        inputs={"stem_height": make_input(h, "ft", "user"), "footing_width": make_input(b, "ft", "prior_calc"),
                "surcharge": make_input(surcharge_psf, "psf", "user"),
                "active_efp": make_input(efp, "pcf", "default", CIT_IBC_1610)},
        formula="FS_OT = MR/MO; FS_slide = (μΣV + cB)/ΣH; q = ΣV/B(1 + 6e/B)",
        steps=steps,
        citations=[*citations, CIT_IBC_FROST, CIT_DOC0119_REBAR],
        warnings=warnings,
        status="ok" if ok else "bad",
    )


def size_wall_footing(
    stem_height_ft: float, stem_in: float = 8.0, retained: str = "water", water_depth_ft=None,
    surcharge_psf: float = 0.0, soil_class=None, bearing_psf=None, frost_depth_in=None, sds=None,
    footing_in: float = 12.0, rules: dict | None = None,
) -> CalcResult:
    """The narrowest footing (in 6 in steps, toe a third of the width) that
    passes ``cantilever_wall``, with its bottom at the frost depth; and the
    concrete and rebar per foot of wall that go with it."""
    rules = merged_rules(rules)
    frost = float(frost_depth_in if frost_depth_in not in (None, "") else rules["frost_depth_in"])
    bottom = max(frost, float(footing_in))
    h = float(stem_height_ft)
    width = max(2.0, round(0.4 * h * 2) / 2)
    tried = []
    result = None
    while width <= max(4.0 * h, 6.0) + 1e-9:
        toe = math.floor(width / 3.0 * 4) / 4
        result = cantilever_wall(h, stem_in, width, toe, footing_in, retained, water_depth_ft, surcharge_psf,
                                 soil_class, bearing_psf, bottom, frost, sds, rules)
        tried.append(f"B = {width:g} ft, toe {toe:g} ft: {'passes' if result.status == 'ok' else 'fails'}")
        if result.status == "ok":
            break
        width += 0.5
    if result is None or result.value is None:
        return CalcResult(calc="structural_wall_footing", value=None, citations=[CIT_IBC_1807],
                          warnings=[DISCLAIMER, "Wall height and stem thickness must be > 0."], status="incomplete")
    v = result.value
    c = float(rules["cover_in"]) / 12.0
    det = v["stem_detail"]
    bar_v = rebar(det["bar"])
    per_ft = 12.0 / det["spacing_in"] * det["mats"]
    vert_len = (bottom / 12.0 - c) + h - c + float(rules["dowel_leg_in"]) / 12.0
    rows = math.ceil((h - c) / (det["spacing_in"] / 12.0)) + 1
    foot_bars = 12.0 / float(rules["slab_spacing_in"])  # transverse footing bars per foot of wall
    foot_long = math.ceil((width - 2 * c) / (float(rules["slab_spacing_in"]) / 12.0)) + 1
    bar_f = rebar(rules["slab_bar"])
    by_bar: dict[str, float] = {}
    by_bar[bar_v["label"]] = (per_ft * vert_len + rows * det["mats"] * 1.0) * bar_v["lb_per_ft"]
    by_bar[bar_f["label"]] = by_bar.get(bar_f["label"], 0.0) + \
        (foot_bars * (width - 2 * c) + foot_long * 1.0) * bar_f["lb_per_ft"]
    lb_per_ft = sum(by_bar.values())
    excavation_cuft = (width + 2 * float(rules["work_space_ft"])) * bottom / 12.0
    concrete = float(stem_in) / 12.0 * (h + bottom / 12.0 - float(footing_in) / 12.0) + width * float(footing_in) / 12.0
    return CalcResult(
        calc="structural_wall_footing",
        value={**v, "footing_bottom_in": bottom, "concrete_cuft_per_ft": concrete, "rebar_lb_per_ft": lb_per_ft,
               "rebar_lb_per_ft_by_bar": by_bar,
               "excavation_cuft_per_ft": excavation_cuft, "tried": tried},
        unit="ft",
        inputs=result.inputs,
        formula="smallest B (0.5 ft steps) passing overturning, sliding, bearing; bottom at frost depth",
        steps=tried + result.steps + [f"concrete {concrete:.3f} ft³/ft, rebar {lb_per_ft:.2f} lb/ft"],
        citations=result.citations,
        warnings=result.warnings if result.status != "ok" else [DISCLAIMER],
        status=result.status,
    )
