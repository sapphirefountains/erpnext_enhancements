"""Bench-free tests for the Contact "Account" rule (``contacts_ux.sync_contact_account_links``, v1.575.0).

The Account is the Customer a person WORKS FOR. Until v1.575.0 it mirrored the first Customer link
row on every save, so a supplier's employee, who has no Customer row of their own, took the first
Customer the directory fanned them out to: on 2026-10-06 eight Supplier contacts showed a customer
as their Account (five of them "Harwood", from one project), and clearing the field promoted the
next fanned-out Customer (Layton Construction) instead of clearing it.

The rule now:

* a stored Account that is still linked is kept;
* a new Account is adopted only from a Customer row ADDED in this save that is also the contact's
  first organisation link (no Supplier row before it);
* clearing the Account removes that one row and promotes nothing.

The integration suite ``tests/test_contacts_ux.py`` covers the same function on a bench; this one
runs in CI. The ``frappe`` stub is installed in ``setUpModule`` and removed in ``tearDownModule``.
Every name here is invented.

Run: python -m unittest erpnext_enhancements.tests.test_contact_account_rule -v
"""

import importlib
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

STUBBED = ("frappe",)
OURS = ("erpnext_enhancements.contacts_ux",)
_saved = {}
contacts_ux = None

#: user_type per User, for the Address guard.
USER_TYPES = {
	"staff@example.com": "System User",
	"Administrator": "System User",
	"buyer@example.com": "Website User",
}


class Row:
	def __init__(self, link_doctype, link_name):
		self.link_doctype = link_doctype
		self.link_name = link_name
		self.link_title = link_name
		self.idx = 0

	def __repr__(self):
		return f"{self.link_doctype}:{self.link_name}"


class Contact:
	"""Just enough of a Document: links, custom_account, append, get_doc_before_save."""

	def __init__(self, links=(), account=None, before=None):
		self.links = [Row(dt, nm) for dt, nm in links]
		self.custom_account = account
		self._before = before

	def append(self, table, row):
		assert table == "links"
		new = Row(row["link_doctype"], row["link_name"])
		self.links.append(new)
		return new

	def get_doc_before_save(self):
		return self._before

	def snapshot(self):
		"""The saved state this doc would be compared with on its next save."""
		return Contact([(r.link_doctype, r.link_name) for r in self.links], self.custom_account)

	def pairs(self):
		return [(r.link_doctype, r.link_name) for r in self.links]


def _install_stub():
	frappe = types.ModuleType("frappe")
	frappe.whitelist = lambda *a, **kw: lambda fn: fn

	def get_value(doctype, name, field, **kwargs):
		if doctype == "User":
			return USER_TYPES.get(name)
		return f"{name} (title)"

	frappe.db = types.SimpleNamespace(get_value=get_value)
	sys.modules["frappe"] = frappe


def setUpModule():
	global contacts_ux
	for name in STUBBED + OURS:
		_saved[name] = sys.modules.pop(name, None)
	_install_stub()
	contacts_ux = importlib.import_module("erpnext_enhancements.contacts_ux")


def tearDownModule():
	for name in STUBBED + OURS:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]
	parent = sys.modules.get("erpnext_enhancements")
	if parent is not None:
		if _saved.get("erpnext_enhancements.contacts_ux") is not None:
			parent.contacts_ux = _saved["erpnext_enhancements.contacts_ux"]
		elif hasattr(parent, "contacts_ux"):
			delattr(parent, "contacts_ux")


def run(doc):
	contacts_ux.sync_contact_account_links(doc)
	return doc


def saved(links, account):
	"""A contact as it is stored, ready to be edited and saved again."""
	return Contact(links, account, before=Contact(links, account))


class InsertTest(unittest.TestCase):
	def test_a_customers_own_person_gets_the_account(self):
		doc = run(Contact([("Customer", "Kier"), ("Project", "PRJ-1")]))
		self.assertEqual(doc.custom_account, "Kier")

	def test_a_supplier_first_contact_gets_no_customer_account(self):
		doc = run(Contact([("Supplier", "County Health"), ("Project", "PRJ-1"), ("Customer", "Harwood")]))
		self.assertIsNone(doc.custom_account)

	def test_an_explicit_account_is_inserted_first(self):
		doc = run(Contact([("Project", "PRJ-1")], account="Kier"))
		self.assertEqual(doc.pairs(), [("Customer", "Kier"), ("Project", "PRJ-1")])
		self.assertEqual(doc.custom_account, "Kier")

	def test_an_explicit_account_that_is_linked_is_kept_even_behind_a_supplier(self):
		"""The same company set up as both Supplier and Customer: the caller said which."""
		doc = run(Contact([("Supplier", "Conely"), ("Customer", "Conely")], account="Conely"))
		self.assertEqual(doc.custom_account, "Conely")

	def test_no_links_no_account(self):
		doc = run(Contact())
		self.assertIsNone(doc.custom_account)
		self.assertEqual(doc.pairs(), [])

	def test_a_lead_row_does_not_block_the_converted_customer(self):
		doc = run(Contact([("Lead", "LEAD-1"), ("Customer", "Smith Residence")]))
		self.assertEqual(doc.custom_account, "Smith Residence")


class FanOutTest(unittest.TestCase):
	"""The shapes the 2026-10-06 audit found on prod."""

	def test_a_fanned_out_customer_never_becomes_a_suppliers_account(self):
		doc = saved([("Supplier", "County Health")], None)
		doc.append("links", {"link_doctype": "Project", "link_name": "PRJ-703"})
		doc.append("links", {"link_doctype": "Customer", "link_name": "Harwood"})
		doc.append("links", {"link_doctype": "Customer", "link_name": "Layton"})
		run(doc)
		self.assertIsNone(doc.custom_account)
		self.assertEqual(len(doc.links), 4, "the rule must never add or drop links on a grid change")

	def test_a_fanned_out_customer_never_replaces_a_customers_account(self):
		doc = saved([("Customer", "Kier"), ("Project", "PRJ-699")], "Kier")
		doc.append("links", {"link_doctype": "Customer", "link_name": "Lowe"})
		self.assertEqual(run(doc).custom_account, "Kier")

	def test_removing_the_wrong_customer_clears_a_suppliers_account(self):
		"""The cleanup: Nathan Brooks' Account is Harwood today; removing the row must blank it."""
		before = Contact(
			[("Supplier", "County Health"), ("Project", "PRJ-703"), ("Customer", "Harwood")], "Harwood"
		)
		doc = Contact([("Supplier", "County Health"), ("Project", "PRJ-703")], "Harwood", before=before)
		self.assertIsNone(run(doc).custom_account)

	def test_removing_both_wrong_customers_at_once_clears_it_without_passing_through_the_second(self):
		before = Contact(
			[
				("Supplier", "Watershape"),
				("Project", "PRJ-703"),
				("Customer", "Harwood"),
				("Customer", "Layton"),
			],
			"Harwood",
		)
		doc = Contact([("Supplier", "Watershape"), ("Project", "PRJ-703")], "Harwood", before=before)
		self.assertIsNone(run(doc).custom_account)

	def test_removing_the_wrong_customer_keeps_a_customers_own_account(self):
		before = Contact([("Customer", "Kier"), ("Project", "PRJ-699"), ("Customer", "Lowe")], "Kier")
		doc = Contact([("Customer", "Kier"), ("Project", "PRJ-699")], "Kier", before=before)
		self.assertEqual(run(doc).custom_account, "Kier")

	def test_removing_one_of_two_wrong_customers_does_not_promote_the_other(self):
		"""If the cleanup ever removed Harwood before Layton, Layton must not become the Account."""
		before = Contact(
			[
				("Supplier", "Watershape"),
				("Project", "PRJ-703"),
				("Customer", "Harwood"),
				("Customer", "Layton"),
			],
			"Harwood",
		)
		doc = Contact(
			[("Supplier", "Watershape"), ("Project", "PRJ-703"), ("Customer", "Layton")],
			"Harwood",
			before=before,
		)
		self.assertIsNone(run(doc).custom_account)


class EditTest(unittest.TestCase):
	def test_an_ordinary_save_changes_nothing(self):
		doc = saved([("Supplier", "County Health"), ("Customer", "Harwood")], "Harwood")
		run(doc)
		self.assertEqual(doc.custom_account, "Harwood", "a routine save must not rewrite existing data")
		self.assertEqual(doc.pairs(), [("Supplier", "County Health"), ("Customer", "Harwood")])

	def test_an_orphaned_account_is_cleared_on_save(self):
		doc = saved([("Supplier", "County Health")], "Harwood")
		self.assertIsNone(run(doc).custom_account)

	def test_clearing_the_account_removes_that_row_and_promotes_nothing(self):
		doc = saved([("Customer", "A"), ("Customer", "B")], "A")
		doc.custom_account = None
		run(doc)
		self.assertEqual(doc.pairs(), [("Customer", "B")])
		self.assertIsNone(doc.custom_account)
		# ...and the NEXT save does not promote B either.
		again = Contact(doc.pairs(), doc.custom_account, before=doc.snapshot())
		self.assertIsNone(run(again).custom_account)

	def test_clearing_a_fanned_out_account_does_not_hand_it_to_the_next_customer(self):
		"""Today on prod: clearing Harwood on four contacts would have made Layton their Account."""
		doc = saved([("Supplier", "Watershape"), ("Customer", "Harwood"), ("Customer", "Layton")], "Harwood")
		doc.custom_account = ""
		run(doc)
		self.assertEqual(doc.pairs(), [("Supplier", "Watershape"), ("Customer", "Layton")])
		self.assertIsNone(doc.custom_account)

	def test_changing_the_account_swaps_the_old_accounts_row_in_place(self):
		doc = saved([("Customer", "A"), ("Customer", "B"), ("Project", "PRJ-1")], "A")
		doc.custom_account = "C"
		run(doc)
		self.assertEqual(doc.pairs(), [("Customer", "C"), ("Customer", "B"), ("Project", "PRJ-1")])
		self.assertEqual(doc.custom_account, "C")

	def test_changing_to_an_already_linked_customer_drops_the_old_accounts_row(self):
		doc = saved([("Customer", "A"), ("Customer", "B")], "A")
		doc.custom_account = "B"
		run(doc)
		self.assertEqual(doc.pairs(), [("Customer", "B")])
		self.assertEqual(doc.custom_account, "B")

	def test_setting_an_account_with_none_before_never_overwrites_another_customer_row(self):
		"""The old code swapped the FIRST Customer row whoever it was; with no Account there is no
		row of the Account's to swap, so the new one is added and the existing link is untouched."""
		doc = saved([("Supplier", "Watershape"), ("Customer", "Harwood")], None)
		doc.custom_account = "Kier"
		run(doc)
		self.assertEqual(
			doc.pairs(), [("Customer", "Kier"), ("Supplier", "Watershape"), ("Customer", "Harwood")]
		)
		self.assertEqual(doc.custom_account, "Kier")

	def test_when_grid_and_field_both_change_the_grid_wins(self):
		before = Contact([("Customer", "A")], "A")
		doc = Contact([("Customer", "B")], "C", before=before)
		self.assertEqual(run(doc).custom_account, "B")

	def test_deleting_one_customer_row_and_picking_the_other_in_one_save_takes_the_pick(self):
		"""Tidying a duplicate in the Desk: drop "Big D", pick "Big-D" as the Account, one save."""
		before = Contact([("Customer", "Big D"), ("Customer", "Big-D"), ("Opportunity", "OPP-1")], "Big D")
		doc = Contact([("Customer", "Big-D"), ("Opportunity", "OPP-1")], "Big-D", before=before)
		self.assertEqual(run(doc).custom_account, "Big-D")

	def test_emptying_the_account_in_the_same_save_as_a_grid_edit_is_a_clear(self):
		"""Delete the Layton row and empty "Harwood" in one save: both go, nothing is promoted."""
		before = Contact(
			[("Supplier", "Watershape"), ("Customer", "Harwood"), ("Customer", "Layton")], "Harwood"
		)
		doc = Contact([("Supplier", "Watershape"), ("Customer", "Harwood")], "", before=before)
		run(doc)
		self.assertEqual(doc.pairs(), [("Supplier", "Watershape")])
		self.assertIsNone(doc.custom_account)

	def test_a_picked_account_behind_a_supplier_is_honoured_when_it_is_linked(self):
		before = Contact([("Supplier", "Fusion"), ("Project", "PRJ-1")], None)
		doc = Contact(
			[("Supplier", "Fusion"), ("Project", "PRJ-1"), ("Customer", "Fusion")], "Fusion", before=before
		)
		self.assertEqual(run(doc).custom_account, "Fusion")

	def test_a_picked_account_that_is_not_linked_still_loses_to_the_grid(self):
		before = Contact([("Supplier", "Watershape")], None)
		doc = Contact([("Supplier", "Watershape"), ("Customer", "Harwood")], "Layton", before=before)
		self.assertIsNone(run(doc).custom_account)

	def test_a_customer_added_from_its_own_form_becomes_the_account_of_a_project_only_contact(self):
		doc = saved([("Project", "PRJ-1")], None)
		doc.append("links", {"link_doctype": "Customer", "link_name": "Kier"})
		self.assertEqual(run(doc).custom_account, "Kier")


class AddressLinkGuardTest(unittest.TestCase):
	"""frappe's Address.link_address files a LINKLESS address under its creator's own Contact
	links. Staff creators must not get that fallback; portal users keep it."""

	def _address(self, owner, links=()):
		calls = []

		class CoreAddress:
			"""Stands in for frappe's Address (and ERPNext's extension of it) behind the guard."""

			def link_address(self):
				calls.append(self.owner)
				self.links.append(("Lead", "LEAD-STAFF"))
				return True

			def get(self, key):
				return getattr(self, key, None)

		Extended = type("ExtendedAddress", (contacts_ux.AddressLinkGuard, CoreAddress), {})
		doc = Extended()
		doc.owner = owner
		doc.links = list(links)
		return doc, calls

	def test_a_staff_users_linkless_address_stays_unlinked(self):
		doc, calls = self._address("staff@example.com")
		self.assertFalse(doc.link_address())
		self.assertEqual(doc.links, [], "filed under the creator's own Contact links")
		self.assertEqual(calls, [])

	def test_administrator_counts_as_staff(self):
		doc, _calls = self._address("Administrator")
		doc.link_address()
		self.assertEqual(doc.links, [])

	def test_a_portal_users_address_keeps_frappes_behaviour(self):
		doc, calls = self._address("buyer@example.com")
		self.assertTrue(doc.link_address())
		self.assertEqual(calls, ["buyer@example.com"])

	def test_an_unknown_owner_keeps_frappes_behaviour(self):
		doc, calls = self._address("ghost@example.com")
		doc.link_address()
		self.assertEqual(calls, ["ghost@example.com"])

	def test_an_address_with_links_is_left_to_frappe(self):
		doc, calls = self._address("staff@example.com", links=[("Customer", "Kier")])
		doc.link_address()
		self.assertEqual(calls, ["staff@example.com"], "frappe's own no-op for a linked address was skipped")

	def test_it_is_wired_as_an_extension_not_a_replacement(self):
		import ast

		hooks = (REPO_ROOT / "erpnext_enhancements" / "hooks.py").read_text()
		tree = ast.parse(hooks)
		values = {
			node.targets[0].id: node.value
			for node in tree.body
			if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
		}
		extend = ast.literal_eval(values["extend_doctype_class"])
		self.assertEqual(extend["Address"], ["erpnext_enhancements.contacts_ux.AddressLinkGuard"])
		override = ast.literal_eval(values["override_doctype_class"])
		self.assertNotIn("Address", override, "replacing Address would drop ERPNext's own extension")


if __name__ == "__main__":
	unittest.main()
