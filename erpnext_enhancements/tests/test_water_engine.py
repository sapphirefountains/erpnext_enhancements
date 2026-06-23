"""Bench-free unit tests for the pure water-feature calculation engine.

Plain ``unittest`` — the engine imports only the stdlib, so these run with no
Frappe site and no FAC. Golden values were extracted directly from the source
workbooks (DOC-0048 Basin, DOC-0049 A - Pipe Size / H - TDH / I - Weir /
SUPPORT) with openpyxl and re-computed to machine precision.

Run: python -m pytest erpnext_enhancements/tests/test_water_engine.py
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering.engine import (
    basin_volume,
    component_loss,
    fitting_minor_loss,
    hazen_williams_loss,
    nozzle_flow,
    pipe_velocity,
    run_spine,
    select_pump,
    size_pipe,
    total_dynamic_head,
    turnover_gpm,
    units,
    velocity_status,
    weir_flow,
)
from erpnext_enhancements.water_engineering.engine.constants import (
    HW_CONSTANT_TDH,
)
from erpnext_enhancements.water_engineering.engine.data.pipe_specs import (
    get_pipe_id,
)


class BasinTests(unittest.TestCase):
    def test_rectangular_volume(self):
        # DOC-0048 Basin: 120 x 60 x 12 in -> 374.0256 gal
        self.assertAlmostEqual(basin_volume("rectangular", 120, 60, 12).value, 374.0256, places=4)

    def test_cylindrical_volume(self):
        # 0.25 * pi * 120^2 * 18 in -> 881.277 gal
        self.assertAlmostEqual(
            basin_volume("cylindrical", diameter_in=120, height_in=18).value, 881.27706, places=3
        )

    def test_weight_in_steps(self):
        # 374.0256 gal * 8.34 = 3119.37 lb
        self.assertAlmostEqual(units.gallons_to_pounds(374.0256), 3119.373504, places=4)

    def test_unknown_shape_warns(self):
        r = basin_volume("triangle", 1, 2, 3)
        self.assertIsNone(r.value)
        self.assertTrue(r.warnings)

    def test_turnover_gpm(self):
        # 374.0256 gal * 2 / 60 = 12.46752 GPM
        self.assertAlmostEqual(turnover_gpm(374.0256, 2).value, 12.46752, places=4)


class FeatureTests(unittest.TestCase):
    def test_weir_francis(self):
        # 36*6*0.25^1.5 - 0.3*2*0.25^2.5 = 26.98125
        self.assertAlmostEqual(weir_flow(6, 0.25, 2).value, 26.98125, places=5)

    def test_weir_table_anchor(self):
        # SUPPORT WeirInfo: L=1, h=1", n=2 -> 35.4 ; h=0.5" -> 12.621856
        self.assertAlmostEqual(weir_flow(1, 1, 2).value, 35.4, places=6)
        self.assertAlmostEqual(weir_flow(1, 0.5, 2).value, 12.621856, places=5)

    def test_weir_monotonic_in_head(self):
        self.assertLess(weir_flow(1, 0.5).value, weir_flow(1, 1.0).value)

    def test_nozzle_flow_is_stubbed(self):
        r = nozzle_flow("smooth_bore", 12)
        self.assertIsNone(r.value)
        self.assertTrue(any("not defined" in w for w in r.warnings))


class PipeTests(unittest.TestCase):
    def test_velocity(self):
        # 120 * 0.4085 / 0.824^2 = 72.196955
        self.assertAlmostEqual(pipe_velocity(120, 0.824).value, 72.196955, places=4)
        # 29 * 0.4085 / 2.469^2 = 1.943336
        self.assertAlmostEqual(pipe_velocity(29, 2.469).value, 1.943336, places=5)

    def test_velocity_rises_as_id_falls(self):
        self.assertGreater(pipe_velocity(120, 1.0).value, pipe_velocity(120, 2.0).value)

    def test_velocity_status_bands(self):
        # PVC limits: suction 4.5, discharge 6.5, legal 8.0
        self.assertEqual(velocity_status(2.58, "discharge", 4.5, 6.5, 8.0), "Okay")
        self.assertEqual(velocity_status(5.79, "suction", 4.5, 6.5, 8.0), "Increase Size")
        self.assertEqual(velocity_status(8.5, "discharge", 4.5, 6.5, 8.0), "Exceeds Legal Limit")

    def test_hazen_williams_default_constant(self):
        # 10.44 * 150 * 120^1.85 / (130^1.85 * 3.068^4.8655) = 5.776797
        self.assertAlmostEqual(hazen_williams_loss(120, 150, 3.068).value, 5.776797, places=4)

    def test_hazen_williams_tdh_constant(self):
        # 10.456 * 2.5 * 29^1.85 / (130^1.85 * 2.469^4.8655) = 0.020050967
        self.assertAlmostEqual(
            hazen_williams_loss(29, 2.5, 2.469, constant=HW_CONSTANT_TDH).value, 0.020050967, places=6
        )

    def test_pipe_id_lookup(self):
        self.assertEqual(get_pipe_id("SCH40 PVC", '2"'), 2.067)
        self.assertEqual(get_pipe_id("SCH80 PVC", '3"'), 2.9)

    def test_size_pipe_recommends_first_ok_size(self):
        # 120 GPM discharge in Sch40 PVC: 2"/2-1/2" exceed limits; 3" is first Okay.
        r = size_pipe(120, 150, "SCH40 PVC", "discharge")
        self.assertEqual(r.value, '3"')
        self.assertEqual(r.status, "Okay")
        self.assertTrue(any(o.recommended for o in r.options))


class TdhTests(unittest.TestCase):
    def test_fitting_minor_loss(self):
        # sum_K = 4*0.81 + 1(reentrant) + 1(exit) = 5.24 ; V=2.58
        # minor = 5.24 * 2.58^2 / (2*32.2) = 0.541608
        fittings = [
            {"type": "ELL 90", "qty": 4},
            {"type": "ENTRANCE REENTRANT", "qty": 1},
            {"type": "EXIT", "qty": 1},
        ]
        self.assertAlmostEqual(fitting_minor_loss(2.58, fittings).value, 0.541608, places=4)

    def test_unknown_fitting_warns(self):
        r = fitting_minor_loss(2.58, [{"type": "WARP DRIVE", "qty": 1}])
        self.assertEqual(r.value, 0.0)
        self.assertTrue(r.warnings)

    def test_component_loss(self):
        # (0.05 + 0.0766667) * 26.9812 = 3.4176
        comps = [
            {"type": "SUCTION OUTLET COVER/GRATE", "qty": 1},
            {"type": "CARTRIDGE FILTER 320 SF CCP CLEAN", "qty": 1},
        ]
        self.assertAlmostEqual(component_loss(26.9812, comps).value, 3.41763, places=3)

    def test_total_dynamic_head_single_segment(self):
        # static 10 + major(120,150,3.068)=5.7768, no minor/component -> 15.7768
        seg = [{"flow_gpm": 120, "id_in": 3.068, "length_ft": 150}]
        self.assertAlmostEqual(total_dynamic_head(seg, static_lift_ft=10).value, 15.776797, places=4)

    def test_segment_resolves_nominal_size(self):
        seg = [{"flow_gpm": 120, "nominal_size": '3"', "material": "SCH40 PVC", "length_ft": 150}]
        self.assertAlmostEqual(total_dynamic_head(seg, static_lift_ft=0).value, 5.776797, places=4)


class PumpTests(unittest.TestCase):
    def test_select_pump_no_catalog_warns(self):
        r = select_pump(50, 30, None)
        self.assertIsNone(r.value)
        self.assertTrue(any("catalog" in w for w in r.warnings))

    def test_select_pump_picks_smallest_adequate(self):
        candidates = [
            {"item_code": "PUMP-BIG", "rated_gpm": 200, "rated_tdh_ft": 80},
            {"item_code": "PUMP-FIT", "rated_gpm": 60, "rated_tdh_ft": 40},
            {"item_code": "PUMP-SMALL", "rated_gpm": 20, "rated_tdh_ft": 10},
        ]
        r = select_pump(50, 30, candidates)
        self.assertEqual(r.value, "PUMP-FIT")


class UnitsTests(unittest.TestCase):
    def test_conversions(self):
        self.assertAlmostEqual(units.cubic_inches_to_gallons(231), 0.999999, places=5)
        self.assertEqual(units.gallons_to_pounds(1), 8.34)
        self.assertAlmostEqual(units.feet_to_psi(2.31), 1.0, places=6)
        self.assertAlmostEqual(units.psi_to_feet(1.0), 2.31, places=6)


class EnvelopeShapeTests(unittest.TestCase):
    def test_every_result_shows_its_work(self):
        for r in (
            basin_volume("rectangular", 120, 60, 12),
            turnover_gpm(374.0256, 2),
            weir_flow(6, 0.25, 2),
            pipe_velocity(120, 3.068),
            hazen_williams_loss(120, 150, 3.068),
        ):
            self.assertTrue(r.formula, f"{r.calc} missing formula")
            self.assertTrue(r.steps, f"{r.calc} missing steps")
            self.assertTrue(r.citations, f"{r.calc} missing citations")

    def test_to_dict_is_json_ready(self):
        d = size_pipe(120, 150).to_dict()
        self.assertIn("options", d)
        self.assertIsInstance(d["options"], list)
        self.assertIsInstance(d["options"][0], dict)  # CalcOption flattened


class PipelineTests(unittest.TestCase):
    def test_run_spine_partial_reports_needs(self):
        out = run_spine({"basins": [{"shape": "rectangular", "length_in": 120, "width_in": 60, "height_in": 12}]})
        self.assertAlmostEqual(out["total_basin_gallons"], 374.0256, places=4)
        self.assertAlmostEqual(out["required_circulation_gpm"], 12.46752, places=4)
        self.assertIn("features", out["next_inputs_needed"])
        self.assertIn("pipe_segments", out["next_inputs_needed"])

    def test_run_spine_full_reaches_pump(self):
        out = run_spine(
            {
                "basins": [{"shape": "rectangular", "length_in": 120, "width_in": 60, "height_in": 12}],
                "features": [{"feature_type": "weir", "weir_length_ft": 6, "head_in": 0.25}],
                "pipe_segments": [{"flow_gpm": 27, "id_in": 2.067, "length_ft": 60}],
                "static_lift_ft": 6,
                "pump_candidates": [{"item_code": "PUMP-FIT", "rated_gpm": 60, "rated_tdh_ft": 40}],
            }
        )
        self.assertAlmostEqual(out["design_flow_gpm"], 26.98125, places=4)
        self.assertIsNotNone(out["tdh_ft"])
        self.assertEqual(out["selected_pump"], "PUMP-FIT")
        self.assertEqual(out["next_inputs_needed"], [])

    def test_run_spine_honors_custom_c(self):
        base = {"pipe_segments": [{"flow_gpm": 100, "id_in": 2.067, "length_ft": 100}], "static_lift_ft": 0}
        out130 = run_spine({**base, "hazen_williams_c": 130})
        out150 = run_spine({**base, "hazen_williams_c": 150})
        # No minor/component/static -> TDH == the Hazen-Williams major loss at that C.
        self.assertAlmostEqual(out150["tdh_ft"], hazen_williams_loss(100, 100, 2.067, 150).value, places=6)
        self.assertLess(out150["tdh_ft"], out130["tdh_ft"])  # smoother pipe (higher C) -> less loss


if __name__ == "__main__":
    unittest.main()
