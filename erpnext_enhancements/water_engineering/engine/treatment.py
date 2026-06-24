"""Water-treatment, thermal, and equipment-sizing calculations.

* :func:`lsi_index`       — Langelier saturation index (scale vs. corrode)
* :func:`evaporation_rate`— ASHRAE pool evaporation -> make-up + latent heat
* :func:`make_up_water`   — daily make-up demand + auto-fill valve size
* :func:`heating_load`    — DOC-0049 O-sheet heat-loss BTU/day + gas cost
* :func:`chemical_dose`   — acid/bicarb/CYA/salt dose to hit a target
* :func:`uv_dose`         — UV disinfection design dose / RED
* :func:`filtration_area` — filter media area vs. NSF/Utah max rate + backwash

The treatment/thermal constants that are NOT in the Sapphire workbooks are
flagged as engineering-standard in ``constants`` and in each result's citations.
"""

from __future__ import annotations

from itertools import pairwise

from .constants import (
    ACID_OZ_PER_10K_PER_0_2_PH,
    AUTOFILL_VALVE_GPM,
    BICARB_LB_PER_10K_PER_10_TA,
    BTU_PER_GAL_DEGF,
    CIT_DOSE,
    CIT_EVAP,
    CIT_FILTER,
    CIT_HEATING,
    CIT_LSI,
    CIT_MAKEUP,
    CIT_UV,
    CYA_PPM_PER_LB_PER_10K,
    EVAP_ACTIVITY_FACTOR,
    EVAP_ASHRAE_COEFF,
    FILTER_BACKWASH_RATE,
    FILTER_MAX_RATE,
    HEAT_BTU_PER_THERM,
    HEAT_COVER_FACTOR,
    HEAT_DAYS_PER_MONTH,
    HEAT_DEFAULT_EFF,
    HEAT_DEFAULT_GAS_RATE,
    HEAT_DEPTH_FACTOR,
    HEAT_WIND_FACTOR,
    LB_PER_GAL_PRECISE,
    LSI_AF,
    LSI_CF,
    LSI_TDS_CONSTANT,
    LSI_TF,
    PSIA_TO_INHG,
    SALT_LB_PER_10K_PER_100PPM,
    UV_DOSE_4LOG_MJ,
    UV_DOSE_DECHLORAMINE_MJ,
)
from .envelope import CalcResult, make_input
from .safety import _vapor_pressure_psia


def _interp(table: dict, x: float) -> float:
    """Linear-interpolate a {key: value} lookup at x (clamped at the ends)."""
    pts = sorted(table.items())
    x = float(x)
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in pairwise(pts):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * ((x - x0) / (x1 - x0))
    return pts[-1][1]


def lsi_index(
    ph: float,
    temp_f: float,
    calcium_hardness_ppm: float,
    total_alkalinity_ppm: float,
    tds_ppm: float = 1000.0,
) -> CalcResult:
    """Langelier Saturation Index: LSI = pH + TF + CF + AF - TDS_constant.
    Target 0.0..+0.3 (acceptable -0.3..+0.5); negative corrodes, positive scales."""
    tf = _interp(LSI_TF, temp_f)
    cf = _interp(LSI_CF, calcium_hardness_ppm)
    af = _interp(LSI_AF, total_alkalinity_ppm)
    const = _interp(LSI_TDS_CONSTANT, tds_ppm)
    lsi = float(ph) + tf + cf + af - const
    if lsi < -0.3:
        status = "Corrosive"
    elif lsi > 0.3:
        status = "Scaling"
    else:
        status = "Balanced"
    warnings = []
    if status != "Balanced":
        warnings.append(
            f"LSI {lsi:+.2f} is {status.lower()} — "
            + ("raise pH / alkalinity / calcium." if lsi < 0 else "lower pH / alkalinity.")
        )
    return CalcResult(
        calc="lsi_index",
        value=round(lsi, 2),
        unit="LSI",
        inputs={
            "ph": make_input(ph, "", "user"),
            "temp_f": make_input(temp_f, "degF", "user"),
            "calcium_hardness_ppm": make_input(calcium_hardness_ppm, "ppm", "user"),
            "total_alkalinity_ppm": make_input(total_alkalinity_ppm, "ppm", "user"),
            "tds_ppm": make_input(tds_ppm, "ppm", "user"),
        },
        formula="LSI = pH + TF + CF + AF - TDS_constant",
        steps=[
            f"TF({temp_f:g}F)={tf:.2f}; CF({calcium_hardness_ppm:g})={cf:.2f}; AF({total_alkalinity_ppm:g})={af:.2f}; const={const:.2f}",
            f"LSI = {float(ph):g} + {tf:.2f} + {cf:.2f} + {af:.2f} - {const:.2f} = {lsi:+.2f} ({status})",
        ],
        citations=[CIT_LSI],
        status=status,
        warnings=warnings,
    )


def evaporation_rate(
    surface_area_sf: float,
    water_temp_f: float,
    air_temp_f: float,
    rh_pct: float,
    activity: str = "residential",
) -> CalcResult:
    """ASHRAE pool evaporation: ER = 0.1*A*AF*(Pw - Pa) lb/h, converted to a
    daily make-up volume and the latent heat it carries off."""
    a = float(surface_area_sf)
    af = EVAP_ACTIVITY_FACTOR.get((activity or "residential").strip().lower(), 0.5)
    pw = _vapor_pressure_psia(water_temp_f) * PSIA_TO_INHG  # inHg at the water surface
    pa = (float(rh_pct) / 100.0) * _vapor_pressure_psia(air_temp_f) * PSIA_TO_INHG  # actual air vapor pressure
    er_lb_hr = EVAP_ASHRAE_COEFF * a * af * max(pw - pa, 0.0)
    gal_day = er_lb_hr * 24.0 / LB_PER_GAL_PRECISE
    latent_btu_hr = er_lb_hr * 1050.0  # ~1050 BTU/lb latent heat of vaporization
    return CalcResult(
        calc="evaporation_rate",
        value=round(gal_day, 1),
        unit="gal/day",
        inputs={
            "surface_area_sf": make_input(a, "SF", "prior_calc"),
            "water_temp_f": make_input(water_temp_f, "degF", "user"),
            "air_temp_f": make_input(air_temp_f, "degF", "user"),
            "rh_pct": make_input(rh_pct, "%", "user"),
            "activity": make_input(af, "", "user", "ASHRAE activity factor"),
        },
        formula="ER(lb/h) = 0.1*A*AF*(Pw - Pa); make-up gal/day = ER*24/8.337",
        steps=[
            f"Pw({water_temp_f:g}F)={pw:.3f} inHg; Pa({air_temp_f:g}F,{rh_pct:g}%RH)={pa:.3f} inHg",
            f"ER = 0.1*{a:g}*{af:g}*({pw:.3f}-{pa:.3f}) = {er_lb_hr:.2f} lb/h",
            f"make-up = {er_lb_hr:.2f}*24/8.337 = {gal_day:.1f} gal/day; latent heat ~{latent_btu_hr:.0f} BTU/h",
        ],
        citations=[CIT_EVAP],
    )


def make_up_water(
    evaporation_gpd: float,
    splash_gpd: float = 0.0,
    backwash_gpd: float = 0.0,
    fill_window_min: float = 20.0,
) -> CalcResult:
    """Daily make-up demand (evaporation + splash + backwash) and the smallest
    auto-fill valve that can refill it within ``fill_window_min``."""
    total = float(evaporation_gpd) + float(splash_gpd) + float(backwash_gpd)
    need_gpm = total / float(fill_window_min) if fill_window_min else 0.0
    valve = next((size for size, gpm in sorted(AUTOFILL_VALVE_GPM.items(), key=lambda kv: kv[1]) if gpm >= need_gpm), None)
    warnings = []
    if valve is None:
        warnings.append(f"No single auto-fill valve covers {need_gpm:.1f} GPM — split the fill or widen the window.")
    return CalcResult(
        calc="make_up_water",
        value=round(total, 1),
        unit="gal/day",
        inputs={
            "evaporation_gpd": make_input(evaporation_gpd, "gal/day", "prior_calc"),
            "splash_gpd": make_input(splash_gpd, "gal/day", "user"),
            "backwash_gpd": make_input(backwash_gpd, "gal/day", "prior_calc"),
            "fill_window_min": make_input(fill_window_min, "min", "user"),
        },
        formula="total = evap + splash + backwash ; valve sized for total / fill_window",
        steps=[
            f"total = {evaporation_gpd:g} + {splash_gpd:g} + {backwash_gpd:g} = {total:.1f} gal/day",
            f"refill rate = {total:.1f}/{fill_window_min:g} min = {need_gpm:.2f} GPM -> {valve or 'oversize'} valve",
        ],
        citations=[CIT_MAKEUP],
        status=valve,
        warnings=warnings,
    )


def heating_load(
    volume_gal: float,
    delta_f: float,
    cover: str = "none",
    wind: bool = True,
    gas_rate: float = HEAT_DEFAULT_GAS_RATE,
    heater_eff: float = HEAT_DEFAULT_EFF,
    warmup_hours: float = 24.0,
) -> CalcResult:
    """DOC-0049 O-sheet heat-loss model: multiplier = water_wt * cover * depth *
    wind; BTU/day = multiplier * deltaF; monthly gas cost; plus a warm-up heater
    BTU/hr so the headline heater size is the larger of the two demands."""
    vol = float(volume_gal)
    dt = float(delta_f)
    cover_f = HEAT_COVER_FACTOR.get((cover or "none").strip().lower(), 0.5)
    wind_f = HEAT_WIND_FACTOR[bool(wind)]
    water_wt = vol * LB_PER_GAL_PRECISE
    multiplier = water_wt * cover_f * HEAT_DEPTH_FACTOR * wind_f
    btu_day = multiplier * dt
    monthly_cost = btu_day / HEAT_BTU_PER_THERM * HEAT_DAYS_PER_MONTH * gas_rate / heater_eff
    maintenance_btu_hr = btu_day / 24.0
    warmup_btu = vol * BTU_PER_GAL_DEGF * dt
    warmup_btu_hr = warmup_btu / warmup_hours if warmup_hours else 0.0
    heater_btu_hr = max(maintenance_btu_hr, warmup_btu_hr)
    return CalcResult(
        calc="heating_load",
        value=round(monthly_cost, 2),
        unit="$/month",
        inputs={
            "volume_gal": make_input(vol, "gal", "prior_calc"),
            "delta_f": make_input(dt, "degF", "user", "desired - ambient"),
            "cover": make_input(cover, "", "user", "none|solid|liquid"),
            "wind": make_input(wind, "", "user"),
            "gas_rate": make_input(gas_rate, "$/therm", "user"),
            "heater_eff": make_input(heater_eff, "", "default"),
        },
        formula="BTU/day = (water_wt * cover * 1.15 * wind) * dF ; $/mo = BTU/day*30.4*rate/(1e5*eff)",
        steps=[
            f"water wt = {vol:g}*8.337 = {water_wt:.0f} lb; multiplier = {water_wt:.0f}*{cover_f:g}*1.15*{wind_f:g} = {multiplier:.0f}",
            f"BTU/day = {multiplier:.0f}*{dt:g} = {btu_day:.0f}",
            f"gas $/mo = {btu_day:.0f}/1e5*30.4*{gas_rate:g}/{heater_eff:g} = {monthly_cost:.2f}",
            f"heater size = max(maintenance {maintenance_btu_hr:.0f}, warm-up {warmup_btu_hr:.0f}) = {heater_btu_hr:.0f} BTU/hr",
        ],
        citations=[CIT_HEATING],
    )


def chemical_dose(volume_gal: float, chemical: str, current: float, target: float) -> CalcResult:
    """Dose to move a parameter to target. ``chemical`` is one of ph_down (muriatic
    acid, fl oz), alkalinity_up (sodium bicarbonate, lb), cya_up (lb), salt_up (lb)."""
    vol = float(volume_gal)
    chem = (chemical or "").strip().lower()
    cur = float(current)
    tgt = float(target)
    scale = vol / 10000.0
    dose = 0.0
    unit = "lb"
    if chem == "ph_down":
        unit = "fl oz"
        dose = max(cur - tgt, 0.0) / 0.2 * ACID_OZ_PER_10K_PER_0_2_PH * scale
    elif chem == "alkalinity_up":
        dose = max(tgt - cur, 0.0) / 10.0 * BICARB_LB_PER_10K_PER_10_TA * scale
    elif chem == "cya_up":
        dose = max(tgt - cur, 0.0) / CYA_PPM_PER_LB_PER_10K * scale
    elif chem == "salt_up":
        dose = max(tgt - cur, 0.0) / 100.0 * SALT_LB_PER_10K_PER_100PPM * scale
    else:
        return CalcResult(
            calc="chemical_dose",
            citations=[CIT_DOSE],
            warnings=[f"Unknown chemical {chemical!r}. Use ph_down, alkalinity_up, cya_up, or salt_up."],
        )
    return CalcResult(
        calc="chemical_dose",
        value=round(dose, 2),
        unit=unit,
        inputs={
            "volume_gal": make_input(vol, "gal", "prior_calc"),
            "chemical": make_input(chem, "", "user"),
            "current": make_input(cur, "ppm/pH", "user"),
            "target": make_input(tgt, "ppm/pH", "user"),
        },
        formula="dose scales linearly with volume/10000 and the (target-current) gap",
        steps=[f"{chem}: move {cur:g} -> {tgt:g} over {vol:g} gal -> {dose:.2f} {unit}"],
        citations=[CIT_DOSE],
        warnings=["Estimate — buffering varies. Add ~3/4, recirculate, and retest before re-dosing."],
    )


def uv_dose(flow_gpm: float, target_red_mj: float = UV_DOSE_DECHLORAMINE_MJ) -> CalcResult:
    """UV design dose (RED, mJ/cm^2) at the design recirculation flow. Reactor
    selection is by a validated RED at this flow — a higher flow lowers the
    delivered dose, so the reactor must be validated at ``flow_gpm``."""
    q = float(flow_gpm)
    target = float(target_red_mj)
    return CalcResult(
        calc="uv_dose",
        value=round(target, 1),
        unit="mJ/cm^2 RED",
        inputs={
            "flow_gpm": make_input(q, "GPM", "prior_calc", "recirculation flow"),
            "target_red_mj": make_input(target, "mJ/cm^2", "user"),
        },
        formula="select a UV reactor with a validated RED >= target at the design flow",
        steps=[
            f"design flow = {q:g} GPM",
            f"target RED = {target:g} mJ/cm^2 (dechloramine {UV_DOSE_DECHLORAMINE_MJ:g}, 4-log crypto {UV_DOSE_4LOG_MJ:g})",
        ],
        citations=[CIT_UV],
        warnings=[
            "Size the reactor by its VALIDATED RED at this flow (not lamp wattage); "
            "apply lamp-aging + quartz-fouling factors."
        ],
    )


def filtration_area(design_gpm: float, media: str = "sand", rate_gpm_sf: float = 0.0) -> CalcResult:
    """Required filter media area = design GPM / max filtration rate (GPM/SF),
    plus the backwash flow. Sand is capped at 3 GPM/SF by Utah R392-302-1."""
    q = float(design_gpm)
    m = (media or "sand").strip().lower()
    rate = float(rate_gpm_sf) or FILTER_MAX_RATE.get(m, 3.0)
    area = q / rate if rate else 0.0
    backwash_gpm = FILTER_BACKWASH_RATE * area if m in ("sand", "high-rate sand", "de") else 0.0
    warnings = []
    if m == "sand" and rate > 3.0:
        warnings.append("Rapid-sand filters are capped at 3 GPM/SF by Utah R392-302-1 — high-rate sand is a different listing.")
    return CalcResult(
        calc="filtration_area",
        value=round(area, 2),
        unit="SF",
        inputs={
            "design_gpm": make_input(q, "GPM", "prior_calc"),
            "media": make_input(m, "", "user", "sand|high-rate sand|cartridge|de"),
            "rate_gpm_sf": make_input(rate, "GPM/SF", "lookup", "max filtration rate"),
        },
        formula="area = design_GPM / max_rate ; backwash = 15 GPM/SF * area (sand/DE)",
        steps=[
            f"required area = {q:g}/{rate:g} = {area:.2f} SF",
            f"backwash flow = 15*{area:.2f} = {backwash_gpm:.1f} GPM" if backwash_gpm else "backwash: n/a (cartridge)",
        ],
        citations=[CIT_FILTER],
        warnings=warnings,
    )
