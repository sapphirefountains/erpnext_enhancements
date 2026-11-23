"""engine/hydraulics/pump.py against DRC flow.js (tests/golden/drc/pump.json): interpolation at 1e-12,
run-out and near-shut-off power (bisections) at 1e-9; the US builder and the envelopes.

Bench-free pytest suite: python -m pytest erpnext_enhancements/tests/test_hydraulics_pump.py -q
"""

from erpnext_enhancements.tests.drc_golden import REL, SOLVE, close, golden, num, pump_from_drc
from erpnext_enhancements.water_engineering.engine.hydraulics.pump import (
    PumpCurve,
    affinity,
    bep_flow,
    bep_flow_si,
    clean,
    interp,
    max_flow,
    max_flow_si,
    npshr,
    npshr_si,
    pump_curve_si,
    pump_eff,
    pump_eff_si,
    pump_head,
    pump_head_si,
    shaft_power,
    shaft_power_si,
)
from erpnext_enhancements.water_engineering.engine.hydraulics.water import water_si

G = golden("pump")


def test_interp_matches_drc():
    for row in G["interp"]:
        close(interp(row["pts"], num(row["x"])), row["y"], REL, f"interp({row['pts']}, {row['x']})")


def test_clean_matches_drc():
    for row in G["pumps"]:
        for k in ("curve", "eff", "npshr"):
            assert clean(row["pump"].get(k)) == [tuple(p) for p in row["out"]["clean"][k]], (row["name"], k)


def test_pump_functions_match_drc():
    assert len(G["pumps"]) >= 10
    for row in G["pumps"]:
        p = pump_from_drc(row["pump"], row["legacy"], row["waterC"])
        w = water_si(row["waterC"])
        speed = p.default_speed
        n = row["name"]
        for g in row["out"]["grid"]:
            q = g["Q"]
            close(pump_head_si(p, q), g["head"], REL, f"{n} head({q})")
            close(pump_head_si(p, q, 0.5), g["head05"], REL, f"{n} head05({q})")
            close(pump_head_si(p, q, 0), g["head0"], REL, f"{n} head0({q})")
            close(pump_eff_si(p, q), g["eff"], REL, f"{n} eff({q})")
            close(pump_eff_si(p, q, 0.5), g["eff05"], REL, f"{n} eff05({q})")
            close(npshr_si(p, q), g["npshr"], REL, f"{n} npshr({q})")
            close(npshr_si(p, q, 0.5), g["npshr05"], REL, f"{n} npshr05({q})")
            close(shaft_power_si(p, q, speed, w), g["shaft"], SOLVE, f"{n} shaft({q})")
            close(shaft_power_si(p, q, 0.5, w), g["shaft05"], SOLVE, f"{n} shaft05({q})")
        close(bep_flow_si(p), row["out"]["bep"][0], REL, f"{n} bep")
        close(bep_flow_si(p, 0.5), row["out"]["bep"][1], REL, f"{n} bep05")
        close(max_flow_si(p), row["out"]["maxFlow"][0], SOLVE, f"{n} maxFlow")
        close(max_flow_si(p, 0.5), row["out"]["maxFlow"][1], SOLVE, f"{n} maxFlow05")
        for key, spd, cnt in (("pumpCurve", speed, 9), ("pumpCurve05", 0.5, 9), ("pumpCurveN1", speed, 1)):
            got = pump_curve_si(p, spd, cnt)
            assert len(got) == len(row["out"][key]), (n, key)
            for gpt, wpt in zip(got, row["out"][key], strict=False):
                close(gpt["Q"], wpt["Q"], SOLVE, f"{n} {key}.Q")
                close(gpt["headM"], wpt["headM"], SOLVE, f"{n} {key}.headM")


CURVE_US = [
    {"flow_gpm": 0, "head_ft": 100, "efficiency_pct": 0, "npshr_ft": 4},
    {"flow_gpm": 100, "head_ft": 95, "efficiency_pct": 55, "npshr_ft": 5},
    {"flow_gpm": 200, "head_ft": 80, "efficiency_pct": 72, "npshr_ft": 7},
    {"flow_gpm": 300, "head_ft": 55, "efficiency_pct": 65, "npshr_ft": 11},
]


def test_from_us_round_trips_the_curve():
    p = PumpCurve.from_us(CURVE_US)
    assert abs(pump_head(p, 150).value - 87.5) < 1e-9
    assert abs(pump_eff(p, 150).value - 63.5) < 1e-9
    assert abs(npshr(p, 150).value - 6.0) < 1e-9
    assert abs(bep_flow(p).value - 200) < 1e-9
    # Run-out: past 300 GPM the last segment (-0.25 ft/GPM) extrapolates to 0 at 520 GPM.
    assert abs(max_flow(p).value - 520) < 1e-6


def test_affinity_laws_on_the_curve():
    p = PumpCurve.from_us(CURVE_US)
    # At 80% speed the duty point (200 GPM, 80 ft) moves to (160 GPM, 51.2 ft).
    assert abs(pump_head(p, 160, speed_pct=80).value - 80 * 0.64) < 1e-9
    assert abs(pump_eff(p, 160, speed_pct=80).value - 72) < 1e-9
    a = affinity(200, 80, 5, 0.8).value
    assert abs(a["flow_gpm"] - 160) < 1e-12 and abs(a["head_ft"] - 51.2) < 1e-12 and abs(a["power_hp"] - 2.56) < 1e-12
    assert affinity(200, 80, 5, 0.5).warnings


def test_shaft_power_hand_value():
    """200 GPM at 80 ft and 72%: rho g Q H / eta = 998.2 * 9.80665 * 0.012618 m^3/s * 24.384 m / 0.72
    = 4,183 W = 5.61 hp."""
    p = PumpCurve.from_us(CURVE_US)
    r = shaft_power(p, 200)
    assert abs(r.value - 5.61) < 0.01 and r.steps and r.citations


def test_quadratic_generic_estimate():
    p = PumpCurve.from_us([], shutoff_head_ft=60, max_flow_gpm=100)
    assert abs(pump_head(p, 0).value - 60) < 1e-9 and abs(pump_head(p, 50).value - 45) < 1e-9
    assert abs(max_flow(p).value - 100) < 1e-9
    assert pump_head(p, 50).warnings
    assert pump_head(PumpCurve.from_us([]), 50).value == 0


def test_envelopes_warn_without_data():
    p = PumpCurve.from_us([{"flow_gpm": 0, "head_ft": 50}, {"flow_gpm": 100, "head_ft": 30}])
    assert pump_eff(p, 50).warnings and npshr(p, 50).warnings and bep_flow(p).warnings
    assert pump_head(p, 150).warnings  # extrapolated
    for r in (pump_head(p, 50), max_flow(p)):
        assert r.formula and r.steps and r.citations
