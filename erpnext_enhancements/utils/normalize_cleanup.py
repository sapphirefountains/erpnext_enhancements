# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The one-time cleanup (form kit P2-C): store every approved value in its one form.

Since P2-B every *new or edited* value in a registered field is stored the one way on save
(``utils/normalize_hook.py``). This rewrites the values that were already there. Nik approved
it on 2026-10-13 from the Data Normalization Preview report (TASK-2026-02751), with no
exceptions, so **what it writes is exactly the report's "Will change" rows**: :func:`plan`
calls the report's own ``collect()`` and nothing else decides. Run by the patch
``patches/normalize_stored_values.py``; ``docs/data-normalization.md`` is the prose version.

**What it leaves alone** (each one is also the report's rule, or stricter):

* every value the report lists as "Can't normalize — needs a person": it stays exactly as
  typed (84 of them on 2026-10-13);
* a submitted or cancelled record, and every Single — the report gives neither a write target;
* a value whose normalized form would be **blank** (one made only of spaces). None existed on
  prod when this was written, but a blank in a mandatory field such as an address line would
  make the record unsaveable, so it is counted and left for a person;
* a value somebody changed between the read and the write: it is re-read just before writing
  and compared exactly, in Python, so MariaDB's PAD SPACE cannot call ``"GA "`` and ``"GA"``
  equal. Their edit wins, and it was normalized on save anyway.

**How it writes.** ``frappe.db.set_value(..., update_modified=False)`` — no controller, no
validation, so no record can fail half-way through the run (the reason the plan gave), and
``modified`` keeps its meaning: nobody's open form reports a conflict and "recently edited"
lists do not fill with records nobody touched. ``set_value`` clears the cached document; for
a child row the parent's cache is cleared too. It commits once per table.

**Before it writes anything** it saves an undo file: a private ``File`` attached to nothing,
so only Administrator and server code can read it (``File.has_permission``), holding every
planned change's before and after. If that file cannot be saved, nothing is written.
:func:`undo` puts the before values back, again only where the value is still the one this
wrote.

**Afterwards** it leaves the counts per field — no values — as a comment on the report, so the
outcome is visible in the Desk and in SQL rather than only in a deploy log.

Never raises: a patch that raises aborts ``bench migrate``, which on this repo is the deploy.
Safe twice: the rules are stable (normalizing a stored value changes nothing), so a second run
plans nothing. The kill switch (``disable_value_normalization`` in site_config) stops it.
"""

import json

import frappe

from erpnext_enhancements.enhancements_core.report.data_normalization_preview import (
	data_normalization_preview as preview,
)
from erpnext_enhancements.utils import normalize_hook

#: The report the cleanup was approved from; its summary comment is left on it.
REPORT = "Data Normalization Preview"

#: Undo files are named for the run, e.g. ``data-normalization-undo-20261013-180501.json``.
UNDO_PREFIX = "data-normalization-undo-"

_LOG_TITLE = "Data normalization cleanup"


def plan():
	"""The report's "Will change" rows that the cleanup may write, in report order."""
	rows, _counts = preview.collect(targets=True)
	return [row for row in rows if row["status"] == preview.STATUS_CHANGE and row.get("_target")]


def run():
	"""Apply :func:`plan`. Returns a summary of counts; never raises."""
	summary = _summary()
	try:
		if normalize_hook.is_disabled():
			summary["stopped"] = f"{normalize_hook.KILL_SWITCH} is set in site_config"
			return summary
		changes = []
		for row in plan():
			if row["normalized"] == "":
				summary["left_blank"] += 1
			else:
				changes.append(row)
		summary["planned"] = len(changes)
		if not changes:
			_record(summary)
			return summary
		undo_file = _save_undo_file(changes)
		if not undo_file:
			summary["stopped"] = "the undo file could not be saved, so nothing was written"
			_log(summary["stopped"])
			return summary
		summary["undo_file"] = undo_file
		for _table, group in _by_table(changes):
			for row in group:
				_apply(row, summary)
			frappe.db.commit()
		_record(summary)
	except Exception as exc:
		summary["stopped"] = f"stopped part-way: {type(exc).__name__}"
		_log(summary["stopped"])
	return summary


def undo(file_name):
	"""Put back every value an undo file recorded, where it still holds what the cleanup wrote.

	For an emergency: ``bench --site <site> execute
	erpnext_enhancements.utils.normalize_cleanup.undo --args "['<file name>']"``.
	A value edited since the cleanup is left as it is. Returns counts; never raises.
	"""
	result = {"restored": 0, "edited_since": 0, "failed": 0, "unreadable": False}
	try:
		file_doc = frappe.get_doc("File", {"file_name": file_name})
		changes = json.loads(file_doc.get_content())["changes"]
	except Exception as exc:
		_log(f"undo could not read {file_name}: {type(exc).__name__}")
		result["unreadable"] = True
		return result
	for change in changes:
		try:
			table, name, fieldname = change["doctype"], change["name"], change["field"]
			if frappe.db.get_value(table, name, fieldname) != change["after"]:
				result["edited_since"] += 1
				continue
			frappe.db.set_value(table, name, fieldname, change["before"], update_modified=False)
			if change["parent_doctype"] != table:
				frappe.clear_document_cache(change["parent_doctype"], change["parent"])
			result["restored"] += 1
		except Exception:
			result["failed"] += 1
	try:
		frappe.db.commit()
	except Exception as exc:
		_log(f"undo commit: {type(exc).__name__}")
	return result


def _summary():
	return {
		"planned": 0,
		"written": 0,
		"edited_since": 0,
		"left_blank": 0,
		"failed": 0,
		"by_field": {},
		"undo_file": None,
		"stopped": None,
	}


def _by_table(changes):
	groups = {}
	for row in changes:
		groups.setdefault(row["_target"][0], []).append(row)
	return groups.items()


def _apply(row, summary):
	table, name, fieldname, parent_doctype, parent = row["_target"]
	try:
		# Exact comparison in Python: SQL equality would ignore trailing spaces (PAD SPACE).
		if frappe.db.get_value(table, name, fieldname) != row["current"]:
			summary["edited_since"] += 1
			return
		frappe.db.set_value(table, name, fieldname, row["normalized"], update_modified=False)
		if parent_doctype != table:
			frappe.clear_document_cache(parent_doctype, parent)
		summary["written"] += 1
		key = f"{table}.{fieldname}"
		summary["by_field"][key] = summary["by_field"].get(key, 0) + 1
	except Exception as exc:
		summary["failed"] += 1
		if summary["failed"] == 1:
			_log(f"{table}.{fieldname}: {type(exc).__name__}; later failures are only counted")


def _save_undo_file(changes):
	"""Save every planned change's before and after; returns the file name, or None."""
	try:
		stamp = frappe.utils.now_datetime().strftime("%Y%m%d-%H%M%S")
		file_name = f"{UNDO_PREFIX}{stamp}.json"
		content = {
			"written_by": "erpnext_enhancements.utils.normalize_cleanup.run",
			"undo_with": "erpnext_enhancements.utils.normalize_cleanup.undo",
			"changes": [
				{
					"doctype": row["_target"][0],
					"name": row["_target"][1],
					"field": row["_target"][2],
					"parent_doctype": row["_target"][3],
					"parent": row["_target"][4],
					"before": row["current"],
					"after": row["normalized"],
				}
				for row in changes
			],
		}
		saved = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"is_private": 1,
				"content": json.dumps(content, indent=1, ensure_ascii=False),
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		# The name as stored, in case File made it unique: undo() looks the file up by it.
		return getattr(saved, "file_name", None) or file_name
	except Exception as exc:
		_log(f"undo file: {type(exc).__name__}")
		return None


def _record(summary):
	"""The outcome as a comment on the report: counts per field, never a value."""
	lines = [
		f"<b>Normalization cleanup ran.</b> {summary['written']} values rewritten of "
		f"{summary['planned']} planned.",
	]
	if summary["edited_since"]:
		lines.append(f"{summary['edited_since']} were edited while it ran and kept their new value.")
	if summary["left_blank"]:
		lines.append(f"{summary['left_blank']} would have become blank and were left for a person.")
	if summary["failed"]:
		lines.append(f"{summary['failed']} could not be written (see the Error Log).")
	if summary["undo_file"]:
		lines.append(f"Undo file (private): {summary['undo_file']}.")
	if summary["by_field"]:
		lines.append("<br>".join(f"{key}: {count}" for key, count in sorted(summary["by_field"].items())))
	try:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Info",
				"reference_doctype": "Report",
				"reference_name": REPORT,
				"content": "<br>".join(lines),
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception as exc:
		_log(f"summary comment: {type(exc).__name__}")


def _log(message):
	"""An Error Log naming no value. Never raises."""
	try:
		frappe.log_error(title=_LOG_TITLE, message=message)
	except Exception:
		pass
