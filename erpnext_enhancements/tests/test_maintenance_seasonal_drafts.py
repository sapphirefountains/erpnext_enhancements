# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""An open seasonal draft is not the site's regular visit.

On 2026-10-05 a technician did Myers Mortuary's regular monthly visit, and every
way of opening the form gave them the Winterization visit instead. The only open
draft for the site was the Winterization, and each entry point treated "an open
draft" as "the site's visit":

* the kiosk's Maintenance Form button opened the newest open draft of any kind;
* "Log a visit" (``create_visit``) returned any open draft on the contract;
* "Upcoming: do one early" hid a Per Site Visit site that had any open draft.

The scheduler never made this mistake. It dedupes the regular draft on
``visit_label is not set``, and that is now the rule everywhere.

Bench-free: it installs its own ``frappe`` stub and runs the real modules.

Run: python -m unittest erpnext_enhancements.tests.test_maintenance_seasonal_drafts
"""

import datetime
import importlib
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

D = datetime.date
TODAY = D(2026, 10, 5)
RECORD = "Sapphire Maintenance Record"
CONTRACT = "Sapphire Maintenance Contract"
FEATURE = "Sapphire Contract Feature"

RECORD_MODULE = (
    "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record"
)
STUBBED = (
    "frappe",
    "frappe.utils",
    RECORD_MODULE,
    "erpnext_enhancements.workforce",
    "erpnext_enhancements.workforce.tracking_health",
    "erpnext_enhancements.workforce.doctype",
    "erpnext_enhancements.workforce.doctype.time_kiosk_settings",
    "erpnext_enhancements.workforce.doctype.time_kiosk_settings.time_kiosk_settings",
    "erpnext_enhancements.api.maintenance_visit",
    "erpnext_enhancements.api.time_kiosk",
)
_saved_modules = {}
frappe = None
visit = None
kiosk = None


class _Doc(dict):
    """Enough of a frappe Document: dict access plus attribute access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        self[name] = value


class _NewRecord(_Doc):
    def insert(self):
        frappe.inserted += 1
        self.name = f"NEW-{frappe.inserted}"
        self.setdefault("visit_label", None)
        self.docstatus = 0
        frappe.rows[RECORD].append(self)
        return self


def _getdate(value=None):
    if value is None:
        return TODAY
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, D):
        return value
    return D.fromisoformat(str(value)[:10])


def _add_days(value, days):
    return _getdate(value) + datetime.timedelta(days=days)


def _is_set(value):
    return value not in (None, "")


def _match(row, filters):
    for field, cond in (filters or {}).items():
        value = row.get(field)
        if isinstance(cond, (list, tuple)):
            op, arg = cond
            if op == "is":
                if (arg == "set") != _is_set(value):
                    return False
            elif op == "in":
                if value not in arg:
                    return False
            elif op == "between":
                if not _is_set(value) or not _getdate(arg[0]) <= _getdate(value) <= _getdate(arg[1]):
                    return False
            else:
                raise AssertionError(f"stub has no operator {op!r}")
        elif value != cond:
            return False
    return True


def _get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, as_list=False, **_):
    rows = [row for row in frappe.rows.get(doctype, []) if _match(row, filters)]
    if order_by:
        field, _, direction = order_by.partition(" ")
        rows.sort(key=lambda row: str(row.get(field) or ""), reverse=direction.strip() == "desc")
    if pluck:
        return [row.get(pluck) for row in rows]
    fields = fields or ["name"]
    if as_list:
        return [tuple(row.get(f) for f in fields) for row in rows]
    return [_Doc({f: row.get(f) for f in fields}) for row in rows]


def _get_value(doctype, filters, fieldname="name", as_dict=False, **_):
    if isinstance(filters, str):
        filters = {"name": filters}
    rows = [row for row in frappe.rows.get(doctype, []) if _match(row, filters)]
    rows.sort(key=lambda row: str(row.get("creation") or ""), reverse=True)
    if not rows:
        return None
    if isinstance(fieldname, (list, tuple)):
        values = _Doc({f: rows[0].get(f) for f in fieldname})
        return values if as_dict else tuple(values.values())
    return rows[0].get(fieldname)


def _throw(message, *_args, **_kwargs):
    raise frappe.ValidationError(message)


def setUpModule():
    global frappe, visit, kiosk
    for name in STUBBED:
        _saved_modules[name] = sys.modules.pop(name, None)

    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe._ = lambda text, *a, **k: text
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.throw = _throw
    frappe.session = types.SimpleNamespace(user="tech@example.com")
    frappe.get_roles = lambda *a: ["Maintenance User"]
    frappe.get_all = _get_all
    frappe.get_list = _get_all
    frappe.get_doc = lambda doctype, name: next(r for r in frappe.rows[doctype] if r.name == name)
    frappe.new_doc = lambda doctype: _NewRecord(doctype=doctype)
    frappe.db = types.SimpleNamespace(
        get_value=_get_value,
        exists=lambda doctype, filters: _get_value(doctype, filters),
    )
    frappe.rows = {}
    frappe.inserted = 0

    utils = types.ModuleType("frappe.utils")
    utils.getdate = _getdate
    utils.add_days = _add_days
    utils.nowdate = lambda: TODAY.isoformat()
    utils.date_diff = lambda a, b: (_getdate(a) - _getdate(b)).days
    utils.cint = lambda v: int(v or 0)
    utils.flt = lambda v: float(v or 0)
    utils.get_datetime = lambda v=None: v
    utils.now_datetime = lambda: datetime.datetime(2026, 10, 5, 9, 0)
    frappe.utils = utils
    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = utils

    record_module = types.ModuleType(RECORD_MODULE)
    record_module.get_dashboard_context = lambda *a, **k: {}
    record_module.get_visit_payload = lambda *a, **k: {}
    record_module.resolve_template = lambda *a, **k: None
    sys.modules[RECORD_MODULE] = record_module

    # time_kiosk's own imports, none of which get_maintenance_context touches.
    workforce = types.ModuleType("erpnext_enhancements.workforce")
    for sub in ("client_time", "costing", "photo_gate", "sites", "tracking_health"):
        setattr(workforce, sub, types.ModuleType(f"erpnext_enhancements.workforce.{sub}"))
    workforce.tracking_health.haversine_m = lambda *a: 0
    settings = types.ModuleType("erpnext_enhancements.workforce.doctype.time_kiosk_settings.time_kiosk_settings")
    settings.get_settings = lambda: {}
    sys.modules["erpnext_enhancements.workforce"] = workforce
    sys.modules["erpnext_enhancements.workforce.tracking_health"] = workforce.tracking_health
    sys.modules["erpnext_enhancements.workforce.doctype"] = types.ModuleType("doctype")
    sys.modules["erpnext_enhancements.workforce.doctype.time_kiosk_settings"] = types.ModuleType("tks")
    sys.modules[settings.__name__] = settings

    visit = importlib.import_module("erpnext_enhancements.api.maintenance_visit")
    kiosk = importlib.import_module("erpnext_enhancements.api.time_kiosk")


def tearDownModule():
    for name, module in _saved_modules.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _site(shape="Per Site Visit", next_visit="2026-11-02"):
    """Myers Mortuary as it stood on 2026-10-05: one fountain, monthly."""
    feature = _Doc(parent="CON-1", parenttype=CONTRACT, idx=1, serial_no="SN-1", next_visit_date=next_visit)
    frappe.inserted = 0
    frappe.rows = {
        CONTRACT: [
            _Doc(
                name="CON-1",
                customer="Myers Mortuary",
                project="PRJ-1",
                status="Active",
                visit_shape=shape,
                covered_features=[feature],
            )
        ],
        FEATURE: [feature],
        RECORD: [],
        "Project": [_Doc(name="PRJ-1", project_name="Myers Mortuary", customer="Myers Mortuary")],
        "Serial No": [_Doc(name="SN-1", item_name="Fountain")],
    }


def _draft(name, label=None, modified="2026-10-05 12:40:00"):
    frappe.rows[RECORD].append(
        _Doc(
            name=name,
            project="PRJ-1",
            maintenance_contract="CON-1",
            serial_no=None,
            visit_label=label,
            docstatus=0,
            creation=modified,
            modified=modified,
        )
    )


class TestLogAVisit(unittest.TestCase):
    def test_a_winterization_draft_is_not_returned_as_the_regular_visit(self):
        _site()
        _draft("WINTER", "Winterization")
        name = visit.create_visit("CON-1")
        self.assertNotEqual(name, "WINTER")
        record = next(r for r in frappe.rows[RECORD] if r.name == name)
        self.assertFalse(record.visit_label)
        self.assertEqual(record.visit_date, TODAY)

    def test_every_labelled_draft_is_a_different_visit(self):
        for label in ("Seasonal Startup", "Extra Visit", "Chemistry Follow-Up", "Spring Planting"):
            _site()
            _draft("LABELLED", label)
            self.assertNotEqual(visit.create_visit("CON-1"), "LABELLED", label)

    def test_an_open_regular_draft_is_still_reused(self):
        _site()
        _draft("WINTER", "Winterization", modified="2026-10-05 12:40:00")
        _draft("REGULAR", None, modified="2026-10-01 07:00:00")
        self.assertEqual(visit.create_visit("CON-1"), "REGULAR")
        self.assertEqual(frappe.inserted, 0)

    def test_the_picker_only_names_a_regular_draft(self):
        _site()
        _draft("WINTER", "Winterization")
        (entry,) = visit.get_loggable_sites()
        self.assertIsNone(entry["open_draft"])
        _draft("REGULAR", "")
        (entry,) = visit.get_loggable_sites()
        self.assertEqual(entry["open_draft"], "REGULAR")


class TestUpcoming(unittest.TestCase):
    def test_a_seasonal_draft_does_not_hide_the_site(self):
        _site(next_visit="2026-11-02")
        _draft("WINTER", "Winterization")
        self.assertEqual([e["contract"] for e in visit.get_upcoming_visits()], ["CON-1"])

    def test_a_regular_or_extra_draft_still_does(self):
        for label in (None, "Extra Visit"):
            _site(next_visit="2026-11-02")
            _draft("QUEUED", label)
            self.assertEqual(visit.get_upcoming_visits(), [], label)


class TestKioskButton(unittest.TestCase):
    def test_myers_offers_a_regular_visit_and_names_the_winterization(self):
        _site()
        _draft("WINTER", "Winterization")
        ctx = kiosk.get_maintenance_context(project="PRJ-1")
        self.assertIsNone(ctx["draft"])
        self.assertIn("/app/sapphire-maintenance-record/new?", ctx["form_route"])
        self.assertEqual(
            [(f["label"], f["draft"]) for f in ctx["forms"]],
            [("New regular visit form", None), ("Open the Winterization form", "WINTER")],
        )
        self.assertEqual(ctx["forms"][0]["route"], ctx["form_route"])
        self.assertEqual(ctx["forms"][1]["route"], "/app/visit-wizard?record=WINTER")

    def test_the_regular_draft_leads_even_when_a_seasonal_one_is_newer(self):
        _site()
        _draft("REGULAR", None, modified="2026-10-01 07:00:00")
        _draft("WINTER", "Winterization", modified="2026-10-05 12:40:00")
        ctx = kiosk.get_maintenance_context(project="PRJ-1")
        self.assertEqual(ctx["draft"], "REGULAR")
        self.assertEqual(ctx["form_route"], "/app/visit-wizard?record=REGULAR")
        self.assertEqual(
            [f["label"] for f in ctx["forms"]],
            ["Open the regular visit form", "Open the Winterization form"],
        )

    def test_a_site_with_only_regular_work_keeps_its_one_button(self):
        _site()
        ctx = kiosk.get_maintenance_context(project="PRJ-1")
        self.assertEqual([f["label"] for f in ctx["forms"]], ["New maintenance form"])
        _draft("REGULAR")
        ctx = kiosk.get_maintenance_context(project="PRJ-1")
        self.assertEqual([f["label"] for f in ctx["forms"]], ["Open the draft form"])
        self.assertEqual(ctx["draft"], "REGULAR")


if __name__ == "__main__":
    unittest.main()
