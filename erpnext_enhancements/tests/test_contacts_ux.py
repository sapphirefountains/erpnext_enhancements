"""Integration tests for ``contacts_ux`` (Account <-> links sync, backfill
patch, directory refresh endpoint).

Run on a bench (``bench run-tests`` is broken under Python 3.14 — use the
bench-execute wrapper at the bottom):

    bench --site dev.localhost execute erpnext_enhancements.tests.test_contacts_ux.run

Fixtures are created with random-suffixed names and rolled back by
FrappeTestCase; ``frappe.enqueue`` is patched out around inserts that would
otherwise queue background jobs (Drive folders etc.) for rolled-back docs.
"""

import unittest
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_enhancements.patches.backfill_contact_custom_account import (
	execute as backfill_execute,
)


def _leaf(doctype):
	rows = frappe.get_all(doctype, filters={"is_group": 0}, limit=1, pluck="name")
	return rows[0] if rows else None


def _make_customer(label):
	doc = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": f"_Test CUX {label} {frappe.generate_hash(length=6)}",
			"customer_group": _leaf("Customer Group"),
			"territory": _leaf("Territory"),
		}
	)
	with patch.object(frappe, "enqueue"):
		doc.insert(ignore_permissions=True)
	return doc


def _make_contact(first_name, links=None, account=None):
	doc = frappe.get_doc(
		{
			"doctype": "Contact",
			"first_name": first_name,
			"last_name": frappe.generate_hash(length=6),
		}
	)
	for link_doctype, link_name in links or []:
		doc.append("links", {"link_doctype": link_doctype, "link_name": link_name})
	if account:
		doc.custom_account = account
	with patch.object(frappe, "enqueue"):
		doc.insert(ignore_permissions=True)
	return doc


def _customer_links(doc):
	return [l.link_name for l in doc.links if l.link_doctype == "Customer"]


class TestContactAccountSync(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.cust_a = _make_customer("A")
		cls.cust_b = _make_customer("B")
		cls.cust_c = _make_customer("C")

	def test_insert_with_account_only_creates_link_and_names_record(self):
		contact = _make_contact("Alice", account=self.cust_a.name)
		self.assertEqual(_customer_links(contact), [self.cust_a.name])
		self.assertEqual(contact.custom_account, self.cust_a.name)
		# The Customer row exists before naming, so core Contact.autoname
		# appends the "-Customer" suffix (links[0]).
		self.assertIn(self.cust_a.name, contact.name)

	def test_insert_with_link_only_backfills_account(self):
		contact = _make_contact("Bob", links=[("Customer", self.cust_a.name)])
		self.assertEqual(contact.custom_account, self.cust_a.name)

	def test_insert_with_no_links_keeps_account_empty(self):
		contact = _make_contact("Carol")
		self.assertFalse(contact.custom_account)
		self.assertEqual(contact.links, [])

	def test_account_edit_swaps_first_customer_row_in_place(self):
		contact = _make_contact(
			"Dave",
			links=[("Customer", self.cust_a.name), ("Customer", self.cust_b.name)],
		)
		contact.custom_account = self.cust_c.name
		contact.save(ignore_permissions=True)
		# First row swapped in place; the second Customer link untouched.
		self.assertEqual(_customer_links(contact), [self.cust_c.name, self.cust_b.name])
		self.assertEqual(contact.custom_account, self.cust_c.name)

	def test_account_edit_preserves_non_customer_links(self):
		contact = _make_contact(
			"Erin",
			links=[("Customer", self.cust_a.name), ("Contact", _make_contact("Ref").name)],
		)
		contact.custom_account = self.cust_b.name
		contact.save(ignore_permissions=True)
		self.assertEqual(_customer_links(contact), [self.cust_b.name])
		self.assertEqual(
			[l.link_doctype for l in contact.links], ["Customer", "Contact"]
		)

	def test_account_edit_to_already_linked_customer_drops_displaced_row(self):
		contact = _make_contact(
			"Frank",
			links=[("Customer", self.cust_a.name), ("Customer", self.cust_b.name)],
		)
		contact.custom_account = self.cust_b.name
		contact.save(ignore_permissions=True)
		self.assertEqual(_customer_links(contact), [self.cust_b.name])
		self.assertEqual(contact.custom_account, self.cust_b.name)

	def test_clear_account_removes_row_and_promotes_next_customer(self):
		contact = _make_contact(
			"Grace",
			links=[("Customer", self.cust_a.name), ("Customer", self.cust_b.name)],
		)
		contact.custom_account = None
		contact.save(ignore_permissions=True)
		# A's row removed; B promoted (the field mirrors the first Customer
		# link, so it can only stay blank when no Customer link remains).
		self.assertEqual(_customer_links(contact), [self.cust_b.name])
		self.assertEqual(contact.custom_account, self.cust_b.name)

	def test_clear_account_with_single_customer_clears_both(self):
		contact = _make_contact("Heidi", links=[("Customer", self.cust_a.name)])
		contact.custom_account = None
		contact.save(ignore_permissions=True)
		self.assertEqual(_customer_links(contact), [])
		self.assertFalse(contact.custom_account)

	def test_both_changed_in_one_save_links_win(self):
		contact = _make_contact("Ivan", links=[("Customer", self.cust_a.name)])
		row = next(l for l in contact.links if l.link_doctype == "Customer")
		row.link_name = self.cust_b.name
		contact.custom_account = self.cust_c.name  # stale rider — must lose
		contact.save(ignore_permissions=True)
		self.assertEqual(_customer_links(contact), [self.cust_b.name])
		self.assertEqual(contact.custom_account, self.cust_b.name)

	def test_grid_append_mirrors_into_account(self):
		contact = _make_contact("Judy")
		contact.append(
			"links", {"link_doctype": "Customer", "link_name": self.cust_b.name}
		)
		contact.save(ignore_permissions=True)
		self.assertEqual(contact.custom_account, self.cust_b.name)

	def test_no_change_save_is_stable(self):
		contact = _make_contact("Ken", links=[("Customer", self.cust_a.name)])
		links_before = [(l.link_doctype, l.link_name) for l in contact.links]
		contact.save(ignore_permissions=True)
		self.assertEqual(
			[(l.link_doctype, l.link_name) for l in contact.links], links_before
		)
		self.assertEqual(contact.custom_account, self.cust_a.name)

	def test_set_value_path_runs_sync(self):
		# List-view / report inline edits go through frappe.client.set_value ->
		# doc.save() -> validate; the sync must apply there too.
		contact = _make_contact("Leo", links=[("Customer", self.cust_a.name)])
		from frappe.client import set_value

		set_value("Contact", contact.name, "custom_account", self.cust_b.name)
		contact.reload()
		self.assertEqual(_customer_links(contact), [self.cust_b.name])
		self.assertEqual(contact.custom_account, self.cust_b.name)


class TestBackfillPatch(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.cust_a = _make_customer("PA")
		cls.cust_b = _make_customer("PB")

	def test_backfill_normalizes_stale_missing_and_orphaned(self):
		linked = _make_contact("Mia", links=[("Customer", self.cust_a.name)])
		stale = _make_contact("Nina", links=[("Customer", self.cust_a.name)])
		orphan = _make_contact("Omar")

		# Simulate pre-patch states behind the sync's back (db-level writes).
		frappe.db.set_value("Contact", linked.name, "custom_account", None)
		frappe.db.set_value("Contact", stale.name, "custom_account", self.cust_b.name)
		frappe.db.set_value("Contact", orphan.name, "custom_account", self.cust_b.name)

		backfill_execute()

		self.assertEqual(
			frappe.db.get_value("Contact", linked.name, "custom_account"), self.cust_a.name
		)
		self.assertEqual(
			frappe.db.get_value("Contact", stale.name, "custom_account"), self.cust_a.name
		)
		self.assertFalse(frappe.db.get_value("Contact", orphan.name, "custom_account"))

		# Idempotent: a second run changes nothing.
		modified = frappe.db.get_value("Contact", linked.name, "modified")
		backfill_execute()
		self.assertEqual(
			frappe.db.get_value("Contact", linked.name, "custom_account"), self.cust_a.name
		)
		self.assertEqual(frappe.db.get_value("Contact", linked.name, "modified"), modified)


class TestGetDirectoryOnload(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.customer = _make_customer("DIR")
		cls.contact = _make_contact("Pat", links=[("Customer", cls.customer.name)])

	def test_returns_contact_and_addr_lists(self):
		from erpnext_enhancements.contacts_ux import get_directory_onload

		data = get_directory_onload("Customer", self.customer.name)
		self.assertIn(self.contact.name, [c.get("name") for c in data["contact_list"]])
		self.assertIsInstance(data["addr_list"], list)

	def test_opportunity_merges_party_lists(self):
		from erpnext_enhancements.contacts_ux import get_directory_onload

		with patch.object(frappe, "enqueue"):
			opp = frappe.get_doc(
				{
					"doctype": "Opportunity",
					"opportunity_from": "Customer",
					"party_name": self.customer.name,
				}
			)
			# Site-required custom fields (opportunity name/value stream/owner)
			# are irrelevant to the directory merge under test.
			opp.flags.ignore_mandatory = True
			opp.insert(ignore_permissions=True)

		data = get_directory_onload("Opportunity", opp.name)
		# The party's contact must appear even though it isn't linked to the
		# Opportunity itself (Opportunity.onload parity).
		self.assertIn(self.contact.name, [c.get("name") for c in data["contact_list"]])

	def test_requires_read_permission_on_the_party(self):
		from erpnext_enhancements.contacts_ux import get_directory_onload

		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				get_directory_onload("Customer", self.customer.name)
		finally:
			frappe.set_user("Administrator")


def run():
	"""Bench-execute entry point (``bench run-tests`` is broken on Python 3.14)."""
	suite = unittest.TestSuite()
	loader = unittest.TestLoader()
	for case in (TestContactAccountSync, TestBackfillPatch, TestGetDirectoryOnload):
		suite.addTests(loader.loadTestsFromTestCase(case))
	runner = unittest.TextTestRunner(verbosity=2)
	result = runner.run(suite)
	frappe.db.rollback()
	if not result.wasSuccessful():
		raise Exception(
			f"contacts_ux tests failed: {len(result.failures)} failure(s), "
			f"{len(result.errors)} error(s)"
		)
	return f"OK ({result.testsRun} tests)"
