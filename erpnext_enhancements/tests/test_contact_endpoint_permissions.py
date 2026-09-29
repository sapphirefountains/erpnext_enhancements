"""Bench-free tests for the permission gates on the contact, address and ship-to endpoints (v1.561.1).

Frappe v16's whitelist refuses nothing but a Guest (``is_whitelisted``, frappe/__init__.py:479-487 at
v16.35.0), so an ``@frappe.whitelist()`` function that checks nothing in its own body answers to every
signed-in user, portal users included. Until v1.561.1 that was true of:

* ``sync_contact.get_contacts_for_context`` / ``get_addresses_for_context`` (any party's contacts with
  phone numbers and email addresses, or its addresses, through ``frappe.get_all``);
* ``sync_contact.link_existing_record`` / ``unlink_record`` (any Contact or Address saved with
  ``ignore_permissions``);
* ``package_dispatch.api.get_customer_ship_to`` and ``get_item_dispatch_details`` (only the feature
  switch);
* ``script_migrations.debug.run_debug_query`` (nothing; now deleted).

``import_contacts`` checked write on the target only and took any Contact names, so write on the
target stood in for permission on every Contact named.

The PR's review added three more, each with its own class here:

* ``set_primary_contact`` / ``set_primary_address`` checked write on the account only and passed the
  Contact or Address name straight to ``frappe.db.set_value``, which reads a dict or a list in that
  place as filters and updates every row they match (``SetPrimaryTest``);
* an integer name passed ``has_permission`` on one document and then went into an ``IN`` filter that
  MariaDB compares numerically, matching every party whose name starts with that number
  (``RequestNamesTest``);
* each returned row's "Linked To" listed every party the record is linked to, readable or not
  (``DirectoryLinksTest``).

The client half of the directory, ``get_all_party_sources``, runs in node:
``scripts/test_party_sources.mjs``.

Each endpoint gets three kinds of test: refused without the permission, allowed with it, and the path
the Desk callers (``unified_tab_controller.js``, ``package_dispatch.js``) take still working, including
the quiet ones (a related party the user cannot read, an unsaved form's placeholder name).

The ``frappe`` stub is installed in ``setUpModule`` and removed in ``tearDownModule``, and permissions
are an explicit set of grants for the session user, so "no grant" is exactly a user with no permission.
Every name here is invented.

Run: python -m unittest erpnext_enhancements.tests.test_contact_endpoint_permissions -v
"""

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

STUBBED = ("frappe", "frappe.utils")
OURS = (
	"erpnext_enhancements.sync_contact",
	"erpnext_enhancements.feature_flags",
	"erpnext_enhancements.package_dispatch.api",
)
_saved = {}

sync_contact = None
dispatch = None


class StubValidationError(Exception):
	"""``frappe.throw`` with no exception class."""


class StubPermissionError(Exception):
	"""``frappe.PermissionError``."""


class StubDoesNotExistError(Exception):
	"""``frappe.DoesNotExistError``."""


EXCLUSION = "Directory Link Exclusion"

#: DocTypes the stub knows. Dynamic Link is the one child table the sources could name.
DOCTYPES = {"Contact", "Address", "Customer", "Supplier", "Project", "Lead", "Item", "Dynamic Link"}
CHILD_TABLES = {"Dynamic Link"}

STATE = {}


class _Dict(dict):
	"""The attribute access frappe._dict gives rows."""

	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


def _reset():
	STATE.clear()
	STATE.update(
		{
			"grants": set(),
			"perm_calls": [],
			"messages": [],
			"queries": [],
			"reads": [],
			"saves": [],
			"set_values": [],
			"flag": 1,
			"exclusion_seq": 0,
			"tables": {
				"Customer": {
					"Northwind Fountains": {
						"name": "Northwind Fountains",
						"customer_name": "Northwind Fountains",
						"customer_primary_address": "A-NW",
						"customer_primary_contact": "C-NW-1",
					},
					# Shares its name with a Supplier below: the directory must key on the pair.
					"Harbor Plaza": {"name": "Harbor Plaza", "customer_name": "Harbor Plaza"},
				},
				"Supplier": {"Harbor Plaza": {"name": "Harbor Plaza"}},
				"Project": {"PROJ-0001": {"name": "PROJ-0001"}},
				"Lead": {"LEAD-0001": {"name": "LEAD-0001"}},
				"Dynamic Link": {"DL-ROW-1": {"name": "DL-ROW-1"}},
				"Contact": {
					"C-NW-1": {
						"name": "C-NW-1",
						"first_name": "Ada",
						"last_name": "Example",
						"custom_email": "ada@example.com",
						"custom_phone_number": "555-0101",
						"phone": "555-0101",
						"is_primary_contact": 1,
					},
					"C-NW-2": {"name": "C-NW-2", "first_name": "Ben", "custom_email": "ben@example.com"},
					"C-PROJ": {"name": "C-PROJ", "first_name": "Cy", "custom_email": "cy@example.com"},
					"C-HP": {"name": "C-HP", "first_name": "Dee", "custom_email": "dee@example.com"},
					"C-SUP": {"name": "C-SUP", "first_name": "Eve", "custom_email": "eve@example.com"},
				},
				"Address": {
					"A-NW": {
						"name": "A-NW",
						"address_line1": "1 Example Way",
						"city": "Springfield",
						"state": "UT",
						"pincode": "84000",
						"country": "United States",
						"phone": "555-0199",
					},
					"A-HP": {"name": "A-HP", "address_line1": "2 Example Way"},
					"A-SUP": {"name": "A-SUP", "address_line1": "3 Example Way"},
				},
				"Item": {
					"ITEM-PUMP": {"name": "ITEM-PUMP", "item_name": "Example Pump"},
					"ITEM-COST": {
						"name": "ITEM-COST",
						"item_name": "Example Part",
						"standard_rate": 0,
						"valuation_rate": 40,
					},
				},
			},
			"Item Price": [
				{
					"item_code": "ITEM-PUMP",
					"price_list": "Standard Selling",
					"selling": 1,
					"price_list_rate": 120,
				},
			],
			"Dynamic Link": [
				_link("C-NW-1", "Contact", "Customer", "Northwind Fountains"),
				_link("C-NW-2", "Contact", "Customer", "Northwind Fountains"),
				_link("C-NW-2", "Contact", "Project", "PROJ-0001"),
				_link("C-PROJ", "Contact", "Project", "PROJ-0001"),
				_link("C-HP", "Contact", "Customer", "Harbor Plaza"),
				_link("C-SUP", "Contact", "Supplier", "Harbor Plaza"),
				_link("A-NW", "Address", "Customer", "Northwind Fountains"),
				_link("A-HP", "Address", "Customer", "Harbor Plaza"),
				_link("A-SUP", "Address", "Supplier", "Harbor Plaza"),
			],
			EXCLUSION: [],
		}
	)


def _link(parent, parenttype, link_doctype, link_name):
	return {"parent": parent, "parenttype": parenttype, "link_doctype": link_doctype, "link_name": link_name}


def grant(doctype, name, *ptypes):
	for ptype in ptypes:
		STATE["grants"].add((doctype, name, ptype))


def _row_matches(row, filters):
	for key, want in (filters or {}).items():
		got = row.get(key)
		if isinstance(want, (list, tuple)) and len(want) == 2 and want[0] == "in":
			if got not in want[1]:
				return False
		elif got != want:
			return False
	return True


class _Record:
	"""A Contact or Address document: its ``links`` table, and a ``save`` that persists it."""

	def __init__(self, doctype, name):
		self.doctype = doctype
		self.name = name
		self.links = [
			_Dict(link_doctype=r["link_doctype"], link_name=r["link_name"])
			for r in STATE["Dynamic Link"]
			if r["parenttype"] == doctype and r["parent"] == name
		]

	def append(self, table, row):
		assert table == "links", table
		self.links.append(_Dict(**row))

	def set(self, table, rows):
		assert table == "links", table
		self.links = [_Dict(**row) for row in rows]

	def save(self, **kwargs):
		STATE["saves"].append((self.doctype, self.name, kwargs))
		STATE["Dynamic Link"] = [
			r
			for r in STATE["Dynamic Link"]
			if not (r["parenttype"] == self.doctype and r["parent"] == self.name)
		] + [_link(self.name, self.doctype, l.link_doctype, l.link_name) for l in self.links]


class _NewDoc:
	"""``frappe.get_doc({...})``, which only the exclusion insert uses."""

	def __init__(self, values):
		self.values = dict(values)

	def insert(self, ignore_permissions=False):
		assert self.values.pop("doctype") == EXCLUSION
		STATE["exclusion_seq"] += 1
		STATE[EXCLUSION].append({"name": f"EXCL-{STATE['exclusion_seq']}", **self.values})
		return self


def _install_stub():
	frappe = types.ModuleType("frappe")
	frappe.ValidationError = StubValidationError
	frappe.PermissionError = StubPermissionError
	frappe.DoesNotExistError = StubDoesNotExistError
	frappe._ = lambda text: text
	frappe._dict = _Dict
	frappe.whitelist = lambda *a, **kw: (lambda fn: fn)

	def throw(msg, exc=None, title=None, **kwargs):
		# frappe.throw queues its message before raising, which is why the quiet paths must
		# never reach it: the Desk shows a queued message even when the exception is caught.
		STATE["messages"].append(str(msg))
		raise (exc or StubValidationError)(str(msg))

	def has_permission(doctype=None, ptype="read", doc=None, user=None, throw=False, **kwargs):
		STATE["perm_calls"].append((doctype, ptype, doc, throw))
		if doctype not in DOCTYPES:
			STATE["messages"].append(f"DocType {doctype} not found")
			raise StubDoesNotExistError(doctype)
		if doc is not None and doctype not in CHILD_TABLES and doc not in STATE["tables"].get(doctype, {}):
			# v16: get_lazy_doc -> load_from_db -> frappe.throw(..., DoesNotExistError).
			STATE["messages"].append(f"{doctype} {doc} not found")
			raise StubDoesNotExistError(f"{doctype} {doc}")
		allowed = (doctype, doc, ptype) in STATE["grants"]
		if ptype == "select":
			# v16 has_permission: "select permission is implied by read permission".
			allowed = allowed or (doctype, doc, "read") in STATE["grants"]
		if not allowed and throw:
			STATE["messages"].append(f"No permission for {doctype} {doc}")
			raise StubPermissionError(f"no {ptype} on {doctype} {doc}")
		return allowed

	def get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
		STATE["queries"].append((doctype, filters))
		if doctype in ("Contact", "Address"):
			wanted = (filters or {}).get("name")
			assert isinstance(wanted, (list, tuple)) and wanted[0] == "in", filters
			table = STATE["tables"][doctype]
			rows = [_Dict({f: table[n].get(f) for f in fields}) for n in table if n in wanted[1]]
		else:
			rows = [_Dict(r) for r in STATE[doctype] if _row_matches(r, filters)]
		if pluck:
			return [r[pluck] for r in rows]
		return rows

	def get_doc(doctype, name=None, **kwargs):
		if isinstance(doctype, dict):
			return _NewDoc(doctype)
		if name not in STATE["tables"].get(doctype, {}):
			raise StubDoesNotExistError(f"{doctype} {name}")
		return _Record(doctype, name)

	def delete_doc(doctype, name, **kwargs):
		STATE[doctype] = [r for r in STATE[doctype] if r["name"] != name]

	class _DB:
		def exists(self, dt, dn=None, cache=False):
			if dt == "DocType":
				return dn if dn in DOCTYPES else None
			if isinstance(dn, dict):
				return any(_row_matches(r, dn) for r in STATE[dt])
			if dt == dn:
				# v16 frappe.db.exists: "single always exists (!)", answered without a lookup.
				return dn
			return dn if dn in STATE["tables"][dt] else None

		def get_value(self, doctype, name, fieldname, as_dict=False, **kwargs):
			STATE["reads"].append((doctype, name))
			if isinstance(name, dict):
				rows = [r for r in STATE[doctype] if _row_matches(r, name)]
				return rows[0].get(fieldname) if rows else None
			row = STATE["tables"].get(doctype, {}).get(name)
			if row is None:
				return None
			if isinstance(fieldname, (list, tuple)):
				values = _Dict({f: row.get(f) for f in fieldname})
				return values if as_dict else tuple(values[f] for f in fieldname)
			return row.get(fieldname)

		def set_value(self, doctype, name, field, value, *args, **kwargs):
			# v16 frappe.db.set_value hands a dict or list here to get_query as FILTERS and
			# updates every row they match, so the stub records exactly what it was given.
			STATE["set_values"].append((doctype, name, field, value))

		def get_single_value(self, doctype, field):
			assert (doctype, field) == ("ERPNext Enhancements Settings", "package_dispatch_enabled")
			return STATE["flag"]

	frappe.throw = throw
	frappe.has_permission = has_permission
	frappe.is_table = lambda doctype: doctype in CHILD_TABLES
	frappe.get_all = get_all
	frappe.get_doc = get_doc
	frappe.delete_doc = delete_doc
	frappe.get_meta = lambda doctype: types.SimpleNamespace(has_field=lambda fieldname: True)
	frappe.db = _DB()

	utils = types.ModuleType("frappe.utils")
	utils.flt = lambda value: float(value or 0)
	utils.cint = lambda value: int(value or 0)
	frappe.utils = utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils


def _set_parent_attribute(name, module):
	"""Point the parent package's attribute at ``module`` (or remove it).

	``from erpnext_enhancements import sync_contact`` reads that attribute before it looks in
	``sys.modules``, so popping the module alone would hand the next suite in this process a
	module bound to this suite's stub.
	"""
	parent_name, _, child = name.rpartition(".")
	parent = sys.modules.get(parent_name)
	if parent is None:
		return
	if module is None:
		if hasattr(parent, child):
			delattr(parent, child)
	else:
		setattr(parent, child, module)


def setUpModule():
	global sync_contact, dispatch
	for name in STUBBED + OURS:
		_saved[name] = sys.modules.pop(name, None)
	_install_stub()
	sync_contact = importlib.import_module("erpnext_enhancements.sync_contact")
	dispatch = importlib.import_module("erpnext_enhancements.package_dispatch.api")


def tearDownModule():
	for name in STUBBED + OURS:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]
		if name in OURS:
			_set_parent_attribute(name, _saved.get(name))


def _names(rows):
	return {r["name"] for r in rows}


def _links_of(parent):
	return {(r["link_doctype"], r["link_name"]) for r in STATE["Dynamic Link"] if r["parent"] == parent}


def _exclusions():
	return {
		(r["source_doctype"], r["source_name"], r["ref_doctype"], r["ref_name"]) for r in STATE[EXCLUSION]
	}


PROJECT = {"doctype": "Project", "name": "PROJ-0001"}
NORTHWIND = {"doctype": "Customer", "name": "Northwind Fountains"}


class Base(unittest.TestCase):
	def setUp(self):
		_reset()

	def assertQuiet(self):
		"""No permission check that could raise, and nothing queued for the Desk to show."""
		self.assertEqual([c for c in STATE["perm_calls"] if c[3]], [], "a read path asked to throw")
		self.assertEqual(STATE["messages"], [], "a read path queued a message")


# ---------------------------------------------------------------------------------------------------
# get_contacts_for_context / get_addresses_for_context
# ---------------------------------------------------------------------------------------------------


class DirectoryReadTest(Base):
	"""Read on each source party, dropped quietly when missing."""

	def test_a_caller_who_may_read_no_party_gets_nothing(self):
		"""The exposure: a portal user naming a customer got its people's phones and emails."""
		self.assertEqual(sync_contact.get_contacts_for_context([NORTHWIND]), [])
		self.assertEqual(sync_contact.get_addresses_for_context([NORTHWIND]), [])
		touched = {doctype for doctype, _ in STATE["queries"]}
		self.assertNotIn("Contact", touched)
		self.assertNotIn("Address", touched)
		self.assertNotIn("Dynamic Link", touched, "it looked up links for a party it may not read")
		self.assertQuiet()

	def test_read_on_the_party_returns_its_contacts(self):
		"""The Customer form's directory."""
		grant("Customer", "Northwind Fountains", "read")
		contacts = sync_contact.get_contacts_for_context([NORTHWIND], "Customer", "Northwind Fountains")

		self.assertEqual(_names(contacts), {"C-NW-1", "C-NW-2"})
		ada = next(c for c in contacts if c["name"] == "C-NW-1")
		self.assertEqual(ada["custom_email"], "ada@example.com")
		self.assertIn({"name": "Northwind Fountains", "doctype": "Customer"}, ada["links"])
		self.assertIn(("Customer", "read", "Northwind Fountains", False), STATE["perm_calls"])
		self.assertQuiet()

	def test_the_json_string_the_desk_posts_is_accepted(self):
		grant("Customer", "Northwind Fountains", "read")
		contacts = sync_contact.get_contacts_for_context(json.dumps([NORTHWIND]))
		self.assertEqual(_names(contacts), {"C-NW-1", "C-NW-2"})

	def test_an_unreadable_related_party_is_dropped_not_refused(self):
		"""A Project's directory for someone who may read the Project but only select its Customer.

		Stock ERPNext gives Projects User exactly that. Raising would replace the whole directory with
		an error on every refresh; dropping shows the Project's own contacts.
		"""
		grant("Project", "PROJ-0001", "read")
		contacts = sync_contact.get_contacts_for_context([PROJECT, NORTHWIND], "Project", "PROJ-0001")

		self.assertEqual(_names(contacts), {"C-NW-2", "C-PROJ"})
		self.assertNotIn("C-NW-1", _names(contacts), "a contact reachable only through the Customer leaked")
		self.assertQuiet()

	def test_every_readable_party_is_pooled(self):
		grant("Project", "PROJ-0001", "read")
		grant("Customer", "Northwind Fountains", "read")
		contacts = sync_contact.get_contacts_for_context([PROJECT, NORTHWIND], "Project", "PROJ-0001")
		self.assertEqual(_names(contacts), {"C-NW-1", "C-NW-2", "C-PROJ"})

	def test_an_unsaved_forms_placeholder_is_dropped_without_a_message(self):
		"""A new Project form sends ``new-project-…`` as its own source, with its Customer."""
		grant("Customer", "Northwind Fountains", "read")
		placeholder = {"doctype": "Project", "name": "new-project-abc123"}
		contacts = sync_contact.get_contacts_for_context(
			[placeholder, NORTHWIND], "Project", placeholder["name"]
		)

		self.assertEqual(_names(contacts), {"C-NW-1", "C-NW-2"})
		self.assertNotIn(("Project", "read", "new-project-abc123", False), STATE["perm_calls"])
		self.assertQuiet()

	def test_a_party_is_matched_on_doctype_and_name_together(self):
		"""Read on Customer "Harbor Plaza" is not read on the Supplier of the same name."""
		grant("Customer", "Harbor Plaza", "read")
		harbor = {"doctype": "Customer", "name": "Harbor Plaza"}

		self.assertEqual(_names(sync_contact.get_contacts_for_context([harbor])), {"C-HP"})
		self.assertEqual(_names(sync_contact.get_addresses_for_context([harbor])), {"A-HP"})

	def test_malformed_and_unknown_sources_are_dropped(self):
		grant("Customer", "Northwind Fountains", "read")
		grant("Dynamic Link", "DL-ROW-1", "read")
		sources = [
			None,
			"Northwind Fountains",
			{"name": "Northwind Fountains"},
			{"doctype": "Customer"},
			{"doctype": ["Customer"], "name": "Northwind Fountains"},
			{"doctype": "Customer", "name": ["Northwind Fountains"]},
			{"doctype": "No Such DocType", "name": "X"},
			{"doctype": "Dynamic Link", "name": "DL-ROW-1"},
			{"doctype": "Customer", "name": "Customer"},
			NORTHWIND,
		]
		contacts = sync_contact.get_contacts_for_context(sources)

		self.assertEqual(_names(contacts), {"C-NW-1", "C-NW-2"})
		checked = {(c[0], c[2]) for c in STATE["perm_calls"]}
		self.assertNotIn(("Dynamic Link", "DL-ROW-1"), checked, "a child table was treated as a party")

	def test_a_repeated_source_is_checked_once(self):
		grant("Customer", "Northwind Fountains", "read")
		sync_contact.get_contacts_for_context([NORTHWIND, NORTHWIND, NORTHWIND])
		checks = [c for c in STATE["perm_calls"] if c[:2] == ("Customer", "read")]
		self.assertEqual(checks, [("Customer", "read", "Northwind Fountains", False)])

	def test_this_documents_exclusions_still_apply(self):
		grant("Customer", "Northwind Fountains", "read")
		STATE[EXCLUSION].append(
			{
				"name": "EXCL-X",
				"source_doctype": "Project",
				"source_name": "PROJ-0001",
				"ref_doctype": "Contact",
				"ref_name": "C-NW-1",
			}
		)
		contacts = sync_contact.get_contacts_for_context([NORTHWIND], "Project", "PROJ-0001")
		self.assertEqual(_names(contacts), {"C-NW-2"})

	def test_addresses_follow_the_same_rule(self):
		grant("Project", "PROJ-0001", "read")
		self.assertEqual(sync_contact.get_addresses_for_context([PROJECT, NORTHWIND]), [])

		grant("Customer", "Northwind Fountains", "read")
		addresses = sync_contact.get_addresses_for_context([PROJECT, NORTHWIND], "Project", "PROJ-0001")
		self.assertEqual(_names(addresses), {"A-NW"})
		self.assertEqual(addresses[0]["links"], [{"name": "Northwind Fountains", "doctype": "Customer"}])
		self.assertQuiet()


class DirectoryLinksTest(Base):
	"""A returned row's "Linked To" names only parties the caller may select (v1.561.1 review).

	C-NW-2 belongs to Customer "Northwind Fountains" and to Project PROJ-0001, so read on the
	Customer alone used to show the Project's name too.
	"""

	def _links(self, rows, name):
		return {(l["doctype"], l["name"]) for l in next(r for r in rows if r["name"] == name)["links"]}

	def test_a_party_the_caller_may_not_select_is_left_off_the_row(self):
		grant("Customer", "Northwind Fountains", "read")
		contacts = sync_contact.get_contacts_for_context([NORTHWIND])

		self.assertEqual(self._links(contacts, "C-NW-2"), {("Customer", "Northwind Fountains")})
		self.assertIn(("Project", "select", "PROJ-0001", False), STATE["perm_calls"])
		self.assertQuiet()

	def test_select_on_the_other_party_keeps_it(self):
		"""The Projects User case in reverse: select, not read, is what naming a party needs."""
		grant("Customer", "Northwind Fountains", "read")
		grant("Project", "PROJ-0001", "select")
		contacts = sync_contact.get_contacts_for_context([NORTHWIND])

		self.assertEqual(
			self._links(contacts, "C-NW-2"),
			{("Customer", "Northwind Fountains"), ("Project", "PROJ-0001")},
		)

	def test_a_source_party_is_not_checked_again(self):
		grant("Project", "PROJ-0001", "read")
		grant("Customer", "Northwind Fountains", "read")
		sync_contact.get_contacts_for_context([PROJECT, NORTHWIND])

		self.assertEqual([c for c in STATE["perm_calls"] if c[1] == "select"], [])

	def test_each_other_party_is_checked_once_per_call(self):
		for contact in ("C-NW-1", "C-NW-2"):
			STATE["Dynamic Link"].append(_link(contact, "Contact", "Lead", "LEAD-0001"))
		grant("Customer", "Northwind Fountains", "read")
		sync_contact.get_contacts_for_context([NORTHWIND])

		lead_checks = [c for c in STATE["perm_calls"] if c[0] == "Lead"]
		self.assertEqual(lead_checks, [("Lead", "select", "LEAD-0001", False)])

	def test_a_link_to_a_missing_party_is_dropped_quietly(self):
		STATE["Dynamic Link"].append(_link("C-NW-1", "Contact", "Customer", "Gone Fountains"))
		STATE["Dynamic Link"].append(_link("C-NW-1", "Contact", "No Such DocType", "X"))
		grant("Customer", "Northwind Fountains", "read")
		contacts = sync_contact.get_contacts_for_context([NORTHWIND])

		self.assertEqual(self._links(contacts, "C-NW-1"), {("Customer", "Northwind Fountains")})
		self.assertQuiet()

	def test_addresses_follow_the_same_rule(self):
		STATE["Dynamic Link"].append(_link("A-NW", "Address", "Supplier", "Harbor Plaza"))
		grant("Customer", "Northwind Fountains", "read")
		addresses = sync_contact.get_addresses_for_context([NORTHWIND])
		self.assertEqual(self._links(addresses, "A-NW"), {("Customer", "Northwind Fountains")})

		_reset()
		STATE["Dynamic Link"].append(_link("A-NW", "Address", "Supplier", "Harbor Plaza"))
		grant("Customer", "Northwind Fountains", "read")
		grant("Supplier", "Harbor Plaza", "read")
		addresses = sync_contact.get_addresses_for_context([NORTHWIND])
		self.assertEqual(
			self._links(addresses, "A-NW"),
			{("Customer", "Northwind Fountains"), ("Supplier", "Harbor Plaza")},
		)


def _add_numbered_customer():
	"""A Customer whose name is a number, with one contact and one exclusion.

	MariaDB compares a VARCHAR with an integer numerically, so ``link_name IN (5)`` would
	match "5", "5 Star Fountains" and every other name starting with a 5, while
	``has_permission`` on 5 checks one document. The stub compares in Python, so what these
	tests pin is the value each query is handed: always the string that was checked.
	"""
	STATE["tables"]["Customer"]["5"] = {"name": "5"}
	STATE["tables"]["Contact"]["C-5"] = {
		"name": "C-5",
		"first_name": "Fay",
		"custom_email": "fay@example.com",
	}
	STATE["Dynamic Link"].append(_link("C-5", "Contact", "Customer", "5"))


def _filter_values(doctype, key):
	"""Every value a ``get_all`` on ``doctype`` filtered ``key`` by, with ``in`` lists flattened."""
	values = []
	for queried, filters in STATE["queries"]:
		if queried != doctype or key not in (filters or {}):
			continue
		want = filters[key]
		if isinstance(want, (list, tuple)) and len(want) == 2 and want[0] == "in":
			values += list(want[1])
		else:
			values.append(want)
	return values


class RequestNamesTest(Base):
	"""One value governs both the permission check and the query (v1.561.1 review)."""

	def test_an_integer_party_is_queried_as_the_string_it_was_checked_as(self):
		_add_numbered_customer()
		grant("Customer", "5", "read")
		contacts = sync_contact.get_contacts_for_context(json.dumps([{"doctype": "Customer", "name": 5}]))

		self.assertEqual(_names(contacts), {"C-5"})
		self.assertIn(("Customer", "read", "5", False), STATE["perm_calls"])
		self.assertEqual(_filter_values("Dynamic Link", "link_name"), ["5"])

	def test_a_name_that_is_not_text_or_a_whole_number_is_dropped(self):
		grant("Customer", "Northwind Fountains", "read")
		sources = [{"doctype": "Customer", "name": bad} for bad in (True, False, 1.5, {"like": "%"}, [5], "")]
		self.assertEqual(sync_contact.get_contacts_for_context(sources), [])
		self.assertEqual(STATE["perm_calls"], [])
		self.assertEqual(_filter_values("Dynamic Link", "link_name"), [])

	def test_link_existing_writes_and_clears_by_the_checked_string(self):
		_add_numbered_customer()
		grant("Contact", "C-HP", "write")
		grant("Customer", "5", "write")
		sync_contact.link_existing_record(
			"Contact", "C-HP", links=[{"link_doctype": "Customer", "link_name": 5}]
		)

		self.assertIn(("Customer", "write", "5", False), STATE["perm_calls"])
		self.assertIn(("Customer", "5"), _links_of("C-HP"))
		self.assertEqual(_filter_values(EXCLUSION, "source_name"), ["5"])

	def test_link_existing_refuses_a_record_that_is_not_one_name(self):
		grant("Contact", "C-HP", "write")
		grant("Project", "PROJ-0001", "write")
		for bad in ({"name": ["like", "%"]}, ["C-HP"], True, None, ""):
			with self.subTest(docname=bad), self.assertRaises(StubValidationError):
				sync_contact.link_existing_record("Contact", bad, "Project", "PROJ-0001")
		self.assertEqual([c for c in STATE["perm_calls"] if c[0] == "Contact"], [])
		self.assertEqual(STATE["saves"], [])

	def test_link_existing_skips_a_link_row_that_is_not_an_object(self):
		grant("Contact", "C-HP", "write")
		with self.assertRaises(StubPermissionError):
			sync_contact.link_existing_record("Contact", "C-HP", links=json.dumps(["PROJ-0001"]))
		self.assertEqual(STATE["saves"], [])

	def test_unlink_refuses_names_that_are_not_text(self):
		grant("Project", "PROJ-0001", "write")
		grant("Contact", "C-NW-2", "write")
		for docname, link_name in (({"name": "C-NW-2"}, "PROJ-0001"), ("C-NW-2", {"name": "PROJ-0001"})):
			with self.subTest(docname=docname, link_name=link_name), self.assertRaises(StubValidationError):
				sync_contact.unlink_record("Contact", docname, "Project", link_name)
		self.assertEqual(STATE["perm_calls"], [])
		self.assertEqual(STATE[EXCLUSION], [])

	def test_unlink_checks_and_hides_by_the_same_string(self):
		_add_numbered_customer()
		grant("Customer", "5", "write")
		sync_contact.unlink_record("Contact", "C-NW-1", "Customer", 5)

		self.assertIn(("Customer", "write", "5", True), STATE["perm_calls"])
		self.assertEqual(_exclusions(), {("Customer", "5", "Contact", "C-NW-1")})

	def test_import_links_and_clears_by_the_checked_string(self):
		_add_numbered_customer()
		grant("Customer", "5", "write")
		grant("Contact", "C-NW-1", "write")
		sync_contact.import_contacts("Customer", 5, ["C-NW-1"])

		self.assertIn(("Customer", "write", "5", True), STATE["perm_calls"])
		self.assertIn(("Customer", "5"), _links_of("C-NW-1"))
		self.assertEqual(_filter_values(EXCLUSION, "source_name"), ["5"])

	def test_import_refuses_a_target_that_is_not_one_name(self):
		grant("Project", "PROJ-0001", "write")
		with self.assertRaises(StubValidationError):
			sync_contact.import_contacts("Project", {"name": ["like", "%"]}, ["C-NW-1"])
		with self.assertRaises(StubValidationError):
			sync_contact.get_importable_contacts("Project", ["PROJ-0001"], [PROJECT])
		self.assertEqual(STATE["perm_calls"], [])
		self.assertEqual(STATE["saves"], [])


class ImportableContactsTest(Base):
	def test_an_unreadable_related_party_offers_nothing(self):
		grant("Project", "PROJ-0001", "read")
		offered = sync_contact.get_importable_contacts("Project", "PROJ-0001", [PROJECT, NORTHWIND])
		self.assertEqual(offered, [])

	def test_a_readable_related_party_offers_what_the_project_lacks(self):
		grant("Project", "PROJ-0001", "read")
		grant("Customer", "Northwind Fountains", "read")
		offered = sync_contact.get_importable_contacts("Project", "PROJ-0001", [PROJECT, NORTHWIND])
		self.assertEqual(_names(offered), {"C-NW-1"})

	def test_read_on_the_target_is_still_refused_loudly(self):
		grant("Customer", "Northwind Fountains", "read")
		with self.assertRaises(StubPermissionError):
			sync_contact.get_importable_contacts("Project", "PROJ-0001", [NORTHWIND])


# ---------------------------------------------------------------------------------------------------
# link_existing_record
# ---------------------------------------------------------------------------------------------------


class LinkExistingTest(Base):
	"""Write on the Contact/Address; write on each party, unwritable ones skipped."""

	#: What the widget's Link Existing sends from a Project form: every party it draws from.
	DESK_LINKS = json.dumps(
		[
			{"link_doctype": "Project", "link_name": "PROJ-0001"},
			{"link_doctype": "Customer", "link_name": "Northwind Fountains"},
		]
	)

	def test_refused_without_write_on_the_contact(self):
		grant("Project", "PROJ-0001", "read", "write")
		grant("Customer", "Northwind Fountains", "read", "write")
		grant("Contact", "C-HP", "read")

		with self.assertRaises(StubPermissionError):
			sync_contact.link_existing_record("Contact", "C-HP", links=self.DESK_LINKS)

		self.assertEqual(_links_of("C-HP"), {("Customer", "Harbor Plaza")})
		self.assertEqual(STATE["saves"], [])

	def test_refused_when_no_party_is_writable(self):
		grant("Contact", "C-HP", "write")
		STATE[EXCLUSION].append(
			{
				"name": "EXCL-X",
				"source_doctype": "Project",
				"source_name": "PROJ-0001",
				"ref_doctype": "Contact",
				"ref_name": "C-HP",
			}
		)

		with self.assertRaises(StubPermissionError):
			sync_contact.link_existing_record("Contact", "C-HP", links=self.DESK_LINKS)

		self.assertEqual(_links_of("C-HP"), {("Customer", "Harbor Plaza")})
		self.assertEqual(STATE["saves"], [])
		self.assertEqual(len(STATE[EXCLUSION]), 1, "a refused link still cleared an exclusion")

	def test_the_single_link_form_is_gated_too(self):
		grant("Contact", "C-HP", "write")
		with self.assertRaises(StubPermissionError):
			sync_contact.link_existing_record("Contact", "C-HP", "Project", "PROJ-0001")
		self.assertEqual(STATE["saves"], [])

	def test_an_unwritable_related_party_is_skipped(self):
		"""A Projects User links a contact from a Project whose Customer they can only select."""
		grant("Contact", "C-HP", "write")
		grant("Project", "PROJ-0001", "read", "write")
		STATE[EXCLUSION].append(
			{
				"name": "EXCL-NW",
				"source_doctype": "Customer",
				"source_name": "Northwind Fountains",
				"ref_doctype": "Contact",
				"ref_name": "C-HP",
			}
		)

		self.assertTrue(sync_contact.link_existing_record("Contact", "C-HP", links=self.DESK_LINKS))

		self.assertEqual(_links_of("C-HP"), {("Customer", "Harbor Plaza"), ("Project", "PROJ-0001")})
		self.assertIn(
			("Customer", "Northwind Fountains", "Contact", "C-HP"),
			_exclusions(),
			"it cleared an exclusion on a directory the user may not change",
		)

	def test_the_desk_path_links_to_every_writable_party(self):
		grant("Contact", "C-HP", "write")
		grant("Project", "PROJ-0001", "write")
		grant("Customer", "Northwind Fountains", "write")

		self.assertTrue(sync_contact.link_existing_record("Contact", "C-HP", links=self.DESK_LINKS))

		self.assertEqual(
			_links_of("C-HP"),
			{("Customer", "Harbor Plaza"), ("Project", "PROJ-0001"), ("Customer", "Northwind Fountains")},
		)
		self.assertEqual(STATE["saves"], [("Contact", "C-HP", {})], "saved with ignore_permissions")
		self.assertIn(("Contact", "write", "C-HP", True), STATE["perm_calls"])

	def test_an_address_links_the_same_way(self):
		grant("Address", "A-HP", "write")
		grant("Project", "PROJ-0001", "write")
		sync_contact.link_existing_record("Address", "A-HP", "Project", "PROJ-0001")
		self.assertIn(("Project", "PROJ-0001"), _links_of("A-HP"))

	def test_an_unsaved_forms_placeholder_still_fails_and_writes_nothing(self):
		"""Before, the save failed link validation on ``new-project-…``. It must not now quietly link
		the contact to the Customer instead."""
		grant("Contact", "C-HP", "write")
		grant("Customer", "Northwind Fountains", "write")
		links = [
			{"link_doctype": "Project", "link_name": "new-project-abc123"},
			{"link_doctype": "Customer", "link_name": "Northwind Fountains"},
		]

		with self.assertRaises(StubDoesNotExistError):
			sync_contact.link_existing_record("Contact", "C-HP", links=links)
		self.assertEqual(_links_of("C-HP"), {("Customer", "Harbor Plaza")})

	def test_a_child_table_row_is_not_a_party(self):
		grant("Contact", "C-HP", "write")
		grant("Dynamic Link", "DL-ROW-1", "write")
		with self.assertRaises(StubPermissionError):
			sync_contact.link_existing_record("Contact", "C-HP", "Dynamic Link", "DL-ROW-1")
		self.assertEqual(STATE["saves"], [])

	def test_only_contacts_and_addresses(self):
		grant("Customer", "Northwind Fountains", "write")
		for doctype in ("Customer", "User", "Dynamic Link", None):
			with self.subTest(doctype=doctype), self.assertRaises(StubValidationError):
				sync_contact.link_existing_record(doctype, "Northwind Fountains", "Project", "PROJ-0001")
		self.assertEqual(STATE["saves"], [])


# ---------------------------------------------------------------------------------------------------
# unlink_record
# ---------------------------------------------------------------------------------------------------


class UnlinkTest(Base):
	"""Write on the document being viewed; write on the Contact/Address when its row changes."""

	def test_refused_without_write_on_the_document_being_viewed(self):
		grant("Project", "PROJ-0001", "read")
		grant("Contact", "C-NW-2", "write")

		with self.assertRaises(StubPermissionError):
			sync_contact.unlink_record("Contact", "C-NW-2", "Project", "PROJ-0001")

		self.assertIn(("Project", "PROJ-0001"), _links_of("C-NW-2"))
		self.assertEqual(STATE[EXCLUSION], [])
		self.assertEqual(STATE["saves"], [])

	def test_an_inherited_contact_is_hidden_without_touching_it(self):
		"""C-NW-1 reaches the Project's directory through the Customer only."""
		grant("Project", "PROJ-0001", "write")

		self.assertTrue(sync_contact.unlink_record("Contact", "C-NW-1", "Project", "PROJ-0001"))

		self.assertEqual(_exclusions(), {("Project", "PROJ-0001", "Contact", "C-NW-1")})
		self.assertEqual(STATE["saves"], [])
		self.assertNotIn("Contact", {c[0] for c in STATE["perm_calls"]})

	def test_a_direct_link_needs_write_on_the_contact(self):
		grant("Project", "PROJ-0001", "write")

		with self.assertRaises(StubPermissionError):
			sync_contact.unlink_record("Contact", "C-NW-2", "Project", "PROJ-0001")

		self.assertIn(("Project", "PROJ-0001"), _links_of("C-NW-2"))
		self.assertEqual(STATE[EXCLUSION], [], "a refused unlink still hid the contact")

	def test_the_desk_path_removes_the_direct_link_and_keeps_the_rest(self):
		grant("Project", "PROJ-0001", "write")
		grant("Contact", "C-NW-2", "write")

		self.assertTrue(sync_contact.unlink_record("Contact", "C-NW-2", "Project", "PROJ-0001"))

		self.assertEqual(_links_of("C-NW-2"), {("Customer", "Northwind Fountains")})
		self.assertEqual(_exclusions(), {("Project", "PROJ-0001", "Contact", "C-NW-2")})
		self.assertEqual(STATE["saves"], [("Contact", "C-NW-2", {})], "saved with ignore_permissions")

	def test_only_contacts_and_addresses(self):
		grant("Project", "PROJ-0001", "write")
		with self.assertRaises(StubValidationError):
			sync_contact.unlink_record("Customer", "Northwind Fountains", "Project", "PROJ-0001")
		self.assertEqual(STATE[EXCLUSION], [])


# ---------------------------------------------------------------------------------------------------
# import_contacts
# ---------------------------------------------------------------------------------------------------


class ImportContactsTest(Base):
	"""Write on the target (as before) and now on each Contact that gains the link."""

	def test_a_contact_the_user_may_not_write_refuses_the_whole_import(self):
		grant("Project", "PROJ-0001", "write")
		grant("Contact", "C-NW-1", "write")

		with self.assertRaises(StubPermissionError):
			sync_contact.import_contacts("Project", "PROJ-0001", ["C-NW-1", "C-HP"])

		self.assertNotIn(("Project", "PROJ-0001"), _links_of("C-NW-1"), "a refused import linked the first")
		self.assertNotIn(("Project", "PROJ-0001"), _links_of("C-HP"))
		self.assertEqual(STATE["saves"], [])

	def test_an_already_linked_contact_needs_no_write(self):
		grant("Project", "PROJ-0001", "write")
		result = sync_contact.import_contacts("Project", "PROJ-0001", ["C-NW-2"])
		self.assertEqual(result, {"linked": 0, "skipped": 1, "contacts": []})

	def test_the_desk_path_links_the_ticked_contacts(self):
		grant("Project", "PROJ-0001", "write")
		grant("Contact", "C-NW-1", "write")
		result = sync_contact.import_contacts("Project", "PROJ-0001", json.dumps(["C-NW-1", "C-NW-2"]))

		self.assertEqual(result, {"linked": 1, "skipped": 1, "contacts": ["C-NW-1"]})
		self.assertIn(("Project", "PROJ-0001"), _links_of("C-NW-1"))
		self.assertEqual(STATE["saves"], [("Contact", "C-NW-1", {})], "saved with ignore_permissions")


# ---------------------------------------------------------------------------------------------------
# set_primary_contact / set_primary_address
# ---------------------------------------------------------------------------------------------------

#: Values that are not one document name. ``frappe.db.set_value`` reads a dict or a list as filters
#: and updates every row they match; ``True`` is a 1 to MariaDB.
NOT_ONE_NAME = ({"name": ["like", "%"]}, {"first_name": "Ada"}, ["C-NW-1", "C-NW-2"], True, 1.5, None, "")


class SetPrimaryTest(Base):
	"""Write on the account (as before) and now on the Contact or Address being flagged, which must
	be one name (v1.561.1 review)."""

	def test_a_filter_in_place_of_the_contact_is_refused_before_any_write(self):
		"""The exposure: write on one account flagged every Contact the filter matched."""
		grant("Customer", "Northwind Fountains", "write")
		for bad in NOT_ONE_NAME:
			with self.subTest(contact_name=bad), self.assertRaises(StubValidationError):
				sync_contact.set_primary_contact("Customer", "Northwind Fountains", bad)
		self.assertEqual(STATE["set_values"], [])

	def test_write_on_the_contact_is_required(self):
		grant("Customer", "Northwind Fountains", "write")
		grant("Contact", "C-NW-2", "read")
		with self.assertRaises(StubPermissionError):
			sync_contact.set_primary_contact("Customer", "Northwind Fountains", "C-NW-2")
		self.assertEqual(STATE["set_values"], [], "a refused call still cleared the others")

	def test_write_on_the_account_is_still_required(self):
		grant("Contact", "C-NW-2", "write")
		with self.assertRaises(StubPermissionError):
			sync_contact.set_primary_contact("Customer", "Northwind Fountains", "C-NW-2")
		self.assertEqual(STATE["set_values"], [])

	def test_the_desk_path_flags_the_contact(self):
		grant("Customer", "Northwind Fountains", "write")
		grant("Contact", "C-NW-2", "write")
		sync_contact.set_primary_contact("Customer", "Northwind Fountains", "C-NW-2")

		self.assertEqual(
			STATE["set_values"],
			[
				("Contact", {"name": ["in", ["C-NW-1", "C-NW-2"]]}, "is_primary_contact", 0),
				("Contact", "C-NW-2", "is_primary_contact", 1),
			],
		)
		self.assertIn(("Contact", "write", "C-NW-2", True), STATE["perm_calls"])

	def test_an_integer_account_is_matched_as_the_string_it_was_checked_as(self):
		_add_numbered_customer()
		grant("Customer", "5", "write")
		grant("Contact", "C-5", "write")
		sync_contact.set_primary_contact("Customer", 5, "C-5")

		self.assertIn(("Customer", "write", "5", True), STATE["perm_calls"])
		self.assertEqual(_filter_values("Dynamic Link", "link_name"), ["5"])
		self.assertEqual(STATE["set_values"][-1], ("Contact", "C-5", "is_primary_contact", 1))

	def test_an_account_that_is_not_one_name_is_refused(self):
		grant("Contact", "C-NW-2", "write")
		with self.assertRaises(StubValidationError):
			sync_contact.set_primary_contact("Customer", {"name": ["like", "%"]}, "C-NW-2")
		self.assertEqual(STATE["perm_calls"], [])
		self.assertEqual(STATE["set_values"], [])

	def test_the_address_counterpart_gates_the_same_way(self):
		grant("Customer", "Northwind Fountains", "write")
		for bad in NOT_ONE_NAME:
			with self.subTest(address_name=bad), self.assertRaises(StubValidationError):
				sync_contact.set_primary_address("Customer", "Northwind Fountains", bad)
		with self.assertRaises(StubPermissionError):
			sync_contact.set_primary_address("Customer", "Northwind Fountains", "A-NW")
		self.assertEqual(STATE["set_values"], [])

		grant("Address", "A-NW", "write")
		sync_contact.set_primary_address("Customer", "Northwind Fountains", "A-NW")
		self.assertEqual(
			STATE["set_values"],
			[
				("Address", {"name": ["in", ["A-NW"]]}, "is_primary_address", 0),
				("Address", "A-NW", "is_primary_address", 1),
			],
		)
		self.assertIn(("Address", "write", "A-NW", True), STATE["perm_calls"])


# ---------------------------------------------------------------------------------------------------
# package_dispatch.api
# ---------------------------------------------------------------------------------------------------


class ShipToTest(Base):
	def test_refused_without_read_on_the_customer(self):
		with self.assertRaises(StubPermissionError):
			dispatch.get_customer_ship_to("Northwind Fountains")
		self.assertEqual(STATE["reads"], [], "it read the customer before checking")

	def test_the_desk_path_fills_the_recipient_block(self):
		grant("Customer", "Northwind Fountains", "read")
		out = dispatch.get_customer_ship_to("Northwind Fountains")

		self.assertEqual(out["recipient_name"], "Northwind Fountains")
		self.assertEqual(out["address_line1"], "1 Example Way")
		self.assertEqual(out["recipient_phone"], "555-0199")
		self.assertIn(("Customer", "read", "Northwind Fountains", True), STATE["perm_calls"])

	def test_the_switch_still_comes_first(self):
		STATE["flag"] = 0
		grant("Customer", "Northwind Fountains", "read")
		with self.assertRaises(StubValidationError):
			dispatch.get_customer_ship_to("Northwind Fountains")
		self.assertEqual(STATE["perm_calls"], [])

	def test_a_blank_customer_is_still_an_empty_answer(self):
		self.assertEqual(dispatch.get_customer_ship_to(""), {})


class ItemDetailsTest(Base):
	def test_refused_without_read_on_the_item(self):
		with self.assertRaises(StubPermissionError):
			dispatch.get_item_dispatch_details("ITEM-COST")
		self.assertEqual(STATE["reads"], [], "it read the item before checking")

	def test_the_desk_path_fills_the_line(self):
		grant("Item", "ITEM-PUMP", "read")
		grant("Item", "ITEM-COST", "read")
		self.assertEqual(
			dispatch.get_item_dispatch_details("ITEM-PUMP"), {"description": "Example Pump", "rate": 120.0}
		)
		self.assertEqual(
			dispatch.get_item_dispatch_details("ITEM-COST"), {"description": "Example Part", "rate": 40.0}
		)

	def test_the_switch_still_comes_first(self):
		STATE["flag"] = 0
		with self.assertRaises(StubValidationError):
			dispatch.get_item_dispatch_details("ITEM-PUMP")
		self.assertEqual(STATE["perm_calls"], [])


# ---------------------------------------------------------------------------------------------------
# run_debug_query
# ---------------------------------------------------------------------------------------------------


class DebugHelperRemovedTest(unittest.TestCase):
	"""``script_migrations.debug.run_debug_query`` was deleted rather than gated: nothing called it."""

	def test_the_module_is_gone(self):
		self.assertFalse((APP / "script_migrations" / "debug.py").exists())
		self.assertIsNone(importlib.util.find_spec("erpnext_enhancements.script_migrations.debug"))

	def test_nothing_shipped_names_it(self):
		for path in [APP / "hooks.py", *APP.rglob("*.js")]:
			if "node_modules" in path.parts:
				continue
			with self.subTest(path=path.relative_to(APP)):
				self.assertNotIn("run_debug_query", path.read_text(encoding="utf-8", errors="replace"))


if __name__ == "__main__":
	unittest.main()
