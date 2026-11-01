"""Bench-free tests for the Fountain Designer catalog (v1.758.0, phase D).

Covers the Pump Curve and Nozzle Profile point checks, the approved-seed loader
(``water_engineering/catalog_seed.py``: approvals, mapping, provenance, idempotence, one bad row
not stopping the rest), the read layer (``catalog_reader``) and ``api/water_design.pump_curves``
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


class FakeFrappe:
    """Just enough of frappe for the loader, the readers and the patch, over dict tables."""

    def __init__(self):
        self.store = {}
        self.counter = 0
        self.db = FakeDB(self)
        self.inserts = []
        self.saves = []
        self.fail_insert = set()

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
            else:
                data["name"] = data[NAME_FIELD[doctype]]
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
    frappe.model = model
    sys.modules.update(
        {"frappe": frappe, "frappe.utils": utils, "frappe.model": model, "frappe.model.document": document}
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
    item = fake.store["Item"]["PUMP-FIXTURECO-FIXTURE-P100"]
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
    curves = {c["curve_label"]: c for c in fake.store["Pump Curve"].values() if c["item"] == "PUMP-FIXTURECO-FIXTURE-P200VS"}
    assert set(curves) == {"Full speed", "50 % speed"}
    assert curves["Full speed"].get("rpm") is None and curves["Full speed"]["source"] == "Digitized"
    assert curves["50 % speed"]["speed_pct"] == 50
    assert curves["Full speed"]["points"][1]["power_bhp"] == pytest.approx(1.2 / 0.745699872, abs=1e-3)
    item = fake.store["Item"]["PUMP-FIXTURECO-FIXTURE-P200VS"]
    assert item["custom_pump_voltage"] == "230" and item["custom_pump_fla_amps"] == 8.1


def test_an_approved_existing_item_keeps_its_curation(fake):
    _seed()
    item = fake.store["Item"]["500999"]
    assert item["item_name"] == "Pump, 1800 GPH, Submersible"
    assert item["description"] == "Curated by a person."
    assert item["custom_model_no"] == "FIXTURE-EXISTING" and item["custom_rated_gpm"] == 30
    assert item["brand"] == "Fixtureco"  # blank before, so filled
    assert any(c["item"] == "500999" for c in fake.store["Pump Curve"].values())
    assert "PUMP-FIXTURECO-FIXTURE-EXISTING" not in fake.store["Item"]


def test_nozzle_and_light_mapping(fake):
    _seed()
    nozzle_item = fake.store["Item"]["NOZZLE-FIXTURECO-FIXTURE-N10"]
    assert nozzle_item["item_group"] == "Nozzles" and nozzle_item["custom_equipment_class"] == "Nozzle"
    assert fake.store["Item Group"]["Nozzles"]["parent_item_group"] == "Aquatic Equipment"
    assert fake.store["Item"]["NOZZLE-FIXTURECO-FIXTURE-N20"]["item_name"] == "NOZZLE, LAMINAR, FIXTURE N20"
    profile = fake.store["Nozzle Profile"]["Fixtureco FIXTURE-N10"]
    assert profile["item"] == "NOZZLE-FIXTURECO-FIXTURE-N10"
    assert profile["nozzle_type"] == "Foam / Aerating" and profile["level_sensitive"] == 1
    assert profile["connection_size"] == "1 in NPT" and profile["is_generic_estimate"] == 0
    assert profile["source_document"] == "FIXTURE nozzle catalog 2026"
    assert [p["psi"] for p in profile["performance"]] == [5, 10, 15]
    assert profile["performance"][1]["display_height_ft"] == 4.5 and profile["performance"][1]["display_diameter_ft"] == 1.2
    assert profile["rated_gpm"] == 14  # the middle row
    assert fake.store["Nozzle Profile"]["Fixtureco FIXTURE-N20"]["nozzle_type"] == "Laminar"
    assert fake.store["Nozzle Profile"]["Fixtureco FIXTURE-N20"]["level_sensitive"] == 0

    light = fake.store["Item"]["LIGHT-FIXTURECO-FIXTURE-L12"]
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
    assert fake.store["Item"]["PUMP-FIXTURECO-FIXTURE-P100"]["custom_pump_hp"] == 1.5


def test_a_failed_write_rolls_back_only_its_row(fake):
    fake.fail_insert.add("Nozzle Profile")
    summary = _seed()
    errors = {e["key"] for e in summary["nozzles"]["errors"]}
    assert errors == {"fixtureco|fixture-n10", "fixtureco|fixture-n20"}
    assert "NOZZLE-FIXTURECO-FIXTURE-N10" not in fake.store["Item"]  # its Item went with it
    assert "PUMP-FIXTURECO-FIXTURE-P100" in fake.store["Item"]


def test_unknown_approver_leaves_verified_by_blank(fake, tmp_path):
    for path in FIXTURES.glob("*.json"):
        (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    approved = json.loads((tmp_path / "approved.json").read_text(encoding="utf-8"))
    approved["approved_by"] = "nobody@example.invalid"
    (tmp_path / "approved.json").write_text(json.dumps(approved), encoding="utf-8")
    from erpnext_enhancements.water_engineering import catalog_seed

    summary = catalog_seed.load_catalog(catalog_dir=tmp_path, dry_run=False)
    assert "not a User" in summary["note"]
    assert not fake.store["Item"]["PUMP-FIXTURECO-FIXTURE-P100"].get("custom_spec_verified_by")


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
    from erpnext_enhancements.water_engineering.api import water_design

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
    from erpnext_enhancements.water_engineering.api import water_design

    params = water_design.nozzle_profile_params("Fixtureco FIXTURE-N10")
    assert params["rated_gpm"] == 14 and params["nozzle_type"] == "Foam / Aerating"
    assert params["level_sensitive"] is True and params["is_generic_estimate"] is False
    assert [p["psi"] for p in params["points"]] == [5, 10, 15]
    assert water_design.nozzle_profile_params("missing") == {}
    assert water_design.nozzle_profile_params("") == {}


def test_light_specs(fake):
    _seed()
    from erpnext_enhancements.water_engineering import catalog_reader

    spec = catalog_reader.light_specs(["LIGHT-FIXTURECO-FIXTURE-L12"])["LIGHT-FIXTURECO-FIXTURE-L12"]
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
    assert lights == ["LIGHT-FIXTURECO-FIXTURE-L12"]


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
    item = fake.store["Item"]["PUMP-FIXTURECO-FIXTURE-P200VS"]
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
    assert [e["key"] for e in summary["pumps"]["created"]] == ["fixtureco|fixture-twin fx-t1"]
    assert "approve this row by its id" in summary["pumps"]["errors"][0]["error"]
    item = fake.store["Item"]["PUMP-FIXTURECO-FIXTURE-TWIN-FX-T1"]
    assert item["custom_model_no"] == "FIXTURE-TWIN FX-T1" and item["custom_pump_phase"] == "1"
    assert item["custom_pump_fla_amps"] == 5.1  # the first value that is a number; "4.0-4.4" is a range
    assert item["custom_ul_listing"] == "NSF/ANSI 50; UL 1081"
    assert item["custom_spec_revision"] == "FIXTURE twin brochure (accessed 2026-11-01), p. 3"
    assert "4.0-4.4 A @ 230 1-ph" in item["description"]
    curve = next(iter(fake.store["Pump Curve"].values()))
    assert "Read by vector, about +/-2 %." in curve["notes"] and "Invented row." in curve["notes"]


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
    vs_curves = by_item["PUMP-FIXTURECO-FIXTURE-VS"]
    assert set(vs_curves) == {"3450 rpm", "1725 rpm"} and vs_curves["1725 rpm"]["speed_pct"] == 50
    assert set(by_item["PUMP-FIXTURECO-FIXTURE-TRIM"]) == {"4 in impeller", "3.5 in impeller", "3.5 in impeller (2)"}
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
    item = fake.store["Item"]["AERATOR-FIXTURECO-FIXTURE-AER-1-HP"]
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
