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
from frappe.utils import add_days, add_months, cint, flt, getdate, now_datetime, nowdate

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
	# Loss reason capture (custom_lost_reason). Winning no longer captures a
	# reason as of v1.149.0 (custom_won_reason removed).
	if frappe.db.has_column("Opportunity", "custom_lost_reason"):
		add(
			"lost_to_competitor_90",
			"Lost to Competitor (90d)",
			_scalar(
				"select count(*) from `tabOpportunity` where status='Lost' and custom_lost_reason='Competitor' and modified >= %(d)s",
				{"d": d90},
			),
			"count",
			"Opportunity",
			metrics.LOWER,
		)
		lost_total = flt(
			_scalar(
				"select count(*) from `tabOpportunity` where status='Lost' and modified >= %(d)s",
				{"d": d90},
			)
		)
		with_reason = flt(
			_scalar(
				"select count(*) from `tabOpportunity` where status='Lost' and coalesce(custom_lost_reason,'')<>'' and modified >= %(d)s",
				{"d": d90},
			)
		)
		add(
			"close_reason_capture_90",
			"Loss-Reason Capture (90d)",
			(with_reason / lost_total * 100.0) if lost_total else None,
			"%",
			"Opportunity",
			metrics.HIGHER,
		)
	return {"values": values, "freshness": {}}


def _design_metrics():
	today = getdate(nowdate())
	d30 = add_days(today, -30)
	values, add = _collector()
	wip = "status in ('Draft','Inputs Gathered','Calculated','Reviewed')"

	add(
		"designs_created_30",
		"Designs Created (30d)",
		_scalar("select count(*) from `tabWater Feature Design` where docstatus<2 and creation >= %(d)s", {"d": d30}),
		"count",
		"Water Feature Design",
		metrics.HIGHER,
	)
	add(
		"designs_issued_30",
		"Designs Issued (30d)",
		_scalar("select count(*) from `tabWater Feature Design` where status='Issued' and modified >= %(d)s", {"d": d30}),
		"count",
		"Water Feature Design",
		metrics.HIGHER,
	)
	add(
		"design_wip",
		"Design WIP",
		_scalar(f"select count(*) from `tabWater Feature Design` where docstatus<2 and {wip}"),
		"count",
		"Water Feature Design",
		metrics.LOWER,
	)
	add(
		"designs_with_warnings",
		"Designs w/ Warnings",
		_scalar("select count(*) from `tabWater Feature Design` where docstatus<2 and has_warnings=1"),
		"count",
		"Water Feature Design",
		metrics.LOWER,
	)
	issued = flt(_scalar("select count(*) from `tabWater Feature Design` where status='Issued'"))
	clean = flt(_scalar("select count(*) from `tabWater Feature Design` where status='Issued' and coalesce(has_warnings,0)=0"))
	add("clean_issue_rate", "Clean-Issue Rate", (clean / issued * 100.0) if issued else None, "%", "Water Feature Design", metrics.HIGHER)
	add(
		"design_revisions",
		"Open Design Revisions",
		_scalar("select count(*) from `tabWater Feature Design` where docstatus<2 and coalesce(amended_from,'')<>''"),
		"count",
		"Water Feature Design",
		metrics.LOWER,
	)
	add(
		"avg_wip_completion",
		"Avg WIP Completion",
		_scalar(f"select avg(completion_percent) from `tabWater Feature Design` where docstatus<2 and {wip}"),
		"%",
		"Water Feature Design",
		metrics.HIGHER,
	)
	return {"values": values, "freshness": {}}


def _production_metrics():
	today = getdate(nowdate())
	d30 = add_days(today, -30)
	d90 = add_days(today, -90)
	values, add = _collector()
	has = frappe.db.has_column

	# "Active"/in-progress Project filter. The stock Project status is 'Open',
	# but this site (and prod) books live projects as status='Active' — zero rows
	# are ever 'Open'. Accept both so the active/overdue/backlog KPIs populate
	# regardless of a site's convention, mirroring how _product_metrics handles
	# rentals. Terminal-ish states ('Completed','Cancelled','Paid','Invoiced')
	# are intentionally excluded from "active".

	add(
		"projects_completed_30",
		"Projects Completed (30d)",
		_scalar("select count(*) from `tabProject` where status='Completed' and modified >= %(d)s", {"d": d30}),
		"count",
		"Project",
		metrics.HIGHER,
	)
	add(
		"active_projects",
		"Active Projects",
		_scalar("select count(*) from `tabProject` where status in ('Open','Active')"),
		"count",
		"Project",
		metrics.HIGHER,
	)
	add(
		"projects_overdue",
		"Overdue Projects",
		_scalar(
			"select count(*) from `tabProject` where status in ('Open','Active') and expected_end_date is not null and expected_end_date < %(t)s",
			{"t": today},
		),
		"count",
		"Project",
		metrics.LOWER,
	)
	add(
		"avg_project_completion",
		"Avg Project Completion",
		_scalar("select avg(percent_complete) from `tabProject` where status in ('Open','Active')"),
		"%",
		"Project",
		metrics.HIGHER,
	)
	# Build-segment throughput. project_type already encodes the segment
	# (Build / Service / Events / Design) — no separate segment field needed.
	if has("Project", "project_type"):
		add(
			"builds_completed_30",
			"Builds Completed (30d)",
			_scalar(
				"select count(*) from `tabProject` where status='Completed' and project_type='Build' and modified >= %(d)s",
				{"d": d30},
			),
			"count",
			"Project",
			metrics.HIGHER,
		)
		add(
			"active_builds",
			"Active Builds",
			_scalar("select count(*) from `tabProject` where status in ('Open','Active') and project_type='Build'"),
			"count",
			"Project",
			metrics.HIGHER,
		)
		add(
			"builds_overdue",
			"Overdue Builds",
			_scalar(
				"select count(*) from `tabProject` where status in ('Open','Active') and project_type='Build' "
				"and expected_end_date is not null and expected_end_date < %(t)s",
				{"t": today},
			),
			"count",
			"Project",
			metrics.LOWER,
		)
		if has("Project", "custom_project_dollar_amount"):
			add(
				"build_backlog_value",
				"Build Backlog Value",
				_scalar("select sum(custom_project_dollar_amount) from `tabProject` where status in ('Open','Active') and project_type='Build'"),
				"USD",
				"Project",
				metrics.HIGHER,
			)
	if has("Project", "custom_time_budget_in_hours") and has("Project", "custom_total_time_elapsed"):
		row = frappe.db.sql(
			"select sum(custom_total_time_elapsed), sum(custom_time_budget_in_hours) from `tabProject` "
			"where status in ('Open','Active') and coalesce(custom_time_budget_in_hours,0)>0"
		)
		used, budget = (row[0][0], row[0][1]) if row and row[0] else (None, None)
		if budget:
			add("labor_budget_utilization", "Labor Budget Utilization", flt(used) / flt(budget) * 100.0, "%", "Project", metrics.LOWER)
	if has("Project", "custom_project_dollar_amount"):
		add(
			"backlog_value",
			"Backlog (Open Project Value)",
			_scalar("select sum(custom_project_dollar_amount) from `tabProject` where status in ('Open','Active')"),
			"USD",
			"Project",
			metrics.HIGHER,
		)
	if _exists("Project Process Step"):
		add(
			"milestones_overdue",
			"Overdue Milestones",
			_scalar(
				"select count(*) from `tabProject Process Step` where status='Pending' and due_by is not null and due_by < %(t)s",
				{"t": today},
			),
			"count",
			"Project Process Step",
			metrics.LOWER,
		)
		done = flt(
			_scalar(
				"select count(*) from `tabProject Process Step` where status='Completed' and completed_on is not null and modified >= %(d)s",
				{"d": d90},
			)
		)
		ontime = flt(
			_scalar(
				"select count(*) from `tabProject Process Step` where status='Completed' and completed_on is not null "
				"and due_by is not null and completed_on <= due_by and modified >= %(d)s",
				{"d": d90},
			)
		)
		add("on_time_milestone_rate", "On-Time Milestone Rate (90d)", (ontime / done * 100.0) if done else None, "%", "Project Process Step", metrics.HIGHER)
	if _exists("Project Contract") and frappe.db.has_column("Project Contract", "revision"):
		add(
			"change_orders",
			"Contract Change Orders",
			_scalar("select count(*) from `tabProject Contract` where coalesce(revision,0)>0"),
			"count",
			"Project Contract",
			metrics.LOWER,
		)
	return {"values": values, "freshness": {}}


def _marketing_metrics():
	today = getdate(nowdate())
	d30 = add_days(today, -30)
	d90 = add_days(today, -90)
	month_start = today.replace(day=1)
	values, add = _collector()
	open_filter = "status not in ('Closed Won','Lost','Closed','Converted')"

	add(
		"new_leads_30",
		"New Leads (30d)",
		_scalar("select count(*) from `tabLead` where creation >= %(d)s", {"d": d30}),
		"count",
		"Lead",
		metrics.HIGHER,
	)
	add(
		"new_leads_mtd",
		"New Leads (MTD)",
		_scalar("select count(*) from `tabLead` where creation >= %(d)s", {"d": month_start}),
		"count",
		"Lead",
		metrics.HIGHER,
	)
	total = flt(_scalar("select count(*) from `tabLead` where creation >= %(d)s", {"d": d90}))
	converted = flt(_scalar("select count(*) from `tabLead` where status='Converted' and creation >= %(d)s", {"d": d90}))
	add("lead_conversion_90", "Lead Conversion (90d)", (converted / total * 100.0) if total else None, "%", "Lead", metrics.HIGHER)
	add(
		"leads_unsourced",
		"Unsourced Leads (30d)",
		_scalar("select count(*) from `tabLead` where creation >= %(d)s and coalesce(source,'')=''", {"d": d30}),
		"count",
		"Lead",
		metrics.LOWER,
	)
	# Source attribution lives on Opportunity.source (stock Lead Source link).
	if frappe.db.has_column("Opportunity", "source"):
		add(
			"marketing_pipeline",
			"Sourced Pipeline Value",
			_scalar(f"select sum(opportunity_amount) from `tabOpportunity` where {open_filter} and coalesce(source,'')<>''"),
			"USD",
			"Opportunity",
			metrics.HIGHER,
		)
		closed_col = "custom_date_closed_won" if frappe.db.has_column("Opportunity", "custom_date_closed_won") else "modified"
		add(
			"marketing_won_30",
			"Sourced Wins (30d)",
			_scalar(
				f"select count(*) from `tabOpportunity` where status='Closed Won' and coalesce(source,'')<>'' and {closed_col} >= %(d)s",
				{"d": d30},
			),
			"count",
			"Opportunity",
			metrics.HIGHER,
		)
		add(
			"opportunities_unsourced",
			"Unsourced Opportunities",
			_scalar(f"select count(*) from `tabOpportunity` where {open_filter} and coalesce(source,'')=''"),
			"count",
			"Opportunity",
			metrics.LOWER,
		)
	# Manual monthly spend paste (Marketing Spend doctype) -> Cost Per Lead.
	if _exists("Marketing Spend"):
		spend_mtd = _scalar("select sum(amount) from `tabMarketing Spend` where month >= %(d)s", {"d": month_start})
		add("marketing_spend_mtd", "Marketing Spend (MTD)", spend_mtd, "USD", "Marketing Spend", metrics.LOWER)
		leads_mtd = flt(_scalar("select count(*) from `tabLead` where creation >= %(d)s", {"d": month_start}))
		if spend_mtd is not None and leads_mtd:
			add("cpl_mtd", "Cost Per Lead (MTD)", flt(spend_mtd) / leads_mtd, "USD", "Marketing Spend", metrics.LOWER)

	# Web traffic — read the cached daily GA4/GSC pull (snapshot_marketing_web),
	# never a live call here. Each source is gated on its own ok-flag so a
	# GA4-only site shows GA4 metrics without misleading 0s for Search Console.
	if _exists("Marketing Web Snapshot"):
		rows = frappe.get_all(
			"Marketing Web Snapshot",
			fields=["sessions_30", "active_users_30", "organic_clicks_30", "organic_impressions_30", "ga4_ok", "gsc_ok"],
			order_by="snapshot_date desc",
			limit=1,
		)
		if rows:
			w = rows[0]
			if w.ga4_ok:
				add("web_sessions_30", "Web Sessions (30d)", w.sessions_30, "count", "GA4", metrics.HIGHER)
				add("web_users_30", "Web Active Users (30d)", w.active_users_30, "count", "GA4", metrics.HIGHER)
			if w.gsc_ok:
				add("organic_clicks_30", "Organic Clicks (30d)", w.organic_clicks_30, "count", "Search Console", metrics.HIGHER)
				add("organic_impressions_30", "Organic Impressions (30d)", w.organic_impressions_30, "count", "Search Console", metrics.HIGHER)

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


def _latest_snapshot_values(department, period="Daily"):
	"""Map kpi_key -> value from a department's most recent snapshot (any date)."""
	name = frappe.get_all(
		"KPI Snapshot",
		filters={"department": department, "period": period},
		order_by="snapshot_date desc",
		limit=1,
		pluck="name",
	)
	if not name:
		return {}
	rows = frappe.get_all("KPI Snapshot Value", filters={"parent": name[0]}, fields=["kpi_key", "value"])
	return {r.kpi_key: r.value for r in rows}


# (exec_key, label, source_department, source_kpi_key, unit, direction)
_EXEC_ROLLUP = (
	("revenue_30", "Revenue (30d)", "Finance", "revenue_30", "USD", metrics.HIGHER),
	("cash_collected_30", "Cash Collected (30d)", "Finance", "cash_collected_30", "USD", metrics.HIGHER),
	("ar_outstanding", "Accounts Receivable", "Finance", "ar_outstanding", "USD", metrics.LOWER),
	("dso", "Days Sales Outstanding", "Finance", "dso", "days", metrics.LOWER),
	("open_pipeline_value", "Open Pipeline", "Sales", "open_pipeline_value", "USD", metrics.HIGHER),
	("win_rate_90", "Win Rate (90d)", "Sales", "win_rate_90", "%", metrics.HIGHER),
	("backlog_value", "Backlog (Open Project Value)", "Production", "backlog_value", "USD", metrics.HIGHER),
	("on_time_milestone_rate", "On-Time Milestone Rate", "Production", "on_time_milestone_rate", "%", metrics.HIGHER),
	("active_contracts", "Active Maintenance Contracts", "Operations", "active_contracts", "count", metrics.HIGHER),
	("chem_oor_rate", "Maintenance Out-of-Range Rate", "Operations", "chem_oor_rate", "%", metrics.LOWER),
	("turnover_rate_12m", "Turnover Rate (12m)", "HR", "turnover_rate_12m", "%", metrics.LOWER),
)


def _executive_metrics():
	"""Company-wide rollup. Re-surfaces curated KPIs from the freshest department
	snapshots (Executive is built last in the nightly batch, so it sees today's),
	plus a couple of direct exec-only computes."""
	values, add = _collector()
	cache = {}

	def latest(dept):
		if dept not in cache:
			cache[dept] = _latest_snapshot_values(dept, "Daily")
		return cache[dept]

	for exec_key, label, dept, src_key, unit, direction in _EXEC_ROLLUP:
		v = latest(dept).get(src_key)
		if v is not None:
			add(exec_key, label, v, unit, f"{dept} snapshot", direction)

	if _exists("Employee"):
		headcount = _scalar("select count(*) from `tabEmployee` where status='Active'")
		add("headcount", "Active Headcount", headcount, "count", "Employee", metrics.HIGHER)
		rev = latest("Finance").get("revenue_30")
		if rev is not None and headcount:
			add("revenue_per_employee", "Revenue per Employee (30d)", flt(rev) / flt(headcount), "USD", "Employee", metrics.HIGHER)

	return {"values": values, "freshness": {}}


def _product_metrics():
	"""Product Management — the fountain product catalog: revenue mix, SKU health,
	rentals, inventory, and catalog data-quality. Reads native ERPNext doctypes
	(Item / Bin / Sales Invoice Item / Project) — no live external calls.

	Grounded-data notes (probed 2026-06-26): the product taxonomy is ``item_group``
	(not a custom segment field); rentals are ``Project`` rows with
	``project_type='Events'`` and this site books live projects as ``status='Active'``
	(not 'Open'), so the rental KPIs accept both for portability; and QBO-synced
	Sales Invoices are currently drafts, so the submitted-only (``docstatus=1``)
	revenue KPIs populate once invoices are submitted — same convention as Finance.
	"""
	today = getdate(nowdate())
	d30 = add_days(today, -30)
	d90 = add_days(today, -90)
	d365 = add_days(today, -365)
	values, add = _collector()
	has = frappe.db.has_column

	# --- catalog revenue (submitted Sales Invoices). A per-item-group split is
	#     intentionally NOT attempted: on this instance every Sales Invoice Item
	#     line carries item_group='All Item Groups' (the root — the leaf taxonomy
	#     is not copied onto QBO-synced invoice lines), so a "Products"-vs-total
	#     split is degenerate and would emit a misleading 0% share. Total catalog
	#     revenue is always correct; it populates once invoices are submitted
	#     (today the QBO-synced invoices are all drafts). ---
	if _exists("Sales Invoice Item") and has("Sales Invoice Item", "base_net_amount"):

		def _catalog_revenue(since):
			return _scalar(
				"select sum(sii.base_net_amount) from `tabSales Invoice Item` sii "
				"join `tabSales Invoice` si on si.name=sii.parent "
				"where si.docstatus=1 and si.posting_date >= %(d)s",
				{"d": since},
			)

		add("catalog_revenue_30", "Catalog Revenue (30d)", _catalog_revenue(d30), "USD", "Sales Invoice", metrics.HIGHER)
		add("catalog_revenue_365", "Catalog Revenue (1y)", _catalog_revenue(d365), "USD", "Sales Invoice", metrics.HIGHER)

	# --- catalog size & freshness ---
	add(
		"active_sku_count",
		"Active SKUs",
		_scalar("select count(*) from `tabItem` where is_sales_item=1 and disabled=0"),
		"count",
		"Item",
		metrics.HIGHER,
	)
	add(
		"new_items_90",
		"New Items (90d)",
		_scalar("select count(*) from `tabItem` where is_sales_item=1 and creation >= %(d)s", {"d": d90}),
		"count",
		"Item",
		metrics.HIGHER,
	)
	add(
		"new_items_365",
		"New Items (1y)",
		_scalar("select count(*) from `tabItem` where is_sales_item=1 and creation >= %(d)s", {"d": d365}),
		"count",
		"Item",
		metrics.HIGHER,
	)

	# --- rentals (Project rows, project_type='Events'; live status is 'Active' here) ---
	if has("Project", "project_type"):
		add(
			"active_rentals",
			"Active Events",
			_scalar("select count(*) from `tabProject` where project_type='Events' and status in ('Open','Active')"),
			"count",
			"Project",
			metrics.HIGHER,
		)
		add(
			"rentals_started_30",
			"Events Started (30d)",
			_scalar("select count(*) from `tabProject` where project_type='Events' and creation >= %(d)s", {"d": d30}),
			"count",
			"Project",
			metrics.HIGHER,
		)
		if has("Project", "custom_project_dollar_amount"):
			add(
				"rental_backlog_value",
				"Events Backlog Value",
				_scalar(
					"select sum(custom_project_dollar_amount) from `tabProject` "
					"where project_type='Events' and status in ('Open','Active')"
				),
				"USD",
				"Project",
				metrics.HIGHER,
			)

	# --- inventory. Bin.stock_value is the stock ledger's own valuation (per-
	#     warehouse moving average) — the canonical on-hand value. The Item-master
	#     valuation_rate is a single static rate that under-reports materially. ---
	if _exists("Bin"):
		add(
			"inventory_stock_value",
			"Inventory Stock Value",
			_scalar("select sum(stock_value) from `tabBin`"),
			"USD",
			"Bin",
			metrics.HIGHER,
		)
		add(
			"out_of_stock_sellable",
			"Out-of-Stock Sellable Items",
			_scalar(
				"select count(*) from `tabItem` i where i.is_sales_item=1 and i.is_stock_item=1 and i.disabled=0 "
				"and coalesce((select sum(actual_qty) from `tabBin` b where b.item_code=i.name),0) <= 0"
			),
			"count",
			"Item",
			metrics.LOWER,
		)

	# --- catalog data-quality completeness % ---
	total_sellable = flt(_scalar("select count(*) from `tabItem` where is_sales_item=1 and disabled=0"))
	if total_sellable:
		if has("Item", "custom_sku"):
			with_sku = flt(_scalar("select count(*) from `tabItem` where is_sales_item=1 and disabled=0 and coalesce(custom_sku,'')<>''"))
			add("sku_completeness_pct", "SKU Completeness", with_sku / total_sellable * 100.0, "%", "Item", metrics.HIGHER)
		if has("Item", "custom_item_identifier"):
			with_id = flt(
				_scalar("select count(*) from `tabItem` where is_sales_item=1 and disabled=0 and coalesce(custom_item_identifier,'')<>''")
			)
			add("item_identifier_completeness_pct", "Item Identifier Completeness", with_id / total_sellable * 100.0, "%", "Item", metrics.HIGHER)
	if has("Item", "custom_rated_gpm") and has("Item", "custom_pump_hp"):
		pumps = flt(_scalar("select count(*) from `tabItem` where item_group='Pumps'"))
		if pumps:
			pumps_ok = flt(
				_scalar("select count(*) from `tabItem` where item_group='Pumps' and coalesce(custom_rated_gpm,0)<>0 and coalesce(custom_pump_hp,0)<>0")
			)
			add("pump_spec_completeness_pct", "Pump Spec Completeness", pumps_ok / pumps * 100.0, "%", "Item", metrics.HIGHER)

	# --- SEMI: gross margin needs COGS via perpetual-inventory incoming_rate on
	#     submitted Sales Invoice lines. Skip unless it is actually populated. ---
	if _exists("Sales Invoice Item") and has("Sales Invoice Item", "incoming_rate") and has("Sales Invoice Item", "stock_qty"):
		rev_365 = _scalar(
			"select sum(sii.base_net_amount) from `tabSales Invoice Item` sii join `tabSales Invoice` si on si.name=sii.parent "
			"where si.docstatus=1 and si.posting_date >= %(d)s",
			{"d": d365},
		)
		cogs_365 = _scalar(
			"select sum(sii.incoming_rate*sii.stock_qty) from `tabSales Invoice Item` sii join `tabSales Invoice` si on si.name=sii.parent "
			"where si.docstatus=1 and si.posting_date >= %(d)s",
			{"d": d365},
		)
		if rev_365 and cogs_365:
			add("gross_margin_pct", "Gross Margin (1y)", (flt(rev_365) - flt(cogs_365)) / flt(rev_365) * 100.0, "%", "Sales Invoice", metrics.HIGHER)

	# --- SEMI: items whose on-hand qty is below a configured reorder level. ---
	if _exists("Item Reorder") and _exists("Bin") and has("Item Reorder", "warehouse_reorder_level"):
		add(
			"items_below_reorder",
			"Items Below Reorder",
			_scalar(
				"select count(distinct r.parent) from `tabItem Reorder` r "
				"where coalesce(r.warehouse_reorder_level,0) > 0 and "
				"coalesce((select sum(b.actual_qty) from `tabBin` b where b.item_code=r.parent),0) < r.warehouse_reorder_level"
			),
			"count",
			"Item Reorder",
			metrics.LOWER,
		)

	# --- SEMI: design fountain-type mix (only if Water Feature Design carries it). ---
	if _exists("Water Feature Design") and has("Water Feature Design", "fountain_type"):
		distinct_ft = _scalar("select count(distinct fountain_type) from `tabWater Feature Design` where coalesce(fountain_type,'')<>''")
		if distinct_ft:
			add("design_fountain_types", "Distinct Fountain Types Designed", distinct_ft, "count", "Water Feature Design", metrics.HIGHER)

	return {"values": values, "freshness": {}}


def _hr_metrics():
	"""HR / People — Employee-master-driven. The hrms app is not installed on this
	site (no Attendance / Leave / Job Opening / Salary tables; payroll lives in
	QuickBooks), so the automatic KPIs read only ``tabEmployee``, which is fully
	populated (joining/relieving dates, department, designation).

	Small-n stance (14 active employees): headline KPIs are counts; the only rate
	KPIs use a 365-day window — one exit at n=14 moves a turnover rate ~7 points,
	so a 90-day rate would whipsaw. Historical headcount for the turnover
	denominator is reconstructed from date_of_joining/relieving_date, so no
	snapshot history is needed.

	Time-tracking KPIs (Job Interval / Timesheet) are guarded and self-suppressing:
	sum() over zero rows is NULL, which ``add`` skips — they appear the day crews
	start clocking in. The two manual KPIs read the newest HR Stat Entry row and
	carry a freshness entry so a forgotten month shows the stale badge.
	"""
	today = getdate(nowdate())
	d30 = add_days(today, -30)
	d90 = add_days(today, -90)
	d365 = add_days(today, -365)
	values, add = _collector()
	freshness = {}

	def _headcount_on(day):
		"""Employed on ``day``, reconstructed from joining/relieving dates.
		Inactive/Suspended count as employed (they are); Left rows missing a
		relieving_date are excluded rather than counted as employed forever."""
		return flt(
			_scalar(
				"select count(*) from `tabEmployee` "
				"where date_of_joining is not null and date_of_joining <= %(d)s "
				"and (relieving_date is null or relieving_date > %(d)s) "
				"and not (status='Left' and relieving_date is null)",
				{"d": day},
			)
		)

	# --- headcount & mix ---
	active = flt(_scalar("select count(*) from `tabEmployee` where status='Active'"))
	add("active_headcount", "Active Headcount", active, "count", "Employee", metrics.HIGHER)
	add(
		"full_time_count",
		"Full-Time Employees",
		_scalar("select count(*) from `tabEmployee` where status='Active' and employment_type='Full-time'"),
		"count",
		"Employee",
		metrics.HIGHER,
	)
	if active:
		classified = flt(
			_scalar("select count(*) from `tabEmployee` where status='Active' and coalesce(employment_type,'')<>''")
		)
		add(
			"employment_type_completeness_pct",
			"Employment Type Completeness",
			classified / active * 100.0,
			"%",
			"Employee",
			metrics.HIGHER,
		)

	# --- hiring & separations (counts headline both windows so the raw numerator
	#     always sits next to the 12-month rate). Windows are half-open
	#     (a, b] to match _headcount_on's "relieving_date > day" convention: an
	#     employee relieved exactly on the window-start day is already absent
	#     from the start headcount, so counting them as a window separation
	#     would put them in the turnover numerator but neither endpoint. ---
	hires_90 = flt(
		_scalar(
			"select count(*) from `tabEmployee` where date_of_joining > %(a)s and date_of_joining <= %(b)s",
			{"a": d90, "b": today},
		)
	)
	seps_90 = flt(
		_scalar(
			"select count(*) from `tabEmployee` where status='Left' and relieving_date > %(a)s and relieving_date <= %(b)s",
			{"a": d90, "b": today},
		)
	)
	add("new_hires_90d", "New Hires (90d)", hires_90, "count", "Employee", metrics.HIGHER)
	add("separations_90d", "Separations (90d)", seps_90, "count", "Employee", metrics.LOWER)
	add("net_headcount_change_90d", "Net Headcount Change (90d)", hires_90 - seps_90, "count", "Employee", metrics.HIGHER)
	add(
		"new_hires_365",
		"New Hires (1y)",
		_scalar(
			"select count(*) from `tabEmployee` where date_of_joining > %(a)s and date_of_joining <= %(b)s",
			{"a": d365, "b": today},
		),
		"count",
		"Employee",
		metrics.HIGHER,
	)
	seps_365 = flt(
		_scalar(
			"select count(*) from `tabEmployee` where status='Left' and relieving_date > %(a)s and relieving_date <= %(b)s",
			{"a": d365, "b": today},
		)
	)
	add("separations_365", "Separations (1y)", seps_365, "count", "Employee", metrics.LOWER)
	add(
		"turnover_rate_12m",
		"Turnover Rate (12m)",
		metrics.turnover_rate_pct(seps_365, _headcount_on(d365), _headcount_on(today)),
		"%",
		"Employee",
		metrics.LOWER,
	)

	# --- tenure ---
	add(
		"avg_tenure_years",
		"Avg Tenure (Active)",
		_scalar(
			"select avg(datediff(%(t)s, date_of_joining)) / 365.25 from `tabEmployee` "
			"where status='Active' and date_of_joining is not null",
			{"t": today},
		),
		"years",
		"Employee",
		metrics.HIGHER,
	)
	add(
		"avg_tenure_at_exit_12m",
		"Avg Tenure at Exit (12m)",
		_scalar(
			# Same (a, b] window as separations_365 — the upper bound matters:
			# a Left row with a future relieving_date (notice period logged in
			# advance) is not yet an exit and _headcount_on still counts it.
			"select avg(datediff(relieving_date, date_of_joining)) / 365.25 from `tabEmployee` "
			"where status='Left' and date_of_joining is not null "
			"and relieving_date > %(a)s and relieving_date <= %(b)s",
			{"a": d365, "b": today},
		),
		"years",
		"Employee",
		metrics.HIGHER,
	)

	# --- org shape ---
	row = frappe.db.sql(
		"select count(*), count(distinct e.reports_to) from `tabEmployee` e "
		"join `tabEmployee` m on m.name = e.reports_to and m.status='Active' "
		"where e.status='Active'"
	)
	directs, managers = (flt(row[0][0]), flt(row[0][1])) if row and row[0] else (0.0, 0.0)
	add("span_of_control", "Avg Directs per Manager", (directs / managers) if managers else None, "", "Employee", metrics.HIGHER)

	# --- SEMI: workforce time. Sum-based and guarded so these stay silent until
	#     the time doctypes carry real data (today Job Interval is empty and the
	#     only Timesheets are draft test rows). ---
	if _exists("Job Interval"):
		field_hours_30 = _scalar(
			"select sum(greatest(timestampdiff(second, start_time, end_time) - coalesce(total_paused_seconds, 0), 0)) / 3600.0 "
			"from `tabJob Interval` where status='Completed' and end_time >= %(d)s",
			{"d": d30},
		)
		add("field_labor_hours_30d", "Field Labor Hours (30d)", field_hours_30, "hours", "Job Interval", metrics.HIGHER)
		if field_hours_30 is not None:
			# Only meaningful once intervals exist — a standing 0 before the
			# kiosk rollout would read as "nobody works here".
			add(
				"field_staff_clocking_30d",
				"Field Staff Clocking In (30d)",
				_scalar("select count(distinct employee) from `tabJob Interval` where start_time >= %(d)s", {"d": d30}),
				"count",
				"Job Interval",
				metrics.HIGHER,
			)
	if _exists("Timesheet"):
		add(
			"timesheet_hours_30",
			"Timesheet Hours (30d)",
			_scalar("select sum(total_hours) from `tabTimesheet` where docstatus=1 and start_date >= %(d)s", {"d": d30}),
			"hours",
			"Timesheet",
			metrics.HIGHER,
		)

	# --- MANUAL: monthly HR Stat Entry paste (open roles, eNPS). Newest row wins;
	#     an entry older than the previous calendar month flags the source stale. ---
	if _exists("HR Stat Entry"):
		latest = frappe.get_all(
			"HR Stat Entry", fields=["month", "open_positions", "enps"], order_by="month desc", limit=1
		)
		if latest:
			entry = latest[0]
			add("open_positions", "Open Positions", entry.open_positions, "count", "HR Stat Entry", metrics.LOWER)
			# Int fields store 0 when unfilled, and an eNPS of exactly 0 is rare —
			# treat 0 as "not surveyed" (documented on the field) so a blank month
			# doesn't masquerade as a neutral score.
			enps = cint(entry.enps)
			add("enps", "eNPS", enps if enps else None, "", "HR Stat Entry", metrics.HIGHER)
			prev_month_start = add_months(today.replace(day=1), -1)
			freshness["HR Stat Entry"] = {
				"last_sync": str(entry.month),
				"stale": bool(entry.month and getdate(entry.month) < prev_month_start),
			}

	return {"values": values, "freshness": freshness}


AGGREGATORS = {
	"Finance": _finance_metrics,
	"Sales": _sales_metrics,
	"Operations": _operations_metrics,
	"Design": _design_metrics,
	"Production": _production_metrics,
	"Marketing": _marketing_metrics,
	# Product is built before Executive so a future exec rollup can read it.
	"Product": _product_metrics,
	# HR is built before Executive so the exec rollup reads today's turnover.
	"HR": _hr_metrics,
	# Executive is intentionally last: the nightly batch builds it after the
	# source departments, so its rollup reads today's fresh snapshots.
	"Executive": _executive_metrics,
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


def _sum_dataset(chart, name_contains):
	"""Sum a Frappe-charts dataset (``{labels, datasets:[{name, values}]}``) whose
	name contains ``name_contains`` (case-insensitive). None if absent/unparseable."""
	if not isinstance(chart, dict):
		return None
	for ds in chart.get("datasets") or []:
		if name_contains.lower() in str(ds.get("name", "")).lower():
			try:
				return sum(int(v or 0) for v in ds.get("values") or [])
			except (TypeError, ValueError):
				return None
	return None


def snapshot_marketing_web():
	"""Pull GA4 + Search Console once and cache the 30-day totals in Marketing
	Web Snapshot. Called at the head of the nightly batch so the Marketing
	aggregator reads cached numbers instead of calling Google in the snapshot
	path. Fully guarded: never raises into the batch; skips quietly when GA4 is
	not configured (the common case on most sites)."""
	if not _exists("Marketing Web Snapshot") or not _exists("GA4 Settings"):
		return
	try:
		if not frappe.db.get_single_value("GA4 Settings", "ga4_property_id"):
			return
	except Exception:
		return

	sessions = users = clicks = impressions = None
	ga4_ok = gsc_ok = False
	errors = []
	try:
		from erpnext_enhancements.api.analytics import get_ga4_data

		ga = get_ga4_data()
		if isinstance(ga, dict) and ga.get("error"):
			errors.append("GA4: " + str(ga.get("error")))
		elif isinstance(ga, dict):
			tl = ga.get("traffic_timeline") or {}
			sessions = _sum_dataset(tl, "session")
			users = _sum_dataset(tl, "active user")
			ga4_ok = True
	except Exception:
		errors.append("GA4 exception")
		frappe.log_error(frappe.get_traceback(), "KPI marketing web — GA4")

	try:
		from erpnext_enhancements.api.analytics import get_gsc_data

		gsc = get_gsc_data()
		if isinstance(gsc, dict) and gsc.get("error"):
			errors.append("GSC: " + str(gsc.get("error")))
		elif isinstance(gsc, dict):
			st = gsc.get("search_timeline") or {}
			clicks = _sum_dataset(st, "click")
			impressions = _sum_dataset(st, "impression")
			gsc_ok = True
	except Exception:
		errors.append("GSC exception")
		frappe.log_error(frappe.get_traceback(), "KPI marketing web — GSC")

	status = f"GA4 {'✓' if ga4_ok else '✗'} · GSC {'✓' if gsc_ok else '✗'}"
	today = nowdate()
	name = f"MWS-{today}"
	if frappe.db.exists("Marketing Web Snapshot", name):
		frappe.delete_doc("Marketing Web Snapshot", name, force=True, ignore_permissions=True)
	doc = frappe.new_doc("Marketing Web Snapshot")
	doc.snapshot_date = today
	doc.generated_at = now_datetime()
	doc.sessions_30 = sessions
	doc.active_users_30 = users
	doc.organic_clicks_30 = clicks
	doc.organic_impressions_30 = impressions
	doc.source_status = status
	doc.ga4_ok = 1 if ga4_ok else 0
	doc.gsc_ok = 1 if gsc_ok else 0
	doc.pull_error = ("; ".join(errors))[:300] or None
	doc.insert(ignore_permissions=True)


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
	# Refresh the cached GA4/GSC pull first so the Marketing aggregator reads
	# today's web numbers. Guarded — a slow/failed pull never blocks the batch.
	try:
		snapshot_marketing_web()
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "KPI snapshot batch — marketing web")
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
