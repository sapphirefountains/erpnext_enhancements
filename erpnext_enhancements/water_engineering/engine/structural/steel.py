"""Steel members: tube frames for water walls and features, beams over
collector tanks, and columns and towers for tall features.

HSS section properties are computed from the tube's geometry the way the
AISC Manual tabulates them: design wall thickness 0.93 × nominal (AISC 360
§B4.2), outside corner radius 2t, and weight from the nominal wall. That
keeps the table honest without copying it, and lets any size be checked. A
short list of light W shapes (properties from the AISC Manual, Table 1-1)
covers beams.

Checks are AISC 360-16 LRFD in their simplest form: flexure φMn = 0.9·Fy·Z
for a compact tube (Fy·S otherwise, and always Fy·S for W shapes, which
also assumes the deck braces the compression flange); shear φVn = 0.9 ×
0.6·Fy·Aw; deflection L/360 for a beam and L/180 for a cantilever under
service load; columns by §E3 with the §H1 interaction. Stainless members use
annealed 304/316 properties (Fy 30 ksi, E 28,000 ksi) as an aid; AISC Design
Guide 27 governs a real stainless design.

DOC-0119 says only to use standard, readily available raw materials for
fabricated parts, which this list honors.
"""

from __future__ import annotations

import math

from ..envelope import CalcResult, make_input
from .defaults import DISCLAIMER, merged_rules

CIT_AISC_B4 = "AISC 360-16 §B4.2 (HSS design wall thickness 0.93 t) and Table B4.1b (compact limit 1.12√(E/Fy))"
CIT_AISC_F = "AISC 360-16 Ch. F (flexure: φ = 0.90, Mn = Fy·Z compact, Fy·S as a conservative bound)"
CIT_AISC_G = "AISC 360-16 Ch. G (shear: φVn = 0.9 × 0.6·Fy·Aw)"
CIT_AISC_E = "AISC 360-16 §E3 (flexural buckling) and §H1 (combined axial and flexure)"
CIT_AISC_W = "AISC Steel Construction Manual, 15th ed., Table 1-1 (W-shape dimensions and properties)"
CIT_DEFLECTION = "IBC 2021 Table 1604.3 (L/360 live-load deflection); L/180 for a cantilever"
CIT_DG27 = "AISC Design Guide 27 (structural stainless steel): annealed 304/316 Fy 30 ksi, E 28,000 ksi"
CIT_DOC0119_FAB = "DOC-0119 Fountain Design Guidelines, Custom Design Fabricated Parts (standard raw materials)"

MATERIALS = {
    "A500C": {"label": "ASTM A500 Gr. C tube", "fy_ksi": 50.0, "e_ksi": 29000.0, "pcf": 490.0},
    "A992": {"label": "ASTM A992 W shape", "fy_ksi": 50.0, "e_ksi": 29000.0, "pcf": 490.0},
    "A36": {"label": "ASTM A36 plate and bar", "fy_ksi": 36.0, "e_ksi": 29000.0, "pcf": 490.0},
    "304SS": {"label": "Type 304 stainless, annealed", "fy_ksi": 30.0, "e_ksi": 28000.0, "pcf": 501.0},
    "316SS": {"label": "Type 316 stainless, annealed", "fy_ksi": 30.0, "e_ksi": 28000.0, "pcf": 501.0},
}

# AISC Manual Table 1-1 (strong axis): A in², d in, Ix in⁴, Sx in³, Zx in³, ry in
W_SHAPES = {
    "W8x10": {"wt": 10.0, "A": 2.96, "d": 7.89, "Ix": 30.8, "Sx": 7.81, "Zx": 8.87, "tw": 0.170},
    "W8x18": {"wt": 18.0, "A": 5.26, "d": 8.14, "Ix": 61.9, "Sx": 15.2, "Zx": 17.0, "tw": 0.230},
    "W10x12": {"wt": 12.0, "A": 3.54, "d": 9.87, "Ix": 53.8, "Sx": 10.9, "Zx": 12.6, "tw": 0.190},
    "W10x22": {"wt": 22.0, "A": 6.49, "d": 10.2, "Ix": 118.0, "Sx": 23.2, "Zx": 26.0, "tw": 0.240},
    "W12x14": {"wt": 14.0, "A": 4.16, "d": 11.9, "Ix": 88.6, "Sx": 14.9, "Zx": 17.4, "tw": 0.200},
    "W12x26": {"wt": 26.0, "A": 7.65, "d": 12.2, "Ix": 204.0, "Sx": 33.4, "Zx": 37.2, "tw": 0.230},
    "W14x22": {"wt": 22.0, "A": 6.49, "d": 13.7, "Ix": 199.0, "Sx": 29.0, "Zx": 33.2, "tw": 0.230},
    "W16x26": {"wt": 26.0, "A": 7.68, "d": 15.7, "Ix": 301.0, "Sx": 38.4, "Zx": 44.2, "tw": 0.250},
    "W18x35": {"wt": 35.0, "A": 10.3, "d": 17.7, "Ix": 510.0, "Sx": 57.6, "Zx": 66.5, "tw": 0.300},
}

# Square and rectangular tube sizes stocked by Utah service centers (outside B × H × nominal wall)
_WALLS = (0.125, 0.1875, 0.25, 0.3125, 0.375, 0.5)
HSS_SIZES = [(b, b, t) for b in (2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0) for t in _WALLS
             if t <= b / 6 + 1e-9 and not (b >= 6 and t < 0.1875)] + \
    [(2.0, 4.0, t) for t in _WALLS[:3]] + [(2.0, 6.0, t) for t in _WALLS[1:3]] + \
    [(3.0, 6.0, t) for t in _WALLS[1:5]] + [(4.0, 6.0, t) for t in _WALLS[1:5]] + [(4.0, 8.0, t) for t in _WALLS[1:5]]


def _fraction(x: float) -> str:
    for den in (2, 4, 8, 16):
        n = round(x * den)
        if abs(n / den - x) < 1e-6:
            whole, rem = divmod(n, den)
            g = math.gcd(rem, den)
            frac = f"{rem // g}/{den // g}" if rem else ""
            return f"{whole}-{frac}" if whole and frac else (str(whole) if whole else frac)
    return f"{x:g}"


def _rounded_rect(b: float, h: float, r: float, n: int = 400) -> tuple[float, float, float]:
    """Area, I about the horizontal centroidal axis and plastic first moment
    (2 × ∫ y·w dy over the top half) of a b × h rectangle with corner radius r,
    by Simpson's rule over the height."""
    def width(y):
        y = abs(y)
        flat = h / 2.0 - r
        if y <= flat:
            return b
        dy = y - flat
        return b - 2.0 * (r - math.sqrt(max(r * r - dy * dy, 0.0)))

    half = h / 2.0
    step = half / n
    area = inertia = first = 0.0
    for i in range(n + 1):
        y = i * step
        wgt = 1 if i in (0, n) else (4 if i % 2 else 2)
        wd = width(y)
        area += wgt * wd
        inertia += wgt * wd * y * y
        first += wgt * wd * y
    k = step / 3.0
    return 2 * area * k, 2 * inertia * k, 2 * first * k


def hss_section(b_in: float, h_in: float, t_nominal_in: float) -> dict:
    """Properties of a rectangular HSS bending about its H (strong) axis."""
    t = 0.93 * float(t_nominal_in)
    a_o, i_o, z_o = _rounded_rect(b_in, h_in, 2 * t)
    a_i, i_i, z_i = _rounded_rect(b_in - 2 * t, h_in - 2 * t, t)
    tn = float(t_nominal_in)
    a_nom = _rounded_rect(b_in, h_in, 2 * tn)[0] - _rounded_rect(b_in - 2 * tn, h_in - 2 * tn, tn)[0]
    area = a_o - a_i
    ix = i_o - i_i
    return {
        "name": f"HSS{_fraction(h_in)}x{_fraction(b_in)}x{_fraction(tn)}", "kind": "HSS", "b": b_in, "h": h_in,
        "t_nom": tn, "t_des": t, "A": area, "Ix": ix, "Sx": ix / (h_in / 2.0), "Zx": z_o - z_i,
        "rx": math.sqrt(ix / area), "wt": a_nom * 490.0 / 144.0, "b_t": (b_in - 3 * t) / t,
        "h_t": (h_in - 3 * t) / t, "Aw": 2 * (h_in - 3 * t) * t,
    }


def w_section(name: str) -> dict:
    s = dict(W_SHAPES[name])
    return {"name": name, "kind": "W", **s, "rx": math.sqrt(s["Ix"] / s["A"]), "Aw": s["d"] * s["tw"]}


def _section(section) -> dict:
    if isinstance(section, dict):
        return section
    name = str(section)
    if name in W_SHAPES:
        return w_section(name)
    for b, h, t in HSS_SIZES:
        sec = hss_section(b, h, t)
        if sec["name"] == name:
            return sec
    raise ValueError(f"Unknown section {section!r}")


def member_check(
    section, span_ft: float, w_plf: float = 0.0, p_lb: float = 0.0, support: str = "simple",
    material: str = "A500C", load_factor: float | None = None, rules: dict | None = None,
) -> CalcResult:
    """A beam (simple span, P at midspan) or cantilever (P at the tip) under
    service loads ``w_plf`` (self weight added) and ``p_lb``: factored flexure
    and shear, and service deflection against L/360 or L/180."""
    rules = merged_rules(rules)
    sec = _section(section)
    mat = MATERIALS.get(material, MATERIALS["A500C"])
    fy, e = mat["fy_ksi"] * 1000.0, mat["e_ksi"] * 1000.0
    lf = float(load_factor if load_factor is not None else rules["steel_load_factor"])
    span = float(span_ft)
    w = float(w_plf) + sec["wt"] * mat["pcf"] / 490.0
    p = float(p_lb)
    cant = str(support).lower() == "cantilever"
    if cant:
        m = w * span ** 2 / 2.0 + p * span
        v = w * span + p
        defl = (w / 12.0) * (span * 12) ** 4 / (8 * e * sec["Ix"]) + p * (span * 12) ** 3 / (3 * e * sec["Ix"])
        limit_div = float(rules["cantilever_deflection_div"])
    else:
        m = w * span ** 2 / 8.0 + p * span / 4.0
        v = w * span / 2.0 + p / 2.0
        defl = 5 * (w / 12.0) * (span * 12) ** 4 / (384 * e * sec["Ix"]) + p * (span * 12) ** 3 / (48 * e * sec["Ix"])
        limit_div = float(rules["beam_deflection_div"])
    compact = sec["kind"] == "HSS" and sec["b_t"] <= 1.12 * math.sqrt(e / fy) and sec["h_t"] <= 2.42 * math.sqrt(e / fy)
    mn = fy * (sec["Zx"] if compact else sec["Sx"]) / 12.0  # lb·ft
    phi_mn = 0.9 * mn
    phi_vn = 0.9 * 0.6 * fy * sec["Aw"]
    mu, vu = lf * m, lf * v
    limit = span * 12.0 / limit_div
    ok_m, ok_v, ok_d = mu <= phi_mn + 1e-9, vu <= phi_vn + 1e-9, defl <= limit + 1e-12
    return CalcResult(
        calc="structural_member_check",
        value={"section": sec["name"], "weight_plf": sec["wt"], "support": "cantilever" if cant else "simple",
               "span_ft": span, "mu_lb_ft": mu, "phi_mn_lb_ft": phi_mn, "vu_lb": vu, "phi_vn_lb": phi_vn,
               "deflection_in": defl, "deflection_limit_in": limit, "compact": compact,
               "utilization": max(mu / phi_mn, vu / phi_vn, defl / limit) if limit else None,
               "ok": ok_m and ok_v and ok_d},
        unit="lb·ft",
        inputs={"section": make_input(sec["name"], "", "user"), "span": make_input(span, "ft", "user"),
                "w": make_input(w_plf, "plf", "prior_calc"), "p": make_input(p_lb, "lb", "prior_calc"),
                "material": make_input(material, "", "user"), "load_factor": make_input(lf, "", "default")},
        formula=("M = wL²/2 + PL; Δ = wL⁴/8EI + PL³/3EI" if cant else "M = wL²/8 + PL/4; Δ = 5wL⁴/384EI + PL³/48EI")
        + "; φMn = 0.9·Fy·(Z or S)",
        steps=[f"{sec['name']}: Ix {sec['Ix']:.3f} in⁴, S {sec['Sx']:.3f} in³, Z {sec['Zx']:.3f} in³, "
               f"{sec['wt']:.2f} lb/ft ({'compact' if compact else 'using S'})",
               f"w = {w:.2f} plf with self weight; M = {m:.1f} lb·ft, Mu = {lf:g} × M = {mu:.1f} ≤ φMn {phi_mn:.1f}",
               f"Vu = {vu:.1f} ≤ φVn {phi_vn:.0f} lb; Δ = {defl:.4f} in ≤ L/{limit_div:g} = {limit:.4f} in"],
        citations=[CIT_AISC_F, CIT_AISC_G, CIT_DEFLECTION] + ([CIT_AISC_B4] if sec["kind"] == "HSS" else [CIT_AISC_W])
        + ([CIT_DG27] if "SS" in material else []),
        warnings=[DISCLAIMER] + ([] if ok_m and ok_v and ok_d else [f"{sec['name']} fails: "
                                 + ", ".join(k for k, ok in (("flexure", ok_m), ("shear", ok_v), ("deflection", ok_d))
                                             if not ok)])
        + (["W shape: assumes the deck braces the compression flange (no lateral-torsional buckling)."]
           if sec["kind"] == "W" else []),
        status="ok" if ok_m and ok_v and ok_d else "bad",
    )


def select_member(span_ft: float, w_plf: float = 0.0, p_lb: float = 0.0, support: str = "simple",
                  material: str = "A500C", kind: str = "HSS", square_only: bool = False,
                  rules: dict | None = None) -> CalcResult:
    """The lightest tube (or W shape) that passes ``member_check``."""
    if kind == "W":
        candidates = [w_section(n) for n in W_SHAPES]
    else:
        candidates = [hss_section(b, h, t) for b, h, t in HSS_SIZES if not square_only or b == h]
    candidates.sort(key=lambda s: s["wt"])
    last = None
    for sec in candidates:
        last = member_check(sec, span_ft, w_plf, p_lb, support, material, rules=rules)
        if last.status == "ok":
            last.steps.insert(0, f"lightest passing {kind}: {sec['name']}")
            return last
    last.warnings.append(f"No {kind} in the table passes: a heavier section or an engineer's design is needed.")
    return last


def column_check(section, height_ft: float, axial_lb: float, lateral_plf: float = 0.0, top_lb: float = 0.0,
                 k_factor: float = 2.1, material: str = "A500C", rules: dict | None = None) -> CalcResult:
    """A post or tower fixed at its base and free at the top (K = 2.1):
    factored axial load against §E3, plus the base moment from a lateral
    line load and a lateral point load at the top, by the §H1 interaction."""
    rules = merged_rules(rules)
    sec = _section(section)
    mat = MATERIALS.get(material, MATERIALS["A500C"])
    fy, e = mat["fy_ksi"] * 1000.0, mat["e_ksi"] * 1000.0
    lf = float(rules["steel_load_factor"])
    h = float(height_ft)
    kl_r = k_factor * h * 12.0 / sec["rx"]
    fe = math.pi ** 2 * e / kl_r ** 2
    fcr = (0.658 ** (fy / fe)) * fy if kl_r <= 4.71 * math.sqrt(e / fy) else 0.877 * fe
    phi_pn = 0.9 * fcr * sec["A"]
    pu = lf * (float(axial_lb) + sec["wt"] * h)
    mu = lf * (float(lateral_plf) * h * h / 2.0 + float(top_lb) * h)
    compact = sec["kind"] == "HSS" and sec["b_t"] <= 1.12 * math.sqrt(e / fy)
    phi_mn = 0.9 * fy * (sec["Zx"] if compact else sec["Sx"]) / 12.0
    ratio = pu / phi_pn
    inter = ratio + 8.0 / 9.0 * mu / phi_mn if ratio >= 0.2 else ratio / 2.0 + mu / phi_mn
    drift = (float(lateral_plf) / 12.0) * (h * 12) ** 4 / (8 * e * sec["Ix"]) + float(top_lb) * (h * 12) ** 3 / (3 * e * sec["Ix"])
    drift_limit = h * 12.0 / float(rules["post_drift_div"])
    ok = inter <= 1.0 + 1e-9 and kl_r <= 200 and drift <= drift_limit + 1e-12
    return CalcResult(
        calc="structural_column_check",
        value={"section": sec["name"], "kl_r": kl_r, "phi_pn_lb": phi_pn, "pu_lb": pu, "mu_lb_ft": mu,
               "phi_mn_lb_ft": phi_mn, "interaction": inter, "drift_in": drift, "drift_limit_in": drift_limit,
               "ok": ok},
        unit="",
        inputs={"height": make_input(h, "ft", "user"), "axial": make_input(axial_lb, "lb", "prior_calc"),
                "k": make_input(k_factor, "", "default")},
        formula="Fcr = 0.658^(Fy/Fe)·Fy (or 0.877Fe); Pr/Pc + 8/9·Mr/Mc ≤ 1 (Pr/Pc ≥ 0.2)",
        steps=[f"KL/r = {k_factor:g} × {h * 12:.1f} / {sec['rx']:.3f} = {kl_r:.1f}; Fe = {fe:.0f} psi, Fcr = {fcr:.0f} psi",
               f"φPn = {phi_pn:.0f} lb vs Pu {pu:.0f}; φMn = {phi_mn:.0f} lb·ft vs Mu {mu:.0f}",
               f"interaction = {inter:.3f}; drift {drift:.3f} in vs H/{rules['post_drift_div']:g} = {drift_limit:.3f} in"],
        citations=[CIT_AISC_E, CIT_AISC_F, "AISC Design Guide 3 (serviceability: lateral drift)"],
        warnings=[DISCLAIMER] + ([] if ok else [f"{sec['name']} fails as a column (interaction {inter:.2f}, "
                                               f"KL/r {kl_r:.0f}, drift {drift:.2f} in)."]),
        status="ok" if ok else "bad",
    )


def select_column(height_ft: float, axial_lb: float, lateral_plf: float = 0.0, top_lb: float = 0.0,
                  material: str = "A500C", rules: dict | None = None) -> CalcResult:
    """The lightest square tube that passes ``column_check``."""
    last = None
    for b, h, t in sorted(HSS_SIZES, key=lambda s: hss_section(*s)["wt"]):
        if b != h:
            continue
        last = column_check(hss_section(b, h, t), height_ft, axial_lb, lateral_plf, top_lb, material=material,
                            rules=rules)
        if last.status == "ok":
            return last
    last.warnings.append("No tube in the table passes: the tower needs an engineer's design.")
    return last


def water_wall_frame(
    height_ft: float, width_ft: float, wind_psf: float, header_plf: float = 0.0, post_spacing_ft: float | None = None,
    material: str = "A500C", rules: dict | None = None,
) -> CalcResult:
    """A water wall's frame: square tube posts at ``post_spacing_ft``
    cantilevered from the base under wind on their share of the panel, and a
    header spanning between posts carrying the trough and its water
    (``header_plf``). Returns the members and the steel weight to buy."""
    rules = merged_rules(rules)
    spacing = float(post_spacing_ft or rules["frame_post_spacing_ft"])
    width = float(width_ft)
    n_posts = max(math.ceil(width / spacing - 1e-9) + 1, 2)
    bay = width / (n_posts - 1)
    header = select_member(bay, header_plf, 0.0, "simple", material, square_only=False, rules=rules)
    reaction = (float(header_plf) + header.value["weight_plf"]) * bay
    post = select_column(height_ft, reaction, float(wind_psf) * bay, 0.0, material, rules)
    steel_lb = n_posts * float(height_ft) * _section(post.value["section"])["wt"] + width * header.value["weight_plf"]
    ok = header.status == "ok" and post.status == "ok"
    return CalcResult(
        calc="structural_water_wall_frame",
        value={"posts": n_posts, "bay_ft": bay, "post_section": post.value["section"],
               "header_section": header.value["section"], "steel_lb": steel_lb, "header": header.to_dict(),
               "post": post.to_dict(), "ok": ok},
        unit="lb",
        inputs={"height": make_input(height_ft, "ft", "user"), "width": make_input(width, "ft", "user"),
                "wind": make_input(wind_psf, "psf", "prior_calc"), "header_load": make_input(header_plf, "plf", "user")},
        formula="posts = ceil(width/spacing) + 1; header simple span = bay; posts cantilever under p × bay",
        steps=[f"{n_posts} posts at {bay:.2f} ft; header {header.value['section']}, posts {post.value['section']}",
               f"steel = {n_posts} × {height_ft:g} ft × post + {width:g} ft × header = {steel_lb:.0f} lb"],
        citations=[CIT_AISC_F, CIT_AISC_E, CIT_DOC0119_FAB],
        warnings=[DISCLAIMER] + ([] if ok else ["Frame member fails: shorten the bays or have it engineered."]),
        status="ok" if ok else "bad",
    )
