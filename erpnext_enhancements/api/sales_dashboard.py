# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whitelisted feeds for the Sales Dashboard widgets.

Four worklists, all live: a queue is only worth showing if it is true right now,
so unlike the KPI Cockpit (which reads the nightly snapshot) these query the
source doctypes on load. They are cheap — each is one indexed query with a small
LIMIT.

Gating is the shared department contract in ``api/dashboard_widgets.py``: Sales
roles per ``api/kpi.py``, plus a per-widget toggle that defaults OFF.

The KPI counterparts of three of these live in ``kpi_dashboards/snapshots.py``
(``stalled_opportunities``, ``handoff_backlog``, ``contracts_expiring_60``).
Thresholds are deliberately kept identical to the snapshot's where one exists —
a widget that disagrees with the number above it is worse than no widget.
"""

import frappe
from frappe.utils import add_days, flt, getdate, now_datetime, nowdate, time_diff_in_hours

from erpnext_enhancements.api.dashboard_widgets import fetch_all, widget_feed

ROW_LIMIT = 20

# Lead statuses that mean "no longer waiting on us for a first touch". Expressed
# as an exclusion list on purpose: the Lead status Select is site-configurable,
# and a new status should default to "still needs a response" rather than
# silently dropping out of the queue.
LEAD_CLOSED_STATUSES = ("Converted", "Opportunity", "Quotation", "Lost Quotation", "Do Not Contact")

# Matches the `stalled_opportunities` KPI's window (snapshots._sales_metrics).
STALLED_DAYS = 14

# Contract renewal horizon. Wider than the `contracts_expiring_60` KPI because a
# renewal conversation starts before the number turns red.
RENEWAL_HORIZON_DAYS = 90

OPEN_OPPORTUNITY_STATUSES = ("Closed Won", "Lost", "Closed", "Converted")


def _days_since(value):
	if not value:
		return None
	return max(0, (getdate(nowdate()) - getdate(value)).days)


@frappe.whitelist()
@widget_feed("Sales", "speed_to_lead")
def get_speed_to_lead():
	"""Leads still waiting on their first outbound touch, oldest first.

	"Responded" is the existence of a Sent Communication against the Lead — the
	same signal the desk timeline shows, so a rep who emailed from the Lead form
	drops off this list without any extra bookkeeping. Inbound-only threads
	(``sent_or_received = 'Received'``) do not count as a response.
	"""
	has_source = frappe.db.has_column("Lead", "custom_lead_source")
	source_col = "l.custom_lead_source" if has_source else "null"
	placeholders = ", ".join(["%(s" + str(i) + ")s" for i in range(len(LEAD_CLOSED_STATUSES))])
	params = {"s" + str(i): s for i, s in enumerate(LEAD_CLOSED_STATUSES)}
	params["since"] = add_days(getdate(nowdate()), -30)
	params["limit"] = ROW_LIMIT

	rows = frappe.db.sql(
		f"""
		select l.name, l.lead_name, l.company_name, l.status, l.creation,
		       l.lead_owner, l.email_id, l.mobile_no, {source_col} as lead_source
		from `tabLead` l
		where l.status not in ({placeholders})
		  and l.creation >= %(since)s
		  and not exists (
		        select 1 from `tabCommunication` c
		        where c.reference_doctype = 'Lead'
		          and c.reference_name = l.name
		          and c.sent_or_received = 'Sent'
		  )
		order by l.creation asc
		limit %(limit)s
		""",
		params,
		as_dict=True,
	)

	now = now_datetime()
	leads = []
	for r in rows:
		hours = time_diff_in_hours(now, r.creation)
		leads.append(
			{
				"name": r.name,
				"title": r.lead_name or r.company_name or r.name,
				"company": r.company_name or "",
				"status": r.status or "",
				"owner": r.lead_owner or "",
				"source": r.lead_source or "",
				"contact": r.email_id or r.mobile_no or "",
				"hours_waiting": round(flt(hours), 1),
			}
		)
	return {"leads": leads}


@frappe.whitelist()
@widget_feed("Sales", "stalled_deals")
def get_stalled_deals():
	"""Open Opportunities with no edit for ``STALLED_DAYS``, oldest touch first.

	``modified`` is the staleness signal for the same reason the KPI uses it: it
	is the only column every write path touches. It does mean a bulk edit can
	reset a deal's clock without anyone having spoken to the customer.
	"""
	cutoff = add_days(getdate(nowdate()), -STALLED_DAYS)
	rows = fetch_all(
		"Opportunity",
		filters={"status": ("not in", OPEN_OPPORTUNITY_STATUSES), "modified": ("<", cutoff)},
		fields=[
			"name",
			"customer_name",
			"party_name",
			"opportunity_amount",
			"sales_stage",
			"status",
			"opportunity_owner",
			"modified",
		],
		order_by="modified asc",
		limit=ROW_LIMIT,
	)

	deals = [
		{
			"name": r.name,
			"title": r.customer_name or r.party_name or r.name,
			"amount": flt(r.opportunity_amount),
			"stage": r.sales_stage or r.status or "",
			"owner": r.opportunity_owner or "",
			"idle_days": _days_since(r.modified),
		}
		for r in rows
	]
	return {"deals": deals, "threshold_days": STALLED_DAYS}


@frappe.whitelist()
@widget_feed("Sales", "handoff_backlog")
def get_handoff_backlog():
	"""Closed-Won Opportunities with no Project yet, longest wait first.

	The hand-off engine stamps ``custom_created_project`` when it turns a won deal
	into a Project; an empty one is the gap. Both custom columns are guarded —
	without the CRM customizations this widget correctly reports an empty queue
	rather than failing the whole dashboard.
	"""
	if not frappe.db.has_column("Opportunity", "custom_created_project"):
		return {"deals": [], "unavailable": "The Closed-Won hand-off customization is not installed."}

	has_won_date = frappe.db.has_column("Opportunity", "custom_date_closed_won")
	fields = ["name", "customer_name", "party_name", "opportunity_amount", "modified"]
	if has_won_date:
		fields.append("custom_date_closed_won")

	rows = fetch_all(
		"Opportunity",
		filters={"status": "Closed Won", "custom_created_project": ("in", ["", None])},
		fields=fields,
		order_by=("custom_date_closed_won asc" if has_won_date else "modified asc"),
		limit=ROW_LIMIT,
	)

	deals = []
	for r in rows:
		won_on = r.get("custom_date_closed_won") if has_won_date else r.modified
		deals.append(
			{
				"name": r.name,
				"title": r.customer_name or r.party_name or r.name,
				"amount": flt(r.opportunity_amount),
				"won_on": str(getdate(won_on)) if won_on else None,
				"waiting_days": _days_since(won_on),
			}
		)
	return {"deals": deals}


@frappe.whitelist()
@widget_feed("Sales", "renewal_radar")
def get_renewal_radar():
	"""Active maintenance contracts ending inside the renewal horizon.

	``auto_renew`` contracts are included rather than filtered out: a renewal that
	happens automatically is still a price conversation, and one carrying a
	non-renewal notice is the opposite of safe to ignore — both are flagged so the
	list sorts by date and reads by risk.
	"""
	today = getdate(nowdate())
	rows = fetch_all(
		"Sapphire Maintenance Contract",
		filters={"status": "Active", "end_date": ("between", [today, add_days(today, RENEWAL_HORIZON_DAYS)])},
		fields=[
			"name",
			"customer",
			"project",
			"end_date",
			"recurring_amount",
			"auto_renew",
			"non_renewal_notice",
		],
		order_by="end_date asc",
		limit=ROW_LIMIT,
	)

	contracts = [
		{
			"name": r.name,
			"customer": r.customer or "",
			"project": r.project or "",
			"end_date": str(getdate(r.end_date)) if r.end_date else None,
			"days_left": (getdate(r.end_date) - today).days if r.end_date else None,
			"amount": flt(r.recurring_amount),
			"auto_renew": bool(r.auto_renew),
			"non_renewing": bool(r.non_renewal_notice),
		}
		for r in rows
	]
	return {"contracts": contracts, "horizon_days": RENEWAL_HORIZON_DAYS}
