"""engine/hydraulics/operating_point.py against DRC flow.js operatingPoint / systemCurve / bypassFlow /
npshaM (tests/golden/drc/operating_point.json: 1e-9 for the solves, 1e-12 for closed forms), plus hand
goldens for the open-discharge, fixed-flow and parallel terminals and the site-elevation NPSHa.

Bench-free pytest suite: python -m pytest erpnext_enhancements/tests/test_hydraulics_operating_point.py -q
"""

import math
from dataclasses import replace

import pytest

from erpnext_enhancements.tests.drc_golden import REL, SOLVE, close, golden, pump_from_drc, seg_from_drc
from erpnext_enhancements.water_engineering.engine.hydraulics.catalog import pipe_spec_si
from erpnext_enhancements.water_engineering.engine.hydraulics.operating_point import (
    STATUS_CANNOT_LIFT,
    System,
    bypass_flow,
    bypass_flow_si,
    npsha_si,
    operating_point,
    operating_point_si,
    pump_curve,
    site_atmosphere,
    system_curve,
    system_curve_si,
)
from erpnext_enhancements.water_engineering.engine.hydraulics.pump import PumpCurve, pump_head_si
from erpnext_enhancements.water_engineering.engine.hydraulics.segment import Segment, supply_loss_si
from erpnext_enhancements.water_engineering.engine.hydraulics.terminals import (
    FixedFlowDemand,
    OpenDischargeDemand,
    OrificeDemand,
    ParallelDemand,
)
from erpnext_enhancements.water_engineering.engine.hydraulics.units import M_PER_FT, PATM, G
from erpnext_enhancements.water_engineering.engine.hydraulics.water import water_si
from erpnext_enhancements.water_engineering.engine.safety import _atm_head_ft

GOLD = golden("operating_point")

KEYS = {  # OpPoint field -> DRC operatingPoint key
    "q": "Q", "q_pump": "Qpump", "q_bypass": "qBypass", "p_in": "pIn", "p_discharge": "pDischarge",
    "static_pa": "staticPa", "loss_supply_pa": "lossSupplyPa", "prv_drop_pa": "prvDropPa", "head_m": "headM",
    "speed": "speed", "set_pa": "setPa", "eff": "eff", "power_hyd_w": "powerHydW", "power_shaft_w": "powerShaftW",
    "power_elec_w": "powerElecW", "npsha": "npsha", "npshr": "npshr", "npsh_margin": "npshMargin",
}


def system_of(sc: dict) -> System:
    i = sc["in"]
    return System(
        pump=pump_from_drc(i["pump"], i["legacy"], i["waterC"]),
        w=water_si(i["waterC"]),
        supply=tuple(seg_from_drc(s) for s in i["supply"]),
        suction=seg_from_drc({**i["suction"], "share": "all"}),
        static_m=i["staticM"],
        lift_m=i["suction"].get("liftM") or 0,
        clog=(i["clogPct"] or 0) / 100,
        control=i["control"],
        set_pa=i["setPa"],
        bypass_d_m=(i["bypassMm"] or 0) / 1000,
    )


def demand_of(d: dict) -> OrificeDemand:
    return OrificeDemand(d["G"], d["pg"], d["anyOpen"])


def check_op(op, want: dict, what: str):
    assert op.control == want["control"], f"{what}.control"
    assert op.dead_head == want["deadHead"], f"{what}.deadHead"
    assert op.cannot_lift == want["cannotLift"], f"{what}.cannotLift"
    for py, js in KEYS.items():
        close(getattr(op, py), want[js], SOLVE, f"{what}.{js}")


@pytest.mark.parametrize("sc", GOLD["scenarios"], ids=[s["name"] for s in GOLD["scenarios"]])
def test_operating_point_matches_drc(sc):
    system = system_of(sc)
    out = sc["out"]
    for d, want in zip(sc["demands"], out["ops"], strict=False):
        check_op(operating_point_si(system, demand_of(d)), want, f"{sc['name']}/{d['name']}")
    check_op(operating_point_si(system, demand_of(sc["demands"][0]), 0.8), out["opSpeed80"], f"{sc['name']}/speed80")
    for row in out["bypass"]:
        close(bypass_flow_si(system.bypass_d_m, row["pd"], system.w), row["q"], REL, f"{sc['name']} bypass({row['pd']})")
    for row in out["npsha"]:
        close(npsha_si(system, row["Q"]), row["h"], REL, f"{sc['name']} npsha({row['Q']})")
    for key, demand, q_max in (("systemCurve", sc["demands"][0], None), ("systemCurveQ30", sc["demands"][0], 30 / 60000),
                               ("systemCurveNone", sc["demands"][2], None)):
        got = system_curve_si(system, demand_of(demand), 8, q_max)
        assert len(got) == len(out[key]), f"{sc['name']} {key} length"
        for g, w in zip(got, out[key], strict=False):
            close(g["Q"], w["Q"], SOLVE, f"{sc['name']} {key}.Q")
            close(g["headM"], w["headM"], SOLVE, f"{sc['name']} {key}.headM")


def test_scenarios_cover_the_branches():
    flat = [op for sc in GOLD["scenarios"] for op in sc["out"]["ops"]]
    assert any(op["cannotLift"] for op in flat) and any(op["deadHead"] for op in flat)
    assert any(op["prvDropPa"] > 0 for op in flat) and any(0.15 <= op["speed"] < 1 for op in flat)
    assert any(op["qBypass"] > 0 for op in flat)
    assert any(abs(op["speed"] - 0.15) < 1e-12 for op in flat)  # the VFD floor


# ---------------------------------------------------------------------------
# Hand goldens: a quadratic pump H = 30 (1 - (Q/0.01)^2) m on a 10 m static lift, no pipe loss.
# ---------------------------------------------------------------------------
W = water_si(20)
RG = W.rho * G
QUAD = PumpCurve(quad=(30.0, 0.01))
BASE = System(pump=QUAD, w=W, static_m=10.0)


def test_open_discharge_hand_golden():
    """Overflow 2 m above the inlet: 30 (1 - r^2) = 12  ->  Q = 0.01 sqrt(0.6)."""
    op = operating_point_si(BASE, OpenDischargeDemand(2.0, W.rho))
    assert op.open_discharge and not op.cannot_lift
    close(op.q, 0.01 * math.sqrt(0.6), SOLVE, "Q")
    close(op.p_in, RG * 2.0, REL, "pIn pinned at the lift")
    close(op.head_m, 12.0, SOLVE, "head")


def test_open_discharge_with_pipe_balances_the_residual():
    seg = Segment(spec=pipe_spec_si("SCH40 PVC", '2"'), length_m=30, fittings=(("elbow_90", 4),))
    system = replace(BASE, supply=(seg,))
    op = operating_point_si(system, OpenDischargeDemand(2.0, W.rho))
    lhs = RG * pump_head_si(QUAD, op.q_pump)
    rhs = RG * 10 + supply_loss_si((seg,), op.q, W) + RG * 2.0
    assert abs(lhs - rhs) <= 1e-9 * rhs


def test_stiff_orifice_approaches_open_discharge():
    """An orifice with a large conductance behaves like the overflow. (A truly stiff one is why the open
    terminal is solved on flow: with G = 1 the 1e-6 Pa pressure tolerance alone is 6.5e-5 m^3/s.)"""
    open_op = operating_point_si(BASE, OpenDischargeDemand(2.0, W.rho))
    stiff = operating_point_si(BASE, OrificeDemand(1e-3, -RG * 2.0))
    assert abs(stiff.q - open_op.q) / open_op.q < 1e-3


def test_open_discharge_cannot_lift():
    op = operating_point_si(BASE, OpenDischargeDemand(25.0, W.rho))
    assert op.cannot_lift and op.q == 0


def test_fixed_flow_hand_golden():
    """Q fixed at 0.005: head 30 * 0.75 = 22.5 m, inlet 12.5 m."""
    op = operating_point_si(BASE, FixedFlowDemand(0.005))
    assert not op.starved
    close(op.q, 0.005, REL, "Q")
    close(op.p_in, RG * 12.5, SOLVE, "pIn")


def test_fixed_flow_starved_hand_golden():
    """Minimum 15 m at the inlet: 30 (1 - r^2) = 25 -> Q = 0.01 / sqrt(6) < 0.005."""
    op = operating_point_si(BASE, FixedFlowDemand(0.005, RG * 15))
    assert op.starved
    close(op.q, 0.01 / math.sqrt(6), SOLVE, "Q")
    close(op.p_in, RG * 15, REL, "pIn")


def test_parallel_orifices_equal_one_lumped_orifice():
    """One pump, N parallel terminals on one node: the degenerate case phase B's network must match."""
    seg = Segment(spec=pipe_spec_si("SCH40 PVC", '1-1/2"'), length_m=12)
    system = replace(BASE, supply=(seg,))
    lumped = operating_point_si(system, OrificeDemand(4e-5, 100))
    both = operating_point_si(system, ParallelDemand([OrificeDemand(1e-5, 100), OrificeDemand(3e-5, 100)]))
    for k in ("q", "p_in", "head_m", "p_discharge"):
        close(getattr(both, k), getattr(lumped, k), 1e-12, k)


def test_parallel_open_and_orifice():
    open_ = OpenDischargeDemand(2.0, W.rho)
    orifice = OrificeDemand(2e-5, 0)
    node = ParallelDemand([orifice, open_])
    op = operating_point_si(BASE, node)
    assert op.open_discharge
    close(op.q, 0.01 * math.sqrt(0.6), SOLVE, "total flow is set by the pinned node")
    flows = node.split(op.p_in, op.q)
    close(flows[0], orifice.q(RG * 2.0), REL, "orifice share")
    # A node that cannot reach the overflow: only the orifice flows.
    low = operating_point_si(BASE, ParallelDemand([OrificeDemand(1e-3, 0), OpenDischargeDemand(19.0, W.rho)]))
    assert not low.open_discharge and low.p_in < RG * 19.0


def test_vfd_holds_the_set_pressure():
    """Uncontrolled the orifice sees 6.4 kPa; the drive slows the pump until it sees 0.3 m (2.9 kPa)."""
    system = replace(BASE, control="vfd", set_pa=RG * 0.3)
    op = operating_point_si(system, OrificeDemand(1e-4, 0))
    close(op.p_in, RG * 0.3, REL, "pIn")
    assert 0.15 < op.speed < 1
    close(RG * pump_head_si(QUAD, op.q_pump, op.speed), RG * 10 + RG * 0.3, 1e-9, "pump meets the set point")


def test_npsha_takes_the_site_elevation():
    """Salt Lake City: 4,300 ft takes about 4.9 ft off the atmosphere that DRC's sea-level PATM keeps."""
    sea = operating_point(QUAD_US, TERMINAL, static_ft=20, elevation_ft=0)
    slc = operating_point(QUAD_US, TERMINAL, static_ft=20, elevation_ft=4300)
    drop = sea.value["npsha_ft"] - slc.value["npsha_ft"]
    # The barometric model is safety._atm_head_ft's; the head is taken at the water's own rho g.
    atm_drop_pa = site_atmosphere(0, 68)[0] - site_atmosphere(4300, 68)[0]
    assert abs(drop - atm_drop_pa / (W.rho * G) / M_PER_FT) < 1e-9
    assert abs(drop - (_atm_head_ft(0) - _atm_head_ft(4300))) < 0.01  # FT_PER_PSI is 60 F water
    assert 4.5 < drop < 5.3
    atm, pv = site_atmosphere(0, 68)
    assert abs(atm - PATM) < 5  # 14.696 psia vs 101325 Pa
    assert any("sea-level" in s for s in slc.steps)


QUAD_US = PumpCurve.from_us(
    [{"flow_gpm": 0, "head_ft": 60, "efficiency_pct": 0, "npshr_ft": 3}, {"flow_gpm": 50, "head_ft": 55, "efficiency_pct": 55, "npshr_ft": 5},
     {"flow_gpm": 100, "head_ft": 42, "efficiency_pct": 68, "npshr_ft": 8}, {"flow_gpm": 150, "head_ft": 20, "efficiency_pct": 55, "npshr_ft": 13}]
)
TERMINAL = {"kind": "open_discharge", "params": {"lift_ft": 2, "weir_length_ft": 8, "weir_head_in": 0.25}}
SUPPLY = [{"label": "Discharge", "material": "SCH40 PVC", "nominal_size": '2"', "length_ft": 60, "fittings": [{"type": "ELL 90", "qty": 6}]}]
SUCTION = {"label": "Suction", "material": "SCH40 PVC", "nominal_size": '2-1/2"', "length_ft": 10,
           "fittings": [{"type": "ENTRANCE FLUSH", "qty": 1}, {"type": "ELL 90", "qty": 1}]}


def test_operating_point_envelope():
    r = operating_point(QUAD_US, TERMINAL, supply=SUPPLY, suction=SUCTION, static_ft=8, suction_lift_ft=-3, elevation_ft=4300)
    v = r.value
    assert r.status == "Okay" and r.formula and r.citations and len(r.steps) >= 4
    assert v["open_discharge"] and v["flow_gpm"] > 0 and v["flow_gpm"] == v["pump_flow_gpm"]
    # The weir wants 36*8*0.25^1.5 - 0.3*2*0.25^2.5 = 36 - 0.01875 = 35.98 GPM; this pump pushes far more.
    assert abs(v["design_flow_gpm"] - 35.98125) < 1e-9
    assert v["safety_flow_gpm"] == v["flow_gpm"] > v["design_flow_gpm"]
    assert any("operating flow" in w for w in r.warnings)
    # Head balance in US units.
    assert abs(v["head_ft"] - (v["static_ft"] + v["supply_loss_ft"] + 2.0)) < 1e-6
    assert abs(v["shutoff_head_ft"] - 60) < 1e-9


def test_envelope_statuses():
    assert operating_point(QUAD_US, TERMINAL, static_ft=70).status == STATUS_CANNOT_LIFT
    marginal = operating_point(QUAD_US, TERMINAL, suction=SUCTION, static_ft=8, suction_lift_ft=12, elevation_ft=4300)
    assert marginal.status in ("Marginal", "Cavitation Risk") and marginal.warnings
    assert operating_point(QUAD_US, TERMINAL, control="prv").warnings  # no set pressure


def test_curve_and_bypass_envelopes():
    sc = system_curve(TERMINAL, pump=QUAD_US, supply=SUPPLY, static_ft=8, n=10)
    assert len(sc.value) == 10 and abs(sc.value[0]["head_ft"] - 10) < 1e-9
    pc = pump_curve(QUAD_US, n=5)
    assert abs(pc.value[0]["head_ft"] - 60) < 1e-9 and pc.value[-1]["head_ft"] < 1e-6
    b = bypass_flow(0.5, 20)
    # 0.61 * 1.2668e-4 m^2 * sqrt(2 * 137,895 Pa / 998.2) = 1.284e-3 m^3/s = 20.4 GPM
    assert 20.2 < b.value < 20.5 and b.citations


def test_starved_fixed_flow_inside_a_parallel_node():
    alone = operating_point_si(BASE, FixedFlowDemand(0.005, RG * 15))
    inside = operating_point_si(BASE, ParallelDemand([FixedFlowDemand(0.005, RG * 15)]))
    assert inside.starved and alone.starved
    close(inside.q, alone.q, SOLVE, "Q")


def test_hot_water_vapor_pressure_is_not_clamped():
    """The workbook table stops at 120 F; above it the Antoine figure is used (140 F: 2.89 psia)."""
    _, pv_120 = site_atmosphere(0, 120)
    _, pv_140 = site_atmosphere(0, 140)
    assert abs(pv_120 / 6894.757 - 1.692) < 1e-9
    assert 2.8 < pv_140 / 6894.757 < 3.0


def test_pump_and_system_are_hashable():
    system = replace(BASE, supply=(Segment(spec=pipe_spec_si("SCH40 PVC", '2"'), length_m=3, fittings=(("elbow_90", 1),)),))
    assert hash(QUAD) and hash(system) and {QUAD: 1}[QUAD] == 1
