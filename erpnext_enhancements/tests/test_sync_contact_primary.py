"""Bench-free tests for primary-contact / primary-address scoping.

``sync_contact`` had **zero** tests. This suite fences the rule the module now
enforces: only Customer and Supplier own the account-wide ``is_primary_contact`` /
``is_primary_address`` flags, and every other context must be refused rather than
quietly applied to whichever account happened to be nearby.

Follows the stubbing pattern in ``test_pickup_routing.py`` — an in-memory ``STATE``
plus a ``frappe`` stub installed in ``setUpModule`` (execution time, not import time,
so the bench-only suites' import guards are unaffected).

Run: python -m unittest erpnext_enhancements.tests.test_sync_contact_primary -v
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))


class StubThrow(Exception):
	"""What the stubbed ``frappe.throw`` raises."""


class StubPermissionError(Exception):
	"""What the stubbed ``frappe.has_permission(..., throw=True)`` raises."""


#: Tables the stub serves. Reset per test.
STATE = {
	"Contact": {},
	"Address": {},
	"Dynamic Link": [],
	"permissions": True,
	"perm_calls": [],
	# Fields the *site* has, per doctype — apply_primary_contact_details guards on
	# these because the three primary_contact_* fields live only in the live
	# database (neither the fixtures nor setup/custom_fields.py create them).
	"fields": {},
}


def _reset():
	STATE["Contact"] = {
		"C-1": {
			"name": "C-1",
			"is_primary_contact": 1,
			"custom_email": "jane@acme.test",
			"custom_phone_number": "801-555-0101",
			"custom_mobile_number": "801-555-0199",
			"custom_title": "Facilities Director",
		},
		"C-2": {"name": "C-2", "is_primary_contact": 0},
		"C-3": {"name": "C-3", "is_primary_contact": 0},
		# Linked to a different account entirely; must never be touched.
		"C-OTHER": {"name": "C-OTHER", "is_primary_contact": 1},
	}
	STATE["Address"] = {
		"ADDR-1": {"name": "ADDR-1", "is_primary_address": 1},
		"ADDR-2": {"name": "ADDR-2", "is_primary_address": 0},
	}
	STATE["Dynamic Link"] = [
		{"parent": "C-1", "parenttype": "Contact", "link_doctype": "Customer", "link_name": "ACME"},
		{"parent": "C-2", "parenttype": "Contact", "link_doctype": "Customer", "link_name": "ACME"},
		{"parent": "C-3", "parenttype": "Contact", "link_doctype": "Customer", "link_name": "ACME"},
		{"parent": "C-OTHER", "parenttype": "Contact", "link_doctype": "Customer", "link_name": "OTHERCO"},
		{"parent": "ADDR-1", "parenttype": "Address", "link_doctype": "Supplier", "link_name": "SUP-1"},
		{"parent": "ADDR-2", "parenttype": "Address", "link_doctype": "Supplier", "link_name": "SUP-1"},
	]
	STATE["permissions"] = True
	STATE["perm_calls"] = []
	STATE["fields"] = {
		"Project": {
			"primary_contact",
			"primary_contact_email",
			"primary_contact_phone",
			"primary_contact_job_title",
		},
		"Opportunity": {
			"primary_contact",
			"primary_contact_email",
			"primary_contact_phone",
			"primary_contact_job_title",
		},
	}


def _matches(row, filters):
	for key, want in (filters or {}).items():
		if row.get(key) != want:
			return False
	return True


def _install_stub():
	frappe = types.ModuleType("frappe")

	def throw(msg, title=None, exc=None):
		raise StubThrow(str(msg))

	def has_permission(doctype, ptype=None, doc=None, throw=False, **kwargs):
		STATE["perm_calls"].append((doctype, ptype, doc, throw))
		if not STATE["permissions"]:
			if throw:
				raise StubPermissionError(f"no {ptype} on {doctype}")
			return False
		return True

	def get_all(doctype, filters=None, pluck=None, fields=None, **kwargs):
		if doctype != "Dynamic Link":
			raise AssertionError(f"unexpected get_all on {doctype}")
		rows = [r for r in STATE["Dynamic Link"] if _matches(r, filters)]
		if pluck:
			return [r[pluck] for r in rows]
		return rows

	class _DB:
		def set_value(self, doctype, name, field, value, update_modified=True):
			table = STATE[doctype]
			# The endpoints use both forms: a docname, and a {"name": ["in", [...]]}
			# filter dict for the bulk "unset the others" write.
			if isinstance(name, dict):
				spec = name.get("name")
				targets = spec[1] if isinstance(spec, (list, tuple)) else [spec]
			else:
				targets = [name]
			for target in targets:
				if target in table:
					table[target][field] = value

		def get_value(self, doctype, name, field, as_dict=False):
			row = STATE.get(doctype, {}).get(name) or {}
			# apply_primary_contact_details reads four Contact columns at once;
			# every other caller reads a single one.
			if isinstance(field, (list, tuple)):
				if not row:
					return None
				values = {f: row.get(f) for f in field}
				return values if as_dict else [values[f] for f in field]
			return row.get(field)

		def has_column(self, doctype, column):
			return True

		def exists(self, doctype, name):
			return name in STATE.get(doctype, {})

	def get_meta(doctype):
		known = STATE["fields"].get(doctype, set())
		return types.SimpleNamespace(has_field=lambda f: f in known)

	frappe.throw = throw
	frappe.has_permission = has_permission
	frappe.get_all = get_all
	frappe.get_meta = get_meta
	frappe.db = _DB()
	frappe.whitelist = lambda *a, **kw: (lambda fn: fn)
	frappe._ = lambda s: s
	frappe.get_doc = lambda *a, **kw: None
	frappe.delete_doc = lambda *a, **kw: None
	frappe.log_error = lambda *a, **kw: None

	class _DoesNotExist(Exception):
		pass

	frappe.DoesNotExistError = _DoesNotExist
	frappe.logger = lambda *a, **kw: types.SimpleNamespace(info=lambda *a, **kw: None)

	sys.modules["frappe"] = frappe
	sys.modules.pop("erpnext_enhancements.sync_contact", None)


def setUpModule():
	_install_stub()
	global sync_contact
	from erpnext_enhancements import sync_contact as module

	sync_contact = module


class TestPrimaryScoping(unittest.TestCase):
	def setUp(self):
		_reset()

	# -- the regression fence ------------------------------------------------

	def test_a_project_cannot_set_an_account_primary(self):
		"""The bug, stated as a test.

		"Set Primary" on a Project used to pass that Project's **Customer** here, so
		one project-level decision cleared and re-set the account's primary across
		every contact on it.
		"""
		with self.assertRaises(StubThrow):
			sync_contact.set_primary_contact("Project", "PROJ-0001", "C-2")

		# And nothing was written on the way to refusing.
		self.assertEqual(STATE["Contact"]["C-1"]["is_primary_contact"], 1)
		self.assertEqual(STATE["Contact"]["C-2"]["is_primary_contact"], 0)

	def test_an_opportunity_cannot_set_an_account_primary(self):
		"""The incoherent pair specifically.

		Opportunity's discriminator is ``opportunity_from``, not ``party_type``, so the
		widget produced ``("Opportunity", <a Customer id>)``. The "unset the others"
		query matched nothing, so the flag was set without the previous one being
		cleared — which is how several contacts ended up primary for one account.
		"""
		with self.assertRaises(StubThrow):
			sync_contact.set_primary_contact("Opportunity", "ACME", "C-2")
		self.assertEqual(STATE["Contact"]["C-1"]["is_primary_contact"], 1)

	def test_master_project_and_contact_are_not_accounts_either(self):
		for doctype in ("Master Project", "Contact", "Lead", "Prospect"):
			with self.assertRaises(StubThrow):
				sync_contact.set_primary_address(doctype, "WHATEVER", "ADDR-2")
		self.assertEqual(STATE["Address"]["ADDR-1"]["is_primary_address"], 1)

	# -- the paths that must keep working ------------------------------------

	def test_customer_still_sets_its_own_primary_contact(self):
		sync_contact.set_primary_contact("Customer", "ACME", "C-2")

		self.assertEqual(STATE["Contact"]["C-2"]["is_primary_contact"], 1)
		self.assertEqual(STATE["Contact"]["C-1"]["is_primary_contact"], 0)
		self.assertEqual(STATE["Contact"]["C-3"]["is_primary_contact"], 0)

	def test_another_accounts_contacts_are_untouched(self):
		"""Only contacts linked to *this* account are cleared."""
		sync_contact.set_primary_contact("Customer", "ACME", "C-2")
		self.assertEqual(STATE["Contact"]["C-OTHER"]["is_primary_contact"], 1)

	def test_supplier_still_sets_its_own_primary_address(self):
		"""Fences the ordering `api/pickup_routing.py` depends on: it sorts supplier
		addresses by ``is_primary_address desc`` to pick a pick-up stop."""
		sync_contact.set_primary_address("Supplier", "SUP-1", "ADDR-2")

		self.assertEqual(STATE["Address"]["ADDR-2"]["is_primary_address"], 1)
		self.assertEqual(STATE["Address"]["ADDR-1"]["is_primary_address"], 0)

	# -- permissions ---------------------------------------------------------

	def test_write_permission_on_the_account_is_required(self):
		"""Both endpoints were whitelisted with no permission check at all."""
		sync_contact.set_primary_contact("Customer", "ACME", "C-2")
		self.assertIn(("Customer", "write", "ACME", True), STATE["perm_calls"])

	def test_a_permission_failure_writes_nothing(self):
		STATE["permissions"] = False
		with self.assertRaises(StubPermissionError):
			sync_contact.set_primary_contact("Customer", "ACME", "C-2")

		self.assertEqual(STATE["Contact"]["C-1"]["is_primary_contact"], 1)
		self.assertEqual(STATE["Contact"]["C-2"]["is_primary_contact"], 0)


class TestSyncFromMainDocIsDoctypeAgnostic(unittest.TestCase):
	"""Retires the ticket's original diagnosis, permanently.

	TASK-2026-01074 named ``sync_from_main_doc`` as the prime suspect. It only ever
	writes the **Contact**'s three convenience fields and has no Project→Customer edge
	at all; the propagation was entirely client-side.
	"""

	def setUp(self):
		_reset()

	def test_it_returns_without_a_primary_contact(self):
		doc = types.SimpleNamespace(doctype="Project", primary_contact=None)
		self.assertIsNone(sync_contact.sync_from_main_doc(doc, "on_update"))

	def test_it_never_writes_a_party(self):
		written = []
		original = sys.modules["frappe"].get_doc

		def get_doc(doctype, name=None, **kwargs):
			written.append((doctype, name))
			raise sync_contact.frappe.DoesNotExistError()

		sys.modules["frappe"].get_doc = get_doc
		try:
			doc = types.SimpleNamespace(
				doctype="Project",
				primary_contact="C-1",
				primary_contact_job_title="PM",
				is_new=lambda: True,
			)
			sync_contact.sync_from_main_doc(doc, "on_update")
		finally:
			sys.modules["frappe"].get_doc = original

		# The only document it reached for was the Contact.
		self.assertEqual(written, [("Contact", "C-1")])


class _FakeParty:
	"""Just enough of a Project / Opportunity Document for the detail mirror."""

	def __init__(self, doctype="Project", **fields):
		self.doctype = doctype
		self._fields = dict(fields)

	def get(self, key, default=None):
		return self._fields.get(key, default)

	def set(self, key, value):
		self._fields[key] = value


class TestApplyPrimaryContactDetails(unittest.TestCase):
	"""``apply_primary_contact_details`` — the write side, used wherever
	``primary_contact`` is set without a browser (fountain-move intake, and the
	Opportunity -> Project hand-off, TASK-2026-01585)."""

	def setUp(self):
		_reset()

	def test_it_fills_the_three_read_through_fields(self):
		doc = _FakeParty("Project", primary_contact="C-1")
		sync_contact.apply_primary_contact_details(doc)

		self.assertEqual(doc.get("primary_contact_email"), "jane@acme.test")
		self.assertEqual(doc.get("primary_contact_phone"), "801-555-0101")
		self.assertEqual(doc.get("primary_contact_job_title"), "Facilities Director")

	def test_mobile_stands_in_for_a_missing_phone(self):
		STATE["Contact"]["C-1"]["custom_phone_number"] = ""
		doc = _FakeParty("Project", primary_contact="C-1")
		sync_contact.apply_primary_contact_details(doc)
		self.assertEqual(doc.get("primary_contact_phone"), "801-555-0199")

	def test_an_explicit_contact_overrides_the_docs_own(self):
		"""The intake path resolves its Contact before the doc carries the link."""
		doc = _FakeParty("Opportunity")
		sync_contact.apply_primary_contact_details(doc, "C-1")
		self.assertEqual(doc.get("primary_contact_email"), "jane@acme.test")

	def test_no_primary_contact_is_a_no_op(self):
		doc = _FakeParty("Project")
		sync_contact.apply_primary_contact_details(doc)
		self.assertIsNone(doc.get("primary_contact_email"))

	def test_a_field_the_site_lacks_is_skipped(self):
		"""Fresh installs genuinely do not have these three."""
		STATE["fields"]["Project"] = {"primary_contact"}
		doc = _FakeParty("Project", primary_contact="C-1")
		sync_contact.apply_primary_contact_details(doc)
		self.assertIsNone(doc.get("primary_contact_job_title"))

	def test_a_contact_with_nothing_on_it_writes_nothing(self):
		"""The truthiness guard: an empty Contact must not blank what is there."""
		doc = _FakeParty("Project", primary_contact="C-2", primary_contact_job_title="Owner")
		sync_contact.apply_primary_contact_details(doc)
		self.assertEqual(doc.get("primary_contact_job_title"), "Owner")

	# -- the reason it derives instead of copying --------------------------------

	def _run_down_sync(self, doc):
		"""Run the party's ``on_update`` sync against the stubbed Contact table.

		Returns the ``custom_title`` values the Contact was re-saved with — empty
		when the sync decided nothing had changed.
		"""
		saved = []

		class _Contact:
			def __init__(self, row):
				self.__dict__.update(row)
				self.flags = types.SimpleNamespace()

			def save(self):
				saved.append(self.custom_title)

		original = sys.modules["frappe"].get_doc
		sys.modules["frappe"].get_doc = lambda dt, name: _Contact(STATE[dt][name])
		try:
			doc.is_new = lambda: True
			for field in (
				"primary_contact",
				"primary_contact_job_title",
				"primary_contact_phone",
				"primary_contact_email",
			):
				setattr(doc, field, doc.get(field))
			sync_contact.sync_from_main_doc(doc, "on_update")
		finally:
			sys.modules["frappe"].get_doc = original
		return saved

	def test_a_blank_job_title_copied_across_erases_the_contacts(self):
		"""The hazard, stated first — this is what mapping the field straight from
		the Opportunity would do on every deal created before v1.198.0 wired up
		``primary_contact.js`` and left these three empty.

		``sync_from_main_doc``'s job-title branch guards on ``is not None``, not on
		truthiness, so an empty string propagates and wins.
		"""
		doc = _FakeParty("Project", primary_contact="C-1", primary_contact_job_title="")

		self.assertEqual(self._run_down_sync(doc), [""])

	def test_the_mirror_makes_that_round_trip_inert(self):
		"""And the fix: derive the three from the Contact, and the push back down
		finds nothing to change. This is why the hand-off calls the mirror instead
		of adding three more rows to its field-mapping table."""
		doc = _FakeParty("Project", primary_contact="C-1", primary_contact_job_title="")
		sync_contact.apply_primary_contact_details(doc)

		self.assertEqual(self._run_down_sync(doc), [], "the mirror is not inert")

	def test_it_never_touches_the_account_wide_flag(self):
		"""A Project's primary contact is a fact about that Project.

		Carrying it over from the Opportunity must not re-point the Customer's
		account-wide primary — the invariant `_assert_account` enforces on the
		click path, restated for the programmatic one.
		"""
		doc = _FakeParty("Project", primary_contact="C-2")
		sync_contact.apply_primary_contact_details(doc)

		self.assertEqual(STATE["Contact"]["C-1"]["is_primary_contact"], 1)
		self.assertEqual(STATE["Contact"]["C-2"]["is_primary_contact"], 0)


class TestRepairPatchWinner(unittest.TestCase):
	"""The de-duplication rule, isolated from the ORM."""

	def setUp(self):
		from erpnext_enhancements.patches import dedupe_party_primary_flags

		self.pick = dedupe_party_primary_flags._pick_primary

	def test_the_accounts_own_witness_field_wins(self):
		"""``customer_primary_contact`` is ERPNext's, never written by sync_contact, so
		where it is set it is the only uncorrupted record of intent."""
		flagged = [{"record": "C-1"}, {"record": "C-2"}]
		self.assertEqual(self.pick(flagged, "C-2"), "C-2")

	def test_falls_back_to_the_least_recently_modified(self):
		"""Rows arrive ordered by ``modified`` ascending; the strays are the ones the
		buggy widget added on top."""
		flagged = [{"record": "C-1"}, {"record": "C-2"}]
		self.assertEqual(self.pick(flagged, None), "C-1")

	def test_a_witness_pointing_outside_the_candidates_is_ignored(self):
		flagged = [{"record": "C-1"}, {"record": "C-2"}]
		self.assertEqual(self.pick(flagged, "C-NOT-FLAGGED"), "C-1")


if __name__ == "__main__":
	unittest.main()
