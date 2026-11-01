"""Hand goldens for freestanding cantilever walls on footings.

Run: python -m pytest erpnext_enhancements/tests/test_structural_walls.py -q
"""

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering.engine.structural import (
    cantilever_wall,
    size_wall_footing,
)


def approx(x, rel=1e-9):
    return pytest.approx(x, rel=rel)


def test_raised_basin_wall_by_hand():
    # 3 ft stem above grade, 8 in thick, 2.5 ft of water on the heel side; footing 3 ft wide, 1 ft toe,
    # 12 in thick, bottom at 30 in (frost), so the stem runs 1.5 ft below grade to the footing's top.
    # Soil class 4 (μ 0.25, 2,000 psf).
    r = cantilever_wall(3.0, 8, 3.0, 1.0, 12, "water", 2.5, soil_class=4, footing_bottom_in=30)
    s = r.value["static"]
    t, heel, bury = 8 / 12, 3.0 - 1.0 - 8 / 12, 1.5
    assert r.value["stem_below_grade_ft"] == approx(bury)
    h_water = 62.4 * 2.5 ** 2 / 2  # 195.0 lb/ft, acting 1 + 1.5 + 2.5/3 ft above the footing bottom
    mo = h_water * (1 + bury + 2.5 / 3)
    v_rows = [(62.4 * 2.5 * heel, 1 + t + heel / 2), (120 * bury * heel, 1 + t + heel / 2),
              (150 * t * (3 + bury), 1 + t / 2), (150 * 3 * 1, 1.5)]
    sum_v = sum(f for f, _ in v_rows)
    mr = sum(f * a for f, a in v_rows)
    assert s["sum_h_lb"] == approx(h_water)
    assert s["mo_lb_ft"] == approx(mo)
    assert s["sum_v_lb"] == approx(sum_v)
    assert s["fs_overturning"] == approx(mr / mo)
    assert s["fs_sliding"] == approx(0.25 * sum_v / h_water)
    e = 3.0 / 2 - (mr - mo) / sum_v
    assert s["eccentricity_ft"] == approx(e)
    q = sum_v / 3.0 * (1 + 6 * abs(e) / 3.0) if abs(e) <= 0.5 else 2 * sum_v / (3 * (1.5 - abs(e)))
    assert s["bearing_psf"] == approx(q)
    assert r.value["frost"]["ok"] is True
    # sliding μ 0.25 × 1,378 / 195 = 1.77 now that the buried stem and the soil over the heel count
    assert s["fs_sliding"] == approx(0.25 * sum_v / 195.0) and r.value["concrete_cuft_per_ft"] == approx(t * 4.5 + 3)
    # the same wall on a 12 in footing at 12 in has no buried stem and slides (μ 0.25 × 958 / 195 = 1.23)
    shallow = cantilever_wall(3.0, 8, 3.0, 1.0, 12, "water", 2.5, soil_class=4, footing_bottom_in=12)
    assert shallow.value["static"]["fs_sliding"] == approx(0.25 * (62.4 * 2.5 * heel + 150 * t * 3 + 450) / 195.0)
    assert shallow.value["static"]["fs_sliding"] < 1.5 and shallow.status == "bad"


def test_soil_wall_with_plaza_surcharge_by_hand():
    # 4 ft of retained soil above the low grade + 250 psf plaza; footing bottom at 30 in, 12 in thick, so the
    # stem is 5.5 ft and the soil acts over Ht = 6.5 ft; EFP 45 pcf, Ka = 45/120 = 0.375
    r = cantilever_wall(4.0, 8, 5.0, 1.5, 12, "soil", surcharge_psf=250, soil_class=3, footing_bottom_in=30)
    s = r.value["static"]
    ht = 6.5
    assert s["sum_h_lb"] == approx(45 * ht ** 2 / 2 + 0.375 * 250 * ht)
    assert s["mo_lb_ft"] == approx(45 * ht ** 2 / 2 * ht / 3 + 0.375 * 250 * ht * ht / 2)
    heel = 5.0 - 1.5 - 8 / 12
    assert s["sum_v_lb"] == approx(120 * 5.5 * heel + 150 * 8 / 12 * 5.5 + 150 * 5.0 * 1.0)


def test_seismic_case_adds_westergaard():
    static = cantilever_wall(3.0, 8, 3.0, 1.0, 12, "water", 2.5, soil_class=4, footing_bottom_in=30)
    quake = cantilever_wall(3.0, 8, 3.0, 1.0, 12, "water", 2.5, soil_class=4, footing_bottom_in=30, sds=1.0)
    seis = quake.value["seismic"]
    kh = 1.0 / 2.5
    assert seis["sum_h_lb"] == approx(static.value["static"]["sum_h_lb"] + 7 / 12 * kh * 62.4 * 2.5 ** 2)
    assert seis["required_fs"] == 1.1


def test_shallow_footing_fails_frost():
    r = cantilever_wall(3.0, 8, 3.0, 1.0, 12, "water", 2.5, soil_class=4, footing_bottom_in=12)
    assert r.value["frost"]["ok"] is False and r.status == "bad"


def test_size_footing_finds_the_narrowest_passing_width():
    r = size_wall_footing(4.0, 8, "soil", surcharge_psf=250, soil_class=3)
    assert r.status == "ok"
    assert r.value["footing_bottom_in"] == 30.0  # frost depth
    width = r.value["footing_width_ft"]
    # one 6 in step narrower (toe a third of the width, in quarter feet) fails
    assert width > 2.0
    narrow = width - 0.5
    narrower = cantilever_wall(4.0, 8, narrow, math.floor(narrow / 3 * 4) / 4, 12, "soil", None, 250, 3, None, 30, 30)
    assert narrower.status == "bad"
    assert r.value["concrete_cuft_per_ft"] == approx(8 / 12 * (4.0 + 30 / 12 - 1.0) + width * 1.0)
    assert r.value["rebar_lb_per_ft"] > 0


def test_clay_cohesion_capped_at_half_the_dead_load():
    # class 5: 130 psf × B; IBC 1806.3.2 caps it at half the dead load
    r = cantilever_wall(3.0, 8, 6.0, 2.0, 12, "water", 2.5, soil_class=5, footing_bottom_in=12).value["static"]
    assert r["fs_sliding"] == approx(min(130 * 6.0, 0.5 * r["sum_v_lb"]) / r["sum_h_lb"])
