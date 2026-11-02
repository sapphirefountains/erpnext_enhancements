# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Retire the nine schema-1 child DocTypes of Water Feature Design (v1.771.0, phase H).

The Fountain Designer's design model (``model_json``, ``docs/fountain-designer/PLAN.md`` 3.3)
replaced Water Feature Basin, Tier, Nozzle, Pipe Segment, Pump, Electrical Load, Equipment and
Calc Result and Water Venue Fixture. Water Feature Design stopped declaring them in v1.768.0 (phase
E), and ``convert_water_feature_designs_to_model`` copied every design's rows into its model then.
This release removes their folders; this patch removes what the folders leave behind.

-------------------------------------------------------------------------------------
Three framework facts, read from frappe v16.53.0 (prod's version on 2026-11-01)
-------------------------------------------------------------------------------------

1. **Post-model-sync patches run before ``remove_orphan_doctypes``** (``migrate.py``:
   ``run_schema_updates`` then ``post_schema_updates``), so the DocType rows are still there when
   this runs. The sweep would delete them anyway; this does it first so a site where the sweep did
   not fire is not left with nine DocTypes whose forms raise ``ImportError``.
2. **Nothing in the framework drops the table.** ``delete_doc("DocType", ...)`` clears the
   ``tabDocType`` row, its DocFields and DocPerms and the table-field Custom Fields pointing at it,
   and ``remove_orphan_doctypes``' docstring says "Deleting the entry doesn't delete any data". So
   the ``DROP TABLE`` is this patch's own, as in ``delete_chat_module``.
3. **A Custom Field or Property Setter ON a retired DocType is not cleared by ``delete_doc``.**
   Production has none (checked 2026-11-01); they are deleted here anyway so a site that had one is
   not left with a customization of a doctype that no longer exists.

-------------------------------------------------------------------------------------
What it deletes, and what it refuses to
-------------------------------------------------------------------------------------

What the conversion copied decides what may go. ``convert_water_feature_designs_to_model`` read
five tables into the model (``CHILD_TABLES``: Basin, Nozzle, Tier, Pipe Segment, Pump), and Calc
Result is the old engine's output, which every derive writes again into ``results_json``. It never
read Electrical Load, Venue Fixture or Equipment, so a row in one of those three is nowhere else.

Per DocType, the stored rows are grouped by parent and judged one design at a time:

* **converted** (the five tables and Calc Result): the parent is a Water Feature Design whose
  ``model_json`` is schema 2. Its rows are in the model (or rebuilt by the derive); they are deleted.
* **orphan**: the parent design no longer exists. Nothing can read them; they are deleted.
* **unconverted**: the parent design's ``model_json`` is missing or still schema 1, or the parent
  is not a Water Feature Design at all. These rows are the only copy of that design: KEPT.
* **not copied**: any row of Electrical Load, Venue Fixture or Equipment, whatever its parent.
  KEPT: those three tables are dropped only when they are empty.

A table is dropped only once it is empty. Its DocType row, and any Custom Field or Property Setter
on it, go only once the table is gone: a table that keeps rows keeps them too, and the Error Log
names the designs. ``remove_orphan_doctypes`` deletes a DocType whose folder is gone later in the
same migrate whatever this patch does, so the conversion patch reads a retired table with SQL when
its DocType is missing: converting a design and running both patches again finishes the job.

Production on 2026-11-01 (read-only through the ERPNext MCP): 13 rows in five of the nine tables,
every one a child of WFD-2026-209315, whose ``model_json`` is schema 2. Basin 1, Nozzle 1, Pipe
Segment 1, Pump 2, Calc Result 8; Tier, Electrical Load, Venue Fixture and Equipment are empty. So
every table and every DocType goes.

-------------------------------------------------------------------------------------
Mechanics
-------------------------------------------------------------------------------------

``post_model_sync``. **It never raises**: a patch that raises aborts ``bench migrate``, which on this
repo is the deploy. Each DocType is its own try, every outcome prints one line to the migrate log
and every failure goes to the Error Log. Safe to run twice: the second run finds no tables and no
DocTypes. ``report()`` returns the same per-DocType judgement without changing anything (the dry
run the tests drive).
"""

import json

import frappe

DESIGN = "Water Feature Design"
LOG_TITLE = "retire_water_feature_child_doctypes"

#: Listed literally rather than read from the filesystem: the filesystem no longer has them.
RETIRED = (
	"Water Feature Basin",
	"Water Feature Tier",
	"Water Feature Nozzle",
	"Water Feature Pipe Segment",
	"Water Feature Pump",
	"Water Feature Electrical Load",
	"Water Venue Fixture",
	"Water Feature Equipment",
	"Water Feature Calc Result",
)

#: The tables the v1.768.0 conversion never read into the model: any row in one is its only copy.
NOT_COPIED_TABLES = ("Water Feature Electrical Load", "Water Venue Fixture", "Water Feature Equipment")

CONVERTED = "converted"
ORPHAN = "orphan"
UNCONVERTED = "unconverted"
NOT_COPIED = "not copied"
DELETABLE = (CONVERTED, ORPHAN)
#: The verdicts that keep rows; a parent judged differently in two groups keeps the kept one.
KEPT = (NOT_COPIED, UNCONVERTED)


def execute():
	for doctype in RETIRED:
		try:
			outcome = retire(doctype)
		except Exception:
			_log(f"{doctype}: the retirement raised; nothing more was done for it.", trace=True)
			continue
		print(f"{LOG_TITLE}: {doctype}: {_describe(outcome)}")
	try:
		frappe.clear_cache()
	except Exception:
		pass


def report():
	"""The per-DocType judgement, changing nothing: ``[plan(doctype) for each retired DocType]``."""
	return [plan(doctype) for doctype in RETIRED]


def plan(doctype):
	"""What ``retire`` would do to one DocType, read only.

	``{doctype, table, rows, parents: {parent: verdict}, delete_rows, keep_rows, drop_table}``:
	``rows`` is the table's row count, ``delete_rows`` and ``keep_rows`` split it by the verdicts
	above, and ``drop_table`` is True only when every row may go.
	"""
	out = {
		"doctype": doctype,
		"table": False,
		"rows": 0,
		"parents": {},
		"delete_rows": 0,
		"keep_rows": 0,
		"drop_table": False,
	}
	if not frappe.db.table_exists(doctype, cached=False):
		return out
	out["table"] = True
	groups = frappe.db.sql(
		f"select parent, parenttype, count(*) as n from `tab{doctype}` group by parent, parenttype",
		as_dict=True,
	)
	# Judge each parent once: a name that appears under two parenttypes takes the stricter verdict,
	# and every row of it is counted (and later deleted, or kept) under that one verdict.
	counts = {}
	for g in groups:
		key = g.get("parent") or ""
		verdict = NOT_COPIED if doctype in NOT_COPIED_TABLES else _verdict(g.get("parent"), g.get("parenttype"))
		if out["parents"].get(key) not in KEPT:
			out["parents"][key] = verdict
		counts[key] = counts.get(key, 0) + int(g.get("n") or 0)
	for key, n in counts.items():
		out["rows"] += n
		out["delete_rows" if out["parents"][key] in DELETABLE else "keep_rows"] += n
	out["drop_table"] = out["keep_rows"] == 0
	return out


def _verdict(parent, parenttype):
	if parenttype and parenttype != DESIGN:
		return UNCONVERTED
	if not parent or not frappe.db.exists(DESIGN, parent):
		return ORPHAN
	return CONVERTED if _is_schema_2(frappe.db.get_value(DESIGN, parent, "model_json")) else UNCONVERTED


def _is_schema_2(raw):
	if isinstance(raw, dict):
		data = raw
	else:
		try:
			data = json.loads(raw or "")
		except (TypeError, ValueError):
			return False
	return isinstance(data, dict) and data.get("schema") == 2


def retire(doctype):
	"""Delete the deletable rows, drop the table once it is empty, then remove the DocType and any
	customization on it. Returns the plan with what was done added (``dropped``, ``deleted_doctype``)."""
	out = plan(doctype)
	out["dropped"] = False
	if out["table"]:
		doomed = [p for p, v in out["parents"].items() if v in DELETABLE]
		if out["drop_table"]:
			frappe.db.sql_ddl(f"drop table if exists `tab{doctype}`")
			out["dropped"] = True
		elif doomed:
			# "" stands for a row with no parent at all, which `in` never matches: delete those apart.
			named = tuple(p for p in doomed if p)
			if named:
				frappe.db.sql(f"delete from `tab{doctype}` where parent in %(parents)s", {"parents": named})
			if "" in doomed:
				frappe.db.sql(f"delete from `tab{doctype}` where coalesce(parent, '') = ''")
		if out["keep_rows"]:
			kept = sorted(p or "(no parent)" for p, v in out["parents"].items() if v in KEPT)
			if doctype in NOT_COPIED_TABLES:
				why = (
					"which the v1.768.0 conversion never copied into a model. Move what they hold into each "
					"design by hand (they stay readable by SQL)"
				)
			else:
				why = (
					"the only copy of each. Convert each design (bench execute "
					"erpnext_enhancements.patches.convert_water_feature_designs_to_model.execute)"
				)
			_log(
				f"{doctype}: kept the table, its DocType and {out['keep_rows']} row(s) of design(s) "
				f"{', '.join(kept)}, {why}, then run this patch again (bench execute "
				"erpnext_enhancements.patches.retire_water_feature_child_doctypes.execute)."
			)
	# The DocType and any customization on it go only once its table is gone.
	out["deleted_doctype"] = False
	if not frappe.db.table_exists(doctype, cached=False):
		for dt, field in (("Custom Field", "dt"), ("Property Setter", "doc_type")):
			frappe.db.delete(dt, {field: doctype})
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc("DocType", doctype, force=True, ignore_missing=True)
			out["deleted_doctype"] = True
	frappe.db.commit()
	return out


def _describe(o):
	if not o["table"]:
		table = "no table"
	elif o["dropped"]:
		table = f"dropped its table ({o['rows']} row(s), none kept)"
	else:
		table = f"deleted {o['delete_rows']} row(s), KEPT the table with {o['keep_rows']} (see the Error Log)"
	if o["deleted_doctype"]:
		doctype = "DocType deleted"
	elif o["table"] and not o["dropped"]:
		doctype = "DocType kept with its table"
	else:
		doctype = "DocType already gone"
	return f"{table}; {doctype}"


def _log(message, trace=False):
	try:
		print(f"{LOG_TITLE}: {message}")
		frappe.log_error(title=LOG_TITLE, message=message + ("\n\n" + frappe.get_traceback() if trace else ""))
	except Exception:
		pass
