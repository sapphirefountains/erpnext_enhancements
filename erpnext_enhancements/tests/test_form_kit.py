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

    def has_field(self, fieldname):
        return self.get_field(fieldname) is not None

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

    def declare_enquiry_lost(self, lost_reasons_list, competitors, detailed_reason=None):
        """ERPNext v16's Opportunity.declare_enquiry_lost, as far as these tests need it."""
        STATE["lost_calls"].append((lost_reasons_list, competitors, detailed_reason))
        if STATE.get("active_quotation"):
            raise FRAPPE.ValidationError("Cannot declare as Lost because an active Quotation exists.")
        self._d["status"] = "Lost"
        self.set("lost_reasons", lost_reasons_list)
        self.set("competitors", competitors)
        if detailed_reason:
            self._d["order_lost_reason"] = detailed_reason
        self.save()

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
        # The unique index on custom_guided_client_id, and a collision a test asks for.
        client_id = self._d.get("custom_guided_client_id")
        if client_id and any(r.get("custom_guided_client_id") == client_id for r in STATE["db"].values()):
            raise FRAPPE.UniqueValidationError("Duplicate entry for custom_guided_client_id")
        if STATE.get("unique_fail"):
            raise FRAPPE.UniqueValidationError(f"Duplicate entry for {STATE['unique_fail']}")
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

    class UniqueValidationError(ValidationError):
        pass

    class DuplicateEntryError(Exception):
        pass

    frappe.ValidationError = ValidationError
    frappe.PermissionError = PermissionError
    frappe.DoesNotExistError = DoesNotExistError
    frappe.UniqueValidationError = UniqueValidationError
    frappe.DuplicateEntryError = DuplicateEntryError

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

    def get_list(doctype, filters=None, fields=None, limit_page_length=None, order_by=None, **kwargs):
        STATE["get_list"].append(
            {"doctype": doctype, "filters": filters, "fields": list(fields or []), "order_by": order_by}
        )
        readable = STATE["readable"]
        if "name" in filters:
            names = filters["name"][1]
            return [
                {field: STATE["db"][name].get(field) for field in fields}
                for name in names
                if name in STATE["db"] and (readable is None or name in readable)
            ]

        def matches(record):
            for key, rule in filters.items():
                value = record.get(key)
                if isinstance(rule, list) and rule[0] == "not in":
                    if value in rule[1]:
                        return False
                elif (value or 0 if rule == 0 else value) != rule:
                    return False
            return True

        rows = [r for name, r in STATE["db"].items() if matches(r) and (readable is None or name in readable)]
        assert order_by == "modified desc"
        rows.sort(key=lambda r: r.get("modified") or "", reverse=True)
        return [{field: r.get(field) for field in fields} for r in rows[:limit_page_length]]

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

    def db_exists(doctype, filters=None, *args, **kwargs):
        if isinstance(filters, dict):
            record = STATE["db"].get(filters.get("name"))
            return bool(record) and all(record.get(k) == v for k, v in filters.items())
        return filters in STATE["db"]

    def db_get_value(doctype, filters, fieldname="name", **kwargs):
        # A read sees the transaction's snapshot (snapshot_hides: rows committed after it began),
        # until a rollback starts a fresh one.
        for name, record in STATE["db"].items():
            if name in STATE["snapshot_hides"]:
                continue
            if all(record.get(k) == v for k, v in filters.items()):
                return record.get(fieldname)
        return None

    def db_rollback():
        STATE["rollbacks"] += 1
        STATE["snapshot_hides"] = set()

    frappe.db = types.SimpleNamespace(sql=sql, exists=db_exists, get_value=db_get_value, rollback=db_rollback)

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
    STATE["lost_calls"] = []
    STATE["active_quotation"] = False
    STATE["snapshot_hides"] = set()
    STATE["rollbacks"] = 0
    STATE["unique_fail"] = None
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
        help_file = json.loads(HELP_FILE.read_text(encoding="utf-8"))["fields"]
        self.assertEqual(field["help"], help_file["custom_design_customer_requests"]["help"])
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
        # Series was the form's one set-only-once field, and the flow now leaves it out, so
        # mark another field the same way.
        _reset(build_metas(field_overrides={"opportunity_from": {"set_only_once": 1}}))
        saved_flow = steps.flow_steps("Opportunity", _doc(_seed()))
        self.assertEqual(_field(saved_flow, "opportunity_from")["read_only"], 1)
        new_flow = steps.flow_steps("Opportunity", FRAPPE.new_doc("Opportunity"))
        self.assertEqual(_field(new_flow, "opportunity_from")["read_only"], 0)
        self.assertFalse(_step(new_flow, "scope_design")["active"])

    def test_the_series_is_left_out_of_every_step(self):
        """Nik, 2026-10-13: there is one series and it never changes."""
        new_flow = steps.flow_steps("Opportunity", FRAPPE.new_doc("Opportunity"))
        for flow in (self.flow, new_flow):
            self.assertIsNone(_field(flow, "naming_series"))


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
                    "naming_series": "OTHER-.YYYY.-",  # not in the flow at all
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
        self.assertEqual(stored["naming_series"], "CRM-OPP-.YYYY.-")  # the one option, from new_doc
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


class TestStartedElsewhere(unittest.TestCase):
    """Form kit P5.2: every new Opportunity opens on the guided page (Nik, 2026-10-13), so one
    started elsewhere (the Customer form's Create > Opportunity, a mapped document) carries its
    values in as ``prefill``, and the server decides which it takes."""

    def setUp(self):
        _reset()

    def _create(self, prefill, **values):
        base = {
            "party_name": "Jones Ranch",
            "custom_opportunity_name": "Jones Ranch - Fountain Service",
            "custom_value_stream": [{"value_stream": "Service"}],
            "opportunity_owner": "ae@example.com",
        }
        base.update(values)
        result = api.create_record("Opportunity", json.dumps(base), json.dumps(prefill))
        return STATE["db"][result["name"]]

    def test_fields_the_flow_offers_and_the_listed_hidden_ones_are_taken(self):
        stored = self._create(
            {
                "customer_address": "Jones Ranch-Billing",
                "contact_person": "Jane Jones-Jones Ranch",
                "custom_description": "<p>From the Lead</p>",
                "company": "Sapphire Fountains",
                "status": "Needs Analysis",
            }
        )
        self.assertEqual(stored["customer_address"], "Jones Ranch-Billing")
        self.assertEqual(stored["contact_person"], "Jane Jones-Jones Ranch")
        self.assertEqual(stored["custom_description"], "<p>From the Lead</p>")
        self.assertEqual(stored["status"], "Needs Analysis")

    def test_step_one_is_never_taken_from_prefill(self):
        stored = self._create({"custom_opportunity_name": "From somewhere else", "party_name": "Other"})
        self.assertEqual(stored["custom_opportunity_name"], "Jones Ranch - Fountain Service")
        self.assertEqual(stored["party_name"], "Jones Ranch")

    def test_what_the_flow_would_not_set_is_ignored(self):
        stored = self._create(
            {
                "owner": "someone@example.com",
                "docstatus": 1,
                "name": "CHOSEN",
                "custom_utm_source": "google",  # attribution: left out of the flow
                "naming_series": "OTHER-.YYYY.-",  # left out of the flow
                "lead": "CRM-LEAD-0001",  # not a field of Opportunity at all
            }
        )
        self.assertEqual(stored["owner"], "ae@example.com")
        self.assertEqual(stored["docstatus"], 0)
        self.assertNotEqual(stored["name"], "CHOSEN")
        self.assertIsNone(stored.get("custom_utm_source"))
        self.assertEqual(stored["naming_series"], "CRM-OPP-.YYYY.-")
        self.assertNotIn("lead", stored)

    def test_a_listed_hidden_field_still_needs_its_permlevel(self):
        metas = build_metas(permission_rows=LEVELS, field_overrides={"contact_person": {"permlevel": 1}})
        _reset(metas, user="ae@example.com")
        stored = self._create({"contact_person": "Jane Jones-Jones Ranch"})
        self.assertIsNone(stored.get("contact_person"))
        _reset(metas, user="boss@example.com")
        stored = self._create({"contact_person": "Jane Jones-Jones Ranch"})
        self.assertEqual(stored["contact_person"], "Jane Jones-Jones Ranch")

    def test_no_prefill_is_the_old_create(self):
        result = api.create_record("Opportunity", json.dumps({"party_name": "Jones Ranch", "custom_opportunity_name": "J",
                                   "custom_value_stream": [{"value_stream": "Service"}], "opportunity_owner": "ae@example.com"}))
        self.assertEqual(STATE["inserts"], [result["name"]])

    def test_prefill_must_be_an_object(self):
        with self.assertRaises(FRAPPE.ValidationError):
            api.create_record("Opportunity", "{}", "[1, 2]")
        self.assertEqual(STATE["inserts"], [])

    def test_a_new_flow_says_what_prefill_may_set(self):
        payload = api.get_flow("Opportunity")
        accepts = set(payload["prefill_fields"])
        self.assertTrue({"customer_address", "contact_person", "custom_description", "company", "party_name"} <= accepts)
        self.assertTrue(accepts.isdisjoint({"custom_utm_source", "naming_series", "language", "owner"}))
        self.assertNotIn("prefill_fields", api.get_flow("Opportunity", _seed()))

    def test_the_listed_hidden_fields_exist_and_are_hidden_on_the_form(self):
        meta = STATE["metas"]["Opportunity"]
        listed = config.CONFIGS["Opportunity"]["create"]["prefill_fields"]
        offered = set(_all_values(steps.flow_steps("Opportunity", FRAPPE.new_doc("Opportunity"))))
        for fieldname, reason in listed.items():
            with self.subTest(fieldname=fieldname):
                self.assertIsNotNone(meta.get_field(fieldname))
                self.assertTrue(reason.strip())
                self.assertNotIn(fieldname, offered, "a field the flow offers needs no listing")


class TestMyRecords(unittest.TestCase):
    """The guided page's start screen: the caller's open deals, newest change first."""

    def setUp(self):
        _reset()

    def test_only_the_callers_open_current_deals(self):
        older = _seed(custom_opportunity_name="Older", customer_name="Smith Residence")
        newer = _seed(custom_opportunity_name="Newer", status="Needs Analysis")
        _seed(custom_opportunity_name="Won", status="Closed Won")
        _seed(custom_opportunity_name="Lost", status="Lost")
        _seed(custom_opportunity_name="Zoho", status="Closed")
        _seed(custom_opportunity_name="Old", custom_is_historical=1)
        _seed(custom_opportunity_name="Theirs", opportunity_owner="someone@example.com")
        mine = api.my_records("Opportunity")
        self.assertEqual([row["name"] for row in mine["rows"]], [newer, older])
        self.assertEqual(mine["rows"][0]["title"], "Newer")
        self.assertEqual(mine["rows"][0]["details"], ["Needs Analysis"])
        self.assertEqual(mine["rows"][1]["details"], ["Smith Residence", "Qualification"])
        self.assertEqual(mine["label"], "Your open opportunities")
        self.assertEqual(STATE["get_list"][-1]["order_by"], "modified desc")

    def test_permissions_decide(self):
        name = _seed()
        STATE["readable"] = set()
        self.assertEqual(api.my_records("Opportunity")["rows"], [])
        STATE["readable"] = {name}
        self.assertEqual([row["name"] for row in api.my_records("Opportunity")["rows"]], [name])
        STATE["perm"]["read"] = False
        with self.assertRaises(FRAPPE.PermissionError):
            api.my_records("Opportunity")

    def test_the_config_names_real_fields(self):
        mine = config.CONFIGS["Opportunity"]["mine"]
        meta = STATE["metas"]["Opportunity"]
        names = [mine["owner_field"], mine["title_field"], mine["open"]["field"], mine["hide_flag"]]
        for fieldname in names + list(mine["detail_fields"]):
            with self.subTest(fieldname=fieldname):
                self.assertIsNotNone(meta.get_field(fieldname))

    def test_the_schema_check_holds_mine_and_prefill_to_their_shape(self):
        broken = dict(config.CONFIGS["Opportunity"])
        broken["mine"] = {"owner_field": "opportunity_owner", "open": {"field": "status"}, "extra": 1}
        broken["create"] = dict(broken["create"], prefill_fields={"contact_person": ""}, extra=1)
        problems = " ".join(config.check(broken))
        for expected in ("unknown mine keys", "mine needs title_field", "mine needs label", "mine.open needs",
                         "unknown create keys", "create.prefill_fields.contact_person has no reason"):
            self.assertIn(expected, problems)


class TestCreateIsNeverRepeated(unittest.TestCase):
    """Form kit P4.7: a create sent with no signal is kept on the phone and sent again when the
    signal returns. The first attempt may have arrived, its answer lost, so the same client id must
    come back with that record rather than a second one. Kept on the record (a hidden unique
    column), not in the cache, which every deploy flushes."""

    VALUES = {
        "party_name": "Jones Ranch",
        "custom_opportunity_name": "Jones Ranch - Fountain Service",
        "custom_value_stream": [{"value_stream": "Service"}],
        "opportunity_owner": "ae@example.com",
    }
    ID = "4f9c2b1e-7a3d-4e8f-9b6a-0c1d2e3f4a5b"

    def setUp(self):
        _reset()

    def _create(self, client_id=ID, **changes):
        return api.create_record("Opportunity", json.dumps(dict(self.VALUES, **changes)), None, client_id)

    def test_the_id_is_kept_on_the_record_it_made(self):
        first = self._create()
        self.assertEqual(STATE["db"][first["name"]]["custom_guided_client_id"], self.ID)
        self.assertNotIn("deduped", first)

    def test_a_resent_create_returns_the_first_record(self):
        first = self._create()
        again = self._create(custom_opportunity_name="Jones Ranch - Fountain Service, renamed")
        self.assertEqual(again["name"], first["name"])
        self.assertEqual(again["deduped"], 1)
        self.assertEqual(STATE["inserts"], [first["name"]])
        # The page re-sends what step 1 holds now; the server answers with what it has.
        self.assertEqual(again["values"]["custom_opportunity_name"], "Jones Ranch - Fountain Service")

    def test_a_resend_racing_the_first_insert_returns_it(self):
        # The second request's snapshot predates the first one's commit: it sees nothing, its
        # insert collides on the unique index, and only a fresh transaction can see the winner.
        first = self._create()
        STATE["snapshot_hides"] = {first["name"]}
        again = self._create()
        self.assertEqual((again["name"], again["deduped"]), (first["name"], 1))
        self.assertEqual(STATE["rollbacks"], 1)
        self.assertEqual(STATE["inserts"], [first["name"]])

    def test_a_record_that_is_gone_is_made_again(self):
        first = self._create()
        del STATE["db"][first["name"]]
        second = self._create()
        self.assertNotEqual(second["name"], first["name"])
        self.assertNotIn("deduped", second)

    def test_another_users_record_is_never_returned(self):
        self._create()
        FRAPPE.session.user = "boss@example.com"
        with self.assertRaises(FRAPPE.UniqueValidationError):
            self._create()
        self.assertEqual(STATE["rollbacks"], 1)

    def test_a_collision_on_something_else_is_raised(self):
        STATE["unique_fail"] = "custom_opportunity_name"
        with self.assertRaises(FRAPPE.UniqueValidationError):
            self._create()

    def test_an_id_not_of_the_pages_shape_is_ignored(self):
        for bad in ("short", "x" * 65, "has space in it", "semi;colon-1234", 12345678):
            with self.subTest(client_id=bad):
                _reset()
                self._create(bad)
                self._create(bad)
                self.assertEqual(len(STATE["inserts"]), 2)

    def test_find_created_answers_only_for_the_callers_own_record(self):
        first = self._create()
        self.assertEqual(api.find_created("Opportunity", self.ID), {"name": first["name"]})
        self.assertEqual(api.find_created("Opportunity", "0000-not-made-0000"), {})
        self.assertEqual(api.find_created("Opportunity", "bad id"), {})
        FRAPPE.session.user = "boss@example.com"
        self.assertEqual(api.find_created("Opportunity", self.ID), {})


class TestDeclareLost(unittest.TestCase):
    """Form kit P4.5: the guided page's Set as Lost. The lost_reasons table is read-only in the
    flow, so choosing Lost there could never pass validate_close_reason with a plain save."""

    def setUp(self):
        _reset()
        self.name = _seed()
        self.modified = STATE["db"][self.name]["modified"]

    def test_it_runs_the_doctypes_own_method_with_rows(self):
        result = api.declare_lost(
            "Opportunity",
            self.name,
            json.dumps([{"lost_reason": "Price"}, "Timing", {"lost_reason": "Price"}, "  "]),
            json.dumps(["Fountain Co"]),
            "Went with a cheaper bid",
            self.modified,
        )
        reasons, competitors, detail = STATE["lost_calls"][0]
        self.assertEqual(reasons, [{"lost_reason": "Price"}, {"lost_reason": "Timing"}])
        self.assertEqual(competitors, [{"competitor": "Fountain Co"}])
        self.assertEqual(detail, "Went with a cheaper bid")
        stored = STATE["db"][self.name]
        self.assertEqual(stored["status"], "Lost")
        self.assertEqual(result["values"]["status"], "Lost")
        self.assertEqual(result["modified"], stored["modified"])

    def test_a_changed_record_is_a_conflict_and_nothing_is_declared(self):
        result = api.declare_lost("Opportunity", self.name, '["Price"]', "[]", "", "2020-01-01 00:00:00")
        self.assertTrue(result["conflict"])
        self.assertEqual(STATE["lost_calls"], [])
        self.assertIn("status", result["server"])

    def test_a_reason_is_required(self):
        with self.assertRaises(FRAPPE.ValidationError):
            api.declare_lost("Opportunity", self.name, "[]", "[]", "", self.modified)
        with self.assertRaises(FRAPPE.ValidationError):
            api.declare_lost("Opportunity", self.name, '{"lost_reason": "Price"}', "[]", "", self.modified)
        self.assertEqual(STATE["lost_calls"], [])

    def test_write_permission_and_a_version_are_required(self):
        with self.assertRaises(FRAPPE.ValidationError):
            api.declare_lost("Opportunity", self.name, '["Price"]', "[]", "", None)
        STATE["perm"]["write"] = False
        with self.assertRaises(FRAPPE.PermissionError):
            api.declare_lost("Opportunity", self.name, '["Price"]', "[]", "", self.modified)
        self.assertEqual(STATE["lost_calls"], [])

    def test_the_doctypes_own_refusal_comes_through(self):
        STATE["active_quotation"] = True
        with self.assertRaises(FRAPPE.ValidationError):
            api.declare_lost("Opportunity", self.name, '["Price"]', "[]", "", self.modified)
        self.assertEqual(STATE["db"][self.name]["status"], "Qualification")

    def test_the_flow_tells_the_page_how_to_ask(self):
        payload = api.get_flow("Opportunity", self.name)
        self.assertEqual(
            payload["lost"],
            {
                "status": "Lost",
                "status_field": "status",
                "reasons": "Opportunity Lost Reason Detail",
                "competitors": "Competitor Detail",
            },
        )
        self.assertEqual(_field(steps.flow_steps("Opportunity", _doc(self.name)), "lost_reasons")["read_only"], 1)
        self.assertEqual(payload["clears"], {"opportunity_from": ["party_name"]})
        self.assertEqual(
            payload["distinct"][0]["fields"], ["custom_scope_rank", "custom_schedule_rank", "custom_budget_rank"]
        )

    def test_the_configured_tables_and_columns_exist(self):
        lost = config.CONFIGS["Opportunity"]["lost"]
        meta = STATE["metas"]["Opportunity"]
        for key, fieldname in (("reasons", "lost_reasons"), ("competitors", "competitors")):
            with self.subTest(table=key):
                self.assertEqual(meta.get_field(fieldname).options, lost[key]["doctype"])
                self.assertIsNotNone(STATE["metas"][lost[key]["doctype"]].get_field(lost[key]["field"]))
        for rule in config.CONFIGS["Opportunity"]["distinct"]:
            for fieldname in rule["fields"]:
                self.assertIsNotNone(meta.get_field(fieldname))
        for fieldname, cleared in config.CONFIGS["Opportunity"]["clears"].items():
            for name in [fieldname, *cleared]:
                self.assertIsNotNone(meta.get_field(name))

    def test_the_schema_check_holds_lost_distinct_and_clears(self):
        broken = dict(config.CONFIGS["Opportunity"])
        broken["lost"] = {"status": "Lost", "reasons": {"doctype": "X"}, "competitors": {}}
        broken["distinct"] = [{"fields": ["a"], "message": ""}]
        broken["clears"] = {"opportunity_from": "party_name"}
        problems = " ".join(config.check(broken))
        for expected in ("lost needs method", "lost.reasons needs", "lost.competitors needs", "distinct rule 0",
                         "clears.opportunity_from"):
            self.assertIn(expected, problems)


class TestGuidedBoot(unittest.TestCase):
    """``frappe.boot.sf_guided``: the DocTypes whose records open on the guided page."""

    def setUp(self):
        _reset()

    def _run(self, conf):
        boot = (APP / "boot.py").read_text(encoding="utf-8")
        tree = ast.parse(boot)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_guided_doctypes")
        namespace = {"frappe": FRAPPE}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "boot.py", "exec"), namespace)
        had = getattr(FRAPPE, "conf", None)
        FRAPPE.conf = conf
        try:
            return namespace["_guided_doctypes"]()
        finally:
            FRAPPE.conf = had

    def test_lists_each_configured_doctype_with_what_a_new_record_accepts(self):
        out = self._run({})
        self.assertEqual(set(out), {"Opportunity"})
        self.assertIn("contact_person", out["Opportunity"]["accepts"])
        self.assertIn("party_name", out["Opportunity"]["accepts"])
        self.assertEqual(out["Opportunity"]["accepts"], sorted(out["Opportunity"]["accepts"]))

    def test_the_kill_switch_and_no_read_permission_leave_it_empty(self):
        self.assertEqual(self._run({"disable_guided_default": 1}), {})
        STATE["perm"]["read"] = False
        self.assertEqual(self._run({}), {})

    def test_it_is_wired_into_boot(self):
        boot = (APP / "boot.py").read_text(encoding="utf-8")
        self.assertIn("bootinfo.sf_guided = _guided_doctypes()", boot)


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
            "A site address", self._missing(status="Proposal/Price Quote", primary_address="ADDR-1")
        )
        # The Billing Address is not a site (Nik, 2026-10-13).
        self.assertIn("A site address", self._missing(status="Proposal/Price Quote", customer_address="ADDR-2"))

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
        self.assertEqual(
            set(payload),
            {
                "doctype",
                "name",
                "modified",
                "steps",
                "values",
                "completeness",
                "can_write",
                "help_article",
                "distinct",
                "clears",
                "lost",
                # Form kit P6.6 (v1.668.0): what a move into Closed Won must carry.
                "won",
            },
        )
        self.assertEqual(payload["name"], name)
        self.assertNotIn("quick", payload, "quick add is for a new record only")
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

    def test_the_moves_and_quick_add_the_side_panel_reads(self):
        """Form kit P6.6 / P6.7 (v1.668.0): ``won`` names fields the flow offers on a saved record,
        and ``quick`` (new records only) asks for fields step 1 shows, filling the rest itself."""
        name = _seed()
        saved = api.get_flow("Opportunity", name)
        won = saved["won"]
        self.assertEqual(won["status"], "Closed Won")
        self.assertEqual(won["status_field"], "status")
        names = set(_all_values(saved["steps"]))
        self.assertEqual(won["fields"], ["custom_scope_rank", "custom_schedule_rank", "custom_budget_rank"])
        self.assertTrue(set(won["fields"]) <= names, "every rank is in the flow")
        new = api.get_flow("Opportunity")
        quick = new["quick"]
        step_one = next(step for step in new["steps"] if step["create"])
        shown = {f["fieldname"]: f for f in step_one["fields"] if f.get("fieldname")}
        for fieldname in quick["fields"]:
            with self.subTest(field=fieldname):
                self.assertIn(fieldname, shown, "quick add asks only for what step 1 shows")
        # What step 1 needs that quick add does not ask for, it fills: fixed, the owner, or a default.
        required = {f for f, d in shown.items() if d.get("reqd") and not d.get("read_only")}
        filled = set(quick["fields"]) | set(quick["fixed"]) | {quick["owner_field"]}
        self.assertEqual(required - filled, set(), "a quick add could never be created")
        self.assertEqual(quick["fixed"], {"opportunity_from": "Customer"})
        self.assertEqual(quick["stage_field"], "status")
        self.assertIn("status", new["prefill_fields"], "the column's stage goes with the create")
        self.assertIn("custom_opportunity_summary", new["prefill_fields"], "so does the summary")

    def test_the_schema_check_holds_won_and_quick(self):
        broken = copy.deepcopy(config.CONFIGS["Opportunity"])
        broken["won"] = {"status": "", "fields": []}
        broken["quick"] = {"fields": [], "extra": 1}
        problems = " ".join(config.check(broken))
        self.assertIn("won needs status", problems)
        self.assertIn("won needs a list of fields", problems)
        self.assertIn("quick needs a list of fields", problems)
        self.assertIn("unknown quick keys", problems)

    def test_get_flow_needs_create_for_a_new_record_and_read_for_an_existing_one(self):
        name = _seed()
        STATE["perm"]["create"] = False
        with self.assertRaises(FRAPPE.PermissionError):
            api.get_flow("Opportunity")
        STATE["perm"]["read"] = False
        with self.assertRaises(FRAPPE.PermissionError):
            api.get_flow("Opportunity", name)

    def test_get_field_help_sends_explanations_with_their_step(self):
        field_help = sys.modules["erpnext_enhancements.form_kit.field_help"]
        original = field_help.load
        field_help.load = lambda doctype: {
            "doctype": doctype,
            "article": "KA-0007",
            "fields": {
                "custom_opportunity_summary": {"hint": "One line", "help": "Why it matters."},
                "custom_lead_source": {"hint": "Where it came from", "help": ""},
                "no_such_field": {"hint": "x", "help": "Never sent: not on the form."},
            },
        }
        try:
            payload = api.get_field_help("Opportunity")
        finally:
            field_help.load = original
        self.assertEqual(payload["article"], "KA-0007")
        self.assertEqual(payload["fields"], {"custom_opportunity_summary": {"help": "Why it matters.", "anchor": "basics"}})

    def test_get_field_help_needs_read_and_a_configured_doctype(self):
        STATE["perm"]["read"] = False
        with self.assertRaises(FRAPPE.PermissionError):
            api.get_field_help("Opportunity")
        STATE["perm"]["read"] = True
        with self.assertRaises(FRAPPE.PermissionError):
            api.get_field_help("User")

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
                with self.assertRaises(FRAPPE.PermissionError):
                    api.my_records(doctype)
                with self.assertRaises(FRAPPE.PermissionError):
                    api.declare_lost(doctype, "X", "[]")

    def test_writes_are_post_only_and_nothing_is_open_to_guests(self):
        tree = ast.parse((KIT / "api.py").read_text(encoding="utf-8"))
        decorated = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                for decorator in node.decorator_list:
                    if "whitelist" in ast.unparse(decorator):
                        decorated[node.name] = ast.unparse(decorator)
        self.assertEqual(
            set(decorated),
            {
                "get_flow",
                "create_record",
                "save_step",
                "completeness_for",
                "get_field_help",
                "my_records",
                "declare_lost",
                "find_created",
            },
        )
        self.assertIn("methods=['POST']", decorated["create_record"])
        self.assertIn("methods=['POST']", decorated["declare_lost"])
        self.assertIn("methods=['POST']", decorated["save_step"])
        for decorator in decorated.values():
            self.assertNotIn("allow_guest", decorator)

    def test_only_api_is_whitelisted(self):
        for path in KIT.rglob("*.py"):
            if path.name == "api.py":
                continue
            self.assertNotIn("frappe.whitelist", path.read_text(encoding="utf-8"), path.name)


# --------------------------------------------------------------------------- field help (P4.3)


HELP_FILE = APP / "form_kit" / "help_text" / "opportunity.json"
DESC_ICONS_JS = APP / "public" / "js" / "global_enhancements" / "field_description_icons.js"


class TestFieldHelp(unittest.TestCase):
    def setUp(self):
        _reset()
        self.help = json.loads(HELP_FILE.read_text(encoding="utf-8"))
        self.field_help = sys.modules["erpnext_enhancements.form_kit.field_help"]

    def test_the_file_covers_every_field_the_flow_shows_and_only_fields_on_the_form(self):
        """Every flow field has help; an extra entry is a field the Desk form still shows that the
        flow leaves out (Language, the attribution fields), whose ⓘ tooltip reads it. A field hidden
        on the form and absent from the flow has no entry."""
        name = _seed()
        offered = set(_all_values(steps.flow_steps("Opportunity", _doc(name))))
        listed = set(self.help["fields"])
        self.assertEqual(offered - listed, set(), "flow fields without help")
        meta = STATE["metas"]["Opportunity"]
        skipped = set(config.CONFIGS["Opportunity"]["skip_fields"])
        for fieldname in listed - offered:
            with self.subTest(fieldname=fieldname):
                df = meta.get_field(fieldname)
                self.assertIsNotNone(df)
                self.assertFalse(df.get("hidden"), "a hidden field needs no help")
                self.assertIn(fieldname, skipped)
        self.assertEqual(self.help["doctype"], "Opportunity")
        for fieldname, entry in self.help["fields"].items():
            with self.subTest(fieldname=fieldname):
                self.assertEqual(set(entry), {"hint", "help"})

    def test_every_hint_is_the_fields_description(self):
        """The hint is also the field's description (fixture or Property Setter), so the Desk form,
        Quick Entry and every grid show the same line as the guided flow."""
        meta = STATE["metas"]["Opportunity"]
        described = {df.fieldname: (df.get("description") or "") for df in meta.fields}
        for fieldname, entry in self.help["fields"].items():
            if not entry["hint"] or fieldname in config.CONFIGS["Opportunity"]["site_only_fields"]:
                continue
            with self.subTest(fieldname=fieldname):
                self.assertEqual(described.get(fieldname), entry["hint"])

    def test_the_flow_reads_hint_and_help_from_the_file(self):
        original = self.field_help.load
        self.field_help.load = lambda doctype: {
            "doctype": doctype,
            "article": None,
            "fields": {"custom_opportunity_summary": {"hint": "One plain line", "help": "Read by everyone."}},
        }
        try:
            flow = steps.flow_steps("Opportunity", _doc(_seed()))
        finally:
            self.field_help.load = original
        summary = _field(flow, "custom_opportunity_summary")
        self.assertEqual((summary["hint"], summary["help"]), ("One plain line", "Read by everyone."))
        # A field the file is silent about keeps its own description and has no (i).
        status = _field(flow, "status")
        self.assertIsNone(status["help"])

    def test_every_field_has_a_hint_and_an_explanation_within_bounds(self):
        """P5.4, reviewed and approved by Nik on 2026-10-13 (all 86 fields and 13 step intros)."""
        for fieldname, entry in self.help["fields"].items():
            with self.subTest(fieldname=fieldname):
                self.assertTrue(entry["hint"].strip())
                self.assertTrue(entry["help"].strip())
                self.assertLessEqual(len(entry["hint"]), 90)
                self.assertLessEqual(len(entry["help"]), 350)
                self.assertNotIn("\u2014", entry["hint"] + entry["help"])

    def test_the_ranks_say_which_end_is_the_top(self):
        for kind in ("scope", "schedule", "budget"):
            with self.subTest(kind=kind):
                self.assertIn("1 is the customer's top priority", self.help["fields"][f"custom_{kind}_rank"]["hint"])

    def test_the_review_decisions_reach_the_form(self):
        meta = STATE["metas"]["Opportunity"]
        for fieldname in (
            "no_of_employees", "annual_revenue", "market_segment", "city", "state", "country",
            "company", "amended_from", "custom_time__materials", "custom_travel_trip",
        ):
            with self.subTest(hidden=fieldname):
                self.assertEqual(int(meta.get_field(fieldname).get("hidden") or 0), 1)
        self.assertEqual(meta.get_field("company").get("default"), "Sapphire Fountains",
                         "hidden and mandatory needs a default, or Customize Form refuses to save")
        for fieldname in config.opportunity.ATTRIBUTION_FIELDS:
            with self.subTest(read_only=fieldname):
                self.assertEqual(int(meta.get_field(fieldname).get("read_only") or 0), 1)
        self.assertNotIn("\u2013", meta.get_field("custom_opportunity_name").get("placeholder"))

    def test_the_loader_reads_the_file_and_tolerates_a_missing_one(self):
        loaded = self.field_help.load("Opportunity")
        self.assertEqual(set(loaded), {"doctype", "article", "fields"})
        self.assertTrue(self.field_help.has_help("Opportunity"))
        self.assertFalse(self.field_help.has_help("Lead"))
        self.assertEqual(self.field_help.load("Lead")["fields"], {})
        self.assertEqual(self.field_help.for_field("Lead", "anything"), {"hint": "", "help": ""})
        self.assertEqual(self.field_help.help_file("Material Request").name, "material_request.json")

    def test_the_desk_tooltip_fetches_quietly_and_escapes(self):
        source = DESC_ICONS_JS.read_text(encoding="utf-8")
        self.assertIn("erpnext_enhancements.form_kit.api.get_field_help", source)
        self.assertIn("fetch(url", source)
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith(("//", "*")))
        self.assertNotIn("frappe.call(", code[code.index("function load_help") : code.index("function more_html")])
        self.assertIn("frappe.utils.escape_html(extra.help)", source)
        self.assertIn("frappe.boot.sf_field_help", source)

    def test_boot_lists_the_doctypes_with_help(self):
        boot = (APP / "boot.py").read_text(encoding="utf-8")
        self.assertIn("bootinfo.sf_field_help = _field_help_doctypes()", boot)


if __name__ == "__main__":
    unittest.main()
