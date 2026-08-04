# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Account Data Quality — the work list for closing the account-data gaps.

Every package downstream of this one produces unreliable output against dirty
account data, so this is the report that has to be worked through first. Measured
2026-08-04, of 1,621 Customers: 1,292 have no industry, 1,160 no customer group,
1,118 no value stream.

**Ordered by consequence, not by name.** Rows are sorted by project count then
opportunity count, so the accounts you have actually done work for — the ones
whose classification changes what a revenue report says — come first. An
alphabetical list would put six hundred dormant records ahead of the fifty that
matter, and the exercise would be abandoned on day two.

**Assisted, never automatic.** The toolbar actions apply a value *a human picked*
to rows *a human selected*. Nothing here infers an industry from a company name.
That was an explicit requirement and it is the right one: a wrong industry is
invisible once written, and every downstream report silently inherits it. See
`crm_enhancements/data_quality.bulk_assign`.

**The legacy bucket.** 364 customers carry `customer_type = "Company"` and are
100% missing *both* industry and customer group — the signature of an
un-migrated import rather than a classification anyone made. The "Legacy import
only" filter isolates them; `patches.retype_legacy_company_customers` moves them
to `Commercial`.
"""

import frappe
from frappe import _

from erpnext_enhancements.crm_enhancements.data_quality import COMMERCIAL_TYPES, audit_rows


def execute(filters=None):
	filters = frappe._dict(filters or {})
	rows = audit_rows(filters)
	data = [_shape(row) for row in rows]
	return get_columns(), data, _message(filters), get_chart(data)


def get_columns():
	return [
		{"label": _("Missing"), "fieldname": "gaps", "fieldtype": "Data", "width": 200},
		{"label": _("Customer"), "fieldname": "name", "fieldtype": "Link", "options": "Customer", "width": 220},
		{"label": _("Type"), "fieldname": "customer_type", "fieldtype": "Data", "width": 110},
		{"label": _("Group"), "fieldname": "customer_group", "fieldtype": "Link", "options": "Customer Group", "width": 150},
		{"label": _("Industry"), "fieldname": "industry", "fieldtype": "Link", "options": "Industry Type", "width": 160},
		{"label": _("Value Streams"), "fieldname": "value_streams", "fieldtype": "Data", "width": 170},
		{"label": _("Territory"), "fieldname": "territory", "fieldtype": "Link", "options": "Territory", "width": 130},
		{"label": _("Status"), "fieldname": "custom_account_status", "fieldtype": "Data", "width": 100},
		{"label": _("Projects"), "fieldname": "projects", "fieldtype": "Int", "width": 90},
		{"label": _("Opps"), "fieldname": "opportunities", "fieldtype": "Int", "width": 80},
	]


def _shape(row):
	gaps = []
	if not row.get("customer_group"):
		gaps.append(_("Group"))
	# Residential accounts are not counted as missing an industry — that is the
	# whole point of the conditional rule.
	if not row.get("industry") and (row.get("customer_type") or "") in COMMERCIAL_TYPES:
		gaps.append(_("Industry"))
	if not row.get("value_streams"):
		gaps.append(_("Value Stream"))

	row["gaps"] = ", ".join(gaps) or _("—")
	row["is_legacy"] = (
		(row.get("customer_type") or "") == "Company"
		and not row.get("industry")
		and not row.get("customer_group")
	)
	return row


def _message(filters):
	notes = [
		_("Sorted by projects then opportunities — the accounts you have actually worked for come first. "
		  "Select rows and use the toolbar to assign a value; nothing is ever inferred for you.")
	]
	if not filters.get("legacy_only"):
		legacy = frappe.db.count("Customer", {
			"customer_type": "Company", "industry": ["in", ["", None]], "customer_group": ["in", ["", None]],
		})
		if legacy:
			notes.append(
				_("<b>{0} accounts are an un-migrated legacy import</b> (customer_type 'Company', missing both "
				  "industry and group). Use the 'Legacy import only' filter to isolate them — they are a "
				  "single bad import, not {0} separate decisions.").format(legacy)
			)
	return "<br><br>".join(notes)


def get_chart(data):
	counts = {}
	for row in data:
		for gap in (row.get("gaps") or "").split(", "):
			if gap and gap != _("—"):
				counts[gap] = counts.get(gap, 0) + 1
	labels = sorted(counts, key=lambda k: counts[k], reverse=True)
	return {
		"data": {"labels": labels, "datasets": [{"name": _("Accounts"), "values": [counts[l] for l in labels]}]},
		"type": "bar",
		"colors": ["#f0a34a"],
	}
