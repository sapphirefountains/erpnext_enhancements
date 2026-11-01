"""engine/autosize.py: DRC pickPipe / autoBypassMm (tests/golden/drc/autosize.json), the DOC-0049 pipe
pick, pump evaluation and ranking through the network solve, and derive() on the PLAN.md 3.3 model.

Bench-free pytest suite: python -m pytest erpnext_enhancements/tests/test_autosize.py -q
"""

import copy
import math

from erpnext_enhancements.tests.drc_golden import golden, pump_from_drc
from erpnext_enhancements.water_engineering.engine.autosize import (
    RULE_DEFAULTS,
    _catalog_curve,
    auto_basin,
    auto_bypass,
    derive,
    drc_auto_bypass_mm,
    drc_pick_pipe,
    eval_pump,
    pick_pipe,
    pick_suction,
    rank_pumps,
)
from erpnext_enhancements.water_engineering.engine.hydraulics.pump import PumpCurve, bep_flow_si, pump_head_si
from erpnext_enhancements.water_engineering.engine.hydraulics.units import M_PER_FT, MM_PER_IN, G, m3s_to_gpm
from erpnext_enhancements.water_engineering.engine.hydraulics.water import water_si

GOLD = golden("autosize")


def test_drc_pick_pipe():
    assert len(GOLD["picks"]) >= 250
    for row in GOLD["picks"]:
        opt = row["opt"]
        got = drc_pick_pipe(row["mat"], row["Q"], v_max=opt.get("vMax", 1.5), grad_max=opt.get("gradMax", 0.04),
                            min_id_mm=opt.get("minIdMm", 0), w=water_si(row["T"]))
        assert (got or {}).get("nps") == row["nps"], row
        assert (got or {}).get("id_mm") == row["idMm"], row


def test_drc_auto_bypass():
    for row in GOLD["bypass"]:
        pump = pump_from_drc(row["pump"])
        assert drc_auto_bypass_mm(pump, row["control"]) == row["mm"], row["name"]


def test_pick_pipe_doc0049_limits_govern():
    """48 GPM on Sch 40 PVC discharge: 1-1/2" runs 7.6 ft/s (over DOC-0049's 6.5), 2" runs 4.6 ft/s and
    0.026 ft/ft, so 2" is the pick; DRC's own 1.5 m/s rule would also have refused 1-1/2"."""
    r = pick_pipe(48, "SCH40 PVC", "discharge")
    assert r.value == '2"'
    opts = {o.key: o.detail for o in r.options}
    assert opts['1-1/2"']["velocity_fps"] > 6.5 and not opts['1-1/2"']["ok"]
    assert opts['2"']["velocity_fps"] <= 6.5 and opts['2"']["gradient_ft_ft"] <= 0.04
    assert abs(opts['2"']["velocity_fps"] - 48 * 0.4085 / 2.067**2) < 0.01  # the workbook's V = GPM x 0.4085 / ID^2
    # Suction: 4.5 ft/s, and at least one size over the trunk.
    s = pick_suction(48, '2"', "SCH40 PVC")
    assert s.value == '2-1/2"'
    assert pick_pipe(48, "SCH40 PVC", "suction").value == '2-1/2"'  # 2" would run 4.59 ft/s, over the 4.5 suction limit
    # The friction-gradient cap binds where velocity does not: tiny copper at a modest flow.
    cu = pick_pipe(4, "Type K Copper", "discharge")
    d = {o.key: o.detail for o in cu.options}
    assert any(x["status"] == "Over Gradient" for x in d.values())


def test_pick_pipe_material_without_doc0049_limits_uses_drc_velocity():
    r = pick_pipe(30, "PEX", "discharge")
    det = next(o.detail for o in r.options if o.recommended)
    assert abs(det["limit_fps"] - 1.5 / M_PER_FT) < 1e-12 and "DRC" in det["basis"]


def pts(h0, qmax, bep):
    return [{"flow_gpm": 0, "head_ft": h0, "efficiency_pct": 0, "npshr_ft": 4},
            {"flow_gpm": bep * 0.5, "head_ft": h0 * 0.95, "efficiency_pct": 55, "npshr_ft": 5},
            {"flow_gpm": bep, "head_ft": h0 * 0.8, "efficiency_pct": 72, "npshr_ft": 7},
            {"flow_gpm": qmax, "head_ft": h0 * 0.25, "efficiency_pct": 45, "npshr_ft": 12}]


CATALOG = {"pumps": [{"item": f"P-{h}-{q}", "name": f"{h} ft / {q} gpm", "points": pts(h, q, q * 0.6), "hp": round(h * q * 0.6 / 3960 / 0.6, 2)}
                     for h, q in [(20, 60), (30, 100), (45, 120), (60, 160), (80, 250), (120, 400)]]}


def model(**setup):
    st = {"basin": "auto", "piping": "auto", "pump": "auto", "control": "auto", "bypass": "auto", "surge": "auto", **setup}
    return {
        "schema": 2, "site": {"elevation_ft": 4300, "water_temp_f": 70, "air_temp_f": 95}, "setup": st,
        "features": [{"id": "f1", "family": "weir", "inputs": {"length_ft": 8, "fall_ft": 10}, "basin": "b1", "node": "t1"}],
        "basins": [{"id": "b1", "shape": "rectangular", "length_in": 120, "width_in": 24, "depth_in": 14, "freeboard_in": None}],
        "network": {
            "nodes": [{"id": "src", "kind": "basin", "basin": "b1", "elevation_ft": 0}, {"id": "p1", "kind": "pump", "elevation_ft": -1},
                      {"id": "j1", "kind": "junction", "elevation_ft": 1}, {"id": "t1", "kind": "terminal", "feature": "f1", "elevation_ft": 10}],
            "pumps": [{"id": "p1", "item": None, "curve": None, "speed_pct": 100, "control": "none", "generic_class": None}],
            "segments": [
                {"id": "s1", "from": "src", "to": "p1", "line": "suction", "material": "SCH40 PVC", "nominal_size": None, "length_ft": 8,
                 "fittings": [{"key": "elbow_90", "qty": 2}]},
                {"id": "s2", "from": "p1", "to": "j1", "line": "discharge", "material": "SCH40 PVC", "nominal_size": None, "length_ft": 45,
                 "fittings": [{"key": "check", "qty": 1}], "components": []},
                {"id": "s3", "from": "j1", "to": "t1", "line": "discharge", "material": "SCH40 PVC", "nominal_size": None, "length_ft": 12,
                 "fittings": [{"key": "elbow_90", "qty": 3}]},
            ],
            "terminals": [{"id": "t1", "kind": "open_discharge", "feature": "f1", "params": {"lift_ft": 0.2, "length_ft": 8, "gpm_per_ft": 6}}],
        },
    }


def sized(net):
    net = copy.deepcopy(net)
    for s, n in zip(net["segments"], ['2-1/2"', '2"', '2"'], strict=True):
        s["nominal_size"] = n
    return net


def test_eval_pump_reasons():
    net = sized(model()["network"])
    small = eval_pump(net, _catalog_curve(CATALOG["pumps"][0]), water_temp_f=70, elevation_ft=4300)
    assert not small["ok"] and small["fails"][0]["code"] == "head_margin" and small["margin_pct"] < 0
    big = eval_pump(net, _catalog_curve(CATALOG["pumps"][5]), water_temp_f=70, elevation_ft=4300)
    assert not big["ok"] and {f["code"] for f in big["fails"]} >= {"piping.velocity"}
    # The same big pump with a VFD holding the design-flow pressure: on design flow, inside the limits.
    tamed = eval_pump(net, _catalog_curve(CATALOG["pumps"][3]), control="vfd", water_temp_f=70, elevation_ft=4300)
    assert tamed["ok"], tamed["reasons"]
    assert abs(tamed["flow_gpm"] - 48) < 1e-6 and tamed["setpoints"]["p1"]["control_node"] == "p1"


def test_rank_pumps_smallest_passing_first():
    net = sized(model()["network"])
    ranked = rank_pumps(net, CATALOG["pumps"], controls=["auto"], water_temp_f=70, elevation_ft=4300)
    ok = [e for e in ranked if e["ok"]]
    assert ok and ranked[: len(ok)] == ok
    assert [e["hp"] for e in ok] == sorted(e["hp"] for e in ok)
    # The smallest motor that passes: the 30 ft pump, under 1 hp, so its trial control is a PRV.
    assert ranked[0]["item"] == "P-30-100" and ranked[0]["control"] == "prv"
    assert next(e for e in ranked if e["item"] == "P-45-120")["control"] == "vfd"


def test_auto_bypass():
    pc = PumpCurve.from_us(pts(45, 120, 72))
    assert auto_bypass(pc, "vfd").value == 0
    r = auto_bypass(pc, "none").value
    q_min = 0.15 * bep_flow_si(pc, 1.0)
    d_mm = math.sqrt(4 * q_min / (0.61 * math.sqrt(2 * G * pump_head_si(pc, 0, 1.0))) / math.pi) * 1000
    assert d_mm <= r * MM_PER_IN < d_mm + 0.5 + 1e-9
    assert r * MM_PER_IN == drc_auto_bypass_mm(pc, "none")


def test_auto_basin():
    pick = auto_basin({}, {"submergence_required_in": 7.3, "level_rise_in": 1.1}, suction_id_in=2.469)
    assert pick["depth_in"] == math.ceil((2.469 + 7.3 + 1.2) * 2) / 2
    assert pick["freeboard_in"] == max(2.0, math.ceil(1.3 * 1.1 * 2) / 2)
    assert RULE_DEFAULTS["freeboard_multiplier"] == 1.3


def test_derive_fills_only_auto_sections():
    m = model()
    before = copy.deepcopy(m)
    out = derive(m, catalogs=CATALOG)
    assert m == before, "derive must not mutate its input"
    d = out["model"]
    assert [s["nominal_size"] for s in d["network"]["segments"]] == ['2-1/2"', '2"', '2"']
    p = d["network"]["pumps"][0]
    assert p["item"] == "P-30-100" and p["control"] == "prv" and p["setpoint_psi"] > 0
    assert d["basins"][0]["freeboard_in"] >= 2.0 and d["basins"][0]["depth_in"] >= 4.0
    calcs = [r["calc"] for r in out["results"]]
    assert calcs.index("basin_checks") < calcs.index("pick_pipe") < calcs.index("rank_pumps") < calcs.index("network_operating_point")
    # The setpoint is written to 0.01 psi, so the controlled flow lands within a fraction of a GPM of 48.
    assert out["network"]["converged"] and abs(out["network"]["terminals"]["t1"]["flow_gpm"] - 48) < 0.25
    assert not [c for c in out["checks"] if c["severity"] == "blocker"]
    for c in out["checks"]:
        assert set(c) >= {"severity", "title", "remedy", "section", "key"}


def test_derive_respects_custom_sections():
    m = model(piping="custom", pump="custom", control="custom", basin="custom")
    m["network"]["pumps"][0]["curve"] = pts(45, 120, 72)
    out = derive(m, catalogs=CATALOG)
    d = out["model"]
    assert all(s["nominal_size"] is None for s in d["network"]["segments"])  # custom: left as the designer had it
    assert any(c["code"] == "piping.unsized" for c in out["checks"])
    assert d["network"]["pumps"][0]["item"] is None and d["network"]["pumps"][0]["control"] == "none"
    assert d["basins"][0] == m["basins"][0]
    assert "rank_pumps" not in [r["calc"] for r in out["results"]]


def test_derive_with_switched_terminals_adds_bypass_and_surge():
    m = model()
    m["network"]["terminals"] = [{"id": "t1", "kind": "orifice", "params": {"rated_gpm": 6, "rated_head_ft": 20, "count": 8, "switched": True}}]
    m["setup"]["control"] = "custom"
    out = derive(m, catalogs=CATALOG)
    p = out["model"]["network"]["pumps"][0]
    assert p["bypass_in"] > 0
    assert "auto_bypass" in [r["calc"] for r in out["results"]]
    assert out["model"]["surge"]["closure_ms"] == RULE_DEFAULTS["valve_close_ms"]
    assert m3s_to_gpm(0) == 0


def test_derive_reports_a_half_drawn_network_instead_of_raising():
    m = model()
    m["network"]["segments"].append({"id": "s9", "from": "j1", "to": "nowhere", "length_ft": 3})
    out = derive(m, catalogs=CATALOG)
    assert out["network"] is None and out["checks"][0]["code"] == "network.invalid"
    assert out["checks"][0]["severity"] == "blocker"


def test_derive_keeps_the_model_json_with_a_pumpcurve_catalog():
    import json

    cat = {"pumps": [dict(CATALOG["pumps"][2], points=None, curve=PumpCurve.from_us(pts(45, 120, 72)))]}
    out = derive(model(), catalogs=cat)
    json.dumps(out["model"])  # no PumpCurve object leaked into the model
    rows = out["model"]["network"]["pumps"][0]["curve"]
    assert abs(rows[2]["head_ft"] - 36) < 1e-9 and rows[2]["efficiency_pct"] == 72


# ---------------------------------------------------------------------------
# PR #1398 review: derive never raises on a half-drawn model, and the output is strict JSON
# ---------------------------------------------------------------------------


def _strict(out):
    import json

    json.dumps(out, allow_nan=False)


def test_basin_with_no_plan_area_is_a_blocker_not_a_crash():
    m = model()
    m["basins"][0].pop("length_in")
    m["basins"][0].pop("width_in")
    out = derive(m, catalogs=CATALOG)
    assert any(c["code"] == "basin.no_area" and c["severity"] == "blocker" for c in out["checks"])
    _strict(out)


def test_terminal_linked_to_its_node_only_by_feature():
    m = model()
    m["network"]["terminals"][0]["id"] = "term_f1"  # the node is t1; the link is feature f1
    out = derive(m, catalogs=CATALOG)
    assert out["network"] is not None, out["checks"]
    assert abs(out["network"]["terminals"]["term_f1"]["flow_gpm"] - 48) < 0.25


def test_stale_control_node_is_reported():
    m = model(control="custom")
    m["network"]["pumps"][0].update(curve=pts(45, 120, 72), control="vfd", setpoint_psi=6, control_node="deleted_terminal")
    out = derive(m, catalogs=CATALOG)
    assert out["network"] is None and out["checks"][0]["code"] == "network.invalid"
    assert "deleted_terminal" in out["checks"][0]["remedy"]


def test_derive_guards_every_build():
    """Anything a later build would raise on (here a pump with no basin to draw from) is network.invalid."""
    m = model()
    m["network"]["segments"] = [s for s in m["network"]["segments"] if s["id"] != "s1"]
    m["network"]["nodes"] = [n for n in m["network"]["nodes"] if n["kind"] != "basin"]
    out = derive(m, catalogs=CATALOG)
    assert out["checks"][0]["code"] == "network.invalid" and out["network"] is None
    _strict(out)


def test_pump_on_its_source_basin_still_sizes_the_basin():
    m = model()
    m["network"]["segments"] = [s for s in m["network"]["segments"] if s["id"] != "s1"]
    m["network"]["pumps"][0]["source"] = "src"
    out = derive(m, catalogs=CATALOG)
    b = out["model"]["basins"][0]
    assert b["freeboard_in"] is not None and b["depth_in"] >= RULE_DEFAULTS["min_depth_in"]
    assert not any(c["code"] == "basin.freeboard" for c in out["checks"])
    assert any(c["code"] == "basin.no_intake" for c in out["checks"])


def test_shutoff_only_catalog_pump_keeps_its_curve():
    cat = {"pumps": [{"item": "GEN-1", "name": "generic 45 ft", "shutoff_head_ft": 45, "max_flow_gpm": 140, "hp": 0.8}]}
    out = derive(model(), catalogs=cat)
    curve = out["model"]["network"]["pumps"][0]["curve"]
    assert curve == {"points": [], "shutoff_head_ft": 45, "max_flow_gpm": 140, "motor_eff_pct": None}
    assert out["network"]["pumps"]["p1"]["status"] != "No Curve"


def test_vgb_fail_text_for_a_cover_too_small_to_evaluate():
    net = sized(model()["network"])
    e = eval_pump(net, _catalog_curve(CATALOG["pumps"][2]), control="vfd", water_temp_f=70, elevation_ft=4300,
                  vgb=[{"pump": "p1", "cover_length_in": 0.5, "cover_width_in": 0.5, "open_area_fraction": 0.2}])
    vgb = [f["text"] for f in e["fails"] if f["code"] == "vgb"]
    assert vgb and "None" not in vgb[0]


def test_derive_output_is_strict_json_with_zero_flow():
    m = model()
    m["network"]["terminals"][0]["params"] = {"lift_ft": 0.2, "design_gpm": 0}
    _strict(derive(m, catalogs=CATALOG))


def test_derive_pins_bypass_and_surge_on_their_own():
    m = model(bypass="custom")
    m["network"]["terminals"] = [{"id": "t1", "kind": "orifice", "params": {"rated_gpm": 6, "rated_head_ft": 20, "count": 8, "switched": True}}]
    m["network"]["pumps"][0]["bypass_in"] = 0.123
    out = derive(m, catalogs=CATALOG)
    assert out["model"]["network"]["pumps"][0]["bypass_in"] == 0.123
    assert "auto_bypass" not in [r["calc"] for r in out["results"]]
    m2 = model(surge="custom")
    m2["surge"] = {"arrestors": [{"pump": "p1", "gas_l": 4, "precharge_psi": 20}]}
    out2 = derive(m2, catalogs=CATALOG)
    assert out2["model"]["surge"] == m2["surge"]
    assert "auto_surge" not in [r["calc"] for r in out2["results"]]
