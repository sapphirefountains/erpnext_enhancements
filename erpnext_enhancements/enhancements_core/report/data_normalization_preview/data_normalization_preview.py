# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Data Normalization Preview — what the phone/email/website/address cleanup *would* do.

A dry run, and only a dry run: this report writes nothing. It walks every field in
``utils.normalize_fields.FIELDS``, runs each stored value through ``utils.normalize``, and lists
what would change and what no rule can fix without a person. The product owner approves the
cleanup (form kit P2-C) from this list, so it has to show exactly what the cleanup would touch —
which is why it applies the cleanup's own rules rather than its own:

* **a submitted or cancelled record is left alone** (``docstatus`` 1 or 2). Those rows are not
  listed; the summary counts them, so "left alone" is visible rather than silent;
* **a Single is read with ``get_single_value``**, never through ``tabSingles`` by hand (CLAUDE.md:
  ``db.get_value("Singles")`` cannot succeed). Singles are reported only — the bulk cleanup never
  writes one, because writing one field of a never-saved Single blanks its other defaults;
* **a child row links to its parent**, the document a person would open to fix it.

**One SELECT per doctype**, every registered column of it at once, so ~3,000 Contacts cost one
query and no ``get_doc``. Identifiers come only from the registry — never from a filter — and are
backtick-quoted; the doctype filter only narrows which registry entries are walked. A table or
column that does not exist on this site (an app not installed, a custom field not yet migrated)
is skipped rather than raised: ``has_column`` takes a doctype and raises on an unknown table, so
``table_exists`` goes first and ``has_column`` sits in a ``try``.

The emptiness test is ``LENGTH(col) > 0``, not ``col != ''``: under MariaDB's PAD SPACE collation
a value of only spaces compares equal to ``''``, and a whitespace-only value is exactly one the
cleanup would change.

**Proposed fixes (P2-C, part two).** A value no rule can read, and one the cleanup would have
blanked, also gets a *proposed fix* from ``utils/normalize_fixes.py`` when one of its rules is
sure: the column shows it with the reason, and the "Has a proposed fix" filter lists only those.
A System Manager ticks the rows to apply and presses *Apply checked fixes*; the server works every
fix out again (``normalize_cleanup.apply_fixes``), so the browser chooses rows and never values.
The rows carry ``fix_key`` (table, row name, field) for that; nothing else about a target is sent.
"""

import json

import frappe
from frappe import _

from erpnext_enhancements.utils import normalize, normalize_fixes
from erpnext_enhancements.utils import normalize_fields as registry

STATUS_CHANGE = "Will change"
STATUS_STUCK = "Can't normalize — needs a person"
STATUS_CLEAN = "Already clean"
STATUS_SUBMITTED = "Submitted — left alone"

FILTER_ATTENTION = "Will change + Can't normalize"
FILTER_EVERYTHING = "Everything, including Already clean"
FILTER_FIXES = "Has a proposed fix"

_WANTED = {
	FILTER_ATTENTION: {STATUS_CHANGE, STATUS_STUCK},
	STATUS_CHANGE: {STATUS_CHANGE},
	STATUS_STUCK: {STATUS_STUCK},
	FILTER_EVERYTHING: {STATUS_CHANGE, STATUS_STUCK, STATUS_CLEAN},
	FILTER_FIXES: {STATUS_CHANGE, STATUS_STUCK},
}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	choice = filters.get("status") or FILTER_ATTENTION
	wanted = _WANTED.get(choice, _WANTED[FILTER_ATTENTION])
	rows, counts = collect(doctype=filters.get("doctype"), kind=filters.get("kind"))
	data = [row for row in rows if row["status"] in wanted]
	if choice == FILTER_FIXES:
		data = [row for row in data if row.get("fix_key")]
	return get_columns(), data, _message(), None, _report_summary(counts)


def get_columns():
	return [
		{
			"label": _("DocType"),
			"fieldname": "doctype",
			"fieldtype": "Link",
			"options": "DocType",
			"width": 150,
		},
		{
			"label": _("Record"),
			"fieldname": "record",
			"fieldtype": "Dynamic Link",
			"options": "doctype",
			"width": 180,
		},
		{"label": _("Field"), "fieldname": "field", "fieldtype": "Data", "width": 220},
		{"label": _("Kind"), "fieldname": "kind", "fieldtype": "Data", "width": 80},
		{"label": _("Current value"), "fieldname": "current", "fieldtype": "Data", "width": 230},
		{"label": _("Normalized value"), "fieldname": "normalized", "fieldtype": "Data", "width": 230},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 210},
		{"label": _("Reason"), "fieldname": "reason", "fieldtype": "Data", "width": 300},
		{"label": _("Proposed fix"), "fieldname": "proposed", "fieldtype": "Data", "width": 260},
		{"label": _("Why"), "fieldname": "fix_why", "fieldtype": "Data", "width": 300},
	]


def collect(doctype=None, kind=None, targets=False):
	"""Every registered value with its verdict, and the counts per status.

	``doctype``/``kind`` narrow the walk to registry entries; a doctype the registry does not
	cover yields nothing. Rows come back sorted by doctype, field and record.

	``targets=True`` is for the cleanup (``utils/normalize_cleanup.py``), which writes exactly
	what this report lists: each table row then also carries ``_target``, the
	``(table, row name, fieldname, parent doctype, parent name)`` the value lives in. A Single's
	row never gets one, because the bulk cleanup never writes a Single. The report itself
	never asks for them, so what it sends the browser is unchanged.
	"""
	counts = {STATUS_CHANGE: 0, STATUS_STUCK: 0, STATUS_CLEAN: 0, STATUS_SUBMITTED: 0}
	rows = []
	doctype = (doctype or "").strip()
	kind = (kind or "").strip()
	for dt, fields in registry.FIELDS.items():
		if doctype and dt != doctype:
			continue
		wanted = {f: k for f, k in fields.items() if not kind or k == kind}
		if not wanted:
			continue
		if dt in registry.SINGLES:
			_scan_single(dt, wanted, rows)
		else:
			_scan_table(dt, wanted, rows, counts, targets)
	for row in rows:
		counts[row["status"]] += 1
	rows.sort(key=lambda r: (r["doctype"], r["field"], r["record"]))
	return rows, counts


def _scan_table(dt, fields, rows, counts, targets=False):
	if not frappe.db.table_exists(dt):
		return
	present = [f for f in fields if _has_column(dt, f)]
	if not present:
		return
	child = dt in registry.CHILD_TABLES
	country_field = registry.COUNTRY_FIELD.get(dt)
	if country_field and not (
		any(fields[f] in normalize.COUNTRY_KINDS for f in present) and _has_column(dt, country_field)
	):
		country_field = None

	columns = ["name", "docstatus"]
	if child:
		columns += ["parenttype", "parent"]
	if country_field and country_field not in present:
		columns.append(country_field)
	# What a proposed fix reads beside the value (normalize_fixes.EXTRA_COLUMNS).
	columns += [
		c for c in normalize_fixes.EXTRA_COLUMNS.get(dt, ()) if c not in present and _has_column(dt, c)
	]
	columns += present
	query = "select {} from {} where {}".format(
		", ".join(_quote(c) for c in columns),
		_quote("tab" + dt),
		" or ".join(f"length({_quote(f)}) > 0" for f in present),
	)
	for record in frappe.db.sql(query, as_dict=True):
		if record.get("docstatus") in (1, 2):
			counts[STATUS_SUBMITTED] += 1
			continue
		country = record.get(country_field) if country_field else None
		parent_dt = (record.get("parenttype") or dt) if child else dt
		name = (record.get("parent") or record.get("name")) if child else record.get("name")
		for fieldname in present:
			label = f"{dt}.{fieldname}" if child else fieldname
			target = (dt, record.get("name"), fieldname, parent_dt, name)
			_add(
				rows,
				parent_dt,
				name,
				label,
				fields[fieldname],
				record.get(fieldname),
				country,
				target if targets else None,
				fix_source=(record, target),
			)


def _scan_single(dt, fields, rows):
	if not frappe.db.exists("DocType", dt):
		return
	try:
		meta = frappe.get_meta(dt)
	except Exception:
		return
	for fieldname, kind in fields.items():
		if not meta.has_field(fieldname):
			continue
		try:
			raw = frappe.db.get_single_value(dt, fieldname)
		except Exception:
			continue
		_add(rows, dt, dt, fieldname, kind, raw, None)


def _add(rows, doctype, record, field, kind, raw, country, target=None, fix_source=None):
	if raw is None or raw == "":
		return
	result = normalize.normalize(kind, raw, country)
	if not result.ok:
		status = STATUS_STUCK
	elif result.changed:
		status = STATUS_CHANGE
	else:
		status = STATUS_CLEAN
	fix = None
	if fix_source and (status == STATUS_STUCK or (status == STATUS_CHANGE and result.value == "")):
		context, (table, row_name, fieldname, _parent_dt, _parent) = fix_source
		fix = normalize_fixes.propose(kind, raw, context, table, fieldname)
	row = {
		"doctype": doctype,
		"record": record,
		"field": field,
		"kind": kind,
		"current": str(raw),
		"normalized": result.value if result.ok else "",
		"status": status,
		"reason": result.reason,
	}
	if target:
		row["_target"] = target
	if fix:
		row["proposed"] = _fix_display(fix)
		row["fix_why"] = fix.why
		row["fix_key"] = json.dumps([table, row_name, fieldname])
		if target:
			row["_fix"] = fix
			row["_also_before"] = {f: context.get(f) for f in fix.also}
	rows.append(row)


def _fix_display(fix):
	shown = [fix.value if fix.value else _("(blank)")]
	shown += [f"{field} = {value}" for field, value in fix.also.items()]
	return "; ".join(shown)


def _has_column(dt, fieldname):
	try:
		return bool(frappe.db.has_column(dt, fieldname))
	except Exception:
		return False


def _quote(identifier):
	"""Backtick-quote a registry identifier. A backtick in one is a registry bug, so it raises."""
	if "`" in identifier:
		raise ValueError(f"unsafe identifier {identifier!r}")
	return f"`{identifier}`"


def _message():
	return "<br>".join(
		[
			_(
				"<b>A dry run: showing this writes nothing.</b> Every phone, email, website and address field "
				"in the normalization registry, run through the rules in docs/data-normalization.md."
			),
			_(
				"<i>Will change</i> is what the cleanup would rewrite. <i>Can't normalize</i> is left "
				"exactly as typed and needs a person. Submitted and cancelled records are never touched, "
				"and settings pages (Singles) are reported only — fix those in the settings form."
			),
			_(
				"<i>Proposed fix</i> is a suggestion for a value no rule can read. Nothing is applied "
				"until you tick its row and press <b>Apply checked fixes</b>; an undo file is saved first."
			),
		]
	)


def _report_summary(counts):
	return [
		{
			"label": _("Will change"),
			"value": counts[STATUS_CHANGE],
			"datatype": "Int",
			"indicator": "Blue",
		},
		{
			"label": _("Can't normalize — needs a person"),
			"value": counts[STATUS_STUCK],
			"datatype": "Int",
			"indicator": "Orange" if counts[STATUS_STUCK] else "Green",
		},
		{"label": _("Already clean"), "value": counts[STATUS_CLEAN], "datatype": "Int", "indicator": "Green"},
		{
			"label": _("Submitted records — left alone"),
			"value": counts[STATUS_SUBMITTED],
			"datatype": "Int",
			"indicator": "Grey",
		},
	]
