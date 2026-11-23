"""Fill ``Project.custom_project_id`` with the Project's name wherever it is not already that (v1.816.4).

``custom_project_id`` ("Project ID") mirrors the Project's name for the Report Builder report "Active
Projects w/ TL and PM". Only the browser used to fill it, and only when someone saved the form, so on
2026-11-13 production held 671 Projects: 451 blank, 220 equal to the name, none different. From
v1.816.4 ``project_enhancements/project_id.py`` keeps it true on every save, insert and rename; this
patch brings the existing rows up to the same rule.

**Keyed on the rule, not on emptiness:** a row is changed when ``custom_project_id`` is NULL or is not
byte-for-byte its ``name``. Today that is the 451 blank rows and nothing else, but a stale value (after a
rename) is the same fact and is corrected too. The comparison is ``BINARY``: MariaDB's default collation
is case-insensitive and PAD SPACE, so a plain ``<>`` says ``'prj-1'`` equals ``'PRJ-1'`` and ``'PRJ-1 '``
equals ``'PRJ-1'``, and would leave exactly those rows wrong while reporting clean.

**Writes production data on migrate:** one field of every Project that differs (451 today) and nothing
else. It is one ``UPDATE`` with no hooks, no ``doc.save`` and no ``frappe.db.set_value`` because
``modified`` / ``modified_by`` must not move: the ID is a mirror of the name, not an edit, and a bump would
make 451 Projects read "last edited" at deploy time, re-sort every list by modified, and (``doc.save``)
run validate and on_update hooks, write Version rows, notify and sync calendars. The statement has no
literal values, so there is nothing to bind.

Never aborts ``bench migrate`` (a patch that raises is a failed deploy): the column is checked first
(``has_column`` takes a DocType and raises on an unknown table, so it is wrapped), the work is wrapped,
and a failure logs one Error Log row and returns. Safe twice: after one run nothing differs. Logs counts
only, never names.
"""

import frappe

# `<>` under the default collation ignores case and trailing spaces; BINARY compares bytes.
_DIFFERS = "`custom_project_id` IS NULL OR BINARY `custom_project_id` <> BINARY `name`"
COUNT_SQL = f"SELECT COUNT(*) FROM `tabProject` WHERE {_DIFFERS}"
UPDATE_SQL = f"UPDATE `tabProject` SET `custom_project_id` = `name` WHERE {_DIFFERS}"


def execute():
	try:
		try:
			has_column = bool(frappe.db.has_column("Project", "custom_project_id"))
		except Exception:
			has_column = False
		if not has_column:
			print("backfill_project_id: Project.custom_project_id is not on this site; nothing to do")
			return

		before = int(frappe.db.sql(COUNT_SQL)[0][0] or 0)
		if not before:
			print("backfill_project_id: every Project already carries its own name; nothing to do")
			return
		frappe.db.sql(UPDATE_SQL)
		after = int(frappe.db.sql(COUNT_SQL)[0][0] or 0)
		print(f"backfill_project_id: set the Project ID on {before - after} of {before} Projects")
	except Exception:
		try:
			frappe.log_error(title="backfill_project_id patch failed", message=frappe.get_traceback())
		except Exception:
			pass
