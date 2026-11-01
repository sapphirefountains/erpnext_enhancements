"""Equipment and piping supports: pads and anchor bolts, thrust blocks,
seismic bracing, sleeves through walls, and moorings for floating fountains.

* Equipment pads: a housekeeping pad 6 in past the equipment on every side,
  anchors checked under the ASCE 7 Chapter 13 seismic force on the unit
  (ap 1.0, Rp 2.5 for pumps, at grade) by ACI 318 Chapter 17 in its simplest
  form: steel strength and concrete breakout of a single anchor (no edge or
  group reduction, so keep anchors 1.5 hef from pad edges), with the 0.75
  seismic factor on breakout and the 1.2 tension-shear interaction.
* Thrust blocks: T = 2·P·A·sin(θ/2) at a bend, P·A at a tee or dead end, on
  the pipe's outside area, bearing on undisturbed soil with a factor of
  safety. Solvent-welded PVC is a restrained joint and needs none; gasketed
  and mechanical joints do.
* Seismic bracing of piping: ASCE 7 §13.6 exemptions, then lateral and
  longitudinal brace spacing.
* Sleeves and penetrations: sleeve length, a hydrophilic waterstop ring round
  the pipe (DOC-0119 waterstop rule), Link-Seal per DOC-0119.
* Moorings: dead-weight anchors resisting wind and current drag on a
  floating fountain.

Phase B owns pipe hanger loads and span tables (``engine/structure.py``);
nothing here duplicates them.
"""

from __future__ import annotations

import math

from ..envelope import CalcResult, make_input
from .defaults import CIT_DOC0119_PENETRATIONS, CIT_DOC0119_WATERSTOP, DISCLAIMER, merged_rules

CIT_ASCE7_13 = "ASCE 7-16 §13.3.1 (Fp) and Table 13.6-1 (pumps: ap 1.0, Rp 2.5)"
CIT_ACI17 = ("ACI 318-19 Ch. 17: Nsa = Ase·futa (φ 0.75), Nb = kc·λ·√f'c·hef^1.5 (kc 24 cast-in, 17 post-installed; "
             "φ 0.70 / 0.65), seismic 0.75 on breakout (§17.10.5.4), Vsa = 0.6·Ase·futa (φ 0.65), interaction 1.2")
CIT_PAD = "Housekeeping pad practice: 6 in beyond the equipment, 4–6 in thick (house rule)"
CIT_THRUST = "Uni-Bell PVC Pipe Association / AWWA M23: T = 2·P·A·sin(θ/2); bearing on undisturbed soil"
CIT_BRACE = ("ASCE 7-16 §13.6.7.3 (piping exemptions) with NFPA 13 / SMACNA practice: lateral braces ≤ 40 ft, "
             "longitudinal ≤ 80 ft (steel); plastic pipe at half (house rule)")
CIT_DRAG = "Drag F = ½·ρ·Cd·A·v² (water ρ 1.94 slug/ft³, Cd 1.0); wind 0.00256·V²·Cd·A (Cd 1.2)"
CIT_ASME_OD = "ASME B36.10M / ASTM D1785 outside diameters by nominal pipe size (as tabulated in DOC-0119)"

ANCHOR_ASE_IN2 = {0.375: 0.0775, 0.5: 0.142, 0.625: 0.226, 0.75: 0.334, 0.875: 0.462, 1.0: 0.606}
ANCHOR_FUTA_PSI = {"F1554-36": 58000.0, "316SS": 75000.0}
PIPE_OD_IN = {0.5: 0.840, 0.75: 1.050, 1.0: 1.315, 1.25: 1.660, 1.5: 1.900, 2.0: 2.375, 2.5: 2.875, 3.0: 3.500,
              4.0: 4.500, 6.0: 6.625, 8.0: 8.625, 10.0: 10.75, 12.0: 12.75}
FITTING_ANGLE = {"90": 90.0, "elbow_90": 90.0, "45": 45.0, "elbow_45": 45.0, "22.5": 22.5, "11.25": 11.25}


def nominal_size(size) -> float:
    """``'3"'``, ``"3"``, ``3`` or ``"1-1/2"`` → 3.0 / 1.5."""
    text = str(size).replace('"', "").replace("in", "").strip()
    if "-" in text and "/" in text:
        whole, frac = text.split("-", 1)
        num, den = frac.split("/")
        return float(whole) + float(num) / float(den)
    if "/" in text:
        num, den = text.split("/")
        return float(num) / float(den)
    return float(text)


def equipment_pad(
    length_in: float, width_in: float, weight_lb: float, cg_height_in: float, sds: float | None = None,
    n_anchors: int = 4, anchor_dia_in: float = 0.5, embed_in: float = 4.0, anchor_type: str = "cast-in",
    anchor_grade: str = "316SS", ip: float = 1.0, rules: dict | None = None,
) -> CalcResult:
    """Pad size and concrete for one piece of equipment, and its anchors
    against the seismic overturning and shear."""
    rules = merged_rules(rules)
    ext = float(rules["pad_extension_in"])
    pad_t = float(rules["pad_thickness_in"])
    pl, pw = float(length_in) + 2 * ext, float(width_in) + 2 * ext
    concrete = pl * pw * pad_t / 1728.0
    w = float(weight_lb)
    sds_v = float(sds or 0)
    fp = 0.4 * 1.0 * sds_v * w / (2.5 / ip)
    fp = min(max(fp, 0.3 * sds_v * ip * w), 1.6 * sds_v * ip * w)
    s = max(min(float(length_in), float(width_in)) - 4.0, 4.0)  # anchor pattern, in
    n = max(int(n_anchors), 2)
    resist = (0.9 - 0.2 * sds_v) * w
    t_total = max((fp * float(cg_height_in) - resist * s / 2.0) / s, 0.0)
    tu = t_total / max(n / 2, 1)
    vu = fp / n
    dia = float(anchor_dia_in)
    listed = [d for d in sorted(ANCHOR_ASE_IN2) if d <= dia + 1e-9] or [min(ANCHOR_ASE_IN2)]
    ase = ANCHOR_ASE_IN2[listed[-1]]
    size_note = None if abs(listed[-1] - dia) < 1e-9 else \
        f"No {dia:g} in anchor in the table: checked as {listed[-1]:g} in (conservative)."
    futa = ANCHOR_FUTA_PSI.get(anchor_grade, 58000.0)
    hef = min(float(embed_in), pad_t - 2.0) if pad_t > 2.0 else float(embed_in)
    cast = str(anchor_type).lower().startswith("cast")
    kc, phi_c = (24.0, 0.70) if cast else (17.0, 0.65)
    fc = float(rules["fc_psi"])
    nb = kc * math.sqrt(fc) * hef ** 1.5
    phi_ncb = phi_c * nb * (0.75 if sds_v >= 0.167 else 1.0)
    phi_nsa = 0.75 * ase * futa
    phi_n = min(phi_ncb, phi_nsa)
    phi_v = 0.65 * 0.6 * ase * futa
    inter = tu / phi_n + vu / phi_v if phi_n and phi_v else 0.0
    ok = tu <= phi_n + 1e-9 and vu <= phi_v + 1e-9 and inter <= 1.2 + 1e-9
    warnings = [DISCLAIMER, "Set pumps on vibration isolation (pads or springs) and connect with flexible "
                "couplings so the pad does not carry pipe loads."]
    if size_note:
        warnings.append(size_note)
    if hef < float(embed_in):
        warnings.append(f"Embedment limited to {hef:g} in by the {pad_t:g} in pad: anchor through into the slab.")
    if not ok:
        warnings.append("Anchors overstressed: larger or deeper anchors, or more of them.")
    return CalcResult(
        calc="structural_equipment_pad",
        value={"pad_length_in": pl, "pad_width_in": pw, "pad_thickness_in": pad_t, "concrete_cuft": concrete,
               "fp_lb": fp, "tension_per_anchor_lb": tu, "shear_per_anchor_lb": vu, "phi_nn_lb": phi_n,
               "phi_vn_lb": phi_v, "interaction": inter, "anchors": n, "hef_in": hef, "ok": ok},
        unit="lb",
        inputs={"weight": make_input(w, "lb", "user"), "sds": make_input(sds, "g", "prior_calc"),
                "anchor": make_input(f"{anchor_dia_in:g} in {anchor_grade} {anchor_type}", "", "user")},
        formula="Fp = 0.4·ap·SDS·W/(Rp/Ip); T = (Fp·hcg − (0.9 − 0.2SDS)W·s/2)/s per row; Tu/φNn + Vu/φVn ≤ 1.2",
        steps=[f"pad {pl:g} × {pw:g} × {pad_t:g} in = {concrete:.3f} ft³",
               f"Fp = {fp:.0f} lb; T per anchor {tu:.0f} lb, V per anchor {vu:.0f} lb",
               f"φNsa {phi_nsa:.0f}, φNcb {phi_ncb:.0f} (hef {hef:g} in), φVsa {phi_v:.0f}; interaction {inter:.2f}"],
        citations=[CIT_ASCE7_13, CIT_ACI17, CIT_PAD],
        warnings=warnings,
        status="ok" if ok else "bad",
    )


def thrust_block(nominal: float, pressure_psi: float, fitting: str = "90", soil_bearing_psf: float | None = None,
                 rules: dict | None = None) -> CalcResult:
    """Thrust at a fitting and the bearing area and concrete of its block.
    ``pressure_psi`` should be the test pressure (normally 1.5 × working)."""
    rules = merged_rules(rules)
    size = nominal_size(nominal)
    od = PIPE_OD_IN.get(size)
    if od is None:
        return CalcResult(calc="structural_thrust_block", value=None, warnings=[DISCLAIMER,
                          f"No outside diameter for {nominal}"], status="incomplete")
    area = math.pi * od * od / 4.0
    key = str(fitting).lower()
    if key in ("tee", "dead_end", "cap", "valve", "plug"):
        factor, label = 1.0, key
    else:
        ang = FITTING_ANGLE.get(key, float(key) if key.replace(".", "", 1).isdigit() else None)
        if ang is None:
            return CalcResult(calc="structural_thrust_block", value=None, citations=[CIT_THRUST],
                              warnings=[DISCLAIMER, f"Fitting {fitting!r} is not a bend, tee or dead end: compute "
                                        "its thrust by hand (a reducer is P·(A1 − A2))."], status="incomplete")
        factor, label = 2 * math.sin(math.radians(ang) / 2), f"{ang:g}° bend"
    thrust = float(pressure_psi) * area * factor
    bearing = float(soil_bearing_psf if soil_bearing_psf not in (None, "") else rules["thrust_soil_bearing_psf"])
    fs = float(rules["thrust_fs"])
    block_area = thrust * fs / bearing
    depth_ft = max(1.0, (od + 12.0) / 12.0)
    concrete = block_area * depth_ft
    return CalcResult(
        calc="structural_thrust_block",
        value={"thrust_lb": thrust, "bearing_area_sqft": block_area, "face_ft": math.sqrt(block_area),
               "concrete_cuft": concrete, "od_in": od},
        unit="lb",
        inputs={"pressure": make_input(pressure_psi, "psi", "prior_calc"), "fitting": make_input(label, "", "user"),
                "soil_bearing": make_input(bearing, "psf", "user" if soil_bearing_psf else "default")},
        formula="T = P·(π·OD²/4)·2 sin(θ/2) (P·A at a tee or dead end); area = T·FS / soil bearing",
        steps=[f"A = π × {od:g}²/4 = {area:.3f} in²; T = {pressure_psi:g} × {area:.3f} × {factor:.4f} = {thrust:.0f} lb",
               f"bearing area = {thrust:.0f} × {fs:g} / {bearing:g} = {block_area:.2f} ft²; ≈ {concrete:.2f} ft³"],
        citations=[CIT_THRUST, CIT_ASME_OD],
        warnings=[DISCLAIMER, "Solvent-welded PVC joints are restrained and need no thrust block; gasketed or "
                  "mechanical joints buried in soil do."],
    )


def seismic_bracing(nominal: float, run_ft: float, sdc: str = "D", ip: float = 1.0, material: str = "pvc",
                    rules: dict | None = None) -> CalcResult:
    """Is a pipe run exempt from seismic bracing, and if not, how many
    lateral and longitudinal braces does it take."""
    rules = merged_rules(rules)
    size = nominal_size(nominal)
    cat = str(sdc or "D").upper()
    exempt = cat in ("A", "B") or (cat == "C" and float(ip) <= 1.0) or (float(ip) <= 1.0 and size <= 2.0)
    plastic = str(material).lower() in ("pvc", "cpvc", "pex", "hdpe", "abs")
    lat = float(rules["brace_lateral_ft"]) * (0.5 if plastic else 1.0)
    lon = float(rules["brace_longitudinal_ft"]) * (0.5 if plastic else 1.0)
    run = float(run_ft)
    n_lat = 0 if exempt else math.ceil(run / lat - 1e-9) + 1
    n_lon = 0 if exempt else max(math.ceil(run / lon - 1e-9), 1)
    return CalcResult(
        calc="structural_seismic_bracing",
        value={"exempt": exempt, "lateral_braces": n_lat, "longitudinal_braces": n_lon, "lateral_spacing_ft": lat,
               "longitudinal_spacing_ft": lon},
        unit="each",
        inputs={"size": make_input(size, "in", "user"), "sdc": make_input(cat, "", "prior_calc"),
                "run": make_input(run, "ft", "user")},
        formula="exempt: SDC A/B, C with Ip 1.0, or Ip 1.0 and NPS ≤ 2 in; else ceil(run/lat) + 1 and ceil(run/long)",
        steps=[("exempt" if exempt else f"{n_lat} lateral at ≤ {lat:g} ft, {n_lon} longitudinal at ≤ {lon:g} ft")],
        citations=[CIT_BRACE],
        warnings=[DISCLAIMER, "Brace at every change of direction and within 2 ft of each piece of equipment; "
                  "flexible connections where pipe crosses a seismic joint."],
    )


def penetration_detail(nominal, wall_in: float) -> CalcResult:
    """One pipe through a wall: sleeve length, the waterstop ring round the
    pipe, and the DOC-0119 seal."""
    size = nominal_size(nominal)
    od = PIPE_OD_IN.get(size, size + 0.4)
    ring_ft = math.pi * od / 12.0
    return CalcResult(
        calc="structural_penetration_detail",
        value={"nominal_in": size, "od_in": od, "sleeve_length_in": float(wall_in), "waterstop_ring_ft": ring_ft},
        unit="in",
        inputs={"size": make_input(size, "in", "user"), "wall": make_input(wall_in, "in", "prior_calc")},
        formula="sleeve = wall thickness; ring = π·OD",
        steps=[f"{size:g} in pipe (OD {od:g} in) through {wall_in:g} in: hydrophilic ring {ring_ft:.2f} ft"],
        citations=[CIT_DOC0119_PENETRATIONS, CIT_DOC0119_WATERSTOP, CIT_ASME_OD],
        warnings=[DISCLAIMER, "Core size and Link-Seal model from the Link-Seal sizing chart (DOC-0119): stainless "
                  "only, non-shrink grout on the outside face."],
    )


def mooring(above_water_sqft: float, submerged_sqft: float, wind_mph: float | None = None,
            current_fps: float = 0.5, n_anchors: int = 3, line_angle_deg: float = 45.0,
            rules: dict | None = None) -> CalcResult:
    """Dead-weight concrete anchors for a floating fountain. One upwind anchor
    is assumed to take the whole drag (conservative for 3–4 anchors); it must
    resist the horizontal pull by friction on the bottom and the vertical
    pull by its submerged weight."""
    rules = merged_rules(rules)
    v = float(wind_mph if wind_mph not in (None, "") else rules["mooring_wind_mph"])
    f_wind = 0.00256 * v * v * 1.2 * float(above_water_sqft)
    f_current = 0.5 * 1.94 * 1.0 * float(submerged_sqft) * float(current_fps) ** 2
    h = f_wind + f_current
    vert = h * math.tan(math.radians(float(line_angle_deg)))
    mu, fs = float(rules["mooring_friction"]), float(rules["mooring_fs"])
    w_sub = fs * (h / mu + vert) if mu > 0 else float("inf")
    gc, gw = float(rules["concrete_pcf"]), float(rules["water_pcf"])
    w_dry = w_sub * gc / (gc - gw)
    cuft = w_dry / gc
    return CalcResult(
        calc="structural_mooring",
        value={"wind_lb": f_wind, "current_lb": f_current, "horizontal_lb": h, "vertical_lb": vert,
               "anchor_submerged_lb": w_sub, "anchor_dry_lb": w_dry, "anchor_cuft": cuft, "anchors": int(n_anchors)},
        unit="lb",
        inputs={"wind": make_input(v, "mph", "user" if wind_mph else "default"),
                "current": make_input(current_fps, "ft/s", "user"), "angle": make_input(line_angle_deg, "°", "user")},
        formula="H = 0.00256V²·1.2·A_air + ½·1.94·1.0·A_water·v²; W_sub = FS·(H/μ + H·tan α); W_dry = W_sub·γc/(γc − γw)",
        steps=[f"wind {f_wind:.1f} lb + current {f_current:.1f} lb = {h:.1f} lb; vertical {vert:.1f} lb",
               f"submerged weight {w_sub:.0f} lb → dry {w_dry:.0f} lb ({cuft:.2f} ft³ of concrete) per anchor"],
        citations=[CIT_DRAG],
        warnings=[DISCLAIMER, "Use stainless or galvanized hardware and leave slack for the water level to move."],
    )
