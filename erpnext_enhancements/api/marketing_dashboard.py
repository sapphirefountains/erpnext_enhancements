# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whitelisted feeds for the Marketing Dashboard widgets.

The funnel, spend against leads, the attribution gap as a fixable worklist, and
whether the nightly web pulls are actually running.

Gating is the shared department contract in ``api/dashboard_widgets.py``:
Marketing roles per ``api/kpi.py``, plus a per-widget toggle defaulting OFF.

**Attribution reads ``custom_lead_source``, never ``source``.** erpnext v15
renamed ``Lead.source`` to ``utm_source`` and frappe never drops columns, so
queries against ``source`` still run — they just read a frozen pre-2023 snapshot.
That mistake cost four KPIs their meaning (see ``kpi_dashboards/README.md``);
``tests/test_marketing_dashboard.py`` fails if it reappears here.

The ``Unknown (pre-Aug 2026)`` bucket counts as **unsourced**. It is a recorded
gap, not a channel, and counting it as attributed would launder the exact hole
these widgets exist to expose.
"""

import frappe
from frappe.utils import add_days, flt, getdate, nowdate

from erpnext_enhancements.api.dashboard_widgets import fetch_all, widget_feed
from erpnext_enhancements.crm_enhancements.attribution import UNKNOWN_LEAD_SOURCE

ROW_LIMIT = 20

# A web snapshot older than this means the nightly pull has stopped landing.
SNAPSHOT_STALE_DAYS = 2


def _doctype_exists(doctype):
	return bool(frappe.db.exists("DocType", doctype))


def _lead_source_column():
	"""The attributed-source column, or None where the customization is absent."""
	return "custom_lead_source" if frappe.db.has_column("Lead", "custom_lead_source") else None


def _count(doctype, filters):
	return frappe.db.count(doctype, filters)


def _rate(numerator, denominator):
	return (flt(numerator) / flt(denominator) * 100.0) if denominator else None


@frappe.whitelist()
@widget_feed("Marketing", "funnel_cascade")
def get_funnel_cascade():
	"""Sessions to leads to opportunities to won, with the rate at each step.

	Sessions come from the cached nightly GA4 pull, never a live call, and only
	when that pull succeeded — a GA4 outage shows a blank step rather than a zero,
	because a zero here reads as "the website got no traffic".
	"""
	today = getdate(nowdate())
	d30 = add_days(today, -30)
	d90 = add_days(today, -90)

	sessions = None
	if _doctype_exists("Marketing Web Snapshot"):
		rows = fetch_all(
			"Marketing Web Snapshot",
			fields=["sessions_30", "ga4_ok"],
			order_by="snapshot_date desc",
			limit=1,
		)
		if rows and rows[0].ga4_ok:
			sessions = flt(rows[0].sessions_30)

	def window(since):
		leads = _count("Lead", {"creation": (">=", since)})
		converted = _count("Lead", {"status": "Converted", "creation": (">=", since)})
		opps = _count("Opportunity", {"creation": (">=", since)})
		won_col = (
			"custom_date_closed_won"
			if frappe.db.has_column("Opportunity", "custom_date_closed_won")
			else "modified"
		)
		won = _count("Opportunity", {"status": "Closed Won", won_col: (">=", since)})
		return {
			"leads": leads,
			"converted": converted,
			"opportunities": opps,
			"won": won,
			"lead_to_opp_pct": _rate(converted, leads),
			"opp_to_won_pct": _rate(won, opps),
		}

	last_30 = window(d30)
	last_30["sessions"] = sessions
	last_30["session_to_lead_pct"] = _rate(last_30["leads"], sessions) if sessions else None

	return {"last_30": last_30, "last_90": window(d90)}


@frappe.whitelist()
@widget_feed("Marketing", "channel_spend")
def get_channel_spend():
	"""Month-to-date spend per channel with the resulting cost per lead.

	Spend channels are free text on ``Marketing Spend`` while lead attribution is a
	Link to ``Lead Source``, so they are matched case-insensitively on the trimmed
	name. A channel that does not match any lead source is **still listed**, with
	no CPL — silently dropping it would hide the exact case worth investigating
	(money going somewhere that produces no attributed leads).
	"""
	if not _doctype_exists("Marketing Spend"):
		return {"channels": [], "unavailable": "Marketing Spend is not installed."}

	today = getdate(nowdate())
	month_start = today.replace(day=1)

	spend_rows = fetch_all(
		"Marketing Spend",
		filters={"month": (">=", month_start)},
		fields=["channel", "amount"],
		limit=200,
	)
	spend = {}
	for r in spend_rows:
		key = (r.channel or "").strip()
		if not key:
			continue
		spend[key] = spend.get(key, 0.0) + flt(r.amount)

	leads_by_source = {}
	source_col = _lead_source_column()
	if source_col:
		rows = frappe.db.sql(
			f"""
			select coalesce({source_col}, '') as source, count(*) as leads
			from `tabLead`
			where creation >= %(since)s
			group by coalesce({source_col}, '')
			""",
			{"since": month_start},
			as_dict=True,
		)
		for r in rows:
			key = (r.source or "").strip().lower()
			if key and key != UNKNOWN_LEAD_SOURCE.lower():
				leads_by_source[key] = int(r.leads or 0)

	channels = []
	for name, amount in spend.items():
		leads = leads_by_source.get(name.lower())
		channels.append(
			{
				"channel": name,
				"spend": amount,
				"leads": leads,
				"cpl": (amount / leads) if leads else None,
				"matched": leads is not None,
			}
		)
	channels.sort(key=lambda c: c["spend"], reverse=True)

	total_spend = sum(c["spend"] for c in channels)
	attributed_leads = sum(c["leads"] or 0 for c in channels)
	return {
		"channels": channels[:ROW_LIMIT],
		"total_spend": total_spend,
		"attributed_leads": attributed_leads,
		"blended_cpl": (total_spend / attributed_leads) if attributed_leads else None,
		"month": str(month_start),
		"attribution_available": bool(source_col),
	}


@frappe.whitelist()
@widget_feed("Marketing", "unsourced_leads")
def get_unsourced_leads():
	"""Recent leads with no attributed source — the gap, as rows someone can fix."""
	source_col = _lead_source_column()
	if not source_col:
		return {"leads": [], "unavailable": "The lead attribution customization is not installed."}

	since = add_days(getdate(nowdate()), -30)
	rows = frappe.db.sql(
		f"""
		select name, lead_name, company_name, status, creation, lead_owner
		from `tabLead`
		where creation >= %(since)s
		  and (coalesce({source_col}, '') = '' or {source_col} = %(unknown)s)
		order by creation desc
		limit %(limit)s
		""",
		{"since": since, "unknown": UNKNOWN_LEAD_SOURCE, "limit": ROW_LIMIT},
		as_dict=True,
	)

	total_unsourced = flt(
		frappe.db.sql(
			f"""
			select count(*) from `tabLead`
			where creation >= %(since)s
			  and (coalesce({source_col}, '') = '' or {source_col} = %(unknown)s)
			""",
			{"since": since, "unknown": UNKNOWN_LEAD_SOURCE},
		)[0][0]
	)
	total_leads = _count("Lead", {"creation": (">=", since)})

	leads = [
		{
			"name": r.name,
			"title": r.lead_name or r.company_name or r.name,
			"company": r.company_name or "",
			"status": r.status or "",
			"owner": r.lead_owner or "",
			"created": str(getdate(r.creation)) if r.creation else None,
		}
		for r in rows
	]
	return {
		"leads": leads,
		"unsourced_count": int(total_unsourced),
		"total_count": int(total_leads),
		"unsourced_pct": _rate(total_unsourced, total_leads),
	}


@frappe.whitelist()
@widget_feed("Marketing", "source_health")
def get_source_health():
	"""Whether the nightly GA4 / Search Console pulls are landing.

	Search Console failed 40 consecutive nightly pulls with no signal beyond a
	``pull_error`` field nobody opens. This surfaces the ok-flags, the snapshot
	date, and the error text where the team already looks.
	"""
	if not _doctype_exists("Marketing Web Snapshot"):
		return {"sources": [], "unavailable": "Marketing Web Snapshot is not installed."}

	rows = fetch_all(
		"Marketing Web Snapshot",
		fields=[
			"snapshot_date",
			"generated_at",
			"ga4_ok",
			"gsc_ok",
			"pull_error",
			"source_status",
			"sessions_30",
			"organic_clicks_30",
		],
		order_by="snapshot_date desc",
		limit=1,
	)
	if not rows:
		return {"sources": [], "unavailable": "No web snapshot has been taken yet."}

	snap = rows[0]
	snapshot_date = getdate(snap.snapshot_date) if snap.snapshot_date else None
	stale = bool(snapshot_date and (getdate(nowdate()) - snapshot_date).days > SNAPSHOT_STALE_DAYS)

	sources = [
		{
			"name": "Google Analytics 4",
			"ok": bool(snap.ga4_ok),
			"detail": f"{int(flt(snap.sessions_30)):,} sessions (30d)" if snap.ga4_ok else "",
		},
		{
			"name": "Search Console",
			"ok": bool(snap.gsc_ok),
			"detail": f"{int(flt(snap.organic_clicks_30)):,} clicks (30d)" if snap.gsc_ok else "",
		},
	]
	return {
		"sources": sources,
		"snapshot_date": str(snapshot_date) if snapshot_date else None,
		"stale": stale,
		"stale_after_days": SNAPSHOT_STALE_DAYS,
		"error": (snap.pull_error or "").strip(),
		"status_note": (snap.source_status or "").strip(),
	}
