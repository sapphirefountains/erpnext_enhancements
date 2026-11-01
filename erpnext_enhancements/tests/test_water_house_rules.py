"""Sapphire house rules: engine/rules.py, the Water Engineering Settings fields, and the
backfill patch that gives the existing Single its defaults.

Bench-free pytest. Three things are pinned:

1. **The engine's defaults and the Settings JSON cannot drift.** Every rule in
   ``rules.RULES`` is a Float field on the Single with the same default, and every field on
   the House Rules tab is a rule. The engine reads ``rules.DEFAULTS`` when it is handed no
   settings, so a drift would make the page and the engine disagree in silence.
2. **The backfill fills what is missing and never a stored value** (the Singles trap in
   ``CLAUDE.md``): run against a fake ``tabSingles`` it writes every declared default with no
   row, including ``contractor_name`` on a never-saved Single, leaves a deliberate 0 alone,
   and writes nothing the second time.
3. **The patch reads ``tabSingles`` by its own shape**, the ``test_singles_table_access``
   rule: no ordering helper aimed at ``"Singles"``, and it is registered in ``patches.txt``
   after the model sync (it needs the new fields in the meta).

Run: python -m pytest erpnext_enhancements/tests/test_water_house_rules.py -q
"""

import importlib
import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering.engine import rules as R

APP = REPO_ROOT / "erpnext_enhancements"
SETTINGS_JSON = APP / "water_engineering/doctype/water_engineering_settings/water_engineering_settings.json"
PATCH_FILE = APP / "patches/backfill_water_house_rules.py"
PATCH_MODULE = "erpnext_enhancements.patches.backfill_water_house_rules"
NO_VALUE = ("Section Break", "Column Break", "Tab Break", "HTML", "Button", "Image", "Fold", "Heading")


def settings():
    return json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))


# --- 1. rules and the Settings JSON agree ----------------------------------------------


def test_every_rule_is_a_settings_field_with_the_same_default():
    by_name = {f["fieldname"]: f for f in settings()["fields"]}
    for r in R.RULES:
        assert r.key in by_name, f"{r.key} has no field on Water Engineering Settings"
        f = by_name[r.key]
        assert f["fieldtype"] == "Float", r.key
        assert float(f["default"]) == pytest.approx(r.default), r.key


def test_every_house_rules_tab_value_field_is_a_rule():
    d = settings()
    order = d["field_order"]
    tab = order.index("tab_house_rules")
    by_name = {f["fieldname"]: f for f in d["fields"]}
    for name in order[tab + 1:]:
        if by_name[name]["fieldtype"] in NO_VALUE or name == R.SET_ON_FIELD:
            continue
        assert name in R.RULES_BY_KEY, f"{name} is on the House Rules tab but not in rules.RULES"
    assert set(order) == set(by_name), "field_order and fields disagree"


def test_settings_json_was_touched_so_migrate_syncs_it():
    assert settings()["modified"] >= "2026-11-01"


def test_the_required_house_rules_have_their_planned_defaults():
    assert R.DEFAULTS["freeboard_multiplier"] == 1.3
    assert R.DEFAULTS["water_rate_per_ccf"] == 3.53  # SLC FY26
    assert R.DEFAULTS["kwh_rate"] == pytest.approx(0.1049)  # RMP proposed 2026
    for fam in R.SPRAY_FAMILIES:
        assert f"spray_loss_pct_{fam}" in R.DEFAULTS
    for fam in R.LABOR_FAMILIES:
        assert R.DEFAULTS[f"labor_hours_{fam}"] == 0.0
    for key in ("foam_submergence_min_in", "foam_submergence_max_in", "light_watts_per_ft_jet", "wind_reduce_mph",
                "wind_stop_mph", "liner_overlap_ft", "labor_hours_per_sqft_basin"):
        assert key in R.DEFAULTS


# --- rules.resolve and citations -------------------------------------------------------


def test_resolve_overlays_and_never_raises():
    out = R.resolve({"freeboard_multiplier": "1.5", "wind_stop_mph": None, "liner_overlap_ft": "",
                     "kwh_rate": "abc", "unknown": 7, R.SET_ON_FIELD: "2026-12-01"})
    assert out["freeboard_multiplier"] == 1.5
    assert out["wind_stop_mph"] == R.DEFAULTS["wind_stop_mph"]
    assert out["liner_overlap_ft"] == R.DEFAULTS["liner_overlap_ft"]
    assert out["kwh_rate"] == R.DEFAULTS["kwh_rate"]
    assert "unknown" not in out
    assert R.resolve()["freeboard_multiplier"] == 1.3
    assert R.rule(None, "freeboard_multiplier") == 1.3


def test_house_rules_cite_the_placeholder_until_a_date_is_set():
    assert R.citation("freeboard_multiplier") .startswith("Sapphire standard, 2026-11-01")
    assert "placeholder" in R.citation("foam_submergence_min_in")
    assert R.citation("foam_submergence_min_in", {R.SET_ON_FIELD: "2026-12-01"}) == "Sapphire standard, 2026-12-01"
    assert "slc.gov" in R.citation("water_rate_per_ccf", {R.SET_ON_FIELD: "2026-12-01"})


def test_a_rule_saved_as_zero_or_negative_falls_back_to_its_default():
    # Frappe stores a cleared Float as 0; a 0 fixture size or aspect ratio would divide by zero.
    out = R.resolve({"light_fixture_watts": 0, "aerator_max_aspect": "0", "wind_stop_mph": -5,
                     "freeboard_multiplier": float("nan"), "labor_hours_weir": 0, "spray_loss_pct_jets": 0})
    assert out["light_fixture_watts"] == R.DEFAULTS["light_fixture_watts"]
    assert out["aerator_max_aspect"] == R.DEFAULTS["aerator_max_aspect"]
    assert out["wind_stop_mph"] == R.DEFAULTS["wind_stop_mph"]
    assert out["freeboard_multiplier"] == R.DEFAULTS["freeboard_multiplier"]
    assert out["labor_hours_weir"] == 0.0  # 0 = not set, kept
    assert out["spray_loss_pct_jets"] == 0.0  # 0 = no spray loss, kept
    assert R.rule({"light_fixture_watts": 0}, "light_fixture_watts") == R.DEFAULTS["light_fixture_watts"]


def test_zeroed_settings_never_crash_the_engine():
    from erpnext_enhancements.water_engineering.engine.families import aerator, lighting

    zeros = R.resolve({r.key: 0 for r in R.RULES})
    m = {"site": {}, "features": [{"id": "w", "family": "weir", "inputs": {"length_ft": 8}}], "lighting": {}}
    assert lighting.calc(m, rules=zeros).value > 0
    aerator.flow({"id": "a", "inputs": {"length_ft": 1000, "width_ft": 100}}, rules=zeros)


def test_labor_zero_means_not_set():
    assert R.is_unset(None, "labor_hours_weir")
    assert not R.is_unset({"labor_hours_weir": 12}, "labor_hours_weir")
    assert not R.is_unset(None, "freeboard_multiplier")


def test_house_rule_rows_for_the_page():
    rows = R.house_rule_rows()
    assert len(rows) == len(R.RULES)
    assert {"key", "label", "unit", "value", "default", "citation", "house"} <= set(rows[0])


def test_the_engine_reads_a_changed_rule():
    from erpnext_enhancements.water_engineering.engine.families import foam

    f = {"id": "f", "inputs": {"height_ft": 4, "depth_over_nozzle_in": 3, "check_valve": True}}
    assert foam.band(f)[:2] == (2.0, 4.0)
    assert foam.band(f, R.resolve({"foam_submergence_min_in": 1, "foam_submergence_max_in": 2}))[:2] == (1.0, 2.0)


# --- 2. the backfill patch -------------------------------------------------------------


class FakeDB:
    def __init__(self, rows, doctype_exists=True):
        self.singles = dict(rows)  # field -> value, the fake tabSingles for this doctype
        self.doctype_exists = doctype_exists
        self.writes = []

    def exists(self, doctype, name=None):
        return self.doctype_exists if (doctype, name) == ("DocType", "Water Engineering Settings") else None

    def sql(self, query, params=()):
        assert "tabSingles" in query and params == ("Water Engineering Settings",)
        return [(k,) for k in self.singles]

    def set_single_value(self, doctype, field, value):
        assert doctype == "Water Engineering Settings"
        self.singles[field] = value
        self.writes.append(field)


@pytest.fixture
def patch(monkeypatch):
    meta_fields = [types.SimpleNamespace(**{"fieldtype": f["fieldtype"], "fieldname": f["fieldname"],
                                            "default": f.get("default")}) for f in settings()["fields"]]
    frappe = types.ModuleType("frappe")
    frappe_model = types.ModuleType("frappe.model")
    frappe_model.no_value_fields = (*NO_VALUE, "Table", "Table MultiSelect")
    frappe.model = frappe_model
    frappe.get_meta = lambda doctype: types.SimpleNamespace(fields=meta_fields)
    frappe.logged = []
    frappe.log_error = lambda title=None, message=None: frappe.logged.append(title)
    frappe.get_traceback = lambda: "traceback"
    frappe.cleared = []
    frappe.clear_document_cache = lambda *a: frappe.cleared.append(a)
    monkeypatch.setitem(sys.modules, "frappe", frappe)
    monkeypatch.setitem(sys.modules, "frappe.model", frappe_model)
    sys.modules.pop(PATCH_MODULE, None)
    mod = importlib.import_module(PATCH_MODULE)
    yield mod, frappe
    sys.modules.pop(PATCH_MODULE, None)


def test_backfill_fills_every_missing_default_on_a_never_saved_single(patch):
    mod, frappe = patch
    frappe.db = FakeDB({})
    written = mod.backfill_water_house_rules()
    assert written == len(R.RULES) + 1  # every rule plus contractor_name's "Sapphire Fountains"
    assert frappe.db.singles["contractor_name"] == "Sapphire Fountains"
    assert float(frappe.db.singles["freeboard_multiplier"]) == 1.3
    assert R.SET_ON_FIELD not in frappe.db.singles  # no default, so no row
    assert frappe.cleared


def test_backfill_never_overwrites_a_stored_value_even_zero(patch):
    mod, frappe = patch
    frappe.db = FakeDB({"engineer_name": "A. Engineer", "contractor_name": "Other Co", "freeboard_multiplier": "0"})
    mod.backfill_water_house_rules()
    assert frappe.db.singles["freeboard_multiplier"] == "0"
    assert frappe.db.singles["contractor_name"] == "Other Co"
    assert "freeboard_multiplier" not in frappe.db.writes
    assert float(frappe.db.singles["wind_stop_mph"]) == 20.0


def test_backfill_is_safe_twice(patch):
    mod, frappe = patch
    frappe.db = FakeDB({})
    mod.execute()
    frappe.db.writes.clear()
    assert mod.backfill_water_house_rules() == 0
    assert frappe.db.writes == []


def test_a_failing_backfill_logs_and_never_aborts_the_migrate(patch):
    mod, frappe = patch
    db = FakeDB({})

    def broken(*a, **k):
        raise RuntimeError("database went away")

    db.sql = broken
    frappe.db = db
    mod.execute()  # must not raise: a raising patch aborts bench migrate, which is the deploy
    assert frappe.logged == ["Water house rules backfill failed"]


def test_backfill_does_nothing_without_the_doctype(patch):
    mod, frappe = patch
    frappe.db = FakeDB({}, doctype_exists=False)
    assert mod.backfill_water_house_rules() == 0


# --- 3. the Singles-table rule and the registration ------------------------------------


def test_the_patch_reads_tabsingles_by_its_own_shape():
    sys.path.insert(0, str(APP / "tests"))
    try:
        from test_singles_table_access import _offenders
    finally:
        sys.path.pop(0)
    assert list(_offenders(PATCH_FILE)) == []
    src = PATCH_FILE.read_text(encoding="utf-8")
    assert "set_single_value" in src and "select field from tabSingles" in src


def test_the_patch_is_registered_after_the_model_sync():
    lines = (APP / "patches.txt").read_text(encoding="utf-8").splitlines()
    post = lines.index("[post_model_sync]")
    entry = "erpnext_enhancements.patches.backfill_water_house_rules"
    assert entry in lines and lines.index(entry) > post
