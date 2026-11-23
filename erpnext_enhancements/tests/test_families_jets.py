"""Jet, foam and laminar families: nozzle tables, the drag trajectory and the jet checks.

Bench-free pytest. Goldens are the industry report's sourced figures (Atlantic's column
nozzle, the CMP deck jet, Utah's 20 ft/s) and closed-form physics (the vacuum parabola and
the analytic vertical rise with quadratic drag).

Run: python -m pytest erpnext_enhancements/tests/test_families_jets.py -q
"""

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering.engine.constants import FT_PER_PSI
from erpnext_enhancements.water_engineering.engine.families import foam, jets, laminar
from erpnext_enhancements.water_engineering.engine.families.base import BLOCKER, INFO, WARNING

# Atlantic column nozzle (practicalgardenponds.com): 2 ft at 6.5 ft of head, 12 ft at 31 ft.
ATLANTIC = {"points": [{"display_height_ft": 2, "head_ft": 6.5}, {"display_height_ft": 12, "head_ft": 31}]}
# A synthetic table with flows, for the flow interpolation (not a real nozzle).
SYNTH = {"points": [{"display_height_ft": 2, "head_ft": 6.5, "flow_gpm": 3.0},
                    {"display_height_ft": 12, "head_ft": 31, "flow_gpm": 9.0}]}


def jet(height, product=None, count=1, **inputs):
    return {"id": "j1", "family": "jets", "inputs": dict(height_ft=height, **inputs), "product": product or {},
            "count": count}


def codes(checks):
    return {c.code: c.severity for c in checks}


def test_atlantic_column_nozzle_twelve_feet_at_thirty_one_feet_of_head():
    pt = jets.nozzle_point(jet(12, ATLANTIC))
    assert pt["source"] == "table"
    assert pt["head_ft"] == pytest.approx(31.0)
    assert jets.nozzle_point(jet(2, ATLANTIC))["head_ft"] == pytest.approx(6.5)
    # Linear between the points: 7 ft -> 6.5 + 0.5 x 24.5.
    assert jets.nozzle_point(jet(7, ATLANTIC))["head_ft"] == pytest.approx(18.75)


def test_psi_points_convert_to_head():
    # The same nozzle as printed in psi: 2 ft at 2.8 psi, 12 ft at 13.5 psi.
    psi = {"points": [{"display_height_ft": 2, "psi": 2.8}, {"display_height_ft": 12, "psi": 13.5}]}
    assert jets.nozzle_point(jet(12, psi))["head_ft"] == pytest.approx(13.5 * FT_PER_PSI)
    assert jets.nozzle_point(jet(12, psi))["head_ft"] == pytest.approx(31.0, abs=0.3)


def test_flow_interpolates_from_the_table_and_multiplies_by_count():
    res = jets.flow(jet(7, SYNTH, count=4))
    assert res.data["gpm_each"] == pytest.approx(6.0)
    assert res.value == pytest.approx(24.0)
    assert res.data["source"] == "table"
    assert "JET_HEIGHT_ESTIMATED" not in codes(jets.checks(jet(7, SYNTH, count=4)))


def test_a_pinned_head_reads_the_height_off_the_table():
    pt = jets.nozzle_point({"id": "j", "inputs": {"supply_head_ft": 18.75}, "product": SYNTH})
    assert pt["height_ft"] == pytest.approx(7.0)


def test_outside_the_table_is_a_blocker_never_an_extrapolation():
    pt = jets.nozzle_point(jet(13, ATLANTIC))
    assert pt["in_table"] is False
    assert codes(jets.checks(jet(13, ATLANTIC)))["JET_OUTSIDE_TABLE"] == BLOCKER


def test_no_table_falls_back_to_k_times_head_and_says_so():
    # Smooth bore: head ~ 1.22 x height (k ~ 0.82); a decorative column nozzle ~ 0.39 (Atlantic).
    pt = jets.nozzle_point(jet(12, nozzle_type="smooth"))
    assert pt["source"] == "estimate"
    assert pt["head_ft"] == pytest.approx(12 / 0.82)
    assert jets.nozzle_point(jet(12, nozzle_type="aerating"))["head_ft"] == pytest.approx(12 / 0.39)
    # Cluster and spray nozzles have no public figure: same stand-in, and the citation says so.
    spray = jets.nozzle_point(jet(12, nozzle_type="spray"))
    assert "no public figure" in spray["k_citation"]
    assert "no public figure" in next(c for c in jets.checks(jet(12, nozzle_type="spray", orifice_diameter_in=0.5))
                                  if c.code == "JET_HEIGHT_ESTIMATED").citation
    c = codes(jets.checks(jet(12, nozzle_type="smooth", orifice_diameter_in=0.5)))
    assert c["JET_HEIGHT_ESTIMATED"] == WARNING
    assert jets.flow(jet(12, nozzle_type="smooth")).warnings


def test_estimate_flow_uses_the_profile_rating():
    profile = {"profile": {"rated_gpm": 8.0, "rated_head_ft": 20.0}}
    pt = jets.nozzle_point({"id": "j", "inputs": {"supply_head_ft": 20}, "product": profile})
    assert pt["gpm_each"] == pytest.approx(8.0)


def test_vacuum_trajectory_matches_the_parabola():
    g = 9.80665 / 0.3048
    assert jets.trajectory(g, 90)["apex_ft"] == pytest.approx(g / 2, rel=1e-6)  # v^2 / 2g
    assert jets.trajectory(g, 45)["range_ft"] == pytest.approx(g, rel=1e-4)  # v^2 / g
    assert jets.trajectory(g, 90)["range_ft"] == pytest.approx(0.0, abs=1e-9)


def test_vertical_rise_with_drag_matches_the_closed_form():
    # dv/dt = -g - k v^2 rising: h = ln(1 + k v0^2 / g) / (2k).
    d_in, v0_fps = 0.5, 40.0
    k = jets.drag_k(d_in)
    v0 = v0_fps * 0.3048
    h_m = math.log(1 + k * v0 * v0 / 9.80665) / (2 * k)
    assert jets.trajectory(v0_fps, 90, d_in)["apex_ft"] == pytest.approx(h_m / 0.3048, rel=1e-5)


def test_calibration_hits_the_table_height_and_wind_drifts_downwind():
    v0 = jets.calibrate_v0(10.0, 90, 0.5)
    assert jets.trajectory(v0, 90, 0.5)["apex_ft"] == pytest.approx(10.0, abs=1e-3)
    windy = jets.trajectory(v0, 90, 0.5, wind_mph=12)
    assert windy["range_ft"] > 0.5
    # Drag needs more launch speed than vacuum for the same height.
    assert v0 > math.sqrt(2 * 32.174 * 10.0)


def test_thin_air_at_altitude_carries_further():
    assert jets.air_density(4300) < jets.air_density(0)
    v0 = 30.0
    assert jets.trajectory(v0, 90, 0.3, elevation_ft=4300)["apex_ft"] > jets.trajectory(v0, 90, 0.3)["apex_ft"]


def test_splash_setback_ratios():
    # Crystal: 1:1 for ground pools, 1:1.5 for raised tables.
    assert jets.splash_setback_ft(12, "ground") == pytest.approx(12)
    assert jets.splash_setback_ft(12, "raised") == pytest.approx(18)
    c = codes(jets.checks(jet(12, SYNTH, edge_distance_ft=8)))
    assert c["JET_SETBACK"] == WARNING
    c = codes(jets.checks(jet(12, SYNTH, edge_distance_ft=14, basin_kind="raised")))
    assert c["JET_SETBACK"] == WARNING


def test_edge_distance_defaults_to_half_the_basin():
    basin = {"shape": "round", "diameter_in": 120}
    c = codes(jets.checks(jet(12, SYNTH), {"basin": basin}))
    assert "JET_SETBACK" in c  # 5 ft to the edge for a 12 ft jet


def test_wind_drift_past_the_edge_asks_for_wind_control():
    f = jet(12, SYNTH, edge_distance_ft=12.5, wind_mph=25)
    c = codes(jets.checks(f))
    assert c["JET_WIND_DRIFT"] == WARNING
    f["inputs"]["wind_control"] = True
    assert codes(jets.checks(f))["JET_WIND_DRIFT"] == INFO


def test_manifold_spread():
    f = jet(7, SYNTH, count=6)
    assert codes(jets.checks(f))["JET_MANIFOLD_TARGET"] == INFO
    assert codes(jets.checks(f, {"manifold_spread_pct": 4.0}))["JET_MANIFOLD_SPREAD"] == WARNING
    assert "JET_MANIFOLD_SPREAD" not in codes(jets.checks(f, {"manifold_spread_pct": 1.5}))
    t = jets.terminal(f)
    assert t["kind"] == "manifold" and len(t["params"]["takeoffs"]) == 6
    assert t["params"]["spread_target_pct"] == 2.0


def test_body_contact_velocity_over_twenty_feet_per_second_is_a_blocker():
    # Utah R392-302-41: nozzle velocity <= 20 ft/s. 10 ft of head -> sqrt(2 g 10) = 25.4 ft/s.
    f = {"id": "j", "inputs": {"supply_head_ft": 10, "body_contact": True}, "product": {}}
    assert codes(jets.checks(f))["JET_IWF_VELOCITY"] == BLOCKER
    f["inputs"]["supply_head_ft"] = 5  # 17.9 ft/s
    assert "JET_IWF_VELOCITY" not in codes(jets.checks(f))


def test_single_nozzle_terminal_is_an_orifice_with_its_curve():
    t = jets.terminal(jet(7, SYNTH))
    assert t["kind"] == "orifice"
    assert t["params"]["head_ft"] == pytest.approx(18.75)
    assert t["params"]["curve"] == [(6.5, 3.0), (31.0, 9.0)]


# --- foam ------------------------------------------------------------------------------


def foam_jet(**inputs):
    return {"id": "fm", "family": "foam", "inputs": dict({"height_ft": 4}, **inputs), "product": {}}


def test_foam_uses_the_house_band_until_a_cut_sheet_gives_one():
    c = codes(foam.checks(foam_jet(depth_over_nozzle_in=3, check_valve=True)))
    assert c["FOAM_BAND_HOUSE_RULE"] == INFO
    assert "FOAM_DEPTH_OUT_OF_BAND" not in c
    assert foam.band(foam_jet()) [:2] == (2.0, 4.0)
    sheet = dict(foam_jet(depth_over_nozzle_in=3), product={"profile": {"min_depth_in": 4, "max_depth_in": 6}})
    assert codes(foam.checks(sheet))["FOAM_DEPTH_OUT_OF_BAND"] == WARNING


def test_foam_level_sensitive_jet_needs_a_check_valve_or_accepted_drain_down():
    assert codes(foam.checks(foam_jet(depth_over_nozzle_in=3)))["FOAM_NO_CHECK_VALVE"] == WARNING
    assert "FOAM_NO_CHECK_VALVE" not in codes(foam.checks(foam_jet(depth_over_nozzle_in=3, drains_down_ok=True)))


def test_foam_autofill_deadband_wider_than_the_band():
    c = codes(foam.checks(foam_jet(depth_over_nozzle_in=3, check_valve=True, autofill_deadband_in=3)))
    assert c["FOAM_AUTOFILL_BAND"] == WARNING


def test_house_rules_override_the_band():
    rules = {"foam_submergence_min_in": 1.0, "foam_submergence_max_in": 2.0}
    c = codes(foam.checks(foam_jet(depth_over_nozzle_in=3, check_valve=True), rules=rules))
    assert c["FOAM_DEPTH_OUT_OF_BAND"] == WARNING


# --- laminar ---------------------------------------------------------------------------


def lam(height, **inputs):
    return {"id": "lj", "family": "laminar", "inputs": dict(arc_height_ft=height, **inputs), "product": {}, "count": 2}


def test_laminar_reads_the_cmp_deck_jet_table():
    # CMP: 2.5 gpm @ 3.8 psi -> 2 ft x 3 ft; 3.5 gpm @ 6.5 psi -> 4 ft x 6 ft.
    assert laminar.flow(lam(2)).data["gpm_each"] == pytest.approx(2.5)
    assert laminar.flow(lam(4)).data["gpm_each"] == pytest.approx(3.5)
    assert laminar.flow(lam(4)).data["psi"] == pytest.approx(6.5)
    assert laminar.flow(lam(3)).value == pytest.approx(2 * 3.0)


def test_laminar_arc_geometry():
    v0, angle = laminar.arc_from_table(2.0, 3.0)
    vy = math.sqrt(2 * 9.80665 / 0.3048 * 2.0)
    assert angle == pytest.approx(math.degrees(math.atan(4 * 2.0 / 3.0)))  # tan = 4H / R
    assert v0 == pytest.approx(vy / math.sin(math.radians(angle)))


def test_laminar_landing_checks():
    c = codes(laminar.checks(lam(4, landing_edge_ft=2)))
    assert c["LAMINAR_LANDS_OUTSIDE"] == BLOCKER
    assert c["LAMINAR_GENERIC"] == WARNING
    assert codes(laminar.checks(lam(4, filtered=False)))["LAMINAR_NO_FILTER"] == WARNING
    assert codes(laminar.checks(lam(9)))["LAMINAR_OUTSIDE_TABLE"] == BLOCKER


def test_a_blank_height_with_a_table_is_unentered_not_outside_the_table():
    c = codes(jets.checks(jet(None, ATLANTIC)))
    assert "JET_OUTSIDE_TABLE" not in c
    assert c["JET_INCOMPLETE"] == INFO


def test_a_known_flow_per_nozzle_sizes_the_array_without_a_head():
    # The old engine's nozzle_array_flow: 5 nozzles at 8 gpm = 40, no head needed.
    res = jets.flow({"id": "j", "inputs": {"gpm_each": 8}, "count": 5})
    assert res.value == pytest.approx(40.0)
    assert res.data["source"] == "rated_flow" and res.warnings
    assert codes(jets.checks({"id": "j", "inputs": {"gpm_each": 8}, "count": 5}))["JET_INCOMPLETE"] == INFO


def test_calibration_is_memoized():
    jets._calibrate_v0.cache_clear()
    jets.calibrate_v0(9.5, 90, 0.4)
    jets.calibrate_v0(9.5, 90.0, 0.4, 0)
    assert jets._calibrate_v0.cache_info().hits == 1


def test_a_phase_d_nozzle_profile_reads_directly():
    # Nozzle Profile as phase D stores it: `performance` rows, `discharge_coefficient`, and the
    # new nozzle_type values.
    profile = {"nozzle_type": "Orifice Jet", "discharge_coefficient": 0.95, "orifice_diameter_in": 0.5,
               "performance": [{"display_height_ft": 2, "head_ft": 6.5, "flow_gpm": 3.0},
                               {"display_height_ft": 12, "head_ft": 31, "flow_gpm": 9.0}]}
    pt = jets.nozzle_point({"id": "j", "inputs": {"height_ft": 7}, "product": {"profile": profile}})
    assert pt["source"] == "table" and pt["head_ft"] == pytest.approx(18.75)
    est = jets.nozzle_point({"id": "j", "inputs": {"height_ft": 7},
                             "product": {"profile": dict(profile, performance=[])}})
    assert est["k"] == 0.82  # Orifice Jet -> smooth bore
    assert est["gpm_each"] == pytest.approx(jets.nozzle_flow(7 / 0.82, cd=0.95, orifice_diameter_in=0.5).value)
    assert jets.fallback_k("Foam / Aerating")[0] == 0.39
    assert jets.fallback_k("Spray Ring")[0] == 0.39 and "no public figure" in jets.fallback_k("Spray Ring")[1]
