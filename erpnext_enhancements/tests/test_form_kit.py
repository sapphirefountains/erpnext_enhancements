"""Bench-free tests for the form kit's server engine (form kit P4-A): steps, saves, completeness.

The guided flow walks a DocType's real form, so these tests walk a real Opportunity meta,
built without a bench the way Frappe v16 builds it:

* ERPNext's own ``opportunity.json`` (+ ``Opportunity Lost Reason Detail``, ``Competitor
  Detail``) at ``origin/version-16``, trimmed and committed as
  ``tests/fixtures/form_kit/erpnext_v16_opportunity.json`` (its ``_source`` key records the
  commit, tag and paths);
* this app's Opportunity Custom Fields (``fixtures/custom_field.json``) and the nine fields
  ``setup/custom_fields.create_unified_tabs`` adds on every migrate (Primary Contact, the
  directories, Primary Address, the map), appended as ``Meta.add_custom_fields`` does;
* this app's Opportunity Property Setters (``fixtures/property_setter.json``) applied as
  ``Meta.apply_property_setters`` does, then the fields ordered by a port of v16's
  ``Meta.sort_fields`` (``field_order`` first, then ``insert_after``);
* the app's own child doctypes read from their JSON.

Simplifications, each harmless to what is tested: Custom Fields are appended in file order
(the fixture carries no ``idx``; ``field_order`` decides nearly every position anyway);
``primary_contact_job_title`` / ``_phone`` / ``_email`` / ``_col_break`` and ``address_and_contact``
exist on production (``field_order`` names them) but no file in this repo creates them, so the
built meta does not have them; Custom DocPerms are not applied (the fixtures have none for
Opportunity).

Stubs ``frappe`` in ``setUpModule`` (never at import), with an in-memory store of Opportunities.

Run: python -m unittest erpnext_enhancements.tests.test_form_kit
"""

import ast
import copy
import itertools
import json
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
KIT = APP / "form_kit"
ERPNEXT_FIXTURE = APP / "tests" / "fixtures" / "form_kit" / "erpnext_v16_opportunity.json"
CUSTOM_FIELDS = APP / "fixtures" / "custom_field.json"
PROPERTY_SETTERS = APP / "fixtures" / "property_setter.json"
SETUP_FIELDS = APP / "setup" / "custom_fields.py"

APP_CHILD_DOCTYPES = {
    "Value Stream": "crm_enhancements/doctype/value_stream/value_stream.json",
    "Opportunity Contributor": "crm_enhancements/doctype/opportunity_contributor/opportunity_contributor.json",
    "Design Customer Requests": "project_enhancements/doctype/design_customer_requests/design_customer_requests.json",
    "Build Customer Requests": "project_enhancements/doctype/build_customer_requests/build_customer_requests.json",
    "Service Customer Requests": "project_enhancements/doctype/service_customer_requests/service_customer_requests.json",
    "Rent Customer Requests": "project_enhancements/doctype/rent_customer_requests/rent_customer_requests.json",
    "Design Deliverables": "project_enhancements/doctype/design_deliverables/design_deliverables.json",
    "Build Deliverables": "project_enhancements/doctype/build_deliverables/build_deliverables.json",
    "Service Deliverables": "project_enhancements/doctype/service_deliverables/service_deliverables.json",
    "Rent Deliverables": "project_enhancements/doctype/rent_deliverables/rent_deliverables.json",
}

#: What setup/custom_fields.create_unified_tabs adds to Opportunity (contacts + addresses).
UNIFIED_TAB_FIELDS = [
    {"fieldname": "primary_contact", "label": "Primary Contact", "fieldtype": "Link", "options": "Contact", "insert_after": "contact_info"},
    {"fieldname": "section_break_contacts", "label": "Contact Directory", "fieldtype": "Section Break", "insert_after": "primary_contact"},
    {"fieldname": "contact_list_html", "label": "Contact List HTML", "fieldtype": "HTML", "insert_after": "section_break_contacts"},
    {"fieldname": "section_break_map", "label": "Location", "fieldtype": "Section Break", "insert_after": "contact_list_html"},
    {"fieldname": "primary_address", "label": "Primary Address", "fieldtype": "Link", "options": "Address", "insert_after": "section_break_map"},
    {"fieldname": "location_map_col_break", "fieldtype": "Column Break", "insert_after": "primary_address"},
    {"fieldname": "location_map_html", "label": "Location Map HTML", "fieldtype": "HTML", "insert_after": "location_map_col_break"},
    {"fieldname": "section_break_address_list", "label": "Address Directory", "fieldtype": "Section Break", "insert_after": "location_map_html"},
    {"fieldname": "address_list_html", "label": "Address List HTML", "fieldtype": "HTML", "insert_after": "section_break_address_list"},
]

#: Custom fields this app creates in code rather than in the fixtures (a patch must write them
#: on the migrate that adds them). Each must equal its definition; see TestMergedMeta.
PROBABILITY_SOURCE = APP / "crm_enhancements" / "opportunity_probability.py"
CODE_FIELDS = [
    {
        "fieldname": "custom_probability_by_hand",
        "label": "Probability Set by Hand",
        "fieldtype": "Check",
        "insert_after": "probability",
        "default": "0",
        "description": (
            "Ticked when someone types the probability: a stage change then keeps it. "
            "Untick to go back to the stage's default (Sales Activity Settings)."
        ),
    },
]

CHECK_PROPERTIES = {"hidden", "reqd", "read_only", "bold", "collapsible", "in_list_view", "permlevel", "set_only_once"}
TABLE_TYPES = {"Table", "Table MultiSelect"}

steps = saver = completeness = api = config = None
FRAPPE = None
STATE = None


# --------------------------------------------------------------------------- the meta


class _DF(dict):
    """A docfield: a dict that also answers attribute access, as frappe's does."""

    def __getattr__(self, key):
        if key.startswith("__"):
            raise AttributeError(key)
        return self.get(key)


class FakeMeta:
    def __init__(self, name, fields, permissions=None, istable=0):
        self.name = name
        self.fields = [_DF(f) for f in fields]
        self.permissions = [_DF(p) for p in permissions or []]
        self.istable = istable

    def get_field(self, fieldname):
        return next((df for df in self.fields if df.fieldname == fieldname), None)

    def get_permlevel_access(self, permission_type="read", parenttype=None, *, user=None):
        """frappe/model/meta.py (v16) Meta.get_permlevel_access, verbatim in behaviour."""
        roles = set(FRAPPE.get_roles(user))
        levels = []
        for perm in self.permissions:
            if perm.role in roles and perm.get(permission_type) and perm.get("permlevel", 0) not in levels:
                levels.append(perm.get("permlevel", 0))
        return levels


def _sort_fields(fields, field_order_json):
    """A port of frappe v16 ``Meta.sort_fields`` + ``_update_field_order_based_on_insert_after``."""
    by_name = {f["fieldname"]: f for f in fields}
    field_order = [n for n in json.loads(field_order_json) if n in by_name] if field_order_json else []
    if field_order and len(field_order) == len(fields):
        return [by_name[n] for n in field_order]
    if field_order and fields[0]["fieldname"] not in field_order:
        prepend, found = [], False
        for f in fields:
            if f.get("is_custom_field"):
                break
            if f["fieldname"] in field_order:
                found = True
                break
            prepend.append(f["fieldname"])
        field_order = prepend + field_order if found else prepend
    existing = set(field_order) if field_order else False
    insertion = {}
    for index, f in enumerate(fields):
        if existing and f["fieldname"] in existing:
            continue
        if not f.get("is_custom_field"):
            if existing:
                insertion.setdefault(fields[index - 1]["fieldname"], []).append(f["fieldname"])
            else:
                field_order.append(f["fieldname"])
        elif f.get("insert_after"):
            target = original = f["insert_after"]
            if f["fieldtype"] in ("Section Break", "Column Break") and target in field_order:
                for current in field_order[field_order.index(target) + 1 :]:
                    if by_name[current]["fieldtype"] == "Section Break" or (
                        by_name[current]["fieldtype"] == by_name[original]["fieldtype"]
                    ):
                        break
                    target = current
            elif f["fieldtype"] == "Tab Break" and target in field_order:
                for current in field_order[field_order.index(target) + 1 :]:
                    if by_name[current]["fieldtype"] == "Tab Break":
                        break
                    target = current
            insertion.setdefault(target, []).append(f["fieldname"])
        else:
            field_order.insert(0, f["fieldname"])
    retry = True
    while retry:
        retry = False
        for fieldname in list(insertion):
            if fieldname not in field_order:
                continue
            position = field_order.index(fieldname)
            for name in insertion.pop(fieldname):
                position += 1
                field_order.insert(position, name)
            retry = True
    for names in insertion.values():
        field_order.extend(names)
    return [by_name[n] for n in field_order]


def build_metas(permission_rows=None, field_overrides=None, extra_fields=None):
    """``{doctype: FakeMeta}``: the merged Opportunity and every child the flow reads."""
    erpnext = json.loads(ERPNEXT_FIXTURE.read_text(encoding="utf-8"))["doctypes"]
    opp = erpnext["Opportunity"]
    fields = [dict(f) for f in opp["fields"]]
    customs = [c for c in json.loads(CUSTOM_FIELDS.read_text(encoding="utf-8")) if c["dt"] == "Opportunity"]
    for custom in customs + UNIFIED_TAB_FIELDS + CODE_FIELDS:
        record = {k: v for k, v in custom.items() if v is not None and k not in ("doctype", "dt", "name", "module")}
        record["is_custom_field"] = 1
        fields.append(record)
    for extra in extra_fields or []:
        fields.append(dict(extra, is_custom_field=1))
    doctype_props = {}
    for ps in json.loads(PROPERTY_SETTERS.read_text(encoding="utf-8")):
        if ps.get("doc_type") != "Opportunity":
            continue
        value = ps.get("value")
        if ps["property"] in CHECK_PROPERTIES or ps.get("property_type") in ("Check", "Int"):
            value = int(value or 0)
        if ps.get("doctype_or_field") == "DocType":
            doctype_props[ps["property"]] = value
        elif ps.get("doctype_or_field") == "DocField":
            for f in fields:
                if f["fieldname"] == ps.get("field_name"):
                    f[ps["property"]] = value
                    break
    for fieldname, overrides in (field_overrides or {}).items():
        next(f for f in fields if f["fieldname"] == fieldname).update(overrides)
    ordered = _sort_fields(fields, doctype_props.get("field_order"))
    permissions = list(opp["permissions"]) + list(permission_rows or [])
    metas = {"Opportunity": FakeMeta("Opportunity", ordered, permissions)}
    for name in ("Opportunity Lost Reason Detail", "Competitor Detail"):
        metas[name] = FakeMeta(name, erpnext[name]["fields"], istable=1)
    for name, path in APP_CHILD_DOCTYPES.items():
        data = json.loads((APP / path).read_text(encoding="utf-8"))
        metas[name] = FakeMeta(name, data["fields"], istable=1)
    return metas


# --------------------------------------------------------------------------- the stub


class Row(dict):
    def __getattr__(self, key):
        if key.startswith("__"):
            raise AttributeError(key)
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value

    def set(self, key, value):
        self[key] = value


class FakeDoc:
    def __init__(self, doctype, data):
        object.__setattr__(self, "doctype", doctype)
        object.__setattr__(self, "_d", {})
        tables = {f.fieldname for f in FRAPPE.get_meta(doctype).fields if f.fieldtype in TABLE_TYPES}
        object.__setattr__(self, "_tables", tables)
        for key, value in data.items():
            self.set(key, value)

    def __getattr__(self, key):
        if key.startswith("__"):
            raise AttributeError(key)
        return self._d.get(key)

    def __setattr__(self, key, value):
        self._d[key] = value

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        if key in self._tables:
            self._d[key] = [v if isinstance(v, Row) else Row(v) for v in value or []]
        else:
            self._d[key] = value

    def is_new(self):
        return not self._d.get("name")

    def check_permission(self, ptype="read"):
        if not FRAPPE.has_permission(self.doctype, ptype, doc=self):
            raise FRAPPE.PermissionError(f"No permission to {ptype}")

    def _validate(self):
        # The "*" normalize hook, as far as these tests need it (website_cleanup adds a scheme).
        website = self._d.get("website")
        if website and not str(website).startswith("http"):
            self._d["website"] = "https://" + str(website).strip()
        if self._d.get("custom_opportunity_name"):
            self._d["custom_opportunity_name"] = str(self._d["custom_opportunity_name"]).strip()
        # script_migrations.opportunity.validate_ranks_on_won, reduced.
        if self._d.get("status") == "Closed Won" and not all(
            self._d.get(f) for f in ("custom_scope_rank", "custom_schedule_rank", "custom_budget_rank")
        ):
            raise FRAPPE.ValidationError("Please rank Scope, Schedule and Budget before Closed Won.")

    def _store(self):
        for table in self._tables:
            for row in self._d.get(table) or []:
                if not row.get("name"):
                    STATE["row_seq"] += 1
                    row["name"] = f"row{STATE['row_seq']:03d}"
        STATE["clock"] += 1
        self._d["modified"] = f"2026-10-13 10:00:{STATE['clock']:02d}.000000"
        self._d["modified_by"] = FRAPPE.session.user
        STATE["db"][self._d["name"]] = copy.deepcopy(self._d)

    def save(self):
        self.check_permission("write")
        self._validate()
        STATE["saves"].append(self._d["name"])
        self._store()

    def insert(self):
        if not FRAPPE.has_permission(self.doctype, "create"):
            raise FRAPPE.PermissionError("No permission to create")
        self._validate()
        STATE["seq"] += 1
        self._d["name"] = f"CRM-OPP-2026-{STATE['seq']:05d}"
        self._d["docstatus"] = 0
        STATE["inserts"].append(self._d["name"])
        self._store()


def _install_frappe_stub(metas):
    global FRAPPE, STATE
    frappe = types.ModuleType("frappe")

    class ValidationError(Exception):
        pass

    class PermissionError(Exception):
        pass

    class DoesNotExistError(ValidationError):
        pass

    frappe.ValidationError = ValidationError
    frappe.PermissionError = PermissionError
    frappe.DoesNotExistError = DoesNotExistError

    class _dict(dict):
        def __getattr__(self, key):
            if key.startswith("__"):
                raise AttributeError(key)
            return self.get(key)

        def __setattr__(self, key, value):
            self[key] = value

    frappe._dict = _dict
    frappe._ = lambda s, *a, **k: s

    def throw(msg="", exc=ValidationError, title=None):
        raise exc(msg)

    frappe.throw = throw

    def whitelist(*dargs, **dkwargs):
        def wrap(fn):
            fn._whitelist = dkwargs
            return fn

        return wrap(dargs[0]) if dargs and callable(dargs[0]) else wrap

    frappe.whitelist = whitelist
    frappe.session = types.SimpleNamespace(user="ae@example.com")
    STATE = {
        "metas": metas,
        "roles": {"ae@example.com": ["Sales User"], "boss@example.com": ["Sales Manager", "Sales User"]},
        "perm": {},
        "readable": None,
        "db": {},
        "saves": [],
        "inserts": [],
        "clock": 0,
        "seq": 0,
        "row_seq": 0,
        "sql": [],
        "get_list": [],
    }
    frappe.get_meta = lambda doctype: STATE["metas"][doctype]
    frappe.get_roles = lambda user=None: list(STATE["roles"].get(user or frappe.session.user, []))

    def has_permission(doctype=None, ptype="read", doc=None, user=None, throw=False, **kwargs):
        allowed = STATE["perm"].get(ptype, True)
        if throw and not allowed:
            raise PermissionError(f"No permission for {doctype}")
        return allowed

    frappe.has_permission = has_permission

    def new_doc(doctype):
        data = {"doctype": doctype, "docstatus": 0, "owner": frappe.session.user}
        for df in STATE["metas"][doctype].fields:
            if df.fieldtype in TABLE_TYPES or df.fieldtype in ("Section Break", "Column Break", "Tab Break", "HTML"):
                continue
            if df.get("default") == "Today":
                data[df.fieldname] = "2026-10-13"
            elif df.get("default") not in (None, ""):
                data[df.fieldname] = df.default
            elif df.fieldtype == "Select" and (df.get("options") or "").strip():
                data[df.fieldname] = df.options.split("\n")[0]
        data["company"] = "Sapphire Fountains"
        return FakeDoc(doctype, data)

    frappe.new_doc = new_doc

    def get_doc(doctype, name=None):
        if name not in STATE["db"]:
            raise DoesNotExistError(f"{doctype} {name} not found")
        return FakeDoc(doctype, copy.deepcopy(STATE["db"][name]))

    frappe.get_doc = get_doc

    def get_list(doctype, filters=None, fields=None, limit_page_length=None, **kwargs):
        STATE["get_list"].append({"doctype": doctype, "filters": filters, "fields": list(fields or [])})
        names = filters["name"][1]
        readable = STATE["readable"]
        return [
            {field: STATE["db"][name].get(field) for field in fields}
            for name in names
            if name in STATE["db"] and (readable is None or name in readable)
        ]

    frappe.get_list = get_list

    def sql(query, values=None, as_dict=False):
        STATE["sql"].append((query, values))
        child = re.search(r"from `tab([^`]+)`", query).group(1)
        column = re.search(r"`([a-z_]+)` as value", query).group(1)
        out = []
        for parent in values["names"]:
            for row in STATE["db"][parent].get(values["table"]) or []:
                out.append({"parent": parent, "value": row.get(column)})
        assert child, query
        return out

    frappe.db = types.SimpleNamespace(sql=sql)

    utils = types.ModuleType("frappe.utils")
    utils.cint = lambda value: int(float(value or 0)) if str(value or "0").strip() not in ("",) else 0

    def flt(value, precision=None):
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    utils.flt = flt
    utils.escape_html = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    frappe.utils = utils
    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = utils
    FRAPPE = frappe


def setUpModule():
    global steps, saver, completeness, api, config
    _install_frappe_stub(build_metas())
    for mod in list(sys.modules):
        if mod.startswith("erpnext_enhancements.form_kit"):
            sys.modules.pop(mod, None)
    from erpnext_enhancements.form_kit import api as _api
    from erpnext_enhancements.form_kit import completeness as _completeness
    from erpnext_enhancements.form_kit import config as _config
    from erpnext_enhancements.form_kit import saver as _saver
    from erpnext_enhancements.form_kit import steps as _steps

    steps, saver, completeness, api, config = _steps, _saver, _completeness, _api, _config


def tearDownModule():
    for mod in list(sys.modules):
        if mod.startswith("erpnext_enhancements.form_kit") or mod in ("frappe", "frappe.utils"):
            sys.modules.pop(mod, None)


# --------------------------------------------------------------------------- helpers


def _reset(metas=None, user="ae@example.com", **perm):
    STATE["metas"] = metas or build_metas()
    STATE["perm"] = dict(perm)
    STATE["readable"] = None
    STATE["db"].clear()
    STATE["saves"].clear()
    STATE["inserts"].clear()
    STATE["sql"].clear()
    STATE["get_list"].clear()
    FRAPPE.session.user = user


def _seed(**values):
    """Store an Opportunity and return its name."""
    data = {
        "doctype": "Opportunity",
        "docstatus": 0,
        "status": "Qualification",
        "opportunity_from": "Customer",
        "party_name": "Smith Residence",
        "custom_opportunity_name": "Smith Residence - Backyard Pond Build",
        "custom_value_stream": [{"name": "vs1", "idx": 1, "value_stream": "Design"}],
        "opportunity_owner": "ae@example.com",
        "company": "Sapphire Fountains",
        "naming_series": "CRM-OPP-.YYYY.-",
    }
    data.update(values)
    STATE["seq"] += 1
    data.setdefault("name", f"CRM-OPP-2026-{STATE['seq']:05d}")
    doc = FakeDoc("Opportunity", data)
    doc._store()
    return data["name"]


def _doc(name):
    return FRAPPE.get_doc("Opportunity", name)


def _step(flow, key):
    return next(step for step in flow if step["key"] == key)


def _value_names(step):
    return [f["fieldname"] for f in step["fields"] if f["fieldtype"] not in ("Section Break", "Column Break", "Tab Break")]


def _all_values(flow):
    return [name for step in flow for name in _value_names(step)]


def _field(flow, fieldname):
    for step in flow:
        for field in step["fields"]:
            if field.get("fieldname") == fieldname and field["fieldtype"] not in ("Section Break", "Column Break"):
                return field
    return None


def _desk_visible(meta, cfg):
    """The value fields the Desk form shows: frappe/form/section.js's hidden rule, by hand."""
    shown, tab_hidden, section_hidden = [], False, False
    skip, forced = set(cfg["skip_fields"]), set(cfg["show_hidden"])
    for df in meta.fields:
        if df.fieldtype == "Tab Break":
            tab_hidden, section_hidden = bool(int(df.get("hidden") or 0)), False
            continue
        if df.fieldtype == "Section Break":
            section_hidden = bool(int(df.get("hidden") or 0))
            continue
        if df.fieldtype in ("Column Break", "HTML", "Button", "Fold", "Heading", "Image") or df.fieldname in skip:
            continue
        if df.fieldname in forced or not (int(df.get("hidden") or 0) or section_hidden or tab_hidden):
            shown.append(df.fieldname)
    return shown


LEVELS = [
    {"role": "Sales User", "permlevel": 1, "read": 1},
    {"role": "Sales Manager", "permlevel": 1, "read": 1, "write": 1},
    {"role": "System Manager", "permlevel": 2, "read": 1, "write": 1},
]
LEVEL_FIELDS = {"opportunity_amount": {"permlevel": 1}, "custom_opportunity_summary": {"permlevel": 2}}


# --------------------------------------------------------------------------- the meta itself


class TestMergedMeta(unittest.TestCase):
    def setUp(self):
        self.meta = build_metas()["Opportunity"]
        self.names = [df.fieldname for df in self.meta.fields]

    def test_field_order_property_setter_leads(self):
        self.assertEqual(self.names[:4], ["custom_opportunity_name", "naming_series", "opportunity_from", "party_name"])

    def test_custom_fields_missing_from_field_order_follow_insert_after(self):
        self.assertEqual(self.names[self.names.index("custom_utm_campaign") + 1], "custom_utm_id")
        self.assertEqual(self.names[self.names.index("custom_process_progress") + 1], "custom_handoff_meeting_held")

    def test_every_source_field_is_in_the_meta_once(self):
        self.assertEqual(len(self.names), len(set(self.names)))
        self.assertIn("primary_address", self.names)
        self.assertIn("custom_billing_email", self.names)

    def test_the_code_owned_fields_match_their_definitions(self):
        tree = ast.parse(PROBABILITY_SOURCE.read_text(encoding="utf-8"))
        # The module's top-level string constants (FIELD), substituted so literal_eval can read it.
        constants = {
            n.targets[0].id: n.value.value
            for n in tree.body
            if isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Name)
            and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)
        }

        class Substitute(ast.NodeTransformer):
            def visit_Name(self, node):
                return ast.Constant(constants[node.id]) if node.id in constants else node

        (func,) = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "field_definition"]
        (ret,) = [n for n in ast.walk(func) if isinstance(n, ast.Return)]
        self.assertEqual(ast.literal_eval(Substitute().visit(ret.value)), CODE_FIELDS[0])
        self.assertEqual(self.names[self.names.index("probability") + 1], "custom_probability_by_hand")

    def test_the_unified_tab_fields_are_the_ones_setup_creates(self):
        source = SETUP_FIELDS.read_text(encoding="utf-8")
        for field in UNIFIED_TAB_FIELDS:
            self.assertIn(f'"fieldname": "{field["fieldname"]}"', source)

    def test_the_erpnext_fixture_records_its_source(self):
        source = json.loads(ERPNEXT_FIXTURE.read_text(encoding="utf-8"))["_source"]
        self.assertEqual(source["ref"], "origin/version-16")
        self.assertRegex(source["commit"], r"^[0-9a-f]{40}$")
        self.assertTrue(source["tag"].startswith("v16."))


# --------------------------------------------------------------------------- the config


class TestConfigSchema(unittest.TestCase):
    def setUp(self):
        _reset()
        self.metas = STATE["metas"]
        self.meta = self.metas["Opportunity"]
        self.cfg = config.CONFIGS["Opportunity"]
        self.names = {df.fieldname for df in self.meta.fields}

    def _exists(self, fieldname):
        return fieldname in self.names or fieldname in self.cfg["site_only_fields"]

    def test_the_config_is_structurally_sound(self):
        self.assertEqual(config.check(self.cfg), [])

    def test_the_schema_check_catches_a_duplicate_step_key_and_a_missing_intro(self):
        broken = copy.deepcopy(self.cfg)
        broken["steps"][1]["key"] = broken["steps"][0]["key"]
        broken["steps"][2]["intro"] = ""
        problems = " ".join(config.check(broken))
        self.assertIn("used twice", problems)
        self.assertIn("has no intro", problems)

    def test_only_listed_doctypes_are_configured(self):
        self.assertEqual(set(config.CONFIGS), {"Opportunity"})
        for doctype, cfg in config.CONFIGS.items():
            self.assertEqual(cfg["doctype"], doctype)

    def test_every_configured_fieldname_exists(self):
        named = list(self.cfg["skip_fields"]) + list(self.cfg["show_hidden"]) + list(self.cfg["read_only_fields"])
        named += self.cfg["create"]["fields"] + list(self.cfg["create"]["filled_on_create"])
        for step in self.cfg["steps"]:
            named += step.get("fields") or []
            if step.get("applies"):
                named.append(step["applies"]["field"])
        for requirements in self.cfg["stages"]["lists"].values():
            for requirement in requirements:
                named += config.requirement_fieldnames(requirement)
        for fieldname in named:
            with self.subTest(fieldname=fieldname):
                self.assertTrue(self._exists(fieldname), fieldname)

    def test_site_only_fields_really_are_absent_from_the_repo(self):
        """An excuse that outlives its reason would hide a typo."""
        for fieldname in self.cfg["site_only_fields"]:
            self.assertNotIn(fieldname, self.names)

    def test_every_configured_section_exists(self):
        keys = {section["key"] for section in steps.sections_of(self.meta)}
        for step in self.cfg["steps"]:
            for section in step.get("sections") or []:
                with self.subTest(section=section):
                    self.assertIn(section, keys)

    def test_every_html_field_without_content_is_skipped_with_a_reason(self):
        for df in self.meta.fields:
            if df.fieldtype == "HTML" and not (df.get("options") or "").strip():
                with self.subTest(fieldname=df.fieldname):
                    self.assertTrue(self.cfg["skip_fields"].get(df.fieldname))

    def test_applies_rules_and_request_tables_point_at_real_columns(self):
        for step in self.cfg["steps"]:
            rule = step.get("applies")
            if rule:
                table = self.meta.get_field(rule["field"])
                self.assertIsNotNone(self.metas[table.options].get_field(rule["row_field"]))
        for requirements in self.cfg["stages"]["lists"].values():
            for requirement in requirements:
                chosen = requirement.get("chosen_rows")
                tables = dict(chosen["tables"].values()) if chosen else {}
                if requirement.get("row_field"):
                    tables[requirement["field"]] = requirement["row_field"]
                for table, column in tables.items():
                    child = self.meta.get_field(table).options
                    self.assertIsNotNone(self.metas[child].get_field(column), f"{table}.{column}")

    def test_intros_are_one_plain_sentence(self):
        for step in self.cfg["steps"]:
            with self.subTest(step=step["key"]):
                intro = step["intro"]
                self.assertTrue(intro.endswith("."), intro)
                self.assertEqual(len(re.findall(r"[.!?](\s|$)", intro)), 1, intro)
                self.assertNotRegex(intro, r"\b(colour|organis|cancell|behaviour)", intro)

    def test_the_value_stream_steps_mirror_the_form_script(self):
        """opportunity_migrated_scripts.js toggle_value_stream_fields: each stream shows these sections."""
        script = (APP / "public/js/crm_enhancements/opportunity_migrated_scripts.js").read_text(encoding="utf-8")
        field_map = {
            "Design": ["custom_design_scope"],
            "Build": ["custom_build_scope"],
            "Service": ["custom_service_scope"],
            "Events": ["custom_rent_scope", "custom_rent"],
        }
        for stream, sections in field_map.items():
            for section in sections:
                self.assertIn(f'"{section}"', script)
                step = next(s for s in self.cfg["steps"] if section in (s.get("sections") or []))
                self.assertEqual(step["applies"]["in"], [stream], section)


# --------------------------------------------------------------------------- steps


class TestSteps(unittest.TestCase):
    def setUp(self):
        _reset()
        self.cfg = config.CONFIGS["Opportunity"]
        self.name = _seed(
            custom_value_stream=[
                {"name": "vs1", "idx": 1, "value_stream": "Design"},
                {"name": "vs2", "idx": 2, "value_stream": "Events"},
            ]
        )
        self.flow = steps.flow_steps("Opportunity", _doc(self.name))

    def test_steps_follow_the_config(self):
        self.assertEqual(
            [step["key"] for step in self.flow],
            [
                "basics",
                "contact",
                "scope",
                "scope_design",
                "scope_build",
                "scope_service",
                "scope_events",
                "schedule",
                "schedule_events",
                "budget",
                "close",
                "more",
                "handoff",
            ],
        )
        for step in self.flow:
            self.assertTrue(step["title"])
            self.assertTrue(step["intro"])

    def test_per_stream_steps_apply_only_to_chosen_streams(self):
        active = {step["key"]: step["active"] for step in self.flow}
        self.assertTrue(active["scope_design"])
        self.assertTrue(active["scope_events"])
        self.assertTrue(active["schedule_events"])
        self.assertFalse(active["scope_build"])
        self.assertFalse(active["scope_service"])
        self.assertEqual(
            _step(self.flow, "scope_build")["applies"],
            {"field": "custom_value_stream", "row_field": "value_stream", "in": ["Build"]},
        )

    def test_every_field_the_form_shows_is_in_exactly_one_step(self):
        shown = _desk_visible(STATE["metas"]["Opportunity"], self.cfg)
        offered = _all_values(self.flow)
        self.assertEqual(len(offered), len(set(offered)), "a field is in two steps")
        self.assertEqual(sorted(offered), sorted(shown))

    def test_hidden_and_skipped_fields_are_absent(self):
        offered = set(_all_values(self.flow))
        for fieldname in (
            "custom_drive_folder_id",  # hidden
            "contact_person",  # hidden
            "items",  # in the hidden Items tab
            "utm_source",  # in the hidden Analytics section
            "contact_list_html",  # skipped widget
            "custom_process_progress",  # skipped panel
            "custom_reminder_action",
        ):
            self.assertNotIn(fieldname, offered)
        for fieldname in ("primary_contact", "custom_general_scope_description"):
            self.assertIn(fieldname, offered)  # show_hidden
        # Owner, 2026-10-13: the contributors stay hidden in the flow as on the form.
        self.assertNotIn("custom_scope_contributors", offered)

    def test_the_initial_invoice_section_is_on_the_budget_step(self):
        """v1.326.0 meant it for the Budget tab, but the hand-kept field_order never listed its four
        fields, so v16 placed them after `items`, in the hidden Items tab: on neither the form nor
        the flow. The field_order Property Setter now lists them after the budget notes."""
        names = [df.fieldname for df in STATE["metas"]["Opportunity"].fields]
        at = names.index("custom_notes_relating_to_budget")
        self.assertEqual(
            names[at + 1 : at + 6],
            [
                "custom_initial_invoice_section",
                "custom_first_invoice_percentage",
                "custom_initial_invoice_column_break",
                "custom_billing_email",
                "custom_process_tab",
            ],
        )
        budget = _value_names(_step(self.flow, "budget"))
        self.assertIn("custom_first_invoice_percentage", budget)
        self.assertIn("custom_billing_email", budget)

    def test_step_one_carries_exactly_the_create_fields(self):
        basics = _step(self.flow, "basics")
        create = {f["fieldname"] for f in basics["fields"] if f.get("create")}
        self.assertEqual(
            create,
            {
                "opportunity_from",
                "party_name",
                "custom_opportunity_name",
                "custom_value_stream",
                "opportunity_owner",
                "custom_lead_source",
            },
        )
        self.assertEqual(basics["create"], 1)
        self.assertFalse(any(f.get("create") for step in self.flow[1:] for f in step["fields"]))

    def test_the_real_required_fields_all_fill_themselves_or_are_asked_in_step_one(self):
        meta = STATE["metas"]["Opportunity"]
        required = sorted(df.fieldname for df in meta.fields if int(df.get("reqd") or 0) and steps.is_value_field(df))
        self.assertEqual(
            required,
            sorted(
                [
                    "company",
                    "custom_opportunity_name",
                    "custom_value_stream",
                    "naming_series",
                    "opportunity_from",
                    "opportunity_owner",
                    "party_name",
                    "status",
                    "transaction_date",
                ]
            ),
        )
        self.assertEqual(steps.create_fieldnames(meta, self.cfg), self.cfg["create"]["fields"])

    def test_a_new_required_field_lands_in_step_one(self):
        metas = build_metas(
            extra_fields=[
                {"fieldname": "custom_site_code", "fieldtype": "Data", "label": "Site Code", "reqd": 1, "insert_after": "custom_materials_budget"}
            ]
        )
        _reset(metas)
        flow = steps.flow_steps("Opportunity", FRAPPE.new_doc("Opportunity"))
        self.assertIn("custom_site_code", _value_names(_step(flow, "basics")))
        self.assertEqual(_field(flow, "custom_site_code")["create"], 1)
        self.assertNotIn("custom_site_code", _value_names(_step(flow, "budget")))

    def test_an_unclaimed_section_becomes_its_own_step_before_the_final_one(self):
        metas = build_metas(
            extra_fields=[
                {"fieldname": "custom_survey_section", "fieldtype": "Section Break", "label": "Site Survey", "insert_after": "custom_take_down_date_time_notes"},
                {"fieldname": "custom_survey_notes", "fieldtype": "Small Text", "label": "Survey Notes", "insert_after": "custom_survey_section"},
            ]
        )
        _reset(metas)
        name = _seed()
        flow = steps.flow_steps("Opportunity", _doc(name))
        keys = [step["key"] for step in flow]
        self.assertEqual(keys[-1], "handoff")
        self.assertIn("custom_survey_section", keys)
        self.assertEqual(_step(flow, "custom_survey_section")["title"], "Site Survey")
        self.assertEqual(_value_names(_step(flow, "custom_survey_section")), ["custom_survey_notes"])

    def test_close_gathers_stage_and_ranks_and_keeps_the_lost_section_condition(self):
        close = _step(self.flow, "close")
        names = _value_names(close)
        self.assertEqual(names[:4], ["status", "custom_scope_rank", "custom_schedule_rank", "custom_budget_rank"])
        self.assertIn("lost_reasons", names)
        lost = next(f for f in close["fields"] if f["fieldtype"] == "Section Break" and f["fieldname"] == "lost_detail_section")
        self.assertEqual(lost["depends_on"], 'eval:doc.status==="Lost"')
        self.assertEqual(_field(self.flow, "lost_reasons")["read_only"], 1)  # ERPNext: set by Declare Lost
        self.assertNotIn("status", _value_names(_step(self.flow, "basics")))

    def test_handoff_is_last_read_only_and_links_to_the_form(self):
        handoff = self.flow[-1]
        self.assertEqual(handoff["key"], "handoff")
        self.assertEqual(handoff["final"], 1)
        self.assertEqual(handoff["link"]["tab"], "custom_process_tab")
        self.assertIn("custom_created_project", _value_names(handoff))
        self.assertTrue(all(f["read_only"] for f in handoff["fields"] if "read_only" in f))

    def test_field_defs_carry_only_control_keys_and_the_hint_hook(self):
        field = _field(self.flow, "custom_design_customer_requests")
        self.assertEqual(field["hint"], STATE["metas"]["Opportunity"].get_field("custom_design_customer_requests").description)
        self.assertIsNone(field["help"])
        self.assertNotIn("insert_after", field)
        self.assertEqual([c["fieldname"] for c in field["child_fields"]], ["design_customer_requests"])
        self.assertNotIn("default", _field(self.flow, "transaction_date"))  # "Today" is dynamic
        self.assertEqual(_field(self.flow, "status")["default"], "Qualification")

    def test_layout_has_no_empty_columns_or_sections(self):
        for step in self.flow:
            fields = step["fields"]
            with self.subTest(step=step["key"]):
                self.assertNotEqual(fields[-1]["fieldtype"], "Column Break")
                self.assertNotEqual(fields[-1]["fieldtype"], "Section Break")
                for before, after in itertools.pairwise(fields):
                    self.assertFalse(before["fieldtype"] == "Section Break" and after["fieldtype"] in ("Section Break", "Column Break"))
                    self.assertFalse(before["fieldtype"] == after["fieldtype"] == "Column Break")

    def test_set_only_once_is_editable_only_while_new(self):
        self.assertEqual(_field(self.flow, "naming_series")["read_only"], 1)
        new_flow = steps.flow_steps("Opportunity", FRAPPE.new_doc("Opportunity"))
        self.assertEqual(_field(new_flow, "naming_series")["read_only"], 0)
        self.assertFalse(_step(new_flow, "scope_design")["active"])


class TestFieldPermissions(unittest.TestCase):
    """Permlevels decide per field, the way the Desk form does."""

    def _flow(self, user, **perm):
        _reset(build_metas(permission_rows=LEVELS, field_overrides=LEVEL_FIELDS), user=user, **perm)
        name = _seed(opportunity_amount=5000, custom_opportunity_summary="Pond rebuild")
        return name, steps.flow_steps("Opportunity", _doc(name))

    def test_readable_but_not_writable_levels_come_back_read_only(self):
        _, flow = self._flow("ae@example.com")
        self.assertEqual(_field(flow, "opportunity_amount")["read_only"], 1)
        self.assertEqual(_field(flow, "custom_opportunity_name")["read_only"], 0)

    def test_unreadable_levels_are_not_sent_at_all(self):
        name, flow = self._flow("ae@example.com")
        self.assertIsNone(_field(flow, "custom_opportunity_summary"))
        payload = api.get_flow("Opportunity", name)
        self.assertNotIn("custom_opportunity_summary", payload["values"])
        self.assertEqual(payload["values"]["opportunity_amount"], 5000)

    def test_a_writable_level_is_editable(self):
        _, flow = self._flow("boss@example.com")
        self.assertEqual(_field(flow, "opportunity_amount")["read_only"], 0)
        self.assertIsNone(_field(flow, "custom_opportunity_summary"))

    def test_nothing_is_editable_without_write_on_the_document(self):
        name, flow = self._flow("boss@example.com", write=False)
        editable = [f for step in flow for f in step["fields"] if f.get("read_only") == 0]
        self.assertEqual(editable, [])
        self.assertEqual(api.get_flow("Opportunity", name)["can_write"], 0)

    def test_a_level_write_without_document_write_is_not_editable(self):
        _, flow = self._flow("boss@example.com", write=False)
        self.assertEqual(_field(flow, "opportunity_amount")["read_only"], 1)


# --------------------------------------------------------------------------- saving


class TestSaveStep(unittest.TestCase):
    def setUp(self):
        _reset(build_metas(permission_rows=LEVELS, field_overrides=LEVEL_FIELDS))
        self.name = _seed(
            opportunity_amount=5000,
            custom_design_customer_requests=[
                {"name": "req1", "idx": 1, "design_customer_requests": "A koi pond"},
                {"name": "req2", "idx": 2, "design_customer_requests": "A waterfall"},
            ],
            custom_design_deliverables=[{"name": "del1", "idx": 1, "design_deliverables": "Concept sketch"}],
        )
        self.modified = STATE["db"][self.name]["modified"]

    def _save(self, values, step=None, modified=None):
        return api.save_step("Opportunity", self.name, json.dumps(values), modified or self.modified, step)

    def test_only_offered_fields_are_assigned(self):
        result = self._save(
            {
                "custom_opportunity_name": "  Smith Pond  ",
                "owner": "someone@else.com",
                "docstatus": 1,
                "custom_date_closed_won": "2026-01-01",  # read-only stamp
                "opportunity_amount": 1,  # level 1: read-only for a Sales User
                "custom_opportunity_summary": "x",  # level 2: not even readable
                "custom_drive_folder_id": "evil",  # hidden
            }
        )
        stored = STATE["db"][self.name]
        self.assertEqual(stored["custom_opportunity_name"], "Smith Pond")
        self.assertNotEqual(stored.get("owner"), "someone@else.com")
        self.assertEqual(stored["docstatus"], 0)
        self.assertIsNone(stored.get("custom_date_closed_won"))
        self.assertEqual(stored["opportunity_amount"], 5000)
        self.assertIsNone(stored.get("custom_opportunity_summary"))
        self.assertIsNone(stored.get("custom_drive_folder_id"))
        self.assertEqual(result["values"], {"custom_opportunity_name": "Smith Pond"})

    def test_returned_values_are_the_normalized_ones(self):
        result = self._save({"website": "smithponds.com"}, step="more")
        self.assertEqual(result["values"]["website"], "https://smithponds.com")
        self.assertEqual(result["modified"], STATE["db"][self.name]["modified"])
        self.assertIn("completeness", result)
        self.assertEqual(set(result), {"name", "modified", "values", "completeness"})

    def test_a_named_step_limits_the_save_to_its_fields(self):
        self._save({"custom_opportunity_name": "Changed", "custom_scope_rank": "1"}, step="close")
        stored = STATE["db"][self.name]
        self.assertEqual(stored["custom_scope_rank"], "1")
        self.assertEqual(stored["custom_opportunity_name"], "Smith Residence - Backyard Pond Build")

    def test_child_rows_are_updated_appended_and_removed(self):
        result = self._save(
            {
                "custom_design_customer_requests": [
                    {"name": "req2", "design_customer_requests": "A taller waterfall", "parent": "CRM-OPP-OTHER"},
                    {"design_customer_requests": "Night lighting"},
                ]
            },
            step="scope_design",
        )
        rows = STATE["db"][self.name]["custom_design_customer_requests"]
        self.assertEqual(rows[0]["name"], "req2")
        self.assertEqual([r["design_customer_requests"] for r in rows], ["A taller waterfall", "Night lighting"])
        self.assertEqual([r["idx"] for r in rows], [1, 2])
        self.assertNotIn("req1", [r["name"] for r in rows])
        self.assertNotEqual(rows[0].get("parent"), "CRM-OPP-OTHER")
        # The table the save did not send is untouched.
        self.assertEqual(STATE["db"][self.name]["custom_design_deliverables"][0]["name"], "del1")
        self.assertEqual(
            [row["design_customer_requests"] for row in result["values"]["custom_design_customer_requests"]],
            ["A taller waterfall", "Night lighting"],
        )

    def test_a_row_name_from_elsewhere_is_appended_as_new(self):
        self._save({"custom_design_customer_requests": [{"name": "someone-elses-row", "design_customer_requests": "Mine"}]})
        rows = STATE["db"][self.name]["custom_design_customer_requests"]
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]["name"], "someone-elses-row")

    def test_a_stale_modified_returns_the_conflict_shape_and_saves_nothing(self):
        before = copy.deepcopy(STATE["db"][self.name])
        _doc(self.name).save()  # someone else saves on the Desk meanwhile
        current = STATE["db"][self.name]["modified"]
        saves = len(STATE["saves"])
        result = self._save({"custom_opportunity_name": "Mine"}, step="basics")
        self.assertEqual(len(STATE["saves"]), saves)
        self.assertEqual(STATE["db"][self.name]["custom_opportunity_name"], before["custom_opportunity_name"])
        self.assertTrue(result["conflict"])
        self.assertEqual(result["modified"], current)
        self.assertEqual(result["name"], self.name)
        self.assertEqual(result["server"]["custom_opportunity_name"], before["custom_opportunity_name"])
        self.assertEqual(set(result["server"]), set(_value_names(_step(steps.flow_steps("Opportunity", _doc(self.name)), "basics"))))
        self.assertEqual(set(result), {"conflict", "name", "modified", "modified_by", "server"})

    def test_without_a_step_the_conflict_reports_the_fields_sent(self):
        _doc(self.name).save()
        result = self._save({"website": "x.com", "owner": "evil"})
        self.assertEqual(set(result["server"]), {"website"})

    def test_a_save_without_modified_is_refused(self):
        with self.assertRaises(FRAPPE.ValidationError):
            api.save_step("Opportunity", self.name, json.dumps({"website": "x.com"}), None)

    def test_validation_errors_propagate_and_nothing_is_stored(self):
        with self.assertRaises(FRAPPE.ValidationError):
            self._save({"status": "Closed Won"}, step="close")
        self.assertEqual(STATE["db"][self.name]["status"], "Qualification")

    def test_without_write_permission_the_save_is_refused(self):
        STATE["perm"]["write"] = False
        with self.assertRaises(FRAPPE.PermissionError):
            self._save({"website": "x.com"})

    def test_nothing_offered_means_no_save(self):
        saves = len(STATE["saves"])
        result = self._save({"owner": "evil"})
        self.assertEqual(len(STATE["saves"]), saves)
        self.assertEqual(result["values"], {})


class TestCreateRecord(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_inserts_from_step_one_fields_only(self):
        result = api.create_record(
            "Opportunity",
            json.dumps(
                {
                    "party_name": "Jones Ranch",
                    "custom_opportunity_name": "Jones Ranch - Fountain Service",
                    "custom_value_stream": [{"value_stream": "Service"}],
                    "opportunity_owner": "ae@example.com",
                    "opportunity_amount": 99999,  # not a step-1 field
                    "custom_general_scope_description": "not yet",
                    "status": "Closed Won",  # not a step-1 field either
                    "name": "CHOSEN-NAME",
                }
            ),
        )
        stored = STATE["db"][result["name"]]
        self.assertEqual(STATE["inserts"], [result["name"]])
        self.assertNotEqual(result["name"], "CHOSEN-NAME")
        self.assertEqual(stored["custom_opportunity_name"], "Jones Ranch - Fountain Service")
        self.assertEqual([r["value_stream"] for r in stored["custom_value_stream"]], ["Service"])
        self.assertIsNone(stored.get("opportunity_amount"))
        self.assertIsNone(stored.get("custom_general_scope_description"))
        self.assertEqual(stored["status"], "Qualification")  # the new record's default
        self.assertEqual(stored["opportunity_from"], "Customer")
        self.assertEqual(stored["company"], "Sapphire Fountains")
        self.assertEqual(result["modified"], stored["modified"])
        self.assertIn("custom_opportunity_name", result["values"])
        self.assertNotIn("opportunity_amount", result["values"])
        self.assertEqual(result["completeness"]["stage"], "Qualification")

    def test_refused_without_create_permission(self):
        STATE["perm"]["create"] = False
        with self.assertRaises(FRAPPE.PermissionError):
            api.create_record("Opportunity", json.dumps({"party_name": "X"}))
        self.assertEqual(STATE["inserts"], [])

    def test_a_values_payload_that_is_not_an_object_is_refused(self):
        with self.assertRaises(FRAPPE.ValidationError):
            api.create_record("Opportunity", "[1, 2]")


# --------------------------------------------------------------------------- completeness


def _opp(**values):
    base = {
        "status": "Qualification",
        "party_name": "Smith Residence",
        "custom_opportunity_name": "Smith Pond",
        "custom_value_stream": [{"value_stream": "Design"}],
        "opportunity_owner": "ae@example.com",
        "custom_opportunity_summary": "A pond",
        "primary_contact": "Jane Smith",
    }
    base.update(values)
    return base


class TestCompleteness(unittest.TestCase):
    def setUp(self):
        _reset()

    def _measure(self, **values):
        return completeness.stage_completeness("Opportunity", _opp(**values))

    def _missing(self, **values):
        return [m["label"] for m in self._measure(**values)["missing"]]

    def test_qualification_complete(self):
        self.assertEqual(self._measure(), {"stage": "Qualification", "done": 6, "total": 6, "missing": []})

    def test_either_contact_field_satisfies(self):
        self.assertEqual(self._missing(primary_contact=None, contact_person="CONT-1"), [])
        self.assertEqual(self._missing(primary_contact=None), ["A contact"])

    def test_a_blank_value_stream_row_does_not_count(self):
        self.assertEqual(self._missing(custom_value_stream=[{"value_stream": ""}]), ["At least one Value Stream"])

    def test_needs_analysis_is_cumulative_and_wants_requests_for_a_chosen_stream(self):
        result = self._measure(status="Needs Analysis")
        self.assertEqual(result["total"], 9)
        self.assertEqual(
            [m["label"] for m in result["missing"]],
            ["Lead Source", "Expected Closing Date", "Customer Requests for a chosen value stream"],
        )
        self.assertEqual(result["missing"][-1]["fieldname"], "custom_design_customer_requests")
        filled = self._measure(
            status="Needs Analysis",
            custom_lead_source="Website",
            expected_closing="2026-11-01",
            custom_design_customer_requests=[{"design_customer_requests": "A koi pond"}],
        )
        self.assertEqual(filled["missing"], [])

    def test_requests_for_a_stream_not_chosen_do_not_count(self):
        self.assertIn(
            "Customer Requests for a chosen value stream",
            self._missing(status="Needs Analysis", custom_build_customer_requests=[{"build_customer_requests": "A wall"}]),
        )

    def test_streams_without_a_requests_table_are_ignored(self):
        products = [{"value_stream": "Products"}, {"value_stream": "Delivery"}]
        result = self._measure(status="Needs Analysis", custom_value_stream=products)
        missing = {m["label"]: m["fieldname"] for m in result["missing"]}
        self.assertEqual(missing["Customer Requests for a chosen value stream"], "custom_value_stream")
        with_service = self._measure(
            status="Needs Analysis",
            custom_value_stream=[*products, {"value_stream": "Service"}],
            custom_service_customer_requests=[{"service_customer_requests": "Monthly cleaning"}],
        )
        self.assertNotIn("Customer Requests for a chosen value stream", [m["label"] for m in with_service["missing"]])

    def test_proposal_and_negotiation_want_amount_and_site(self):
        for stage in ("Proposal/Price Quote", "Negotiation/Review"):
            result = self._measure(status=stage)
            self.assertEqual(result["total"], 11)
            self.assertIn("Opportunity Amount", [m["label"] for m in result["missing"]])
            self.assertIn("A site address", [m["label"] for m in result["missing"]])
        self.assertIn("Opportunity Amount", self._missing(status="Proposal/Price Quote", opportunity_amount=0))
        self.assertNotIn(
            "A site address", self._missing(status="Proposal/Price Quote", customer_address="ADDR-1")
        )

    def test_closed_won_wants_the_ranks(self):
        result = self._measure(status="Closed Won")
        self.assertEqual(result["total"], 14)
        labels = [m["label"] for m in result["missing"]]
        self.assertEqual(labels[-3:], ["Scope Rank", "Schedule Rank", "Budget Rank"])

    def test_lost_wants_the_basics_and_a_reason(self):
        # Owner, 2026-10-13: "Basics + lost reason" -- the six Qualification items and the reason.
        self.assertEqual(self._measure(status="Lost")["total"], 7)
        self.assertEqual(self._missing(status="Lost"), ["A lost reason"])
        self.assertEqual(self._missing(status="Lost", lost_reasons=[{"lost_reason": "Price"}]), [])

    def test_on_hold_is_the_basics_only(self):
        self.assertEqual(self._measure(status="On Hold")["total"], 6)

    def test_an_unknown_status_is_measured_as_the_default_stage(self):
        result = self._measure(status="Closed")
        self.assertEqual(result["stage"], "Closed")
        self.assertEqual(result["total"], 6)


class TestCompletenessBatch(unittest.TestCase):
    def setUp(self):
        _reset()
        self.a = _seed(custom_opportunity_summary="A", primary_contact="C1")
        self.b = _seed(status="Needs Analysis", custom_design_customer_requests=[{"name": "r1", "design_customer_requests": "Pond"}])
        self.c = _seed()

    def test_one_list_query_and_one_bound_query_per_table(self):
        result = api.completeness_for("Opportunity", json.dumps([self.a, self.b, self.c]))
        self.assertEqual(set(result), {self.a, self.b, self.c})
        self.assertEqual(len(STATE["get_list"]), 1)
        tables = {values["table"] for _query, values in STATE["sql"]}
        self.assertEqual(len(STATE["sql"]), len(tables))
        self.assertTrue(
            {"custom_value_stream", "custom_design_customer_requests", "lost_reasons"} <= tables
        )
        for query, values in STATE["sql"]:
            self.assertNotIn(self.a, query)  # names are bound, never interpolated
            self.assertEqual(values["doctype"], "Opportunity")
        self.assertEqual(result[self.a]["missing"], [])
        self.assertEqual(
            [m["label"] for m in result[self.b]["missing"]],
            ["Opportunity Summary", "A contact", "Lead Source", "Expected Closing Date"],
        )
        for field in STATE["get_list"][0]["fields"]:
            self.assertNotIn("(", field)  # no SQL function strings (Frappe 16 refuses them)

    def test_records_the_user_cannot_read_are_left_out(self):
        STATE["readable"] = {self.a}
        result = api.completeness_for("Opportunity", [self.a, self.b, "CRM-OPP-NOPE"])
        self.assertEqual(set(result), {self.a})

    def test_refused_without_read_and_beyond_the_batch_limit(self):
        with self.assertRaises(FRAPPE.ValidationError):
            api.completeness_for("Opportunity", [f"N{i}" for i in range(completeness.MAX_BATCH + 1)])
        STATE["perm"]["read"] = False
        with self.assertRaises(FRAPPE.PermissionError):
            api.completeness_for("Opportunity", [self.a])


# --------------------------------------------------------------------------- the endpoints


class TestEndpoints(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_get_flow_payload(self):
        name = _seed()
        payload = api.get_flow("Opportunity", name)
        self.assertEqual(set(payload), {"doctype", "name", "modified", "steps", "values", "completeness", "can_write"})
        self.assertEqual(payload["name"], name)
        self.assertEqual(payload["modified"], STATE["db"][name]["modified"])
        self.assertEqual(payload["can_write"], 1)
        self.assertEqual(payload["values"]["custom_value_stream"][0]["value_stream"], "Design")
        self.assertEqual(set(payload["values"]), set(_all_values(payload["steps"])))

    def test_get_flow_for_a_new_record(self):
        payload = api.get_flow("Opportunity")
        self.assertIsNone(payload["name"])
        self.assertIsNone(payload["modified"])
        self.assertEqual(payload["values"]["status"], "Qualification")
        self.assertEqual(payload["steps"][0]["create"], 1)

    def test_get_flow_needs_create_for_a_new_record_and_read_for_an_existing_one(self):
        name = _seed()
        STATE["perm"]["create"] = False
        with self.assertRaises(FRAPPE.PermissionError):
            api.get_flow("Opportunity")
        STATE["perm"]["read"] = False
        with self.assertRaises(FRAPPE.PermissionError):
            api.get_flow("Opportunity", name)

    def test_doctypes_outside_the_registry_are_refused(self):
        for doctype in ("User", "Sales Invoice", "", None, ["Opportunity"]):
            with self.subTest(doctype=doctype):
                with self.assertRaises(FRAPPE.PermissionError):
                    api.get_flow(doctype)
                with self.assertRaises(FRAPPE.PermissionError):
                    api.save_step(doctype, "X", "{}", "2026-01-01")
                with self.assertRaises(FRAPPE.PermissionError):
                    api.create_record(doctype, "{}")
                with self.assertRaises(FRAPPE.PermissionError):
                    api.completeness_for(doctype, "[]")

    def test_writes_are_post_only_and_nothing_is_open_to_guests(self):
        tree = ast.parse((KIT / "api.py").read_text(encoding="utf-8"))
        decorated = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                for decorator in node.decorator_list:
                    if "whitelist" in ast.unparse(decorator):
                        decorated[node.name] = ast.unparse(decorator)
        self.assertEqual(set(decorated), {"get_flow", "create_record", "save_step", "completeness_for"})
        self.assertIn("methods=['POST']", decorated["create_record"])
        self.assertIn("methods=['POST']", decorated["save_step"])
        for decorator in decorated.values():
            self.assertNotIn("allow_guest", decorator)

    def test_only_api_is_whitelisted(self):
        for path in KIT.rglob("*.py"):
            if path.name == "api.py":
                continue
            self.assertNotIn("frappe.whitelist", path.read_text(encoding="utf-8"), path.name)


if __name__ == "__main__":
    unittest.main()
