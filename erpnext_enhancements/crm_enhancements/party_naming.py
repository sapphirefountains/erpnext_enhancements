# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Reads for the party-naming advisor. :mod:`party_naming_rules` holds every judgement.

I/O and nothing else. It writes nothing, and there is no ``validate`` hook on any of the
three doctypes — the check reports and a human acts, the same posture the Item naming check
takes and for the same reason: most of the catalogue predates the rule, so anything that
blocked a save would fire constantly on legitimate edits to records that were already there.

--------------------------------------------------------------------------------------
Address is the only one that needs a join, and picking the party is a judgement
--------------------------------------------------------------------------------------

Project and Opportunity carry their party in a column. An Address carries its links in
``tabDynamic Link`` and may carry several at once — 42 live records link to a Customer *and*
a Project *and* an Opportunity. Frappe's own ``autoname`` resolves that by taking
``links[0]``, which is whatever order the child rows happened to be written in.

:data:`party_naming_rules.ADDRESS_PARTY_DOCTYPES` orders them deliberately instead —
Customer, then Supplier, then Project, then Opportunity — so the same address always
resolves to the same party and the answer does not depend on row order. An address linked to
both a Customer and the Project for that customer is named after the Customer, which is what
people actually do.

Addresses with no link at all — 442 of 1,011 — have no party to be named after, so they are
**out of scope rather than failing**. They are counted separately and reported as their own
number, because "this address belongs to nobody" is a real finding and a different one from
"this address is badly named".
"""

import frappe
from frappe import _

from erpnext_enhancements.crm_enhancements import party_naming_rules as rules

#: Above this many rows for one doctype, refuse rather than degrade. Set an order of
#: magnitude clear of the live tables (1,011 Addresses, 823 Opportunities, 644 Projects on
#: 2026-08-19) — a runaway guard, not a tuning knob.
CORPUS_CEILING = 20000

#: Columns to read per doctype, beyond ``name``. Kept here rather than derived so a field
#: that stops existing fails loudly at the query instead of silently reading as empty.
FIELDS = {
	"Project": ("project_name", "project_type", "customer"),
	"Opportunity": ("title", "party_name", "customer_name"),
	"Address": ("address_title", "address_type"),
}


def read_rows(doctype):
	"""Every record of ``doctype`` the current user can read, as plain dicts.

	Returns ``(rows, meta)``. ``meta`` carries ``total`` and ``visible`` because reads go
	through ``frappe.get_list``, which applies DocPerms and User Permissions — so a user who
	cannot see every record gets a check against the subset they can see, and "no duplicate
	found" then means "none that you can see". Saying so beside the answer is the difference
	between a caveat and a silent one.
	"""
	if doctype not in rules.DOCTYPES:
		frappe.throw(_("{0} has no naming rules.").format(doctype), frappe.ValidationError)

	total = frappe.db.count(doctype)
	rows = [
		dict(row)
		for row in frappe.get_list(doctype, fields=["name", *FIELDS[doctype]], limit_page_length=0)
	]
	if doctype == "Address":
		attach_address_parties(rows)

	return rows, {
		"total": total,
		"visible": len(rows),
		"permission_filtered": len(rows) < total,
	}


def attach_address_parties(rows):
	"""Set ``party`` on each Address row from its Dynamic Links, best link first.

	One query for the whole set rather than one per address — 1,011 round trips would make
	the report unusable and the result would be identical.
	"""
	names = [row["name"] for row in rows if row.get("name")]
	if not names:
		return rows

	links = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Address", "parent": ["in", names]},
		fields=["parent", "link_doctype", "link_name", "link_title"],
	)
	# {address: {doctype: label}} — the last link of a given doctype wins, which does not
	# matter because two Customer links on one address are already a different problem.
	by_address = {}
	for link in links:
		by_address.setdefault(link["parent"], {})[link["link_doctype"]] = (
			link.get("link_title") or link.get("link_name") or ""
		)

	for row in rows:
		found = by_address.get(row.get("name")) or {}
		row["party"] = ""
		row["party_doctype"] = ""
		for doctype in rules.ADDRESS_PARTY_DOCTYPES:
			if found.get(doctype):
				row["party"] = found[doctype]
				row["party_doctype"] = doctype
				break
		row["link_count"] = len(found)
	return rows


def audit_doctype(doctype, severity=None, code=None):
	"""Findings for one doctype, filtered, with the counts that describe the whole set.

	Filters are applied **after** the audit so the summary describes everything while the
	rows describe what was asked for — a report whose headline moved every time somebody
	ticked a filter would be a report nobody could quote.
	"""
	total = frappe.db.count(doctype)
	if total > CORPUS_CEILING:
		return {
			"success": False,
			"error": "corpus_too_large",
			"message": _(
				"{0} has {1} rows, above the {2}-row ceiling this check reads in full. Raise "
				"crm_enhancements.party_naming.CORPUS_CEILING deliberately."
			).format(doctype, total, CORPUS_CEILING),
			"total_rows": total,
		}

	rows, meta = read_rows(doctype)
	audited = rules.audit(doctype, rows)
	summary = rules.summarise(audited)
	summary["out_of_scope_rows"] = len(rows) - len(audited)
	if doctype == "Address":
		# Counted, not checked. An address linked to nothing has no party to be named after,
		# and 442 of them dumped into the findings list would drown everything actionable.
		summary["unlinked_rows"] = sum(1 for row in rows if not (row.get("party") or "").strip())

	wanted_severity = (severity or "").strip().upper()
	wanted_code = (code or "").strip()
	out = []
	for row in audited:
		if not row["findings"]:
			continue
		if wanted_severity and wanted_severity not in ("", "ANY") and row["verdict"] != wanted_severity:
			continue
		if wanted_code and not any(f["code"] == wanted_code for f in row["findings"]):
			continue
		out.append(row)

	out.sort(key=rules.audit_sort_key)
	return {"success": True, "doctype": doctype, "rows": out, "summary": summary, "corpus": meta}


@frappe.whitelist()
def check_record(doctype=None, name=None):
	"""Check one saved record. Read-only, advisory, writes nothing.

	Reads only the record in hand plus, for an Address, its links — **no corpus read**, which
	is what makes it cheap enough for a form to call on every refresh. Duplicate detection
	needs the whole set and therefore belongs to the report, not here.
	"""
	doctype = (doctype or "").strip()
	name = (name or "").strip()
	if doctype not in rules.DOCTYPES:
		frappe.throw(_("{0} has no naming rules.").format(doctype or "?"), frappe.ValidationError)
	if not name:
		frappe.throw(_("A record name is required."), frappe.ValidationError)
	if not frappe.has_permission(doctype, "read", doc=name):
		frappe.throw(_("No read permission for {0} {1}").format(doctype, name), frappe.PermissionError)

	values = frappe.db.get_value(doctype, name, list(FIELDS[doctype]), as_dict=True) or {}
	row = {"name": name, **values}
	if doctype == "Address":
		attach_address_parties([row])

	scoped = rules.in_scope(doctype, row)
	findings = rules.check(doctype, row) if scoped else []
	return {
		"success": True,
		"doctype": doctype,
		"name": name,
		"in_scope": bool(scoped),
		# Stated rather than implied: out of scope is silence, not a pass, and a caller that
		# rendered "PASS" for an internal project would be asserting something untrue.
		"reason_out_of_scope": _out_of_scope_reason(doctype, row) if not scoped else None,
		"party": rules.party_of(doctype, row),
		"verdict": rules.verdict(findings) if scoped else None,
		"findings": findings,
	}


def _out_of_scope_reason(doctype, row):
	if doctype == "Project":
		project_type = (row.get("project_type") or "").strip() or "(none)"
		return _(
			"Project Type is {0}, which is an internal project rather than customer work. "
			"Only {1} are checked."
		).format(project_type, ", ".join(sorted(rules.CUSTOMER_FACING_PROJECT_TYPES)))
	if doctype == "Address":
		return _(
			"This Address is not linked to a Customer, Supplier, Project or Opportunity, so "
			"there is no party to name it after. Link it and the check applies."
		)
	return None
