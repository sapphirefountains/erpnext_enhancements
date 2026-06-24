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
    calc_lighting,
    calc_solenoid_relays,
    chemistry_targets,
    chlorinator_feed,
    component_loss,
    fitting_minor_loss,
    hazen_williams_loss,
    head_at_flow,
    lighting_sizing,
    manning_drain_flow,
    nozzle_flow,
    npsh_available,
    ozone_sidestream,
    pipe_velocity,
    run_spine,
    select_pump,
    size_drain,
    size_pipe,
    suction_outlet_vgb,
    surge_basin_volume,
    total_dynamic_head,
    turnover_gpm,
    units,
    velocity_status,
    water_hammer,
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

    def test_nozzle_flow_stub_without_profile(self):
        r = nozzle_flow(10)
        self.assertIsNone(r.value)
        self.assertTrue(any("Nozzle Profile" in w for w in r.warnings))

    def test_nozzle_flow_orifice(self):
        # Q = Cd*A*sqrt(2gh): Cd 0.97, area 0.20 in^2, head 10 ft -> ~15.34 GPM
        self.assertAlmostEqual(nozzle_flow(10, cd=0.97, orifice_area_in2=0.20).value, 15.34, places=1)
        # the diameter path (A = pi/4 * d^2) also computes a positive flow
        self.assertGreater(nozzle_flow(10, cd=0.97, orifice_diameter_in=0.5).value, 0)

    def test_nozzle_flow_rated_scaling(self):
        # Q = rated_gpm * sqrt(head/rated_head): 10 GPM @ 10 ft -> 14.142 GPM @ 20 ft
        self.assertAlmostEqual(nozzle_flow(20, rated_gpm=10, rated_head_ft=10).value, 14.1421, places=3)


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

    def test_select_pump_flow_only_when_head_unknown(self):
        # Catalog seeded from GPH (flow) with no head rating: match on flow,
        # pick the smallest adequate, and warn to verify head against the curve.
        candidates = [
            {"item_code": "P-20", "rated_gpm": 20},  # too small for 27 GPM
            {"item_code": "P-33", "rated_gpm": 33.3},  # smallest adequate
            {"item_code": "P-97", "rated_gpm": 96.85},
        ]
        r = select_pump(27, 11, candidates)
        self.assertEqual(r.value, "P-33")
        self.assertTrue(any("head" in w.lower() for w in r.warnings))

    def test_select_pump_excludes_insufficient_known_head(self):
        # A pump WITH a head rating that can't meet the duty head is excluded.
        candidates = [
            {"item_code": "LOWHEAD", "rated_gpm": 100, "rated_tdh_ft": 5},
            {"item_code": "OK", "rated_gpm": 100, "rated_tdh_ft": 50},
        ]
        r = select_pump(27, 30, candidates)
        self.assertEqual(r.value, "OK")

    def test_head_at_flow_interpolation(self):
        curve = [{"flow_gpm": 0, "head_ft": 60}, {"flow_gpm": 50, "head_ft": 40}, {"flow_gpm": 100, "head_ft": 10}]
        self.assertEqual(head_at_flow(curve, 0), 60)
        self.assertEqual(head_at_flow(curve, 50), 40)
        self.assertAlmostEqual(head_at_flow(curve, 25), 50, places=6)  # interpolated
        self.assertAlmostEqual(head_at_flow(curve, 75), 25, places=6)
        self.assertIsNone(head_at_flow(curve, 120))  # beyond the pump's max flow
        self.assertIsNone(head_at_flow([], 10))

    def test_select_pump_uses_curve(self):
        # Envelope check (rated 80gpm/30ft) would pass the small pump at (40,25),
        # but its curve (head=20 @ 40 gpm) correctly rejects it; the bigger pump wins.
        small = {"item_code": "P-SMALL", "rated_gpm": 80, "rated_tdh_ft": 30,
                 "curve": [{"flow_gpm": 0, "head_ft": 30}, {"flow_gpm": 40, "head_ft": 20}, {"flow_gpm": 80, "head_ft": 5}]}
        big = {"item_code": "P-BIG", "rated_gpm": 150, "rated_tdh_ft": 60,
               "curve": [{"flow_gpm": 0, "head_ft": 60}, {"flow_gpm": 40, "head_ft": 45}, {"flow_gpm": 120, "head_ft": 10}]}
        r = select_pump(40, 25, [small, big])
        self.assertEqual(r.value, "P-BIG")
        rec = next(o for o in r.options if o.recommended)
        self.assertEqual(rec.detail["head_basis"], "curve")
        self.assertAlmostEqual(rec.detail["head_at_duty_ft"], 45.0, places=2)

    def test_select_pump_curve_beyond_max_flow_excluded(self):
        p = {"item_code": "P", "rated_gpm": 999,
             "curve": [{"flow_gpm": 0, "head_ft": 50}, {"flow_gpm": 60, "head_ft": 10}]}
        r = select_pump(100, 5, [p])  # 100 GPM is beyond the curve's 60 GPM max
        self.assertIsNone(r.value)


class ChemistryTests(unittest.TestCase):
    def test_chlorinator_feed(self):
        # DOC-0049 C36: 50000 gal -> 0.625 gal/hr of 10% chlorine
        self.assertAlmostEqual(chlorinator_feed(50000).value, 0.625, places=6)
        # stronger product needs proportionally less
        self.assertAlmostEqual(chlorinator_feed(50000, 12.5).value, 0.5, places=6)

    def test_chemistry_targets(self):
        out = chemistry_targets("outdoor")
        self.assertEqual(out.value, "outdoor")
        self.assertTrue(any("1.0-3.0 ppm" in s for s in out.steps))
        salt = chemistry_targets("saltwater")
        self.assertTrue(any("60-80 ppm" in s for s in salt.steps))
        self.assertTrue(chemistry_targets("lava").warnings)

    def test_ozone_sidestream(self):
        # DOC-0049 C - Chemicals worked example: 40000 gal, 360 min, 25%, CNT120
        r = ozone_sidestream(40000, 360, 0.25, "CNT120", 1, "2-log")
        self.assertEqual(r.status, "Okay")
        self.assertAlmostEqual(r.value, 7.145833, places=4)  # g/hr, 2-log
        r3 = ozone_sidestream(40000, 360, 0.25, "CNT120", 1, "3-log")
        self.assertAlmostEqual(r3.value, 10.791667, places=4)  # g/hr, 3-log

    def test_ozone_undersized_tank_warns(self):
        # A tiny tank can't pass the side-stream flow -> status + warning
        r = ozone_sidestream(40000, 60, 0.25, "CNT30", 1, "2-log")  # 666 GPM full, 167 side vs 40 max
        self.assertNotEqual(r.status, "Okay")
        self.assertTrue(r.warnings)


class DrainageTests(unittest.TestCase):
    def test_manning_drain_flow_golden(self):
        # DOC-0049 10-Gravity K-column, slope 1/4"/ft, table n (verified)
        self.assertAlmostEqual(manning_drain_flow('3"', 0.25).value, 30.369546829898628, places=6)
        self.assertAlmostEqual(manning_drain_flow('4"', 0.25).value, 58.20622076699431, places=6)
        self.assertAlmostEqual(manning_drain_flow('6"', 0.25).value, 162.01613635126017, places=5)
        # G-Gravity!E34 worked example: 4" @ 3/8"/ft
        self.assertAlmostEqual(manning_drain_flow('4"', 0.375).value, 71.2877703674629, places=6)

    def test_manning_unknown_size_warns(self):
        self.assertTrue(manning_drain_flow('99"', 0.25).warnings)

    def test_size_drain_picks_smallest_adequate(self):
        # @ 1/4"/ft: 3"=30.4, 4"=58.2 GPM -> 50 GPM needs 4", 25 GPM needs 3"
        self.assertEqual(size_drain(50, 0.25).value, '4"')
        self.assertEqual(size_drain(25, 0.25).value, '3"')

    def test_surge_basin_volume(self):
        # pool 400 sf over basin 100 sf, defaults, no swimmers:
        # depth = 3+3+0 + (400*1/100=4) + (400*0.25/100=1) + 12 = 23 in
        # gal = (23/12)*100*7.48 = 1433.6667
        r = surge_basin_volume(400, 100)
        self.assertAlmostEqual(r.value, 1433.6667, places=3)
        # swimmers add displacement -> larger basin
        self.assertGreater(surge_basin_volume(400, 100, swimmers=20).value, r.value)


class ControlsTests(unittest.TestCase):
    def test_lighting_sizing(self):
        # 4 x 50W @ 12VDC -> 200W, 16.67A, ceil(200/60)=4 relays
        s = lighting_sizing([{"qty": 4, "watts_each": 50}])
        self.assertEqual(s["total_watts"], 200)
        self.assertAlmostEqual(s["current_a"], 16.6667, places=3)
        self.assertEqual(s["relay_count"], 4)
        self.assertEqual(calc_lighting([{"qty": 4, "watts_each": 50}]).value, 4)

    def test_lighting_oversized_single_light_warns(self):
        r = calc_lighting([{"qty": 1, "watts_each": 90}])
        self.assertTrue(any("exceeds" in w for w in r.warnings))

    def test_solenoid_relays(self):
        self.assertEqual(calc_solenoid_relays(5).value, 5)


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


class SafetyTests(unittest.TestCase):
    # --- VGB suction-outlet anti-entrapment (DOC-0049 P-sheet worked example) ---
    def test_vgb_psheet_golden(self):
        # 362"x14" channel grate, 21.5% open: AR=7.086 SF, AB=0.4808 SF,
        # entrapment Q=78.44 CFS=35,204 GPM, velocity-limit 4,770 GPM -> min governs.
        r = suction_outlet_vgb(2000, 362, 14, 0.215, outlets=2)
        self.assertAlmostEqual(r.value, 4770.3, delta=1.0)  # velocity-limited max safe
        self.assertEqual(r.status, "Okay")  # 2 outlets, 2000 GPM each < 4770

    def test_vgb_single_outlet_flags_dual_drain(self):
        r = suction_outlet_vgb(2000, 362, 14, 0.215, outlets=1)
        self.assertEqual(r.status, "Add Second Drain")
        self.assertTrue(any("second anti-entrapment" in w.lower() for w in r.warnings))

    def test_vgb_overflow_fails(self):
        r = suction_outlet_vgb(10000, 362, 14, 0.215, outlets=2)
        self.assertEqual(r.status, "Entrapment Risk — Resize")
        self.assertTrue(any("exceeds" in w.lower() for w in r.warnings))

    # --- NPSH available ---
    def test_npsh_lift_golden(self):
        # sea level Ha=33.948, Hvp@70F=0.839; lift 10 + friction 3 -> 20.11 ft
        r = npsh_available(-10, 3, water_temp_f=70)
        self.assertAlmostEqual(r.value, 20.11, places=1)

    def test_npsh_status_bands(self):
        self.assertEqual(npsh_available(-10, 3, water_temp_f=70, npshr_ft=15).status, "Okay")
        self.assertEqual(npsh_available(-10, 3, water_temp_f=70, npshr_ft=19).status, "Marginal")
        self.assertEqual(npsh_available(-10, 3, water_temp_f=70, npshr_ft=25).status, "Cavitation Risk")

    def test_npsh_altitude_reduces_head(self):
        self.assertLess(npsh_available(0, 0, elevation_ft=5000).value, npsh_available(0, 0, elevation_ft=0).value)

    # --- water hammer (Joukowsky) ---
    def test_water_hammer_instantaneous_golden(self):
        # a=1300 ft/s, dV=6: surge = (1300*6/32.2)/2.31 = 104.86 psi
        r = water_hammer(6, 200, closure_time_s=0, material="SCH40 PVC")
        self.assertAlmostEqual(r.value, 104.9, delta=0.2)

    def test_water_hammer_slow_closure_scales_down(self):
        # 2L/a = 0.3077 s; a 3 s closure scales the surge by 0.3077/3
        r = water_hammer(6, 200, closure_time_s=3, material="SCH40 PVC")
        self.assertAlmostEqual(r.value, 10.75, delta=0.2)

    def test_water_hammer_rating_check_and_material(self):
        self.assertEqual(water_hammer(6, 200, static_psi=40, pipe_rating_psi=140).status, "Exceeds Pipe Rating")
        self.assertEqual(water_hammer(6, 200, static_psi=40, pipe_rating_psi=200).status, "Okay")
        # copper's stiffer wall -> much faster wave -> bigger surge than PVC
        self.assertGreater(water_hammer(6, 200, material="COPPER").value, water_hammer(6, 200, material="SCH40 PVC").value)


if __name__ == "__main__":
    unittest.main()
