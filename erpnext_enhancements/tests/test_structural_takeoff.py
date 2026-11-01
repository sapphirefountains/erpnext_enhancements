"""Hand goldens for the structural takeoff (Fountain Designer phase S).

The golden basin is Nikolas's example: 10 ft × 6 ft inside, 18 in deep, a
6 in slab and 8 in walls, rim at grade. Every expected figure below is worked
by hand in the comment beside it, not read back from the engine.

Plain pytest functions: this suite has its own `python -m pytest` step in
ci.yml (``python -m unittest`` would collect nothing from it).

Run: python -m pytest erpnext_enhancements/tests/test_structural_takeoff.py -q
"""

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering.engine.data.rebar import REBAR, bar
from erpnext_enhancements.water_engineering.engine.structural import (
    DISCLAIMER,
    lap_length,
    stock_sticks,
    structural_takeoff,
)

GOLDEN = {"id": "b1", "label": "Pool", "shape": "rectangular", "length_in": 120, "width_in": 72,
          "depth_in": 18, "slab_in": 6, "wall_in": 8}

# Outside: 10 + 2(8/12) = 11.3333 ft by 6 + 2(8/12) = 7.3333 ft
FOOTPRINT = (10 + 16 / 12) * (6 + 16 / 12)  # 83.1111 ft²
CENTERLINE = 2 * (10 + 8 / 12) + 2 * (6 + 8 / 12)  # 34.6667 ft
SLAB_CUFT = FOOTPRINT * 0.5  # 41.5556 ft³
WALL_CUFT = CENTERLINE * (8 / 12) * 1.5  # 34.6667 ft³
CONCRETE_YD3 = (SLAB_CUFT + WALL_CUFT) / 27  # 2.8230 yd³


def approx(x, rel=1e-9):
    return pytest.approx(x, rel=rel)


@pytest.fixture(scope="module")
def golden():
    return structural_takeoff({"basins": [GOLDEN]})


def test_detail_uses_doc0119_wall_table(golden):
    d = golden["basins"][0]["detail"]
    # 8 in wall -> #4 at 16 in (DOC-0119 table); slab #4 at 18 in; one mat each
    assert (d["wall_in"], d["wall_bar"], d["wall_spacing_in"], d["wall_mats"]) == (8.0, 4, 16.0, 1)
    assert (d["slab_in"], d["slab_bar"], d["slab_spacing_in"], d["slab_mats"]) == (6.0, 4, 18.0, 1)


def test_concrete_golden(golden):
    c = golden["basins"][0]["quantities"]["concrete"]
    assert c["elements"]["slab"]["cuft"] == approx(41.55555555555556)
    assert c["elements"]["walls"]["cuft"] == approx(34.66666666666667)
    assert c["net_yd3"] == approx(CONCRETE_YD3)
    assert c["net_yd3"] == approx(2.823045267489712)
    assert c["with_waste_yd3"] == approx(CONCRETE_YD3 * 1.10)  # 3.1053
    assert c["order_yd3"] == 3.25  # rounded up to the quarter yard
    assert c["trucks"] == 1


def test_rebar_golden(golden):
    q = golden["basins"][0]["quantities"]
    marks = {m["mark"]: m for m in golden["rebar_schedule"]}
    # slab: bars run 11.3333 - 2(2/12) = 11.0 ft and 7.0 ft
    # long bars across 7.0 ft = 84 in: ceil(84/18)+1 = 6 ; short bars across 132 in: ceil(132/18)+1 = 9
    assert (marks["S1"]["qty"], marks["S1"]["length_ft"]) == (6, approx(11.0))
    assert (marks["S2"]["qty"], marks["S2"]["length_ft"]) == (9, approx(7.0))
    # wall verticals: ceil(416 in / 16) = 26 L-bars of (6 + 18 - 2 - 2) + 24 = 44 in
    assert (marks["W1"]["qty"], marks["W1"]["length_ft"]) == (26, approx(44 / 12))
    # horizontal rows: ceil((18 - 2)/16) + 1 = 2 ; per row 2 × 11.0 ft + 2 × 7.0 ft
    assert (marks["W2"]["qty"], marks["W3"]["qty"]) == (4, 4)
    # corner bars: 4 corners × 2 rows, legs one 24 in lap each = 4.0 ft
    assert (marks["W4"]["qty"], marks["W4"]["length_ft"]) == (8, approx(4.0))
    total_ft = 6 * 11 + 9 * 7 + 26 * 44 / 12 + 4 * 11 + 4 * 7 + 8 * 4  # 328.3333 ft
    assert q["rebar_ft"] == approx(total_ft)
    assert q["rebar_lb"] == approx(total_ft * 0.668)  # 219.33 lb
    # first-fit-decreasing into 20 ft bars, by hand: ten 11 ft cuts take a bar each and a 7 ft
    # cut each (2 ft left); the other three 7s fill bar 11 (two) and 12 (one); the 4 ft corner
    # bars go 1 into bar 11, 3 into bar 12, 4 into bar 13; the 26 verticals (3.67 ft): one into
    # bar 13, then five to a bar = 5 more bars -> 18 bars of 20 ft
    sticks = q["rebar_by_size"]["#4"]["sticks"]
    assert sticks["20"]["count"] == 18
    assert q["rebar_purchase_lb"] == approx(18 * 20 * 0.668)  # 240.48 lb
    assert sticks["40"]["count"] == 9


def test_gravel_and_excavation_golden(golden):
    q = golden["basins"][0]["quantities"]
    g = q["gravel"]
    assert g["yd3"] == approx(FOOTPRINT / 27)  # 12 in under the footprint: 3.0782 yd³
    assert g["tons"] == approx(FOOTPRINT / 27 * 1.4 * 1.1)  # 4.7404 t
    ex = q["excavation"]
    # dig to 18 + 6 + 12 = 36 in; footprint + 2 ft each side; vertical (under 5 ft)
    assert ex["depth_ft"] == approx(3.0)
    bottom = (10 + 16 / 12 + 4) * (6 + 16 / 12 + 4)  # 173.7778 ft²
    assert ex["yd3"] == approx(bottom * 3 / 27)  # 19.3086 yd³
    backfill = bottom * 3 - FOOTPRINT * 2.0 - FOOTPRINT * 1.0  # shell below grade + gravel
    assert ex["backfill_yd3"] == approx(backfill / 27)  # 10.0741 yd³
    assert ex["haul_yd3"] == approx((bottom * 3 - backfill) * 1.25 / 27)


def test_uplift_golden_two_ft_of_groundwater():
    # water table at grade = 2 ft of head on the slab bottom (18 in + 6 in slab)
    t = structural_takeoff({"basins": [dict(GOLDEN, groundwater_depth_ft=0.0)]})
    up = t["basins"][0]["results"]["uplift"]
    v = up["value"]
    assert v["head_ft"] == approx(2.0)
    assert v["uplift_lb"] == approx(62.4 * FOOTPRINT * 2.0)  # 10,372.3 lb
    assert v["dead_weight_lb"] == approx((SLAB_CUFT + WALL_CUFT) * 150)  # 11,433.3 lb
    assert v["factor_of_safety"] == approx((SLAB_CUFT + WALL_CUFT) * 150 / (62.4 * FOOTPRINT * 2.0))  # 1.102
    assert up["status"] == "warn"  # 1.0 <= FS < 1.3
    codes = {c["code"]: c for c in t["checks"]}
    assert codes["STRUCT_UPLIFT"]["severity"] == "warning"
    assert codes["STRUCT_UPLIFT"]["key"] == "STRUCT_UPLIFT|Pool"
    assert codes["STRUCT_UPLIFT"]["section"] == "basin"


def test_uplift_water_table_two_ft_down_is_no_uplift():
    t = structural_takeoff({"basins": [dict(GOLDEN, groundwater_depth_ft=2.0)]})
    up = t["basins"][0]["results"]["uplift"]
    assert up["value"]["head_ft"] == 0.0
    assert up["status"] == "ok"
    assert "STRUCT_UPLIFT" not in {c["code"] for c in t["checks"]}


def test_uplift_blocker_below_fs_one():
    # 1 ft of extra head over the at-grade case -> FS well below 1
    t = structural_takeoff({"basins": [dict(GOLDEN, groundwater_depth_ft=-1.0)]})
    assert {c["code"]: c["severity"] for c in t["checks"]}["STRUCT_UPLIFT"] == "blocker"


def test_other_quantities_golden(golden):
    q = golden["basins"][0]["quantities"]
    assert q["formwork_sqft"] == approx(32 * 1.5 + (2 * (10 + 16 / 12) + 2 * (6 + 16 / 12)) * 2.0)  # 122.67
    assert q["waterstop_ft"] == approx(CENTERLINE)  # wall-to-slab cold joint, no movement joints
    assert q["sealant_ft"] == approx(32.0)  # inside floor-to-wall cove
    assert q["coating_sqft"] == approx(32 * 1.5 + 60)  # inside walls + floor = 108
    assert q["chairs"] == 6  # ceil(83.11 / 16)
    geo = golden["basins"][0]["geometry"]
    assert geo["water_volume_gal"] == approx(60 * (16 / 12) * 7.480519)  # 2 in freeboard
    assert geo["corner_count"] == 4


def test_bom_lines_shape_and_quantities(golden):
    bom = {line["key"]: line for line in golden["bom"]}
    for line in golden["bom"]:
        assert set(line) >= {"section", "key", "description", "qty", "uom", "item_hint"}
        assert line["section"] == "Structural"
        assert line["qty"] > 0
    assert bom["structural.concrete"]["qty"] == 3.25
    assert bom["structural.concrete"]["uom"] == "Cubic Yard"
    assert bom["structural.rebar.#4.lb"]["qty"] == 240.48
    assert bom["structural.rebar.#4.bars20"]["qty"] == 18
    assert bom["structural.rebar.#4.bars20"]["priced"] is False
    assert bom["structural.gravel"]["qty"] == 9480.82  # 4.7404 short tons × 2,000: ERPNext has no "Ton" UOM
    assert bom["structural.gravel"]["uom"] == "Pound"
    assert {line["uom"] for line in golden["bom"]} <= {"Cubic Yard", "Pound", "Nos", "Foot", "Square Foot"}
    assert bom["structural.waterstop"]["uom"] == "Foot"
    assert bom["structural.coating"]["qty"] == 108.0
    assert "structural.anchors" not in bom  # no equipment in this model


def test_every_result_carries_the_disclaimer(golden):
    assert golden["disclaimer"] == DISCLAIMER
    for name, res in golden["basins"][0]["results"].items():
        assert DISCLAIMER in res["warnings"], name
        assert res["citations"], name


def test_penetrations_and_anchors_from_network():
    model = {
        "basins": [GOLDEN],
        "network": {
            "nodes": [{"id": "src", "kind": "basin", "basin": "b1"}, {"id": "p1", "kind": "pump"},
                      {"id": "t1", "kind": "terminal"}],
            "pumps": [{"id": "p1", "generic_class": "end-suction"}, {"id": "p2", "generic_class": "submersible"}],
            "segments": [{"id": "s1", "from": "src", "to": "p1", "nominal_size": '3"'},
                         {"id": "s2", "from": "p1", "to": "t1", "nominal_size": '2"'},
                         {"id": "s3", "from": "t1", "to": "src", "nominal_size": '2"'}],
        },
        "equipment": [{"slot": "filter", "qty": 1}, {"slot": "pump", "qty": 1}, {"slot": "nozzle", "qty": 4}],
    }
    t = structural_takeoff(model)
    pen = t["basins"][0]["quantities"]["penetrations"]
    assert pen["count"] == 2 and pen["by_size"] == {'3"': 1, '2"': 1}
    bom = {line["key"]: line for line in t["bom"]}
    assert bom["structural.link_seal.3in"]["qty"] == 1
    # one end-suction pump + one filter (the equipment "pump" row is not double counted) × 4
    assert bom["structural.anchors"]["qty"] == 8


def test_lap_and_development_lengths():
    # ld = 60000 / (25 √4500) × 0.5 = 17.89 in; Class B 1.3 ld = 23.26 -> 24 in
    assert lap_length(4).value == 24.0
    assert lap_length(4, {"lap_rule": "40db"}).value == 20.0
    # f'c 4000: ld = 18.97, 1.3 ld = 24.67 -> 25 in
    assert lap_length(4, {"fc_psi": 4000}).value == 25.0
    # #7 uses the /20 case: 60000 / (20 √4500) × 0.875 = 39.13; × 1.3 = 50.87 -> 51
    assert lap_length(7).value == 51.0


def test_rebar_table_astm_a615():
    assert bar("#3")["lb_per_ft"] == 0.376
    assert bar(4)["lb_per_ft"] == 0.668 and bar(4)["area_in2"] == 0.20
    assert bar("8")["diameter_in"] == 1.0
    for size, row in REBAR.items():
        # unit weight = steel 490 lb/ft³ × nominal area, within the table's rounding
        assert row["lb_per_ft"] == pytest.approx(row["area_in2"] / 144 * 490, rel=0.03)
        assert row["diameter_in"] == size / 8
    with pytest.raises(ValueError):
        bar(9)


def test_stock_sticks_ffd():
    assert stock_sticks([11, 11, 7, 7, 4], 20).value == 3
    assert stock_sticks([20, 20], 20).value == 2
    assert stock_sticks([10] * 4, 40).value == 1
    assert stock_sticks([], 20).value == 0


def test_rules_override_and_none_fallback():
    t = structural_takeoff({"basins": [GOLDEN]}, {"waste_factor": 0.0, "gravel_in": None})
    c = t["basins"][0]["quantities"]["concrete"]
    assert c["with_waste_yd3"] == approx(CONCRETE_YD3)
    assert t["basins"][0]["quantities"]["gravel"]["depth_in"] == 12.0  # None falls back to DOC-0119


def test_incomplete_basin_is_an_info_check():
    t = structural_takeoff({"basins": [{"id": "b9", "label": "Draft", "shape": "rectangular", "depth_in": 18}]})
    assert t["basins"][0]["complete"] is False
    assert [c["code"] for c in t["checks"]] == ["STRUCT_INCOMPLETE"]
    assert t["bom"] == []


def test_empty_model():
    t = structural_takeoff({})
    assert t["basins"] == [] and t["bom"] == [] and t["totals"]["trucks"] == 0
    assert math.isclose(t["totals"]["concrete_net_yd3"], 0.0)


def test_no_infinity_reaches_the_json():
    import json

    # a 6 in wall 12 ft tall: no bar area carries the moment
    t = structural_takeoff({"basins": [{"id": "t", "label": "Tall", "shape": "rectangular", "length_in": 240,
                                        "width_in": 240, "depth_in": 144, "wall_in": 6}]})
    json.dumps(t, allow_nan=False)
    wall = t["basins"][0]["results"]["wall_water"]["value"]
    assert wall["as_required_in2_per_ft"] is None and wall["flexure_ok"] is False
    detail = {c["code"]: c for c in t["checks"]}["STRUCT_WALL_WATER"]["detail"]
    assert "section too thin" in detail and "inf" not in detail


def test_vault_coating_has_its_own_line_and_keys_stay_unique():
    twin = dict(GOLDEN, id="b2")
    vault = {"id": "v", "label": "Vault", "kind": "vault", "shape": "rectangular", "length_in": 96, "width_in": 72,
             "depth_in": 84}
    t = structural_takeoff({"basins": [GOLDEN, twin, vault]})
    bom = {line["key"]: line for line in t["bom"]}
    assert "inside walls" in bom["structural.coating"]["description"]
    assert "outside faces" in bom["structural.coating.exterior"]["description"]
    keys = [c["key"] for c in t["checks"]]
    assert len(keys) == len(set(keys))
    assert "STRUCT_ACI350_ST|Pool (2)" in keys
