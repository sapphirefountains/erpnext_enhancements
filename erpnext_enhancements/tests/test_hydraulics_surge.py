"""engine/hydraulics/surge.py against DRC transient.js / presets.js (tests/golden/drc/surge.json, 1e-12),
and the envelope against engine/safety.water_hammer as its cross-check.

Bench-free pytest suite: python -m pytest erpnext_enhancements/tests/test_hydraulics_surge.py -q
"""

from erpnext_enhancements.tests.drc_golden import REL, close, golden, manifold_from_drc, seg_from_drc
from erpnext_enhancements.water_engineering.engine.hydraulics.surge import (
    accumulator_gas_l_si,
    manifold_wave_si,
    std_gas_l,
    supply_wave_si,
    surge,
    surge_for_si,
)
from erpnext_enhancements.water_engineering.engine.hydraulics.water import water_si
from erpnext_enhancements.water_engineering.engine.safety import water_hammer

GOLD = golden("surge")


def _line(sc):
    """(supply segments, manifold) as DRC sees them: the simple model has no supply line."""
    segs = [seg_from_drc(s) for s in sc["supply"]] if sc["model"] == "system" else []
    return segs, manifold_from_drc(sc["manifold"])


def test_waves():
    for sc in GOLD["scenarios"]:
        w = water_si(sc["waterC"])
        segs, m = _line(sc)
        mw = manifold_wave_si(m, w)
        for k in ("a", "L", "A"):
            close(getattr(mw, k), sc["manifoldWave"][k], REL, f"{sc['name']} manifold {k}")
        sw = supply_wave_si(segs, w, mw)
        for k in ("a", "L", "A"):
            close(getattr(sw, k), sc["supplyWave"][k], REL, f"{sc['name']} supply {k}")


def test_surge_for():
    for sc in GOLD["scenarios"]:
        w = water_si(sc["waterC"])
        segs, m = _line(sc)
        for row in sc["surge"]:
            got = surge_for_si(row["dQ"], w, row["tc"], segs, m)
            want = row["out"]
            what = f"{sc['name']} dQ={row['dQ']} tc={row['tc']}"
            close(got["pa"], want["pa"], REL, what + " pa")
            close(got["supply_pa"], want["supplyPa"], REL, what + " supplyPa")
            close(got["manifold_pa"], want["manifoldPa"], REL, what + " manifoldPa")
            close(got["du"], want["dU"], REL, what + " dU")
            close(got["a"], want["a"], REL, what + " a")
            close(got["t_wave"], want["tWave"], REL, what + " tWave")
            assert got["slow"] == want["slow"], what


def test_accumulator_gas():
    for sc in GOLD["scenarios"]:
        w = water_si(sc["waterC"])
        segs, m = _line(sc)
        for row in sc["acc"]:
            close(accumulator_gas_l_si(row["dQ"], row["pOp"], row["allow"], w, segs, m), row["L"], REL, f"{sc['name']} {row}")


def test_stock_gas_sizes():
    for row in GOLD["gas"]:
        assert std_gas_l(row["need"]) == row["L"], row


def test_envelope_crosschecks_the_workbook_water_hammer():
    supply = [{"material": "SCH40 PVC", "nominal_size": '2"', "length_ft": 120}]
    r = surge(80, supply, closure_ms=5, operating_psi=40, rating_psi=166)
    v = r.value
    # Same Joukowsky, two wave speeds: the Korteweg one from the 2" Sch 40 wall and the 1300 ft/s table.
    assert 1250 < v["wave_speed_fps"] < 1500
    table = water_hammer(v["velocity_change_fps"], 120, closure_time_s=0.005, material="SCH40 PVC")
    assert v["crosscheck_psi"] == table.value
    assert abs(v["surge_psi"] / table.value - v["wave_speed_fps"] / 1300) < 0.01
    assert v["peak_psi"] == 40 + v["surge_psi"]
    # Over the rating -> an arrestor from the stock sizes, precharged under the running pressure.
    assert v["peak_psi"] > 166 and v["arrestor_gas_l"] > 0 and v["precharge_psi"] == 32
    slow = surge(80, supply, closure_ms=2000, operating_psi=40, rating_psi=166).value
    assert slow["slow_closure"] and slow["surge_psi"] < v["surge_psi"] / 10 and slow["arrestor_gas_l"] == 0
