"""Bench-free tests for the Fountain Designer catalog (v1.758.0, phase D).

Covers the Pump Curve and Nozzle Profile point checks, the approved-seed loader
(``water_engineering/catalog_seed.py``: approvals, mapping, provenance, idempotence, one bad row
not stopping the rest), the read layer (``catalog_reader``) and ``api/designer.pump_curves``
preferring Pump Curve records over the Item's legacy table, the legacy-curve migration patch,
and the doctype structure the build relies on (module, controller class name).

The loader runs against a small in-memory frappe (``FakeFrappe``) and the INVENTED rows in
``tests/fixtures/water_catalog/`` (manufacturer "Fixtureco", models prefixed ``FIXTURE-``).

Plain pytest functions with their own frappe stub, so this suite has its own ``python -m pytest``
step in ci.yml; ``python -m unittest`` would collect nothing from it.

Run: python -m pytest erpnext_enhancements/tests/test_water_catalog.py -q
"""

import ast
import copy
import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
WE = APP / "water_engineering"
FIXTURES = APP / "tests" / "fixtures" / "water_catalog"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

NEW_DOCTYPES = ["pump_curve", "pump_curve_point", "nozzle_profile", "nozzle_performance_point"]
#: Child doctype -> {parent doctype: table fieldname}, for the fake's flattened child reads.
CHILD_TABLES = {
    "Pump Curve Point": {"Pump Curve": "points", "Item": "custom_pump_curve"},
    "Nozzle Performance Point": {"Nozzle Profile": "performance"},
}
NAME_FIELD = {"Item": "item_code", "Nozzle Profile": "profile_name", "Brand": "brand", "Item Group": "item_group_name"}
#: The ERPNext Item fields the loader writes, as erpnext v16.52.1's item.json declares them.
ITEM_STANDARD_FIELDS = {
    "item_code": ("Data", "Item Code"),
    "item_name": ("Data", "Item Name"),
    "item_group": ("Link", "Item Group"),
    "stock_uom": ("Link", "Default Unit of Measure"),
    "is_stock_item": ("Check", "Maintain Stock"),
    "brand": ("Link", "Brand"),
    "description": ("Text Editor", "Description"),
}


def declared_fields():
    """``{doctype: {fieldname: {fieldtype, length, label}}}`` as this repo declares them.

    Item: ERPNext's own fields above plus every custom field ``water_engineering/setup.py`` creates
    (the literal keys of each ``{"fieldname": ...}`` dict). The four catalog doctypes: their JSON.
    The fake's ``get_meta`` serves these, so every loader test runs the field-length check against
    the schema a migrate would build.
    """
    item = {f: {"fieldtype": t, "length": 0, "label": label} for f, (t, label) in ITEM_STANDARD_FIELDS.items()}
    for node in ast.walk(ast.parse((WE / "setup.py").read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Dict):
            continue
        df = {k.value: v.value for k, v in zip(node.keys, node.values, strict=True)
              if isinstance(k, ast.Constant) and isinstance(v, ast.Constant)}
        if "fieldname" in df and "fieldtype" in df:
            item[df["fieldname"]] = {"fieldtype": df["fieldtype"], "length": df.get("length", 0),
                                     "label": df.get("label")}
    out = {"Item": item}
    for folder in NEW_DOCTYPES:
        data = json.loads((WE / "doctype" / folder / f"{folder}.json").read_text(encoding="utf-8"))
        out[data["name"]] = {f["fieldname"]: {"fieldtype": f["fieldtype"], "length": f.get("length", 0),
                                              "label": f.get("label")} for f in data["fields"]}
    return out


# ----------------------------------------------------------------------------- the fake frappe


class ValidationError(Exception):
    pass


class Row(dict):
    """A dict with attribute access (a missing attribute reads None), like a frappe row."""

    def __getattr__(self, key):
        if key.startswith("__"):
            raise AttributeError(key)
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value


class FakeDoc:
    def __init__(self, fake, data):
        object.__setattr__(self, "_fake", fake)
        object.__setattr__(self, "flags", types.SimpleNamespace())
        data = dict(data)
        self_data = {}
        for key, value in data.items():
            if isinstance(value, list):
                value = [Row(v) for v in value]
            self_data[key] = value
        object.__setattr__(self, "_data", self_data)

    def __getattr__(self, key):
        return self._data.get(key)

    def __setattr__(self, key, value):
        self._data[key] = value

    @property
    def doctype(self):
        return self._data["doctype"]

    @property
    def name(self):
        return self._data.get("name")

    def get(self, key, default=None):
        value = self._data.get(key)
        return default if value is None else value

    def set(self, key, value):
        self._data[key] = [Row(v) for v in value] if isinstance(value, list) else value

    def append(self, table, row):
        self._data.setdefault(table, []).append(Row(row))

    def insert(self, ignore_permissions=False):
        self._fake.insert(self)
        return self

    def save(self, ignore_permissions=False):
        self._fake.save(self)
        return self


class FakeDB:
    def __init__(self, fake):
        self.fake = fake
        self.commits = 0
        self.savepoints = {}

    def exists(self, doctype, name_or_filters=None):
        store = self.fake.store.get(doctype, {})
        if isinstance(name_or_filters, dict):
            return any(all(r.get(k) == v for k, v in name_or_filters.items()) for r in store.values())
        return name_or_filters in store

    def has_column(self, doctype, column):
        return True

    def savepoint(self, name):
        self.savepoints[name] = copy.deepcopy(self.fake.store)

    def rollback(self, save_point=None):
        if save_point in self.savepoints:
            self.fake.store = copy.deepcopy(self.savepoints[save_point])

    def commit(self):
        self.commits += 1

    def get_value(self, doctype, name, field):
        return self.fake.store.get(doctype, {}).get(name, {}).get(field)

    def set_value(self, doctype, name, field, value, update_modified=True):
        self.fake.store[doctype][name][field] = value

    def sql(self, query, values=None):
        self.fake.sql.append((" ".join(query.split()), values))

    def count(self, doctype, filters=None):
        return len(self.fake.get_all(doctype, filters=filters))

    def get_single_value(self, doctype, field):
        return self.fake.store.get(doctype, {}).get(doctype, {}).get(field)


class FakeFrappe:
    """Just enough of frappe for the loader, the readers and the patch, over dict tables."""

    def __init__(self):
        self.store = {}
        self.counter = 0
        self.db = FakeDB(self)
        self.inserts = []
        self.saves = []
        self.fail_insert = set()
        self.fields = declared_fields()
        self.sql = []
        self.renamed = []
        self.cache = types.SimpleNamespace(deleted=[], get_value=lambda key: None,
                                           set_value=lambda *a, **k: None)
        self.cache.delete_value = self.cache.deleted.append
        self.fail_rename = set()
        self.deleted = []
        # Link-to-Item fields as frappe.model.rename_doc.get_link_fields returns them: a few real
        # ones (erpnext 16.52.1), a Single among them.
        self.link_fields = [
            {"parent": "Pump Curve", "fieldname": "item", "issingle": 0},
            {"parent": "Nozzle Profile", "fieldname": "item", "issingle": 0},
            {"parent": "Quotation Item", "fieldname": "item_code", "issingle": 0},
            {"parent": "Item Price", "fieldname": "item_code", "issingle": 0},
            {"parent": "Fixture Settings", "fieldname": "default_pump", "issingle": 1},
        ]

    def get_link_fields(self, doctype):
        return [dict(f) for f in self.link_fields] if doctype == "Item" else []

    def delete_doc(self, doctype=None, name=None, force=0, ignore_doctypes=None, for_reload=False,
                   ignore_permissions=False, flags=None, ignore_on_trash=False, ignore_missing=True,
                   delete_permanently=False):
        """``frappe.delete_doc`` at v16.53.0: never forced here, so any Link refuses, and on a
        doctype with ``disabled`` the message names no linked record."""
        for link in self.get_link_fields(doctype):
            if link["issingle"]:
                hit = self.db.get_single_value(link["parent"], link["fieldname"]) == name
            else:
                hit = self.db.count(link["parent"], {link["fieldname"]: name})
            if hit and not force:
                raise ValidationError(f"You can disable this {doctype} instead of deleting it.")
        self.store[doctype].pop(name)
        self.deleted.append((doctype, name, ignore_permissions, force))

    def rename_doc(self, doctype=None, old=None, new=None, force=False, merge=False, ignore_permissions=False,
                   ignore_if_exists=False, show_alert=True, rebuild_search=True, doc=None, validate=True):
        """``frappe.model.rename_doc.rename_doc`` at frappe v16.53.0, its parameters exactly, so a
        keyword it does not take fails here as it would on the site. The record moves and every
        Link to it follows."""
        kwargs = {"ignore_permissions": ignore_permissions, "show_alert": show_alert, "rebuild_search": rebuild_search}
        if old in self.fail_rename:
            raise ValidationError(f"rename of {old} refused")
        if new in self.store[doctype]:
            raise ValidationError(f"Another {doctype} with name {new} exists, select another name")
        row = self.store[doctype].pop(old)
        self.store[doctype][new] = {**row, "name": new, "item_code": new}
        for linked, field in (("Pump Curve", "item"), ("Nozzle Profile", "item")):
            for rec in self.store.get(linked, {}).values():
                if rec.get(field) == old:
                    rec[field] = new
        self.renamed.append((old, new, kwargs))
        return new

    def get_meta(self, doctype):
        fields = self.fields.get(doctype, {})
        meta = types.SimpleNamespace()
        meta.get_field = lambda fieldname: Row(fields[fieldname]) if fieldname in fields else None
        return meta

    # --- writes
    def _stamp(self, data, creating):
        self.counter += 1
        data["modified"] = self.counter
        if creating:
            data["creation"] = self.counter

    def insert(self, doc):
        data = doc._data
        doctype = data["doctype"]
        if doctype in self.fail_insert and not getattr(doc.flags, "ignore_validate", False):
            raise ValidationError(f"{doctype} refused")
        if not data.get("name"):
            if doctype == "Pump Curve":
                data["name"] = f"PCRV-{len(self.store.get(doctype, {})) + 1:05d}"
            elif doctype in NAME_FIELD:
                data["name"] = data[NAME_FIELD[doctype]]
            else:
                data["name"] = f"{doctype[:4].upper()}-{len(self.store.get(doctype, {})) + 1:05d}"
        if data["name"] in self.store.get(doctype, {}):
            raise ValidationError(f"Duplicate {doctype} {data['name']}")
        self._stamp(data, True)
        self.store.setdefault(doctype, {})[data["name"]] = copy.deepcopy(data)
        self.inserts.append((doctype, data["name"]))

    def save(self, doc):
        data = doc._data
        self._stamp(data, False)
        self.store[data["doctype"]][data["name"]] = copy.deepcopy(data)
        self.saves.append((data["doctype"], data["name"]))

    def get_doc(self, doctype, name=None):
        if isinstance(doctype, dict):
            return FakeDoc(self, doctype)
        return FakeDoc(self, copy.deepcopy(self.store[doctype][name]))

    # --- reads
    def _rows(self, doctype):
        if doctype in CHILD_TABLES:
            out = []
            for parenttype, field in CHILD_TABLES[doctype].items():
                for parent in self.store.get(parenttype, {}).values():
                    for idx, row in enumerate(parent.get(field) or [], start=1):
                        out.append(
                            {**row, "parent": parent["name"], "parenttype": parenttype, "parentfield": field, "idx": idx}
                        )
            return out
        return [dict(r) for r in self.store.get(doctype, {}).values()]

    def get_all(self, doctype, filters=None, fields=None, pluck=None, order_by=None, **_kw):
        rows = self._rows(doctype)
        for key, want in (filters or {}).items():
            if isinstance(want, (list, tuple)) and want and want[0] == "in":
                rows = [r for r in rows if r.get(key) in want[1]]
            else:
                rows = [r for r in rows if r.get(key) == want]
        if order_by:
            for part in reversed([p.strip() for p in order_by.split(",")]):
                key, _sep, direction = part.partition(" ")
                rows.sort(key=lambda r: (r.get(key) is None, r.get(key) or 0), reverse=direction == "desc")
        if pluck:
            return [r.get(pluck) for r in rows]
        if fields:
            rows = [{f: r.get(f) for f in fields} for r in rows]
        return [Row(r) for r in rows]


def _install(fake):
    frappe = types.ModuleType("frappe")
    frappe._ = lambda msg, *a, **k: msg
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.db = fake.db
    frappe.get_doc = fake.get_doc
    frappe.get_all = fake.get_all
    frappe.get_meta = fake.get_meta
    frappe.delete_doc = fake.delete_doc
    frappe.cache = lambda: fake.cache
    frappe.flags = types.SimpleNamespace()
    frappe.messages = []
    frappe.msgprint = lambda msg, *a, **k: frappe.messages.append(msg)
    frappe.roles_checked = []
    frappe.only_for = lambda role: frappe.roles_checked.append(role)
    frappe.has_permission = lambda *a, **k: True
    frappe.log_error = lambda *a, **k: None
    frappe.get_traceback = lambda *a, **k: ""
    frappe.PermissionError = PermissionError
    frappe.ValidationError = ValidationError

    def throw(msg, *a, **k):
        raise ValidationError(msg)

    frappe.throw = throw
    utils = types.ModuleType("frappe.utils")
    utils.cint = lambda v=0: int(float(v or 0))
    utils.flt = lambda v=0, precision=None: float(v or 0)
    utils.nowdate = lambda: "2026-11-01"
    frappe.utils = utils
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")

    class Document:
        pass

    document.Document = Document
    model.document = document
    rename = types.ModuleType("frappe.model.rename_doc")
    rename.rename_doc = fake.rename_doc
    rename.get_link_fields = fake.get_link_fields
    model.rename_doc = rename
    frappe.model = model
    sys.modules.update(
        {"frappe": frappe, "frappe.utils": utils, "frappe.model": model, "frappe.model.document": document,
         "frappe.model.rename_doc": rename}
    )
    # Re-import the modules under test against this fake each time. The packages go too: a
    # cached package keeps its submodule as an attribute, and `from pkg import mod` would hand
    # back the copy bound to the previous fake.
    for name in list(sys.modules):
        if name.startswith(("erpnext_enhancements.water_engineering", "erpnext_enhancements.patches")):
            if ".engine" not in name:
                del sys.modules[name]
    return frappe


@pytest.fixture
def fake():
    f = FakeFrappe()
    _install(f)
    f.store["User"] = {"fixture.approver@example.invalid": {"name": "fixture.approver@example.invalid"}}
    f.store["Item Group"] = {
        "All Item Groups": {"name": "All Item Groups"},
        "Pumps": {"name": "Pumps"},
    }
    f.store["Item"] = {
        "500999": {
            "doctype": "Item",
            "name": "500999",
            "item_code": "500999",
            "item_name": "Pump, 1800 GPH, Submersible",
            "item_group": "Pumps",
            "description": "Curated by a person.",
            "custom_rated_gpm": 30,
        }
    }
    return f


def _seed(dry_run=False):
    from erpnext_enhancements.water_engineering import catalog_seed

    return catalog_seed.load_catalog(catalog_dir=FIXTURES, dry_run=dry_run)


# ----------------------------------------------------------------------------- doctype structure


def _doctype_json(folder):
    return json.loads((WE / "doctype" / folder / f"{folder}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("folder", NEW_DOCTYPES)
def test_doctype_sits_in_water_engineering_with_frappes_class_name(folder):
    meta = _doctype_json(folder)
    assert meta["module"] == "Water Engineering"
    assert meta["name"].lower().replace(" ", "_") == folder
    expected = meta["name"].replace(" ", "").replace("-", "")
    tree = ast.parse((WE / "doctype" / folder / f"{folder}.py").read_text(encoding="utf-8"))
    assert expected in {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
    assert (WE / "doctype" / folder / "__init__.py").exists()


def test_pump_curve_tables_and_fields():
    curve = {f["fieldname"]: f for f in _doctype_json("pump_curve")["fields"]}
    assert curve["item"]["options"] == "Item" and curve["item"]["reqd"] == 1
    assert curve["points"]["options"] == "Pump Curve Point"
    assert curve["source"]["options"].split("\n") == ["Datasheet", "Digitized", "Rep-supplied", "Generic estimate"]
    assert curve["speed_pct"]["default"] == "100"
    point = [f["fieldname"] for f in _doctype_json("pump_curve_point")["fields"]]
    assert point == ["flow_gpm", "head_ft", "efficiency_pct", "npshr_ft", "power_bhp"]
    nozzle = {f["fieldname"]: f for f in _doctype_json("nozzle_profile")["fields"]}
    assert nozzle["performance"]["options"] == "Nozzle Performance Point"
    for field in ("item", "connection_size", "level_sensitive", "min_depth_in", "max_depth_in",
                  "recommended_beam_deg", "source_url", "source_document", "verified_by", "verified_on"):
        assert field in nozzle, field


def test_retired_nozzle_types_map_onto_live_options():
    """The remap patch and the generic seeds may only write values the Select accepts."""
    options = _doctype_json("nozzle_profile")["fields"]
    options = next(f for f in options if f["fieldname"] == "nozzle_type")["options"].split("\n")
    patch = ast.parse((APP / "patches" / "remap_nozzle_profile_types.py").read_text(encoding="utf-8"))
    mapping = next(
        ast.literal_eval(n.value) for n in patch.body if isinstance(n, ast.Assign) and n.targets[0].id == "OLD_TO_NEW"
    )
    assert set(mapping.values()) <= set(options)
    for old in ("Smooth Bore", "Aerating", "Spray", "Cascade"):  # the five on prod, minus Geyser (kept)
        assert old in mapping and old not in options
    assert "Geyser" in options
    setup = ast.parse((WE / "setup.py").read_text(encoding="utf-8"))
    seed = next(
        ast.literal_eval(n.value)
        for n in setup.body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "NOZZLE_PROFILE_SEED"
    )
    assert {s["nozzle_type"] for s in seed} <= set(options)


def test_equipment_classes_are_appended_never_reordered():
    setup = ast.parse((WE / "setup.py").read_text(encoding="utf-8"))
    value = next(
        n.value for n in setup.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "EQUIPMENT_CLASS_OPTIONS"
    )
    options = ast.literal_eval(value).split("\n")
    before = ["", "Filter", "Heater", "Chemical Feed Pump", "Chemical Controller", "Skimmer",
              "Suction Outlet (VGB)", "Therapy Jet", "Return Inlet", "Gauge / Meter",
              "Panel Electrical Device", "Control Hardware"]
    assert options[: len(before)] == before
    assert options[len(before):] == ["Pump", "Nozzle", "Light", "Aerator", "Valve", "Controller", "Fitting kit"]


def test_the_seed_loader_never_runs_on_migrate():
    hooks = (APP / "hooks.py").read_text(encoding="utf-8")
    assert "catalog_seed" not in hooks
    assert "patches.migrate_item_pump_curves" in (APP / "patches.txt").read_text(encoding="utf-8")
    assert "patches.remap_nozzle_profile_types" in (APP / "patches.txt").read_text(encoding="utf-8")


def test_fixtures_are_labeled_invented():
    for path in FIXTURES.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "INVENTED TEST DATA" in text, path.name
        assert "example.invalid" in text, path.name


# ----------------------------------------------------------------------------- point checks


def test_pump_curve_points_sorted_and_accepted(fake):
    from erpnext_enhancements.water_engineering.doctype.pump_curve.pump_curve import check_points

    ordered, errors, warnings = check_points(
        [{"flow_gpm": 40, "head_ft": 46}, {"flow_gpm": 0, "head_ft": 60}, {"flow_gpm": 20, "head_ft": 55},
         {"flow_gpm": 60, "head_ft": 32}]
    )
    assert errors == [] and warnings == []
    assert [p["flow_gpm"] for p in ordered] == [0, 20, 40, 60]


@pytest.mark.parametrize(
    "points, needle",
    [
        ([{"flow_gpm": 0, "head_ft": 20}, {"flow_gpm": 10, "head_ft": 10}, {"flow_gpm": 20, "head_ft": 15}], "after the curve has peaked"),
        ([{"flow_gpm": 0, "head_ft": 20}, {"flow_gpm": 0, "head_ft": 18}], "share the flow"),
        ([{"flow_gpm": 0, "head_ft": 20}], "at least two"),
        ([{"flow_gpm": 0, "head_ft": 20}, {"flow_gpm": 10, "head_ft": None}], "both a flow and a head"),
        ([{"flow_gpm": 0, "head_ft": 20}, {"flow_gpm": 10, "head_ft": -1}], "negative"),
        ([{"flow_gpm": 0, "head_ft": 20, "efficiency_pct": 120}, {"flow_gpm": 10, "head_ft": 10}], "between 0 and 100"),
    ],
)
def test_pump_curve_points_refused(fake, points, needle):
    from erpnext_enhancements.water_engineering.doctype.pump_curve.pump_curve import check_points

    _ordered, errors, _warnings = check_points(points)
    assert any(needle in e for e in errors), errors


def test_a_drooping_curve_is_accepted_with_a_warning(fake):
    """Pentair EQ500 as published: 77.0 ft at shutoff, 77.5 ft at 50 GPM, then falling."""
    from erpnext_enhancements.water_engineering.doctype.pump_curve.pump_curve import check_points

    points = [(0, 77.0), (50, 77.5), (100, 77.3), (150, 74.9), (200, 69.5), (250, 60.0)]
    _ordered, errors, warnings = check_points([{"flow_gpm": f, "head_ft": h} for f, h in points])
    assert errors == []
    assert any("Drooping curve" in w and "77.5 ft at 50 GPM" in w for w in warnings)


def test_pump_curve_gaps_only_warn(fake):
    from erpnext_enhancements.water_engineering.doctype.pump_curve.pump_curve import check_points

    _ordered, errors, warnings = check_points(
        [{"flow_gpm": 10, "head_ft": 50}, {"flow_gpm": 15, "head_ft": 48}, {"flow_gpm": 80, "head_ft": 10}]
    )
    assert errors == []
    assert any("not at shutoff" in w for w in warnings)
    assert any("Gap between 15 and 80" in w for w in warnings)


def test_nozzle_points_fill_head_and_psi_and_sort(fake):
    from erpnext_enhancements.water_engineering.doctype.nozzle_profile.nozzle_profile import (
        check_performance_points,
    )
    from erpnext_enhancements.water_engineering.engine.constants import FT_PER_PSI

    rows = [{"psi": 10, "flow_gpm": 14}, {"head_ft": 2 * FT_PER_PSI, "flow_gpm": 6}]
    ordered, errors, warnings = check_performance_points(rows)
    assert errors == [] and warnings == []
    assert ordered[0]["psi"] == pytest.approx(2) and ordered[1]["head_ft"] == pytest.approx(10 * FT_PER_PSI, abs=1e-3)

    _o, _e, warnings = check_performance_points([{"psi": 5, "flow_gpm": 10}, {"psi": 10, "flow_gpm": 8}])
    assert any("Flow falls" in w for w in warnings)
    _o, errors, _w = check_performance_points([{"psi": 5}])
    assert errors


# ----------------------------------------------------------------------------- the seed loader


def test_dry_run_writes_nothing_and_says_what_it_would_do(fake):
    before = copy.deepcopy(fake.store)
    summary = _seed(dry_run=True)
    assert fake.store == before and fake.inserts == [] and fake.saves == []
    assert summary["dry_run"] is True
    created = {e["key"] for e in summary["pumps"]["created"]}
    assert created == {"fixtureco|fixture-p100", "fixtureco|fixture-p200vs"}
    assert any("would be created" in c for e in summary["pumps"]["created"] for c in e["changes"])


def test_loads_only_approved_rows_and_reports_the_rest(fake):
    summary = _seed()
    pumps = summary["pumps"]
    assert pumps["unapproved"] == ["fixtureco|fixture-unapproved"]
    assert pumps["approved_not_found"] == ["fixtureco|fixture-ghost"]
    assert [e["key"] for e in pumps["errors"]] == ["fixtureco|fixture-bad"]
    assert "Head rises" in pumps["errors"][0]["error"]
    assert [e["key"] for e in summary["lights"]["errors"]] == ["fixtureco|fixture-l0"]
    assert "FIXTURE-UNAPPROVED" not in json.dumps(fake.store)
    assert "FIXTURE-BAD" not in json.dumps(fake.store)
    assert fake.db.commits == 1


def test_pump_item_and_curve_mapping_with_provenance(fake):
    _seed()
    item = fake.store["Item"]["FX-100-115"]
    assert item["item_group"] == "Pumps" and item["is_stock_item"] == 0 and item["stock_uom"] == "Nos"
    assert item["item_name"] == "PUMP, FIXTURECO FIXTURE-P100"
    assert item["brand"] == "Fixtureco" and "Fixtureco" in fake.store["Brand"]
    assert item["custom_equipment_class"] == "Pump" and item["custom_model_no"] == "FIXTURE-P100"
    assert item["custom_rated_gpm"] == 75 and item["custom_rated_tdh_ft"] == 60
    assert item["custom_pump_hp"] == 1 and item["custom_pump_phase"] == "1"
    assert item["custom_pump_voltage"] == "115/230" and item["custom_pump_fla_amps"] == 9.8
    assert "4.9 A @ 230 V" in item["description"] and "FX-100-115" in item["description"]
    assert item["custom_ul_listing"] == "UL 778"
    assert item["custom_spec_source_url"] == "https://example.invalid/fixture-p100.pdf"
    assert item["custom_spec_revision"] == "FIXTURE brochure rev A (accessed 2026-11-01), p. 4"
    assert item["custom_spec_verified_by"] == "fixture.approver@example.invalid"
    assert item["custom_spec_verified_on"] == "2026-11-01"
    assert item["custom_generic_estimate"] == 0

    curves = [c for c in fake.store["Pump Curve"].values() if c["item"] == item["name"]]
    assert len(curves) == 1
    curve = curves[0]
    assert curve["curve_label"] == "3450 rpm" and curve["rpm"] == 3450 and curve["source"] == "Datasheet"
    assert curve["max_flow_gpm"] == 75 and curve["shutoff_head_ft"] == 60
    assert curve["source_page"] == "4" and curve["verified_by"] == "fixture.approver@example.invalid"
    assert [p["flow_gpm"] for p in curve["points"]] == [0, 20, 40, 60, 75]
    assert curve["points"][2]["efficiency_pct"] == 52 and curve["points"][2]["npshr_ft"] == 6


def test_variable_speed_pump_gets_one_curve_per_speed(fake):
    _seed()
    curves = {c["curve_label"]: c for c in fake.store["Pump Curve"].values() if c["item"] == "FIXTURE-P200VS"}
    assert set(curves) == {"Full speed", "50 % speed"}
    assert curves["Full speed"].get("rpm") is None and curves["Full speed"]["source"] == "Digitized"
    assert curves["50 % speed"]["speed_pct"] == 50
    assert curves["Full speed"]["points"][1]["power_bhp"] == pytest.approx(1.2 / 0.745699872, abs=1e-3)
    item = fake.store["Item"]["FIXTURE-P200VS"]
    assert item["custom_pump_voltage"] == "230" and item["custom_pump_fla_amps"] == 8.1


def test_an_approved_existing_item_keeps_its_curation(fake):
    _seed()
    item = fake.store["Item"]["500999"]
    assert item["item_name"] == "Pump, 1800 GPH, Submersible"
    assert item["description"] == "Curated by a person."
    assert item["custom_model_no"] == "FIXTURE-EXISTING" and item["custom_rated_gpm"] == 30
    assert item["brand"] == "Fixtureco"  # blank before, so filled
    assert any(c["item"] == "500999" for c in fake.store["Pump Curve"].values())
    assert "FIXTURE-EXISTING" not in fake.store["Item"]


def test_nozzle_and_light_mapping(fake):
    _seed()
    nozzle_item = fake.store["Item"]["FIXTURE-N10"]
    assert nozzle_item["item_group"] == "Nozzles" and nozzle_item["custom_equipment_class"] == "Nozzle"
    assert fake.store["Item Group"]["Nozzles"]["parent_item_group"] == "Aquatic Equipment"
    assert fake.store["Item"]["FIXTURE-N20"]["item_name"] == "NOZZLE, LAMINAR, FIXTURE N20"
    profile = fake.store["Nozzle Profile"]["Fixtureco FIXTURE-N10"]
    assert profile["item"] == "FIXTURE-N10"
    assert profile["nozzle_type"] == "Foam / Aerating" and profile["level_sensitive"] == 1
    assert profile["connection_size"] == "1 in NPT" and profile["is_generic_estimate"] == 0
    assert profile["source_document"] == "FIXTURE nozzle catalog 2026"
    assert [p["psi"] for p in profile["performance"]] == [5, 10, 15]
    assert profile["performance"][1]["display_height_ft"] == 4.5 and profile["performance"][1]["display_diameter_ft"] == 1.2
    assert profile["rated_gpm"] == 14  # the middle row
    assert fake.store["Nozzle Profile"]["Fixtureco FIXTURE-N20"]["nozzle_type"] == "Laminar"
    assert fake.store["Nozzle Profile"]["Fixtureco FIXTURE-N20"]["level_sensitive"] == 0

    light = fake.store["Item"]["FIXTURE-L12"]
    assert light["item_group"] == "Lights" and light["custom_equipment_class"] == "Light"
    assert light["custom_elec_watts"] == 12 and light["custom_elec_voltage"] == "12"
    assert light["custom_lumens"] == 900 and light["custom_beam_deg"] == 30
    assert light["custom_light_control"] == "DMX" and light["custom_max_depth_ft"] == 10
    assert light["custom_ip_rating"] == "IP68" and light["custom_ul_listing"] == "UL 676"


def test_second_run_changes_nothing(fake):
    _seed()
    inserts, saves = len(fake.inserts), len(fake.saves)
    summary = _seed()
    assert len(fake.inserts) == inserts and len(fake.saves) == saves
    for kind in ("pumps", "nozzles", "lights"):
        assert summary[kind]["created"] == [] and summary[kind]["updated"] == [], kind
    assert sorted(summary["pumps"]["unchanged"]) == [
        "fixtureco|fixture-existing",
        "fixtureco|fixture-p100",
        "fixtureco|fixture-p200vs",
    ]


def test_changed_data_updates_in_place(fake, tmp_path):
    for path in FIXTURES.glob("*.json"):
        (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    from erpnext_enhancements.water_engineering import catalog_seed

    catalog_seed.load_catalog(catalog_dir=tmp_path, dry_run=False)
    data = json.loads((tmp_path / "pumps.json").read_text(encoding="utf-8"))
    data["pumps"][0]["curve"][4]["head_ft"] = 17
    data["pumps"][0]["hp"] = 1.5
    (tmp_path / "pumps.json").write_text(json.dumps(data), encoding="utf-8")
    curves_before = len(fake.store["Pump Curve"])
    summary = catalog_seed.load_catalog(catalog_dir=tmp_path, dry_run=False)
    updated = {e["key"]: e for e in summary["pumps"]["updated"]}
    assert set(updated) == {"fixtureco|fixture-p100"}
    assert any("custom_pump_hp" in c for c in updated["fixtureco|fixture-p100"]["changes"])
    assert any(c.endswith("points") for c in updated["fixtureco|fixture-p100"]["changes"])
    assert len(fake.store["Pump Curve"]) == curves_before
    assert fake.store["Item"]["FX-100-115"]["custom_pump_hp"] == 1.5


def test_a_failed_write_rolls_back_only_its_row(fake):
    fake.fail_insert.add("Nozzle Profile")
    summary = _seed()
    errors = {e["key"] for e in summary["nozzles"]["errors"]}
    assert errors == {"fixtureco|fixture-n10", "fixtureco|fixture-n20"}
    assert "FIXTURE-N10" not in fake.store["Item"]  # its Item went with it
    assert "FX-100-115" in fake.store["Item"]


def test_unknown_approver_leaves_verified_by_blank(fake, tmp_path):
    for path in FIXTURES.glob("*.json"):
        (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    approved = json.loads((tmp_path / "approved.json").read_text(encoding="utf-8"))
    approved["approved_by"] = "nobody@example.invalid"
    (tmp_path / "approved.json").write_text(json.dumps(approved), encoding="utf-8")
    from erpnext_enhancements.water_engineering import catalog_seed

    summary = catalog_seed.load_catalog(catalog_dir=tmp_path, dry_run=False)
    assert "not a User" in summary["note"]
    assert not fake.store["Item"]["FX-100-115"].get("custom_spec_verified_by")


def test_the_endpoint_is_system_manager_only_and_dry_by_default(fake, monkeypatch):
    from erpnext_enhancements.water_engineering import catalog_seed

    monkeypatch.setattr(catalog_seed, "CATALOG_DIR", FIXTURES)
    summary = catalog_seed.seed_catalog()
    import frappe

    assert frappe.roles_checked == ["System Manager"]
    assert summary["dry_run"] is True and fake.inserts == []


def test_catalog_keys_ignore_case_and_spacing(fake):
    from erpnext_enhancements.water_engineering.catalog_seed import catalog_key, nozzle_type_for

    assert catalog_key(" Fixtureco ", "FIXTURE  P1") == catalog_key("fixtureco", "fixture p1")
    assert nozzle_type_for("Aerating foam jet") == "Foam / Aerating"
    assert nozzle_type_for("Water curtain") == "Water Screen"
    assert nozzle_type_for("Smooth bore jet") == "Orifice Jet"
    assert nozzle_type_for("something new") == "Other"


# ----------------------------------------------------------------------------- the readers


def _curve(fake, item, label, points, **extra):
    fake.insert(
        FakeDoc(
            fake,
            {"doctype": "Pump Curve", "item": item, "curve_label": label, "source": "Datasheet", "speed_pct": 100,
             "points": [{"flow_gpm": f, "head_ft": h} for f, h in points], **extra},
        )
    )


def test_pump_curves_prefers_pump_curve_records_over_the_legacy_table(fake):
    fake.store["Item"]["500999"]["custom_pump_curve"] = [{"flow_gpm": 0, "head_ft": 99}, {"flow_gpm": 10, "head_ft": 90}]
    fake.store["Item"]["LEGACY-ONLY"] = {
        "doctype": "Item", "name": "LEGACY-ONLY",
        "custom_pump_curve": [{"flow_gpm": 5, "head_ft": 40}, {"flow_gpm": 0, "head_ft": 44}],
    }
    _curve(fake, "500999", "Real", [(0, 30), (15, 24), (30, 10)])
    from erpnext_enhancements.water_engineering.api import designer as water_design  # moved in v1.771.0

    curves = water_design.pump_curves(["500999", "LEGACY-ONLY", "NO-CURVE", None])
    assert curves["500999"] == [
        {"flow_gpm": 0, "head_ft": 30},
        {"flow_gpm": 15, "head_ft": 24},
        {"flow_gpm": 30, "head_ft": 10},
    ]
    assert curves["LEGACY-ONLY"] == [{"flow_gpm": 0, "head_ft": 44}, {"flow_gpm": 5, "head_ft": 40}]
    assert "NO-CURVE" not in curves


def test_curve_sets_rank_full_speed_sourced_curves_first(fake):
    _curve(fake, "500999", "Half", [(0, 8), (10, 4)], speed_pct=50)
    _curve(fake, "500999", "Generic", [(0, 40), (20, 10)], source="Generic estimate")
    _curve(fake, "500999", "Small impeller", [(0, 25), (20, 5)], impeller_dia_in=5)
    _curve(fake, "500999", "Big impeller", [(0, 35), (20, 15)], impeller_dia_in=6.5)
    from erpnext_enhancements.water_engineering import catalog_reader

    sets = catalog_reader.pump_curve_sets(["500999"])["500999"]
    assert [c["curve_label"] for c in sets] == ["Big impeller", "Small impeller", "Generic", "Half"]
    assert sets[2]["is_generic_estimate"] is True
    assert set(sets[0]["points"][0]) == {"flow_gpm", "head_ft", "efficiency_pct", "npshr_ft", "power_bhp"}


def test_nozzle_profile_params_return_the_points(fake):
    _seed()
    from erpnext_enhancements.water_engineering.api import designer as water_design  # moved in v1.771.0

    params = water_design.nozzle_profile_params("Fixtureco FIXTURE-N10")
    assert params["rated_gpm"] == 14 and params["nozzle_type"] == "Foam / Aerating"
    assert params["level_sensitive"] is True and params["is_generic_estimate"] is False
    assert [p["psi"] for p in params["points"]] == [5, 10, 15]
    assert water_design.nozzle_profile_params("missing") == {}
    assert water_design.nozzle_profile_params("") == {}


def test_light_specs(fake):
    _seed()
    from erpnext_enhancements.water_engineering import catalog_reader

    spec = catalog_reader.light_specs(["FIXTURE-L12"])["FIXTURE-L12"]
    assert spec["watts"] == 12 and spec["voltage"] == "12" and spec["lumens"] == 900
    assert spec["control"] == "DMX" and spec["listing"] == "UL 676" and spec["is_generic_estimate"] is False
    assert spec["verified_on"] == "2026-11-01" and spec["manufacturer"] == "Fixtureco"


# ----------------------------------------------------------------------------- the migration patch


def test_legacy_curves_migrate_once_and_keep_the_item_rows(fake):
    fake.store["Item"]["500999"]["custom_pump_curve"] = [{"flow_gpm": 0, "head_ft": 30}, {"flow_gpm": 20, "head_ft": 12}]
    fake.store["DocType"] = {"Pump Curve": {"name": "Pump Curve"}}
    from erpnext_enhancements.patches import migrate_item_pump_curves as patch

    patch.execute()
    curves = list(fake.store["Pump Curve"].values())
    assert len(curves) == 1 and curves[0]["item"] == "500999" and curves[0]["source"] == "Digitized"
    assert [(p["flow_gpm"], p["head_ft"]) for p in curves[0]["points"]] == [(0, 30), (20, 12)]
    assert fake.store["Item"]["500999"]["custom_pump_curve"]  # untouched
    patch.execute()
    assert len(fake.store["Pump Curve"]) == 1


def test_a_curve_the_new_rules_refuse_is_still_migrated_and_flagged(fake):
    fake.store["Item"]["500999"]["custom_pump_curve"] = [{"flow_gpm": 0, "head_ft": 10}, {"flow_gpm": 20, "head_ft": 12}]
    fake.store["DocType"] = {"Pump Curve": {"name": "Pump Curve"}}
    fake.fail_insert.add("Pump Curve")
    from erpnext_enhancements.patches import migrate_item_pump_curves as patch

    patch.execute()
    curve = next(iter(fake.store["Pump Curve"].values()))
    assert "failed the curve checks" in curve["notes"]


def test_blank_optional_point_values_match_a_stored_zero(fake):
    """Child Float columns are NOT NULL DEFAULT 0: a blank NPSHr reads back 0 and is no change."""
    _seed()
    for curve in fake.store["Pump Curve"].values():
        for point in curve["points"]:
            for key in ("efficiency_pct", "npshr_ft", "power_bhp"):
                if point.get(key) is None:
                    point[key] = 0.0
    saves = len(fake.saves)
    summary = _seed()
    assert summary["pumps"]["updated"] == [] and len(fake.saves) == saves


def test_item_lookup_ignores_spacing_like_the_approvals(fake, tmp_path):
    for path in FIXTURES.glob("*.json"):
        (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    from erpnext_enhancements.water_engineering import catalog_seed

    catalog_seed.load_catalog(catalog_dir=tmp_path, dry_run=False)
    data = json.loads((tmp_path / "lights.json").read_text(encoding="utf-8"))
    data["lights"][0]["model"] = " fixture-l12 "
    (tmp_path / "lights.json").write_text(json.dumps(data), encoding="utf-8")
    catalog_seed.load_catalog(catalog_dir=tmp_path, dry_run=False)
    lights = [n for n, r in fake.store["Item"].items() if r.get("custom_equipment_class") == "Light"]
    assert lights == ["FIXTURE-L12"]


def test_a_pump_outside_the_pumps_group_is_flagged(fake):
    fake.store["Item"]["500999"]["item_group"] = "Finished Fountains"
    summary = _seed()
    assert any("500999" in w and "Pumps" in w for w in summary["pumps"]["warnings"])
    assert not any("P100" in w and "item group" in w for w in summary["pumps"]["warnings"])


def test_item_ratings_come_from_the_best_curve(fake, tmp_path):
    for path in FIXTURES.glob("*.json"):
        (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    data = json.loads((tmp_path / "pumps.json").read_text(encoding="utf-8"))
    data["pumps"][1]["curves"].reverse()  # the 50 % curve first
    (tmp_path / "pumps.json").write_text(json.dumps(data), encoding="utf-8")
    from erpnext_enhancements.water_engineering import catalog_seed

    catalog_seed.load_catalog(catalog_dir=tmp_path, dry_run=False)
    item = fake.store["Item"]["FIXTURE-P200VS"]
    assert item["custom_rated_gpm"] == 100 and item["custom_rated_tdh_ft"] == 80


@pytest.mark.parametrize("value, dry", [("false", False), ("0", False), ("no", False), ("1", True), ("true", True), (0, False), (True, True)])
def test_dry_run_parsing(fake, value, dry):
    from erpnext_enhancements.water_engineering import catalog_seed

    assert catalog_seed.load_catalog(catalog_dir=FIXTURES, dry_run=value)["dry_run"] is dry


# ----------------------------------------------------------------------------- the research data's shapes
# docs/fountain-designer/catalog/README.md: source is a list of {url, title, page, accessed},
# phase and listing are lists, impeller trims are printed text, VS speeds and trims come as
# extra_curves, the same model can be several part numbers, and floating aerators are filed as
# nozzles with no pressure. Rows below are INVENTED in that shape.


def _write(tmp_path, pumps=(), nozzles=(), lights=(), approved=None):
    (tmp_path / "pumps.json").write_text(json.dumps(list(pumps)), encoding="utf-8")
    (tmp_path / "nozzles.json").write_text(json.dumps(list(nozzles)), encoding="utf-8")
    (tmp_path / "lights.json").write_text(json.dumps(list(lights)), encoding="utf-8")
    base = {"approved_by": "fixture.approver@example.invalid", "approved_on": "2026-11-01"}
    (tmp_path / "approved.json").write_text(json.dumps({**base, **(approved or {})}), encoding="utf-8")


def _twin(part, phase, **extra):
    return {
        "id": f"fixtureco-twin-{part.lower()}",
        "manufacturer": "Fixtureco",
        "series": "Twin",
        "model": "FIXTURE-TWIN",
        "part_no": [f"{part} ({phase}-ph)"],
        "hp": 1,
        "voltage": [230],
        "phase": [phase],
        "fla_amps": {f"230 {phase}-ph": "4.0-4.4", "208 3-ph": 5.1},
        "rpm": 3450,
        "curve": [{"flow_gpm": 0, "head_ft": 50}, {"flow_gpm": 40, "head_ft": 40}, {"flow_gpm": 80, "head_ft": 20}],
        "listing": ["NSF/ANSI 50", "UL 1081"],
        "source": [{"url": "https://example.invalid/twin.pdf", "title": "FIXTURE twin brochure", "page": 3,
                    "accessed": "2026-11-01", "what": "curve"}],
        "confidence": "digitized",
        "method": "vector",
        "error_band_pct": 2,
        "notes": "Invented row.",
        **extra,
    }


def test_shared_model_names_split_by_part_number_and_approve_by_id(fake, tmp_path):
    _write(
        tmp_path,
        pumps=[_twin("FX-T1", 1), _twin("FX-T3", 3)],
        approved={"pumps": ["fixtureco-twin-fx-t1", "Fixtureco|FIXTURE-TWIN"]},
    )
    from erpnext_enhancements.water_engineering import catalog_seed

    summary = catalog_seed.load_catalog(catalog_dir=tmp_path, dry_run=False)
    assert [e["key"] for e in summary["pumps"]["created"]] == ["fixtureco-twin-fx-t1"]
    assert "approve this row by its id" in summary["pumps"]["errors"][0]["error"]
    item = fake.store["Item"]["FX-T1"]
    assert item["custom_model_no"] == "FIXTURE-TWIN FX-T1" and item["custom_pump_phase"] == "1"
    assert item["custom_pump_fla_amps"] == 5.1  # the first value that is a number; "4.0-4.4" is a range
    assert item["custom_ul_listing"] == "NSF/ANSI 50; UL 1081"
    assert item["custom_spec_revision"] == "FIXTURE twin brochure (accessed 2026-11-01), p. 3"
    assert "4.0-4.4 A @ 230 1-ph" in item["description"]
    assert item["custom_catalog_id"] == "fixtureco-twin-fx-t1"
    assert item["custom_spec_confidence"] == "digitized" and item["custom_spec_method"] == "vector"
    assert item["custom_spec_error_band_pct"] == 2
    curve = next(iter(fake.store["Pump Curve"].values()))
    assert curve["catalog_id"] == "fixtureco-twin-fx-t1" and curve["read_method"] == "vector"
    assert curve["error_band_pct"] == 2 and curve["source_page"] == "3"
    assert "Invented row." in curve["notes"]


def test_the_catalog_id_finds_the_item_after_its_model_is_renamed(fake, tmp_path):
    """Keyed on the row id: a corrected model name updates the same Item, never a second one."""
    row = _twin("FX-ID", 1, model="FIXTURE-SOLO")
    row["source"].append({"url": "https://example.invalid/second.pdf", "title": "FIXTURE errata"})
    _write(tmp_path, pumps=[row], approved={"pumps": [row["id"]]})
    from erpnext_enhancements.water_engineering import catalog_seed

    catalog_seed.load_catalog(catalog_dir=tmp_path, dry_run=False)
    row["model"] = "FIXTURE-SOLO II"
    _write(tmp_path, pumps=[row], approved={"pumps": [row["id"]]})
    summary = catalog_seed.load_catalog(catalog_dir=tmp_path, dry_run=False)
    pumps = [n for n, r in fake.store["Item"].items() if r.get("custom_catalog_id") == row["id"]]
    assert pumps == ["FX-ID"]
    assert fake.store["Item"]["FX-ID"]["custom_model_no"] == "FIXTURE-SOLO II"
    assert [e["key"] for e in summary["pumps"]["updated"]] == [row["id"]]
    curve = next(c for c in fake.store["Pump Curve"].values() if c["item"] == "FX-ID")
    assert "Also cited: FIXTURE errata https://example.invalid/second.pdf." in curve["notes"]


def test_vs_speeds_and_printed_impeller_trims_become_separate_curves(fake, tmp_path):
    vs = _twin("FX-VS", 1, variable_speed=True, rpm=None, curve_speed_rpm=3450, extra_curves=[
        {"speed_rpm": 1725, "curve": [{"flow_gpm": 0, "head_ft": 12.5}, {"flow_gpm": 40, "head_ft": 5}]},
    ])
    trims = _twin("FX-TR", 3, impeller_dia_in='4"', extra_curves=[
        {"speed_rpm": 3450, "impeller_dia_in": '3-1/2"', "curve": [{"flow_gpm": 0, "head_ft": 38}, {"flow_gpm": 60, "head_ft": 15}]},
        {"speed_rpm": 3450, "impeller_dia_in": '3-1/2"', "curve": [{"flow_gpm": 0, "head_ft": 37}, {"flow_gpm": 60, "head_ft": 14}]},
    ])
    trims["model"] = "FIXTURE-TRIM"
    vs["model"] = "FIXTURE-VS"
    _write(tmp_path, pumps=[vs, trims], approved={"pumps": [vs["id"], trims["id"]]})
    from erpnext_enhancements.water_engineering import catalog_seed

    catalog_seed.load_catalog(catalog_dir=tmp_path, dry_run=False)
    by_item = {}
    for c in fake.store["Pump Curve"].values():
        by_item.setdefault(c["item"], {})[c["curve_label"]] = c
    vs_curves = by_item["FX-VS"]
    assert set(vs_curves) == {"3450 rpm", "1725 rpm"} and vs_curves["1725 rpm"]["speed_pct"] == 50
    assert set(by_item["FX-TR"]) == {"4 in impeller", "3.5 in impeller", "3.5 in impeller (2)"}
    assert catalog_seed.load_catalog(catalog_dir=tmp_path, dry_run=False)["pumps"]["updated"] == []


@pytest.mark.parametrize(
    "text, inches",
    [(4, 4.0), ('4"', 4.0), ('3-3/4"', 3.75), ("3 3/4 in", 3.75), ('4-3/16"', 4.1875), ("4.25", 4.25), ("trim A", None)],
)
def test_printed_inches(fake, text, inches):
    from erpnext_enhancements.water_engineering.catalog_seed import _inches

    assert _inches(text) == inches


def test_a_floating_aerator_filed_as_a_nozzle_becomes_an_aerator_item(fake, tmp_path):
    aerator = {
        "id": "fixtureco-aerator-1hp",
        "manufacturer": "Fixtureco",
        "model": "FIXTURE-AER 1 HP",
        "part_no": None,
        "type": "other",
        "min_submergence_in": 30,
        "rows": [{"pressure_psi": None, "head_ft": None, "flow_gpm": 400, "height_ft": 5, "spread_ft": 12, "pattern": "Fan"}],
        "listing": ["ETL"],
        "source": [{"url": "https://example.invalid/aer.pdf", "title": "FIXTURE aerator sheet"}],
        "confidence": "table",
        "notes": "115/1/15 A (invented).",
    }
    _write(tmp_path, nozzles=[aerator], approved={"nozzles": [aerator["id"]]})
    from erpnext_enhancements.water_engineering import catalog_seed

    summary = catalog_seed.load_catalog(catalog_dir=tmp_path, dry_run=False)
    assert summary["nozzles"]["errors"] == []
    item = fake.store["Item"]["FIXTURE-AER 1 HP"]
    assert item["item_group"] == "Aerators" and item["custom_equipment_class"] == "Aerator"
    assert item["custom_aerator_pattern_height_ft"] == 5 and item["custom_aerator_pattern_diameter_ft"] == 12
    assert item["custom_aerator_min_depth_ft"] == 2.5
    assert "400 GPM" in item["description"] and "115/1/15 A" in item["description"]
    assert "Nozzle Profile" not in fake.store


def test_a_wiggle_within_reading_noise_after_the_peak_only_warns(fake):
    from erpnext_enhancements.water_engineering.doctype.pump_curve.pump_curve import check_points

    points = [(0, 176.1), (20, 175.3), (40, 175.7), (80, 170.0), (120, 150.0)]
    _ordered, errors, warnings = check_points([{"flow_gpm": f, "head_ft": h} for f, h in points])
    assert errors == [] and any("within reading noise" in w for w in warnings)


def test_unpublished_point_values_read_as_none_not_zero(fake):
    """A NOT NULL DEFAULT 0 column stores 0 for "not published"; the reader must not pass it on."""
    fake.insert(
        FakeDoc(
            fake,
            {"doctype": "Pump Curve", "item": "500999", "curve_label": "Z", "source": "Digitized", "speed_pct": 100,
             "points": [
                 {"flow_gpm": 0, "head_ft": 30, "efficiency_pct": 0, "npshr_ft": 0, "power_bhp": 0},
                 {"flow_gpm": 20, "head_ft": 20, "efficiency_pct": 0, "npshr_ft": 0, "power_bhp": 0},
                 {"flow_gpm": 40, "head_ft": 8, "efficiency_pct": 55, "npshr_ft": 7.5, "power_bhp": 1.1},
             ]},
        )
    )
    from erpnext_enhancements.water_engineering import catalog_reader

    points = catalog_reader.pump_curve_sets(["500999"])["500999"][0]["points"]
    assert points[0] == {"flow_gpm": 0, "head_ft": 30, "efficiency_pct": 0, "npshr_ft": None, "power_bhp": None}
    assert points[1]["efficiency_pct"] is None and points[1]["npshr_ft"] is None
    assert points[2] == {"flow_gpm": 40, "head_ft": 8, "efficiency_pct": 55, "npshr_ft": 7.5, "power_bhp": 1.1}


@pytest.mark.parametrize("sent", ["true", "yes", "on", "1", 1, True])
def test_the_endpoint_treats_every_spelling_of_true_as_a_dry_run(fake, monkeypatch, sent):
    from erpnext_enhancements.water_engineering import catalog_seed

    monkeypatch.setattr(catalog_seed, "CATALOG_DIR", FIXTURES)
    assert catalog_seed.seed_catalog(dry_run=sent)["dry_run"] is True
    assert fake.inserts == []


@pytest.mark.parametrize("sent", ["false", "0", 0])
def test_the_endpoint_writes_only_for_an_explicit_no(fake, monkeypatch, sent):
    from erpnext_enhancements.water_engineering import catalog_seed

    monkeypatch.setattr(catalog_seed, "CATALOG_DIR", FIXTURES)
    assert catalog_seed.seed_catalog(dry_run=sent)["dry_run"] is False
    assert fake.inserts


# ----------------------------------------------------------------------------- field lengths
# On 2026-11-02 the first write on prod refused 120 of the 380 approved rows: Frappe checks a value
# against its column's length only on insert, so the dry run before it had reported clean. The
# loader now applies the same rule before writing, in both modes. Rows below are INVENTED.

#: 157 characters, so the revision "<title> (accessed 2026-11-01), p. 7" is 185, the longest on prod.
LONG_TITLE = "FIXTURE brochure " + "x" * 140


def _long_pump(title=LONG_TITLE):
    return _twin("LONG", 1, id="fixtureco-long", model="FIXTURE-LONG",
                 source=[{"url": "https://example.invalid/long.pdf", "title": title, "page": 7,
                          "accessed": "2026-11-01"}])


def _as_data(fake, doctype, *fields, length=0):
    """The field as it was declared before v1.774.3: Data (140 characters unless ``length`` says)."""
    for field in fields:
        fake.fields[doctype][field] = {**fake.fields[doctype][field], "fieldtype": "Data", "length": length}


def _load(folder, dry_run):
    from erpnext_enhancements.water_engineering import catalog_seed

    return catalog_seed.load_catalog(catalog_dir=folder, dry_run=dry_run)


def test_a_185_character_revision_is_a_dry_run_error_against_a_data_field(fake, tmp_path):
    from erpnext_enhancements.water_engineering import catalog_seed

    row = _long_pump()
    revision = catalog_seed._item_provenance(catalog_seed.provenance(row, {}))["custom_spec_revision"]
    assert len(revision) == 185
    _write(tmp_path, pumps=[row], approved={"pumps": ["fixtureco-long"]})
    _as_data(fake, "Item", "custom_spec_revision")

    dry = _load(tmp_path, dry_run=True)
    assert dry["pumps"]["created"] == []
    (error,) = dry["pumps"]["errors"]
    assert error["key"] == "fixtureco-long"
    assert error["error"] == (f"Item LONG: 'Spec Revision' ({revision}) will get "
                              "truncated, as max characters allowed is 140")
    # The write refuses the same row with the same words, and leaves nothing of it behind.
    wrote = _load(tmp_path, dry_run=False)
    assert wrote["pumps"]["errors"] == dry["pumps"]["errors"]
    assert "LONG" not in fake.store["Item"]
    assert "Fixtureco" not in fake.store.get("Brand", {}) and not fake.store.get("Pump Curve")


def test_a_185_character_revision_loads_once_the_field_is_small_text(fake, tmp_path):
    _write(tmp_path, pumps=[_long_pump()], approved={"pumps": ["fixtureco-long"]})
    assert fake.fields["Item"]["custom_spec_revision"]["fieldtype"] == "Small Text"
    assert fake.fields["Pump Curve"]["source_document"]["fieldtype"] == "Small Text"

    dry = _load(tmp_path, dry_run=True)
    assert dry["pumps"]["errors"] == [] and [e["key"] for e in dry["pumps"]["created"]] == ["fixtureco-long"]
    wrote = _load(tmp_path, dry_run=False)
    assert wrote["pumps"]["errors"] == []
    item = fake.store["Item"]["LONG"]
    assert len(item["custom_spec_revision"]) == 185  # whole, never truncated
    (curve,) = fake.store["Pump Curve"].values()
    assert curve["source_document"] == f"{LONG_TITLE} (accessed 2026-11-01)"


def test_a_curve_value_too_long_refuses_the_row_and_its_item_with_it(fake, tmp_path):
    """The prod write never reached this: each overlong row failed on its Item first."""
    _write(tmp_path, pumps=[_long_pump()], approved={"pumps": ["fixtureco-long"]})
    _as_data(fake, "Pump Curve", "source_document")

    dry = _load(tmp_path, dry_run=True)
    (error,) = dry["pumps"]["errors"]
    assert error["error"].startswith("Pump Curve (3450 rpm): 'Source Document' (FIXTURE brochure x")
    wrote = _load(tmp_path, dry_run=False)
    assert wrote["pumps"]["errors"] == dry["pumps"]["errors"]
    assert "LONG" not in fake.store["Item"]  # inserted, then rolled back


def test_a_light_url_over_140_characters_is_caught_the_same_way(fake, tmp_path):
    url = "https://example.invalid/fixture-light-sheets/" + "l" * 100 + ".pdf"
    assert 140 < len(url) <= 154
    light = {"id": "fixtureco-l-long", "manufacturer": "Fixtureco", "model": "FIXTURE-LLONG", "watts": 20,
             "voltage": [12], "source": {"url": url, "title": "FIXTURE light sheet"}}
    _write(tmp_path, lights=[light], approved={"lights": ["fixtureco-l-long"]})
    _as_data(fake, "Item", "custom_spec_source_url")

    (error,) = _load(tmp_path, dry_run=True)["lights"]["errors"]
    assert error["error"] == (f"Item FIXTURE-LLONG: 'Spec Source URL' ({url}) will get "
                              "truncated, as max characters allowed is 140")

    fake.fields = declared_fields()
    wrote = _load(tmp_path, dry_run=False)
    assert wrote["lights"]["errors"] == []
    assert fake.store["Item"]["FIXTURE-LLONG"]["custom_spec_source_url"] == url


def test_an_update_is_checked_like_an_insert(fake, tmp_path):
    _write(tmp_path, pumps=[_long_pump("FIXTURE brochure rev A")], approved={"pumps": ["fixtureco-long"]})
    _load(tmp_path, dry_run=False)
    stored = fake.store["Item"]["LONG"]["custom_spec_revision"]
    _write(tmp_path, pumps=[_long_pump()], approved={"pumps": ["fixtureco-long"]})
    _as_data(fake, "Item", "custom_spec_revision")

    for dry_run in (True, False):
        (error,) = _load(tmp_path, dry_run=dry_run)["pumps"]["errors"]
        assert "Item LONG: 'Spec Revision'" in error["error"]
    assert fake.store["Item"]["LONG"]["custom_spec_revision"] == stored


def test_a_declared_length_and_the_sites_type_map_set_the_limit(fake, tmp_path):
    _write(tmp_path, pumps=[_long_pump()], approved={"pumps": ["fixtureco-long"]})
    _as_data(fake, "Item", "custom_spec_revision", length=185)
    assert _load(tmp_path, dry_run=True)["pumps"]["errors"] == []
    _as_data(fake, "Item", "custom_spec_revision", length=184)
    assert "max characters allowed is 184" in _load(tmp_path, dry_run=True)["pumps"]["errors"][0]["error"]
    # length 0 takes the type's width from the site's own map when it has one.
    _as_data(fake, "Item", "custom_spec_revision")
    fake.db.type_map = {"Data": ("varchar", 190), "Small Text": ("text", "")}
    assert _load(tmp_path, dry_run=True)["pumps"]["errors"] == []


# ----------------------------------------------------------------------------- the real catalog

REAL_CATALOG = REPO_ROOT / "docs" / "fountain-designer" / "catalog"


def _real_plans():
    """``(kind, label, plan)`` for every approved row of the real catalog, planned by the loader."""
    from erpnext_enhancements.water_engineering import catalog_seed as cs

    meta, approvals = cs.read_approvals(cs._read_json(REAL_CATALOG / cs.APPROVAL_FILE))
    for kind, filename in cs.FILES.items():
        by_key, by_id = approvals[kind]["keys"], approvals[kind]["ids"]
        for key, _base, row in cs.resolve_rows(cs.rows_of(cs._read_json(REAL_CATALOG / filename), kind)):
            row_id = str(row.get("id") or "").strip()
            if row_id in by_id or key in by_key:
                approval = by_id[row_id] if row_id in by_id else by_key[key]
                yield kind, row_id or key, cs.PLANNERS[kind](row, approval, meta)


def test_every_field_the_loader_writes_from_the_real_catalog_is_declared(fake):
    """So the length check below cannot pass by skipping a field the meta does not know."""
    declared = declared_fields()
    plans = list(_real_plans())
    assert {k for k, _l, _p in plans} == {"pumps", "nozzles", "lights"}
    for _kind, label, plan in plans:
        item = {"item_code", *plan["item"]["set"], *plan["item"]["fill"]}
        assert item <= set(declared["Item"]), (label, item - set(declared["Item"]))
        for curve in plan.get("curves") or []:
            assert {*curve, "item", "verified_by"} - {"points"} <= set(declared["Pump Curve"]), label
            assert {f for p in curve["points"] for f in p} <= set(declared["Pump Curve Point"]), label
        if plan.get("profile"):
            fields = {*plan["profile"], *plan["profile_fill"], "item", "verified_by"} - {"performance"}
            assert fields <= set(declared["Nozzle Profile"]), (label, fields - set(declared["Nozzle Profile"]))
            points = {f for p in plan["profile"]["performance"] for f in p}
            assert points <= set(declared["Nozzle Performance Point"]), label


def test_every_approved_row_of_the_real_catalog_fits_its_declared_fields(fake):
    """A catalog row that would overflow a field fails CI here, not on the site's first write."""
    summary = _load(REAL_CATALOG, dry_run=True)
    for kind in ("pumps", "nozzles", "lights"):
        assert summary[kind]["errors"] == [], summary[kind]["errors"][:3]
    approved = len(list(_real_plans()))
    assert sum(len(summary[k]["created"]) for k in ("pumps", "nozzles", "lights")) == approved


def test_the_real_catalog_overflowed_the_old_data_fields(fake):
    """The schema before v1.774.3 refuses rows of all three kinds, every one of them for length."""
    _as_data(fake, "Item", "custom_spec_revision", "custom_spec_source_url")
    _as_data(fake, "Pump Curve", "source_document")
    _as_data(fake, "Nozzle Profile", "source_document")
    summary = _load(REAL_CATALOG, dry_run=True)
    for kind in ("pumps", "nozzles", "lights"):
        assert summary[kind]["errors"], kind
        assert all("max characters allowed is 140" in e["error"] for e in summary[kind]["errors"])
    assert all("Spec Source URL" in e["error"] for e in summary["lights"]["errors"])


# ----------------------------------------------------------------------------- item codes (v1.775.0)
# Nikolas, 2026-11-02: a catalog Item's code is the manufacturer's part number.


@pytest.mark.parametrize("part_no, code", [
    (["340030 (EQ500 1-ph)", "340031 (EQK500 3-ph ODP)"], "340030"),
    ("51296 (order no. on sheet)", "51296"),
    ("F2462038 / F2462051 (ball joint)", "F2462038"),
    ("F2342002 / F2342057 (ball joint)", "F2342002"),
    ("IWC15W100 / IWC15W200", "IWC15W100"),
    (["022032-INT"], "022032-INT"),
    ("4.0050.30.11", "4.0050.30.11"),
    ("  SP0524LED  ", "SP0524LED"),
    ("806-020", "806-020"),  # two numbers, different lengths: a part, not a range
    ("7.3JF100 to 7.3JF400 (cord length variants)", None),
    ("LED3125050 ... LED3125400", None),
    ("601000-601004 / 602000-602004", None),
    ("640120-640124", None),
    (None, None),
    ([], None),
])
def test_part_number_trims_notes_and_refuses_ranges(fake, part_no, code):
    from erpnext_enhancements.water_engineering import catalog_seed

    assert catalog_seed.part_number(part_no) == code


def test_every_row_of_the_real_catalog_gets_a_unique_code(fake):
    from erpnext_enhancements.inventory_enhancements import item_naming_rules
    from erpnext_enhancements.water_engineering import catalog_seed

    codes = catalog_seed.catalog_codes(REAL_CATALOG)
    assert len(codes) == 380
    assert [k for k, v in codes.items() if v.get("error")] == []
    normalized = [item_naming_rules.normalise(v["code"]) for v in codes.values()]
    assert len(set(normalized)) == 380, "two codes are one code to the Item naming guard"
    # 320 rows are coded by part number; approved.json names the Item of three of them (the
    # Pentair pumps Sapphire already stocked under those part numbers), which is the same code.
    assert sum(v["basis"] == "part_no" for v in codes.values()) == 317
    merged = {k: v["code"] for k, v in codes.items() if v["merge"]}
    assert merged == {
        "pentair-whisperfloxf-vs-022035": "022035",
        "pentair-superflo-vst-342002": "342002",
        "pentair-superflo-348190": "348190",
    }
    assert all(codes[k]["basis"] == "approved.json item_code" for k in merged)
    assert codes["pentair-eq-eq500"]["code"] == "340030"
    assert codes["oase-profilux-led-1100-01"]["code"] == "51296"
    assert codes["safe-rain-joint-cascade-f2462038"]["code"] == "F2462038"
    assert codes["grundfos-nbs-015-090-4pole-9p84"]["code"] == "NBS 015-090"
    assert codes["roman-rpj-375"]["code"] == "RPJ-375"
    assert codes["otterbine-gemini-1hp-60hz"]["code"] == "Gemini Aerating Fountain 1 HP (60 Hz)"
    assert codes["otterbine-gemini-1hp-60hz"]["kind"] == "aerators"
    # The comma goes, as the naming rules ask of a code; every other character is the model's.
    assert codes["goulds-3656lh-2x2-5-3450"]["code"] == '3656LH 2x2-5 (also 3756LH) 3450 rpm trim A 4"'


def test_shared_part_numbers_and_ranges_fall_back_to_the_model(fake):
    from erpnext_enhancements.water_engineering import catalog_seed

    codes = catalog_seed.catalog_codes(REAL_CATALOG)
    shared = {k: v for k, v in codes.items() if "shared with another row" in v["basis"]}
    assert len(shared) == 8
    for part in ("F2501065", "F2501561", "F2751006", "F2751017"):
        rows = [v for k, v in shared.items() if part.lower() in k]
        assert len(rows) == 2 and all(part in v["code"] and v["code"] != part for v in rows), part
    assert codes["safe-rain-bubbler-f2501065-water-level-5-cm"]["code"] == "Bubbler F2501065 (water level 5 cm)"
    ranges = {k for k, v in codes.items() if "range" in v["basis"]}
    assert {"kasco-7-3jf-linden", "kasco-7-3jf-sequoia", "kasco-7-3jf-birch"} <= ranges
    assert codes["kasco-7-3jf-linden"]["code"] == "7.3JF Aerating Fountain Linden pattern"
    assert codes["pentair-intellibrite-5g-color-spa-120v"]["code"] == "IntelliBrite 5G Color LED spa light 120 V"
    # SHPF.50 (0.5 HP) and SHPF5.0 (5 HP) are different pumps whose numbers the guard reads as one.
    assert codes["jandy-stealth-shpf050-shpm075"]["code"] == "SHPF.50 / SHPM.75"
    assert codes["jandy-stealth-shpf500"]["code"] == "SHPF5.0 / SHPF5.0-3PH"
    assert "once case and punctuation are ignored" in codes["jandy-stealth-shpf500"]["basis"]


def _coded(**row):
    return {"manufacturer": "Fixtureco", "model": "FIXTURE-X", **row}


def test_an_approved_item_code_wins_and_a_collision_is_refused_not_suffixed(fake):
    from erpnext_enhancements.water_engineering import catalog_seed

    entries = [
        ("pumps", "a", _coded(part_no="FX-1"), {}),
        ("lights", "b", _coded(part_no="FX-2", model="FIXTURE-Y"), {"item_code": "500141"}),
        ("nozzles", "c", _coded(model="FX-1"), {}),  # no part number: its model is a's part number
        ("pumps", "d", _coded(part_no="FX-3"), {"item_code": "FX-1"}),  # chose it: kept
    ]
    codes = catalog_seed.assign_codes(entries)
    assert codes[("lights", "b")]["code"] == "500141" and codes[("lights", "b")]["basis"].startswith("approved")
    assert codes[("pumps", "d")]["code"] == "FX-1"
    for key in (("pumps", "a"), ("nozzles", "c")):
        assert "is also the code of" in codes[key]["error"] and "approved.json" in codes[key]["error"]
    assert not any(v.get("code", "").startswith("FX-1-") for v in codes.values())


def test_a_fresh_load_creates_items_under_the_part_number_and_reports_each_code(fake):
    summary = _seed()
    assert summary["pumps"]["codes"]["Fixtureco|FIXTURE-P100".casefold()] == "FX-100-115"
    assert "FX-100-115" in fake.store["Item"]
    assert fake.store["Item"]["FX-100-115"]["item_name"].startswith("PUMP, FIXTURECO FIXTURE-P100")
    assert summary["nozzles"]["codes"]["fixtureco|fixture-n10"] == "FIXTURE-N10"
    assert summary["pumps"]["renames_pending"] == []


def test_a_code_an_existing_item_holds_is_refused(fake):
    fake.store["Item"]["FX100115"] = {"doctype": "Item", "name": "FX100115", "item_code": "FX100115",
                                       "item_name": "PUMP, SOMETHING ELSE"}
    summary = _seed(dry_run=True)
    (error,) = [e for e in summary["pumps"]["errors"] if e["key"] == "fixtureco|fixture-p100"]
    assert "already held by Item FX100115" in error["error"] and "approved.json" in error["error"]
    assert summary["pumps"]["codes"]["fixtureco|fixture-p100"] == "FX-100-115"


def test_an_item_under_the_old_code_is_found_by_its_catalog_id_and_flagged_for_rename(fake, tmp_path):
    row = _twin("FX-OLD", 1, model="FIXTURE-OLD")
    _write(tmp_path, pumps=[row], approved={"pumps": [row["id"]]})
    fake.store["Item"]["PUMP-FIXTURECO-FIXTURE-OLD"] = {
        "doctype": "Item", "name": "PUMP-FIXTURECO-FIXTURE-OLD", "item_code": "PUMP-FIXTURECO-FIXTURE-OLD",
        "item_name": "PUMP, FIXTURECO FIXTURE-OLD", "item_group": "Pumps", "brand": "Elsewhere",
        "custom_catalog_id": row["id"],
    }
    summary = _load(tmp_path, dry_run=False)
    assert summary["pumps"]["created"] == []
    assert "FX-OLD" not in fake.store["Item"]
    assert summary["pumps"]["renames_pending"] == [
        {"key": row["id"], "item_code": "PUMP-FIXTURECO-FIXTURE-OLD", "code": "FX-OLD"}
    ]


def _codes(**rows):
    return {cid: {"kind": "pumps", "code": code, "basis": "part_no", "legacy_code": f"PUMP-{cid.upper()}"}
            for cid, code in rows.items()}


def test_the_rename_planner_refuses_with_a_reason(fake):
    from erpnext_enhancements.water_engineering import catalog_seed

    codes = _codes(a="FX-A", b="FX-B", c="FX-C", d="FX-D", e="FX-E")
    codes["f"] = {"kind": "lights", "error": "Item code 'X' is also the code of g", "legacy_code": "LIGHT-F"}
    items = [
        {"name": "PUMP-A", "custom_catalog_id": "a"},  # renamed
        {"name": "FX-B", "custom_catalog_id": "b"},  # done already
        {"name": "PUMP-C", "custom_catalog_id": "c"},  # FX-C is taken by a Sapphire Item
        {"name": "500141", "custom_catalog_id": "d"},  # a Sapphire Item attached to the row
        {"name": "PUMP-E", "custom_catalog_id": "e"},
        {"name": "PUMP-E2", "custom_catalog_id": "e"},
        {"name": "LIGHT-F", "custom_catalog_id": "f"},
        {"name": "PUMP-GONE", "custom_catalog_id": "gone"},
    ]
    names = [i["name"] for i in items] + ["fx.c"]
    plan = catalog_seed.plan_renames(items, codes, names)
    assert plan["renames"] == [{"old": "PUMP-A", "kind": "pumps", "catalog_id": "a", "new": "FX-A", "basis": "part_no"}]
    assert plan["unchanged"] == [{"old": "FX-B", "kind": "pumps", "catalog_id": "b"}]
    reasons = {r["old"]: r["reason"] for r in plan["refused"]}
    assert "Item fx.c already holds the code 'FX-C'" in reasons["PUMP-C"]
    assert "not the code the loader gave this row (PUMP-D)" in reasons["500141"]
    assert reasons["PUMP-E"] == reasons["PUMP-E2"] == "2 Items carry this catalog id: PUMP-E, PUMP-E2"
    assert reasons["LIGHT-F"].startswith("Item code 'X' is also the code of g")
    assert reasons["PUMP-GONE"] == "no row in the catalog files has this catalog id"


def test_the_rename_plan_for_prod_as_loaded_on_2026_11_02(fake):
    """Prod's 380 Items carry the old rule's codes; three part numbers are Sapphire Items already,
    and approved.json merges those three rows into them (Nikolas, 2026-11-02)."""
    from erpnext_enhancements.water_engineering import catalog_seed

    codes = catalog_seed.catalog_codes(REAL_CATALOG)
    items = [{"name": v["legacy_code"], "custom_catalog_id": cid} for cid, v in codes.items()]
    sapphire = ["022035", "342002", "348190", "AL10300", "500141"]
    plan = catalog_seed.plan_renames(items, codes, [i["name"] for i in items] + sapphire)
    assert len(plan["renames"]) == 377 and plan["unchanged"] == [] and plan["refused"] == []
    # The names prod's duplicates carry, read on 2026-11-02.
    assert {m["old"]: m["new"] for m in plan["merges"]} == {
        "PUMP-PENTAIR-WHISPERFLOXF-VS-5-THP-COMMERCIAL-VARIABLE-SPEED": "022035",
        "PUMP-PENTAIR-SUPERFLO-VST-VARIABLE-SPEED-2-2-THP": "342002",
        "PUMP-PENTAIR-SUPERFLO-1-1-THP": "348190",
    }
    by_old = {r["old"]: r["new"] for r in plan["renames"]}
    assert by_old["PUMP-PENTAIR-EQ500-EQK500"] == "340030"
    assert by_old["LIGHT-JANDY-COMMERCIAL-WHITE-LED-LIGHT-LARGE-12-VAC-42-W-5000K"] == "WPLV3LS"
    assert by_old["PUMP-GRUNDFOS-NBS-015-090"] == "NBS 015-090"


def test_rewrite_codes_reaches_every_place_a_design_names_an_item(fake):
    from erpnext_enhancements.water_engineering import catalog_seed

    model = {
        "network": {"pumps": [{"id": "p1", "item": "PUMP-OLD", "curve": "PUMP-OLD"}]},
        "equipment": [{"slot": "pump", "item": "PUMP-OLD"}, {"slot": "light", "item": "LIGHT-KEEP"}],
        "features": [{"product": {"item": "NOZZLE-OLD", "generic": False}}],
        "bom": [{"key": "nozzle|NOZZLE-OLD", "item_hint": {"item": "NOZZLE-OLD"}}],
        "prices": {"PUMP-OLD": {"rate": 10}},
        "notes": "was PUMP-OLD before",
        "n": 3,
    }
    new, count = catalog_seed.rewrite_codes(model, {"PUMP-OLD": "340030", "NOZZLE-OLD": "F2501267"})
    assert count == 7
    assert new["network"]["pumps"][0] == {"id": "p1", "item": "340030", "curve": "340030"}
    assert [e["item"] for e in new["equipment"]] == ["340030", "LIGHT-KEEP"]
    assert new["features"][0]["product"]["item"] == "F2501267"
    assert new["bom"][0] == {"key": "nozzle|F2501267", "item_hint": {"item": "F2501267"}}
    assert new["prices"] == {"340030": {"rate": 10}}
    assert new["notes"] == "was PUMP-OLD before" and new["n"] == 3


def _renamable(fake, tmp_path, n=3):
    rows = [_twin(f"FX-R{i}", 1, model=f"FIXTURE-R{i}") for i in range(n)]
    _write(tmp_path, pumps=rows, approved={"pumps": [r["id"] for r in rows]})
    for row in rows:
        old = f"PUMP-FIXTURECO-FIXTURE-R{row['id'][-1]}"
        fake.store["Item"][old] = {"doctype": "Item", "name": old, "item_code": old, "item_name": f"PUMP, {old}",
                                   "item_group": "Pumps", "custom_catalog_id": row["id"]}
    fake.store["Pump Curve"] = {"PCRV-00001": {"name": "PCRV-00001", "item": "PUMP-FIXTURECO-FIXTURE-R0"}}
    model = {"network": {"pumps": [{"id": "p1", "item": "PUMP-FIXTURECO-FIXTURE-R0"}]},
             "equipment": [{"slot": "pump", "item": "PUMP-FIXTURECO-FIXTURE-R2"}]}
    fake.store["Water Feature Design"] = {
        "WFD-1": {"name": "WFD-1", "model_json": json.dumps(model), "results_json": None},
        "WFD-2": {"name": "WFD-2", "model_json": json.dumps({"network": {"pumps": []}}), "results_json": "{}"},
    }
    return rows


def test_a_dry_run_rename_writes_nothing_and_shows_the_designs_it_would_fix(fake, tmp_path):
    from erpnext_enhancements.water_engineering import catalog_seed

    _renamable(fake, tmp_path)
    before = copy.deepcopy(fake.store)
    summary = catalog_seed.rename_items(dry_run="true", catalog_dir=tmp_path)
    assert summary["dry_run"] is True and fake.store == before and fake.renamed == []
    assert [(r["old"], r["new"]) for r in summary["renames"]] == [
        ("PUMP-FIXTURECO-FIXTURE-R0", "FX-R0"), ("PUMP-FIXTURECO-FIXTURE-R1", "FX-R1"),
        ("PUMP-FIXTURECO-FIXTURE-R2", "FX-R2"),
    ]
    assert summary["designs"] == [{"design": "WFD-1", "fields": ["model_json"], "replaced": 2}]
    assert summary["remaining"] == 3


def test_renames_run_in_rounds_and_each_round_fixes_the_designs(fake, tmp_path):
    from erpnext_enhancements.water_engineering import catalog_seed

    _renamable(fake, tmp_path)
    first = catalog_seed.rename_items(dry_run=0, limit=1, catalog_dir=tmp_path)
    assert [r["new"] for r in first["renamed"]] == ["FX-R0"] and first["remaining"] == 2
    assert fake.renamed[0][2] == {"ignore_permissions": True, "show_alert": False, "rebuild_search": False}
    assert fake.store["Pump Curve"]["PCRV-00001"]["item"] == "FX-R0"
    model = json.loads(fake.store["Water Feature Design"]["WFD-1"]["model_json"])
    assert model["network"]["pumps"][0]["item"] == "FX-R0"
    assert model["equipment"][0]["item"] == "PUMP-FIXTURECO-FIXTURE-R2"  # its round has not come
    assert fake.db.commits == 1
    assert any("__global_search" in q for q, _v in fake.sql)
    assert fake.cache.deleted == ["fountain_designer:pump_catalog"]  # the designer's pump catalog
    (comment,) = fake.store["Comment"].values()
    assert comment["reference_name"] == "WFD-1" and "PUMP-FIXTURECO-FIXTURE-R0 → FX-R0" in comment["content"]

    rest = catalog_seed.rename_items(dry_run=0, catalog_dir=tmp_path)
    assert [r["new"] for r in rest["renamed"]] == ["FX-R1", "FX-R2"] and rest["remaining"] == 0
    model = json.loads(fake.store["Water Feature Design"]["WFD-1"]["model_json"])
    assert (model["network"]["pumps"][0]["item"], model["equipment"][0]["item"]) == ("FX-R0", "FX-R2")

    again = catalog_seed.rename_items(dry_run=0, catalog_dir=tmp_path)
    assert again["renames"] == [] and len(again["unchanged"]) == 3 and again["designs"] == []
    assert sorted(fake.store["Item"]) == ["500999", "FX-R0", "FX-R1", "FX-R2"]


def test_a_design_saved_with_an_old_code_after_its_round_is_repaired_by_the_next(fake, tmp_path):
    from erpnext_enhancements.water_engineering import catalog_seed

    _renamable(fake, tmp_path)
    catalog_seed.rename_items(dry_run=0, limit=1, catalog_dir=tmp_path)
    stale = {"network": {"pumps": [{"id": "p1", "item": "PUMP-FIXTURECO-FIXTURE-R0"}]}}
    fake.store["Water Feature Design"]["WFD-2"]["model_json"] = json.dumps(stale)
    summary = catalog_seed.rename_items(dry_run=0, limit=1, catalog_dir=tmp_path)
    assert {d["design"] for d in summary["designs"]} >= {"WFD-2"}
    assert json.loads(fake.store["Water Feature Design"]["WFD-2"]["model_json"])["network"]["pumps"][0]["item"] == "FX-R0"


def test_a_failed_rename_rolls_back_only_that_item(fake, tmp_path):
    from erpnext_enhancements.water_engineering import catalog_seed

    _renamable(fake, tmp_path)
    fake.fail_rename.add("PUMP-FIXTURECO-FIXTURE-R1")
    summary = catalog_seed.rename_items(dry_run=0, catalog_dir=tmp_path)
    assert [r["new"] for r in summary["renamed"]] == ["FX-R0", "FX-R2"]
    assert summary["errors"][0]["old"] == "PUMP-FIXTURECO-FIXTURE-R1" and "refused" in summary["errors"][0]["error"]
    assert summary["remaining"] == 1 and "PUMP-FIXTURECO-FIXTURE-R1" in fake.store["Item"]


def test_the_rename_endpoint_is_system_manager_only_and_dry_by_default(fake, tmp_path, monkeypatch):
    from erpnext_enhancements.water_engineering import catalog_seed

    _renamable(fake, tmp_path)
    monkeypatch.setattr(catalog_seed, "CATALOG_DIR", tmp_path)
    import frappe

    for sent in (1, "1", "true", "yes", None):
        summary = catalog_seed.rename_catalog_items(dry_run=sent)
        assert summary["dry_run"] is True and fake.renamed == [], sent
    assert set(frappe.roles_checked) == {"System Manager"}
    assert catalog_seed.rename_catalog_items()["dry_run"] is True
    assert catalog_seed.rename_catalog_items(dry_run="0", limit="2")["remaining"] == 1


def test_the_rename_never_runs_on_migrate_or_in_a_job(fake):
    hooks = (APP / "hooks.py").read_text(encoding="utf-8")
    source = (WE / "catalog_seed.py").read_text(encoding="utf-8")
    assert "rename_catalog_items" not in hooks and "rename_items" not in hooks
    assert "frappe.enqueue(" not in source and "rebuild_search=False" in source


# --- merging a catalog row into an Item Sapphire already had (Nikolas, 2026-11-02)

DUP = "PUMP-FIXTURECO-FIXTURE-M1"


def _mergeable(fake, tmp_path):
    """Prod's shape for 022035: a Sapphire Item under the part number, and the loader's Item for
    the same pump under the old rule's code, holding the curves; approved.json names the former."""
    row = _twin("FX-M1", 1, model="FIXTURE-M1")
    _write(tmp_path, pumps=[row], approved={"pumps": [{"id": row["id"], "item_code": "FX-M1"}]})
    fake.store["Item"]["FX-M1"] = {
        "doctype": "Item", "name": "FX-M1", "item_code": "FX-M1", "item_name": "PUMP, FIXTURECO M1, 1HP",
        "item_group": "Products", "description": "Sapphire's own words.",
    }
    fake.store["Item"][DUP] = {
        "doctype": "Item", "name": DUP, "item_code": DUP, "item_name": f"PUMP, {DUP}", "item_group": "Pumps",
        "brand": "Fixtureco", "custom_catalog_id": row["id"], "custom_equipment_class": "Pump",
        "custom_model_no": "FIXTURE-M1", "custom_pump_hp": 1,
        "custom_spec_source_url": "https://example.invalid/twin.pdf", "custom_generic_estimate": 0,
    }
    fake.store["Pump Curve"] = {
        "PCRV-00001": {"doctype": "Pump Curve", "name": "PCRV-00001", "item": DUP, "curve_label": "3450 rpm"},
        "PCRV-00002": {"doctype": "Pump Curve", "name": "PCRV-00002", "item": DUP, "curve_label": "1725 rpm"},
    }
    fake.store["Water Feature Design"] = {
        "WFD-1": {"name": "WFD-1", "model_json": "{}",
                  "results_json": json.dumps({"pump_candidates": [{"item": DUP, "ok": True}]})},
    }
    return row


def test_a_dry_run_reports_a_merge_with_both_codes_and_that_the_duplicate_can_go(fake, tmp_path):
    from erpnext_enhancements.water_engineering import catalog_seed

    row = _mergeable(fake, tmp_path)
    before = copy.deepcopy(fake.store)
    summary = catalog_seed.rename_items(dry_run=1, catalog_dir=tmp_path)
    assert summary["merges"] == [{
        "old": DUP, "new": "FX-M1", "kind": "pumps", "catalog_id": row["id"], "basis": "approved.json item_code",
        "moves": {"Pump Curve": ["PCRV-00001", "PCRV-00002"]}, "delete": True,
    }]
    assert summary["renames"] == [] and summary["refused"] == [] and summary["remaining"] == 1
    assert summary["designs"] == [{"design": "WFD-1", "fields": ["results_json"], "replaced": 1}]
    assert fake.store == before and fake.deleted == []


def test_a_merge_moves_the_catalog_fields_and_curves_and_deletes_the_duplicate(fake, tmp_path):
    from erpnext_enhancements.water_engineering import catalog_seed

    row = _mergeable(fake, tmp_path)
    summary = catalog_seed.rename_items(dry_run=0, catalog_dir=tmp_path)
    assert [m["old"] for m in summary["merged"]] == [DUP] and summary["errors"] == []
    assert "custom_catalog_id" in summary["merged"][0]["fields"] and summary["remaining"] == 0
    assert DUP not in fake.store["Item"] and fake.deleted == [("Item", DUP, True, 0)]
    item = fake.store["Item"]["FX-M1"]
    assert item["custom_catalog_id"] == row["id"] and item["custom_equipment_class"] == "Pump"
    assert item["custom_pump_hp"] == 1 and item["custom_generic_estimate"] == 0
    # Sapphire's own fields stay Sapphire's: the group is not moved to "Pumps".
    assert item["item_group"] == "Products" and item["item_name"] == "PUMP, FIXTURECO M1, 1HP"
    assert item["description"] == "Sapphire's own words." and not item.get("brand")
    assert {c["item"] for c in fake.store["Pump Curve"].values()} == {"FX-M1"}
    results = json.loads(fake.store["Water Feature Design"]["WFD-1"]["results_json"])
    assert results["pump_candidates"][0]["item"] == "FX-M1"
    assert fake.cache.deleted and fake.db.commits >= 1

    again = catalog_seed.rename_items(dry_run=0, catalog_dir=tmp_path)
    assert again["merges"] == [] and again["merged"] == [] and again["remaining"] == 0
    assert [u["old"] for u in again["unchanged"]] == ["FX-M1"]


def test_after_the_merge_the_loader_treats_the_existing_item_as_the_catalog_item(fake, tmp_path):
    from erpnext_enhancements.water_engineering import catalog_seed

    _mergeable(fake, tmp_path)
    catalog_seed.rename_items(dry_run=0, catalog_dir=tmp_path)
    curves = copy.deepcopy(fake.store["Pump Curve"])
    summary = _load(tmp_path, dry_run=False)
    pumps = summary["pumps"]
    assert pumps["errors"] == [] and pumps["created"] == [] and pumps["renames_pending"] == []
    assert pumps["codes"] == {"fixtureco-twin-fx-m1": "FX-M1"}
    assert set(fake.store["Pump Curve"]) == set(curves), "the moved curves are found, not loaded again"
    item = fake.store["Item"]["FX-M1"]
    assert item["brand"] == "Fixtureco" and item["item_group"] == "Products"
    assert any("FX-M1" in w and "equipment class" in w for w in pumps["warnings"])


def test_the_loader_refuses_the_row_until_the_merge_has_run(fake, tmp_path):
    _mergeable(fake, tmp_path)
    summary = _load(tmp_path, dry_run=False)
    (error,) = summary["pumps"]["errors"]
    assert DUP in error["error"] and "rename_catalog_items" in error["error"]
    assert not fake.store["Item"]["FX-M1"].get("custom_catalog_id")
    assert {c["item"] for c in fake.store["Pump Curve"].values()} == {DUP}


def test_a_merge_is_refused_with_the_links_that_keep_the_duplicate(fake, tmp_path):
    from erpnext_enhancements.water_engineering import catalog_seed

    _mergeable(fake, tmp_path)
    fake.store["Quotation Item"] = {"QI-1": {"name": "QI-1", "item_code": DUP}}
    fake.store["Fixture Settings"] = {"Fixture Settings": {"default_pump": DUP}}
    for dry_run in (1, 0):
        summary = catalog_seed.rename_items(dry_run=dry_run, catalog_dir=tmp_path)
        assert summary["merges"] == [] and summary["merged"] == [] and summary["remaining"] == 0
        (refused,) = summary["refused"]
        assert refused["old"] == DUP and refused["new"] == "FX-M1"
        assert refused["linked"] == ["Quotation Item.item_code (1)", "Fixture Settings.default_pump (1)"]
        assert "cannot be deleted" in refused["reason"] and "Quotation Item.item_code" in refused["reason"]
    assert DUP in fake.store["Item"] and fake.deleted == []
    assert {c["item"] for c in fake.store["Pump Curve"].values()} == {DUP}


def test_a_merge_that_fails_on_delete_rolls_back_the_moved_curves(fake, tmp_path, monkeypatch):
    """A Link that appears between the check and the delete: delete_doc refuses, never forced."""
    from erpnext_enhancements.water_engineering import catalog_seed

    _mergeable(fake, tmp_path)
    real = catalog_seed.merge_links
    monkeypatch.setattr(catalog_seed, "merge_links", lambda old: (real(old)[0], []))
    fake.store["Item Price"] = {"IP-1": {"name": "IP-1", "item_code": DUP}}
    summary = catalog_seed.rename_items(dry_run=0, catalog_dir=tmp_path)
    assert summary["merged"] == [] and summary["errors"][0]["old"] == DUP
    assert "disable" in summary["errors"][0]["error"]
    assert DUP in fake.store["Item"] and not fake.store["Item"]["FX-M1"].get("custom_catalog_id")
    assert {c["item"] for c in fake.store["Pump Curve"].values()} == {DUP}


def test_the_planner_will_not_merge_into_another_catalog_rows_item(fake):
    from erpnext_enhancements.water_engineering import catalog_seed

    codes = {
        "fx-a": {"kind": "pumps", "code": "SHARED-1", "basis": "approved.json item_code",
                 "legacy_code": "PUMP-FX-A", "merge": True, "fields": []},
        "fx-b": {"kind": "pumps", "code": "SHARED-1", "basis": "part_no", "legacy_code": "PUMP-FX-B",
                 "merge": False, "fields": []},
    }
    items = [{"name": "PUMP-FX-A", "custom_catalog_id": "fx-a"}, {"name": "SHARED-1", "custom_catalog_id": "fx-b"}]
    plan = catalog_seed.plan_renames(items, codes, [i["name"] for i in items])
    assert plan["merges"] == []
    (refused,) = [r for r in plan["refused"] if r["old"] == "PUMP-FX-A"]
    assert "already the Item of catalog row fx-b" in refused["reason"]


def test_a_merge_counts_against_the_round_limit_and_goes_first(fake, tmp_path):
    from erpnext_enhancements.water_engineering import catalog_seed

    row = _mergeable(fake, tmp_path)
    other = _twin("FX-R9", 1, model="FIXTURE-R9")
    _write(tmp_path, pumps=[row, other],
           approved={"pumps": [{"id": row["id"], "item_code": "FX-M1"}, other["id"]]})
    fake.store["Item"]["PUMP-FIXTURECO-FIXTURE-R9"] = {
        "doctype": "Item", "name": "PUMP-FIXTURECO-FIXTURE-R9", "item_code": "PUMP-FIXTURECO-FIXTURE-R9",
        "item_name": "PUMP, R9", "item_group": "Pumps", "custom_catalog_id": other["id"],
    }
    first = catalog_seed.rename_items(dry_run=0, limit=1, catalog_dir=tmp_path)
    assert [m["old"] for m in first["merged"]] == [DUP] and first["renamed"] == [] and first["remaining"] == 1
    second = catalog_seed.rename_items(dry_run=0, limit=1, catalog_dir=tmp_path)
    assert [r["new"] for r in second["renamed"]] == ["FX-R9"] and second["remaining"] == 0
