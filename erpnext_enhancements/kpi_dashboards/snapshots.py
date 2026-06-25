"""KPI snapshot engine — nightly precompute of department KPIs.

Mirrors the Morning Briefing pattern (api/briefing.py): a cron entry checks the
master switch and hands a batch to the ``long`` queue; the batch builds one
**KPI Snapshot** per department, committing per-department so one slow/broken
aggregator can't sink the rest. Each aggregator is a pure read over the same
doctypes the dashboard catalog cites (Sales Invoice / Purchase Invoice / Payment
Entry as the post-QBO-sync system of record, Opportunity / Lead, Sapphire
Maintenance Record / Contract, etc.) — it never calls QBO/Stripe live; freshness
of those syncs is recorded in ``source_freshness_json`` so a stale upstream shows
a Watch badge instead of a silently-wrong number.

Phase 1 ships aggregators for Finance, Sales, and Operations. Adding a department
is one entry in ``AGGREGATORS`` returning ``{"values": [...], "freshness": {...}}``.

Settings: the "KPI Dashboards" section of ERPNext Enhancements Settings —
``kpi_dashboards_enabled`` master switch (default off, the app's staged-rollout
convention) and ``kpi_snapshot_retention_days``.
"""

import json

import frappe
from frappe.utils import add_days, cint, flt, getdate, now_datetime, nowdate

from erpnext_enhancements.kpi_dashboards import metrics

DEFAULT_RETENTION_DAYS = 120

# Department -> aggregator. A department absent here has no snapshot yet (the
# endpoint reports it as "not configured").
# AGGREGATORS is defined at the bottom, once the aggregator fns exist.


# --------------------------------------------------------------------- settings


def _settings():
	return frappe.get_cached_doc("ERPNext Enhancements Settings")


def kpi_enabled(settings=None):
	settings = settings or _settings()
	return bool(cint(settings.get("kpi_dashboards_enabled")))


def _retention_days(settings=None):
	settings = settings or _settings()
	return cint(settings.get("kpi_snapshot_retention_days")) or DEFAULT_RETENTION_DAYS


# ------------------------------------------------------------------ query helpers


def _scalar(query, params=None):
	"""First cell of a single-row aggregate query, or None."""
	res = frappe.db.sql(query, params or {})
	if res and res[0]:
		return res[0][0]
	return None


def _exists(doctype):
	return bool(frappe.db.exists("DocType", doctype))


# ------------------------------------------------------------- department metrics


def _collector():
	"""Return (values, add) where ``add`` appends a metric, skipping None values."""
	values = []

	def add(kpi_key, label, value, unit, source, direction):
		if value is None:
			return
		values.append(
			{
				"kpi_key": kpi_key,
				"label": label,
				"value": flt(value),
				"unit": unit,
				"source": source,
				"direction": direction,
			}
		)

	return values, add


def _finance_metrics():
	today = getdate(nowdate())
	d30 = add_days(today, -30)
	d90 = add_days(today, -90)
	month_start = today.replace(day=1)
	values, add = _collector()
	freshness = {}

	add(
		"ar_outstanding",
		"Accounts Receivable",
		_scalar("select sum(outstanding_amount) from `tabSales Invoice` where docstatus=1 and outstanding_amount>0"),
		"USD",
		"Sales Invoice",
		metrics.LOWER,
	)
	add(
		"ar_overdue",
		"AR Overdue",
		_scalar(
			"select sum(outstanding_amount) from `tabSales Invoice` "
			"where docstatus=1 and outstanding_amount>0 and due_date < %(t)s",
			{"t": today},
		),
		"USD",
		"Sales Invoice",
		metrics.LOWER,
	)
	add(
		"revenue_30",
		"Revenue (30d)",
		_scalar(
			"select sum(base_grand_total) from `tabSales Invoice` where docstatus=1 and posting_date >= %(d)s",
			{"d": d30},
		),
		"USD",
		"Sales Invoice",
		metrics.HIGHER,
	)
	add(
		"revenue_mtd",
		"Revenue (MTD)",
		_scalar(
			"select sum(base_grand_total) from `tabSales Invoice` where docstatus=1 and posting_date >= %(d)s",
			{"d": month_start},
		),
		"USD",
		"Sales Invoice",
		metrics.HIGHER,
	)
	# DSO = AR / (trailing-90d revenue / 90)
	rev90 = flt(
		_scalar(
			"select sum(base_grand_total) from `tabSales Invoice` where docstatus=1 and posting_date >= %(d)s",
			{"d": d90},
		)
	)
	ar = flt(_scalar("select sum(outstanding_amount) from `tabSales Invoice` where docstatus=1 and outstanding_amount>0"))
	dso = (ar / (rev90 / 90.0)) if rev90 else None
	add("dso", "Days Sales Outstanding", dso, "days", "Sales Invoice", metrics.LOWER)

	add(
		"ap_outstanding",
		"Accounts Payable",
		_scalar("select sum(outstanding_amount) from `tabPurchase Invoice` where docstatus=1 and outstanding_amount>0"),
		"USD",
		"Purchase Invoice",
		metrics.LOWER,
	)
	add(
		"cash_collected_30",
		"Cash Collected (30d)",
		_scalar(
			"select sum(base_received_amount) from `tabPayment Entry` "
			"where docstatus=1 and payment_type='Receive' and posting_date >= %(d)s",
			{"d": d30},
		),
		"USD",
		"Payment Entry",
		metrics.HIGHER,
	)
	add(
		"overdue_invoices",
		"Overdue Invoices",
		_scalar("select count(*) from `tabSales Invoice` where docstatus=1 and status='Overdue'"),
		"count",
		"Sales Invoice",
		metrics.LOWER,
	)

	if _exists("QuickBooks Sync Log"):
		add(
			"qbo_failed_7d",
			"QBO Failed Syncs (7d)",
			_scalar(
				"select count(*) from `tabQuickBooks Sync Log` where status='Failed' and creation >= %(d)s",
				{"d": add_days(today, -7)},
			),
			"count",
			"QuickBooks Sync Log",
			metrics.LOWER,
		)
	if _exists("QuickBooks Sync Mapping"):
		add(
			"qbo_conflicts",
			"QBO Open Conflicts",
			_scalar("select count(*) from `tabQuickBooks Sync Mapping` where conflict_status='Conflict'"),
			"count",
			"QuickBooks Sync Mapping",
			metrics.LOWER,
		)
	if _exists("Stripe Payment"):
		add(
			"stripe_unreconciled",
			"Stripe Paid w/o Entry",
			_scalar("select count(*) from `tabStripe Payment` where status='Paid' and coalesce(payment_entry,'')=''"),
			"count",
			"Stripe Payment",
			metrics.LOWER,
		)
	if _exists("Document Intake"):
		add(
			"intake_review_queue",
			"AP Intake Review Queue",
			_scalar("select count(*) from `tabDocument Intake` where status='Needs Review'"),
			"count",
			"Document Intake",
			metrics.LOWER,
		)

	freshness.update(_qbo_freshness())
	return {"values": values, "freshness": freshness}


def _operations_metrics():
	today = getdate(nowdate())
	d30 = add_days(today, -30)
	values, add = _collector()

	completed_30 = _scalar(
		"select count(*) from `tabSapphire Maintenance Record` "
		"where workflow_state='Final/Submitted' and creation >= %(d)s",
		{"d": d30},
	)
	add("visits_completed_30", "Visits Completed (30d)", completed_30, "count", "Sapphire Maintenance Record", metrics.HIGHER)
	add(
		"visits_open",
		"Open Visit Drafts",
		_scalar(
			"select count(*) from `tabSapphire Maintenance Record` where workflow_state in ('Draft','Pending Review')"
		),
		"count",
		"Sapphire Maintenance Record",
		metrics.LOWER,
	)
	oor_30 = _scalar(
		"select count(*) from `tabSapphire Maintenance Record` "
		"where has_out_of_range_readings=1 and creation >= %(d)s",
		{"d": d30},
	)
	add("chem_oor_30", "Out-of-Range Visits (30d)", oor_30, "count", "Sapphire Maintenance Record", metrics.LOWER)
	comp = flt(completed_30)
	oor_rate = (flt(oor_30) / comp * 100.0) if comp else None
	add("chem_oor_rate", "Out-of-Range Rate", oor_rate, "%", "Sapphire Maintenance Record", metrics.LOWER)

	add(
		"active_contracts",
		"Active Maintenance Contracts",
		_scalar("select count(*) from `tabSapphire Maintenance Contract` where status='Active'"),
		"count",
		"Sapphire Maintenance Contract",
		metrics.HIGHER,
	)
	add(
		"contracts_expiring_60",
		"Contracts Expiring (60d)",
		_scalar(
			"select count(*) from `tabSapphire Maintenance Contract` "
			"where status='Active' and end_date is not null and end_date between %(a)s and %(b)s",
			{"a": today, "b": add_days(today, 60)},
		),
		"count",
		"Sapphire Maintenance Contract",
		metrics.LOWER,
	)
	if _exists("Managed Device"):
		add(
			"device_noncompliant",
			"Non-Compliant Devices",
			_scalar(
				"select count(*) from `tabManaged Device` where coalesce(compliance_status,'') not in ('Compliant','')"
			),
			"count",
			"Managed Device",
			metrics.LOWER,
		)
	if _exists("Job Interval") and frappe.db.has_column("Job Interval", "sync_status"):
		add(
			"time_unsynced",
			"Unsynced Time Logs",
			_scalar("select count(*) from `tabJob Interval` where coalesce(sync_status,'') not in ('Synced','')"),
			"count",
			"Job Interval",
			metrics.LOWER,
		)
	if _exists("Inventory Count Session"):
		add(
			"inventory_open_counts",
			"Open Inventory Counts",
			_scalar(
				"select count(*) from `tabInventory Count Session` "
				"where coalesce(status,'') not in ('Completed','Cancelled','')"
			),
			"count",
			"Inventory Count Session",
			metrics.LOWER,
		)
	return {"values": values, "freshness": {}}


def _sales_metrics():
	today = getdate(nowdate())
	d30 = add_days(today, -30)
	d90 = add_days(today, -90)
	values, add = _collector()
	open_filter = "status not in ('Closed Won','Lost','Closed','Converted')"

	add(
		"open_pipeline_value",
		"Open Pipeline Value",
		_scalar(f"select sum(opportunity_amount) from `tabOpportunity` where {open_filter}"),
		"USD",
		"Opportunity",
		metrics.HIGHER,
	)
	add(
		"open_opportunities",
		"Open Opportunities",
		_scalar(f"select count(*) from `tabOpportunity` where {open_filter}"),
		"count",
		"Opportunity",
		metrics.HIGHER,
	)
	# Use the stamped close date if the CRM customization is present, else modified.
	closed_col = "custom_date_closed_won" if frappe.db.has_column("Opportunity", "custom_date_closed_won") else "modified"
	won_90 = flt(
		_scalar(
			f"select count(*) from `tabOpportunity` where status='Closed Won' and {closed_col} >= %(d)s",
			{"d": d90},
		)
	)
	lost_90 = flt(
		_scalar(
			"select count(*) from `tabOpportunity` where status in ('Lost','Closed') and modified >= %(d)s",
			{"d": d90},
		)
	)
	decided = won_90 + lost_90
	add("win_rate_90", "Win Rate (90d)", (won_90 / decided * 100.0) if decided else None, "%", "Opportunity", metrics.HIGHER)

	won_30 = _scalar(
		f"select count(*) from `tabOpportunity` where status='Closed Won' and {closed_col} >= %(d)s",
		{"d": d30},
	)
	add("won_count_30", "Closed-Won (30d)", won_30, "count", "Opportunity", metrics.HIGHER)
	won_val_30 = _scalar(
		f"select sum(opportunity_amount) from `tabOpportunity` where status='Closed Won' and {closed_col} >= %(d)s",
		{"d": d30},
	)
	add("won_value_30", "Closed-Won Value (30d)", won_val_30, "USD", "Opportunity", metrics.HIGHER)
	cnt30 = flt(won_30)
	add(
		"avg_deal_size",
		"Avg Deal Size (30d)",
		(flt(won_val_30) / cnt30) if cnt30 else None,
		"USD",
		"Opportunity",
		metrics.HIGHER,
	)

	add(
		"new_leads_30",
		"New Leads (30d)",
		_scalar("select count(*) from `tabLead` where creation >= %(d)s", {"d": d30}),
		"count",
		"Lead",
		metrics.HIGHER,
	)
	total_leads = flt(_scalar("select count(*) from `tabLead` where creation >= %(d)s", {"d": d90}))
	converted = flt(
		_scalar("select count(*) from `tabLead` where status='Converted' and creation >= %(d)s", {"d": d90})
	)
	add(
		"lead_conversion_90",
		"Lead Conversion (90d)",
		(converted / total_leads * 100.0) if total_leads else None,
		"%",
		"Lead",
		metrics.HIGHER,
	)
	add(
		"stalled_opportunities",
		"Stalled Opportunities (>14d)",
		_scalar(
			f"select count(*) from `tabOpportunity` where {open_filter} and modified < %(d)s",
			{"d": add_days(today, -14)},
		),
		"count",
		"Opportunity",
		metrics.LOWER,
	)
	if frappe.db.has_column("Opportunity", "custom_created_project"):
		add(
			"handoff_backlog",
			"Hand-Off Backlog",
			_scalar(
				"select count(*) from `tabOpportunity` where status='Closed Won' and coalesce(custom_created_project,'')=''"
			),
			"count",
			"Opportunity",
			metrics.LOWER,
		)
	return {"values": values, "freshness": {}}


def _qbo_freshness():
	"""Last QBO CDC sync time + staleness, for the Watch badge on QBO-sourced KPIs."""
	out = {}
	try:
		if _exists("QuickBooks Online Settings"):
			last = frappe.db.get_single_value("QuickBooks Online Settings", "last_cdc_sync")
			stale = metrics.is_source_stale(last, max_age_hours=6)
			entry = {"last_sync": str(last) if last else None, "stale": stale}
			out["QuickBooks Sync Log"] = entry
			out["QuickBooks Sync Mapping"] = entry
	except Exception:
		frappe.log_error(frappe.get_traceback(), "KPI snapshot — QBO freshness")
	return out


AGGREGATORS = {
	"Finance": _finance_metrics,
	"Sales": _sales_metrics,
	"Operations": _operations_metrics,
}


# ------------------------------------------------------------------- build/merge


def _prior_values(department, period, today):
	"""Map kpi_key -> value from the most recent snapshot before ``today`` (for trend)."""
	prior = frappe.get_all(
		"KPI Snapshot",
		filters={"department": department, "period": period, "snapshot_date": ("<", today)},
		order_by="snapshot_date desc",
		limit=1,
		pluck="name",
	)
	if not prior:
		return {}
	rows = frappe.get_all("KPI Snapshot Value", filters={"parent": prior[0]}, fields=["kpi_key", "value"])
	return {r.kpi_key: r.value for r in rows}


def _targets(department, period):
	"""Map kpi_key -> target dict, preferring a target whose period matches ``period``."""
	out = {}
	for r in frappe.get_all(
		"KPI Target",
		filters={"department": department},
		fields=["kpi_key", "target_value", "direction", "period"],
	):
		existing = out.get(r.kpi_key)
		if existing is None or (existing.get("period") != period and r.period == period):
			out[r.kpi_key] = {"target_value": r.target_value, "direction": r.direction, "period": r.period}
	return out


def _append_value(doc, v, targets, prior, freshness):
	key = v["kpi_key"]
	target = targets.get(key) or {}
	direction = target.get("direction") or v.get("direction") or metrics.HIGHER
	target_value = target.get("target_value")
	value = flt(v.get("value"))
	unit = v.get("unit") or ""
	source = v.get("source") or ""
	stale = bool((freshness.get(source) or {}).get("stale"))
	doc.append(
		"values",
		{
			"kpi_key": key,
			"label": v.get("label") or key,
			"value": value,
			"value_text": v.get("value_text") or metrics.fmt_value(value, unit),
			"unit": unit,
			"target_value": target_value,
			"status": metrics.compute_status(value, target_value, direction),
			"direction": direction,
			"trend_pct": metrics.compute_trend_pct(value, prior.get(key)),
			"source": source,
			"is_stale": 1 if stale else 0,
		},
	)


def build_department_snapshot(department, period="Daily", generated_by="Scheduler"):
	"""Compute and persist one snapshot for ``department`` today (idempotent)."""
	aggregator = AGGREGATORS.get(department)
	if not aggregator:
		frappe.throw(frappe._("No KPI aggregator is configured for {0}.").format(department))

	today = nowdate()
	name = f"KPI-{department}-{period}-{today}"
	prior = _prior_values(department, period, today)
	targets = _targets(department, period)

	error = None
	try:
		result = aggregator()
	except Exception:
		error = frappe.get_traceback()
		frappe.log_error(error, f"KPI aggregator failed: {department}")
		result = {"values": [], "freshness": {}}

	if frappe.db.exists("KPI Snapshot", name):
		frappe.delete_doc("KPI Snapshot", name, force=True, ignore_permissions=True)

	freshness = result.get("freshness") or {}
	doc = frappe.new_doc("KPI Snapshot")
	doc.department = department
	doc.period = period
	doc.snapshot_date = today
	doc.generated_at = now_datetime()
	doc.generated_by = generated_by
	doc.generation_error = (error or "")[:500] or None
	doc.source_freshness_json = json.dumps(freshness, default=str, indent=2)
	for v in result.get("values") or []:
		_append_value(doc, v, targets, prior, freshness)
	doc.insert(ignore_permissions=True)
	return doc


# ------------------------------------------------------------------- scheduler


def scheduled_kpi_run():
	"""Cron entry (nightly): hand the batch to a long worker if enabled."""
	if not kpi_enabled():
		return
	frappe.enqueue(
		"erpnext_enhancements.kpi_dashboards.snapshots.generate_all_snapshots",
		queue="long",
		timeout=1800,
		period="Daily",
	)


def generate_all_snapshots(period="Daily"):
	"""Build every department's snapshot; one failure never kills the batch."""
	settings = _settings()
	if not kpi_enabled(settings):
		return
	for department in AGGREGATORS:
		try:
			build_department_snapshot(department, period=period, generated_by="Scheduler")
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(frappe.get_traceback(), f"KPI snapshot batch — {department}")


def purge_old_snapshots():
	"""Daily scheduler: drop snapshots older than the retention window."""
	cutoff = add_days(nowdate(), -_retention_days())
	for name in frappe.get_all("KPI Snapshot", filters={"snapshot_date": ("<", cutoff)}, pluck="name"):
		frappe.delete_doc("KPI Snapshot", name, force=True, ignore_permissions=True)
