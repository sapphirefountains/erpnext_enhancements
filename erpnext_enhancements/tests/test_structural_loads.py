"""Hand goldens for structural site loads: soil, surcharge, frost, seismic
(ASCE 7 site coefficients, ACI 350.3 hydrodynamic) and wind (ASCE 7 Ch. 29).

Plain pytest functions with their own `python -m pytest` step in ci.yml.

Run: python -m pytest erpnext_enhancements/tests/test_structural_loads.py -q
"""

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering.engine.structural import (
    DISCLAIMER,
    frost_check,
    hydrodynamic_wall_load,
    seismic_design_values,
    soil_properties,
    surcharge_load,
    wind_panel_load,
)


def approx(x, rel=1e-9):
    return pytest.approx(x, rel=rel)


def test_soil_presumptive_values():
    assert soil_properties(4).value["bearing_psf"] == 2000
    assert soil_properties(4).value["friction"] == 0.25
    unknown = soil_properties()
    assert unknown.value["soil_class"] == 5 and unknown.value["bearing_psf"] == 1500
    assert unknown.value["cohesion_psf"] == 130
    assert soil_properties(5, 3000).value["bearing_psf"] == 3000.0  # geotechnical value wins


def test_surcharge():
    assert surcharge_load("plaza").value == 250.0
    assert surcharge_load("pedestrian").value == 100.0
    # vehicle at a 7.5 ft wall: heq interpolates 5.0 -> 3.5 ft between 5 and 10 ft = 4.25 ft
    assert surcharge_load("vehicle", 7.5).value == approx(4.25 * 120)


def test_frost():
    assert frost_check(30).status == "ok"
    assert frost_check(18).status == "bad"
    assert frost_check(36, 42).status == "bad"


def test_seismic_wasatch_front_by_hand():
    # Ss 1.5, S1 0.55, Site Class D: Fa = 1.0; Fv halfway between 1.8 (0.5) and 1.7 (0.6) = 1.75
    v = seismic_design_values(1.5, 0.55, "D").value
    assert v["fa"] == approx(1.0) and v["fv"] == approx(1.75)
    assert v["sds"] == approx(2 / 3 * 1.0 * 1.5)  # 1.000
    assert v["sd1"] == approx(2 / 3 * 1.75 * 0.55)  # 0.6417
    assert v["sdc"] == "D"
    # low hazard: Ss 0.2, S1 0.08, Site B -> SDS 0.12 (A), SD1 0.0427 (A)
    assert seismic_design_values(0.2, 0.08, "B").value["sdc"] == "A"
    assert seismic_design_values(sds=0.4, sd1=0.15).value["sdc"] == "C"


def test_seismic_needs_inputs():
    r = seismic_design_values()
    assert r.status == "incomplete"
    assert any("Hazard Tool" in s for s in r.steps)


def test_hydrodynamic_by_hand():
    # 20 ft long, 3 ft of water, SDS 1.0, SD1 0.6, 8 in walls 3 ft tall, buried (Ri 3.0)
    r = hydrodynamic_wall_load(3.0, 20.0, 1.0, 0.6, 8.0, 3.0, buried=True)
    v = r.value
    ratio = 20 / 3
    wi = math.tanh(0.866 * ratio) / (0.866 * ratio)
    wc = 0.264 * ratio * math.tanh(3.16 / ratio)
    x = 3.16 / ratio
    hc = (1 - (math.cosh(x) - 1) / (x * math.sinh(x))) * 3
    lam = math.sqrt(3.16 * 32.2 * math.tanh(x))
    tc = 2 * math.pi * math.sqrt(20) / lam
    assert tc > 1.6 / 0.6  # Tc ≈ 4.19 s is past 1.6/Ts (Ts = 0.6 s), so the long-period branch applies
    cc = 2.4 * 1.0 / tc ** 2
    pi = 1.0 * wi * 62.4 * 3 * 20 / (2 * 3.0)
    pc = cc * wc * 62.4 * 3 * 20 / 2
    eps = min(0.0151 * ratio ** 2 - 0.1908 * ratio + 1.021, 1.0)
    pw = 1.0 * eps * 150 * (8 / 12) * 3 / 3.0
    mb = math.sqrt((pi * 0.375 * 3 + pw * 1.5) ** 2 + (pc * hc) ** 2)
    assert v["wi_ratio"] == approx(wi) and v["wc_ratio"] == approx(wc)
    assert v["hi_ft"] == approx(0.375 * 3) and v["hc_ft"] == approx(hc)
    assert v["tc_s"] == approx(tc) and v["cc"] == approx(cc)
    assert v["pi_lb_per_ft"] == approx(pi) and v["pc_lb_per_ft"] == approx(pc)
    assert v["base_moment_lb_ft_per_ft"] == approx(mb)
    assert v["static_moment_lb_ft_per_ft"] == approx(62.4 * 27 / 6)
    assert DISCLAIMER in r.warnings


def test_wind_on_a_water_wall_by_hand():
    # 10 ft tall, 8 ft wide wall on the ground, 115 mph, exposure C, sea level
    r = wind_panel_load(10, 8, 0, 115, "C", 1.0, 0.0)
    v = r.value
    kz = 2.01 * (15 / 900) ** (2 / 9.5)  # 0.849 (z below 15 ft uses 15 ft)
    qh = 0.00256 * kz * 1.0 * 0.85 * 1.0 * 115 ** 2
    cf = 1.55 + (1.45 - 1.55) * (0.8 - 0.5) / (1 - 0.5)  # B/s = 0.8 between 0.5 and 1
    assert v["kz"] == approx(kz)
    assert v["qh_psf"] == approx(qh)  # about 24.4 psf
    assert v["cf"] == approx(cf)
    assert v["pressure_psf"] == approx(qh * 0.85 * cf)
    assert v["force_lb"] == approx(qh * 0.85 * cf * 80)
    assert v["resultant_height_ft"] == approx(5.0)


def test_wind_porous_and_elevated_and_altitude():
    solid = wind_panel_load(10, 8, 0, 115).value["pressure_psf"]
    # 70% solid screen: (1 - 0.3^1.5) = 0.8357
    assert wind_panel_load(10, 8, 0, 115, solid_ratio=0.7).value["pressure_psf"] == approx(solid * (1 - 0.3 ** 1.5))
    assert wind_panel_load(10, 8, 4, 115).value["cf"] == 2.0  # clear of the ground: upper bound
    # Salt Lake City at 4,300 ft: Ke = e^(-0.0000362 × 4300) = 0.8558
    assert wind_panel_load(10, 8, 0, 115, elevation_ft=4300).value["ke"] == approx(math.exp(-0.0000362 * 4300))
