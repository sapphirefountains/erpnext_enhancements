# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Attribution Gaps — Leads and Opportunities whose origin is unknown, by owner.

The work list for closing the attribution hole. One row per record that cannot
be credited to an acquisition channel, grouped by the person who can actually
fix it.

**Two kinds of gap, and they are not the same thing.**

* *No source at all* — the field is blank. After
  ``patches.backfill_unknown_lead_source`` this can only happen to a record
  created on or after 2026-08-01, which makes it a **live process failure**: a
  deal is being worked right now with no idea where it came from.
* *Unknown (pre-Aug 2026)* — the bucket the backfill stamped on history. Honest,
  expected, and not anybody's fault. Included by default so the size of the debt
  stays visible, but sorted below the live gaps and separable with one filter.

**The bucket is history only on a record created before capture started**
(``attribution.CAPTURE_START``). The source gate lets a salesperson pick that bucket
when the source is genuinely unknown -- deliberately, so nobody is blocked mid-day --
and until v1.502.1 this report filed every such record under *Historical*: sorted to
the bottom, left out of the chart, and hidden by "only records with no source". So
once enforcement was on, every bypass of the gate read as debt nobody owned, and the
live-gap count stayed at zero while the gate was being routed around. Two Opportunities
created in August and September 2026 were already sitting there. A bucket record
created on or after ``CAPTURE_START`` is now its own live gap, *Unknown (new)*.

Sorting is by that distinction first and owner second, so the top of the report
is always the thing worth doing today.

**Why a Script Report and not a Report Builder view:** the two doctypes have to
appear in one list (a salesperson owns both, and splitting them across two
saved views guarantees one of them goes unread), and Report Builder cannot union
doctypes. The owner column also needs a three-step fallback — Opportunity has
``opportunity_owner``, Lead has ``lead_owner``, and either may be blank, in which
case ``owner`` (whoever created the record) is the only person to ask.
"""

import frappe
from frappe import _

from erpnext_enhancements.crm_enhancements.attribution import CAPTURE_START, UNKNOWN_LEAD_SOURCE

GAP_NO_SOURCE = "No source"
GAP_UNKNOWN_NEW = "Unknown (new)"
GAP_HISTORICAL = "Historical"
LIVE_GAPS = (GAP_NO_SOURCE, GAP_UNKNOWN_NEW)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(filters)
	return columns, data, None, get_chart(data)


def get_columns():
	return [
		{"label": _("Owner"), "fieldname": "owner_user", "fieldtype": "Link", "options": "User", "width": 200},
		{"label": _("Gap"), "fieldname": "gap", "fieldtype": "Data", "width": 130},
		{"label": _("Type"), "fieldname": "doctype_label", "fieldtype": "Data", "width": 100},
		{"label": _("Record"), "fieldname": "record", "fieldtype": "Dynamic Link", "options": "doctype_name", "width": 180},
		{"label": _("Name / Party"), "fieldname": "title", "fieldtype": "Data", "width": 240},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 130},
		{"label": _("Created"), "fieldname": "created", "fieldtype": "Date", "width": 110},
		{"label": _("Value"), "fieldname": "value", "fieldtype": "Currency", "width": 130},
		# Hidden support column for the Dynamic Link above.
		{"label": _("Doctype"), "fieldname": "doctype_name", "fieldtype": "Data", "width": 1, "hidden": 1},
	]


def get_data(filters):
	rows = []
	if _has_field("Lead"):
		rows.extend(_lead_rows(filters))
	if _has_field("Opportunity"):
		rows.extend(_opportunity_rows(filters))

	# Live gaps first, then by owner, then newest first within an owner.
	rows.sort(key=lambda r: (r["_gap_rank"], r["owner_user"] or "", r["created"] or ""), reverse=False)
	for row in rows:
		row.pop("_gap_rank", None)
	return rows


def _has_field(doctype):
	"""The report must not 500 on a bench where the fixtures have not landed."""
	try:
		return frappe.db.has_column(doctype, "custom_lead_source")
	except Exception:
		return False


def _source_condition(filters, alias):
	"""SQL fragment selecting the gap rows, honouring the live-gaps filter.

	"Live" keeps the bucket when the record was created after capture started --
	that is somebody choosing "unknown" today, not history.
	"""
	blank = f"({alias}.custom_lead_source IS NULL OR {alias}.custom_lead_source = '')"
	bucket = f"{alias}.custom_lead_source = %(bucket)s"
	if filters.get("only_live_gaps"):
		return f"({blank} OR ({bucket} AND {alias}.creation >= %(capture_start)s))"
	return f"({blank} OR {bucket})"


def _base_values(filters):
	values = {"bucket": UNKNOWN_LEAD_SOURCE, "capture_start": CAPTURE_START}
	if filters.get("from_date"):
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		values["to_date"] = filters.to_date
	if filters.get("owner_user"):
		values["owner_user"] = filters.owner_user
	return values


def _date_conditions(filters, alias):
	conditions = []
	if filters.get("from_date"):
		conditions.append(f"{alias}.creation >= %(from_date)s")
	if filters.get("to_date"):
		conditions.append(f"DATE({alias}.creation) <= %(to_date)s")
	return conditions


def _lead_rows(filters):
	conditions = [_source_condition(filters, "l")] + _date_conditions(filters, "l")
	if filters.get("owner_user"):
		conditions.append("COALESCE(NULLIF(l.lead_owner, ''), l.owner) = %(owner_user)s")

	records = frappe.db.sql(
		f"""
		SELECT
			l.name,
			COALESCE(NULLIF(l.lead_owner, ''), l.owner) AS owner_user,
			COALESCE(NULLIF(l.lead_name, ''), NULLIF(l.company_name, ''), l.name) AS title,
			l.status,
			DATE(l.creation) AS created,
			l.custom_lead_source AS source
		FROM `tabLead` l
		WHERE {" AND ".join(conditions)}
		""",
		_base_values(filters),
		as_dict=True,
	)
	return [_shape(r, "Lead") for r in records]


def _opportunity_rows(filters):
	conditions = [_source_condition(filters, "o")] + _date_conditions(filters, "o")
	if filters.get("owner_user"):
		conditions.append("COALESCE(NULLIF(o.opportunity_owner, ''), o.owner) = %(owner_user)s")
	if filters.get("open_only"):
		conditions.append("o.status NOT IN ('Lost', 'Closed', 'Closed Won')")

	records = frappe.db.sql(
		f"""
		SELECT
			o.name,
			COALESCE(NULLIF(o.opportunity_owner, ''), o.owner) AS owner_user,
			COALESCE(NULLIF(o.customer_name, ''), o.party_name, o.name) AS title,
			o.status,
			DATE(o.creation) AS created,
			o.opportunity_amount AS value,
			o.custom_lead_source AS source
		FROM `tabOpportunity` o
		WHERE {" AND ".join(conditions)}
		""",
		_base_values(filters),
		as_dict=True,
	)
	return [_shape(r, "Opportunity") for r in records]


def gap_kind(source, created):
	"""Which gap a record is: ``GAP_NO_SOURCE``, ``GAP_UNKNOWN_NEW`` or ``GAP_HISTORICAL``."""
	if source != UNKNOWN_LEAD_SOURCE:
		return GAP_NO_SOURCE
	if created and str(created)[:10] >= CAPTURE_START:
		return GAP_UNKNOWN_NEW
	return GAP_HISTORICAL


def _shape(record, doctype):
	kind = gap_kind(record.get("source"), record.get("created"))
	return {
		"owner_user": record.get("owner_user"),
		"gap": _(kind),
		"_gap_rank": 0 if kind in LIVE_GAPS else 1,
		"doctype_label": _(doctype),
		"doctype_name": doctype,
		"record": record.get("name"),
		"title": record.get("title"),
		"status": record.get("status"),
		"created": record.get("created"),
		"value": record.get("value") or 0,
	}


def get_chart(data):
	"""Live gaps per owner -- no source, or "unknown" chosen on a new record.
	Historical rows are excluded on purpose: a bar chart dominated by a decade of
	backfill would hide the handful of records somebody needs to fix this week."""
	live = {_(kind) for kind in LIVE_GAPS}
	counts = {}
	for row in data:
		if row.get("gap") not in live:
			continue
		counts[row.get("owner_user") or _("Unassigned")] = counts.get(row.get("owner_user") or _("Unassigned"), 0) + 1

	labels = sorted(counts, key=lambda k: counts[k], reverse=True)
	return {
		"data": {
			"labels": labels,
			"datasets": [{"name": _("Records with no known source"), "values": [counts[label] for label in labels]}],
		},
		"type": "bar",
		"colors": ["#e24c4c"],
	}
