"""Bench-free tests for the one-off wrong-link cleanup (``crm_enhancements.party_link_cleanup``, v1.575.0).

The script removes the links the directory's old Link Existing fanned out (a county health inspector
filed under Harwood and Layton Construction because he was linked from a Harwood project). Its whole
value is in what it refuses to do, so that is what is tested here, against a fake database:

* a dry run (the default) saves nothing and rolls back;
* only the listed ``(doctype, name)`` links are removed: never a link that is not listed, never the
  record's own company or its job;
* ONE record that no longer matches the review (a listed link re-added under a new row, an audited
  row now holding another company because of a rename or merge, home or job link gone, renamed,
  deleted) stops the whole run before the first save, while an edit to anything else does not;
* a second run finds nothing to do;
* the Account of a supplier's person goes blank in the same save, by the real ``contacts_ux`` rule.

The ``frappe`` stub is installed in ``setUpModule`` and removed in ``tearDownModule``. Every name here
is invented.

Run: python -m unittest erpnext_enhancements.tests.test_party_link_cleanup -v
"""

import importlib
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

STUBBED = ("frappe", "frappe.utils")
OURS = ("erpnext_enhancements.crm_enhancements.party_link_cleanup", "erpnext_enhancements.contacts_ux")
_saved = {}
cleanup = None

DB = {}
LOG = {"saves": [], "rollbacks": 0}


class Row:
	def __init__(self, name, link_doctype, link_name):
		self.name = name
		self.link_doctype = link_doctype
		self.link_name = link_name
		self.link_title = link_name
		self.parentfield = "links"
		self.idx = 0


class Doc:
	"""A fresh copy of a stored record, the way frappe.get_doc returns one."""

	def __init__(self, doctype, stored):
		self.doctype = doctype
		self.name = stored["name"]
		self.modified = stored["modified"]
		self.modified_by = "someone@example.com"
		self.custom_account = stored.get("custom_account")
		self.user = stored.get("user")
		self.links = [Row(*r) for r in stored["links"]]
		self.flags = types.SimpleNamespace()
		self._doc_before_save = None

	def get_doc_before_save(self):
		return self._doc_before_save

	def get(self, key):
		return getattr(self, key, None)

	def append(self, table, row):
		new = Row(f"new-{len(self.links)}", row["link_doctype"], row["link_name"])
		self.links.append(new)
		return new

	def remove(self, row):
		self.links.remove(row)
		for i, r in enumerate(self.links, start=1):
			r.idx = i

	def save(self):
		# What validate does here: the Account rule, against the stored version.
		self._doc_before_save = Doc(self.doctype, DB[self.doctype][self.name])
		sys.modules["erpnext_enhancements.contacts_ux"].sync_contact_account_links(
			self
		) if self.doctype == "Contact" else None
		LOG["saves"].append((self.doctype, self.name, getattr(self.flags, "is_syncing", False)))
		DB[self.doctype][self.name] = {
			"name": self.name,
			"modified": "2026-10-07 09:00:00",
			"custom_account": self.custom_account,
			"user": self.user,
			"links": [(r.name, r.link_doctype, r.link_name) for r in self.links],
		}


def _install_stub():
	frappe = types.ModuleType("frappe")
	frappe.whitelist = lambda *a, **kw: lambda fn: fn
	frappe.local = types.SimpleNamespace(site="test.local")
	frappe.parse_json = lambda v: {"true": True, "false": False}.get(str(v).lower(), v)

	def exists(doctype, name):
		# MariaDB: case-insensitive, trailing spaces ignored. Returns the STORED name.
		for stored in DB.get(doctype, {}):
			if stored.lower().rstrip() == str(name).lower().rstrip():
				return stored
		return None

	def get_value(doctype, name, field, **kwargs):
		return f"{name} (title)"

	def rollback():
		LOG["rollbacks"] += 1

	frappe.db = types.SimpleNamespace(exists=exists, get_value=get_value, rollback=rollback)

	def get_doc(doctype, name):
		stored = exists(doctype, name)
		return Doc(doctype, DB[doctype][stored])

	frappe.get_doc = get_doc
	utils = types.ModuleType("frappe.utils")
	utils.get_datetime = lambda v: v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils


def setUpModule():
	global cleanup
	for name in STUBBED + OURS:
		_saved[name] = sys.modules.pop(name, None)
	_install_stub()
	importlib.import_module("erpnext_enhancements.contacts_ux")
	cleanup = importlib.import_module("erpnext_enhancements.crm_enhancements.party_link_cleanup")


def tearDownModule():
	for name in STUBBED + OURS:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]
	for name in OURS:
		parent_name, _, child = name.rpartition(".")
		parent = sys.modules.get(parent_name)
		if parent is None:
			continue
		if _saved.get(name) is not None:
			setattr(parent, child, _saved[name])
		elif hasattr(parent, child):
			delattr(parent, child)


OLD = "2026-09-04 10:38:53"

INSPECTOR = {
	"doctype": "Contact",
	"name": "Ned Inspector-County Health",
	"home": ("Supplier", "County Health"),
	"job": ("Project", "PRJ-0001"),
	"remove": (("Customer", "Harbor Homes", ("r3",)), ("Customer", "Layton Builders", ("r4",))),
}
ARCHITECT = {
	"doctype": "Contact",
	"name": "Ann Architect-Studio A",
	"home": ("Customer", "Studio A"),
	"job": ("Project", "PRJ-0002"),
	"remove": (("Customer", "Owner Co", ("a3",)),),
}
SITE = {
	"doctype": "Address",
	"name": "Lot 12-Other",
	"home": ("Customer", "Owner Co"),
	"job": ("Project", "PRJ-0002"),
	"remove": (("Supplier", "Stone Works", ("s3", "s3-old")),),
}


STAFF = {
	"doctype": "Contact",
	"name": "Sam Staff",
	"staff_user": "sam@example.com",
	"home": None,
	"job": None,
	"remove": (("Customer", "Sam Staff Residence", ("t1",)), ("Lead", "LEAD-TEST", ("t2",))),
}


def _seed():
	DB.clear()
	DB["Contact"] = {
		"Ned Inspector-County Health": {
			"name": "Ned Inspector-County Health",
			"modified": OLD,
			"custom_account": "Harbor Homes",
			"links": [
				("r1", "Supplier", "County Health"),
				("r2", "Project", "PRJ-0001"),
				("r3", "Customer", "Harbor Homes"),
				("r4", "Customer", "Layton Builders"),
				("r5", "Opportunity", "OPP-0009"),
			],
		},
		"Ann Architect-Studio A": {
			"name": "Ann Architect-Studio A",
			"modified": OLD,
			"custom_account": "Studio A",
			"links": [
				("a1", "Customer", "Studio A"),
				("a2", "Project", "PRJ-0002"),
				("a3", "Customer", "Owner Co"),
			],
		},
	}
	DB["Contact"]["Sam Staff"] = {
		"name": "Sam Staff",
		"modified": OLD,
		"user": "sam@example.com",
		"custom_account": "Sam Staff Residence",
		"links": [("t1", "Customer", "Sam Staff Residence"), ("t2", "Lead", "LEAD-TEST")],
	}
	DB["Address"] = {
		"Lot 12-Other": {
			"name": "Lot 12-Other",
			"modified": OLD,
			"links": [
				("s1", "Project", "PRJ-0002"),
				("s2", "Customer", "Owner Co"),
				("s3", "Supplier", "Stone Works"),
			],
		},
	}
	LOG["saves"] = []
	LOG["rollbacks"] = 0


def _links(doctype, name):
	return [(dt, nm) for _row, dt, nm in DB[doctype][name]["links"]]


class Base(unittest.TestCase):
	def setUp(self):
		_seed()
		self._real = cleanup.WRONG_LINKS
		cleanup.WRONG_LINKS = (INSPECTOR, ARCHITECT, SITE)
		cleanup.print = lambda *a, **k: None

	def tearDown(self):
		cleanup.WRONG_LINKS = self._real
		del cleanup.print


class DryRunTest(Base):
	def test_the_default_writes_nothing_and_rolls_back(self):
		summary = cleanup.run()
		self.assertEqual(LOG["saves"], [])
		self.assertGreaterEqual(LOG["rollbacks"], 1)
		self.assertTrue(summary["dry_run"])
		self.assertEqual(summary["links_to_remove"], 4)
		self.assertEqual(summary["to_change"], 3)

	def test_a_string_false_from_the_command_line_applies(self):
		cleanup.run(dry_run="false")
		self.assertEqual(len(LOG["saves"]), 3)


class ApplyTest(Base):
	def test_only_the_listed_links_go(self):
		cleanup.run(dry_run=False)
		self.assertEqual(
			_links("Contact", "Ned Inspector-County Health"),
			[("Supplier", "County Health"), ("Project", "PRJ-0001"), ("Opportunity", "OPP-0009")],
			"an unlisted link was removed, or a listed one survived",
		)
		self.assertEqual(
			_links("Contact", "Ann Architect-Studio A"), [("Customer", "Studio A"), ("Project", "PRJ-0002")]
		)
		self.assertEqual(
			_links("Address", "Lot 12-Other"), [("Project", "PRJ-0002"), ("Customer", "Owner Co")]
		)

	def test_one_save_per_record_without_the_upward_push(self):
		cleanup.run(dry_run=False)
		self.assertEqual(
			sorted(name for _dt, name, _sync in LOG["saves"]),
			sorted(["Ned Inspector-County Health", "Ann Architect-Studio A", "Lot 12-Other"]),
		)
		self.assertTrue(all(sync for _dt, _name, sync in LOG["saves"]), "saved without flags.is_syncing")

	def test_a_suppliers_person_loses_the_fanned_out_account_and_a_customers_keeps_theirs(self):
		cleanup.run(dry_run=False)
		self.assertIsNone(DB["Contact"]["Ned Inspector-County Health"]["custom_account"])
		self.assertEqual(DB["Contact"]["Ann Architect-Studio A"]["custom_account"], "Studio A")

	def test_the_dry_run_predicts_the_account(self):
		plan_lines = []
		cleanup.print = lambda *a, **k: plan_lines.append(" ".join(str(x) for x in a))
		cleanup.run()
		line = next(x for x in plan_lines if "Ned Inspector" in x)
		self.assertIn("Account 'Harbor Homes' -> None", line)

	def test_a_second_run_finds_nothing_to_do(self):
		cleanup.run(dry_run=False)
		LOG["saves"] = []
		summary = cleanup.run(dry_run=False)  # every record is now "edited after the audit"
		self.assertEqual(LOG["saves"], [])
		self.assertEqual(summary["already_clean"], 3)
		self.assertEqual(summary["blocked"], [], "a cleaned record reads as blocked")


class NotBlockedTest(Base):
	def test_an_edit_to_anything_else_on_the_record_does_not_stall_the_cleanup(self):
		"""The office fixing a primary contact first touches the record, not the audited rows."""
		DB["Contact"]["Ned Inspector-County Health"]["modified"] = "2026-10-07 08:00:00"
		cleanup.run(dry_run=False)
		self.assertNotIn(("Customer", "Harbor Homes"), _links("Contact", "Ned Inspector-County Health"))

	def test_any_of_a_links_audited_row_names_matches(self):
		"""Nathan Brooks' rows were rebuilt by an Unlink on the audit day: both names are audited."""
		links = DB["Address"]["Lot 12-Other"]["links"]
		links[2] = ("s3-old", "Supplier", "Stone Works")
		cleanup.run(dry_run=False)
		self.assertEqual(
			_links("Address", "Lot 12-Other"), [("Project", "PRJ-0002"), ("Customer", "Owner Co")]
		)


class StaffContactTest(Base):
	def setUp(self):
		super().setUp()
		cleanup.WRONG_LINKS = (STAFF,)

	def test_a_staff_contact_is_cleared_of_every_listed_link(self):
		cleanup.run(dry_run=False)
		self.assertEqual(_links("Contact", "Sam Staff"), [])
		self.assertIsNone(DB["Contact"]["Sam Staff"]["custom_account"])

	def test_a_contact_that_is_no_longer_the_staff_members_blocks(self):
		DB["Contact"]["Sam Staff"]["user"] = "someone.else@example.com"
		summary = cleanup.run(dry_run=False)
		self.assertTrue(summary["blocked"])
		self.assertEqual(LOG["saves"], [])


class BlockedTest(Base):
	"""Any one changed record stops everything, before the first save."""

	def _assert_nothing_written(self, summary):
		self.assertEqual(LOG["saves"], [])
		self.assertTrue(summary["blocked"])
		self.assertEqual(
			_links("Address", "Lot 12-Other")[-1],
			("Supplier", "Stone Works"),
			"a valid record was cleaned anyway",
		)

	def test_a_link_added_again_after_the_review(self):
		"""Same value, new row: someone chose it after the review, so it is theirs."""
		links = DB["Contact"]["Ann Architect-Studio A"]["links"]
		links[2] = ("a9-new", "Customer", "Owner Co")
		self._assert_nothing_written(cleanup.run(dry_run=False))

	def test_a_company_renamed_or_merged_since_the_review(self):
		"""rename_doc rewrites Dynamic Link rows in place: same row name, new value."""
		links = DB["Contact"]["Ned Inspector-County Health"]["links"]
		links[2] = ("r3", "Customer", "Harbor Homes LLC")
		self._assert_nothing_written(cleanup.run(dry_run=False))

	def test_a_missing_home_link(self):
		DB["Contact"]["Ned Inspector-County Health"]["links"].pop(0)
		self._assert_nothing_written(cleanup.run(dry_run=False))

	def test_a_missing_job_link(self):
		DB["Contact"]["Ann Architect-Studio A"]["links"].pop(1)
		self._assert_nothing_written(cleanup.run(dry_run=False))

	def test_a_deleted_record(self):
		del DB["Contact"]["Ann Architect-Studio A"]
		self._assert_nothing_written(cleanup.run(dry_run=False))

	def test_a_name_that_only_matches_case_insensitively(self):
		stored = DB["Contact"].pop("Ann Architect-Studio A")
		stored["name"] = "ann architect-studio a"
		DB["Contact"]["ann architect-studio a"] = stored
		self._assert_nothing_written(cleanup.run(dry_run=False))

	def test_an_entry_listing_its_own_home_for_removal(self):
		bad = dict(ARCHITECT, remove=(("Customer", "Studio A", ("a1",)),))
		cleanup.WRONG_LINKS = (INSPECTOR, bad, SITE)
		self._assert_nothing_written(cleanup.run(dry_run=False))


class RealListTest(unittest.TestCase):
	"""The shipped list itself."""

	def test_shape(self):
		names = [(e["doctype"], e["name"]) for e in self._real()]
		self.assertEqual(len(names), len(set(names)), "a record is listed twice")
		for e in self._real():
			with self.subTest(record=e["name"]):
				self.assertIn(e["doctype"], ("Contact", "Address"))
				removing = {(dt, nm) for dt, nm, _seen in e["remove"]}
				for _dt, _nm, seen in e["remove"]:
					self.assertTrue(
						seen and all(isinstance(n, str) and len(n) == 10 for n in seen),
						"an audited row name is missing",
					)
				if e.get("staff_user"):
					# A staff member's own contact: no company or job survives, the user guards it.
					self.assertIsNone(e["home"])
					self.assertIsNone(e["job"])
					self.assertEqual(e["doctype"], "Contact")
					continue
				self.assertIn(e["home"][0], ("Customer", "Supplier"))
				self.assertIn(e["job"][0], ("Project", "Opportunity"))
				self.assertNotIn(tuple(e["home"]), removing)
				self.assertNotIn(tuple(e["job"]), removing)
				self.assertTrue(
					all(dt in ("Customer", "Supplier") for dt, _nm in removing),
					"only company links are removed",
				)

	def test_the_office_items_are_not_in_the_automatic_list(self):
		listed = {e["name"] for e in self._real()}
		for kept in (
			"Jaxon Kier-Lowe Property Group",
			"Jobsite-Permanent",
			"Stenmark-Billing",
		):
			self.assertNotIn(kept, listed)

	@staticmethod
	def _real():
		return sys.modules["erpnext_enhancements.crm_enhancements.party_link_cleanup"].WRONG_LINKS


if __name__ == "__main__":
	unittest.main()
