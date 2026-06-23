"""Basin geometry -> water volume & weight, and turnover -> circulation GPM.

Verified against DOC-0048 ``Basin`` sheet:
    rect area  H = E * F                 (in^2; E=length, F=width)
    cyl  area  H = 0.25 * PI() * (C*C)   (in^2; C=diameter)
         vol   I = H * G                 (in^3; G=height)
         gal   J = I * 0.004329
         lb    K = J * 8.34
    turnover   Gal/hr = Volume(gal) * Turnovers/hr (D15 default 2)
               Gal/min = Gal/hr / 60
"""

from __future__ import annotations

import math

from .constants import CIT_BASIN, DEFAULT_TURNOVERS_PER_HR
from .envelope import CalcResult, make_input
from .units import cubic_inches_to_gallons, gallons_to_pounds


def basin_volume(
    shape: str,
    length_in: float = 0.0,
    width_in: float = 0.0,
    height_in: float = 0.0,
    diameter_in: float = 0.0,
) -> CalcResult:
    """Water volume (gallons) for a rectangular or cylindrical basin.

    The companion weight (lb) is shown in ``steps``; call
    :func:`~.units.gallons_to_pounds` on ``value`` for the number.
    """
    shape_key = (shape or "").strip().lower()

    if shape_key in ("rect", "rectangle", "rectangular"):
        area_in2 = float(length_in) * float(width_in)
        formula = "vol_gal = (L * W * H) * 0.004329 ; weight_lb = vol_gal * 8.34"
        inputs = {
            "length": make_input(length_in, "in", "user", "Basin!E"),
            "width": make_input(width_in, "in", "user", "Basin!F"),
            "height": make_input(height_in, "in", "user", "Basin!G"),
        }
        steps = [f"area = {length_in} * {width_in} = {area_in2:g} in^2"]
    elif shape_key in ("cyl", "cylinder", "cylindrical"):
        area_in2 = 0.25 * math.pi * float(diameter_in) ** 2
        formula = "vol_gal = (0.25 * pi * D^2 * H) * 0.004329 ; weight_lb = vol_gal * 8.34"
        inputs = {
            "diameter": make_input(diameter_in, "in", "user", "Basin!C"),
            "height": make_input(height_in, "in", "user", "Basin!G"),
        }
        steps = [f"area = 0.25 * pi * {diameter_in}^2 = {area_in2:g} in^2"]
    else:
        return CalcResult(
            calc="basin_volume",
            unit="gal",
            inputs={"shape": make_input(shape, "", "user")},
            formula="(rectangular or cylindrical)",
            citations=[CIT_BASIN],
            warnings=[f"Unknown basin shape {shape!r}; use 'rectangular' or 'cylindrical'."],
        )

    vol_in3 = area_in2 * float(height_in)
    gal = cubic_inches_to_gallons(vol_in3)
    weight_lb = gallons_to_pounds(gal)
    steps += [
        f"vol_in3 = {area_in2:g} * {height_in} = {vol_in3:g} in^3",
        f"vol_gal = {vol_in3:g} * 0.004329 = {gal:.4f} gal",
        f"weight_lb = {gal:.4f} * 8.34 = {weight_lb:.2f} lb",
    ]
    return CalcResult(
        calc="basin_volume",
        value=gal,
        unit="gal",
        inputs=inputs,
        formula=formula,
        steps=steps,
        citations=[CIT_BASIN],
    )


def turnover_gpm(volume_gal: float, turnovers_per_hr: float = DEFAULT_TURNOVERS_PER_HR) -> CalcResult:
    """Required circulation flow (GPM) to turn the basin over N times an hour."""
    gph = float(volume_gal) * float(turnovers_per_hr)
    gpm = gph / 60.0
    return CalcResult(
        calc="turnover_gpm",
        value=gpm,
        unit="GPM",
        inputs={
            "volume": make_input(volume_gal, "gal", "prior_calc", "basin_volume"),
            "turnovers_per_hr": make_input(turnovers_per_hr, "1/hr", "user", "Basin!D15 (default 2)"),
        },
        formula="circ_gpm = volume_gal * turnovers_per_hr / 60",
        steps=[
            f"gal_per_hr = {volume_gal:g} * {turnovers_per_hr} = {gph:g}",
            f"gpm = {gph:g} / 60 = {gpm:.4f}",
        ],
        citations=[CIT_BASIN],
    )
