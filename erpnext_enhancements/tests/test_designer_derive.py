"""Bench-free tests for the Fountain Designer's derive (``water_engineering/derive.py``, v1.768.0).

* ``derive_model`` end to end with the real engine (phases A, B, C, L and S) and a stubbed pump
  catalog and price list, on the three demo designs phase F ships (lobby sheet wall, plaza
  interactive, pond aerator) and on every engine kit.
* The contract: every key the page reads (``public/js/fountain_designer/README.md``, "derive",
  whose reference instance is the demo fixture) is in the reply, at every level, with the
  documented vocabularies; the reply is strict JSON; the charts pass the page's SVG allow-list.
* The promises: a ``custom`` section is never written, a half-drawn or junk model never raises, a
  failing stage becomes one blocker, issue keys survive a re-derive, a sign-off in ``model.acks``
  marks its warning.

The charts (the duty point, and the page-safe schematic and one-line) and their allow-list checks
land with the next change.

The derive imports no frappe, so this needs no stub. Plain pytest functions: its own
``python -m pytest`` step in ci.yml.

Run: python -m pytest erpnext_enhancements/tests/test_designer_derive.py -q
"""

import copy
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering import derive
from erpnext_enhancements.water_engineering.engine import kits

DEMO = REPO_ROOT / "erpnext_enhancements" / "public" / "js" / "fountain_designer" / "demo"
FIXTURES = ("lobby", "plaza", "pond")
SECTION_STATUS = {"ok", "warn", "blocked", "missing", "n_a"}
SEVERITIES = {"blocker", "warning", "info"}
# The per-row values the README documents (a fixture row may carry more).
VALUE_KEYS = {
    "features": {"flow_gpm", "head_ft", "note"},
    "basins": {"volume_gal", "weight_lb", "surge_in", "freeboard_in"},
    "segments": {"velocity_fps", "loss_ft", "size"},
    "pumps": {"flow_gpm", "head_ft", "kw", "npsh_margin_ft", "bep_pct"},
}


def _curve(shutoff, qmax, hp, klass, item):
    points = []
    for i in range(6):
        q = qmax * i / 5
        eff = 0 if i == 0 else max(70 * (1 - ((q - 0.6 * qmax) / (0.6 * qmax)) ** 2), 5)
        points.append({"flow_gpm": q, "head_ft": shutoff * (1 - 0.85 * (q / qmax) ** 2), "efficiency_pct": eff,
                       "npshr_ft": 4 + 8 * (q / qmax) ** 2})
    return {"item": item, "name": f"Stub {klass} {hp} HP", "curve_name": f"{item}-CURVE", "points": points,
            "shutoff_head_ft": shutoff, "max_flow_gpm": qmax, "motor_eff_pct": 82, "hp": hp, "generic_class": klass,
            "catalog_source": "datasheet"}


CATALOG = {"pumps": [
    _curve(s, q, hp, k, f"STUB-{k[:3].upper()}-{hp}")
    for k in ("end-suction", "submersible", "self-priming")
    for s, q, hp in ((20, 25, 0.25), (45, 90, 0.75), (60, 160, 1.5), (80, 260, 3), (100, 420, 5), (120, 700, 10))
]}
PRICES = {"items": {r["item"]: {"rate": 400 + 300 * r["hp"]} for r in CATALOG["pumps"]}, "hints": {}}


def run(model, **kw):
    return derive.derive_model(copy.deepcopy(model), CATALOG, None, prices=PRICES, **kw)


def fixture(name):
    return json.loads((DEMO / f"derive_{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- the contract


def _keys_cover(reference, actual, where):
    missing = set(reference) - set(actual)
    assert not missing, f"{where}: missing {sorted(missing)}"


@pytest.mark.parametrize("name", FIXTURES)
def test_reply_carries_every_key_the_page_reads(name):
    ref = fixture(name)
    out = run(ref["model"])
    _keys_cover(ref, out, "reply")
    _keys_cover(ref["rollups"], out["rollups"], "rollups")
    _keys_cover(ref["verdict"], out["verdict"], "verdict")
    _keys_cover(ref["readiness"], out["readiness"], "readiness")
    _keys_cover(ref["charts"], out["charts"], "charts")
    _keys_cover(ref["outputs"], out["outputs"], "outputs")
    _keys_cover(ref["outputs"]["om"], out["outputs"]["om"], "outputs.om")
    _keys_cover(ref["outputs"]["winterization"], out["outputs"]["winterization"], "outputs.winterization")
    for kind, keys in VALUE_KEYS.items():
        rows = {"features": out["model"]["features"], "basins": out["model"]["basins"],
                "segments": out["model"]["network"]["segments"], "pumps": out["model"]["network"]["pumps"]}[kind]
        assert set(out["values"][kind]) == {r["id"] for r in rows}, kind  # one entry per row, keyed by id
        for row_id, row in out["values"][kind].items():
            _keys_cover(keys, row, f"values.{kind}.{row_id}")
    issue_keys = set().union(*(set(i) for i in ref["issues"])) - {"step", "path"}  # both optional in the README
    for i in out["issues"]:
        _keys_cover(issue_keys | {"acknowledged", "remedies"}, i, f"issue {i['key']}")
    line_keys = set(ref["bom"][0])
    for line in out["bom"]:
        _keys_cover(line_keys, line, "bom line")


@pytest.mark.parametrize("name", FIXTURES)
def test_reply_vocabularies_and_strict_json(name):
    out = run(fixture(name)["model"])
    json.dumps(out, allow_nan=False)
    assert out["verdict"]["level"] in {"good", "warn", "bad"}
    assert isinstance(out["verdict"]["package_ready"], bool)
    for s in out["readiness"]["sections"]:
        assert s["status"] in SECTION_STATUS
        assert all(isinstance(m, str) for m in s["missing"])
    for i in out["issues"]:
        assert i["severity"] in SEVERITIES
        for r in i["remedies"]:
            assert set(r) == {"label", "set"} and isinstance(r["set"], dict)
    for line in out["bom"]:
        assert line["source"] in {"Auto", "Manual"}
        assert line["amount"] == pytest.approx(line["qty"] * line["rate"], abs=0.01)
    assert out["rollups"]["budget_total"] == pytest.approx(sum(r["amount"] for r in out["bom"]), abs=0.01)
    assert out["rollups"]["blocker_count"] == sum(1 for i in out["issues"] if i["severity"] == "blocker")
    for url in [s["url"] for s in out["outputs"]["schedules"]] + [out["outputs"]["packet"]["url"]]:
        assert url is None  # phase G prints them


# ----------------------------------------------------------------- the ladder


def test_lobby_kit_sizes_end_to_end():
    out = run(kits.apply_kit("lobby-sheet-wall-8ft"))
    r = out["rollups"]
    assert r["design_flow_gpm"] == pytest.approx(80, rel=0.01)  # 8 ft at the average look
    assert r["operating_flow_gpm"] == pytest.approx(80, rel=0.05)
    assert r["selected_pump"] and r["selected_pump"].startswith("STUB-SUB")  # the kit's submersible class
    assert r["tdh_ft"] > 10  # the 10 ft fall plus friction
    assert r["total_basin_gallons"] == pytest.approx(120 * 24 * 14 / 231, rel=0.2)
    assert r["budget_total"] > 0 and r["om_cost_per_year"] > 0
    assert all(s["size"] for s in out["values"]["segments"].values())
    assert not [i for i in out["issues"] if i["severity"] == "blocker"]


@pytest.mark.parametrize("kit", [k.key for k in kits.KITS])
def test_every_kit_derives_without_a_stage_failure(kit):
    out = run(kits.apply_kit(kit))
    json.dumps(out, allow_nan=False)
    assert not [i for i in out["issues"] if i["code"] in ("ENGINE_STAGE_FAILED", "network.invalid")], kit


def test_custom_piping_is_never_written():
    model = kits.apply_kit("weir-spillway-outdoor-4ft")
    model["setup"]["piping"] = "custom"
    for seg in model["network"]["segments"]:
        seg.update(nominal_size='3"', length_ft=12, fittings=[{"key": "elbow_90", "qty": 1}])
    out = run(model)
    for seg in out["model"]["network"]["segments"]:
        assert (seg["nominal_size"], seg["length_ft"], seg["fittings"]) == ('3"', 12, [{"key": "elbow_90", "qty": 1}])


def test_auto_piping_takes_the_route_template_and_sizes():
    model = kits.apply_kit("weir-spillway-outdoor-4ft")
    model["pump_room"] = {"location": "room", "distance_ft": 40, "material": "SCH80 PVC"}
    out = run(model)
    segs = out["model"]["network"]["segments"]
    assert [s["length_ft"] for s in segs] == [40, 40 + 2 - 0]  # room: suction = distance; discharge + the lift
    assert all(s["material"] == "SCH80 PVC" and s["nominal_size"] for s in segs)
    assert "route_template" in out["results"]


def test_a_blank_network_gets_the_kit_skeleton():
    model = fixture("lobby")["model"]
    model["network"] = {"nodes": [], "pumps": [], "segments": [], "terminals": []}
    out = run(model)
    net = out["model"]["network"]
    assert [n["kind"] for n in net["nodes"]] == ["basin", "pump", "terminal"]
    assert net["terminals"][0]["params"]["design_gpm"] > 0


@pytest.mark.parametrize("junk", [{}, None, "", "not json", {"features": [{}]}, {"features": [None], "basins": "x"},
                                  {"network": {"pumps": [{"id": "p1"}], "segments": [{"from": "p1"}]}}])
def test_a_half_drawn_model_never_raises(junk):
    out = derive.derive_model(junk, CATALOG, None)
    json.dumps(out, allow_nan=False)
    assert out["verdict"]["level"] in {"good", "warn", "bad"}


def test_a_failing_stage_is_one_blocker_and_the_rest_still_runs(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("panel fault")

    seen = []
    monkeypatch.setattr(derive, "electrical_report", boom)
    out = run(kits.apply_kit("lobby-sheet-wall-8ft"), on_error=lambda stage, exc: seen.append(stage))
    failed = [i for i in out["issues"] if i["code"] == "ENGINE_STAGE_FAILED"]
    assert [i["key"] for i in failed] == ["ENGINE_STAGE_FAILED|electrical"]
    assert seen == ["electrical"]
    assert out["rollups"]["selected_pump"]  # the network still solved
    assert out["verdict"]["level"] == "bad"


def test_issue_keys_survive_a_rederive_and_a_signoff_marks_the_warning():
    first = run(kits.apply_kit("jet-ring-6"))
    second = run(first["model"])
    assert {i["key"] for i in first["issues"]} == {i["key"] for i in second["issues"]}
    warnings = [i for i in second["issues"] if i["severity"] == "warning"]
    assert warnings
    model = copy.deepcopy(second["model"])
    model["acks"] = [{"issue_key": w["key"], "note": "ok", "by": "a@b", "on": "2026-11-01"} for w in warnings]
    signed = run(model)
    assert all(i["acknowledged"] for i in signed["issues"] if i["severity"] == "warning")
    assert signed["verdict"]["package_ready"] is True
    assert signed["rollups"]["warning_count"] == len(warnings)  # signed-off warnings still count


def test_a_curve_named_by_its_catalog_name_is_resolved():
    model = kits.apply_kit("lobby-sheet-wall-8ft")
    model["setup"]["pump"] = "custom"
    model["network"]["pumps"][0].update(item="STUB-SUB-1.5", curve="STUB-SUB-1.5-CURVE")
    out = run(model)
    pump = out["model"]["network"]["pumps"][0]
    assert isinstance(pump["curve"], dict) and len(pump["curve"]["points"]) == 6
    assert out["values"]["pumps"]["p1"]["flow_gpm"] > 0
    missing = run(dict(model, network=dict(model["network"], pumps=[dict(model["network"]["pumps"][0], curve="NOPE")])))
    assert "PUMP_CURVE_NOT_FOUND" in {i["code"] for i in missing["issues"]}


def test_site_fills_blanks_only():
    model = kits.apply_kit("lobby-sheet-wall-8ft")
    model["site"].update(elevation_ft=5000, kwh_rate=None)
    out = run(model, site_lookup={"lat": 40.7, "lng": -111.9, "elevation_ft": 9999})
    site = out["model"]["site"]
    assert site["elevation_ft"] == 5000  # typed: kept
    assert (site["lat"], site["lng"]) == (40.7, -111.9)  # from the lookup
    assert site["frost_depth_in"] == 30  # the Utah default
    assert site["kwh_rate"] > 0  # the house rule


def test_utah_interactive_venues_are_regulated():
    assert derive.regulated_site({"venue_type": "Interactive Water Feature", "jurisdiction": "UT"})
    assert derive.regulated_site({"venue_type": "Commercial Pool"})
    assert not derive.regulated_site({"venue_type": "Decorative Fountain"})


PAGE_ROUND_TRIP = r"""
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [dir, file] = process.argv.slice(2);
const load = (name) => import(pathToFileURL(`${dir}/${name}`).href);
const M = await load("model.js");
const V = await load("verdict.js");
const esc = (v) => (v == null ? "" : String(v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]));
const __ = (t, a) => String(t).replace(/\{(\d+)\}/g, (m, n) => (a && a[n] !== undefined ? String(a[n]) : m));
const H = { esc, __, chip: (l, t) => (l ? `<span class="sf-chip sf-chip--${t}">${esc(l)}</span>` : ""), state_html: () => "", parser: null };
const out = [];
for (const [name, pair] of Object.entries(JSON.parse(fs.readFileSync(file, "utf8")))) {
  const merged = M.merge_derived(pair.local, pair.reply.model);
  const html = V.verdict_html(pair.reply, H, {});
  out.push({ name, merged_schema: merged.schema, has_html: html.length > 0,
             steps: pair.reply.issues.map((i) => V.step_for_issue(i)) });
}
console.log(JSON.stringify(out));
"""


def test_a_real_reply_runs_through_the_page_modules(tmp_path):
    """The page's own merge and verdict panel, under node, on real derive replies."""
    import shutil
    import subprocess

    if not shutil.which("node"):
        pytest.skip("node is not installed")
    pairs = {}
    for name in FIXTURES:
        local = fixture(name)["model"]
        pairs[name] = {"local": local, "reply": run(local)}
    data = tmp_path / "replies.json"
    data.write_text(json.dumps(pairs), encoding="utf-8")
    script = tmp_path / "round_trip.mjs"
    script.write_text(PAGE_ROUND_TRIP, encoding="utf-8")
    page = REPO_ROOT / "erpnext_enhancements" / "public" / "js" / "fountain_designer"
    done = subprocess.run(["node", str(script), str(page), str(data)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    for row in json.loads(done.stdout):
        assert row["merged_schema"] == 2 and row["has_html"], row["name"]
        assert all(isinstance(s, str) and s for s in row["steps"]), row


def test_bom_merges_every_phase_and_prices_the_pump():
    out = run(kits.apply_kit("lobby-sheet-wall-8ft"))
    sections = {line["section"] for line in out["bom"]}
    assert {"Equipment", "Electrical", "Controls", "Structural"} <= sections
    pump = next(line for line in out["bom"] if line["line_key"].startswith("pump|"))
    assert pump["item"] == out["rollups"]["selected_pump"] and pump["rate"] > 0
    assert pump["catalog_source"] == "datasheet"
    keys = [line["line_key"] for line in out["bom"]]
    assert "lvpsu:valves" not in keys or not any(k.startswith("psu:") for k in keys)  # L's duplicate supply dropped


# ------------------------------------------------- an auto section follows its inputs


def test_auto_piping_follows_the_pump_room_on_every_derive():
    model = kits.apply_kit("weir-spillway-outdoor-4ft")
    model["pump_room"] = {"location": "room", "distance_ft": 20}
    first = run(model)
    again = copy.deepcopy(first["model"])  # the page merges the derived model and edits on top of it
    again["pump_room"]["distance_ft"] = 150
    again["features"][0]["inputs"]["length_ft"] = 16  # four times the flow
    second = run(again)
    s1 = {s["id"]: s for s in first["model"]["network"]["segments"]}
    s2 = {s["id"]: s for s in second["model"]["network"]["segments"]}
    assert s2["s2"]["length_ft"] > s1["s2"]["length_ft"]
    assert s2["s2"]["nominal_size"] != s1["s2"]["nominal_size"]  # resized, not pinned by the first derive
    assert second["rollups"]["design_flow_gpm"] > 3 * first["rollups"]["design_flow_gpm"]


def test_a_feature_added_later_is_connected_and_a_removed_one_dropped():
    first = run(kits.apply_kit("lobby-sheet-wall-8ft"))
    model = copy.deepcopy(first["model"])
    model["features"].append({"id": "f2", "family": "weir", "type": "Spillway", "label": "Second wall",
                              "inputs": {"style": "spillway", "length_ft": 4, "fall_ft": 3}, "effect": {"look": "average"},
                              "count": 1, "basin": "b1"})
    second = run(model)
    net = second["model"]["network"]
    term = next(t for t in net["terminals"] if t["feature"] == "f2")
    assert any(s["to"] == term["id"] and s["line"] == "discharge" for s in net["segments"])
    assert second["rollups"]["design_flow_gpm"] > first["rollups"]["design_flow_gpm"]
    model = copy.deepcopy(second["model"])
    model["features"] = [f for f in model["features"] if f["id"] != "f2"]
    third = run(model)
    assert not [t for t in third["model"]["network"]["terminals"] if t["feature"] == "f2"]
    assert not [s for s in third["model"]["network"]["segments"] if s["to"] == term["id"]]


def test_the_regulated_flag_follows_the_venue():
    model = kits.apply_kit("lobby-sheet-wall-8ft")
    model["site"]["venue_type"] = "Commercial Spa"
    spa = run(model)
    assert spa["model"]["site"]["regulated"] is True
    back = copy.deepcopy(spa["model"])
    back["site"]["venue_type"] = "Decorative Fountain"
    assert run(back)["model"]["site"]["regulated"] is False


def test_the_pump_hp_follows_a_repick():
    first = run(kits.apply_kit("weir-spillway-outdoor-4ft"))
    model = copy.deepcopy(first["model"])
    model["features"][0]["inputs"]["length_ft"] = 20
    second = run(model)
    p1, p2 = first["model"]["network"]["pumps"][0], second["model"]["network"]["pumps"][0]
    assert p1["item"] != p2["item"]
    hp = {r["item"]: r["hp"] for r in CATALOG["pumps"]}
    assert (p1["hp"], p2["hp"]) == (hp[p1["item"]], hp[p2["item"]])


def test_the_hazen_williams_crosscheck_runs():
    out = run(kits.apply_kit("lobby-sheet-wall-8ft"))
    hw = out["results"]["hazen_williams_crosscheck"]
    assert hw["inputs"]["darcy_ft"]["value"] > 0 and hw["inputs"]["hazen_williams_ft"]["value"] > 0
    if hw["status"] == "Diverges":
        assert next(i for i in out["issues"] if i["code"] == "HW_CROSSCHECK")["severity"] == "info"


# ------------------------------------------------------------------ the #1399 review


@pytest.mark.parametrize("name", FIXTURES)
def test_every_demo_feature_has_a_real_flow(name):
    out = run(fixture(name)["model"])
    for fid, row in out["values"]["features"].items():
        assert row["flow_gpm"] and row["flow_gpm"] > 0, (name, fid)
    assert not {"terminal.no_flow", "pump.dead_head"} & {i["code"] for i in out["issues"]}


@pytest.mark.parametrize("kit", [k.key for k in kits.KITS])
def test_a_second_derive_reproduces_the_first(kit):
    first = run(kits.apply_kit(kit))
    second = run(first["model"])
    for key, value in first["rollups"].items():
        if isinstance(value, float):
            assert second["rollups"][key] == pytest.approx(value, rel=1e-6), (kit, key)
        else:
            assert second["rollups"][key] == value, (kit, key)
    assert {i["key"] for i in first["issues"]} == {i["key"] for i in second["issues"]}


def test_the_reported_volume_is_the_basin_phase_b_sized():
    out = run(kits.apply_kit("plaza-interactive-12-jets"))
    b = out["model"]["basins"][0]
    expected = b["length_in"] * b["width_in"] * b["depth_in"] / 231.0
    assert out["rollups"]["total_basin_gallons"] == pytest.approx(expected, rel=0.01)
    assert out["values"]["basins"][b["id"]]["volume_gal"] == pytest.approx(expected, rel=0.01)


@pytest.mark.parametrize("venue, regulated", [("Pond / Lake", False), ("Event Rental", False),
                                              ("Decorative Fountain", False), ("Commercial Pool", True),
                                              ("Commercial Spa", True), ("Wading Pool", True), ("Therapy Pool", True),
                                              ("Interactive Water Feature", True)])
def test_only_pools_spas_and_utah_interactive_features_are_regulated(venue, regulated):
    assert derive.regulated_site({"venue_type": venue, "jurisdiction": "UT"}) is regulated


def test_a_pond_gets_no_code_minimum_blocker():
    model = kits.apply_kit("lobby-sheet-wall-8ft")
    model["site"]["venue_type"] = "Pond / Lake"
    model["basins"][0].update(length_in=600, width_in=600)
    assert "REG_MIN_FLOW" not in {i["code"] for i in run(model)["issues"]}


def test_the_code_minimum_is_judged_at_the_operating_flow():
    model = kits.apply_kit("lobby-sheet-wall-8ft")
    model["site"]["venue_type"] = "Commercial Spa"
    out = run(model)
    mf = next(v for k, v in out["results"].items() if k.startswith("minimum_flow"))
    blocker = next((i for i in out["issues"] if i["code"] == "REG_MIN_FLOW"), None)
    if out["rollups"]["operating_flow_gpm"] >= mf["value"]:
        assert blocker is None
    else:
        assert blocker and "(operating)" in blocker["title"]


def test_the_circulation_rollup_is_written():
    r = run(kits.apply_kit("lobby-sheet-wall-8ft"))["rollups"]
    assert r["required_circulation_gpm"] == pytest.approx(r["operating_flow_gpm"])
