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
    # 3 ft stem, 8 in thick, 2.5 ft of water on the heel side; footing 3 ft wide, 1 ft toe, 12 in thick;
    # soil class 4 (μ 0.25, 2,000 psf); footing bottom at 30 in (frost)
    r = cantilever_wall(3.0, 8, 3.0, 1.0, 12, "water", 2.5, soil_class=4, footing_bottom_in=30)
    s = r.value["static"]
    t, heel = 8 / 12, 3.0 - 1.0 - 8 / 12
    h_water = 62.4 * 2.5 ** 2 / 2  # 195.0 lb/ft at 1 + 2.5/3 ft
    mo = h_water * (1 + 2.5 / 3)
    v_rows = [(62.4 * 2.5 * heel, 1 + t + heel / 2), (150 * t * 3, 1 + t / 2), (150 * 3 * 1, 1.5)]
    sum_v = sum(f for f, _ in v_rows)
    mr = sum(f * a for f, a in v_rows)
    assert s["sum_h_lb"] == approx(h_water)
    assert s["mo_lb_ft"] == approx(mo)
    assert s["sum_v_lb"] == approx(sum_v)
    assert s["fs_overturning"] == approx(mr / mo)
    assert s["fs_sliding"] == approx(0.25 * sum_v / h_water)
    e = 3.0 / 2 - (mr - mo) / sum_v
    assert s["eccentricity_ft"] == approx(e)
    assert s["bearing_psf"] == approx(sum_v / 3.0 * (1 + 6 * abs(e) / 3.0))
    assert r.value["frost"]["ok"] is True
    # overturning 4.36 passes, but sliding μ 0.25 × 958 / 195 = 1.23 < 1.5: the wall slides
    assert s["fs_overturning"] > 1.5 and s["fs_sliding"] < 1.5
    assert r.status == "bad"
    # on sandy gravel (class 3, μ 0.35): 0.35 × 958 / 195 = 1.72 and it stands
    assert cantilever_wall(3.0, 8, 3.0, 1.0, 12, "water", 2.5, soil_class=3, footing_bottom_in=30).status == "ok"


def test_soil_wall_with_plaza_surcharge_by_hand():
    # 4 ft stem retaining soil + 250 psf plaza; EFP 45 pcf, Ka = 45/120 = 0.375; Ht = 5 ft
    r = cantilever_wall(4.0, 8, 5.0, 1.5, 12, "soil", surcharge_psf=250, soil_class=3, footing_bottom_in=30)
    s = r.value["static"]
    ht = 5.0
    assert s["sum_h_lb"] == approx(45 * ht ** 2 / 2 + 0.375 * 250 * ht)
    assert s["mo_lb_ft"] == approx(45 * ht ** 2 / 2 * ht / 3 + 0.375 * 250 * ht * ht / 2)


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
