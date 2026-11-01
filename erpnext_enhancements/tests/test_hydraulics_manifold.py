"""engine/hydraulics/manifold.py against DRC manifold.js (tests/golden/drc/manifold.json): the
uniform rain-curtain case at 1e-12 (velocities through float32), the per-takeoff generalization
collapsing to it, and the manifold as a terminal.

Bench-free pytest suite: python -m pytest erpnext_enhancements/tests/test_hydraulics_manifold.py -q
"""

import math

from erpnext_enhancements.tests.drc_golden import REL, close, close_all, close_f32, golden, manifold_from_drc, num
from erpnext_enhancements.water_engineering.engine.hydraulics.manifold import (
    Manifold,
    ManifoldDemand,
    Takeoff,
    curtain_spec,
    manifold_distribution,
    manifold_runs,
    manifold_size_sweep,
    manifold_solve_si,
    manifold_volume_l,
    size_sweep_si,
)
from erpnext_enhancements.water_engineering.engine.hydraulics.operating_point import System, operating_point_si
from erpnext_enhancements.water_engineering.engine.hydraulics.pump import PumpCurve
from erpnext_enhancements.water_engineering.engine.hydraulics.terminals import Demand, build_demand
from erpnext_enhancements.water_engineering.engine.hydraulics.units import G, PA_PER_PSI, m3s_to_gpm
from erpnext_enhancements.water_engineering.engine.hydraulics.water import water_si

GOLD = golden("manifold")


def build(P: dict) -> Manifold:
    """The DRC project fields -> the per-takeoff manifold (curtain_manifold)."""
    return manifold_from_drc(P)


def pattern(n: int, kind: str) -> list[bool]:
    return [kind == "all" or (kind == "one" and i == 0) or (kind == "alternate" and i % 2 == 1)
            or (kind == "firsthalf" and i < n / 2) for i in range(n)]


def test_runs():
    for row in GOLD["runs"]:
        assert manifold_runs(row["n"], row["feed"]) == row["runs"], row


def test_geometry_helpers_match_common_js():
    for s in GOLD["solves"]:
        m = build(s["P"])
        w = water_si(s["P"]["waterC"])
        close(m.p_design, s["design"]["pManifold"], REL, f"{s['name']} pManifold")
        close(m.p_branch, s["design"]["pBranch"], REL, f"{s['name']} pBranch")
        close(m.takeoffs[0].k, s["branch"]["k"], REL, f"{s['name']} branch k")
        close(m.spec.id_mm, s["spec"]["idMm"], REL, f"{s['name']} id")
        close_all([t.dz for t in m.takeoffs], s["dz"], REL, f"{s['name']} dz")
        close(manifold_volume_l(m), s["volumeL"], REL, f"{s['name']} volume")
        assert w.rho > 0


def test_solve_matches_manifold_solve():
    assert len(GOLD["solves"]) >= 18
    for s in GOLD["solves"]:
        m = build(s["P"])
        w = water_si(s["P"]["waterC"])
        sol = manifold_solve_si(m, s["pIn"], w, pattern(m.n, s["pattern"]))
        out = s["out"]
        close(sol.Q, out["Q"], REL, f"{s['name']} Q")
        close_all(sol.q, out["q"], REL, f"{s['name']} q")
        close_all(sol.p, out["p"], REL, f"{s['name']} p")
        close_f32(sol.v, out["v"], f"{s['name']} v")
        assert sol.converged == out["converged"], s["name"]
        assert sol.iters == out["iters"], f"{s['name']}: {sol.iters} vs {out['iters']} iterations"


def test_hand_built_uniform_list_equals_drc():
    """The generalization collapses to DRC: a takeoff list written out by hand (no curtain builder)
    with the same x, K and dz reproduces manifoldSolve to 1e-12."""
    for s in GOLD["solves"]:
        P = s["P"]
        w = water_si(P["waterC"])
        pitch = P["pitchMm"] / 1000
        open_ = pattern(P["valves"], s["pattern"])
        takeoffs = tuple(
            Takeoff(x=(i + 0.5) * pitch, k=num(s["branch"]["k"]), dz=num(s["dz"][i]), open=open_[i],
                    aj=math.pi * (P["orificeMm"] / 1000) ** 2 / 4 * min(1, max(0.5, P["nozzleCc"] or 1)))
            for i in range(P["valves"])
        )
        m = Manifold(spec=curtain_spec(P["manMat"], P["manNps"], P["pipeIdMm"], P["manWallMm"]), takeoffs=takeoffs,
                     feed=P["feed"], regain=P["regain"], nozzle_below_m=P["nozzleBelowM"])
        sol = manifold_solve_si(m, s["pIn"], w)
        close(sol.Q, s["out"]["Q"], REL, f"{s['name']} Q")
        close_all(sol.q, s["out"]["q"], REL, f"{s['name']} q")
        close_all(sol.p, s["out"]["p"], REL, f"{s['name']} p")


def test_size_sweep_matches():
    for sw in GOLD["sweeps"]:
        m = build(sw["P"])
        w = water_si(sw["P"]["waterC"])
        rows = size_sweep_si(m, sw["mat"], None, w, sw["target"])
        assert [r["nps"] for r in rows] == [r["nps"] for r in sw["rows"]], sw["name"]
        for got, want in zip(rows, sw["rows"], strict=True):
            close(got["min_v"], want["minV"], REL, f"{sw['name']} {want['nps']} minV")
            close(got["max_v"], want["maxV"], REL, f"{sw['name']} {want['nps']} maxV")
            close(got["spread_pct"], want["spreadPct"], REL, f"{sw['name']} {want['nps']} spread")
            assert got["ok"] == want["ok"]


def test_lumped_demand_matches_demand_model():
    for d in GOLD["demands"]:
        m = build(d["P"])
        w = water_si(d["P"]["waterC"])
        md = ManifoldDemand(m, w, pattern(m.n, d["pattern"]))
        assert isinstance(md, Demand)
        assert md.n_open == d["nOpen"]
        close(md.pg, d["pg"], REL, f"{d['name']} pg")
        close(md.p_ref, d["pRef"], REL, f"{d['name']} pRef")
        close(md.q_ref, d["Qref"], REL, f"{d['name']} Qref")
        close(md.G, d["G"], REL, f"{d['name']} G")
        for pt in d["vAt"]:
            close_f32(md.v_at(pt["p"]), pt["v"], f"{d['name']} vAt({pt['p']})")


# ---------------------------------------------------------------------------
# Generalization: hand goldens
# ---------------------------------------------------------------------------


def test_single_takeoff_is_an_orifice():
    """One open takeoff of resistance K, no friction to reach it: q = sqrt(p / K)."""
    from erpnext_enhancements.water_engineering.engine.hydraulics.catalog import pipe_spec_si

    w = water_si(20)
    m = Manifold(spec=pipe_spec_si("SCH40 PVC", '2"'), takeoffs=(Takeoff(x=0.5, k=4e9),), regain=False)
    sol = manifold_solve_si(m, 2e4, w)
    assert abs(sol.Q - math.sqrt(2e4 / 4e9)) < 1e-15


def test_nonuniform_takeoffs_conserve_flow_and_energy():
    """Mixed nozzles at uneven spacing: each takeoff obeys its own orifice law at its own pressure,
    and the pressures along the run fall by exactly the regain-minus-friction steps."""
    from erpnext_enhancements.water_engineering.engine.hydraulics.catalog import pipe_spec_si
    from erpnext_enhancements.water_engineering.engine.hydraulics.manifold import _f_at, _f_table

    w = water_si(20)
    spec = pipe_spec_si("SCH40 PVC", '1"')
    xs = [0.1, 0.25, 0.7, 0.75, 1.6]
    ks = [3e9, 1.5e9, 6e9, 3e9, 1e9]
    dzs = [0.0, 0.01, -0.02, 0.0, 0.03]
    m = Manifold(spec=spec, takeoffs=tuple(Takeoff(x=x, k=k, dz=z) for x, k, z in zip(xs, ks, dzs, strict=True)))
    p_in = 3e4
    sol = manifold_solve_si(m, p_in, w)
    assert sol.converged
    rg = w.rho * G
    for i in range(5):
        p_local = sol.p[i] - rg * dzs[i]  # the centerline pressure at the takeoff
        assert abs(sol.q[i] - math.sqrt((p_local + rg * dzs[i]) / ks[i])) < 1e-12 * sol.q[i]
    # End feed marches from the last takeoff back to the first.
    ft = _f_table(spec.eps_mm / spec.id_mm)
    down, p = sol.Q, p_in
    for j, i in enumerate([4, 3, 2, 1, 0]):
        assert abs(sol.p[i] - rg * dzs[i] - p) < 1e-9 * p_in
        after = down - sol.q[i]
        p += 0.9 * w.rho / 2 * ((down / spec.area) ** 2 - (after / spec.area) ** 2)
        down = after
        if j < 4:
            u = down / spec.area
            p -= _f_at(ft, u * spec.id_m / w.nu) * (abs(xs[i] - xs[i - 1]) / spec.id_m) * w.rho / 2 * u * u
    assert abs(down) <= 1e-9 * sol.Q


def test_exact_demand_inverts_and_tracks_the_solve():
    m = build(GOLD["solves"][8]["P"])  # small bore: friction bends the curve away from sqrt(p)
    w = water_si(20)
    exact = ManifoldDemand(m, w, exact=True)
    for p in (500.0, 2e3, 1e4):
        q = exact.q(p)
        assert abs(q - manifold_solve_si(m, p, w).Q) < 1e-15
        assert abs(exact.p_for(q) - p) < 1e-7 * p


def test_manifold_terminal_on_the_operating_point():
    """A manifold terminal built from the design model solves with phase A's operating point, and
    the split at the settled pressure sums to the delivered flow."""
    w = water_si(20)
    params = {"material": "SCH40 PVC", "nominal_size": '1-1/2"', "count": 12, "pitch_in": 6,
              "rated_gpm": 2.0, "rated_head_ft": 15, "feed": "center"}
    d = build_demand({"kind": "manifold", "params": params}, w.rho)
    pump = PumpCurve(curve=[[0, 20], [100, 17], [200, 10], [260, 2]])
    op = operating_point_si(System(pump=pump, w=w, static_m=1.0), d)
    split = d.solution(op.p_in)
    assert op.q > 0 and abs(split.Q - op.q) < 0.02 * op.q  # lumped vs exact at the operating pressure
    assert abs(m3s_to_gpm(op.q) / 12 - 2.0) < 1.5


def test_envelopes():
    params = {"material": "SCH40 PVC", "nominal_size": '1"', "count": 20, "pitch_in": 4, "cd": 0.9,
              "orifice_diameter_in": 0.125}
    r = manifold_distribution(params, 10.0)
    assert r.value["flow_gpm"] > 0 and len(r.value["takeoffs"]) == 20
    # A short, low-friction header: regain wins, so the dead end (takeoff 1) runs a little fastest.
    flows = [t["flow_gpm"] for t in r.value["takeoffs"]]
    assert flows[0] == max(flows)
    assert abs(r.value["spread_pct"] - (max(flows) - min(flows)) / max(flows) * 100) < 1e-9
    sweep = manifold_size_sweep(params, inlet_psi=10.0)
    assert sweep.value is not None and any(o.recommended for o in sweep.options)
    assert abs(sweep.inputs["inlet"]["value"] - 10.0) < 1e-12
    assert PA_PER_PSI > 0
