"""Water properties, the Darcy friction factor, pipe area and pressure-wave speed.

Port of Digital Rain Curtain ``src/hydro/common.js`` ``water``, ``frictionFactor``,
``areaOf``, ``waveSpeed``; golden file ``tests/golden/drc/water.json`` (1e-12).

The ``*_si`` kernels are the port: SI in, SI out, the same arithmetic in the same
order as the JavaScript. The envelope functions without the suffix take and return
US customary and wrap the kernel in a :class:`CalcResult`.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from ..envelope import CalcResult, make_input
from . import _js
from .units import CIT_DRC, M_PER_FT, MM_PER_IN, PA_PER_PSI, c_to_f, f_to_c

CIT_WATER = (
    "Water properties: density Tanaka/Thiesen (Metrologia 2001), viscosity Vogel equation, "
    "surface tension IAPWS R1-76 (2014), vapor pressure Antoine (1-100 C)"
)
CIT_CHURCHILL = "Churchill, S.W. (1977) 'Friction-factor equation spans all fluid-flow regimes', Chem. Eng. 84(24)"
CIT_KORTEWEG = "Korteweg wave speed, thin-walled elastic pipe (Wylie & Streeter, Fluid Transients, eq. 1.4)"


@dataclass(frozen=True, slots=True)
class Water:
    """Water at temperature ``T`` (deg C, clamped 0-100): SI throughout."""

    T: float  # deg C
    rho: float  # kg/m^3
    mu: float  # Pa*s
    nu: float  # m^2/s
    sigma: float  # N/m
    pv: float  # Pa, vapor pressure
    K: float  # Pa, bulk modulus
    latent: float  # J/kg, latent heat of vaporization

    def to_dict(self) -> dict:
        return asdict(self)


def water_si(t_c: float = 20.0) -> Water:
    """``water(T)``: Thiesen density, Vogel viscosity, IAPWS surface tension, Antoine vapor pressure."""
    t = _js.jmin(100.0, _js.jmax(0.0, _js.or_(_js.num(t_c), 0.0)))
    rho = 999.97495 * (1 - ((t - 3.983035) ** 2 * (t + 301.797)) / (522528.9 * (t + 69.34881)))
    mu = 2.414e-5 * 10 ** (247.8 / (t + 273.15 - 140))
    tr = 1 - (t + 273.15) / 647.096
    sigma = 0.2358 * tr**1.256 * (1 - 0.625 * tr)
    pv = 133.322 * 10 ** (8.07131 - 1730.63 / (233.426 + t))
    return Water(T=t, rho=rho, mu=mu, nu=mu / rho, sigma=sigma, pv=pv, K=2.2e9, latent=(2501 - 2.37 * t) * 1000)


def friction_factor_si(re: float, rel_rough: float = 0.0) -> float:
    """``frictionFactor``: Churchill (1977), one formula for laminar, transition and turbulent flow."""
    if not (re > 0):
        return 0.0
    if re < 1e-6:
        return 64 / re
    a = _js.jpow(2.457 * _js.jlog(1 / (_js.jpow(7 / re, 0.9) + 0.27 * rel_rough)), 16)
    b = _js.jpow(37530 / re, 16)
    return 8 * _js.jpow(_js.jpow(8 / re, 12) + _js.jpow(a + b, -1.5), 1 / 12)


def area_of_si(d_m: float) -> float:
    """``areaOf``: circle area from a diameter."""
    return (math.pi * d_m * d_m) / 4


def wave_speed_si(w: Water, id_m: float, wall_m: float, e_gpa: float) -> float:
    """``waveSpeed``: pressure-wave speed in a liquid-filled elastic pipe (m/s), anchored with joints."""
    return _js.jsqrt(w.K / w.rho / (1 + (w.K * id_m) / (e_gpa * 1e9 * _js.jmax(wall_m, 1e-5))))


# ---------------------------------------------------------------------------
# Envelopes (US customary at the boundary)
# ---------------------------------------------------------------------------


def water(temp_f: float = 68.0) -> CalcResult:
    """Water properties at a temperature (deg F), in US and SI units."""
    temp_f = float(temp_f)
    w = water_si(f_to_c(temp_f))
    warnings = []
    if not 32.0 <= temp_f <= 212.0:
        warnings.append(f"{temp_f:g} F is outside 32-212 F; properties are taken at {c_to_f(w.T):g} F.")
    value = {
        "density_lb_ft3": w.rho * M_PER_FT**3 / 0.45359237,
        "kinematic_viscosity_ft2_s": w.nu / M_PER_FT**2,
        "vapor_pressure_psia": w.pv / PA_PER_PSI,
        "surface_tension_lbf_ft": w.sigma * M_PER_FT / 4.4482216152605,
        "si": w.to_dict(),
    }
    return CalcResult(
        calc="water_properties",
        value=value,
        unit="",
        inputs={"water_temp": make_input(temp_f, "degF", "user")},
        formula=(
            "rho = 999.97495(1 - (t-3.983035)^2 (t+301.797) / (522528.9 (t+69.34881))) ; "
            "mu = 2.414e-5 * 10^(247.8/(T-140)) ; pv = 133.322 * 10^(8.07131 - 1730.63/(233.426+t))"
        ),
        steps=[
            f"t = {w.T:.3f} C",
            f"rho = {w.rho:.4f} kg/m^3 ; mu = {w.mu:.4e} Pa*s ; nu = {w.nu:.4e} m^2/s",
            f"pv = {w.pv:.1f} Pa = {w.pv / PA_PER_PSI:.4f} psia ; sigma = {w.sigma:.5f} N/m",
        ],
        citations=[CIT_WATER, CIT_DRC],
        warnings=warnings,
    )


def friction_factor(reynolds: float, relative_roughness: float = 0.0) -> CalcResult:
    """Darcy friction factor (dimensionless) from the Reynolds number and eps/D."""
    re, rr = float(reynolds), float(relative_roughness)
    f = friction_factor_si(re, rr)
    regime = "laminar" if re < 2300 else "transition" if re < 4000 else "turbulent"
    return CalcResult(
        calc="friction_factor",
        value=f,
        unit="",
        inputs={
            "reynolds": make_input(re, "", "prior_calc"),
            "relative_roughness": make_input(rr, "eps/D", "lookup", "pipe material roughness"),
        },
        formula=(
            "f = 8[(8/Re)^12 + (A+B)^-1.5]^(1/12) ; A = [2.457 ln(1/((7/Re)^0.9 + 0.27 eps/D))]^16 ; "
            "B = (37530/Re)^16"
        ),
        steps=[f"Re = {re:.6g} ({regime}) ; eps/D = {rr:.3g}", f"f = {f:.6f}"],
        citations=[CIT_CHURCHILL, CIT_DRC],
    )


def area_of(diameter_in: float) -> CalcResult:
    """Flow area (in^2 and ft^2) of a circular bore given its inside diameter (in)."""
    d_in = float(diameter_in)
    a_m2 = area_of_si(d_in * MM_PER_IN / 1000)
    a_in2 = a_m2 / (MM_PER_IN / 1000) ** 2
    return CalcResult(
        calc="area_of",
        value=a_in2,
        unit="in^2",
        inputs={"diameter": make_input(d_in, "in", "lookup", "pipe inside diameter")},
        formula="A = pi d^2 / 4",
        steps=[f"A = pi * {d_in:g}^2 / 4 = {a_in2:.4f} in^2 = {a_in2 / 144:.5f} ft^2"],
        citations=[CIT_DRC],
    )


def wave_speed(id_in: float, wall_in: float, modulus_gpa: float, temp_f: float = 68.0) -> CalcResult:
    """Korteweg pressure-wave speed (ft/s) in a water-filled elastic pipe."""
    w = water_si(f_to_c(float(temp_f)))
    a = wave_speed_si(w, float(id_in) * MM_PER_IN / 1000, float(wall_in) * MM_PER_IN / 1000, float(modulus_gpa))
    return CalcResult(
        calc="wave_speed",
        value=a / M_PER_FT,
        unit="ft/s",
        inputs={
            "id": make_input(float(id_in), "in", "lookup", "pipe spec"),
            "wall": make_input(float(wall_in), "in", "lookup", "pipe spec"),
            "modulus": make_input(float(modulus_gpa), "GPa", "lookup", "pipe material"),
            "water_temp": make_input(float(temp_f), "degF", "user"),
        },
        formula="a = sqrt((K/rho) / (1 + K D / (E e)))",
        steps=[
            f"K = {w.K:.3g} Pa ; rho = {w.rho:.3f} kg/m^3",
            f"a = {a:.1f} m/s = {a / M_PER_FT:.0f} ft/s",
        ],
        citations=[CIT_KORTEWEG, CIT_DRC],
    )
