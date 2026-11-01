"""Bench-free tests for the Fountain Designer API (``water_engineering/api/designer.py``, v1.768.0).

The page's contract is ``public/js/fountain_designer/README.md``, "The contract", and its demo
fixture ``public/js/fountain_designer/demo/boot.json`` is the reference instance of ``boot()``.
These check, against a small in-memory frappe:

* ``boot()`` carries every key of the fixture at every level, the sections come in ladder order,
  every kit headline path exists in the kit's model, and every kit model is schema 2;
* ``boot``, ``get_design`` and ``catalog_search`` are not POST-only (the page sends them as GET),
  and every write is;
* ``derive`` answers the README's keys and refuses a schema-1 model in words for a person;
* ``get_design`` lists and loads; ``save_design`` creates and updates through ``doc.save`` and
  refuses an issued design; ``acknowledge_issue`` writes the ack row and ``model.acks`` and refuses
  a blocker; ``push_quotation`` / ``push_material_request`` split Item and text lines.

Plain pytest functions with their own frappe stub: its own ``python -m pytest`` step in ci.yml.

Run: python -m pytest erpnext_enhancements/tests/test_designer_api.py -q
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

DEMO = REPO_ROOT / "erpnext_enhancements" / "public" / "js" / "fountain_designer" / "demo"
LADDER = ["features", "basin", "piping", "pump", "control", "bypass", "surge", "treatment", "lighting",
          "electrical", "winterization"]


class Thrown(Exception):
    pass


class PermissionError_(Thrown):
    pass


class Row(dict):
    def __getattr__(self, key):
        if key.startswith("__"):
            raise AttributeError(key)
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value


class FakeDoc(Row):
    """Enough of a Document for the API: get/set/append/save/check_permission."""

    saved: list = []

    def set(self, key, value):
        self[key] = value

    def append(self, key, row):
        self.setdefault(key, [])
        self[key].append(Row(row))

    def check_permission(self, ptype):
        if not self.get("_allowed", True):
            raise PermissionError_(ptype)

    def has_permission(self, ptype):
        return self.get("_allowed", True)

    def save(self):
        if not self.get("name"):
            self["name"] = f"{self['doctype'][:3].upper()}-0001"
        self.setdefault("status", "Derived")
        self.setdefault("modified", "2026-11-01 12:00:00")
        FakeDoc.saved.append(self)

    @property
    def meta(self):
        return types.SimpleNamespace(has_field=lambda f: True)


def _get_in(path, data):
    cur = data
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


@pytest.fixture()
def api(monkeypatch):
    frappe = types.ModuleType("frappe")
    utils = types.ModuleType("frappe.utils")
    store = {}
    frappe._store = store
    frappe._ = lambda msg, *a, **k: msg
    frappe.PermissionError = PermissionError_
    frappe.DoesNotExistError = Thrown

    def throw(msg, exc=None, title=None):
        raise (exc or Thrown)(msg)

    def whitelist(*a, **k):
        def wrap(f):
            f._methods = k.get("methods")
            return f
        return wrap

    frappe.throw = throw
    frappe.whitelist = whitelist
    frappe.has_permission = lambda doctype, ptype="read", doc=None, throw=False: True
    frappe.log_error = lambda *a, **k: None
    frappe.get_traceback = lambda *a, **k: ""
    frappe.session = types.SimpleNamespace(user="designer@example.com")
    frappe.cache = lambda: types.SimpleNamespace(get_value=lambda k: None, set_value=lambda *a, **k: None)
    frappe.get_cached_doc = lambda doctype: Row(kwh_rate=0.11)
    frappe.get_meta = lambda doctype: types.SimpleNamespace(has_field=lambda f: True)
    frappe.get_all = lambda *a, **k: []
    frappe.get_list = lambda doctype, **k: [Row(name="WFD-2026-00001", design_title="Lobby", modified="x")]
    frappe.defaults = types.SimpleNamespace(get_user_default=lambda key: "Sapphire Fountains")

    def get_doc(doctype, name=None):
        if (doctype, name) not in store:
            raise Thrown(f"{doctype} {name} not found")
        return store[(doctype, name)]

    frappe.get_doc = get_doc
    frappe.new_doc = lambda doctype: FakeDoc(doctype=doctype, docstatus=0)
    frappe.db = types.SimpleNamespace(get_single_value=lambda *a, **k: None, get_value=lambda *a, **k: None,
                                      exists=lambda *a, **k: True, has_column=lambda *a, **k: True)
    utils.now_datetime = lambda: "2026-11-01 12:00:00"
    utils.nowdate = lambda: "2026-11-01"
    utils.add_days = lambda d, n: "2026-11-08"
    utils.escape_html = lambda s: str(s)
    utils.strip_html = lambda s: str(s)
    frappe.utils = utils
    monkeypatch.setitem(sys.modules, "frappe", frappe)
    monkeypatch.setitem(sys.modules, "frappe.utils", utils)
    for mod in [m for m in sys.modules if m.startswith("erpnext_enhancements.water_engineering.api")]:
        monkeypatch.delitem(sys.modules, mod)
    module = importlib.import_module("erpnext_enhancements.water_engineering.api.designer")
    monkeypatch.setattr(module, "load_catalogs", lambda: {"pumps": []})
    monkeypatch.setattr(module, "site_lookup", lambda site: None)
    FakeDoc.saved = []
    module._frappe = frappe
    return module


def _boot_fixture():
    return json.loads((DEMO / "boot.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------- boot


def test_boot_carries_every_fixture_key(api):
    ref, out = _boot_fixture(), api.boot()
    assert set(ref) <= set(out)
    assert out["schema"] == 2
    assert set(ref["can"]) <= set(out["can"]) and all(isinstance(v, bool) for v in out["can"].values())
    assert set(ref["options"]) <= set(out["options"])
    assert set(ref["catalogs"]) <= set(out["catalogs"])
    assert set(ref["catalogs"]["slot_classes"]) <= set(out["catalogs"]["slot_classes"])
    classes = {c["key"] for c in out["catalogs"]["equipment_classes"]}
    assert set(out["catalogs"]["slot_classes"].values()) <= classes
    assert {f["product_class"] for f in out["families"]} - {None} <= classes
    for key, sample in (("kits", ref["kits"][0]), ("families", ref["families"][0]), ("sections", ref["sections"][0]),
                        ("house_rules", ref["house_rules"][0])):
        for row in out[key]:
            assert set(sample) <= set(row), (key, set(sample) - set(row))


def test_boot_sections_are_in_ladder_order(api):
    assert [s["key"] for s in api.boot()["sections"]] == LADDER


def test_every_kit_is_a_schema_2_model_and_its_headline_paths_exist(api):
    from erpnext_enhancements.water_engineering.engine import model as model_engine

    for kit in api.boot()["kits"]:
        m = kit["model"]
        assert m["schema"] == 2 and m["kit"] == kit["key"] and m["mode"] == "quick"
        assert not [p for p in model_engine.validate(m) if p.severity == "error"], kit["key"]
        for h in kit["headline"]:
            _get_in(h["path"], m)  # raises when the path is not in the model
            assert h["kind"] in ("number", "int", "tiles")
            if h["kind"] == "tiles":
                assert all({"value", "label", "desc"} <= set(o) for o in h["options"])


def test_family_inputs_use_the_page_kinds(api):
    for fam in api.boot()["families"]:
        assert fam["types"] and fam["inputs"]
        for row in fam["inputs"]:
            assert row["kind"] in ("number", "int", "select", "check", "text")
    assert api.boot()["families"][0]["key"] == "weir"  # "Add a feature" falls back to the first


# ----------------------------------------------------------------- methods


def test_reads_are_get_and_writes_are_post(api):
    for name in ("boot", "get_design", "catalog_search", "run_calc"):
        assert getattr(api, name)._methods is None, name
    for name in ("derive", "save_design", "acknowledge_issue", "push_quotation", "push_material_request"):
        assert getattr(api, name)._methods == ["POST"], name


# ------------------------------------------------------------------- derive


def test_derive_answers_the_readme_keys(api):
    model = _boot_fixture()["kits"][0]["model"]
    out = api.derive(json.dumps(model))
    assert set(out) == set(api.REPLY_KEYS)
    json.dumps(out, allow_nan=False)


def test_derive_refuses_a_schema_1_model(api):
    with pytest.raises(Thrown, match="before the Fountain Designer"):
        api.derive(json.dumps({"basins": [{"shape": "Rectangular"}], "pipe_segments": []}))


def test_derive_refuses_without_read(api):
    api._frappe.has_permission = lambda *a, **k: False
    with pytest.raises(PermissionError_):
        api.derive("{}")


# ------------------------------------------------------------- load / save


def test_get_design_lists_and_loads(api):
    assert api.get_design()["designs"][0]["name"] == "WFD-2026-00001"
    model = {"schema": 2, "features": []}
    api._frappe._store[("Water Feature Design", "WFD-1")] = FakeDoc(
        doctype="Water Feature Design", name="WFD-1", docstatus=1, model_json=json.dumps(model), mode="Full")
    got = api.get_design("WFD-1")
    assert got["model"] == model and got["can_write"] is False  # issued: read only
    with pytest.raises(Thrown):
        api.get_design("WFD-MISSING")


def test_save_design_creates_and_updates(api):
    model = {"schema": 2, "mode": "full", "features": []}
    out = api.save_design(None, json.dumps(model), json.dumps({"design_title": "Plaza", "customer": "ACME", "status": "Issued"}))
    doc = FakeDoc.saved[-1]
    assert out["name"] == doc["name"] and doc["design_title"] == "Plaza" and doc["mode"] == "Full"
    assert json.loads(doc["model_json"]) == model
    assert "status" not in doc or doc["status"] != "Issued"  # only the four record fields are writable
    api._frappe._store[("Water Feature Design", "WFD-2")] = FakeDoc(doctype="Water Feature Design", name="WFD-2", docstatus=1)
    with pytest.raises(Thrown, match="issued"):
        api.save_design("WFD-2", json.dumps(model), "{}")
    with pytest.raises(Thrown, match="no design"):
        api.save_design(None, "", "{}")


# ---------------------------------------------------------------- sign-offs


def _design_with_issues(api, issues, docstatus=0):
    doc = FakeDoc(doctype="Water Feature Design", name="WFD-3", docstatus=docstatus, issue_acks=[],
                  design_issues_json=json.dumps(issues), model_json=json.dumps({"schema": 2, "acks": []}))
    api._frappe._store[("Water Feature Design", "WFD-3")] = doc
    return doc


def test_acknowledge_issue_writes_the_row_and_the_model(api):
    doc = _design_with_issues(api, [{"key": "W|1", "severity": "warning", "title": "Low freeboard"}])
    out = api.acknowledge_issue("WFD-3", "W|1", "accepted")
    assert out["acks"][0]["issue_key"] == "W|1" and out["acks"][0]["by"] == "designer@example.com"
    assert doc["issue_acks"][0]["issue_key"] == "W|1"
    assert json.loads(doc["model_json"])["acks"][0]["note"] == "accepted"


def test_acknowledge_issue_refuses_a_blocker_a_closed_issue_and_an_issued_design(api):
    _design_with_issues(api, [{"key": "B|1", "severity": "blocker", "title": "x"}])
    with pytest.raises(Thrown, match="blocking"):
        api.acknowledge_issue("WFD-3", "B|1")
    with pytest.raises(Thrown, match="no longer open"):
        api.acknowledge_issue("WFD-3", "GONE")
    _design_with_issues(api, [{"key": "W|1", "severity": "warning"}], docstatus=1)
    with pytest.raises(Thrown, match="issued"):
        api.acknowledge_issue("WFD-3", "W|1")


# --------------------------------------------------------------------- push


def _design_with_bom(api, **fields):
    doc = FakeDoc(doctype="Water Feature Design", name="WFD-4", docstatus=0, bom=[
        Row(item="PUMP-A", description="Pump", qty=1, rate=1500, uom="ea"),
        Row(item=None, description="Crane day", qty=1, rate=900, uom="day"),
        Row(item="PIPE-2", description="2 in pipe", qty=0, rate=3, uom="ft"),  # zero qty: skipped
    ], **fields)
    api._frappe._store[("Water Feature Design", "WFD-4")] = doc
    return doc


def test_push_quotation_splits_item_and_text_lines(api):
    _design_with_bom(api, customer="ACME")
    out = api.push_quotation("WFD-4")
    qtn = FakeDoc.saved[-1]
    assert (out["doctype"], out["lines"], out["text_lines"]) == ("Quotation", 1, 1)
    assert out["url"] == f"/desk/quotation/{qtn['name']}"
    assert [i["item_code"] for i in qtn["items"]] == ["PUMP-A"]
    assert "Crane day" in qtn["terms"]  # no text-line Item set: listed in the terms
    assert (qtn["quotation_to"], qtn["party_name"]) == ("Customer", "ACME")


def test_push_quotation_uses_the_text_line_item_when_set(api):
    _design_with_bom(api, customer="ACME")
    api._frappe.db.get_single_value = lambda doctype, field: "TEXT-LINE" if field == "designer_text_item" else None
    api.push_quotation("WFD-4")
    assert [i["item_code"] for i in FakeDoc.saved[-1]["items"]] == ["PUMP-A", "TEXT-LINE"]


def test_push_quotation_needs_a_party(api):
    _design_with_bom(api)
    with pytest.raises(Thrown, match="customer or an opportunity"):
        api.push_quotation("WFD-4")


def test_push_material_request_sends_items_only(api):
    _design_with_bom(api, project="PROJ-1")
    out = api.push_material_request("WFD-4")
    mr = FakeDoc.saved[-1]
    assert (out["doctype"], out["lines"], out["text_lines"]) == ("Material Request", 1, 1)
    assert [(i["item_code"], i["project"]) for i in mr["items"]] == [("PUMP-A", "PROJ-1")]
    assert mr["material_request_type"] == "Purchase"
