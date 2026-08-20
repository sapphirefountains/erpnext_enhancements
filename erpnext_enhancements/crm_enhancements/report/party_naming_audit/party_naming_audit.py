# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Party Naming Audit — the work list for Project, Opportunity and Address names.

Three doctypes name themselves after the party they belong to. This is where you see the
ones that do not. Sibling of `Account Data Quality`, which does the same job for the account
fields themselves, and of `Item Naming Audit`, which does it for the catalogue.

**Ordered by consequence.** STOP before FIX before NOTE, then by record. A STOP is a
duplicate — the same job entered twice. A FIX is a name that does not identify its party. A
NOTE is worth knowing and not worth acting on alone.

**Out of scope does not appear here at all**, and that distinction is the report's most
important property. An internal Project is not badly named; it is a different kind of record.
101 of 644 Projects are internal — Overhead, Internal, Other, leftover stage templates, and
the software backlog that lives in the Project doctype — and every one of them is excluded by
its own `project_type`, not by a list anybody maintains. Likewise 442 of 1,011 Addresses are
linked to nothing, so there is no party to name them after; they are counted in the header
rather than listed, because "belongs to nobody" is a real finding and a different one from
"badly named".

Every rule lives in `crm_enhancements.party_naming_rules`, which imports no Frappe and is
unit-tested in CI against verbatim production records. Nothing is decided here.
"""

import frappe
from frappe import _

from erpnext_enhancements.crm_enhancements import party_naming_rules as rules
from erpnext_enhancements.crm_enhancements.party_naming import audit_doctype


def execute(filters=None):
	filters = frappe._dict(filters or {})
	doctype = (filters.get("target_doctype") or "Project").strip()
	if doctype not in rules.DOCTYPES:
		frappe.throw(_("{0} has no naming rules.").format(doctype), frappe.ValidationError)

	result = audit_doctype(
		doctype, severity=filters.get("severity"), code=filters.get("finding_code")
	)
	if not result.get("success"):
		return get_columns(), [], result.get("message"), None

	data = [_shape(row) for row in result["rows"]]
	return get_columns(), data, _message(doctype, result), get_chart(result["rows"])


def get_columns():
	return [
		{"label": _("Severity"), "fieldname": "severity", "fieldtype": "Data", "width": 90},
		{"label": _("Record"), "fieldname": "record", "fieldtype": "Dynamic Link", "options": "doctype", "width": 190},
		{"label": _("Doctype"), "fieldname": "doctype", "fieldtype": "Data", "width": 110},
		{"label": _("Name it carries"), "fieldname": "value", "fieldtype": "Data", "width": 300},
		{"label": _("Party it belongs to"), "fieldname": "party", "fieldtype": "Data", "width": 220},
		{"label": _("Findings"), "fieldname": "findings", "fieldtype": "Data", "width": 260},
		{"label": _("What to do"), "fieldname": "first_message", "fieldtype": "Data", "width": 460},
	]


def _shape(row):
	findings = row.get("findings") or []
	return {
		"severity": row.get("verdict"),
		"record": row.get("name"),
		"doctype": row.get("doctype"),
		"value": row.get("value"),
		"party": row.get("party") or "—",
		# Codes rather than prose so the column stays scannable and the finding_code filter
		# has something to match; the sentence goes in `What to do`.
		"findings": ", ".join(f.get("code") for f in findings),
		"first_message": findings[0].get("message") if findings else "",
	}


def _message(doctype, result):
	summary = result.get("summary") or {}
	corpus = result.get("corpus") or {}
	config = rules.DOCTYPES[doctype]

	notes = [
		_("Checking <b>{0}.{1}</b>. Sorted worst-first. Nothing here is enforced — there is no "
		  "validate hook on any of these doctypes and no save is ever blocked."
		  ).format(doctype, config["field"]),
	]

	in_scope = summary.get("in_scope_rows") or 0
	passing = summary.get("passing_rows") or 0
	pct = summary.get("compliance_pct")
	if pct is not None:
		notes.append(
			_("<b>{0} of {1} in-scope {2} records ({3}%) pass every check.</b> {4} more were not "
			  "checked at all — see below."
			  ).format(passing, in_scope, doctype, round(pct, 1), summary.get("out_of_scope_rows") or 0)
		)

	if doctype == "Project":
		notes.append(
			_("Only Projects typed {0} are checked. Everything else is an internal project with "
			  "no customer — Overhead, Internal, Other, leftover stage templates, and the "
			  "software backlog — and is skipped by its own Project Type rather than by a list "
			  "anyone maintains."
			  ).format(", ".join(sorted(rules.CUSTOMER_FACING_PROJECT_TYPES)))
		)
	elif doctype == "Address":
		unlinked = summary.get("unlinked_rows") or 0
		notes.append(
			_("<b>{0} Addresses are linked to nothing at all</b> and are not listed here — there "
			  "is no party to name them after. That is its own piece of work: an address nobody "
			  "references cannot be found by anybody."
			  ).format(unlinked)
		)
		notes.append(
			_("Address names are built by Frappe as <code>address_title + '-' + address_type</code> "
			  "at insert and never again, so the title is the only thing worth correcting. A "
			  "title that already ends in an address type produces a doubled one.")
		)

	notes.append(
		_("Matching the party allows a shortening of it — <i>West Jordan</i> is accepted for "
		  "<i>West Jordan Parks and Recreation</i> — compared word by word rather than character "
		  "by character. Duplicates are grouped by: <i>{0}</i>."
		  ).format(summary.get("normalisation") or "")
	)

	if corpus.get("permission_filtered"):
		notes.append(
			_("<b>You can see {0} of {1} {2} records.</b> This list, and the counts above, "
			  "describe only what your permissions allow."
			  ).format(corpus.get("visible"), corpus.get("total"), doctype)
		)
	return "<br><br>".join(notes)


def get_chart(rows):
	"""What the backlog is made of, rather than how big it is."""
	counts = {}
	for row in rows:
		for item in row.get("findings") or ():
			code = item.get("code")
			counts[code] = counts.get(code, 0) + 1
	labels = sorted(counts, key=lambda key: counts[key], reverse=True)[:10]
	return {
		"data": {
			"labels": labels,
			"datasets": [{"name": _("Records"), "values": [counts[label] for label in labels]}],
		},
		"type": "bar",
		"colors": ["#f0a34a"],
	}
