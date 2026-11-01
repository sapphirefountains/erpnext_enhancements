"""The schema-2 design model (engine/model.py) and the kits (engine/kits.py).

Bench-free pytest: round-trip, validation, migration from both schema-1 shapes, and the
kit catalog (every kit applies, validates clean, matches itself, and turns "custom" the
moment a kit value changes).

Run: python -m pytest erpnext_enhancements/tests/test_water_model.py -q
"""

import copy
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering.engine import kits, model
from erpnext_enhancements.water_engineering.engine.families import run_feature

# PLAN.md section 3.3, verbatim apart from choosing one value where it lists alternatives.
PLAN_EXAMPLE = {
    "schema": 2, "mode": "quick", "kit": "lobby-sheet-wall-8ft",
    "site": {"address": "", "lat": 0, "lng": 0, "elevation_ft": 4300, "air_temp_f": 95, "water_temp_f": 70,
             "frost_depth_in": 30, "hardness_ppm": 250, "kwh_rate": 0.105, "water_rate_per_ccf": 3.53,
             "jurisdiction": "UT", "venue_type": "Decorative Fountain", "regulated": False},
    "setup": {"features": "custom", "basin": "auto", "piping": "auto", "pump": "auto", "control": "auto",
              "bypass": "auto", "surge": "auto", "treatment": "auto", "lighting": "auto",
              "electrical": "auto", "winterization": "auto"},
    "features": [{"id": "f1", "family": "weir", "type": "Waterwall (sheet)", "label": "Lobby wall",
                  "inputs": {"length_ft": 8, "fall_ft": 10, "lip": "square", "tolerance_in": 0.0625},
                  "effect": {"look": "average"}, "product": {"item": None, "generic": True},
                  "count": 1, "basin": "b1", "node": "t1"}],
    "basins": [{"id": "b1", "label": "Trough", "shape": "rectangular", "length_in": 120, "width_in": 24,
                "depth_in": 14, "freeboard_in": None, "volume_gal_override": None, "elevation_ft": 0}],
    "network": {
        "nodes": [{"id": "src", "kind": "basin", "basin": "b1", "elevation_ft": 0},
                  {"id": "p1", "kind": "pump"}, {"id": "j1", "kind": "junction", "elevation_ft": 1},
                  {"id": "t1", "kind": "terminal", "feature": "f1", "elevation_ft": 10}],
        "pumps": [{"id": "p1", "item": None, "curve": None, "speed_pct": 100, "control": "none",
                   "setpoint_psi": None, "generic_class": "end-suction"}],
        "segments": [{"id": "s1", "from": "src", "to": "p1", "line": "suction", "material": "SCH40 PVC",
                      "nominal_size": "3\"", "length_ft": 8, "fittings": [{"key": "elbow_90", "qty": 2}],
                      "components": []},
                     {"id": "s2", "from": "p1", "to": "t1", "line": "discharge", "material": "SCH40 PVC",
                      "nominal_size": None, "length_ft": 45, "fittings": [], "components": [{"key": "filter_ccp520"}]}],
        "terminals": [{"id": "t1", "kind": "open_discharge", "feature": "f1", "params": {}}],
    },
    "equipment": [{"slot": "filter", "item": None, "qty": 1, "auto": True}],
    "treatment": {"sanitizer": "chlorine", "uv": False}, "lighting": {"fixtures": [], "control": "switched"},
    "electrical": {"service_v": 240, "phase": 1, "panel_distance_ft": 60},
    "acks": [], "notes": "",
}


def problems(m):
    return {p.code for p in model.validate(m)}


# --- round trip ------------------------------------------------------------------------


def test_the_plan_example_round_trips_and_validates():
    m = model.from_dict(PLAN_EXAMPLE)
    assert m.network.segments[0].from_ == "src"
    out = m.to_dict()
    assert out["network"]["segments"][0]["from"] == "src"
    assert model.from_dict(out) == m
    assert model.validate(m) == []
    # Every key the plan wrote comes back with its value; the only additions are defaults.
    def subset(a, b):
        if isinstance(a, dict):
            return all(k in b and subset(v, b[k]) for k, v in a.items())
        if isinstance(a, list):
            return len(a) == len(b) and all(subset(x, y) for x, y in zip(a, b, strict=False))
        return a == b
    assert subset(PLAN_EXAMPLE, out)


def test_json_string_round_trip():
    s = json.dumps(PLAN_EXAMPLE)
    assert model.from_dict(s).to_dict() == model.from_dict(PLAN_EXAMPLE).to_dict()


def test_unknown_keys_survive_a_load_and_save():
    d = copy.deepcopy(PLAN_EXAMPLE)
    d["future_section"] = {"x": 1}
    d["site"]["governing_code"] = "R392-302"
    d["features"][0]["shiny"] = True
    d["network"]["segments"][1]["drain"] = True
    out = model.from_dict(d).to_dict()
    assert out["future_section"] == {"x": 1}
    assert out["site"]["governing_code"] == "R392-302"
    assert out["features"][0]["shiny"] is True
    assert out["network"]["segments"][1]["drain"] is True
    assert model.to_dict(model.from_dict(out)) == out


def test_new_model_is_valid_apart_from_the_empty_custom_features():
    m = model.new_model()
    assert m["schema"] == 2 and m["setup"]["basin"] == "auto"
    assert problems(m) == {"empty_custom"}  # features default to custom; nothing in them yet


# --- validate --------------------------------------------------------------------------


def test_validate_finds_each_structural_problem():
    d = copy.deepcopy(PLAN_EXAMPLE)
    d["features"].append({"id": "f2", "family": "volcano", "basin": "b9", "node": "t9"})
    d["network"]["segments"].append({"id": "s3", "from": "p1", "to": "nowhere"})
    d["setup"]["lighting"] = "custom"
    d["network"]["terminals"].append({"id": "t2", "kind": "sprinkler", "feature": "f7"})
    d["basins"].append(dict(d["basins"][0]))
    got = problems(d)
    assert {"unknown_family", "unknown_basin", "unknown_node", "unknown_endpoint", "empty_custom",
            "unknown_terminal_kind", "unknown_feature", "duplicate_id"} <= got


def test_validate_paths_point_at_the_row():
    d = copy.deepcopy(PLAN_EXAMPLE)
    d["features"][0]["basin"] = "b2"
    p = next(x for x in model.validate(d) if x.code == "unknown_basin")
    assert p.path == "features[0].basin" and p.severity == model.ERROR


def test_validate_never_raises_on_junk():
    assert model.validate({"schema": 1})[0].code == "schema"
    assert model.validate("not json")[0].code == "unreadable"
    model.validate({"features": [None, 3, "x"], "network": None})


def test_custom_sections_with_content_are_fine():
    d = copy.deepcopy(PLAN_EXAMPLE)
    d["setup"].update({"piping": "custom", "pump": "custom", "basin": "custom"})
    assert "empty_custom" not in problems(d)
    d["equipment"] = []
    d["setup"]["surge"] = "custom"
    assert "empty_custom" in problems(d)
    d["equipment"] = [{"slot": "surge_arrestor", "item": "X", "qty": 1, "auto": False}]
    assert "empty_custom" not in problems(d)


# --- migrate ---------------------------------------------------------------------------

ENGINE_INPUTS = {  # water_feature_design._engine_inputs(doc), schema 1
    "basins": [{"shape": "Rectangular", "length_in": 120, "width_in": 60, "height_in": 18, "diameter_in": 0,
                "surface_area_sf": 0, "volume_gal_override": 0}],
    "features": [
        {"feature_type": "Weir", "weir_length_ft": 4, "head_in": 0.25, "contractions": 0, "nozzle_count": 0,
         "gpm_each": 0, "nozzle_profile": "", "supply_head_ft": 0},
        {"feature_type": "Nozzle Array", "weir_length_ft": 0, "head_in": 0, "contractions": 2, "nozzle_count": 5,
         "gpm_each": 8, "nozzle_profile": "NP-0001", "supply_head_ft": 20, "rated_gpm": 8, "rated_head_ft": 20},
        {"feature_type": "Tiered Fountain", "tiers": [{"diameter_in": 36, "rim_height_in": 30, "spill_gpm_per_ft": 0.5}],
         "gpm_per_ft": 0.5},
    ],
    "pipe_segments": [
        {"label": "S1", "flow_gpm": 0, "nominal_size": "3\"", "material": "SCH40 PVC", "length_ft": 10,
         "line_type": "Suction", "fittings": [{"type": "90 Elbow", "qty": 2}], "components": []},
        {"label": "D1", "flow_gpm": 0, "nominal_size": "2\"", "material": "SCH40 PVC", "length_ft": 30,
         "line_type": "Discharge", "fittings": [], "components": [{"type": "Filter", "qty": 1}]},
        {"label": "D2", "flow_gpm": 0, "nominal_size": "2\"", "material": "SCH40 PVC", "length_ft": 15,
         "line_type": "Discharge", "fittings": [], "components": []},
    ],
    "static_lift_ft": 6, "turnovers_per_hr": 2, "hazen_williams_c": 130, "pump_candidates": None,
    "venue_type": "", "governing_code": "", "surface_area_sf": 0, "max_turnover_min": 0,
    "design_flow_published_gpm": 0, "bather_sf_per_person": 0, "skimmer_sf_each": 0, "skimmer_rated_gpm": 0,
    "venue_fixtures": [],
}

AI_PAYLOAD = {  # save_water_design's arguments, schema 1
    "global_inputs": {"design_title": "Plaza", "static_lift_ft": 4, "pipe_material": "SCH80 PVC", "turnover_per_hr": 3},
    "basins": [{"shape": "Cylindrical", "diameter_in": 96, "height_in": 18}],
    "features": [{"feature_type": "Splash Pad", "nozzle_count": 12, "gpm_each": 10}],
    "pipe_segments": [{"segment_label": "D", "line_type": "Discharge", "pipe_length_ft": 40,
                       "fittings_json": "[{\"type\": \"90 Elbow\", \"qty\": 3}]"}],
}


def test_migrate_engine_inputs():
    m = model.migrate(ENGINE_INPUTS)
    assert m["schema"] == 2 and m["migrated_from"] == 1
    assert model.validate(m) == []
    assert [f["family"] for f in m["features"]] == ["weir", "jets", "cascade"]
    assert m["features"][0]["inputs"]["contractions"] == 0  # a real 0 survives
    assert m["basins"][0]["depth_in"] == 18
    segs = m["network"]["segments"]
    assert [s["line"] for s in segs] == ["suction", "discharge", "discharge"]
    assert segs[0]["from"] == "src" and segs[0]["to"] == "p1"
    assert segs[1]["from"] == "p1" and segs[2]["to"] == "t1"  # discharge rows chained to the first feature
    assert segs[0]["fittings"] == [{"key": "90 Elbow", "qty": 2}]
    t1 = next(n for n in m["network"]["nodes"] if n["id"] == "t1")
    assert t1["elevation_ft"] == 6  # static lift
    assert m["legacy"]["hazen_williams_c"] == 130
    assert m["features"][1]["legacy"]["nozzle_profile"] == "NP-0001"


def test_migrated_features_size_like_the_old_engine():
    old = copy.deepcopy(ENGINE_INPUTS)
    old["features"].append({"feature_type": "Rain Curtain", "nozzle_count": 24, "gpm_each": 0.5})
    m = model.migrate(old)
    flows = [run_feature(f, m["site"])["flow"]["value"] for f in m["features"]]
    assert flows[0] == pytest.approx(36 * 4 * 0.25**1.5)  # Francis, n = 0
    assert flows[1] == pytest.approx(5 * 8)  # nozzle_array_flow
    assert flows[2] == pytest.approx(3.141592653589793 * 3 * 0.5)  # tiered_fountain_flow at 0.5 gpm/ft
    assert flows[3] == pytest.approx(24 * 0.5)  # nozzle_array_flow for the old rain curtain


def test_migrate_the_ai_payload():
    m = model.migrate(AI_PAYLOAD)
    assert model.validate(m) == []
    assert m["features"][0]["family"] == "interactive"
    assert m["features"][0]["inputs"]["play_features"] == [{"label": "Splash Pad 1", "gpm": 10.0, "count": 12}]
    assert m["basins"][0]["shape"] == "round"
    seg = m["network"]["segments"][0]
    assert seg["material"] == "SCH80 PVC" and seg["length_ft"] == 40
    assert seg["fittings"] == [{"key": "90 Elbow", "qty": 3}]
    assert m["legacy"]["turnover_per_hr"] == 3


def test_migrate_is_idempotent_on_schema_two():
    once = model.migrate(ENGINE_INPUTS)
    assert model.migrate(once) == once
    assert model.migrate(json.dumps(PLAN_EXAMPLE)) == model.from_dict(PLAN_EXAMPLE).to_dict()
    assert not model.is_legacy(PLAN_EXAMPLE)
    assert model.is_legacy(ENGINE_INPUTS) and model.is_legacy(AI_PAYLOAD)


# --- kits ------------------------------------------------------------------------------


@pytest.mark.parametrize("kit", [k.key for k in kits.KITS])
def test_every_kit_applies_validates_and_matches_itself(kit):
    m = kits.apply_kit(kit)
    assert model.validate(m) == [], model.validate(m)
    assert kits.match_kit(m) == kit
    assert model.to_dict(model.from_dict(m)) == m
    for f in m["features"]:
        out = run_feature(f, m["site"])
        assert out["flow"]["value"] is not None or f["inputs"].get("style") == "digital"


def test_every_family_has_a_kit_and_the_named_kits_exist():
    from erpnext_enhancements.water_engineering.engine.families import FAMILIES

    assert set(FAMILIES) <= {k.family for k in kits.KITS}
    for key in ("lobby-sheet-wall-8ft", "plaza-interactive-12-jets", "pond-aerator-1-acre", "event-rental-curtain"):
        assert key in kits.KITS_BY_KEY
    assert kits.KITS_BY_KEY["event-rental-curtain"].link["repo"] == "sapphirefountains/digital-rain-curtain"


def test_a_changed_value_is_custom():
    m = kits.apply_kit("lobby-sheet-wall-8ft")
    m["features"][0]["inputs"]["length_ft"] = 9
    assert kits.match_kit(m) == "custom"
    m = kits.apply_kit("lobby-sheet-wall-8ft")
    m["setup"]["pump"] = "custom"
    assert kits.match_kit(m) == "custom"
    m = kits.apply_kit("lobby-sheet-wall-8ft")
    m["basins"][0]["depth_in"] = 16
    assert kits.match_kit(m) == "custom"


def test_a_value_the_kit_does_not_set_keeps_the_match():
    m = kits.apply_kit("lobby-sheet-wall-8ft")
    m["features"][0]["inputs"]["catch_width_in"] = 24
    m["site"]["address"] = "1 Main St"
    assert kits.match_kit(m) == "lobby-sheet-wall-8ft"


def test_apply_kit_keeps_the_site_and_pins_nothing_it_should_size():
    base = model.new_model(site={"address": "1 Main St", "elevation_ft": 4300}, notes="keep me")
    m = kits.apply_kit("plaza-interactive-12-jets", base)
    assert m["site"]["address"] == "1 Main St" and m["site"]["regulated"] is True
    assert m["notes"] == "keep me"
    assert all(s["nominal_size"] is None for s in m["network"]["segments"])
    assert m["setup"]["piping"] == "auto"
    with pytest.raises(ValueError):
        kits.apply_kit("no-such-kit")


def test_unpumped_kits_build_no_network():
    for key in ("pond-aerator-1-acre", "mist-patio-20", "event-rental-curtain"):
        m = kits.apply_kit(key)
        assert m["network"]["nodes"] == [] and m["features"][0]["node"] is None
