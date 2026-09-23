# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Social Post Performance: each post on each network, from reach to revenue (TASK-2026-01488).

The organic twin of Ad Spend ROAS. Engagement is each job's newest Social Post Metric row (a
lifetime total); leads, deals and revenue join on the tracking tags every link to our own site
carries since v1.516.0 (``utm_content`` = the post, ``utm_source`` = the network). The rules are in
``marketing/publish/performance.py``, which is pure and tested; this file only fetches.

Revenue columns make this a financial report, so it is for System Manager, Sales Manager and
Marketing Manager -- not Marketing Team, which a marketing hire holds without financials
(TASK-2026-01486).

Every custom column is guarded with ``has_column``, so the report renders, empty, on a bench where
the fixtures have not landed.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, nowdate

from erpnext_enhancements.marketing.core import roas
from erpnext_enhancements.marketing.publish import metrics, performance, tracking

UTM_FIELDS = ("custom_utm_content", "custom_utm_source")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	from_date = getdate(filters.get("from_date") or add_days(nowdate(), -90))
	to_date = getdate(filters.get("to_date") or nowdate())
	window = cint(filters.get("window_days")) or roas.DEFAULT_WINDOW_DAYS
	network = filters.get("network") or None

	jobs = _jobs(from_date, to_date, network)
	posts = _posts({j["social_post"] for j in jobs})
	slugs = {tracking.slug(p["name"]) for p in posts}
	rows = performance.build(
		posts,
		jobs,
		_metric_rows([j["name"] for j in jobs]),
		_records("Lead", slugs),
		_opportunities(slugs),
		window,
	)
	if network:
		rows = [r for r in rows if r["network"] in (network, performance.NETWORK_NOT_RECORDED)]
	return get_columns(), rows, _message(rows)


# ---------------------------------------------------------------- fetch


def _jobs(from_date, to_date, network):
	filters = [
		["state", "=", "Published"],
		["published_at", ">=", from_date],
		["published_at", "<", add_days(to_date, 1)],
	]
	if network:
		filters.append(["network", "=", network])
	jobs = [
		dict(row)
		for row in frappe.get_all(
			"Social Publish Job",
			filters=filters,
			fields=["name", "social_post", "social_account", "network", "published_at", "permalink"],
			limit_page_length=0,
		)
	]
	labels = {
		row.name: row.account_name or row.name
		for row in frappe.get_all(
			"Social Account",
			filters={"name": ["in", sorted({j["social_account"] for j in jobs}) or [""]]},
			fields=["name", "account_name"],
		)
	}
	for job in jobs:
		job["account"] = labels.get(job["social_account"], job["social_account"])
	return jobs


def _posts(names):
	if not names:
		return []
	return [
		dict(r)
		for r in frappe.get_all(
			"Social Post", filters={"name": ["in", sorted(names)]}, fields=["name", "title"]
		)
	]


def _metric_rows(job_names):
	out = {}
	if not job_names:
		return out
	for row in frappe.get_all(
		"Social Post Metric",
		filters={"publish_job": ["in", job_names]},
		fields=["publish_job", "metric_date", *metrics.FIGURES],
		limit_page_length=0,
	):
		out.setdefault(row.publish_job, []).append(dict(row))
	return out


def _utm_columns(doctype, alias):
	return ", ".join(
		f"{alias}.{f}" if frappe.db.has_column(doctype, f) else f"NULL AS {f}" for f in UTM_FIELDS
	)


def _records(doctype, slugs):
	"""Leads whose ``utm_content`` names one of the posts (compared lower-case, as tagged)."""
	if not slugs or not frappe.db.has_column(doctype, "custom_utm_content"):
		return []
	return frappe.db.sql(
		f"""
		SELECT t.name, t.creation, {_utm_columns(doctype, "t")}
		FROM `tab{doctype}` t
		WHERE LOWER(TRIM(t.custom_utm_content)) IN %(slugs)s
		""",
		{"slugs": tuple(sorted(slugs))},
		as_dict=True,
	)


def _opportunities(slugs):
	if not slugs or not frappe.db.has_column("Opportunity", "custom_utm_content"):
		return []
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
		       {_utm_columns("Opportunity", "o")},
		       p.name AS project, p.total_billed_amount
		FROM `tabOpportunity` o
		{project_join}
		WHERE LOWER(TRIM(o.custom_utm_content)) IN %(slugs)s
		""",
		{"slugs": tuple(sorted(slugs))},
		as_dict=True,
	)


# ---------------------------------------------------------------- shape


def get_columns():
	count = {"fieldtype": "Int", "width": 95}
	money = {"fieldtype": "Currency", "width": 115}
	return [
		{
			"label": _("Post"),
			"fieldname": "social_post",
			"fieldtype": "Link",
			"options": "Social Post",
			"width": 115,
		},
		{"label": _("Title"), "fieldname": "post_title", "fieldtype": "Data", "width": 220},
		{"label": _("Network"), "fieldname": "network", "fieldtype": "Data", "width": 100},
		{"label": _("Account"), "fieldname": "account", "fieldtype": "Data", "width": 150},
		{"label": _("Published"), "fieldname": "published_at", "fieldtype": "Datetime", "width": 150},
		{"label": _("Impressions"), "fieldname": "impressions", **count},
		{"label": _("Reach"), "fieldname": "reach", **count},
		{"label": _("Engagements"), "fieldname": "engagements", **count},
		{"label": _("Link Clicks"), "fieldname": "clicks", **count},
		{"label": _("Video Views"), "fieldname": "video_views", **count},
		{"label": _("Leads"), "fieldname": "leads", **count},
		{"label": _("Opportunities"), "fieldname": "opportunities", **count},
		{"label": _("Won"), "fieldname": "won", **count},
		{"label": _("Contract Value"), "fieldname": "contract_value", **money},
		{"label": _("Invoiced"), "fieldname": "invoiced", **money},
		{"label": _("Link"), "fieldname": "permalink", "fieldtype": "Data", "width": 1, "hidden": 1},
	]


def _message(rows):
	if not rows:
		return _("No posts were published in this range.")
	return _(
		"Engagement is each post's lifetime total as of its newest pull (Instagram can be up to 48 "
		"hours behind, YouTube two or three days). Leads and deals join on the tracking tags on "
		"links to our own site, so they count only once the website capture is installed "
		"(docs/website-capture/README.md), and only for links sent since v1.516.0. A blank figure "
		"is one the network does not report, not a zero."
	)
