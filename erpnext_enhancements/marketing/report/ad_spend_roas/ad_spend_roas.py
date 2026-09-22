# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Ad Spend ROAS — what each campaign's spend bought, in booked revenue.

TASK-2026-01477. Ad Daily Metric -> Lead -> Opportunity -> Project -> Sales Invoice: cost
per lead, cost per won project, and ROAS against booked revenue (the won Opportunity's
contract value, and the Project's invoiced total), not against platform-reported
conversions. The rules -- how a record finds its campaign, the lead-month cohorts, the
attribution window, the two revenue columns -- are in ``marketing/core/roas.py``, which is
pure and tested; this file only fetches.

Every custom column is guarded with ``has_column`` so the report renders, empty, on a bench
where the fixtures have not landed.

**Read the newest months as incomplete.** Rows are cohorts by lead month: a month's spend
against the deals its leads produce, whenever they close. September's ROAS will rise for a
year.
"""

import frappe
from frappe import _
from frappe.utils import add_months, cint, get_first_day, getdate, nowdate

from erpnext_enhancements.crm_enhancements.attribution import PAID_MEDIUMS
from erpnext_enhancements.marketing.core import roas

ATTRIBUTION_FIELDS = (
	"custom_utm_id",
	"custom_gclid",
	"custom_utm_source",
	"custom_utm_medium",
	"custom_lead_source",
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	from_date = getdate(filters.get("from_date") or add_months(get_first_day(nowdate()), -11))
	to_date = getdate(filters.get("to_date") or nowdate())
	window = cint(filters.get("window_days")) or roas.DEFAULT_WINDOW_DAYS
	group_by = "platform" if filters.get("group_by") == "Platform" else "campaign"
	platform = filters.get("platform") or None

	campaigns = _campaigns(platform)
	leads = _leads(from_date, to_date)
	opportunities = _opportunities(from_date, to_date)
	clicks = _clicks(leads + opportunities)
	spend = _spend(from_date, to_date, {c["name"] for c in campaigns})

	rows, coverage = roas.build(
		spend,
		leads,
		opportunities,
		campaigns,
		clicks,
		paid_mediums=PAID_MEDIUMS,
		window_days=window,
		group_by=group_by,
	)
	if platform:
		rows = [r for r in rows if r["platform"] == platform]
	return get_columns(group_by), rows, _message(coverage, window)


# ---------------------------------------------------------------- fetch


def _campaigns(platform):
	if not frappe.db.table_exists("Ad Campaign"):
		return []
	filters = {"platform": platform} if platform else {}
	return frappe.get_all(
		"Ad Campaign", filters=filters, fields=["name", "external_id", "campaign_name", "platform"]
	)


def _spend(from_date, to_date, campaign_names):
	if not campaign_names or not frappe.db.table_exists("Ad Daily Metric"):
		return []
	return frappe.get_all(
		"Ad Daily Metric",
		filters={"metric_date": ["between", [from_date, to_date]], "campaign": ["in", list(campaign_names)]},
		fields=["campaign", "metric_date", "spend", "impressions", "clicks"],
		limit_page_length=0,
	)


def _columns(doctype, alias):
	"""The attribution columns that exist on ``doctype``, as ``alias.col``; blanks otherwise."""
	parts = []
	for fieldname in ATTRIBUTION_FIELDS:
		if frappe.db.has_column(doctype, fieldname):
			parts.append(f"{alias}.{fieldname}")
		else:
			parts.append(f"NULL AS {fieldname}")
	return ", ".join(parts)


def _leads(from_date, to_date):
	return frappe.db.sql(
		f"""
		SELECT l.name, l.creation, {_columns("Lead", "l")}
		FROM `tabLead` l
		WHERE l.creation >= %(from)s AND l.creation < DATE_ADD(%(to)s, INTERVAL 1 DAY)
		""",
		{"from": from_date, "to": to_date},
		as_dict=True,
	)


def _opportunities(from_date, to_date):
	touched = (
		"COALESCE(o.custom_attribution_captured_on, o.creation)"
		if frappe.db.has_column("Opportunity", "custom_attribution_captured_on")
		else "o.creation"
	)
	captured = (
		"o.custom_attribution_captured_on"
		if frappe.db.has_column("Opportunity", "custom_attribution_captured_on")
		else "NULL AS custom_attribution_captured_on"
	)
	project_join = (
		"LEFT JOIN `tabProject` p ON p.custom_opportunity = o.name"
		if frappe.db.has_column("Project", "custom_opportunity")
		else "LEFT JOIN `tabProject` p ON 1 = 0"
	)
	return frappe.db.sql(
		f"""
		SELECT o.name, o.status, o.creation, o.opportunity_amount, {captured},
		       {_columns("Opportunity", "o")},
		       p.name AS project, p.total_billed_amount
		FROM `tabOpportunity` o
		{project_join}
		WHERE {touched} >= %(from)s AND {touched} < DATE_ADD(%(to)s, INTERVAL 1 DAY)
		""",
		{"from": from_date, "to": to_date},
		as_dict=True,
	)


def _clicks(records):
	gclids = sorted({(r.get("custom_gclid") or "").strip() for r in records} - {""})
	if not gclids or not frappe.db.table_exists("Ad Click"):
		return {}
	rows = frappe.get_all("Ad Click", filters={"name": ["in", gclids]}, fields=["name", "campaign"])
	return {r.name: r.campaign for r in rows}


# ---------------------------------------------------------------- shape


def get_columns(group_by):
	columns = [{"label": _("Lead Month"), "fieldname": "month", "fieldtype": "Data", "width": 95}]
	columns.append({"label": _("Platform"), "fieldname": "platform", "fieldtype": "Data", "width": 110})
	if group_by == "campaign":
		columns += [
			{"label": _("Campaign"), "fieldname": "campaign_name", "fieldtype": "Data", "width": 220},
			{
				"label": _("Ad Campaign"),
				"fieldname": "campaign",
				"fieldtype": "Link",
				"options": "Ad Campaign",
				"width": 1,
				"hidden": 1,
			},
		]
	money = {"fieldtype": "Currency", "width": 115}
	count = {"fieldtype": "Int", "width": 80}
	ratio = {"fieldtype": "Float", "precision": 2, "width": 95}
	columns += [
		{"label": _("Spend"), "fieldname": "spend", **money},
		{"label": _("Clicks"), "fieldname": "clicks", **count},
		{"label": _("Leads"), "fieldname": "leads", **count},
		{"label": _("via utm_id"), "fieldname": "leads_via_utm_id", **count},
		{"label": _("via gclid"), "fieldname": "leads_via_gclid", **count},
		{"label": _("Opportunities"), "fieldname": "opportunities", **count},
		{"label": _("Won"), "fieldname": "won", **count},
		{"label": _("Won Projects"), "fieldname": "won_projects", **count},
		{"label": _("Contract Value"), "fieldname": "contract_value", **money},
		{"label": _("Invoiced"), "fieldname": "invoiced", **money},
		{"label": _("Cost / Lead"), "fieldname": "cost_per_lead", **money},
		{"label": _("Cost / Won Project"), "fieldname": "cost_per_won_project", **money},
		{"label": _("ROAS (contract)"), "fieldname": "roas_contract", **ratio},
		{"label": _("ROAS (invoiced)"), "fieldname": "roas_invoiced", **ratio},
	]
	return columns


def _message(coverage, window):
	paid = coverage["paid_leads"]
	if not paid:
		return _(
			"No paid leads in this range. Until the website capture is installed and the ads carry "
			"utm_id (docs/website-capture/README.md), spend shows here with nothing to set it against."
		)
	return _(
		"{0} paid leads: {1} joined by utm_id, {2} by gclid, <b>{3} not joinable</b> (shown as their "
		"own row, never as free leads). Opportunities count for {4} days after the ad touched the "
		"customer; {5} fell outside that window. Rows are cohorts by lead month, so recent months "
		"fill in as deals close."
	).format(
		paid,
		coverage["joined_utm_id"],
		coverage["joined_gclid"],
		coverage["unjoinable"],
		window,
		coverage["outside_window"],
	)
