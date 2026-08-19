# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Item Naming Audit — the work list for closing the item-naming gap.

The *ERPNext Item Naming Schema* SOP v1.0 set the standard and measured the gap: 120 of 713
records satisfied it. This is the report that turns that paragraph into a list somebody can
work through, and it is how WI-070 gets executed and verified.

**Ordered by consequence, not by name.** Rows are sorted STOP before FIX before NOTE, then by
Item Code. A STOP is a record that should not be transacted against — a duplicate, a name four
records share, a code that collides with an existing one. A FIX is a record that is merely
untidy. An alphabetical list would bury the first behind the second, and the exercise would be
abandoned on day two.

**Tombstones are hidden by default.** 135 rows carry a `(deleted)` suffix from the QuickBooks
migration and every one of them has `item_name` identical to `item_code`, so they fail nearly
every check and would be most of this list. They are a single batch retirement (WI-070 bucket
A), not 135 separate decisions. The filter is there when you want them.

**No bulk-fix toolbar, deliberately.** `Account Data Quality` has one because assigning a
customer group is a value a human picks applied to rows a human selected. Almost nothing here
is that shape: which amperage a breaker is, which of two duplicate records is the real part,
and whether a code/name mismatch is a swap or a bad name are all judgements that need a vendor
catalogue open. The one exception — uppercasing 75 names — is genuinely mechanical, and it
belongs to WI-070 bucket C where it ships with the rollback export a bulk write needs.

Every rule this report applies lives in `inventory_enhancements.item_naming_rules`, which
imports no Frappe and is unit-tested in CI against verbatim production strings. Nothing is
decided here — this module is presentation. Two definitions of "compliant" that disagree by one
row is a bug report nobody can close.
"""

import frappe
from frappe import _

from erpnext_enhancements.inventory_enhancements import item_naming_rules as rules
from erpnext_enhancements.inventory_enhancements.item_naming import audit_corpus

#: What the Severity column renders as. `verdict` is the row's worst finding.
_INDICATORS = {
	rules.VERDICT_STOP: "red",
	rules.VERDICT_FIX: "orange",
	rules.VERDICT_PASS: "green",
}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	result = audit_corpus(
		include_deleted=bool(filters.get("include_deleted")),
		severity=filters.get("severity"),
		family=filters.get("family"),
		code=filters.get("finding_code"),
	)
	data = [_shape(row) for row in result["rows"]]
	return get_columns(), data, _message(result), get_chart(result["rows"])


def get_columns():
	return [
		{"label": _("Severity"), "fieldname": "severity", "fieldtype": "Data", "width": 90},
		{"label": _("Item Code"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 230},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 300},
		{"label": _("Findings"), "fieldname": "findings", "fieldtype": "Data", "width": 320},
		{"label": _("Family"), "fieldname": "family", "fieldtype": "Data", "width": 110},
		{"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 150},
		{"label": _("What to do"), "fieldname": "first_message", "fieldtype": "Data", "width": 420},
	]


def _shape(row):
	findings = row.get("findings") or []
	return {
		"severity": row.get("verdict"),
		"item_code": row.get("item_code"),
		"item_name": row.get("item_name"),
		# Codes rather than prose, so the column stays scannable and the finding_code filter
		# has something to match against. The sentence goes in `What to do`.
		"findings": ", ".join(f.get("code") for f in findings),
		"family": row.get("family"),
		"item_group": row.get("item_group"),
		"first_message": findings[0].get("message") if findings else "",
		"is_tombstone": row.get("is_tombstone"),
	}


def _message(result):
	summary = result.get("summary") or {}
	corpus = result.get("corpus") or {}
	notes = [
		_("Sorted worst-first: STOP before FIX before NOTE. A STOP is a record that should not "
		  "be transacted against; a FIX is one that is merely untidy. Nothing here is enforced — "
		  "there is no Item hook and nothing blocks a save."),
	]

	live = summary.get("live_rows") or 0
	passing = summary.get("passing_live_rows") or 0
	pct = summary.get("compliance_pct")
	if pct is not None:
		notes.append(
			_("<b>{0} of {1} live records ({2}%) pass every check.</b> {3} rows carry a "
			  "'(deleted)' suffix from the QuickBooks migration and are hidden unless you ask "
			  "for them — they are one batch retirement (WI-070), not {3} decisions.").format(
				passing, live, round(pct, 1), summary.get("tombstone_rows") or 0
			)
		)

	# The normalisation is printed because a duplicate count is a property of it, not of the
	# data — quoting one without the other is unfalsifiable. See item_naming_rules.NORMALISATION.
	notes.append(
		_("Duplicate detection normalises names by: <i>{0}</i>. Note what that cannot catch — "
		  "the same physical part filed under two codes and two wordings collides on nothing. "
		  "Use the 'Check naming' button on an Item form for scored near-neighbours."
		  ).format(summary.get("normalisation") or "")
	)

	if corpus.get("permission_filtered"):
		notes.append(
			_("<b>You can see {0} of {1} Items.</b> This list, and the counts above, describe "
			  "only what your permissions allow.").format(corpus.get("visible"), corpus.get("total"))
		)
	return "<br><br>".join(notes)


def get_chart(rows):
	"""Findings by code — what the backlog is actually made of, rather than how big it is."""
	counts = {}
	for row in rows:
		for item in row.get("findings") or ():
			code = item.get("code")
			counts[code] = counts.get(code, 0) + 1
	labels = sorted(counts, key=lambda key: counts[key], reverse=True)[:12]
	return {
		"data": {
			"labels": labels,
			"datasets": [{"name": _("Records"), "values": [counts[label] for label in labels]}],
		},
		"type": "bar",
		"colors": ["#f0a34a"],
	}
