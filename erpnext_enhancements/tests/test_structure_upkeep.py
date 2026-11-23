"""engine/structure.py and engine/upkeep.py: beam_reactions and water_plan against DRC rigging.js and
upkeep.js (tests/golden/drc/structure.json, upkeep.json, 1e-12), DRC's textbook beam coefficients, and
hand goldens for the hangers, the floor load and the yearly O&M.

Bench-free pytest suite: python -m pytest erpnext_enhancements/tests/test_structure_upkeep.py -q
"""

import math

from erpnext_enhancements.tests.drc_golden import REL, close, close_all, golden
from erpnext_enhancements.water_engineering.engine.chemistry import chlorinator_feed
from erpnext_enhancements.water_engineering.engine.structure import beam_reactions, floor_load, pipe_hangers
from erpnext_enhancements.water_engineering.engine.upkeep import (
    maintenance_schedule,
    om_per_year,
    water_plan,
    water_plan_si,
)
from erpnext_enhancements.water_engineering.engine.workbook import electric_cost

STRUCTURE = golden("structure")
UPKEEP = golden("upkeep")


def test_beam_reactions_match_rigging_js():
    for c in STRUCTURE["cases"]:
        close_all(beam_reactions(c["n"], c["L"], c["w"], c["a"]), c["R"], REL, f"n={c['n']} L={c['L']} w={c['w']} a={c['a']}")


def test_beam_reactions_textbook_coefficients():
    """DRC's own rigging.test.mjs cases: the classic continuous-beam coefficients."""
    assert [round(x, 9) for x in beam_reactions(1, 2, 3)] == [3, 3]
    assert [round(x, 9) for x in beam_reactions(2, 1, 1)] == [0.375, 1.25, 0.375]
    assert [round(x, 9) for x in beam_reactions(3, 1, 1)] == [0.4, 1.1, 1.1, 0.4]
    four = beam_reactions(4, 1, 1)
    for got, want in zip(four, [11 / 28, 32 / 28, 26 / 28, 32 / 28, 11 / 28], strict=True):
        assert abs(got - want) < 1e-12
    for n, span, w, a in [(5, 0.8, 2.5, 0.12), (9, 0.7, 4, 0.3), (1, 3, 1, 0.5)]:
        r = beam_reactions(n, span, w, a)
        assert abs(sum(r) - w * (n * span + 2 * a)) < 1e-9 and abs(r[0] - r[n]) < 1e-9


def test_pipe_hangers_at_the_doc0049_span():
    """20 ft of 2" Sch 40 PVC: 3 ft maximum span -> 7 spans of 2.857 ft at 2.1341 lb/ft (DOC-0049);
    the reactions carry the whole wet weight and the worst is the three-moment result."""
    r = pipe_hangers({"id": "s1", "material": "SCH40 PVC", "nominal_size": '2"', "length_ft": 20})
    v = r.value
    assert v["hangers"] == 8 and abs(v["spacing_ft"] - 20 / 7) < 1e-12 and v["wet_lb_ft"] == 2.1341
    assert abs(sum(v["reactions_lb"]) - 2.1341 * 20) < 1e-9
    assert v["max_load_lb"] == max(beam_reactions(7, 20 / 7, 2.1341))
    assert r.status == "Okay"
    wide = pipe_hangers({"id": "s1", "material": "SCH40 PVC", "nominal_size": '2"', "length_ft": 20}, spacing_ft=5)
    assert wide.status == "Blocked" and wide.value["checks"][0]["code"] == "structure.hanger_span"


def test_floor_load():
    r = floor_load(18, 100, occupancy="office")
    assert abs(r.value["water_psf"] - 1.5 * 998.2 * 0.06242796) < 0.05  # ~93.5 psf for 18 in of water
    assert r.status == "Blocked"  # over the 50 psf office live load
    ok = floor_load(12, 100, slab_capacity_psf=150, dead_psf=20)
    assert ok.status == "Okay" and abs(ok.value["total_psf"] - ok.value["water_psf"] - 20) < 1e-12


def test_water_plan_matches_upkeep_js():
    assert len(UPKEEP["scenarios"]) >= 30
    for sc in UPKEEP["scenarios"]:
        b, u = sc["basin"], sc["upkeep"]
        vol = b["basinL"] + b["manifoldVolL"] + b["supplyVolL"] + b["suctionVolL"]
        r = water_plan_si(vol_l=vol, evap_lph=b["evapLph"], spray_lph=b["evapSprayLph"], hours_per_day=sc["hoursPerDay"],
                          water_c=sc["waterC"], outdoor=sc["outdoor"], treatment=u["treatment"], pct=u["pct"], ppm=u["ppm"],
                          loss_ppm=u["lossPpm"], max_cycles=u["maxCycles"])
        out = sc["out"]
        what = f"{sc['name']} / {b}"
        for mine, theirs in (("vol_l", "volL"), ("evap_lpd", "evapLpd"), ("spray_lpd", "sprayLpd"), ("makeup_lpd", "makeupLpd"),
                             ("ppm", "ppm"), ("loss_ppm", "lossPpm"), ("pct", "pct"), ("max_cycles", "maxCycles"),
                             ("mineral_days", "mineralDays"), ("hygiene_days", "hygieneDays"), ("refresh_days", "refreshDays"),
                             ("makeup_share", "makeupShare")):
            close(r[mine], out[theirs], REL, f"{what} {theirs}")
        assert r["refresh_by"] == out["refreshBy"] and r["warm"] == out["warm"] and r["kind"] == sc["kind"], what
        if out["dose"] is None:
            assert r["dose"] is None, what
        else:
            close(r["dose"]["fill_ml"], out["dose"]["fillMl"], REL, what + " fillMl")
            close(r["dose"]["daily_ml"], out["dose"]["dailyMl"], REL, what + " dailyMl")


def test_water_plan_envelope_and_governing_chlorine():
    r = water_plan(800, evaporation_gph=0.6, spray_gph=1.5, hours_per_day=10, water_temp_f=86)
    v = r.value
    assert v["warm"] and v["hygiene_days"] == 14 and v["refresh_days"] <= 14
    assert v["chlorinator_feed_gph"] == chlorinator_feed(800, 12.5).value
    assert any(c["code"] == "upkeep.legionella_band" for c in v["checks"])
    sched = maintenance_schedule(v, outdoor=True, frost=True, filters=1, uv=True, lights=4, nozzles=12, run_hours_year=3650)
    tasks = [t["task"] for t in sched]
    assert "Winterize" in tasks and "Replace the UV lamp" in tasks and any("Drain, clean" in t for t in tasks)


def test_a_pond_or_lake_is_never_drained():
    """The drain-and-refill rule is for recirculating features: a pond or lake (or anything a floating
    aerator sits in) takes make-up only (Sapphire standard 2026-11-01, phase G review)."""
    from erpnext_enhancements.water_engineering.engine.upkeep import natural_water

    assert natural_water("Pond / Lake") and natural_water("Retention lake") and natural_water("", ["jets", "aerator"])
    assert not natural_water("Decorative Fountain", ["weir"]) and not natural_water(None)
    plan = water_plan(1_955_000, evaporation_gph=45.0, spray_gph=0.0, hours_per_day=12, water_temp_f=72, natural=True).value
    assert plan["natural"] and plan["refresh_days"] is None and plan["refresh_by"] == "never"
    assert any(c["code"] == "upkeep.refresh" and "never drained" in c["title"] for c in plan["checks"])
    om = om_per_year(plan, [], volume_gal=1_955_000).value
    assert om["refills"] == 0
    assert math.isclose(om["water_gal"], plan["makeup_gpd"] * 365, rel_tol=1e-9)
    tasks = [t["task"] for t in maintenance_schedule(plan, outdoor=True, frost=True)]
    assert not any("Drain, clean" in t for t in tasks)
    # A recirculating feature is unchanged: it still refills.
    fountain = water_plan(800, evaporation_gph=0.6, spray_gph=1.5, hours_per_day=10, water_temp_f=68).value
    assert fountain["natural"] is False and om_per_year(fountain, [], volume_gal=800).value["refills"] > 0


def test_om_per_year_by_hand():
    plan = water_plan(1000, evaporation_gph=1.0, spray_gph=2.0, hours_per_day=10, water_temp_f=68).value
    pumps = [{"flow_gpm": 100, "head_ft": 40, "efficiency_pct": 65, "motor_eff_pct": 88, "hours_per_day": 10, "electrical_kw": 1.36}]
    rules = {"kwh_rate": 0.12, "water_rate_per_ccf": 4.0, "chlorine_per_gal": 5.0, "days_per_year": 365}
    r = om_per_year(plan, pumps, volume_gal=1000, rules=rules, equipment_cost=20000)
    v = r.value
    refills = 365 / plan["refresh_days"]
    water_gal = plan["makeup_gpd"] * 365 + refills * 1000
    assert math.isclose(v["water"], water_gal / 748.051948 * 4.0, rel_tol=1e-12)
    ec = electric_cost(100, 40, hours_per_day=10, rate_per_kwh=0.12, pump_eff=0.65, motor_eff=0.88).value
    assert math.isclose(v["power"], ec, rel_tol=1e-12)
    assert math.isclose(v["power_crosscheck"], 1.36 * 10 * 365 * 0.12, rel_tol=1e-12)
    assert math.isclose(v["parts"], 600.0) and math.isclose(v["total"], v["water"] + v["power"] + v["chemicals"] + v["parts"])


def test_water_plan_without_evaporation_is_strict_json():
    import json

    r = water_plan(500, evaporation_gph=0, spray_gph=1.0)
    assert r.value["mineral_days"] is None and r.value["refresh_days"] == 30
    json.dumps(r.to_dict(), allow_nan=False)
