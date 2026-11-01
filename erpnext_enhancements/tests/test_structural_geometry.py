"""Geometry round trips and the sizing checks of the structural package
(Fountain Designer phase S). Hand values in the comments.

Plain pytest functions with their own `python -m pytest` step in ci.yml.

Run: python -m pytest erpnext_enhancements/tests/test_structural_geometry.py -q
"""

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering.engine.structural import (
    basin_shell,
    basin_spec,
    cover_check,
    detail_for_wall,
    floor_load,
    hydrostatic_pressure,
    select_thickness,
    shrinkage_temperature_check,
    structural_takeoff,
    wall_bending_check,
)


def approx(x, rel=1e-9):
    return pytest.approx(x, rel=rel)


def shell(basin, slab=8, wall=8):
    return basin_shell(basin, slab, wall).value


@pytest.mark.parametrize("basin", [
    {"shape": "rectangular", "length_in": 120, "width_in": 72, "depth_in": 18},
    {"shape": "cylindrical", "diameter_in": 144, "depth_in": 24},
    {"shape": "freeform", "area_sqft": 50.0, "perimeter_ft": 30.0, "depth_in": 20},
])
def test_round_trip_footprint_and_walls(basin):
    s = shell(basin, 6, 8)
    t = 8 / 12
    # footprint − inside = wall plan = thickness × centerline, for every shape
    assert s["footprint_sqft"] - s["inside_area_sqft"] == approx(s["wall_plan_area_sqft"])
    assert s["wall_plan_area_sqft"] == approx(t * s["wall_centerline_ft"])
    # volumes rebuild from areas
    assert s["concrete_cuft"]["walls"] == approx(s["wall_plan_area_sqft"] * s["wall_height_ft"])
    assert s["concrete_cuft"]["slab"] == approx(s["footprint_sqft"] * 0.5)
    assert s["concrete_total_cuft"] == approx(sum(s["concrete_cuft"].values()))
    # water: gallons back to cubic feet, and depth × area
    assert s["water_volume_gal"] / 7.480519 == approx(s["water_volume_cuft"])
    assert s["water_depth_ft"] * s["inside_area_sqft"] == approx(s["water_volume_cuft"])
    assert s["wall_area_inside_sqft"] == approx(s["inside_perimeter_ft"] * s["wall_height_ft"])


def test_freeform_circle_equals_cylinder():
    r = 6.0
    cyl = shell({"shape": "cylindrical", "diameter_in": 2 * r * 12, "depth_in": 24})
    free = shell({"shape": "freeform", "area_sqft": math.pi * r * r, "perimeter_ft": 2 * math.pi * r,
                  "depth_in": 24})
    for key in ("footprint_sqft", "wall_centerline_ft", "outside_perimeter_ft", "concrete_total_cuft",
                "water_volume_cuft"):
        assert free[key] == approx(cyl[key]), key


def test_cylinder_by_hand():
    # D = 12 ft, t = 8 in: footprint π(13.3333)²/4 = 139.626 ft², centerline π × 12.6667 = 39.794 ft
    s = shell({"shape": "cylindrical", "diameter_in": 144, "depth_in": 24})
    assert s["footprint_sqft"] == approx(math.pi * (12 + 16 / 12) ** 2 / 4)
    assert s["wall_centerline_ft"] == approx(math.pi * (12 + 8 / 12))
    assert s["corner_count"] == 0


def test_stepped_floor_water_and_risers():
    # 20 × 10 ft, half at 12 in, half at 36 in, 2 in freeboard:
    # water = 100 × 10/12 + 100 × 34/12 = 366.67 ft³ ; walls at 36 in ; one riser 10 ft × 2 ft × 8 in
    s = shell({"shape": "rectangular", "length_in": 240, "width_in": 120, "depth_in": 18,
               "steps": [{"depth_in": 12, "area_fraction": 0.5}, {"depth_in": 36, "area_fraction": 0.5}]})
    assert s["water_volume_cuft"] == approx(100 * 10 / 12 + 100 * 34 / 12)
    assert s["wall_height_ft"] == approx(3.0)
    assert s["concrete_cuft"]["risers"] == approx(10 * 2 * 8 / 12)


def test_grade_levels_by_placement():
    base = {"shape": "rectangular", "length_in": 120, "width_in": 72, "depth_in": 18}
    in_grade = shell(base, 6)
    assert (in_grade["slab_bottom_depth_ft"], in_grade["wall_below_grade_ft"]) == (approx(2.0), approx(1.5))
    raised = shell(dict(base, placement="raised"), 6)
    assert (raised["slab_bottom_depth_ft"], raised["wall_above_grade_ft"]) == (approx(0.5), approx(1.5))
    partly = shell(dict(base, rim_above_grade_in=6), 6)
    assert partly["wall_below_grade_ft"] == approx(1.0)
    roof = shell(dict(base, placement="on_structure"), 6)
    assert roof["rim_above_grade_ft"] is None


def test_vault_lid_and_no_water():
    s = shell({"kind": "vault", "shape": "rectangular", "length_in": 96, "width_in": 72, "depth_in": 84})
    # lid = footprint (9.3333 × 7.3333) − 3 × 3 ft hatch, 8 in thick
    assert s["concrete_cuft"]["lid"] == approx((28 / 3 * 22 / 3 - 9) * 8 / 12)
    assert s["water_volume_cuft"] == 0.0
    # lid top at grade: slab bottom = 8 in lid + 84 in + 8 in slab = 100 in
    assert s["slab_bottom_depth_ft"] == approx(100 / 12)


def test_structure_block_wins_over_top_level():
    spec = basin_spec({"id": "b", "shape": "rect", "length_in": 120, "width_in": 72, "depth_in": 18,
                       "wall_in": 8, "structure": {"wall_in": 10}}, {"groundwater_depth_ft": 3})
    assert spec["wall_in"] == 10 and spec["groundwater_depth_ft"] == 3


def test_incomplete_shell():
    r = basin_shell({"shape": "rectangular", "length_in": 0, "width_in": 72, "depth_in": 18}, 8, 8)
    assert r.status == "incomplete" and r.value is None


def test_hydrostatic_by_hand():
    v = hydrostatic_pressure(3.0).value
    assert v["base_pressure_psf"] == approx(187.2)  # 62.4 × 3
    assert v["resultant_lb_per_ft"] == approx(280.8)  # 62.4 × 9 / 2
    assert v["base_moment_lb_ft_per_ft"] == approx(280.8)  # 62.4 × 27 / 6


def test_wall_bending_by_hand():
    # 4 ft of water on an 8 in wall, #4 at 16 in, one centered mat: d = 4 in
    # M = 62.4 × 64 / 6 = 665.6 ; Mu = 1.6 × 1.3 × 665.6 × 12 = 16,613.4 lb·in/ft
    # Rn = 16613.4 / (0.9 × 12 × 16) = 96.14 psi ; ρ = 0.85·4500/60000·(1 − √(1 − 2·96.14/3825)) = 0.001630
    r = wall_bending_check(4.0, 8, 4, 16, 1)
    v = r.value
    assert v["d_in"] == 4.0
    assert v["mu_lb_in_per_ft"] == approx(1.6 * 1.3 * 62.4 * 64 / 6 * 12)
    rn = v["mu_lb_in_per_ft"] / (0.9 * 12 * 16)
    rho = 0.85 * 4500 / 60000 * (1 - math.sqrt(1 - 2 * rn / (0.85 * 4500)))
    assert v["as_required_in2_per_ft"] == approx(rho * 12 * 4)
    assert v["as_provided_in2_per_ft"] == approx(0.15)  # 0.20 × 12 / 16
    assert r.status == "ok"
    # propped (vault under a lid): M = γh³/15
    p = wall_bending_check(4.0, 8, 4, 16, 1, case="soil", top="pinned").value
    assert p["mu_lb_in_per_ft"] == approx(1.6 * 1.3 * 60 * 64 / 15 * 12)


def test_select_thickness_steps_up_for_tall_walls():
    # 12 ft wall: ACI 350 pushes to 12 in, beyond DOC-0119's table -> flagged
    spec = basin_spec({"shape": "rectangular", "length_in": 480, "width_in": 360, "depth_in": 144})
    r = select_thickness(spec)
    assert r.value["wall_in"] == 12.0 and r.value["wall_mats"] == 2
    assert r.value["beyond_standard_details"] is True
    # an 18 in basin keeps the 8 in detail
    small = select_thickness(basin_spec({"shape": "rectangular", "length_in": 120, "width_in": 72,
                                         "depth_in": 18}))
    assert small.value["wall_in"] == 8.0 and not small.value["beyond_standard_details"]


def test_detail_table_lookup():
    assert detail_for_wall(8)["spacing_in"] == 16.0
    assert detail_for_wall(9)["spacing_in"] == 16.0
    assert detail_for_wall(10)["spacing_in"] == 12.0
    assert detail_for_wall(6)["spacing_in"] == 18.0  # below the table: #4 at 18 in
    assert detail_for_wall(12)["mats"] == 2 and detail_for_wall(10)["mats"] == 1


def test_cover_check_rejects_a_four_inch_stem_wall():
    # 2 in cover each face + two crossing #4 = 5 in
    assert cover_check(4, 4, 1).status == "bad"
    assert cover_check(5, 4, 1).status == "ok"


def test_aci350_shrinkage_check():
    # 8 in wall, joints under 20 ft: 0.003 × 12 × 8 = 0.288 in²/ft; #4 at 16 gives 0.15
    r = shrinkage_temperature_check(8, 4, 16, 1, 11.3)
    assert r.value["required_in2_per_ft"] == approx(0.288) and r.status == "warn"
    assert shrinkage_temperature_check(8, 4, 12, 2, 11.3).status == "ok"  # 0.40 in²/ft at 12 in
    assert shrinkage_temperature_check(8, 5, 18, 2, 11.3).status == "warn"  # enough steel, spacing > 12 in
    assert shrinkage_temperature_check(6, 4, 12, 2, 25).value["ratio"] == 0.004


def test_floor_load_on_a_roof():
    # 10 × 10 ft, 12 in deep, 8 in slab and walls on a 100 psf roof
    t = structural_takeoff({"basins": [{"id": "r", "label": "Roof", "shape": "rectangular", "length_in": 120,
                                        "width_in": 120, "depth_in": 12, "placement": "on_structure",
                                        "floor_capacity_psf": 100}]})
    b = t["basins"][0]
    s = b["geometry"]
    fl = floor_load(s).value
    expected = (s["concrete_total_cuft"] * 150 + 100 * 1.0 * 62.4) / s["footprint_sqft"]
    assert fl["pressure_psf"] == approx(expected)
    assert {c["code"]: c["severity"] for c in t["checks"]}["STRUCT_FLOOR_LOAD"] == "blocker"
    assert b["quantities"]["excavation"]["yd3"] == 0 and b["quantities"]["gravel"]["tons"] == 0


def test_deep_excavation_is_sloped():
    # 8 ft deep basin: dig 8 + 8/12 + 1 = 9.667 ft, sloped 1.5:1
    t = structural_takeoff({"basins": [{"id": "d", "label": "Deep", "shape": "rectangular", "length_in": 240,
                                        "width_in": 240, "depth_in": 96}]})
    ex = t["basins"][0]["quantities"]["excavation"]
    assert ex["side_slope_h_per_v"] == 1.5
    w = t["basins"][0]["geometry"]["outside_length_ft"] + 4
    D, k = ex["depth_ft"], 1.5
    by_hand = w * w * D + 4 * w * k * D * D / 2 + 4 * k * k * D ** 3 / 3
    assert ex["yd3"] == approx(by_hand / 27)
    assert "STRUCT_EXCAVATION_DEPTH" in {c["code"] for c in t["checks"]}


def test_blank_form_values_read_as_missing():
    t = structural_takeoff({"basins": [{"id": "b", "label": "B", "shape": "rectangular", "length_in": 120,
                                        "width_in": 72, "depth_in": 18, "groundwater_depth_ft": "",
                                        "corner_count": "", "rim_above_grade_in": " ", "freeboard_in": ""}],
                            "site": {"groundwater_depth_ft": ""}})
    b = t["basins"][0]
    assert b["results"]["uplift"]["status"] == "n/a"
    assert b["geometry"]["corner_count"] == 4  # blank falls back to the rectangle's corners
    assert b["geometry"]["rim_above_grade_ft"] == 0.0
    assert b["geometry"]["freeboard_in"] == 2.0


def test_groundwater_adds_to_the_soil_case():
    # 6 ft wall, water table 2 ft below grade: 4 ft of water pressure on top of 60 pcf soil
    # M = (60 × 216 + 62.4 × 64) / 6 = 2,825.6 lb·ft/ft
    dry = wall_bending_check(6.0, 8, 4, 16, 1, case="soil").value
    wet = wall_bending_check(6.0, 8, 4, 16, 1, case="soil", groundwater_ft=4.0).value
    assert wet["mu_lb_in_per_ft"] == approx(1.6 * 1.3 * (60 * 216 + 62.4 * 64) / 6 * 12)
    assert wet["mu_lb_in_per_ft"] > dry["mu_lb_in_per_ft"]
    t = structural_takeoff({"basins": [{"id": "b", "label": "B", "shape": "rectangular", "length_in": 120,
                                        "width_in": 120, "depth_in": 72, "wall_in": 8,
                                        "groundwater_depth_ft": 2}]})
    assert t["basins"][0]["results"]["wall_soil"]["value"]["groundwater_ft"] == approx(4.0)


def test_rules_defaults_are_not_shared():
    from erpnext_enhancements.water_engineering.engine.structural import DEFAULT_RULES, merged_rules

    rules = merged_rules()
    rules["wall_details"].append({"wall_in": 12.0, "bar": 5, "spacing_in": 12.0})
    assert len(DEFAULT_RULES["wall_details"]) == 2


def test_anchor_qty_zero_is_zero():
    from erpnext_enhancements.water_engineering.engine.structural import anchor_bolts

    assert anchor_bolts({"equipment": [{"slot": "filter", "qty": 0}, {"slot": "heater"}]}).value["count"] == 4


def test_excavation_check_follows_the_rule():
    deep = {"id": "d", "label": "D", "shape": "rectangular", "length_in": 120, "width_in": 120, "depth_in": 36}
    # dig 3 + 8/12 + 1 = 4.67 ft: no check at the 5 ft default, a check when the rule is 4 ft
    assert "STRUCT_EXCAVATION_DEPTH" not in {c["code"] for c in structural_takeoff({"basins": [deep]})["checks"]}
    checks = structural_takeoff({"basins": [deep]}, {"excavation_slope_free_ft": 4})["checks"]
    ex = [c for c in checks if c["code"] == "STRUCT_EXCAVATION_DEPTH"]
    assert ex and "4.7 ft deep" in ex[0]["detail"]
