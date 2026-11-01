"""Heat loss from the water surface: evaporation, convection and radiation.

Engine audit section 1.14. ``treatment.heating_load`` reproduces the DOC-0049 O-sheet, whose
loss scales with water *volume*; real heat loss scales with *surface area*, so the sheet
misstates shallow, wide fountains. This module computes the surface heat balance and reports
the DOC-0049 figure beside it (``data["doc0049_btu_hr"]``). Phase E decides which governs;
``treatment.heating_load`` is unchanged.

A **system module** (see :mod:`.lighting`): ``inputs()``, ``calc(model, derived=None,
rules=None)`` and ``checks(...)``. It reads the basins' surface area and
``model["treatment"]["heat"]``: ``target_water_temp_f``, ``design_air_temp_f``, ``rh_pct``,
``wind_mph``, ``cover`` (``none`` | ``solid`` | ``liquid``). :func:`surface_heat_loss` is the
plain calculator.

No solar gain is counted, so the figure is the night-time (worst) loss.
"""

from __future__ import annotations

import math
from typing import Any

from ..constants import HEAT_COVER_FACTOR, HEAT_DEPTH_FACTOR, HEAT_WIND_FACTOR, LB_PER_GAL_PRECISE
from ..basin import basin_volume
from ..envelope import make_input
from ..treatment import heating_load
from .base import GAL_PER_CUBIC_FOOT, INFO, WARNING, Check, FamilyResult, basin_plan, num

FAMILY = "heat"
LABEL = "Heating"

CIT_CARRIER = (
    "ASHRAE Handbook, HVAC Applications ch. 6 (Carrier), I-P form: w = A (pw - pa)(95 + 0.425 V) / Y lb/h, "
    "A ft2, p inHg, V fpm, Y Btu/lb; so q_evap = w Y = A (pw - pa)(95 + 0.425 V) Btu/h"
)
CIT_MCADAMS = "McAdams / Watmuff et al. (1977): h_c = 5.7 + 3.8 v W/m2K, v in m/s"
CIT_SWINBANK = "Swinbank (1963): T_sky = 0.0552 T_air^1.5 (K); water emissivity 0.95"
CIT_BUCK = "Buck (1981) saturation vapor pressure over water"
CIT_DOC0049 = "DOC-0049 / O - Heating (treatment.heating_load, shown for comparison)"

SIGMA_BTU = 0.1714e-8  # Btu/h ft2 R4
EMISSIVITY = 0.95
W_M2K_TO_BTU = 0.17611
KPA_TO_INHG = 0.295300
MPH_TO_MPS = 0.44704
FPM_PER_MPH = 88.0
# A cover cuts evaporation by the DOC-0049 O-sheet's own cover factors, relative to none
# (solid 0.2 / 0.5 = 0.4, liquid 0.3 / 0.5 = 0.6).
COVER_EVAP = {k: v / HEAT_COVER_FACTOR["none"] for k, v in HEAT_COVER_FACTOR.items()}
# Placeholders when the designer has not entered the site's humidity and wind.
PLACEHOLDER_RH = 40.0
PLACEHOLDER_WIND = 5.0
DISAGREE_RATIO = 2.0


def inputs() -> list[dict[str, Any]]:
    from .base import spec

    return [
        spec("target_water_temp_f", "Water temperature to hold", "number", "F", None, True),
        spec("design_air_temp_f", "Design air temperature", "number", "F", None, False, "Blank uses the site's air temperature."),
        spec("rh_pct", "Relative humidity", "number", "%", None, False, "Blank uses 40% (a placeholder)."),
        spec("wind_mph", "Wind over the water", "number", "mph", None, False, "Blank uses 5 mph (a placeholder)."),
        spec("cover", "Cover", "select", "", "none", False, "", list(COVER_EVAP)),
    ]


def f_to_c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def sat_pressure_inhg(temp_f: float) -> float:
    t = f_to_c(temp_f)
    kpa = 0.61121 * math.exp((18.678 - t / 234.5) * (t / (257.14 + t)))
    return kpa * KPA_TO_INHG


def surface_heat_loss(area_sqft: float, water_f: float, air_f: float, rh_pct: float = 40.0,
                      wind_mph: float = 5.0, cover: str = "none") -> dict[str, float]:
    """Btu/h lost from ``area_sqft`` of water at ``water_f`` to air at ``air_f``."""
    a = max(area_sqft, 0.0)
    pw = sat_pressure_inhg(water_f)
    pa = max(min(rh_pct, 100.0), 0.0) / 100.0 * sat_pressure_inhg(air_f)
    v_fpm = max(wind_mph, 0.0) * FPM_PER_MPH
    evap = a * max(pw - pa, 0.0) * (95.0 + 0.425 * v_fpm) * COVER_EVAP.get(cover, 1.0)
    h_c = (5.7 + 3.8 * max(wind_mph, 0.0) * MPH_TO_MPS) * W_M2K_TO_BTU
    conv = h_c * a * (water_f - air_f)
    tw_r = water_f + 459.67
    ta_k = f_to_c(air_f) + 273.15
    tsky_r = 0.0552 * ta_k**1.5 * 1.8
    rad = EMISSIVITY * SIGMA_BTU * a * (tw_r**4 - tsky_r**4)
    return {"evaporation": evap, "convection": conv, "radiation": rad, "total": evap + conv + rad,
            "pw_inhg": pw, "pa_inhg": pa, "h_c": h_c, "t_sky_f": tsky_r - 459.67}


def doc0049_btu_hr(volume_gal: float, delta_f: float, cover: str = "none", wind: bool = True) -> float:
    """The DOC-0049 O-sheet's maintenance load, in Btu/h (its BTU/day / 24)."""
    mult = volume_gal * LB_PER_GAL_PRECISE * HEAT_COVER_FACTOR.get(cover, 0.5) * HEAT_DEPTH_FACTOR * HEAT_WIND_FACTOR[bool(wind)]
    return mult * delta_f / 24.0


def _basins(model: dict[str, Any]) -> tuple[float, float]:
    area = volume = 0.0
    for b in model.get("basins") or []:
        plan = basin_plan(b)
        area += plan["area_sqft"]
        shape = str(b.get("shape") or "rectangular").lower()
        if num(b.get("volume_gal_override")):
            volume += num(b.get("volume_gal_override"))
        elif shape.startswith(("rect", "cyl", "round", "circ")):
            volume += basin_volume(
                "Rectangular" if shape.startswith("rect") else "Cylindrical",
                length_in=num(b.get("length_in")), width_in=num(b.get("width_in")),
                height_in=num(b.get("depth_in")), diameter_in=num(b.get("diameter_in")),
            ).value or 0.0
        else:  # "other": a surface area and a depth
            volume += plan["area_sqft"] * plan["depth_ft"] * GAL_PER_CUBIC_FOOT
    return area, volume


def calc(model: dict[str, Any], derived: dict[str, Any] | None = None, rules: dict[str, Any] | None = None) -> FamilyResult:
    site = model.get("site") or {}
    h = (model.get("treatment") or {}).get("heat") or {}
    tw = num(h.get("target_water_temp_f")) or num(site.get("water_temp_f"))
    ta_raw = h.get("design_air_temp_f")
    if ta_raw in (None, ""):
        ta_raw = site.get("air_temp_f")
    rh = num(h.get("rh_pct"), PLACEHOLDER_RH)
    wind = num(h.get("wind_mph"), PLACEHOLDER_WIND)
    warnings = [
        f"No {name} entered: using {val:g}{unit} as a placeholder."
        for key, name, val, unit in (("rh_pct", "humidity", PLACEHOLDER_RH, "%"), ("wind_mph", "wind", PLACEHOLDER_WIND, " mph"))
        if h.get(key) in (None, "")
    ]
    cover = str(h.get("cover") or "none").lower()
    area, volume = _basins(model)
    if area <= 0 or not tw or ta_raw in (None, ""):
        return FamilyResult(calc="surface_heat_loss", unit="Btu/h",
                            warnings=["Enter the basin size, the water temperature to hold and the design air temperature."],
                            data={"complete": False})
    ta = num(ta_raw)
    loss = surface_heat_loss(area, tw, ta, rh, wind, cover)
    dt = tw - ta
    doc = doc0049_btu_hr(volume, dt, cover, wind > 0) if volume > 0 else None
    doc_result = heating_load(volume, dt, cover=cover, wind=wind > 0).to_dict() if volume > 0 else None
    steps = [
        f"evaporation: {area:.0f} sq ft x ({loss['pw_inhg']:.3f} - {loss['pa_inhg']:.3f}) inHg x (95 + 0.425 x {wind * FPM_PER_MPH:g} fpm) = {loss['evaporation']:.0f} Btu/h",
        f"convection: h {loss['h_c']:.2f} x {area:.0f} x {dt:g} F = {loss['convection']:.0f} Btu/h",
        f"radiation: 0.95 sigma {area:.0f} (T_w^4 - T_sky^4), sky {loss['t_sky_f']:.0f} F = {loss['radiation']:.0f} Btu/h",
        f"surface loss = {loss['total']:.0f} Btu/h",
    ]
    if doc is not None:
        steps.append(f"DOC-0049 volume method for comparison: {doc:.0f} Btu/h")
    return FamilyResult(
        calc="surface_heat_loss",
        value=loss["total"],
        unit="Btu/h",
        inputs={
            "area_sqft": make_input(round(area, 2), "sq ft", "prior_calc"),
            "water_f": make_input(tw, "F", "user"),
            "air_f": make_input(ta, "F", "user"),
            "rh_pct": make_input(rh, "%", "user"),
            "wind_mph": make_input(wind, "mph", "user"),
        },
        formula="q = A (pw - pa)(95 + 0.425 V_fpm) + h_c A dT + e sigma A (Tw^4 - Tsky^4)",
        steps=steps,
        citations=[CIT_CARRIER, CIT_MCADAMS, CIT_SWINBANK, CIT_BUCK, CIT_DOC0049],
        warnings=warnings,
        data={**loss, "area_sqft": area, "volume_gal": volume, "delta_f": dt, "surface_btu_hr": loss["total"],
              "doc0049_btu_hr": doc, "doc0049": doc_result, "complete": True},
    )


def checks(model: dict[str, Any], derived: dict[str, Any] | None = None, rules: dict[str, Any] | None = None) -> list[Check]:
    d = (derived or {}).get("heat") or calc(model, derived, rules).data
    if not d.get("complete") or not d.get("doc0049_btu_hr"):
        return []
    a, b = d["surface_btu_hr"], d["doc0049_btu_hr"]
    if a > 0 and b > 0 and max(a, b) / min(a, b) > DISAGREE_RATIO:
        return [Check(
            "HEAT_METHODS_DISAGREE", WARNING, "The two heat-loss methods disagree by more than 2x",
            detail=f"Surface balance {a:.0f} Btu/h; DOC-0049 volume method {b:.0f} Btu/h. The surface balance "
                   "follows the water area; the workbook follows volume, so shallow wide basins differ most.",
            remedy="Size the heater from the surface balance and note the difference.", section="treatment",
            citation=CIT_CARRIER + "; " + CIT_DOC0049,
        )]
    return [Check("HEAT_SIDE_BY_SIDE", INFO, "Heat loss: surface balance and DOC-0049 shown side by side",
                  detail=f"{a:.0f} vs {b:.0f} Btu/h.", section="treatment")]
