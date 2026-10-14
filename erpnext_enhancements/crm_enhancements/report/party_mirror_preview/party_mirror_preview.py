# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Party Mirror Preview — the records whose visible and hidden party fields disagree.

A dry run, and only a dry run: this report writes nothing (form kit P3.2, TASK-2026-02755). On
Opportunity the form shows Primary Contact and hides ERPNext's Contact Person, which is the one
*Create > Quotation* copies. Since v1.669.0 a new record, and a save that changes either field,
leaves them equal (``crm_enhancements/party_mirrors.py``). A save that changes neither leaves a
pair that already disagreed exactly as it is, so the rows saved before that, and rows written past
``save()`` (``db.set_value``, an import), wait here. This lists each of them with both values as
stored and what the fix would make them (the visible contact, else the hidden one copied up), so
Nik can approve the fix before
anything is written, the way the Data Normalization Preview came before the P2-C cleanup.

Which pairs are walked is the Party block's ``mirrors`` (``form_kit/blocks.py``); what each row
would become is ``party_mirrors.plan_stored``, the save rule itself, so the list is exactly what a
fix would touch. A row the rule would point at a Contact that no longer exists is listed as left
alone.

**Compared in Python, never in SQL.** Under MariaDB's PAD SPACE collation ``a <> b`` ignores
trailing spaces, so a SQL comparison would call ``"Ann Lee"`` and ``"Ann Lee "`` the same contact
and report clean on a row that is not. The query only finds rows where either field is set
(``LENGTH(col) > 0``); the comparison happens here. One SELECT per pair, plus one read of the
Contacts' names.

**Raw SQL, so User Permissions on Opportunity do not narrow it.** Acceptable for who can open it:
the report's roles are System Manager and Sales Manager, who see every Opportunity anyway, and it
shows only names (record, account, contacts), no amounts.
"""

import frappe
from frappe import _

from erpnext_enhancements.crm_enhancements import party_mirrors
from erpnext_enhancements.form_kit import blocks

STATUS_DIFFER = "Both set, different"
STATUS_HIDDEN_ONLY = "Only the hidden field set"
STATUS_VISIBLE_ONLY = "Only the visible field set"
STATUS_MISSING = "Left alone: that record no longer exists"
STATUS_AGREE = "Agree"

FILTER_ALL = "Every difference"
_WANTED = {
	FILTER_ALL: {STATUS_DIFFER, STATUS_HIDDEN_ONLY, STATUS_VISIBLE_ONLY, STATUS_MISSING},
	STATUS_DIFFER: {STATUS_DIFFER},
	STATUS_HIDDEN_ONLY: {STATUS_HIDDEN_ONLY},
	STATUS_VISIBLE_ONLY: {STATUS_VISIBLE_ONLY},
}

#: The columns that say which record and whose it is, per doctype (each read only if present).
CONTEXT = {
	"Opportunity": {
		"title": "title",
		"party_type": "opportunity_from",
		"party": "party_name",
		"party_label": "customer_name",
	},
}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	wanted = _WANTED.get(filters.get("status") or FILTER_ALL, _WANTED[FILTER_ALL])
	rows, counts = collect()
	data = [row for row in rows if row["status"] in wanted]
	return get_columns(), data, _message(), None, _report_summary(counts)


def mirror_doctypes():
	"""``{doctype: {visible: hidden}}`` for every doctype the registry gives a mirror."""
	out = {}
	for block in blocks.BLOCKS.values():
		for doctype in block.get("doctypes", {}):
			mirrors = blocks.mirrors_for(doctype)
			if mirrors:
				out[doctype] = mirrors
	return out


def collect():
	"""Every stored pair that disagrees, with the proposed values, and the counts per status."""
	counts = {
		s: 0 for s in (STATUS_DIFFER, STATUS_HIDDEN_ONLY, STATUS_VISIBLE_ONLY, STATUS_MISSING, STATUS_AGREE)
	}
	rows = []
	for doctype, mirrors in sorted(mirror_doctypes().items()):
		if not frappe.db.table_exists(doctype):
			continue
		meta = frappe.get_meta(doctype)
		for visible_field, hidden_field in mirrors.items():
			if not (_has_column(doctype, visible_field) and _has_column(doctype, hidden_field)):
				continue
			_scan(doctype, meta, visible_field, hidden_field, rows, counts)
	_attach_labels(rows)
	rows.sort(key=lambda r: (r["doctype"], r["status"], r["record"]))
	return rows, counts


def _scan(doctype, meta, visible_field, hidden_field, rows, counts):
	context = {
		key: column for key, column in CONTEXT.get(doctype, {}).items() if _has_column(doctype, column)
	}
	columns = ["name", *context.values(), visible_field, hidden_field]
	query = "select {} from {} where length({}) > 0 or length({}) > 0".format(
		", ".join(_quote(c) for c in dict.fromkeys(columns)),
		_quote("tab" + doctype),
		_quote(visible_field),
		_quote(hidden_field),
	)
	target = _link_target(meta, visible_field)
	found = []
	for record in frappe.db.sql(query, as_dict=True):
		visible = record.get(visible_field) or ""
		hidden = record.get(hidden_field) or ""
		new_visible, new_hidden, rule = party_mirrors.plan_stored(visible, hidden)
		if rule is None:
			counts[STATUS_AGREE] += 1
			continue
		if visible and hidden:
			status = STATUS_DIFFER
		elif hidden:
			status = STATUS_HIDDEN_ONLY
		else:
			status = STATUS_VISIBLE_ONLY
		found.append(
			{
				"doctype": doctype,
				"record": record.get("name"),
				"title": record.get(context.get("title", "")) or "",
				"party_type": record.get(context.get("party_type", "")) or "",
				"party": record.get(context.get("party", "")) or "",
				"party_label": record.get(context.get("party_label", "")) or "",
				"visible_field": visible_field,
				"visible": visible,
				"hidden_field": hidden_field,
				"hidden": hidden,
				"proposed_visible": new_visible,
				"proposed_hidden": new_hidden,
				"status": status,
				"rule": rule,
				"_target": target,
			}
		)
	existing = _existing(target, {row["proposed_visible"] for row in found if row["proposed_visible"]})
	for row in found:
		if target and row["proposed_visible"] and row["proposed_visible"] not in existing:
			row["status"] = STATUS_MISSING
			row["proposed_visible"], row["proposed_hidden"] = row["visible"], row["hidden"]
		counts[row["status"]] += 1
		rows.append(row)


def _link_target(meta, fieldname):
	df = meta.get_field(fieldname) if meta else None
	if df is not None and getattr(df, "fieldtype", None) == "Link":
		return getattr(df, "options", None)
	return None


def _existing(doctype, names):
	if not doctype or not names:
		return set()
	return set(frappe.get_all(doctype, filters={"name": ["in", sorted(names)]}, pluck="name"))


def _attach_labels(rows):
	"""Each Contact's full name beside its docname, read once for the whole list."""
	by_target = {}
	for row in rows:
		target = row.pop("_target", None)
		if target:
			by_target.setdefault(target, set()).update(
				v for v in (row["visible"], row["hidden"], row["proposed_visible"]) if v
			)
	labels = {}
	for target, names in by_target.items():
		meta_fields = ("full_name",) if target == "Contact" else ()
		if not meta_fields:
			continue
		for found in frappe.get_all(
			target, filters={"name": ["in", sorted(names)]}, fields=["name", *meta_fields]
		):
			labels[found["name"]] = found.get("full_name") or ""
	for row in rows:
		for key in ("visible", "hidden", "proposed_visible", "proposed_hidden"):
			row[f"{key}_label"] = labels.get(row[key]) or row[key]


def get_columns():
	def contact(fieldname, label):
		return {
			"label": _(label),
			"fieldname": fieldname,
			"fieldtype": "Link",
			"options": "Contact",
			"sf_face": "party",
			"sf_label": f"{fieldname}_label",
			"width": 190,
		}

	return [
		{
			"label": _("DocType"),
			"fieldname": "doctype",
			"fieldtype": "Link",
			"options": "DocType",
			"width": 110,
		},
		{
			"label": _("Record"),
			"fieldname": "record",
			"fieldtype": "Dynamic Link",
			"options": "doctype",
			"sf_face": "party",
			"sf_label": "title",
			"width": 230,
		},
		{
			"label": _("Account"),
			"fieldname": "party",
			"fieldtype": "Dynamic Link",
			"options": "party_type",
			"sf_face": "party",
			"sf_label": "party_label",
			"width": 190,
		},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 200},
		contact("visible", "Primary Contact (shown)"),
		contact("hidden", "Contact Person (hidden)"),
		contact("proposed_visible", "Primary Contact would be"),
		contact("proposed_hidden", "Contact Person would be"),
		{"label": _("Rule"), "fieldname": "rule", "fieldtype": "Data", "width": 220},
		{"label": _("Party Type"), "fieldname": "party_type", "fieldtype": "Data", "hidden": 1},
	]


def _has_column(doctype, fieldname):
	try:
		return bool(frappe.db.has_column(doctype, fieldname))
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
				"<b>A dry run: showing this writes nothing.</b> Opportunities whose Primary Contact (shown on "
				"the form) and Contact Person (hidden; what Create > Quotation copies) disagree, and what "
				"they would become."
			),
			_(
				"The fix would keep the shown field when it has a value and copy the hidden one up when it is "
				"the only one set. A save that changes neither field leaves these rows as they are; they are "
				"fixed only after the list is approved."
			),
		]
	)


def _report_summary(counts):
	return [
		{"label": _(STATUS_DIFFER), "value": counts[STATUS_DIFFER], "datatype": "Int", "indicator": "Orange"},
		{
			"label": _(STATUS_HIDDEN_ONLY),
			"value": counts[STATUS_HIDDEN_ONLY],
			"datatype": "Int",
			"indicator": "Blue",
		},
		{
			"label": _(STATUS_VISIBLE_ONLY),
			"value": counts[STATUS_VISIBLE_ONLY],
			"datatype": "Int",
			"indicator": "Blue",
		},
		{"label": _(STATUS_MISSING), "value": counts[STATUS_MISSING], "datatype": "Int", "indicator": "Grey"},
		{"label": _(STATUS_AGREE), "value": counts[STATUS_AGREE], "datatype": "Int", "indicator": "Green"},
	]
