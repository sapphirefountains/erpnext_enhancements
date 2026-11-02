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
    frappe.ValidationError = Thrown

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
    module._real_site_lookup = module.site_lookup
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


def test_boot_lists_only_the_print_formats_this_site_has(api):
    """Phase G: the Outputs step links each paper to its Print Format; a format after_migrate could not
    seed is left out, so the page says "not available yet" instead of linking to a 404."""
    from erpnext_enhancements.water_engineering import prints

    seeded = [pf for _k, _l, pf in prints.OUTPUTS] + [prints.PF_PACKET]
    asked = []

    def get_all(doctype, filters=None, pluck=None, **k):
        if doctype != "Print Format":
            return []
        asked.append(filters)
        return [n for n in filters["name"][1] if n in seeded]

    api._frappe.get_all = get_all
    every = api.boot()["prints"]
    assert [r["key"] for r in every] == [k for k, _l, _pf in prints.OUTPUTS] + ["packet"]
    assert every == _boot_fixture()["prints"]
    assert len(asked) == 1 and asked[0]["disabled"] == 0, "one query, enabled formats only"
    seeded[:] = [pf for pf in seeded if pf != prints.PF_SCHEDULES]
    keys = {r["key"] for r in api.boot()["prints"]}
    assert not keys & {"equipment", "piping", "fittings"}
    assert {"budget", "packet", "calcs"} <= keys

    def broken(*a, **k):
        raise RuntimeError("db gone")

    api._frappe.get_all = broken
    assert api.boot()["prints"] == []


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


def _emailable(api, **extra):
    from erpnext_enhancements.water_engineering import prints

    doc = FakeDoc(doctype="Water Feature Design", name="WFD-9", design_title="Lobby & wall", docstatus=0, budget_total=12000,
                  modified="2026-11-01 12:00:00",
                  results_json=json.dumps({"rollups": {"operating_flow_gpm": 80.0, "total_basin_gallons": 200.0}}), **extra)
    api._frappe._store[("Water Feature Design", "WFD-9")] = doc
    seeded = list(prints.PRINT_FORMATS)

    def get_all(doctype, filters=None, pluck=None, **k):
        if doctype == "Print Format":
            return [n for n in filters["name"][1] if n in seeded]
        return []

    api._frappe.get_all = get_all
    return doc, seeded


def test_email_draft_starts_the_email_and_sends_nothing(api, monkeypatch):
    """Phase G, Send design: subject, the customer's address (the prints' party lookup), the starting text
    and the papers this site has. Nothing is sent and, without attach_pdf, nothing is made."""
    from erpnext_enhancements import print_lookup
    from erpnext_enhancements.water_engineering import prints

    _doc, seeded = _emailable(api, customer="ACME")
    monkeypatch.setattr(print_lookup, "party_details", lambda doc: {"email": "pm@acme.example"})
    out = api.email_draft("WFD-9")
    assert out["print_format"] == prints.PF_REPORT, "the Design Report unless another is picked"
    assert out["subject"] == "Lobby & wall: Design report (#WFD-9)"
    assert out["recipients"] == "pm@acme.example"
    assert [f["print_format"] for f in out["formats"]] == list(prints.PRINT_FORMATS)
    assert out["attachment"] is None
    assert api.email_draft("WFD-9", prints.PF_PACKET)["subject"].endswith("Submittal packet (#WFD-9)")
    seeded.remove(prints.PF_BUDGET)
    assert prints.PF_BUDGET not in [f["print_format"] for f in api.email_draft("WFD-9")["formats"]], \
        "a paper this site could not seed is not offered (boot().prints' filter)"


def test_email_draft_text_is_plain_for_the_composer(api):
    """frappe's composer is a rich-text editor: it strips a <style> block and mangles tables and MSO
    comments, so the starting text is plain paragraphs and the email chrome goes on at send time."""
    from erpnext_enhancements.water_engineering import prints

    doc, _seeded = _emailable(api)
    doc["results_json"] = json.dumps({"rollups": {"operating_flow_gpm": 80.0}, "paper": {"budget_rules": {"contingency_pct": 10}}})
    msg = api.email_draft("WFD-9", prints.PF_BUDGET)["message"]
    assert msg.startswith("<p>Attached is the budget proposal for Lobby & wall.</p>")
    # budget_total is the paper's Budget (the subtotal plus the contingency), and the email says so
    assert "<strong>Budget (with 10% contingency):</strong> $12,000" in msg and "<strong>Flow:</strong> 80 gpm" in msg
    for tag in ("<style", "<table", "<!--", "max-width"):
        assert tag not in msg
    assert "Budget" not in api.email_draft("WFD-9", prints.PF_CALCS)["message"]
    assert api.email_draft("WFD-9")["recipients"] == "", "no customer, no address"


def test_a_design_email_goes_out_in_the_email_chrome(api, monkeypatch):
    """The composer edits plain text; email_hooks.wrap_design_email puts the shell on the in-memory
    content frappe's make() sends right after insert. after_insert, not before: the Text Editor sanitizer
    runs between the two and strips the shell's <style> and Outlook comments."""
    from erpnext_enhancements import email_style
    from erpnext_enhancements.water_engineering import email_hooks

    calls = []
    monkeypatch.setattr(email_style, "wrap", lambda body, **k: calls.append(k) or f"<shell>{body}</shell>")
    comm = Row(reference_doctype="Water Feature Design", communication_medium="Email", sent_or_received="Sent",
               subject="Lobby: Design report (#WFD-9)", content="<p>Hi</p>")
    email_hooks.wrap_design_email(comm)
    assert comm.content == "<shell><p>Hi</p></shell>"
    assert calls == [{"title": "Lobby: Design report", "pillar": "design", "tagline": True}], \
        "no eyebrow: the shell already names the Design pillar"
    for other in (dict(reference_doctype="Lead"), dict(sent_or_received="Received"), dict(communication_medium="Phone")):
        c = Row(**{**dict(comm, content="<p>x</p>"), **other})
        email_hooks.wrap_design_email(c)
        assert c.content == "<p>x</p>", other
    monkeypatch.setattr(email_style, "wrap", lambda *a, **k: 1 / 0)
    broken = Row(**dict(comm, content="<p>y</p>"))
    email_hooks.wrap_design_email(broken)  # never raises: a failed wrap must not stop an email
    assert broken.content == "<p>y</p>"
    hooks = (REPO_ROOT / "erpnext_enhancements" / "hooks.py").read_text(encoding="utf-8")
    block = hooks[hooks.index('"Communication": {'):]
    block = block[:block.index("},")]
    assert '"after_insert": [' in block and "water_engineering.email_hooks.wrap_design_email" in block
    assert "before_insert" not in block, "before_insert runs ahead of the sanitizer"
    src = (REPO_ROOT / "erpnext_enhancements" / "water_engineering" / "email_hooks.py").read_text(encoding="utf-8")
    assert "derive" not in src.split('"""', 2)[2] and "api.designer" not in src, \
        "the hook runs for every Communication: it must not import the engine"


def test_email_draft_refuses_what_it_should(api):
    from erpnext_enhancements.water_engineering import prints

    _doc, seeded = _emailable(api)
    with pytest.raises(Thrown):
        api.email_draft("WFD-9", "Sales Invoice - Sapphire")
    seeded.remove(prints.PF_BUDGET)
    with pytest.raises(Thrown):
        api.email_draft("WFD-9", prints.PF_BUDGET)  # a paper this site could not seed is not accepted
    seeded.remove(prints.PF_REPORT)
    assert api.email_draft("WFD-9")["print_format"] == prints.PRINT_FORMATS[1], \
        "no Design Report here: the first paper the site has"
    seeded.clear()
    with pytest.raises(Thrown):
        api.email_draft("WFD-9")
    seeded.extend(prints.PRINT_FORMATS)
    api._frappe.has_permission = lambda doctype, ptype="read", doc=None, throw=False: ptype != "email"
    with pytest.raises(PermissionError_):
        api.email_draft("WFD-9")


def test_email_draft_makes_the_pdf_for_the_page_and_reuses_it(api):
    """The page's composer has no form to print from, so the paper comes as a File on the design. Its
    name carries "-print-", which keeps the packet from merging it as a CAD drawing, and the one made
    since the design last changed is reused rather than piling up."""
    from erpnext_enhancements.water_engineering import prints

    _doc, seeded = _emailable(api)
    made, files = [], []
    api._frappe.get_print = lambda doctype, name, print_format=None, as_pdf=False, pdf_generator=None: (
        made.append((doctype, name, print_format, as_pdf, pdf_generator)) or b"%PDF")
    real_get_doc = api._frappe.get_doc
    print_formats = api._frappe.get_all

    def get_doc(doctype, name=None):
        if isinstance(doctype, dict):
            def insert(**k):
                row = {"name": f"F-{len(files) + 1}", "file_name": doctype["file_name"],
                       "file_url": "/private/files/" + doctype["file_name"]}
                files.append(row)
                return types.SimpleNamespace(**row)
            return types.SimpleNamespace(insert=insert, **doctype)
        return real_get_doc(doctype, name)

    def get_all(doctype, filters=None, **k):
        if doctype == "File":
            assert filters["creation"] == [">=", "2026-11-01 12:00:00"], "only one made since the design last changed"
            return list(reversed(files))[:1]
        return print_formats(doctype, filters=filters, **k)

    api._frappe.get_doc = get_doc
    api._frappe.get_all = get_all
    out = api.email_draft("WFD-9", prints.PF_SCHEDULES, attach_pdf=1)
    assert made == [("Water Feature Design", "WFD-9", prints.PF_SCHEDULES, True, "chrome")]
    assert out["attachment"] == {"name": "F-1", "file_name": "WFD-9-print-schedules.pdf",
                                 "file_url": "/private/files/WFD-9-print-schedules.pdf"}
    again = api.email_draft("WFD-9", prints.PF_SCHEDULES, attach_pdf=1)
    assert len(made) == 1 and again["attachment"]["name"] == "F-1", "the same paper is reused"
    packet = (REPO_ROOT / "erpnext_enhancements" / "water_engineering" / "packet.py").read_text(encoding="utf-8")
    assert '"-print-" in' in packet, "the packet skips the emailed papers"


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


def test_pushed_rows_carry_the_stock_uom(api):
    _design_with_bom(api, customer="ACME")
    api._frappe.get_all = lambda doctype, **k: [Row(name="PUMP-A", stock_uom="Nos")] if doctype == "Item" else []
    api.push_quotation("WFD-4")
    row = FakeDoc.saved[-1]["items"][0]
    assert (row["uom"], row["stock_uom"], row["conversion_factor"]) == ("Nos", "Nos", 1)
    api.push_material_request("WFD-4")
    assert FakeDoc.saved[-1]["items"][0]["uom"] == "Nos"


# ------------------------------------------------------------------ the #1399 review


def test_the_page_derive_never_geocodes(api, monkeypatch):
    def boom(site):
        raise AssertionError("geocoded on a page derive")

    monkeypatch.setattr(api, "site_lookup", boom)
    api.derive(json.dumps(_boot_fixture()["kits"][0]["model"]))  # must not call it
    seen = []
    monkeypatch.setattr(api, "site_lookup", lambda site: seen.append(site) or None)
    api.run_derive(_boot_fixture()["kits"][0]["model"], geocode=True)
    assert seen  # a save does


def test_a_geocode_failure_is_cached_briefly(api, monkeypatch):
    stored = []
    api._frappe.cache = lambda: types.SimpleNamespace(
        get_value=lambda k: None, set_value=lambda k, v, expires_in_sec=None: stored.append(expires_in_sec))
    sites = types.ModuleType("erpnext_enhancements.workforce.sites")
    monkeypatch.setitem(sys.modules, "erpnext_enhancements.workforce.sites", sites)
    for problem, ttl in (("OVER_QUERY_LIMIT", api.GEOCODE_RETRY_S), ("ZERO_RESULTS", api.GEOCODE_CACHE_S)):
        sites.geocode_text = lambda address, problem=problem: (None, problem)
        assert api._real_site_lookup({"address": "1 Main St"}) is None
        assert stored[-1] == ttl
    sites.geocode_text = lambda address: ({"lat": 40.7, "lng": -111.9}, None)
    assert api._real_site_lookup({"address": "2 Main St"}) == {"lat": 40.7, "lng": -111.9}
    assert stored[-1] == api.GEOCODE_CACHE_S


def test_the_bom_rate_is_the_selling_price_and_valuation_only_for_who_may_see_it(api, monkeypatch):
    monkeypatch.setattr(api, "list_rates", lambda codes: {c: 100 for c in codes if c == "LISTED"})
    monkeypatch.setattr(api, "valuation_rates", lambda codes: {c: 60 for c in codes})
    model = {"network": {"pumps": [{"item": "LISTED"}, {"item": "COSTED"}]}}
    monkeypatch.setattr(api, "can_read_valuation", lambda: False)
    assert api.load_prices(model)["items"] == {"LISTED": {"rate": 100}}
    monkeypatch.setattr(api, "can_read_valuation", lambda: True)
    assert api.load_prices(model)["items"] == {"LISTED": {"rate": 100}, "COSTED": {"rate": 60}}


def test_a_pushed_unit_is_converted_or_flagged(api):
    uoms = {"PIPE": {"stock": "Stick", "factors": {"Foot": 0.05}}, "PUMP": {"stock": "Nos", "factors": {}},
            "ODD": {"stock": "Stick", "factors": {}}}
    row, odd = api._item_row({"item_code": "PIPE", "qty": 40, "description": "2 in pipe", "uom_note": "ft"}, uoms)
    assert (row["uom"], row["conversion_factor"], odd) == ("Foot", 0.05, False)
    row, odd = api._item_row({"item_code": "PUMP", "qty": 1, "description": "Pump", "uom_note": "ea"}, uoms)
    assert (row["uom"], row["conversion_factor"], odd) == ("Nos", 1, False)
    row, odd = api._item_row({"item_code": "ODD", "qty": 11, "description": "Rail", "uom_note": "ft"}, uoms)
    assert odd and row["uom"] == "Stick" and "11 ft" in row["description"]


def test_a_partial_ai_payload_changes_only_what_it_names(api):
    from erpnext_enhancements.water_engineering.engine import kits as kits_engine

    wd = api  # merge_legacy_payload moved here from api/water_design.py in v1.771.0

    existing = kits_engine.apply_kit("lobby-sheet-wall-8ft")
    existing["lighting"]["fixtures"] = [{"qty": 2, "watts": 35}]
    model, ignored = wd.merge_legacy_payload(existing, {}, {"venue_type": "Commercial Spa", "static_lift_ft": 6})
    assert model["site"]["venue_type"] == "Commercial Spa"
    assert model["features"] == existing["features"] and model["lighting"] == existing["lighting"]
    assert ignored == ["static_lift_ft"]
    basin = {"shape": "Rectangular", "length_in": 60, "width_in": 30, "height_in": 12}
    model, ignored = wd.merge_legacy_payload(existing, {"basins": [basin]}, {})
    assert model["basins"][0]["length_in"] == 60 and model["setup"]["basin"] == "custom"
    assert model["features"][0]["basin"] == model["basins"][0]["id"]
    assert model["network"] == existing["network"] and model["kit"] == existing["kit"]
    segs = [{"line_type": "Discharge", "nominal_size": '2"', "pipe_length_ft": 30}]
    model, ignored = wd.merge_legacy_payload(existing, {"pipe_segments": segs, "electrical_loads": [{"hp": 1}]}, {})
    assert model["network"] == existing["network"]  # segments alone cannot be laid against drawn features
    assert any(i.startswith("pipe_segments") for i in ignored) and any(i.startswith("electrical_loads") for i in ignored)


def test_a_features_only_payload_keeps_them_in_a_basin_and_a_pump_pick_brings_its_curve(api):
    from erpnext_enhancements.water_engineering.engine import kits as kits_engine

    wd = api  # merge_legacy_payload moved here from api/water_design.py in v1.771.0

    existing = kits_engine.apply_kit("lobby-sheet-wall-8ft")
    existing["network"]["pumps"][0].update(item="OLD", curve={"points": [{"flow_gpm": 0, "head_ft": 9}]}, hp=1.5)
    feature = {"feature_type": "Weir", "weir_length_ft": 6, "head_in": 0.25}
    model, _ = wd.merge_legacy_payload(existing, {"features": [feature], "pumps": [{"pump_item": "NEW-5HP"}]}, {})
    assert model["features"][0]["basin"] == existing["basins"][0]["id"]
    pump = model["network"]["pumps"][0]
    assert (pump["item"], pump["curve"]) == ("NEW-5HP", "NEW-5HP") and "hp" not in pump


def test_a_text_line_on_the_placeholder_item_is_not_a_unit_mismatch(api):
    row = {"item_code": "TEXT-LINE", "qty": 2, "description": "Concrete (2 Cubic Yard)", "uom_note": None}
    built, odd = api._item_row(row, {"TEXT-LINE": {"stock": "Nos", "factors": {}}})
    assert not odd and built["uom"] == "Nos" and built["description"] == "Concrete (2 Cubic Yard)"


# ------------------------------------------- the AI tools and the calculator (v1.771.0, phase H)


def _stored_design(api, name="WFD-7", model=None, **fields):
    from erpnext_enhancements.water_engineering.engine import kits as kits_engine

    model = model or kits_engine.apply_kit("lobby-sheet-wall-8ft")
    doc = FakeDoc(doctype="Water Feature Design", name=name, docstatus=0, model_json=json.dumps(model), **fields)
    api._frappe._store[("Water Feature Design", name)] = doc
    return doc, model


def test_tool_model_takes_a_schema_2_model_whole_and_names_what_it_set_aside(api):
    from erpnext_enhancements.water_engineering.engine import kits as kits_engine

    sent = kits_engine.apply_kit("plaza-interactive-12-jets")
    model, ignored = api.tool_model(kits_engine.apply_kit("lobby-sheet-wall-8ft"), sent,
                                    {"basins": [{"length_in": 10}]}, {"static_lift_ft": 4})
    assert model == sent and model is not sent
    assert [i.split(" ")[0] for i in ignored] == ["basins", "static_lift_ft"]
    with pytest.raises(Thrown, match="schema 2"):
        api.tool_model(None, {"basins": [], "pipe_segments": []})
    with pytest.raises(Thrown, match="schema 2"):
        api.tool_model(None, {"schema": 1, "features": []})


def test_tool_model_merges_the_old_tables_onto_a_schema_2_design_and_migrates_a_new_one(api):
    from erpnext_enhancements.water_engineering.engine import model as model_engine

    _, existing = _stored_design(api)
    basin = {"shape": "Rectangular", "length_in": 60, "width_in": 30, "height_in": 12}
    merged, ignored = api.tool_model(existing, None, {"basins": [basin]}, {"turnover_per_hr": 3})
    assert merged["basins"][0]["length_in"] == 60 and merged["setup"]["basin"] == "custom"
    assert merged["features"] == existing["features"] and ignored == ["turnover_per_hr"]
    same, ignored = api.tool_model(existing, None, {}, {})
    assert same == existing and ignored == []
    fresh, ignored = api.tool_model(None, None, {"basins": [basin], "electrical_loads": [{"hp": 1}]},
                                    {"venue_type": "Decorative Fountain", "hazen_williams_c": 140})
    assert fresh["schema"] == model_engine.SCHEMA and not model_engine.is_legacy(fresh)
    assert fresh["basins"] and fresh["acks"] == []
    assert ignored == ["electrical_loads (the Fountain Designer derives these)", "hazen_williams_c"]


def test_tool_save_goes_through_save_design_with_the_record_fields(api):
    doc, existing = _stored_design(api, design_title="Old")
    name, ignored = api.tool_save("WFD-7", None, {"basins": [{"length_in": 48, "width_in": 48, "height_in": 12}]},
                                  {}, {"design_title": "New wall", "project": "PROJ-1", "serial_no": "S1"})
    saved = FakeDoc.saved[-1]
    assert name == "WFD-7" and saved is doc
    assert (saved["design_title"], saved["project"]) == ("New wall", "PROJ-1")
    assert json.loads(saved["model_json"])["basins"][0]["length_in"] == 48
    assert ignored == ["serial_no (set it on the record in ERPNext)"]
    new_name, _ = api.tool_save(None, existing, None, None, {"design_title": "Fresh"})
    assert new_name == FakeDoc.saved[-1]["name"] and FakeDoc.saved[-1]["design_title"] == "Fresh"
    api._frappe.db.exists = lambda *a, **k: False
    with pytest.raises(Thrown, match="not found"):
        api.tool_save("WFD-404", existing)
    doc["docstatus"] = 1
    api._frappe.db.exists = lambda *a, **k: True
    with pytest.raises(Thrown, match="issued"):
        api.tool_save("WFD-7", existing)


def test_design_status_is_what_the_page_shows_with_its_links(api, monkeypatch):
    issues = [{"key": "A|x", "severity": "warning", "title": "Trough surge", "section": "basin"}]
    readiness = {"sections": [], "calc_ready": True, "issue_ready": False}
    results = {"verdict": {"level": "warn", "headline": "Works, with one warning."},
               "rollups": {"design_flow_gpm": 120.0, "tdh_ft": 31.5},
               "results": {"weir_flow": {"value": 120, "unit": "GPM", "formula": "Q = 3.33 L H^1.5", "steps": ["a"],
                                         "citations": ["DOC-0049"], "warnings": []}}}
    _stored_design(api, design_issues_json=json.dumps(issues), readiness_json=json.dumps(readiness),
                   results_json=json.dumps(results), warning_count=1, design_flow_gpm=120.0, selected_pump=None,
                   next_inputs_needed="Pump\nLighting", bom=[Row(item="P1")])
    monkeypatch.setattr(api, "print_formats", lambda: [
        {"key": "report", "label": "Design report", "print_format": "Water Feature Design - Design Report"},
        {"key": "packet", "label": "Submittal packet", "print_format": "Water Feature Design - Submittal Packet"}])
    state = api.design_status("WFD-7")
    assert state["verdict"] == results["verdict"] and state["rollups"] == results["rollups"]
    assert state["issues"] == issues and state["readiness"] == readiness and state["warning_count"] == 1
    assert state["next_inputs_needed"] == ["Pump", "Lighting"] and state["counts"]["bom_lines"] == 1
    assert state["designer_url"] == "/desk/fountain-designer?design=WFD-7&step=review"
    assert [p["key"] for p in state["prints"]] == ["report"]
    assert state["packet"]["url"].startswith("/printview?doctype=Water+Feature+Design&name=WFD-7&format=")
    assert state["calc_results"][0]["calc"] == "weir_flow" and "model" not in state
    assert api.design_status("WFD-7", include_results=False, include_model=True)["model"]["schema"] == 2
    api._frappe._store[("Water Feature Design", "WFD-7")]["_allowed"] = False
    with pytest.raises(PermissionError_):
        api.design_status("WFD-7")


def test_design_status_falls_back_to_the_verdict_of_the_stored_issues(api, monkeypatch):
    monkeypatch.setattr(api, "print_formats", lambda: [])
    issues = [{"key": "B", "severity": "blocker", "title": "No UV", "section": "treatment"}]
    _stored_design(api, design_issues_json=json.dumps(issues),
                   readiness_json=json.dumps({"sections": [], "calc_ready": True, "issue_ready": False}))
    assert api.design_status("WFD-7")["verdict"]["level"] == "bad"


def test_run_calc_dispatches_the_calculator_and_refuses_an_unknown_one(api):
    out = api.run_calc("weir_flow", json.dumps({"length_ft": 4, "head_in": 0.5}))
    assert out["calc"] == "weir_flow" and out["value"] > 0 and out["formula"]
    with pytest.raises(Thrown, match="Unknown calculation"):
        api.run_calc("no_such_calc", "{}")
    api._frappe.has_permission = lambda *a, **k: False
    with pytest.raises(PermissionError_):
        api.run_calc("weir_flow", "{}")


def test_boot_lists_the_calculator_and_the_gate_moved_here(api):
    from erpnext_enhancements.water_engineering import calculator

    assert [c["key"] for c in api.boot()["calculator"]] == list(calculator.CALCS)
    assert api.check_permission() is True
    wd = importlib.import_module("erpnext_enhancements.water_engineering.api.water_design")
    for name in ("check_permission", "run_calc", "item_pump_curves", "pump_curves", "nozzle_profile_params"):
        assert getattr(wd, name) is getattr(api, name), name  # deprecated re-exports, the same functions
    for gone in ("design_state", "save_inputs", "_save_design", "_run_calc", "get_design_issues", "merge_legacy_payload"):
        assert not hasattr(wd, gone), gone


def test_tool_model_converts_an_unconverted_design_before_laying_the_payload_on(api):
    from erpnext_enhancements.water_engineering.engine import model as model_engine

    stored = {"basins": [{"shape": "Rectangular", "length_in": 96, "width_in": 48, "height_in": 18}],
              "features": [{"feature_type": "Weir", "weir_length_ft": 4, "head_in": 0.25}],
              "global_inputs": {}, "acks": [{"issue_key": "K", "note": "ok"}]}
    assert model_engine.is_legacy(stored)
    kept, ignored = api.tool_model(stored, None, {}, {})
    assert kept["schema"] == 2 and kept["basins"][0]["length_in"] == 96 and kept["features"], "a title-only save keeps it"
    assert kept["acks"] == [{"issue_key": "K", "note": "ok"}] and ignored == []
    merged, _ = api.tool_model(stored, None, {"basins": [{"shape": "Rectangular", "length_in": 60}]}, {})
    assert merged["basins"][0]["length_in"] == 60 and merged["features"], "its features survive a basins-only payload"


def test_run_calc_reads_unreadable_inputs_as_the_defaults(api):
    assert api.run_calc("turnover_gpm", "not json")["calc"] == "turnover_gpm"
    assert api.run_calc("turnover_gpm", "[1, 2]")["calc"] == "turnover_gpm"


def test_a_model_sent_without_acks_keeps_the_stored_sign_offs(api):
    from erpnext_enhancements.water_engineering.engine import kits as kits_engine

    existing = kits_engine.apply_kit("lobby-sheet-wall-8ft")
    existing["acks"] = [{"issue_key": "SURGE|trough", "note": "ok by Logan", "by": "l@x", "on": "2026-11-01"}]
    sent = kits_engine.apply_kit("lobby-sheet-wall-8ft")
    sent.pop("acks", None)
    model, ignored = api.tool_model(existing, sent)
    assert model["acks"] == existing["acks"] and "acks" not in sent
    assert any(i.startswith("acks (") and "1 stored sign-off" in i for i in ignored)
    sent["acks"] = [{"issue_key": "OTHER", "note": "n"}]
    model, ignored = api.tool_model(existing, sent)
    assert model["acks"] == [{"issue_key": "OTHER", "note": "n"}] and ignored == [], "acks sent are the model's"
    sent["acks"] = []
    model, ignored = api.tool_model(existing, sent)
    assert model["acks"] == [] and ignored == [], "an empty list withdraws them"


def test_new_piping_without_pumps_keeps_the_pumps_pick_curve_and_control(api):
    from erpnext_enhancements.water_engineering.engine import kits as kits_engine

    existing = kits_engine.apply_kit("lobby-sheet-wall-8ft")
    existing["network"]["pumps"][0].update(item="PUMP-A", curve="PUMP-A", control="vfd", setpoint_psi=30)
    feature = {"feature_type": "Weir", "weir_length_ft": 6, "head_in": 0.25}
    segs = [{"line_type": "Discharge", "nominal_size": '2"', "pipe_length_ft": 30}]
    model, _ = api.merge_legacy_payload(existing, {"features": [feature], "pipe_segments": segs}, {})
    pump = model["network"]["pumps"][0]
    assert (pump["item"], pump["curve"], pump["control"], pump["setpoint_psi"]) == ("PUMP-A", "PUMP-A", "vfd", 30)
    assert model["setup"]["piping"] == "custom" and model["network"]["segments"] != existing["network"]["segments"]
    ids = {n.get("pump") for n in model["network"]["nodes"] if n.get("pump")} | {pump["id"]}
    assert pump["id"] in ids
    model, _ = api.merge_legacy_payload(existing, {"features": [feature], "pipe_segments": segs,
                                                   "pumps": [{"pump_item": "PUMP-B"}]}, {})
    assert model["network"]["pumps"][0]["item"] == "PUMP-B", "a pump sent still wins"


def test_the_canvas_carries_each_segments_velocity_band_and_the_worst(api, monkeypatch):
    from erpnext_enhancements.water_engineering.engine import kits as kits_engine

    monkeypatch.setattr(api, "pump_curves", lambda codes: {})
    model = kits_engine.apply_kit("lobby-sheet-wall-8ft")
    segs = model["network"]["segments"]
    assert len(segs) >= 2
    values = {segs[0]["id"]: {"velocity_fps": 3.0, "size": '2"'}, segs[1]["id"]: {"velocity_fps": 9.5, "size": '2"'}}
    doc, _ = _stored_design(api, model=model, results_json=json.dumps({"values": {"segments": values}}))
    canvas = api.canvas_state(doc)
    bands = [s["velocity_status"] for s in canvas["segments"][:2]]
    assert bands == ["Okay", "Exceeds Legal Limit"]
    assert canvas["worst_status"] == "Exceeds Legal Limit"
    assert api.segment_velocity_status({"line": "suction"}, {"velocity_fps": 5.0, "size": '2"'}) == "Increase Size"
    assert api.segment_velocity_status({}, {"velocity_fps": None}) == ""
    assert api.segment_velocity_status({"material": "HDPE"}, {"velocity_fps": 7.0, "size": '2"'}) == "", "no limits"
