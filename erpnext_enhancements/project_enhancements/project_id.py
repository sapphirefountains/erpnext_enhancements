"""Keep ``Project.custom_project_id`` equal to the Project's own name (v1.816.4).

``custom_project_id`` ("Project ID", read-only Data) is the Project's name copied into a field the
Report Builder can show: the saved report "Active Projects w/ TL and PM" lists it. Nothing in Python
or JS reads it back. Until now only ``public/js/project.js`` filled it, from the browser, and only on
a form that someone then saved, so 451 of 671 Projects on production had it blank and that report
showed blanks. These hooks make the server own it:

* ``ensure_project_id`` -- ``validate``. Every save of a Project carries its own name.
* ``ensure_on_insert`` -- ``after_insert``. ``validate`` never runs for a Project created with
  ``flags.ignore_validate`` (``create_project_from_opportunity_background`` does exactly that, and
  creates most Projects), so those arrive blank without this.
* ``follow_rename`` -- ``after_rename``. A renamed Project's ID follows at once, without waiting for
  someone to open and save it.

The two that write after the row exists use a bound-parameter ``UPDATE`` and do not touch
``modified`` / ``modified_by``: the ID is a mirror of the name, not an edit anyone made, so it must not
read as "last edited by" the user whose save or rename triggered it, add a Version row, or run any
other hook. ``frappe.db.set_value`` is avoided because it bumps ``modified`` unless told not to and
this stays one explicit statement.

Defensive on purpose: ``doc_events`` fire during ERPNext's own test bootstrap, before this app's Custom
Fields exist, so each hook returns quietly when the column is absent, and none can raise (a failure
logs one Error Log row, never the Project's name, and the save or rename goes on).

The one-shot backfill for Projects already blank is ``patches/backfill_project_id``.
"""

import frappe

FIELD = "custom_project_id"


def _column_exists():
	"""has_column takes a DOCTYPE, and RAISES on an unknown table; False is not a possible return."""
	try:
		return bool(frappe.db.has_column("Project", FIELD))
	except Exception:
		return False


def _log(title):
	try:
		frappe.log_error(title=title, message=frappe.get_traceback())
	except Exception:
		pass


def ensure_project_id(doc, method=None, *args, **kwargs):
	"""validate: a saved Project carries its own name. A new document that has no name yet is skipped."""
	try:
		name = getattr(doc, "name", None)
		if not name or (getattr(doc, FIELD, None) or "") == name:
			return
		if not _column_exists():
			return
		doc.set(FIELD, name)
	except Exception:
		_log("Project ID: validate hook failed")


def _write(name):
	"""Set the ID on the stored row without touching modified / modified_by."""
	frappe.db.sql("UPDATE `tabProject` SET `custom_project_id` = %s WHERE `name` = %s", (name, name))


def ensure_on_insert(doc, method=None, *args, **kwargs):
	"""after_insert: covers the path where validate was skipped (flags.ignore_validate)."""
	try:
		name = getattr(doc, "name", None)
		if not name or (getattr(doc, FIELD, None) or "") == name:
			return
		if not _column_exists():
			return
		_write(name)
		doc.set(FIELD, name)
	except Exception:
		_log("Project ID: after_insert hook failed")


def follow_rename(doc, method=None, old=None, new=None, merge=False, *args, **kwargs):
	"""after_rename: the ID follows the new name. `new` is authoritative; the document may still be the old one."""
	try:
		name = new or getattr(doc, "name", None)
		if not name or not _column_exists():
			return
		_write(name)
		doc.set(FIELD, name)
	except Exception:
		_log("Project ID: after_rename hook failed")
