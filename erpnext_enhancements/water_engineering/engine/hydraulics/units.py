"""Exact unit constants for the boundary between the SI solver and US customary.

Everything inside ``hydraulics/`` is SI (m, s, Pa, m^3/s, kg/m^3), as in the
Digital Rain Curtain source it is ported from; that is what makes 1e-9 parity with
the JavaScript possible. The envelope functions convert at the edge with these
exact constants and nothing else. ``engine.constants.FT_PER_PSI`` (DOC-0049
F-Formulas F173, 2.308966) stays the workbook's fresh-water figure; the solver
uses rho(T) * g instead, so a head in feet and a pressure in psi stay consistent
at any water temperature.
"""

from __future__ import annotations

G = 9.80665  # m/s^2, standard gravity (CGPM 1901; DRC common.js G)
PATM = 101325.0  # Pa, standard sea-level atmosphere (DRC common.js PATM; see operating_point for altitude)

M_PER_FT = 0.3048  # exact (international foot, 1959)
MM_PER_IN = 25.4  # exact
M3_PER_GAL = 3.785411784e-3  # exact (US gallon = 231 in^3)
PA_PER_PSI = 6894.757  # NIST SP 811 (6894.757293...), to the precision the plan fixes
W_PER_HP = 745.69987158227  # mechanical horsepower (550 ft*lbf/s)
LPM_PER_GPM = 3.785411784  # L/min per US gpm (curve axes are L/min, as in DRC)

CIT_DRC = "Digital Rain Curtain src/hydro (ported under golden vectors; tests/golden/drc)"


def gpm_to_m3s(gpm: float) -> float:
    return gpm * M3_PER_GAL / 60.0


def m3s_to_gpm(q: float) -> float:
    return q * 60.0 / M3_PER_GAL


def ft_to_m(ft: float) -> float:
    return ft * M_PER_FT


def m_to_ft(m: float) -> float:
    return m / M_PER_FT


def psi_to_pa(psi: float) -> float:
    return psi * PA_PER_PSI


def pa_to_psi(pa: float) -> float:
    return pa / PA_PER_PSI


def in_to_mm(inches: float) -> float:
    return inches * MM_PER_IN


def mm_to_in(mm: float) -> float:
    return mm / MM_PER_IN


def f_to_c(temp_f: float) -> float:
    return (temp_f - 32.0) * 5.0 / 9.0


def c_to_f(temp_c: float) -> float:
    return temp_c * 9.0 / 5.0 + 32.0
