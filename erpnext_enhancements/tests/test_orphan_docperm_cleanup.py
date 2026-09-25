# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The v1.537.2 ``delete_orphan_docperms`` patch: what it deletes, and what it must never touch.
Bench-free.

Frappe 16.35.0's File list permission hook calls ``frappe.get_meta`` on every doctype named by
the user's DocPerm / Custom DocPerm rows. Production held 161 Custom DocPerm rows for 40
HRMS/Helpdesk doctypes that are not installed, so every non-Administrator File list query failed
("DocType Attendance Request not found"). The patch deletes the rows whose parent matches no
DocType. The property worth guarding is the other direction: it is a DELETE on the site's
permission tables, so a row for a DocType that exists, even spelt differently, must survive.
Also: nothing happens when nothing is orphaned, and the patch never raises, because a patch that
raises aborts ``bench migrate``, which is the deploy.

Installs its own ``frappe`` stub in ``setUpModule``, which is why it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_orphan_docperm_cleanup
"""

import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
PATCHES_TXT = APP / "patches.txt"

STATE = {}
patch = None


def _reset(doctypes=("DocType", "DocPerm", "Custom DocPerm", "Opportunity", "Material Request")):
	STATE.clear()
	STATE.update(
		{
			"doctypes": list(doctypes),
			"custom": [],  # Custom DocPerm rows: {"parent": ...}
			"docperm": [],  # DocPerm rows: {"parent": ..., "parenttype": ...}
			"deletes": [],
			"commits": 0,
			"rollbacks": 0,
			"clear_cache": 0,
			"errors": [],
			"fail_sql": None,  # substring of a query that should raise
			"fail_delete": set(),  # tables whose delete should raise
			"fail_clear_cache": False,
		}
	)


def _custom(*parents):
	STATE["custom"].extend({"parent": p} for p in parents)


def _docperm(*parents, parenttype="DocType"):
	STATE["docperm"].extend({"parent": p, "parenttype": parenttype} for p in parents)


def _matches(row, filters):
	for key, cond in filters.items():
		if isinstance(cond, tuple):
			op, values = cond
			assert op == "in", op
			if row.get(key) not in values:
				return False
		elif row.get(key) != cond:
			return False
	return True


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")

	def sql(query, values=()):
		if STATE["fail_sql"] and STATE["fail_sql"] in query:
			raise RuntimeError("database gone")
		if "`tabDocType`" in query:
			return [(name,) for name in STATE["doctypes"]]
		if "`tabCustom DocPerm`" in query:
			return [(row["parent"],) for row in STATE["custom"]]
		if "`tabDocPerm`" in query:
			assert "parenttype = %s" in query and values == ("DocType",), (query, values)
			return [(row["parent"],) for row in STATE["docperm"] if row["parenttype"] == values[0]]
		raise AssertionError(query)

	def delete(doctype, filters=None):
		if doctype in STATE["fail_delete"]:
			raise RuntimeError("delete refused")
		assert isinstance(filters, dict) and filters, filters
		STATE["deletes"].append((doctype, filters))
		table = {"Custom DocPerm": "custom", "DocPerm": "docperm"}[doctype]
		STATE[table] = [row for row in STATE[table] if not _matches(row, filters)]

	def commit():
		STATE["commits"] += 1

	def rollback():
		STATE["rollbacks"] += 1

	def clear_cache(*args, **kwargs):
		if STATE["fail_clear_cache"]:
			raise RuntimeError("redis gone")
		STATE["clear_cache"] += 1

	frappe.db = types.SimpleNamespace(sql=sql, delete=delete, commit=commit, rollback=rollback)
	frappe.clear_cache = clear_cache
	frappe.log_error = lambda *a, **k: STATE["errors"].append(k.get("title") or a)
	sys.modules["frappe"] = frappe


def setUpModule():
	global patch
	_install_frappe_stub()
	sys.modules.pop("erpnext_enhancements.patches.delete_orphan_docperms", None)
	from erpnext_enhancements.patches import delete_orphan_docperms as p

	patch = p


def _parents(table):
	return sorted(row["parent"] for row in STATE[table])


class DeletesOrphansTest(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_rows_for_missing_doctypes_are_deleted(self):
		_custom("Attendance Request", "Attendance Request", "HD Ticket", "Salary Slip")
		result = patch.delete_orphan_docperms()
		self.assertEqual(result, {"Custom DocPerm": 4})
		self.assertEqual(_parents("custom"), [])

	def test_rows_for_existing_doctypes_are_untouched(self):
		_custom("Opportunity", "Material Request", "Material Request", "Attendance Request", "HD Ticket")
		patch.delete_orphan_docperms()
		self.assertEqual(_parents("custom"), ["Material Request", "Material Request", "Opportunity"])

	def test_the_delete_names_only_the_orphans(self):
		_custom("Opportunity", "HD Ticket", "Attendance Request", "HD Ticket")
		patch.delete_orphan_docperms()
		self.assertEqual(
			STATE["deletes"], [("Custom DocPerm", {"parent": ("in", ["Attendance Request", "HD Ticket"])})]
		)

	def test_orphan_docperm_rows_are_deleted_scoped_to_doctype_parents(self):
		_docperm("Opportunity", "Expense Claim", "Expense Claim")
		_docperm("Expense Claim", parenttype="Something Else")
		result = patch.delete_orphan_docperms()
		self.assertEqual(result, {"DocPerm": 2})
		self.assertEqual(
			STATE["deletes"], [("DocPerm", {"parenttype": "DocType", "parent": ("in", ["Expense Claim"])})]
		)
		self.assertEqual(
			sorted((r["parent"], r["parenttype"]) for r in STATE["docperm"]),
			[("Expense Claim", "Something Else"), ("Opportunity", "DocType")],
		)

	def test_both_tables_are_cleaned_in_one_run(self):
		_custom("HD Ticket", "Opportunity")
		_docperm("Leave Application", "Material Request")
		result = patch.delete_orphan_docperms()
		self.assertEqual(result, {"Custom DocPerm": 1, "DocPerm": 1})
		self.assertEqual(_parents("custom"), ["Opportunity"])
		self.assertEqual(_parents("docperm"), ["Material Request"])


class KeepsWhatItShouldTest(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_nothing_is_deleted_when_nothing_is_orphaned(self):
		_custom("Opportunity", "Material Request")
		_docperm("Opportunity")
		result = patch.delete_orphan_docperms()
		self.assertEqual(result, {})
		self.assertEqual(STATE["deletes"], [])
		self.assertEqual(STATE["commits"], 0)
		self.assertEqual(STATE["clear_cache"], 0)

	def test_a_differently_spelt_existing_doctype_is_kept(self):
		# MariaDB's utf8mb4_unicode_ci / PAD SPACE treats these as the same name, so the DELETE's
		# IN match would reach them too. The patch must fold at least as far as the database.
		_reset(doctypes=("DocType", "Opportunity", "Café Order"))
		_custom("opportunity", "Opportunity ", "OPPORTUNITY", "Cafe Order", "cafe order")
		result = patch.delete_orphan_docperms()
		self.assertEqual(result, {})
		self.assertEqual(len(STATE["custom"]), 5)

	def test_blank_parents_are_left_alone(self):
		_custom("", None, "Opportunity")
		result = patch.delete_orphan_docperms()
		self.assertEqual(result, {})
		self.assertEqual(STATE["deletes"], [])

	def test_an_incomplete_doctype_read_deletes_nothing(self):
		# Without DocType in the list the read is broken, and every row would look orphaned.
		_reset(doctypes=())
		_custom("Opportunity", "Material Request")
		self.assertEqual(patch.delete_orphan_docperms(), {})
		self.assertEqual(STATE["deletes"], [])
		self.assertEqual(len(STATE["custom"]), 2)


class ClearCacheTest(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_clear_cache_runs_once_when_something_was_deleted(self):
		_custom("HD Ticket")
		_docperm("Leave Application")
		patch.delete_orphan_docperms()
		self.assertEqual(STATE["clear_cache"], 1)

	def test_clear_cache_does_not_run_when_nothing_was_deleted(self):
		_custom("Opportunity")
		patch.delete_orphan_docperms()
		self.assertEqual(STATE["clear_cache"], 0)

	def test_safe_twice(self):
		_custom("HD Ticket", "Attendance Request", "Opportunity")
		patch.execute()
		patch.execute()
		self.assertEqual(len(STATE["deletes"]), 1)
		self.assertEqual(STATE["clear_cache"], 1)
		self.assertEqual(_parents("custom"), ["Opportunity"])


class NeverRaisesTest(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_a_failed_doctype_read_is_logged_not_raised(self):
		_custom("HD Ticket")
		STATE["fail_sql"] = "`tabDocType`"
		self.assertIsNone(patch.execute())
		self.assertEqual(STATE["errors"], ["delete_orphan_docperms: failed"])
		self.assertEqual(STATE["deletes"], [])

	def test_a_failed_perm_read_is_logged_and_the_other_table_still_runs(self):
		_custom("HD Ticket")
		_docperm("Leave Application")
		STATE["fail_sql"] = "`tabCustom DocPerm`"
		self.assertIsNone(patch.execute())
		self.assertEqual(STATE["errors"], ["delete_orphan_docperms: Custom DocPerm cleanup failed"])
		self.assertEqual(_parents("custom"), ["HD Ticket"])
		self.assertEqual(_parents("docperm"), [])

	def test_a_failed_delete_rolls_back_and_is_logged(self):
		_custom("HD Ticket")
		STATE["fail_delete"] = {"Custom DocPerm"}
		self.assertIsNone(patch.execute())
		self.assertEqual(STATE["rollbacks"], 1)
		self.assertEqual(STATE["commits"], 0)
		self.assertEqual(STATE["clear_cache"], 0)
		self.assertEqual(STATE["errors"], ["delete_orphan_docperms: Custom DocPerm cleanup failed"])

	def test_a_failed_clear_cache_is_logged_not_raised(self):
		_custom("HD Ticket")
		STATE["fail_clear_cache"] = True
		self.assertIsNone(patch.execute())
		self.assertEqual(_parents("custom"), [])
		self.assertEqual(STATE["errors"], ["delete_orphan_docperms: clear_cache failed"])


class RegistrationTest(unittest.TestCase):
	def test_registered_post_model_sync(self):
		text = PATCHES_TXT.read_text(encoding="utf-8")
		post = text[text.index("[post_model_sync]") :]
		self.assertIn("\nerpnext_enhancements.patches.delete_orphan_docperms\n", post)

	def test_no_sql_function_strings_in_get_all(self):
		# Frappe 16 rejects fields=["count(name) as n"] and nothing bench-free can see it; the patch
		# sidesteps the question by not calling get_all/get_list at all.
		source = (APP / "patches" / "delete_orphan_docperms.py").read_text(encoding="utf-8")
		code = source.split('"""', 2)[2]
		self.assertNotIn("get_all(", code)
		self.assertNotIn("get_list(", code)


if __name__ == "__main__":
	unittest.main()
