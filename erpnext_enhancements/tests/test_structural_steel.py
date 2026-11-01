"""Steel members (AISC 360 aids), fabrications and the rigging port.

HSS properties are computed from the tube geometry; the tests pin them to the
AISC Manual's published values (Table 1-12) within rounding. The rigging
port is pinned to the Digital Rain Curtain's JavaScript ``beamReactions``
(golden vectors generated with Node from src/rigging.js at DRC 737fa80).

Run: python -m pytest erpnext_enhancements/tests/test_structural_steel.py -q
"""

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering.engine.structural import (
    HSS_SIZES,
    beam_reactions,
    column_check,
    floor_rating,
    hss_section,
    member_check,
    pedestal_check,
    rig_plan,
    select_member,
    show_basin_floor,
    veneer_attachment,
    water_wall_frame,
    weir_lip_sag,
)


def approx(x, rel=1e-9):
    return pytest.approx(x, rel=rel)


@pytest.mark.parametrize("dims, aisc", [
    # AISC Manual Table 1-12: A in², Ix in⁴, Sx in³, Zx in³, lb/ft
    ((4, 4, 0.25), (3.37, 7.80, 3.90, 4.69, 12.21)),
    ((2, 2, 0.125), (0.840, 0.486, 0.486, 0.584, 3.05)),
    ((6, 6, 0.25), (5.24, 28.6, 9.54, 11.2, 19.02)),
    ((3, 3, 0.1875), (1.89, 2.46, 1.64, 1.97, 6.87)),
    ((6, 6, 0.375), (7.58, 39.5, 13.2, 15.8, 27.48)),
])
def test_hss_properties_match_the_aisc_manual(dims, aisc):
    s = hss_section(*dims)
    for got, want in zip((s["A"], s["Ix"], s["Sx"], s["Zx"], s["wt"]), aisc, strict=True):
        assert got == pytest.approx(want, rel=0.01)


def test_hss_table_is_sane():
    names = [hss_section(*d)["name"] for d in HSS_SIZES]
    assert "HSS4x4x1/4" in names and "HSS8x4x1/4" in names
    assert len(names) == len(set(names))


def test_member_check_by_hand():
    # HSS4x4x1/4, simple 10 ft span, 100 plf service + self weight
    s = hss_section(4, 4, 0.25)
    r = member_check("HSS4x4x1/4", 10, 100)
    v = r.value
    w = 100 + s["wt"]
    assert v["mu_lb_ft"] == approx(1.6 * w * 100 / 8)
    assert v["phi_mn_lb_ft"] == approx(0.9 * 50000 * s["Zx"] / 12)  # compact: b/t = (4 - 3t)/t = 14.2 < 27
    assert v["deflection_in"] == approx(5 * (w / 12) * 120 ** 4 / (384 * 29e6 * s["Ix"]))
    assert v["deflection_limit_in"] == approx(120 / 360)
    assert r.status == "ok"
    # cantilever 4 ft with 300 lb at the tip
    c = member_check("HSS4x4x1/4", 4, 0, 300, "cantilever").value
    assert c["mu_lb_ft"] == approx(1.6 * (s["wt"] * 16 / 2 + 300 * 4))
    assert c["deflection_limit_in"] == approx(48 / 180)


def test_select_member_is_the_lightest_passing():
    r = select_member(12, 150)
    assert r.status == "ok"
    chosen = r.value["weight_plf"]
    lighter = [hss_section(*d) for d in HSS_SIZES if hss_section(*d)["wt"] < chosen - 1e-9]
    assert all(member_check(sec, 12, 150).status == "bad" for sec in lighter)


def test_w_shape_uses_s():
    r = member_check("W10x22", 16, 400)
    assert r.value["phi_mn_lb_ft"] == approx(0.9 * 50000 * 23.2 / 12)
    assert any("braces the compression flange" in w for w in r.warnings)


def test_column_by_hand():
    s = hss_section(4, 4, 0.25)
    r = column_check("HSS4x4x1/4", 12, 2000, 20.0)
    v = r.value
    kl_r = 2.1 * 144 / s["rx"]
    fe = math.pi ** 2 * 29e6 / kl_r ** 2
    fcr = 0.658 ** (50000 / fe) * 50000 if kl_r <= 4.71 * math.sqrt(29e6 / 50000) else 0.877 * fe
    phi_pn = 0.9 * fcr * s["A"]
    pu = 1.6 * (2000 + s["wt"] * 12)
    mu = 1.6 * 20 * 144 / 2
    phi_mn = 0.9 * 50000 * s["Zx"] / 12
    ratio = pu / phi_pn
    inter = ratio + 8 / 9 * mu / phi_mn if ratio >= 0.2 else ratio / 2 + mu / phi_mn
    assert v["kl_r"] == approx(kl_r) and v["phi_pn_lb"] == approx(phi_pn) and v["interaction"] == approx(inter)


def test_water_wall_frame():
    r = water_wall_frame(10, 8, 20.0, 60.0)
    v = r.value
    assert v["posts"] == 2 and v["bay_ft"] == approx(8.0)  # 8 ft wide at 8 ft spacing
    assert r.status == "ok"
    post = hss_section(*next(d for d in HSS_SIZES if hss_section(*d)["name"] == v["post_section"]))
    header = hss_section(*next(d for d in HSS_SIZES if hss_section(*d)["name"] == v["header_section"]))
    assert v["steel_lb"] == approx(2 * 10 * post["wt"] + 8 * header["wt"])


def test_weir_lip_sag_by_hand():
    # 3/16 in 304 lip, 3 in wide on a 2 in flange, 1 in of water on it, continuous supports at 48 in
    t, hf, wl = 0.1875, 2.0, 3.0
    a1, y1, a2, y2 = wl * t, hf - t / 2, t * (hf - t), (hf - t) / 2
    yc = (a1 * y1 + a2 * y2) / (a1 + a2)
    i = wl * t ** 3 / 12 + a1 * (y1 - yc) ** 2 + t * (hf - t) ** 3 / 12 + a2 * (y2 - yc) ** 2
    w = 0.0361 * 1.0 * wl + 501 / 1728 * (a1 + a2)
    r = weir_lip_sag(48, t, hf, wl, 1.0)
    assert r.value["I_in4"] == approx(i)
    assert r.value["sag_in"] == approx(2.5 / 384 * w * 48 ** 4 / (28e6 * i))
    assert r.value["max_support_spacing_in"] == approx((0.0625 * 28e6 * i / (2.5 / 384 * w)) ** 0.25)
    assert r.status == "ok"
    # 20% past the reported support spacing sags 1.2⁴ ≈ 2.07 × the tolerance
    longer = weir_lip_sag(r.value["max_support_spacing_in"] * 1.2, t, hf, wl, 1.0)
    assert longer.value["sag_in"] == approx(0.0625 * 1.2 ** 4) and longer.status == "bad"
    # a scupper (cantilever) of the same section reaches the tolerance much sooner than a supported lip
    assert weir_lip_sag(48, t, hf, wl, 1.0, support="cantilever").value["max_support_spacing_in"] <         r.value["max_support_spacing_in"]


def test_veneer_by_hand():
    # 80 ft² of 1-1/4 in granite in SDC D: 168 × 1.25/12 = 17.5 psf; one anchor per 0.75 × 2.67 ft²
    r = veneer_attachment(80, 1.25, "granite", "anchored", "D")
    assert r.value["weight_psf"] == approx(17.5)
    assert r.value["anchors"] == math.ceil(80 / (0.75 * 2.67))  # 40
    assert veneer_attachment(80, 1.25, "granite", "adhered").status == "bad"  # over 15 psf
    assert veneer_attachment(80, 0.375, "porcelain", "adhered").status == "ok"


def test_pedestal_by_hand():
    # 2 × 2 × 3 ft concrete plinth with an 800 lb sculpture (cg 4.5 ft); 100 lb wind at 5 ft, on a slab
    r = pedestal_check(2, 2, 3, 150, 800, 4.5, 100, 5, None, "slab")
    v = r.value
    w = 150 * 12 + 800
    assert v["weight_lb"] == approx(w)
    assert v["fs_overturning"] == approx(0.9 * w * 1.0 / (100 * 5))
    assert v["fs_sliding"] == approx(0.6 * 0.9 * w / 100)
    assert v["bearing_psf"] == approx(w / 4 * (1 + 6 * (500 / w) / 2))


def test_beam_reactions_match_the_drc_javascript():
    golden = [
        ((1, 3, 10, 0), [15, 15]),
        ((1, 3, 10, 0.5), [20, 20]),
        ((2, 4, 12, 0), [18, 60, 18]),
        ((3, 2.5, 7, 0.3), [9.2512, 19.0988, 19.0988, 9.2512]),
        ((5, 1.2, 3.3, 0.12), [1.9841684210526314, 4.44978947368421, 3.862042105263157, 3.8620421052631575,
                               4.44978947368421, 1.9841684210526314]),
        ((4, 6, 1, 0), [2.357142857142857, 6.857142857142858, 5.571428571428571, 6.857142857142858,
                        2.357142857142857]),
    ]
    for args, want in golden:
        got = beam_reactions(*args).value
        assert got == pytest.approx(want, rel=1e-12, abs=1e-12)
    # hand: two equal spans, 3wL/8 and 5wL/4
    assert beam_reactions(2, 4, 12).value[1] == approx(5 * 12 * 4 / 4)


def test_rig_plan_towers_and_flown_by_hand():
    from erpnext_enhancements.water_engineering.engine.structural.rigging import (
        CORNER_LB,
        PLATE,
        TOWER_OUT_FT,
        TRUSSES,
    )

    box = TRUSSES["box12"]
    r = rig_plan(4.0, 8.0, 3, 2.6, 0.4, 8.0, "towers", "box12", 10.0, riser_lb=30.0)
    v = r.value
    span = 8 + 2 * TOWER_OUT_FT
    header_lb = box["lb_ft"] * span + 4.0 * 8.0
    tower_lb = box["lb_ft"] * (10 + box["size_ft"] / 2) + PLATE["lb"] + CORNER_LB
    assert v["header"]["lb"] == approx(header_lb)
    assert v["header"]["moment_lb_ft"] == approx(header_lb * span / 8)
    assert v["towers"][0]["lb"] == approx(header_lb / 2 + tower_lb)
    assert v["towers"][1]["lb"] == approx(header_lb / 2 + tower_lb + 30.0)  # the riser rides the right tower
    assert sum(h["lb"] for h in v["hangers"]) == approx(4.0 * (3 * 2.6 + 0.8))
    flown = rig_plan(4.0, 8.0, 3, 2.6, 0.4, 8.0, "flown", "box12", 10.0, picks=2, riser_lb=30.0).value
    assert flown["picks"][0]["lb"] == approx(header_lb / 2)
    assert flown["picks"][1]["lb"] == approx(header_lb / 2 + 15.0)
    assert flown["picks"][1]["hoist"] == "1/4 ton (500 lb)"


def test_floor_rating_and_show_basin():
    assert floor_rating(50).value == "office" and floor_rating(50.1).value == "assembly"
    assert floor_rating(120).status == "bad"
    # 10 × 4 ft show basin, 4 in of water: 62.4/3 = 20.8 psf + 5.12 psf pan = 25.92 psf
    r = show_basin_floor(10, 4, 1 / 3)
    assert r.value["psf"] == approx(62.4 / 3 + 5.12)
    assert r.value["rating"] == "office"


def test_pedestal_bearing_does_not_depend_on_input_order():
    a = pedestal_check(2, 4, 3, 150, 800, 4.5, 400, 5, None, "slab").value["bearing_psf"]
    b = pedestal_check(4, 2, 3, 150, 800, 4.5, 400, 5, None, "slab").value["bearing_psf"]
    assert a == approx(b)
    assert pedestal_check(2, 2, 3, 168, material="granite").value["concrete_cuft"] == 0.0
