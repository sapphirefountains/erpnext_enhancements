"""engine/hydraulics/water.py against DRC common.js (tests/golden/drc/water.json, 1e-12), plus the
JS-semantics helpers and the envelopes.

Bench-free pytest suite: python -m pytest erpnext_enhancements/tests/test_hydraulics_water.py -q
"""

import math

from erpnext_enhancements.tests.drc_golden import REL, close, golden, num
from erpnext_enhancements.water_engineering.engine.envelope import CalcResult
from erpnext_enhancements.water_engineering.engine.hydraulics import _js, units
from erpnext_enhancements.water_engineering.engine.hydraulics.water import (
    area_of,
    area_of_si,
    friction_factor,
    friction_factor_si,
    water,
    water_si,
    wave_speed,
    wave_speed_si,
)

G = golden("water")


def test_header_pins_the_reference():
    assert len(G["drc_sha"]) >= 40 and G["node"].startswith("v")
    assert G["G"] == units.G and G["PATM"] == units.PATM


def test_water_matches_drc():
    assert len(G["water"]) >= 12
    for row in G["water"]:
        w = water_si(num(row["T"])).to_dict()
        for k, v in row["out"].items():
            close(w[k], v, REL, f"water({row['T']}).{k}")


def test_friction_factor_matches_drc():
    assert len(G["friction"]) > 80
    for row in G["friction"]:
        close(friction_factor_si(num(row["Re"]), num(row["rr"])), row["f"], REL, f"f(Re={row['Re']}, rr={row['rr']})")


def test_area_and_wave_speed_match_drc():
    for row in G["areas"]:
        close(area_of_si(row["d"]), row["a"], REL, f"area({row['d']})")
    for row in G["waves"]:
        a = wave_speed_si(water_si(row["T"]), row["idM"], row["wallM"], row["eGPa"])
        close(a, row["a"], REL, f"wave({row})")


def test_friction_factor_hand_values():
    # Laminar: Churchill collapses to 64/Re.
    assert abs(friction_factor_si(1000, 0) - 0.064) < 1e-4
    # Smooth turbulent Re 1e5: Blasius 0.316 Re^-0.25 = 0.01777 (Churchill within 2%).
    assert abs(friction_factor_si(1e5, 0) - 0.01777) / 0.01777 < 0.02
    # Fully rough eps/D 0.01: von Karman 1/sqrt(f) = -2 log10(eps/D / 3.7) -> 0.0379.
    assert abs(friction_factor_si(1e8, 0.01) - 0.0379) / 0.0379 < 0.01


def test_js_helpers():
    assert math.isnan(_js.jmax(1.0, math.nan)) and math.isnan(_js.jmin(math.nan, 1.0))
    assert _js.or_(0, 5) == 5 and _js.or_(math.nan, 5) == 5 and _js.or_("", 5) == 5 and _js.or_(2, 5) == 2
    assert not _js.is_number(True) and not _js.is_number("3") and not _js.is_number(math.inf) and _js.is_number(3)
    # toFixed ties away from zero on the exact binary value; Python's round() would give 0.12 / 2.
    assert _js.to_fixed(0.125, 2) == 0.13 and _js.to_fixed(2.5, 0) == 3.0 and _js.to_fixed(-2.5, 0) == -3.0
    assert _js.to_fixed(1.005, 2) == 1.0  # 1.005 is 1.00499999999999989... in binary
    assert _js.jpow(10.0, 400) == math.inf and _js.jpow(0.0, -1) == math.inf and math.isnan(_js.jpow(-8.0, 1 / 3))


def test_units_are_exact():
    assert units.gpm_to_m3s(1) == 3.785411784e-3 / 60
    assert abs(units.m3s_to_gpm(units.gpm_to_m3s(123.4)) - 123.4) < 1e-12
    assert units.psi_to_pa(1) == 6894.757 and units.ft_to_m(1) == 0.3048
    assert abs(units.f_to_c(68) - 20) < 1e-12


def test_envelopes_show_their_work():
    for r in (water(68), friction_factor(1e5, 1e-5), area_of(2.067), wave_speed(2.067, 0.154, 2.9)):
        assert isinstance(r, CalcResult)
        assert r.formula and r.steps and r.citations
    w = water(68).value
    assert abs(w["density_lb_ft3"] - 62.32) < 0.01  # 998.2 kg/m^3
    assert abs(w["vapor_pressure_psia"] - 0.339) < 0.002  # 20 C steam table 2.339 kPa
    assert abs(area_of(2.067).value - math.pi * 2.067**2 / 4) < 1e-9
    # PVC 2" Sch 40 at 68 F, E = 2.9 GPa: sqrt(K/rho) = 1485 m/s, K D/(E e) = 10.2, a = 444 m/s = 1,457 ft/s.
    assert 1440 < wave_speed(2.067, 0.154, 2.9).value < 1470
    assert water(250).warnings
