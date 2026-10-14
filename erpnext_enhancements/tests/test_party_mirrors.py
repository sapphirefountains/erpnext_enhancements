"""Bench-free tests for the Party mirror rule and its dry run (form kit P3.2, TASK-2026-02755).

``crm_enhancements/party_mirrors.py`` keeps Opportunity's hidden ``contact_person`` (what
*Create > Quotation* copies) equal to the visible ``primary_contact`` on every save, and the
*Party Mirror Preview* report lists the stored rows that still disagree. Held here:

* the rule: the field changed in this save wins, cleared included; a hidden field changed by code
  is copied up only when it holds a value; with nothing changed the visible field wins, and a
  hidden-only value is copied up;
* the hook never raises, skips fields the meta does not have yet (ERPNext's own test bootstrap),
  never copies a link to a record that does not exist, and leaves the address pair alone
  (``customer_address`` is the Billing Address since v1.633.0);
* hooks.py runs it on Opportunity ``before_save`` ahead of
  ``sync_contact.refresh_primary_contact_copies``;
* the report writes nothing, compares in Python (a trailing space is a difference, which a SQL
  ``<>`` under PAD SPACE would miss), proposes exactly what the save rule would do, and leaves a
  row alone when the contact it would copy no longer exists;
* no patch changes the data yet: Nik approves the dry run first.

A minimal ``frappe`` stub is installed in ``setUpModule`` and removed afterwards.

Run: python -m unittest erpnext_enhancements.tests.test_party_mirrors -v
"""

import ast
import importlib
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
RULE = "erpnext_enhancements.crm_enhancements.party_mirrors"
REPORT = "erpnext_enhancements.crm_enhancements.report.party_mirror_preview.party_mirror_preview"
REPORT_DIR = APP / "crm_enhancements" / "report" / "party_mirror_preview"

mirrors = report = None
FRAPPE = None
_saved = {}


class _Dict(dict):
	__getattr__ = dict.get


class DF(_Dict):
	pass


class FakeMeta:
	def __init__(self, fields):
		self.fields = {name: DF(fieldname=name, **df) for name, df in fields.items()}

	def has_field(self, fieldname):
		return fieldname in self.fields

	def get_field(self, fieldname):
		return self.fields.get(fieldname)


OPPORTUNITY_FIELDS = {
	"primary_contact": {"fieldtype": "Link", "options": "Contact"},
	"contact_person": {"fieldtype": "Link", "options": "Contact"},
	"primary_address": {"fieldtype": "Link", "options": "Address"},
	"customer_address": {"fieldtype": "Link", "options": "Address"},
	"title": {"fieldtype": "Data"},
	"opportunity_from": {"fieldtype": "Link", "options": "DocType"},
	"party_name": {"fieldtype": "Dynamic Link", "options": "opportunity_from"},
	"customer_name": {"fieldtype": "Data"},
}

CONTACTS = {
	"Ann Lee-Example Ponds": "Ann Lee",
	"Bo Diaz-Example Ponds": "Bo Diaz",
	"Ann Lee-Example Ponds ": "Ann Lee (spaced)",
}


class FakeDB:
	def __init__(self, rows=None, contacts=None, columns=None):
		self.rows = rows or []
		self.contacts = dict(CONTACTS if contacts is None else contacts)
		self.columns = set(OPPORTUNITY_FIELDS) | {"name"} if columns is None else set(columns)
		self.queries = []
		self.writes = []

	def exists(self, doctype, name):
		return doctype == "Contact" and name in self.contacts

	def table_exists(self, doctype):
		return doctype == "Opportunity"

	def has_column(self, doctype, column):
		if doctype != "Opportunity":
			raise Exception(f"TableMissingError: tab{doctype}")
		return column in self.columns

	def sql(self, query, values=None, as_dict=False):
		self.queries.append(query)
		m = re.match(r"select (.+) from `tab([^`]+)` where (.+)$", query)
		assert m, query
		wanted = re.findall(r"`([^`]+)`", m.group(1))
		tested = re.findall(r"length\(`([^`]+)`\) > 0", m.group(3))
		assert len(tested) == 2, "the emptiness test must be LENGTH(col) > 0 on both fields"
		assert "<>" not in query and "!=" not in query, "the comparison happens in Python"
		return [
			_Dict({c: row.get(c) for c in wanted})
			for row in self.rows
			if any(len(row.get(c) or "") > 0 for c in tested)
		]

	def set_value(self, *args, **kwargs):
		self.writes.append(args)

	def commit(self):
		self.writes.append("commit")


def _get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
	assert doctype == "Contact"
	names = filters["name"][1]
	found = [n for n in names if n in FRAPPE.db.contacts]
	if pluck:
		return found
	return [_Dict(name=n, full_name=FRAPPE.db.contacts[n]) for n in found]


class FakeDoc:
	def __init__(self, values, before=None, new=False, doctype="Opportunity"):
		self.doctype = doctype
		self.name = values.get("name", "CRM-OPP-2026-00001")
		self.__dict__.update(values)
		self._before = before
		self._new = new

	def is_new(self):
		return self._new

	def get_doc_before_save(self):
		return None if self._before is None else _Dict(self._before)

	def get(self, key):
		return self.__dict__.get(key)

	def set(self, key, value):
		self.__dict__[key] = value


def setUpModule():
	global mirrors, report, FRAPPE
	for name in ("frappe", RULE, REPORT, "erpnext_enhancements.form_kit.blocks"):
		_saved[name] = sys.modules.get(name)
		sys.modules.pop(name, None)
	FRAPPE = types.ModuleType("frappe")
	FRAPPE._ = lambda text, *a, **k: text
	FRAPPE._dict = _Dict
	FRAPPE.db = FakeDB()
	FRAPPE.logged = []
	FRAPPE.log_error = lambda *a, **k: FRAPPE.logged.append(k or a)
	FRAPPE.get_meta = lambda doctype: FakeMeta(OPPORTUNITY_FIELDS if doctype == "Opportunity" else {})
	FRAPPE.get_all = _get_all
	sys.modules["frappe"] = FRAPPE
	mirrors = importlib.import_module(RULE)
	report = importlib.import_module(REPORT)


def tearDownModule():
	for name, module in _saved.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


ANN, BO = "Ann Lee-Example Ponds", "Bo Diaz-Example Ponds"


class DecideTest(unittest.TestCase):
	def test_agreeing_pairs_are_left_alone(self):
		self.assertEqual(mirrors.decide(ANN, ANN), (ANN, ANN, None))
		self.assertEqual(mirrors.decide("", None), ("", "", None))
		self.assertEqual(mirrors.decide(ANN, ANN, (BO, BO)), (ANN, ANN, None))

	def test_with_no_save_the_visible_field_wins(self):
		self.assertEqual(mirrors.decide(ANN, BO), (ANN, ANN, mirrors.COPY_DOWN))
		self.assertEqual(mirrors.decide(ANN, ""), (ANN, ANN, mirrors.COPY_DOWN))

	def test_a_hidden_only_value_is_copied_up(self):
		self.assertEqual(mirrors.decide("", BO), (BO, BO, mirrors.COPY_UP))
		self.assertEqual(mirrors.decide(None, BO, ("", BO)), (BO, BO, mirrors.COPY_UP))

	def test_the_visible_field_changed_in_this_save_wins(self):
		self.assertEqual(mirrors.decide(BO, ANN, (ANN, ANN)), (BO, BO, mirrors.COPY_DOWN))
		# Both changed (the Desk sets contact_person when the account changes): the person's pick.
		self.assertEqual(mirrors.decide(BO, "Cy-Other", (ANN, ANN)), (BO, BO, mirrors.COPY_DOWN))

	def test_clearing_the_visible_field_clears_the_hidden_one(self):
		self.assertEqual(mirrors.decide("", ANN, (ANN, ANN)), ("", "", mirrors.CLEARED))

	def test_a_hidden_field_changed_by_code_is_copied_up(self):
		self.assertEqual(mirrors.decide(ANN, BO, (ANN, ANN)), (BO, BO, mirrors.COPY_UP))

	def test_a_hidden_field_cleared_by_code_never_clears_the_visible_one(self):
		self.assertEqual(mirrors.decide(ANN, "", (ANN, ANN)), (ANN, ANN, mirrors.COPY_DOWN))

	def test_a_trailing_space_is_a_difference(self):
		self.assertEqual(mirrors.decide(ANN, ANN + " ")[2], mirrors.COPY_DOWN)

	def test_plan_stored_is_decide_with_no_save(self):
		for pair in ((ANN, BO), ("", BO), (ANN, ""), (ANN, ANN)):
			self.assertEqual(mirrors.plan_stored(*pair), mirrors.decide(*pair, None))


class HookTest(unittest.TestCase):
	def setUp(self):
		FRAPPE.db = FakeDB()
		FRAPPE.logged = []
		FRAPPE.get_meta = lambda doctype: FakeMeta(OPPORTUNITY_FIELDS if doctype == "Opportunity" else {})

	def test_a_new_record_from_a_mapping_gets_its_contact_shown(self):
		doc = FakeDoc({"primary_contact": None, "contact_person": BO}, new=True)
		mirrors.keep_in_step(doc)
		self.assertEqual((doc.primary_contact, doc.contact_person), (BO, BO))

	def test_a_new_record_keeps_the_visible_pick(self):
		doc = FakeDoc({"primary_contact": ANN, "contact_person": BO}, new=True)
		mirrors.keep_in_step(doc)
		self.assertEqual((doc.primary_contact, doc.contact_person), (ANN, ANN))

	def test_a_saved_record_follows_the_field_that_changed(self):
		doc = FakeDoc(
			{"primary_contact": ANN, "contact_person": BO},
			before={"primary_contact": ANN, "contact_person": ANN},
		)
		mirrors.keep_in_step(doc)
		self.assertEqual((doc.primary_contact, doc.contact_person), (BO, BO))
		doc = FakeDoc(
			{"primary_contact": "", "contact_person": ANN},
			before={"primary_contact": ANN, "contact_person": ANN},
		)
		mirrors.keep_in_step(doc)
		self.assertEqual((doc.primary_contact, doc.contact_person), ("", ""))

	def test_a_saved_record_with_no_stored_copy_is_treated_as_unchanged(self):
		doc = FakeDoc({"primary_contact": ANN, "contact_person": BO}, before=None)
		mirrors.keep_in_step(doc)
		self.assertEqual((doc.primary_contact, doc.contact_person), (ANN, ANN))

	def test_the_address_pair_is_left_alone(self):
		doc = FakeDoc(
			{
				"primary_contact": ANN,
				"contact_person": ANN,
				"primary_address": "Job Site-Shipping",
				"customer_address": "Head Office-Billing",
			},
			new=True,
		)
		mirrors.keep_in_step(doc)
		self.assertEqual(doc.primary_address, "Job Site-Shipping")
		self.assertEqual(doc.customer_address, "Head Office-Billing")

	def test_a_contact_that_no_longer_exists_is_never_copied(self):
		doc = FakeDoc({"primary_contact": "", "contact_person": "Gone-Example"}, new=True)
		mirrors.keep_in_step(doc)
		self.assertEqual((doc.primary_contact, doc.contact_person), ("", "Gone-Example"))

	def test_fields_the_meta_lacks_are_skipped(self):
		FRAPPE.get_meta = lambda doctype: FakeMeta({"contact_person": OPPORTUNITY_FIELDS["contact_person"]})
		doc = FakeDoc({"contact_person": BO}, new=True)
		mirrors.keep_in_step(doc)
		self.assertFalse(hasattr(doc, "primary_contact"))
		self.assertEqual(doc.contact_person, BO)

	def test_a_doctype_with_no_mirror_is_untouched(self):
		calls = []
		FRAPPE.get_meta = lambda doctype: calls.append(doctype)
		doc = FakeDoc({"primary_contact": ANN}, new=True, doctype="Project")
		mirrors.keep_in_step(doc)
		self.assertEqual(calls, [])

	def test_it_never_raises(self):
		def boom(doctype):
			raise RuntimeError("meta unavailable")

		FRAPPE.get_meta = boom
		mirrors.keep_in_step(FakeDoc({"primary_contact": ANN, "contact_person": BO}, new=True))
		self.assertEqual(len(FRAPPE.logged), 1)
		mirrors.keep_in_step(object())


class HookWiringTest(unittest.TestCase):
	def _before_save(self):
		tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
		for node in tree.body:
			if isinstance(node, ast.Assign) and any(
				getattr(t, "id", None) == "doc_events" for t in node.targets
			):
				events = ast.literal_eval(node.value)
				return events["Opportunity"]["before_save"]
		self.fail("doc_events not found")

	def test_it_runs_on_opportunity_before_save_ahead_of_the_contact_copies(self):
		hooks = self._before_save()
		rule = f"{RULE}.keep_in_step"
		copies = "erpnext_enhancements.sync_contact.refresh_primary_contact_copies"
		self.assertIn(rule, hooks)
		self.assertLess(hooks.index(rule), hooks.index(copies))

	def test_no_patch_rewrites_the_stored_rows_yet(self):
		"""Nik approves the Party Mirror Preview first (as with P2.4's dry run)."""
		patches = (APP / "patches.txt").read_text(encoding="utf-8")
		self.assertNotIn("party_mirror", patches)
		for path in (APP / "patches").glob("*.py"):
			self.assertNotIn("party_mirrors", path.read_text(encoding="utf-8"), path.name)


class PreviewReportTest(unittest.TestCase):
	def setUp(self):
		FRAPPE.get_meta = lambda doctype: FakeMeta(OPPORTUNITY_FIELDS if doctype == "Opportunity" else {})
		FRAPPE.db = FakeDB(
			rows=[
				{"name": "OPP-1", "title": "Pond", "primary_contact": ANN, "contact_person": ANN},
				{"name": "OPP-2", "title": "Wall", "primary_contact": ANN, "contact_person": BO},
				{"name": "OPP-3", "title": "Spray", "primary_contact": "", "contact_person": BO},
				{"name": "OPP-4", "title": "Koi", "primary_contact": BO, "contact_person": None},
				{"name": "OPP-5", "title": "Gone", "primary_contact": None, "contact_person": "Gone-Example"},
				{"name": "OPP-6", "title": "Space", "primary_contact": ANN + " ", "contact_person": ANN},
				{"name": "OPP-7", "title": "Empty", "primary_contact": None, "contact_person": None},
			]
		)

	def _rows(self, status=None):
		columns, data, message, chart, summary = report.execute({"status": status} if status else {})
		return columns, {row["record"]: row for row in data}, summary

	def test_it_lists_exactly_the_rows_that_disagree(self):
		_, rows, summary = self._rows()
		self.assertEqual(set(rows), {"OPP-2", "OPP-3", "OPP-4", "OPP-5", "OPP-6"})
		counts = {s["label"]: s["value"] for s in summary}
		self.assertEqual(counts[report.STATUS_AGREE], 1)
		self.assertEqual(counts[report.STATUS_DIFFER], 2)
		self.assertEqual(counts[report.STATUS_HIDDEN_ONLY], 1)
		self.assertEqual(counts[report.STATUS_VISIBLE_ONLY], 1)
		self.assertEqual(counts[report.STATUS_MISSING], 1)

	def test_it_proposes_what_the_save_rule_would_do(self):
		_, rows, _ = self._rows()
		self.assertEqual((rows["OPP-2"]["proposed_visible"], rows["OPP-2"]["proposed_hidden"]), (ANN, ANN))
		self.assertEqual((rows["OPP-3"]["proposed_visible"], rows["OPP-3"]["proposed_hidden"]), (BO, BO))
		self.assertEqual((rows["OPP-4"]["proposed_visible"], rows["OPP-4"]["proposed_hidden"]), (BO, BO))
		for name in ("OPP-2", "OPP-3", "OPP-4"):
			row = rows[name]
			self.assertEqual(
				mirrors.plan_stored(row["visible"], row["hidden"])[:2],
				(row["proposed_visible"], row["proposed_hidden"]),
			)

	def test_a_trailing_space_is_listed(self):
		_, rows, _ = self._rows()
		self.assertEqual(rows["OPP-6"]["status"], report.STATUS_DIFFER)

	def test_a_missing_contact_is_left_alone(self):
		_, rows, _ = self._rows()
		row = rows["OPP-5"]
		self.assertEqual(row["status"], report.STATUS_MISSING)
		self.assertEqual((row["proposed_visible"], row["proposed_hidden"]), ("", "Gone-Example"))

	def test_contacts_show_their_names(self):
		_, rows, _ = self._rows()
		self.assertEqual(rows["OPP-2"]["hidden_label"], "Bo Diaz")
		self.assertEqual(rows["OPP-5"]["hidden_label"], "Gone-Example")

	def test_the_status_filter_narrows_without_changing_the_counts(self):
		_, rows, summary = self._rows(report.STATUS_HIDDEN_ONLY)
		self.assertEqual(set(rows), {"OPP-3"})
		self.assertEqual({s["label"]: s["value"] for s in summary}[report.STATUS_DIFFER], 2)

	def test_it_writes_nothing(self):
		self._rows()
		self.assertEqual(FRAPPE.db.writes, [])
		for query in FRAPPE.db.queries:
			self.assertTrue(query.lower().startswith("select "), query)

	def test_a_missing_column_is_skipped_not_raised(self):
		FRAPPE.db = FakeDB(rows=[], columns={"name", "contact_person"})
		_, rows, _ = self._rows()
		self.assertEqual(rows, {})
		self.assertEqual(FRAPPE.db.queries, [])

	def test_columns_use_the_report_face(self):
		columns, _, _ = self._rows()
		by_name = {c["fieldname"]: c for c in columns}
		for fieldname in ("record", "party", "visible", "hidden", "proposed_visible", "proposed_hidden"):
			with self.subTest(column=fieldname):
				self.assertEqual(by_name[fieldname].get("sf_face"), "party")
				self.assertIn(by_name[fieldname]["sf_label"], {"title", "party_label", f"{fieldname}_label"})

	def test_the_report_has_no_apply_button(self):
		code = (REPORT_DIR / "party_mirror_preview.js").read_text(encoding="utf-8")
		self.assertNotIn("frappe.call", code)
		self.assertNotIn("add_inner_button", code)


if __name__ == "__main__":
	unittest.main()
