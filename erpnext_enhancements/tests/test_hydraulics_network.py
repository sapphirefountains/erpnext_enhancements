"""engine/hydraulics/network.py: the junction-balancing solver (no JS reference).

* The degenerate case, one pump feeding parallel terminals, must reproduce phase A's
  operating_point to 1e-9 (plain, bypass, VFD, PRV, part speed, open discharge, starved fixed flow).
* Hand goldens: two identical pumps on a header equal one pump with its flow axis doubled; two
  unequal pumps match an independent nested-bisection solve; a symmetric ring main equals its
  symmetric tree; every solution closes continuity at each node and head around each loop.
* The pump states the solver must report: backflow without a check valve, a dead-headed pump
  held shut by its check valve, run-out past the curve.

Bench-free pytest suite: python -m pytest erpnext_enhancements/tests/test_hydraulics_network.py -q
"""

import math

import pytest

from erpnext_enhancements.water_engineering.engine.hydraulics.network import (
    build_network,
    network_operating_point,
    solve_network,
)
from erpnext_enhancements.water_engineering.engine.hydraulics.operating_point import operating_point
from erpnext_enhancements.water_engineering.engine.hydraulics.pump import PumpCurve, pump_head_si
from erpnext_enhancements.water_engineering.engine.hydraulics.segment import Segment, segment_loss_si
from erpnext_enhancements.water_engineering.engine.hydraulics.terminals import build_demand
from erpnext_enhancements.water_engineering.engine.hydraulics.units import (
    M_PER_FT,
    G,
    f_to_c,
    gpm_to_m3s,
    m3s_to_gpm,
)
from erpnext_enhancements.water_engineering.engine.hydraulics.water import water_si

SOLVE = 1e-9

PUMP = [
    {"flow_gpm": 0, "head_ft": 60, "efficiency_pct": 0, "npshr_ft": 3},
    {"flow_gpm": 40, "head_ft": 56, "efficiency_pct": 55, "npshr_ft": 4},
    {"flow_gpm": 80, "head_ft": 46, "efficiency_pct": 68, "npshr_ft": 6},
    {"flow_gpm": 120, "head_ft": 28, "efficiency_pct": 60, "npshr_ft": 9},
    {"flow_gpm": 150, "head_ft": 8, "efficiency_pct": 40, "npshr_ft": 13},
]
WEAK = [{"flow_gpm": 0, "head_ft": 50}, {"flow_gpm": 60, "head_ft": 42}, {"flow_gpm": 120, "head_ft": 15}]
SUPPLY = [
    {"id": "s1", "material": "SCH40 PVC", "nominal_size": '2"', "length_ft": 40,
     "fittings": [{"key": "elbow_90", "qty": 4}, {"key": "ball", "qty": 1}]},
    {"id": "s2", "material": "SCH40 PVC", "nominal_size": '1-1/2"', "length_ft": 12, "fittings": [{"key": "tee_branch", "qty": 1}]},
]
ORIFICES = {"kind": "parallel", "params": {"members": [
    {"kind": "orifice", "params": {"rated_gpm": 10, "rated_head_ft": 20, "count": 3}},
    {"kind": "orifice", "params": {"cd": 0.9, "orifice_diameter_in": 0.75, "count": 2, "below_ft": 1}},
]}}
SITE = {"water_temp_f": 70, "elevation_ft": 4300}


def rel(a, b):
    return abs(a - b) / max(abs(a), abs(b), 1e-300)


def chain(term, static_ft=8.0, lift_ft=-1.0, bypass_in=0.0, control="none", set_psi=None, speed=None):
    """One pump drawing straight from the basin (no suction segment: DRC's model) through SUPPLY."""
    pump = {"id": "p1", "curve": PUMP, "source": "src", "bypass_in": bypass_in, "control": control, "setpoint_psi": set_psi}
    if speed:
        pump["speed_pct"] = speed
    return {
        "nodes": [{"id": "src", "kind": "basin", "elevation_ft": 0}, {"id": "p1", "kind": "pump", "elevation_ft": lift_ft},
                  {"id": "n1", "kind": "junction", "elevation_ft": static_ft}, {"id": "t1", "kind": "terminal", "elevation_ft": static_ft}],
        "pumps": [pump],
        "segments": [dict(SUPPLY[0], **{"from": "p1", "to": "n1"}), dict(SUPPLY[1], **{"from": "n1", "to": "t1"})],
        "terminals": [dict(term, id="t1")],
    }


DEGENERATE = [
    ("orifices", ORIFICES, {}),
    ("bypass", ORIFICES, {"bypass_in": 0.5}),
    ("vfd", ORIFICES, {"control": "vfd", "set_psi": 5}),
    ("vfd + bypass", ORIFICES, {"control": "vfd", "set_psi": 4, "bypass_in": 0.4}),
    ("prv", ORIFICES, {"control": "prv", "set_psi": 5}),
    ("prv + bypass", ORIFICES, {"control": "prv", "set_psi": 5, "bypass_in": 0.4}),
    ("speed 80", ORIFICES, {"speed": 80}),
    ("suction lift", ORIFICES, {"lift_ft": 4}),
    ("open discharge", {"kind": "open_discharge", "params": {"lift_ft": 2, "design_gpm": 60}}, {}),
    ("open + orifice", {"kind": "parallel", "params": {"members": [{"kind": "open_discharge", "params": {"lift_ft": 3}},
                                                                    {"kind": "orifice", "params": {"rated_gpm": 10, "rated_head_ft": 2}}]}}, {}),
    ("fixed flow held", {"kind": "fixed_flow", "params": {"flow_gpm": 50, "min_psi": 5}}, {}),
    ("fixed flow starved", {"kind": "fixed_flow", "params": {"flow_gpm": 100, "min_psi": 10}}, {}),
    ("manifold", {"kind": "manifold", "params": {"material": "SCH40 PVC", "nominal_size": '1-1/2"', "count": 16, "pitch_in": 6,
                                                  "rated_gpm": 3, "rated_head_ft": 20}}, {}),
]


@pytest.mark.parametrize("name,term,kw", DEGENERATE, ids=[d[0] for d in DEGENERATE])
def test_one_pump_matches_operating_point(name, term, kw):
    res = solve_network(chain(term, **kw), **SITE)
    assert res.converged, name
    op = operating_point(PUMP, term, supply=SUPPLY, static_ft=kw.get("static_ft", 8.0), suction_lift_ft=kw.get("lift_ft", -1.0),
                         bypass_in=kw.get("bypass_in", 0.0), control=kw.get("control", "none"), set_psi=kw.get("set_psi"),
                         speed_pct=kw.get("speed"), **SITE).value
    p, t = res.pumps["p1"], res.terminals["t1"]
    for got, want, what in [
        (t["flow_gpm"], op["flow_gpm"], "flow"), (p["flow_gpm"], op["pump_flow_gpm"], "pump flow"),
        (p["head_ft"], op["head_ft"], "head"), (t["pressure_psi"], op["inlet_psi"], "terminal pressure"),
        (p["speed_pct"], op["speed_pct"], "speed"), (p["npsha_ft"], op["npsha_ft"], "NPSHa"), (p["npshr_ft"], op["npshr_ft"], "NPSHr"),
        (p["bypass_gpm"], op["bypass_gpm"], "bypass"), (p["prv_drop_psi"], op["prv_drop_psi"], "PRV drop"),
        (p["electrical_kw"], op["electrical_kw"], "power"), (p["efficiency_pct"], op["efficiency_pct"], "efficiency"),
    ]:
        assert rel(got, want) <= SOLVE or abs(got - want) < 1e-12, f"{name} {what}: network {got!r} vs operating_point {want!r}"
    assert (t["state"] == "starved") == op["starved"]
    assert res.totals["continuity_gpm"] < 1e-9


def test_cannot_lift_is_a_dead_headed_pump():
    res = solve_network(chain(ORIFICES, static_ft=70), **SITE)
    op = operating_point(PUMP, ORIFICES, supply=SUPPLY, static_ft=70, suction_lift_ft=-1, **SITE).value
    assert op["cannot_lift"] and op["flow_gpm"] == 0
    assert res.converged and abs(res.terminals["t1"]["flow_gpm"]) < 1e-12
    # The pump churns at shut-off (open at zero flow, or held by its check valve: the same state).
    assert res.pumps["p1"]["dead_head"] and res.pumps["p1"]["head_ft"] == pytest.approx(op["head_ft"], rel=1e-12)
    assert any(c["code"] == "pump.dead_head" and c["severity"] == "blocker" for c in res.checks)


# ---------------------------------------------------------------------------
# Hand goldens: several pumps on one header
# ---------------------------------------------------------------------------


def header(pumps, legs, term=ORIFICES, check=True):
    """Pumps drawing from one basin, each through its own leg into header j1, then SUPPLY to t1."""
    nodes = [{"id": "src", "kind": "basin", "elevation_ft": 0}, {"id": "j1", "kind": "junction", "elevation_ft": 1},
             {"id": "n1", "kind": "junction", "elevation_ft": 8}, {"id": "t1", "kind": "terminal", "elevation_ft": 8}]
    segs = [dict(SUPPLY[0], **{"from": "j1", "to": "n1"}), dict(SUPPLY[1], **{"from": "n1", "to": "t1"})]
    prow = []
    for i, (curve, leg) in enumerate(zip(pumps, legs, strict=True)):
        pid = f"p{i + 1}"
        nodes.append({"id": pid, "kind": "pump", "elevation_ft": -1})
        prow.append({"id": pid, "curve": curve, "source": "src", "check_valve": check})
        segs.append(dict(leg, id=f"leg{i + 1}", **{"from": pid, "to": "j1"}))
    return {"nodes": nodes, "pumps": prow, "segments": segs, "terminals": [dict(term, id="t1")]}


LEG = {"material": "SCH40 PVC", "nominal_size": '2"', "length_ft": 6, "fittings": [{"key": "check", "qty": 1}, {"key": "elbow_90", "qty": 2}]}


def doubled(points):
    return [{**p, "flow_gpm": 2 * p["flow_gpm"]} for p in points]


def test_two_identical_pumps_equal_one_doubled_pump():
    """Two identical pumps with identical legs into a header behave as one pump with the flow axis
    doubled feeding the legs as two parallel branches (Segment.branches = 2)."""
    res = solve_network(header([PUMP, PUMP], [LEG, LEG]), **SITE)
    assert res.converged
    op = operating_point(doubled(PUMP), ORIFICES, supply=[dict(LEG, branches=2), *SUPPLY], static_ft=8, suction_lift_ft=-1, **SITE).value
    q1, q2 = res.pumps["p1"]["flow_gpm"], res.pumps["p2"]["flow_gpm"]
    assert rel(q1, q2) < 1e-12
    assert rel(q1 + q2, op["pump_flow_gpm"]) <= SOLVE
    assert rel(res.terminals["t1"]["flow_gpm"], op["flow_gpm"]) <= SOLVE
    assert rel(res.pumps["p1"]["head_ft"], op["head_ft"]) <= SOLVE
    assert rel(res.terminals["t1"]["pressure_psi"], op["inlet_psi"]) <= SOLVE


def _bisect(f, lo, hi, n=200):
    """Root of an increasing f on [lo, hi]."""
    for _ in range(n):
        mid = 0.5 * (lo + hi)
        if f(mid) < 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def test_two_unequal_pumps_match_nested_bisection():
    """An independent reference: bisect the header head until the two pumps' flows (each found by
    bisection on its own curve less its leg loss) equal what the line and orifices take."""
    leg2 = dict(LEG, length_ft=25, nominal_size='1-1/2"')
    res = solve_network(header([PUMP, WEAK], [LEG, leg2]), **SITE)
    assert res.converged
    w = water_si(f_to_c(70))
    rg = w.rho * G
    pumps = [PumpCurve.from_us(PUMP), PumpCurve.from_us(WEAK)]
    legs = [Segment.from_us(LEG), Segment.from_us(leg2)]
    main = [Segment.from_us(s) for s in SUPPLY]
    term = build_demand(ORIFICES, w.rho)
    z_t = 8 * M_PER_FT

    def pump_q(i, h_j):  # flow pump i delivers into header head h_j (basin surface = 0); 0 if it cannot
        def f(q):
            return h_j + segment_loss_si(legs[i], q, w) / rg - pump_head_si(pumps[i], q, 1.0)
        return 0.0 if f(0.0) >= 0 else _bisect(f, 0.0, 0.05)

    def line_q(h_j):  # flow the line and orifices take from header head h_j
        def f(q):
            return z_t + (sum(segment_loss_si(s, q, w) for s in main) + term.p_for(q)) / rg - h_j
        return 0.0 if f(0.0) >= 0 else _bisect(f, 0.0, 0.05)

    h_j = _bisect(lambda h: line_q(h) - pump_q(0, h) - pump_q(1, h), 0.0, 60 * M_PER_FT)
    q1, q2, qt = pump_q(0, h_j), pump_q(1, h_j), line_q(h_j)
    assert q2 > 0, "the weak pump should still contribute in this layout"
    assert rel(res.pumps["p1"]["flow_gpm"], m3s_to_gpm(q1)) <= SOLVE
    assert rel(res.pumps["p2"]["flow_gpm"], m3s_to_gpm(q2)) <= SOLVE
    assert rel(res.terminals["t1"]["flow_gpm"], m3s_to_gpm(qt)) <= SOLVE
    assert rel(res.nodes["j1"]["head_ft"], h_j / M_PER_FT) <= SOLVE


def test_weak_pump_without_check_valve_runs_backwards():
    weak = [{"flow_gpm": 0, "head_ft": 18}, {"flow_gpm": 60, "head_ft": 12}, {"flow_gpm": 120, "head_ft": 2}]
    res = solve_network(header([PUMP, weak], [LEG, LEG], check=False), **SITE)
    assert res.converged
    assert res.pumps["p2"]["backflow"] and res.pumps["p2"]["flow_gpm"] < 0
    assert any(c["code"] == "pump.backflow" and c["ref"] == "p2" for c in res.checks)
    # The same pumps with check valves: the weak one is held shut (dead-headed), the strong one carries on.
    res_cv = solve_network(header([PUMP, weak], [LEG, LEG], check=True), **SITE)
    assert res_cv.pumps["p2"]["check_closed"] and res_cv.pumps["p2"]["dead_head"]
    assert any(c["code"] == "pump.dead_head" and c["ref"] == "p2" for c in res_cv.checks)
    assert res_cv.pumps["p1"]["flow_gpm"] > 0 and not res_cv.pumps["p1"]["backflow"]
    assert rel(res_cv.pumps["p1"]["flow_gpm"], res_cv.terminals["t1"]["flow_gpm"]) < 1e-9


def test_pump_running_out_past_its_curve():
    """A pump discharging downhill into an open basin 20 ft below: gravity alone would drive flow,
    so the pump is pushed past the zero-head end of its curve."""
    net = chain({"kind": "open_discharge", "params": {"lift_ft": 0}}, static_ft=-20.0)
    net["segments"] = [dict(s, nominal_size='3"', fittings=[]) for s in net["segments"]]
    res = solve_network(net, **SITE)
    assert res.converged
    p = res.pumps["p1"]
    assert p["runout"] and p["flow_gpm"] > p["runout_gpm"]
    assert any(c["code"] == "pump.runout" and c["severity"] == "blocker" for c in res.checks)


# ---------------------------------------------------------------------------
# Hand goldens: a ring main fed from two sides
# ---------------------------------------------------------------------------


def _orifice(gpm, head_ft=15, count=1):
    return {"kind": "orifice", "params": {"rated_gpm": gpm, "rated_head_ft": head_ft, "count": count}}


def ring(ab, bc, cd, da, term_b, term_c, term_d):
    pipe = {"material": "SCH40 PVC", "fittings": [{"key": "elbow_90", "qty": 1}]}
    return {
        "nodes": [{"id": "src", "kind": "basin", "elevation_ft": 0}, {"id": "p1", "kind": "pump", "elevation_ft": -1},
                  {"id": "A", "kind": "junction", "elevation_ft": 2}, {"id": "B", "kind": "terminal", "elevation_ft": 2},
                  {"id": "C", "kind": "terminal", "elevation_ft": 2}, {"id": "D", "kind": "terminal", "elevation_ft": 2}],
        "pumps": [{"id": "p1", "curve": PUMP, "source": "src"}],
        "segments": [dict(pipe, id="feed", nominal_size='2"', length_ft=20, **{"from": "p1", "to": "A"}),
                     dict(pipe, id="AB", **ab, **{"from": "A", "to": "B"}), dict(pipe, id="BC", **bc, **{"from": "B", "to": "C"}),
                     dict(pipe, id="CD", **cd, **{"from": "C", "to": "D"}), dict(pipe, id="DA", **da, **{"from": "D", "to": "A"})],
        "terminals": [dict(term_b, id="B"), dict(term_c, id="C"), dict(term_d, id="D")],
    }


def _assert_balanced(net_dict, res):
    """Continuity at every free node and h(Q) = H_from - H_to on every link of the solved network."""
    net = res.si["net"]
    from erpnext_enhancements.water_engineering.engine.hydraulics.network import CLOSED, _h

    bal = [0.0] * len(net.nodes)
    for lk in net.links:
        q = 0.0 if lk.status == CLOSED else lk.q
        bal[lk.a] -= q
        bal[lk.b] += q
        if lk.status != CLOSED:
            drop = net.nodes[lk.a].H - net.nodes[lk.b].H
            assert abs(_h(net, lk, lk.q) - drop) <= 1e-9 * max(1.0, abs(drop)), (lk.kind, lk.ref)
    for i, nd in enumerate(net.nodes):
        if not nd.fixed:
            assert abs(bal[i]) <= 1e-12, nd.id


def test_symmetric_ring_equals_its_tree():
    """By symmetry A->B carries what A->D does and C is fed half from each side, so the ring is the
    tree A ->(2 branches)-> B' ->(2 branches)-> C with B' taking both B and D."""
    side = {"nominal_size": '1-1/2"', "length_ft": 30}
    far = {"nominal_size": '1"', "length_ft": 25}
    r = ring(side, far, far, side, _orifice(12), _orifice(10), _orifice(12))
    res = solve_network(r, **SITE)
    assert res.converged
    _assert_balanced(r, res)
    tree = ring(side, far, far, side, _orifice(12), _orifice(10), _orifice(12))
    pipe = {"material": "SCH40 PVC", "fittings": [{"key": "elbow_90", "qty": 1}]}
    tree["nodes"] = [n for n in tree["nodes"] if n["id"] != "D"]
    tree["segments"] = [tree["segments"][0], dict(pipe, id="AB", branches=2, **side, **{"from": "A", "to": "B"}),
                        dict(pipe, id="BC", branches=2, **far, **{"from": "B", "to": "C"})]
    tree["terminals"] = [dict(_orifice(12, count=2), id="B"), dict(_orifice(10), id="C")]
    res_t = solve_network(tree, **SITE)
    assert res_t.converged
    assert rel(res.terminals["B"]["flow_gpm"], res.terminals["D"]["flow_gpm"]) < 1e-12
    assert rel(res.terminals["B"]["flow_gpm"] + res.terminals["D"]["flow_gpm"], res_t.terminals["B"]["flow_gpm"]) <= SOLVE
    assert rel(res.terminals["C"]["flow_gpm"], res_t.terminals["C"]["flow_gpm"]) <= SOLVE
    assert rel(res.segments["AB"]["flow_gpm"], res_t.segments["AB"]["flow_gpm"] / 2) <= SOLVE
    # C is fed from both sides: B->C and D->C (CD runs C->D, so its flow is negative).
    assert rel(res.segments["BC"]["flow_gpm"], -res.segments["CD"]["flow_gpm"]) < 1e-9
    assert rel(res.pumps["p1"]["flow_gpm"], res_t.pumps["p1"]["flow_gpm"]) <= SOLVE


def test_asymmetric_ring_closes_continuity_and_loop_head():
    r = ring({"nominal_size": '2"', "length_ft": 15}, {"nominal_size": '1"', "length_ft": 40}, {"nominal_size": '1-1/4"', "length_ft": 10},
             {"nominal_size": '1-1/2"', "length_ft": 60}, _orifice(8), _orifice(20, 25), _orifice(5))
    res = solve_network(r, **SITE)
    assert res.converged
    _assert_balanced(r, res)
    # Around the ring the signed head drops sum to zero.
    loop = sum(math.copysign(res.segments[s]["loss_ft"], res.segments[s]["flow_gpm"]) for s in ("AB", "BC", "CD", "DA"))
    assert abs(loop) < 1e-9
    total = sum(t["flow_gpm"] for t in res.terminals.values())
    assert rel(total, res.pumps["p1"]["flow_gpm"]) < 1e-12


def test_parallel_pumps_from_one_basin_with_suction_segments():
    """Suction segments are part of the balance here (DRC leaves them out); NPSHa reflects them."""
    net = header([PUMP, PUMP], [LEG, LEG])
    for p in net["pumps"]:
        p.pop("source")
    suction = {"material": "SCH40 PVC", "nominal_size": '3"', "length_ft": 10, "fittings": [{"key": "entrance", "qty": 1}]}
    net["segments"] += [dict(suction, id="suc1", line="suction", **{"from": "src", "to": "p1"}),
                        dict(suction, id="suc2", line="suction", **{"from": "src", "to": "p2"})]
    res = solve_network(net, **SITE)
    assert res.converged
    plain = solve_network(header([PUMP, PUMP], [LEG, LEG]), **SITE)
    assert res.pumps["p1"]["npsha_ft"] < plain.pumps["p1"]["npsha_ft"]
    assert res.terminals["t1"]["flow_gpm"] < plain.terminals["t1"]["flow_gpm"]
    assert res.segments["suc1"]["line"] == "suction"


def test_envelope_and_build():
    r = network_operating_point(chain(ORIFICES), **SITE)
    assert r.calc == "network_operating_point" and r.value["converged"]
    assert set(r.value) >= {"nodes", "segments", "pumps", "terminals", "totals", "checks"}
    net = build_network(chain(ORIFICES))
    assert net.terminals["t1"]["design_q"] == pytest.approx(gpm_to_m3s(30) + 0, rel=1e-12) or net.terminals["t1"]["design_q"] is None
    with pytest.raises(ValueError):
        build_network({"nodes": [{"id": "src", "kind": "basin"}], "pumps": [], "segments": [{"from": "src", "to": "nowhere"}]})
