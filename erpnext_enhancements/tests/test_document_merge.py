"""Integration tests for the generic ``document_merge`` engine.

Uses Project as the merge subject and Task (``Task.project`` is a plain Link to
Project) as a referrer, plus a Comment as a soft reference. Asserts that merging
a loser into a survivor repoints the hard link, moves the comment, backfills a
blank survivor field from the loser, and deletes the loser — and that the guard
rails (self-merge, feature switch) hold.

(The Project fixtures mirror ``test_project_merge``: they force ``status =
'Active'`` and bypass ``_validate_selects`` because the framework otherwise forces
the now-removed "Open" status.)
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_enhancements.document_merge import get_merge_preview, perform_merge

SETTINGS = "ERPNext Enhancements Settings"


def _make_project(name, **values):
	doc = frappe.new_doc("Project")
	doc.project_name = name
	doc.company = "_Test Company Merge"
	doc.status = "Active"
	for k, v in values.items():
		doc.set(k, v)
	doc._validate_selects = lambda: None
	doc.insert(ignore_permissions=True)
	if doc.status != "Active":
		frappe.db.set_value("Project", doc.name, "status", "Active")
		doc.reload()
	return doc


class TestDocumentMerge(FrappeTestCase):
	def setUp(self):
		super().setUp()
		# Enable the feature for the duration of the test (default OFF in prod).
		self._prev_flag = frappe.db.get_single_value(SETTINGS, "document_merge_enabled")
		frappe.db.set_single_value(SETTINGS, "document_merge_enabled", 1)

		if not frappe.db.exists("Company", "_Test Company Merge"):
			frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": "_Test Company Merge",
					"default_currency": "USD",
					"country": "United States",
				}
			).insert(ignore_permissions=True)

		self.survivor = _make_project("DocMerge Survivor")
		self.loser = _make_project("DocMerge Loser", notes="<p>loser note</p>")

	def tearDown(self):
		frappe.db.set_single_value(SETTINGS, "document_merge_enabled", self._prev_flag)
		super().tearDown()

	def test_hard_link_repointed_and_loser_deleted(self):
		task = frappe.get_doc(
			{
				"doctype": "Task",
				"subject": "DocMerge Task",
				"project": self.loser.name,
			}
		).insert(ignore_permissions=True)

		perform_merge("Project", self.survivor.name, self.loser.name)

		self.assertFalse(frappe.db.exists("Project", self.loser.name))
		self.assertEqual(frappe.db.get_value("Task", task.name, "project"), self.survivor.name)

	def test_blank_field_backfilled_from_loser(self):
		# Survivor has no notes; loser does → survivor should inherit them.
		self.assertFalse(self.survivor.notes)
		perform_merge("Project", self.survivor.name, self.loser.name)
		self.survivor.reload()
		self.assertIn("loser note", self.survivor.notes or "")

	def test_existing_survivor_value_is_kept(self):
		self.survivor.db_set("notes", "<p>survivor note</p>")
		self.survivor.reload()
		perform_merge("Project", self.survivor.name, self.loser.name)
		self.survivor.reload()
		# Survivor wins: its value is kept, the loser's is discarded.
		self.assertIn("survivor note", self.survivor.notes or "")
		self.assertNotIn("loser note", self.survivor.notes or "")

	def test_comment_soft_reference_moves(self):
		self.loser.add_comment("Comment", "a note on the loser")
		perform_merge("Project", self.survivor.name, self.loser.name)
		moved = frappe.get_all(
			"Comment",
			filters={
				"reference_doctype": "Project",
				"reference_name": self.survivor.name,
				"content": ("like", "%a note on the loser%"),
			},
		)
		self.assertTrue(moved)

	def test_preview_lists_the_hard_reference(self):
		frappe.get_doc(
			{"doctype": "Task", "subject": "Preview Task", "project": self.loser.name}
		).insert(ignore_permissions=True)
		preview = get_merge_preview("Project", self.survivor.name, self.loser.name)
		self.assertGreaterEqual(preview["reference_total"], 1)
		task_refs = [r for r in preview["hard_references"] if r["doctype"] == "Task"]
		self.assertTrue(task_refs)

	def test_self_merge_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			perform_merge("Project", self.survivor.name, self.survivor.name)

	def test_feature_switch_off_is_refused(self):
		frappe.db.set_single_value(SETTINGS, "document_merge_enabled", 0)
		with self.assertRaises(frappe.ValidationError):
			perform_merge("Project", self.survivor.name, self.loser.name)
