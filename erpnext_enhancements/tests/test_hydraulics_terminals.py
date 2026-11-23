"""engine/hydraulics/terminals.py: OrificeDemand against DRC manifold.js demandModel
(tests/golden/drc/terminals.json, 1e-12); the open-discharge, fixed-flow and parallel demands
against hand goldens and the existing engine's nozzle and weir formulas.

Bench-free pytest suite: python -m pytest erpnext_enhancements/tests/test_hydraulics_terminals.py -q
"""

import math

from erpnext_enhancements.tests.drc_golden import REL, close, golden
from erpnext_enhancements.water_engineering.engine.feature import nozzle_flow, weir_flow
from erpnext_enhancements.water_engineering.engine.hydraulics.terminals import (
    Demand,
    FixedFlowDemand,
    OpenDischargeDemand,
    OrificeDemand,
    ParallelDemand,
    build_demand,
    terminal_demand,
)
from erpnext_enhancements.water_engineering.engine.hydraulics.units import M_PER_FT, G, gpm_to_m3s, m3s_to_gpm
from erpnext_enhancements.water_engineering.engine.hydraulics.water import water_si

GOLD = golden("terminals")
RHO = water_si(20).rho


def test_orifice_matches_demand_model():
    assert len(GOLD["demands"]) >= 16
    for row in GOLD["demands"]:
        d = OrificeDemand(row["G"], row["pg"], row["anyOpen"])
        for pt in row["q"]:
            close(d.q(pt["p"]), pt["Q"], REL, f"{row['name']} Q({pt['p']})")
        for pt in row["pFor"]:
            close(d.p_for(pt["Q"]), pt["p"], REL, f"{row['name']} pFor({pt['Q']})")


def test_every_demand_honours_the_protocol():
    for d in (OrificeDemand(1e-4), OpenDischargeDemand(1.0, RHO), FixedFlowDemand(1e-3), ParallelDemand([OrificeDemand(1e-4)])):
        assert isinstance(d, Demand)


def test_orifice_from_cd_area_is_the_engines_nozzle_flow():
    """Q = Cd A sqrt(2 g h): the same number feature.nozzle_flow gives, at any water density."""
    for t in (5, 20, 40):
        rho = water_si(t).rho
        d = build_demand({"kind": "orifice", "params": {"cd": 0.82, "orifice_diameter_in": 0.5, "count": 3}}, rho)
        for h_ft in (2.0, 11.5, 40.0):
            q = m3s_to_gpm(d.q(rho * G * h_ft * M_PER_FT))
            assert abs(q - 3 * nozzle_flow(h_ft, cd=0.82, orifice_diameter_in=0.5).value) < 1e-9 * q


def test_orifice_from_rated():
    d = build_demand({"kind": "orifice", "params": {"rated_gpm": 12, "rated_head_ft": 20}}, RHO)
    for h_ft in (5, 20, 45):
        q = m3s_to_gpm(d.q(RHO * G * h_ft * M_PER_FT))
        assert abs(q - nozzle_flow(h_ft, rated_gpm=12, rated_head_ft=20).value) < 1e-9 * q
    # A nozzle 2 ft below the inlet node flows at zero inlet pressure.
    below = build_demand({"kind": "orifice", "params": {"rated_gpm": 12, "rated_head_ft": 20, "below_ft": 2}}, RHO)
    assert abs(m3s_to_gpm(below.q(0)) - 12 * math.sqrt(2 / 20)) < 1e-9


def test_open_discharge():
    d = OpenDischargeDemand(1.5, RHO)
    assert d.p_fixed == RHO * G * 1.5 and d.pg == -d.p_fixed
    assert d.p_for(0) == d.p_fixed and d.p_for(1.0) == d.p_fixed  # vertical system-curve offset
    assert d.q(d.p_fixed) == 0 and d.q(d.p_fixed + 1) == math.inf
    weir = build_demand({"kind": "open_discharge", "params": {"lift_ft": 6, "weir_length_ft": 8, "weir_head_in": 0.25}}, RHO)
    assert abs(m3s_to_gpm(weir.q_design) - weir_flow(8, 0.25, 2).value) < 1e-9
    rule = build_demand({"kind": "open_discharge", "params": {"lift_ft": 6, "length_ft": 8, "gpm_per_ft": 5}}, RHO)
    assert abs(m3s_to_gpm(rule.q_design) - 40) < 1e-9


def test_fixed_flow():
    d = FixedFlowDemand(2e-3, 1000)
    assert d.q(999) == 0 and d.q(1000) == 2e-3 and d.q(1e6) == 2e-3
    assert d.p_for(1e-3) == 1000 and d.p_for(3e-3) == math.inf and d.p_for(0) == 1000
    assert not FixedFlowDemand(0).any_open


def test_parallel_sums_in_order_and_inverts():
    a, b = OrificeDemand(1e-4, 50), OrificeDemand(3e-4, 50)
    both = ParallelDemand([a, b])
    lumped = OrificeDemand(4e-4, 50)
    for p in (-60, -50, 0, 500, 2e4):
        assert abs(both.q(p) - lumped.q(p)) <= 1e-15
    for q in (0, 1e-3, 5e-3, 2e-2):
        close(both.p_for(q), lumped.p_for(q), 1e-10, f"pFor({q})")
    # Different offsets: nothing flows until the lower threshold.
    mixed = ParallelDemand([OrificeDemand(1e-4, 0), OrificeDemand(1e-4, 200)])
    assert mixed.pg == 200 and mixed.q(-150) == 1e-4 * math.sqrt(50)
    assert ParallelDemand([OrificeDemand(1e-4, 0, any_open=False)]).p_for(1e-3) == math.inf


def test_parallel_with_an_open_discharge_is_pinned_and_split():
    open_ = OpenDischargeDemand(2.0, RHO)
    orifice = OrificeDemand(1e-4, 0)
    node = ParallelDemand([orifice, open_])
    assert node.p_fixed == open_.p_fixed
    assert node.p_for(1.0) == open_.p_fixed  # any flow beyond the orifice's share
    flows = node.split(open_.p_fixed, 0.05)  # the orifice takes 1e-4 sqrt(rho g 2) = 0.0140; the weir the rest
    assert abs(flows[0] - orifice.q(open_.p_fixed)) < 1e-15 and abs(sum(flows) - 0.05) < 1e-15 and flows[1] > 0


def test_terminal_demand_envelope():
    r = terminal_demand({"kind": "orifice", "params": {"rated_gpm": 12, "rated_head_ft": 20}}, 12)
    assert abs(r.value["head_ft"] - 20) < 1e-9 and r.formula and r.citations
    r = terminal_demand({"kind": "open_discharge", "params": {"lift_ft": 6, "length_ft": 8, "gpm_per_ft": 5}})
    assert abs(r.value["head_ft"] - 6) < 1e-9 and abs(r.value["design_gpm"] - 40) < 1e-9
    r = terminal_demand({"kind": "fixed_flow", "params": {"flow_gpm": 10, "min_psi": 5}}, 20)
    assert r.value["pressure_psi"] is None and r.warnings
    assert abs(m3s_to_gpm(gpm_to_m3s(10)) - 10) < 1e-12
