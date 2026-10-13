# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests: follow-ups and parts needed, from the visit to an Opportunity (v1.600.0).

D3 of Phase D (TASK-2026-02488). Lisa, 2026-10-12: "say Austin talked to someone and told them
you need a new manifold, and then he adds it to the form with a follow up and then I can get
it over to Brian and follow it through until the new part is installed and invoiced."

* the visit tidies its follow-ups and keeps each on a water feature it covers;
* approval makes one Maintenance Follow-Up per row, linked back, and only once;
* the Opportunity is for the customer, owned by the setting's person (Brian) or the clicker,
  in the Service value stream, with the part as its item, made once, by someone allowed to;
* the follow-up's title is its first line, shortened.

Stubs a minimal ``frappe`` in ``setUpModule``; runs the real ``follow_ups`` and the
Maintenance Follow-Up controller.

Run: python -m unittest erpnext_enhancements.tests.test_maintenance_follow_ups
"""

import ast
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
MODULE = "erpnext_enhancements.sapphire_maintenance.follow_ups"
CONTROLLER = "erpnext_enhancements.sapphire_maintenance.doctype.maintenance_follow_up.maintenance_follow_up"
STUBBED = ("frappe", "frappe.utils", "frappe.model", "frappe.model.document", MODULE, CONTROLLER)

follow_ups = None
controller = None
_ORIGINAL_GET_VALUE = []
STATE = {}
_saved = {}


class _Dict(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class _Thrown(Exception):
	pass


class _PermissionError(Exception):
	pass


class _Doc(_Dict):
	"""A document being inserted: Maintenance Follow-Up or Opportunity."""

	def insert(self, ignore_permissions=False):
		number = len(STATE["inserted"]) + 1
		self.name = {"Maintenance Follow-Up": f"MFU-{number}", "Opportunity": f"CRM-OPP-{number}"}[
			self.doctype
		]
		self.ignore_permissions = ignore_permissions
		STATE["inserted"].append(self)
		STATE["docs"][(self.doctype, self.name)] = self
		return self

	def add_comment(self, kind, text):
		STATE["comments"].append((self.name, kind, text))

	def db_set(self, field, value):
		self[field] = value
		STATE["db_set"].append((self.name, field, value))

	def save(self):
		STATE["saved"].append(
			(self.name, self.status, bool((self.get("flags") or {}).get("ignore_permissions")))
		)


class _Flags(_Dict):
	pass


def _throw(message, exc=None, title=None):
	raise (exc or _Thrown)(message)


def setUpModule():
	global follow_ups, controller
	for name in STUBBED:
		_saved[name] = sys.modules.pop(name, None)
	import erpnext_enhancements.sapphire_maintenance as package

	if "follow_ups" in vars(package):
		delattr(package, "follow_ups")

	frappe = types.ModuleType("frappe")
	frappe.__dict__["_"] = lambda text, *a, **k: text
	frappe.PermissionError = _PermissionError
	frappe.throw = _throw
	frappe.get_doc = lambda arg, name=None: _Doc(arg) if isinstance(arg, dict) else STATE["docs"][(arg, name)]
	frappe.has_permission = lambda doctype, ptype="read", *a, **k: STATE["can_create"]
	frappe.session = types.SimpleNamespace(user="lisa@example.com")
	frappe.defaults = types.SimpleNamespace(get_user_default=lambda key: "Sapphire Fountains, LLC")

	def _get_value(doctype, name, field):
		if (doctype, field) == ("Maintenance Follow-Up", "visit"):
			doc = STATE["docs"].get((doctype, name))
			return doc.visit if doc else None
		assert (doctype, field) == ("Project", "project_name")
		return {"PRJ-00228": "Hardware Apartments Downtown Salt Lake City"}.get(name)

	def _exists(doctype, filters):
		if doctype == "File":
			assert filters["attached_to_doctype"] == "Sapphire Maintenance Record"
			return (filters["file_url"], filters["attached_to_name"]) in STATE["files"]
		return (doctype, filters) in STATE["docs"]

	def _get_all(doctype, filters=None, pluck=None, **kwargs):
		assert doctype == "Maintenance Follow-Up" and pluck == "name"
		wanted = set(filters["status"][1])
		return [
			doc.name
			for (dt, _name), doc in STATE["docs"].items()
			if dt == doctype and doc.visit == filters["visit"] and doc.status in wanted
		]

	def _get_single_value(doctype, field):
		assert (doctype, field) == ("ERPNext Enhancements Settings", "follow_up_opportunity_owner")
		return STATE["owner_setting"]

	def _set_value(doctype, name, field, value, update_modified=True):
		STATE["set_values"].append((doctype, name, field, value, update_modified))

	frappe.db = types.SimpleNamespace(
		get_value=_get_value,
		get_single_value=_get_single_value,
		set_value=_set_value,
		exists=_exists,
	)
	frappe.get_all = _get_all
	utils = types.ModuleType("frappe.utils")
	utils.flt = lambda v, *a: float(v or 0)
	utils.getdate = lambda v=None: str(v)[:10] if v else "2026-10-12"
	utils.nowdate = lambda: "2026-10-12"
	utils.escape_html = lambda text: str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
	frappe.utils = utils
	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = _Dict
	model.document = document
	frappe.model = model
	sys.modules.update(
		{"frappe": frappe, "frappe.utils": utils, "frappe.model": model, "frappe.model.document": document}
	)
	from erpnext_enhancements.sapphire_maintenance import follow_ups as module
	from erpnext_enhancements.sapphire_maintenance.doctype.maintenance_follow_up import (
		maintenance_follow_up,
	)

	follow_ups = module
	controller = maintenance_follow_up
	_ORIGINAL_GET_VALUE.append(frappe.db.get_value)


def tearDownModule():
	import erpnext_enhancements.sapphire_maintenance as package
	import erpnext_enhancements.sapphire_maintenance.doctype.maintenance_follow_up as tracked

	for owner, attr in ((package, "follow_ups"), (tracked, "maintenance_follow_up")):
		if attr in vars(owner):
			delattr(owner, attr)
	for name in STUBBED:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


def _reset():
	STATE.clear()
	STATE.update(
		{
			"inserted": [],
			"docs": {},
			"comments": [],
			"db_set": [],
			"set_values": [],
			"can_create": True,
			"owner_setting": "brian.morisseau@example.com",
			"files": set(),
			"saved": [],
		}
	)


def _visit(rows, **extra):
	visit = _Dict(
		name="PRJ-00228-2026-V001",
		customer="CUST-HARDWARE",
		project="PRJ-00228",
		serial_no=None,
		technician="austin@example.com",
		owner="Administrator",
		visit_date="2026-10-19",
		scheduled_visit_date="2026-10-19",
		chemistry_readings=[_Dict(serial_no="SN-EAST"), _Dict(serial_no="SN-WEST")],
		cleaning_tasks=[],
		maintenance_results=[],
		consumables=[],
		follow_ups=[_Dict(r) for r in rows],
	)
	visit.update(extra)
	return visit


def _tracked(**extra):
	doc = _Doc(
		doctype="Maintenance Follow-Up",
		name="MFU-9",
		title="New manifold",
		description="New manifold for the <east> basin",
		customer="CUST-HARDWARE",
		project="PRJ-00228",
		visit="PRJ-00228-2026-V001",
		told="Property manager",
		item=None,
		qty=None,
		opportunity=None,
		status="Open",
		flags=_Flags(),
	)
	doc.update(extra)
	STATE["docs"][("Maintenance Follow-Up", doc.name)] = doc
	return doc


class TestOnTheVisit(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_rows_are_tidied(self):
		visit = _visit([{"description": "  New manifold \n", "told": "   ", "serial_no": "SN-EAST"}])
		follow_ups.check_follow_ups(visit)
		self.assertEqual((visit.follow_ups[0].description, visit.follow_ups[0].told), ("New manifold", None))

	def test_a_follow_up_stays_on_a_feature_the_visit_covers(self):
		follow_ups.check_follow_ups(_visit([{"description": "x", "serial_no": None}]))  # the whole site
		with self.assertRaises(_Thrown):
			follow_ups.check_follow_ups(_visit([{"description": "x", "serial_no": "SN-ELSEWHERE", "idx": 1}]))
		with self.assertRaises(_Thrown):
			follow_ups.check_follow_ups(_visit([{"description": "x", "qty": -1}]))


class TestOnApproval(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_each_row_becomes_a_tracked_follow_up_linked_back(self):
		visit = _visit(
			[
				{
					"name": "r1",
					"description": "New manifold",
					"item": "MANIFOLD-2",
					"qty": 1,
					"told": "PM",
					"serial_no": "SN-EAST",
				},
				{"name": "r2", "description": "Come back for the lights"},
			]
		)
		follow_ups.on_visit_submit(visit)
		first, second = STATE["inserted"]
		self.assertEqual(
			{
				k: first[k]
				for k in (
					"status",
					"description",
					"item",
					"qty",
					"told",
					"customer",
					"project",
					"serial_no",
					"visit",
					"raised_by",
					"raised_on",
				)
			},
			{
				"status": "Open",
				"description": "New manifold",
				"item": "MANIFOLD-2",
				"qty": 1,
				"told": "PM",
				"customer": "CUST-HARDWARE",
				"project": "PRJ-00228",
				"serial_no": "SN-EAST",
				"visit": "PRJ-00228-2026-V001",
				"raised_by": "austin@example.com",
				"raised_on": "2026-10-19",
			},
		)
		self.assertTrue(first.ignore_permissions, "the system makes them, whoever approves")
		self.assertEqual(second.serial_no, None)
		self.assertEqual(
			STATE["set_values"],
			[
				("Sapphire Visit Follow-Up", "r1", "follow_up", "MFU-1", False),
				("Sapphire Visit Follow-Up", "r2", "follow_up", "MFU-2", False),
			],
		)

	def test_a_row_already_tracked_is_not_tracked_twice(self):
		_tracked(name="MFU-OLD", visit="PRJ-00228-2026-V001")
		follow_ups.on_visit_submit(_visit([{"name": "r1", "description": "x", "follow_up": "MFU-OLD"}]))
		self.assertEqual(STATE["inserted"], [])

	def test_an_amended_visit_tracks_its_rows_again(self):
		"""An amendment copies the canceled original's links; its follow-ups were canceled with it."""
		_tracked(name="MFU-OLD", visit="PRJ-00228-2026-V001", status="Canceled")
		visit = _visit(
			[{"name": "r1", "description": "x", "follow_up": "MFU-OLD"}], name="PRJ-00228-2026-V001-1"
		)
		follow_ups.on_visit_submit(visit)
		(new,) = STATE["inserted"]
		self.assertEqual((new.visit, visit.follow_ups[0].follow_up), ("PRJ-00228-2026-V001-1", new.name))

	def test_a_photo_is_copied_only_when_it_is_attached_to_the_visit(self):
		"""The follow-up attaches a copy of the File it names, readable by technicians and sales:
		a URL a phone sent naming some other private file must not reach it."""
		STATE["files"].add(("/private/files/manifold.jpg", "PRJ-00228-2026-V001"))
		STATE["files"].add(("/private/files/payroll.png", "SAL-OTHER"))
		rows = [
			{"name": "r1", "description": "a", "photo": "/private/files/manifold.jpg"},
			{"name": "r2", "description": "b", "photo": "/private/files/payroll.png"},
			{"name": "r3", "description": "c", "photo": "https://example.test/x.jpg"},
			{"name": "r4", "description": "d", "photo": None},
		]
		follow_ups.on_visit_submit(_visit(rows))
		self.assertEqual(
			[doc.photo for doc in STATE["inserted"]], ["/private/files/manifold.jpg", None, None, None]
		)

	def test_it_is_wired_to_approval_and_cancel(self):
		# Read hooks.doc_events as data, not as text: an event's handler may be a string or a list,
		# and another feature adding a handler (the calendar sync, v1.604.0, turned on_cancel into
		# a list) must not break this.
		tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
		doc_events = next(
			ast.literal_eval(node.value)
			for node in tree.body
			if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "doc_events" for t in node.targets)
		)
		events = doc_events["Sapphire Maintenance Record"]

		def handlers(event):
			value = events.get(event) or []
			return value if isinstance(value, list) else [value]

		self.assertIn("erpnext_enhancements.sapphire_maintenance.follow_ups.on_visit_submit", handlers("on_submit"))
		self.assertIn("erpnext_enhancements.sapphire_maintenance.follow_ups.on_visit_cancel", handlers("on_cancel"))

	def test_cancel_cancels_the_ones_still_being_worked(self):
		_tracked(name="MFU-A", visit="PRJ-00228-2026-V001", status="Ordered")
		_tracked(name="MFU-B", visit="PRJ-00228-2026-V001", status="Invoiced")
		_tracked(name="MFU-C", visit="ANOTHER", status="Open")
		follow_ups.on_visit_cancel(_visit([]))
		self.assertEqual(STATE["saved"], [("MFU-A", "Canceled", True)])
		self.assertEqual(
			STATE["comments"], [("MFU-A", "Comment", "Canceled with its visit PRJ-00228-2026-V001.")]
		)


class TestTheOpportunity(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_for_the_customer_owned_by_the_setting_in_service_with_the_part(self):
		_tracked(item="MANIFOLD-2", qty=2)
		name = follow_ups.make_opportunity("MFU-9")
		opportunity = STATE["docs"][("Opportunity", name)]
		self.assertEqual(
			{
				k: opportunity[k]
				for k in (
					"opportunity_from",
					"party_name",
					"company",
					"status",
					"opportunity_owner",
					"custom_opportunity_name",
				)
			},
			{
				"opportunity_from": "Customer",
				"party_name": "CUST-HARDWARE",
				"company": "Sapphire Fountains, LLC",
				"status": "Qualification",
				"opportunity_owner": "brian.morisseau@example.com",
				"custom_opportunity_name": "Hardware Apartments Downtown Salt Lake City: New manifold",
			},
		)
		self.assertEqual(opportunity.custom_value_stream, [{"value_stream": "Service"}])
		self.assertEqual(opportunity["items"], [{"item_code": "MANIFOLD-2", "qty": 2.0, "rate": 0}])
		self.assertFalse(opportunity.ignore_permissions, "made as the person clicking")
		self.assertEqual(STATE["docs"][("Maintenance Follow-Up", "MFU-9")].opportunity, name)

	def test_what_was_found_goes_on_its_timeline_escaped(self):
		_tracked()
		name = follow_ups.make_opportunity("MFU-9")
		((who, kind, text),) = STATE["comments"]
		self.assertEqual((who, kind), (name, "Comment"))
		self.assertIn("New manifold for the &lt;east&gt; basin", text)
		self.assertIn("Told on site: Property manager", text)
		self.assertIn("PRJ-00228-2026-V001", text)

	def test_no_owner_set_means_the_person_clicking(self):
		STATE["owner_setting"] = None
		_tracked()
		name = follow_ups.make_opportunity("MFU-9")
		self.assertEqual(STATE["docs"][("Opportunity", name)].opportunity_owner, "lisa@example.com")

	def test_a_long_project_name_is_cut_to_the_field(self):
		_tracked(title="x" * 80, project="PRJ-LONG")
		sys.modules["frappe"].db.get_value = lambda doctype, name, field: "P" * 120
		try:
			name = follow_ups.make_opportunity("MFU-9")
		finally:
			sys.modules["frappe"].db.get_value = _ORIGINAL_GET_VALUE[0]
		self.assertEqual(len(STATE["docs"][("Opportunity", name)].custom_opportunity_name), 140)

	def test_made_once(self):
		_tracked()
		first = follow_ups.make_opportunity("MFU-9")
		self.assertEqual(follow_ups.make_opportunity("MFU-9"), first)
		self.assertEqual(len(STATE["inserted"]), 1)

	def test_refused_without_create_permission_or_a_customer(self):
		_tracked()
		STATE["can_create"] = False
		with self.assertRaises(_PermissionError):
			follow_ups.make_opportunity("MFU-9")
		STATE["can_create"] = True
		_tracked(customer=None)
		with self.assertRaises(_Thrown):
			follow_ups.make_opportunity("MFU-9")
		self.assertEqual(STATE["inserted"], [])


class TestTheTitle(unittest.TestCase):
	def test_first_line_shortened(self):
		doc = controller.MaintenanceFollowUp(description="New manifold\nfor the east basin")
		doc.validate()
		self.assertEqual(doc.title, "New manifold")
		doc = controller.MaintenanceFollowUp(description="x" * 200)
		doc.validate()
		self.assertEqual(len(doc.title), controller.TITLE_LENGTH)
		self.assertTrue(doc.title.endswith("…"))


if __name__ == "__main__":
	unittest.main()
