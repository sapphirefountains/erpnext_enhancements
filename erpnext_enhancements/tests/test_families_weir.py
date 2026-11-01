"""Weir family (engine/families/weir.py): hand goldens from the industry report's sourced rules.

Bench-free pytest. Every expected number below is the sourced figure, not the code's output:
the report section 1.1 table, with the URL in the module's citations.

Run: python -m pytest erpnext_enhancements/tests/test_families_weir.py -q
"""

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering.engine.families import weir
from erpnext_enhancements.water_engineering.engine.families.base import BLOCKER, WARNING


def feat(**inputs):
    return {"id": "w1", "family": "weir", "inputs": inputs}


def codes(checks):
    return {c.code: c.severity for c in checks}


def test_quarter_inch_on_one_foot_is_about_four_and_a_half_gpm():
    # WaterShapes: Francis, 1/4 in of head ~ 4.5 gpm per foot.
    assert weir.francis_gpm_per_ft(0.25, 1.0, 2) == pytest.approx(4.5, abs=0.05)
    res = weir.flow(feat(length_ft=1, head_in=0.25))
    assert res.value == pytest.approx(4.48125, rel=1e-9)  # 36(0.125) - 0.3(2)(0.03125)


def test_one_inch_is_about_thirty_six_gpm_per_foot():
    assert weir.francis_gpm_per_ft(1.0, 1.0, 0) == pytest.approx(36.0)
    assert weir.francis_gpm_per_ft(1.0, 100.0, 2) == pytest.approx(36.0, abs=0.01)


def test_edge_tolerance_sets_the_head():
    # P&S: 1/16 in of tolerance needs 1/8 in of head; 1/4 needs 1/2; a rounded crest >= 1 in.
    assert weir.tolerance_head_in(1 / 16) == pytest.approx(1 / 8)
    assert weir.tolerance_head_in(1 / 4) == pytest.approx(1 / 2)
    assert weir.tolerance_head_in(1 / 16, "rounded") == pytest.approx(1.0)


def test_square_edge_planning_flow_governs_above_francis():
    # P&S: square edge 7-10 gpm/ft at 1/4 in; Francis (4.5) is only the floor.
    light = weir.flow(feat(length_ft=8, fall_ft=1), rules=None)
    assert light.data["gpm_per_ft"] == pytest.approx(10.0)  # average look = top of the band
    assert light.value == pytest.approx(80.0)
    res = weir.flow({"id": "w", "inputs": {"length_ft": 8}, "effect": {"look": "light"}})
    assert res.data["gpm_per_ft"] == pytest.approx(7.0)
    heavy = weir.flow({"id": "w", "inputs": {"length_ft": 8}, "effect": {"look": "heavy"}})
    assert heavy.data["gpm_per_ft"] == pytest.approx(36.0)  # 36 at 1 in beats Francis 35.9
    assert heavy.data["head_in"] == pytest.approx(1.0)


def test_vanishing_and_slot_planning_flows():
    # WaterShapes: vanishing edge ~5 gpm/ft; slot overflow ~2 gpm/ft.
    assert weir.flow(feat(style="vanishing_edge", length_ft=30)).data["gpm_per_ft"] == pytest.approx(5.0)
    assert weir.flow(feat(style="slot_overflow", length_ft=30)).data["gpm_per_ft"] == pytest.approx(2.0)


def test_sheet_product_uses_its_cut_sheet_or_the_typical_figure():
    typical = weir.flow(feat(style="sheet_product", length_ft=2))
    assert typical.data["gpm_per_ft"] == pytest.approx(12.0)  # Jandy Sheer Descent ~12
    assert typical.warnings
    picked = weir.flow(feat(style="sheet_product", length_ft=2, product_gpm_per_ft=9))
    assert picked.data["gpm_per_ft"] == pytest.approx(9.0)


def test_landing_distance_reproduces_both_published_points():
    # P&S: a 1 m fall carries a 6 mm sheet ~0.3 m out and a 25 mm sheet ~0.7 m.
    one_m = 1 / 0.3048
    assert weir.landing_distance_ft(6 / 25.4, one_m) * 0.3048 == pytest.approx(0.3, rel=1e-9)
    assert weir.landing_distance_ft(25 / 25.4, one_m) * 0.3048 == pytest.approx(0.7, rel=1e-9)
    # A longer fall carries further, with the fall time (sqrt of the fall).
    assert weir.landing_distance_ft(0.25, 4 * one_m) == pytest.approx(2 * weir.landing_distance_ft(0.25, one_m))


def test_vanishing_edge_surge_is_two_inches_over_the_upper_surface():
    # WaterShapes: area x 7.5 / 6 gal (2 in). 100 sq ft -> ~125 gal; busy pools 3 in.
    s = weir.surge_gal(100.0)
    assert s["surge_gal"] == pytest.approx(125.0, rel=0.003)
    assert weir.surge_gal(100.0, busy=True)["surge_gal"] == pytest.approx(187.5, rel=0.003)
    full = weir.surge_gal(100.0, spa_gal=300.0, trough_area_sqft=20.0)
    assert full["total_gal"] == pytest.approx(full["surge_gal"] + 300.0 + 20.0 * 7.480519)


def test_free_sheet_checks():
    c = codes(weir.checks(feat(style="spillway", length_ft=4, fall_ft=4, catch_width_in=36)))
    assert c["WEIR_SHEET_BREAKUP"] == WARNING  # > 24 in, smooth lip
    assert c["WEIR_WIND_CONTROL"] == WARNING  # > 3 ft, no wind control
    assert c["WEIR_CATCH_NARROW"] == WARNING  # 3 ft catch for a 4 ft fall


def test_a_catch_narrower_than_the_landing_is_a_blocker():
    c = codes(weir.checks({"id": "w", "inputs": {"style": "spillway", "length_ft": 4, "fall_ft": 3, "catch_width_in": 6},
                           "effect": {"look": "heavy"}}))
    assert c["WEIR_LANDS_OUTSIDE"] == BLOCKER


def test_catch_width_comes_from_the_basin_when_not_entered():
    f = feat(style="spillway", length_ft=4, fall_ft=2)
    basin = {"shape": "rectangular", "length_in": 60, "width_in": 12, "depth_in": 18}
    assert "WEIR_CATCH_NARROW" in codes(weir.checks(f, {"basin": basin}))
    wide = dict(basin, width_in=36)
    assert "WEIR_CATCH_NARROW" not in codes(weir.checks(f, {"basin": wide}))


def test_a_waterwall_face_and_indoor_skip_the_free_sheet_checks():
    c = codes(weir.checks(feat(style="waterwall", length_ft=8, fall_ft=10, indoor=True)))
    assert not {"WEIR_SHEET_BREAKUP", "WEIR_WIND_CONTROL", "WEIR_LANDS_OUTSIDE"} & set(c)


def test_tolerance_and_vanishing_length_checks():
    c = codes(weir.checks(feat(style="vanishing_edge", length_ft=12, tolerance_in=0.25)))
    assert c["WEIR_TOLERANCE"] == WARNING
    assert c["WEIR_VANISHING_SHORT"] == WARNING


def test_pinned_head_below_the_tolerance_head_is_flagged():
    c = codes(weir.checks(feat(length_ft=8, head_in=0.25, tolerance_in=0.25)))
    assert c["WEIR_HEAD_BELOW_TOLERANCE"] == WARNING


def test_trough_volume_check_reads_the_derived_volume():
    f = feat(style="vanishing_edge", length_ft=30, upper_area_sqft=600)
    d = weir.flow(f).data
    assert "WEIR_TROUGH_SMALL" in codes(weir.checks(f, {"flow": d, "trough_volume_gal": 100}))
    assert "WEIR_TROUGH_SMALL" not in codes(weir.checks(f, {"flow": d, "trough_volume_gal": 5000}))


def test_terminal_is_an_open_discharge_at_the_fall_height():
    t = weir.terminal(feat(length_ft=8, fall_ft=10))
    assert t["kind"] == "open_discharge"
    assert t["params"]["lift_ft"] == 10
    assert t["params"]["design_flow_gpm"] == pytest.approx(80.0)


def test_inputs_schema_shape():
    for row in weir.inputs():
        assert {"name", "label", "kind", "unit", "default", "required", "help"} <= set(row)
    assert math.isclose(next(r for r in weir.inputs() if r["name"] == "tolerance_in")["default"], 1 / 16)
