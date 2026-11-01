"""engine/hydraulics/basin_checks.py against DRC checks.js basinChecks (tests/golden/drc/basin_checks.json,
1e-12) minus the curtain geometry, and the envelope's checks and workbook cross-checks.

Bench-free pytest suite: python -m pytest erpnext_enhancements/tests/test_hydraulics_basin_checks.py -q
"""

import math

from erpnext_enhancements.tests.drc_golden import REL, close, golden, seg_from_drc
from erpnext_enhancements.water_engineering.engine.drainage import surge_basin_volume
from erpnext_enhancements.water_engineering.engine.hydraulics.basin_checks import basin_checks, basin_checks_si
from erpnext_enhancements.water_engineering.engine.hydraulics.water import water_si
from erpnext_enhancements.water_engineering.engine.treatment import evaporation_rate

GOLD = golden("basin_checks")
FIELDS = {
    "supply_vol_l": "supplyVolL", "suction_vol_l": "suctionVolL", "in_flight_l": "inFlightL", "drainback_l": "drainbackL",
    "drainback_no_check_l": "drainbackNoCheckL", "level_rise_mm": "levelRiseMm", "submerge_req_m": "submergeReqM",
    "submerge_have_m": "submergeHaveM", "filter_um": "filterUm", "basin_l": "basinL", "turnover_min": "turnoverMin",
    "evap_lph": "evapLph", "spray_lph": "evapSprayLph", "makeup_lpd": "makeupLpd", "manifold_vol_l": "manifoldVolL",
}


def test_matches_basin_checks():
    assert len(GOLD["scenarios"]) >= 7
    for sc in GOLD["scenarios"]:
        w = water_si(sc["waterC"])
        r = basin_checks_si(
            plan_area_m2=sc["planArea"], water_depth_m=sc["basinWaterM"], freeboard_m=sc["basinFreeM"], q_all=sc["ctx"]["Qall"], w=w,
            supply=[seg_from_drc(s) for s in sc["supply"]], suction=seg_from_drc(sc["suction"]), manifold_vol_l=sc["manifoldVolL"],
            in_flight=[(sc["ctx"]["Qall"], sc["ctx"]["fallT"])], orifice_mm=sc["orificeMm"], air_c=sc["airC"], rh_pct=sc["rhPct"],
            wind_ms=sc["windMs"], hours_per_day=sc["hoursPerDay"],
        )
        out = sc["out"]
        for mine, theirs in FIELDS.items():
            close(r[mine], out[theirs], REL, f"{sc['name']} {theirs}")
        for mine, theirs in (("has_check", "hasCheck"), ("freeboard_ok", "freeboardOk"), ("submerge_ok", "submergeOk"), ("mesh_no", "meshNo")):
            assert r[mine] == out[theirs], f"{sc['name']} {theirs}: {r[mine]} vs {out[theirs]}"


def _basin(**kw):
    return {"id": "b1", "shape": "rectangular", "length_in": 120, "width_in": 24, "depth_in": 14, "freeboard_in": 2, **kw}


SUPPLY = [{"material": "SCH40 PVC", "nominal_size": '2"', "length_ft": 40, "fittings": [{"key": "elbow_90", "qty": 3}]}]
SUCTION = {"material": "SCH40 PVC", "nominal_size": '3"', "length_ft": 6, "fittings": [{"key": "entrance", "qty": 1}]}


def test_check_valve_holds_the_drain_back():
    open_ = basin_checks(_basin(), flow_gpm=60, supply=SUPPLY, suction=SUCTION).value
    held = basin_checks(_basin(), flow_gpm=60, supply=[dict(SUPPLY[0], fittings=[{"key": "check", "qty": 1}])], suction=SUCTION).value
    assert not open_["has_check"] and held["has_check"]
    assert open_["drainback_gal"] == open_["drainback_no_check_gal"] > 0 and held["drainback_gal"] == 0
    # 40 ft of 2" Sch 40 holds pi/4 * 2.067^2 * 480 / 231 = 6.97 gal
    assert abs(open_["supply_gal"] - math.pi / 4 * 2.067**2 * 480 / 231) < 1e-9
    rise = open_["drainback_gal"] * 231 / (120 * 24)
    assert abs(open_["level_rise_in"] - rise) < 1e-9


def test_freeboard_and_submergence_checks():
    tight = basin_checks(_basin(freeboard_in=0.5, depth_in=4), flow_gpm=120, supply=SUPPLY, suction=SUCTION,
                         in_flight=[{"flow_gpm": 120, "fall_s": 0.8}])
    codes = {c["code"]: c for c in tight.value["checks"]}
    assert "basin.freeboard" in codes and "basin.submergence" in codes
    assert codes["basin.submergence"]["severity"] == "blocker" and codes["basin.freeboard"]["section"] == "basin"
    assert tight.status == "Blocked"
    # In-flight water: 120 GPM for 0.8 s is 1.6 gal.
    assert abs(tight.value["in_flight_gal"] - 1.6) < 1e-12


def test_hi98_by_hand():
    """3" Sch 40 at 150 GPM: V = 6.51 ft/s, Fr = V/sqrt(gD) = 2.32, S = D(1 + 2.3 Fr) = 19.4 in."""
    r = basin_checks(_basin(depth_in=24), flow_gpm=150, suction=SUCTION).value
    d_ft = 3.068 / 12
    v = 150 * 0.002228009 / (math.pi / 4 * d_ft**2)
    fr = v / math.sqrt(32.174049 * d_ft)
    assert abs(r["froude"] - fr) < 1e-4
    assert abs(r["submergence_required_in"] - 3.068 * (1 + 2.3 * fr)) < 1e-3


def test_crosschecks_are_the_workbook_functions():
    r = basin_checks(_basin(), flow_gpm=60, supply=SUPPLY, suction=SUCTION, water_temp_f=80, air_temp_f=90, rh_pct=30).value
    area = 120 * 24 / 144
    assert r["crosscheck"]["surge_basin_gal"] == surge_basin_volume(area, area).value
    assert r["crosscheck"]["evaporation_gpd_ashrae"] == evaporation_rate(area, 80, 90, 30).value
    # Carrier and ASHRAE are the same family: within a factor of two on a still day.
    ratio = r["crosscheck"]["evaporation_gpd_carrier"] / r["crosscheck"]["evaporation_gpd_ashrae"]
    assert 0.5 < ratio < 2.0
    # Spray loss is 0.5% of the flow.
    assert abs(r["spray_loss_gph"] - 0.005 * 60 * 60) < 1e-9
