# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Marketing Performance page's one read: every section for a date range (v1.756.0).

``get_performance`` is POST, starts with the role check (``rules.views_for``: the three marketing
roles plus Sales Manager), and leaves out what the viewer may not see rather than blanking it:
money for anyone without a financial role, who called for anyone below Sales Manager, the
Opportunity rows of the fix list for anyone who cannot read Opportunity. Every read is stored data
-- nothing here calls Google or any other platform.

Reads run with permissions off (``frappe.db.sql``, ``ignore_permissions``) because the gate is the
role check above, the same contract the department dashboard widgets used
(``api/dashboard_widgets.fetch_all``): a shared board must not empty out for one viewer's User
Permissions.

**Inquiries are Opportunities**, sourced from ``custom_lead_source`` (never ``source``, the frozen
pre-2023 column), plus first-time callers. Sapphire works in Opportunities: 12 Leads against 108
Opportunities in the 180 days to 2026-10-29. "Unknown (pre-Aug 2026)" counts as unsourced, as it
always has: it is a recorded gap, not a channel.
"""

import datetime

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, getdate, nowdate

from erpnext_enhancements.marketing.performance import rules

GA4 = "GA4 Daily Metric"
GSC = "Search Console Daily Metric"
GBP = "Business Profile Daily Metric"
SOCIAL = "Social Page Daily Metric"

LANDING_PAGE_ROWS = 50
UNSOURCED_ROWS = 25
CALL_ROWS = 25
#: A web source whose newest day is older than this has stopped landing.
STALE_DAYS = 4


def _exists(doctype):
	return bool(frappe.db.exists("DocType", doctype))


def _has_column(doctype, column):
	try:
		return bool(frappe.db.has_column(doctype, column))
	except Exception:
		return False


def _end_exclusive(end):
	return end + datetime.timedelta(days=1)


# ---------------------------------------------------------------- web


def _daily_totals(doctype, columns, start, end):
	if not _exists(doctype):
		return {}
	select = ", ".join(f"sum({c}) as {c}" for c in columns)
	rows = frappe.db.sql(
		f"""select metric_date, {select} from `tab{doctype}`
		where dimension = 'Total' and metric_date between %s and %s group by metric_date""",
		(start, end),
		as_dict=True,
	)
	return {getdate(r.metric_date): r for r in rows}


def _coverage(doctype):
	if not _exists(doctype):
		return None
	low, high = frappe.db.sql(
		f"select min(metric_date), max(metric_date) from `tab{doctype}` where dimension = 'Total'"
	)[0]
	return (getdate(low), getdate(high)) if low and high else None


def _dimension_rows(doctype, dimension, columns, start, end, weighted_position=False):
	if not _exists(doctype):
		return []
	sums = [f"sum({c}) as {c}" for c in columns]
	if weighted_position:
		sums.append("sum(position * impressions) / nullif(sum(impressions), 0) as position")
	return frappe.db.sql(
		f"""select dimension_value as value, {", ".join(sums)} from `tab{doctype}`
		where dimension = %s and metric_date between %s and %s group by dimension_value""",
		(dimension, start, end),
		as_dict=True,
	)


def _web_totals(start, end):
	ga = _daily_totals(GA4, ("sessions", "total_users", "engaged_sessions", "key_events"), start, end)
	gsc = _daily_totals(GSC, ("clicks", "impressions"), start, end)
	return (
		{
			"sessions": sum(cint(r.sessions) for r in ga.values()) if ga else None,
			"engaged_sessions": sum(cint(r.engaged_sessions) for r in ga.values()) if ga else None,
			"key_events": sum(flt(r.key_events) for r in ga.values()) if ga else None,
			"organic_clicks": sum(cint(r.clicks) for r in gsc.values()) if gsc else None,
			"organic_impressions": sum(cint(r.impressions) for r in gsc.values()) if gsc else None,
		},
		ga,
		gsc,
	)


# ---------------------------------------------------------------- Opportunities


def _opportunities(start, end):
	"""Opportunities created in the range: day, Customer, Contact, source."""
	source = "o.custom_lead_source" if _has_column("Opportunity", "custom_lead_source") else "null"
	title = (
		"coalesce(nullif(o.custom_opportunity_name, ''), o.title, o.name)"
		if _has_column("Opportunity", "custom_opportunity_name")
		else "coalesce(o.title, o.name)"
	)
	rows = frappe.db.sql(
		f"""select o.name, o.creation, o.opportunity_from, o.party_name, o.contact_person, o.status,
			{source} as lead_source, {title} as title
		from `tabOpportunity` o
		where o.creation >= %s and o.creation < %s
		order by o.creation desc""",
		(start, _end_exclusive(end)),
		as_dict=True,
	)
	return [
		{
			"name": r.name,
			"day": getdate(r.creation),
			"customer": r.party_name if r.opportunity_from == "Customer" else None,
			"contact": r.contact_person,
			"lead_source": r.lead_source,
			"title": r.title,
			"status": r.status,
		}
		for r in rows
	]


def _won(start, end):
	from erpnext_enhancements.crm_enhancements.historical import exclude_sql

	won_col = "custom_date_closed_won" if _has_column("Opportunity", "custom_date_closed_won") else "modified"
	return cint(
		frappe.db.sql(
			f"""select count(*) from `tabOpportunity` o
			where o.status = 'Closed Won' and o.{won_col} >= %s and o.{won_col} < %s and {exclude_sql("o")}""",
			(start, _end_exclusive(end)),
		)[0][0]
	)


# ---------------------------------------------------------------- calls


def _calls(end):
	"""Every incoming call up to ``end``, with its Customer and Contact links. Days are site-local.

	``creation`` rather than ``start_time``: Triton writes ``start_time`` in UTC, six or seven hours
	off the site's own clock, so an evening call would land on the next day.
	"""
	if not _exists("Call Log"):
		return []
	rows = frappe.db.sql(
		"""select c.name, c.creation, c.`from` as number, c.customer
		from `tabCall Log` c
		where c.type = 'Incoming' and c.creation < %s
		order by c.creation""",
		(_end_exclusive(end),),
		as_dict=True,
	)
	links = {}
	for link in frappe.db.sql(
		"""select parent, link_doctype, link_name from `tabDynamic Link`
		where parenttype = 'Call Log' and link_doctype in ('Customer', 'Contact')""",
		as_dict=True,
	):
		entry = links.setdefault(link.parent, {"customers": set(), "contacts": set()})
		entry["customers" if link.link_doctype == "Customer" else "contacts"].add(link.link_name)
	out = []
	for r in rows:
		entry = links.get(r.name, {"customers": set(), "contacts": set()})
		out.append(
			{
				"name": r.name,
				"day": getdate(r.creation),
				"at": r.creation,
				"number": r.number,
				"caller": r.customer,
				"customers": entry["customers"],
				"contacts": entry["contacts"],
			}
		)
	return out


def _call_section(calls, opportunities, start, end, views):
	summary = rules.summarize_calls(calls, opportunities, start, end)
	section = {
		"incoming": summary["incoming"],
		"first_time": summary["first_time"],
		"converted_calls": summary["converted_calls"],
		"opportunities": len(summary["opportunities"]),
	}
	if views["call_details"]:
		recent = [c for c in calls if start <= c["day"] <= end][-CALL_ROWS:]
		section["rows"] = [
			{
				"name": c["name"],
				"at": str(c["at"]),
				"caller": c["caller"],
				"number": c["number"],
				"first_time": c["name"] in summary["first_time_names"],
				"opportunities": summary["converted"].get(c["name"], []),
			}
			for c in reversed(recent)
		]
	return section, summary


# ---------------------------------------------------------------- spend


def _spend_section(start, end, views):
	"""Ad platforms' clicks (everyone) and spend (financial roles), offline spend, and ROAS."""
	out = {"platforms": [], "connected": [], "offline": None, "roas": None}
	from erpnext_enhancements.marketing.core import constants as C
	from erpnext_enhancements.marketing.core.utils import get_credentials, get_settings, is_connected

	try:
		settings, creds = get_settings(), get_credentials()
		out["enabled"] = bool(cint(settings.get("enabled")))
		out["connected"] = [p for p in C.PLATFORMS if is_connected(p, creds)]
	except Exception:
		out["enabled"] = False
	if _exists("Ad Daily Metric"):
		for r in frappe.db.sql(
			"""select platform, sum(impressions) as impressions, sum(clicks) as clicks,
				sum(spend) as spend, max(currency) as currency
			from `tabAd Daily Metric` where metric_date between %s and %s group by platform""",
			(start, end),
			as_dict=True,
		):
			row = {"platform": r.platform, "impressions": cint(r.impressions), "clicks": cint(r.clicks)}
			if views["financial"]:
				row.update(spend=flt(r.spend), currency=r.currency)
			out["platforms"].append(row)
	if not views["financial"]:
		return out
	if _exists("Marketing Spend"):
		rows = frappe.db.sql(
			"""select channel, sum(amount) as amount from `tabMarketing Spend`
			where month between %s and %s group by channel order by amount desc""",
			(start.replace(day=1), end),
			as_dict=True,
		)
		out["offline"] = [{"channel": r.channel, "amount": flt(r.amount)} for r in rows]
	if out["platforms"]:
		out["roas"] = _roas(start, end)
	return out


def _roas(start, end):
	"""The Ad Spend ROAS report's rows over the range, summed per platform."""
	from erpnext_enhancements.marketing.report.ad_spend_roas import ad_spend_roas

	try:
		_columns, rows, *_rest = ad_spend_roas.execute(
			{"from_date": start, "to_date": end, "group_by": "Platform"}
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Marketing Performance: ROAS")
		return None
	keys = ("spend", "clicks", "leads", "opportunities", "won", "contract_value", "invoiced")
	by_platform = {}
	for row in rows or ():
		p = by_platform.setdefault(row.get("platform") or "", {k: 0.0 for k in keys})
		for key in keys:
			p[key] += flt(row.get(key))
	out = []
	for platform, p in by_platform.items():
		p["platform"] = platform
		p["cost_per_lead"] = rules.ratio(p["spend"], p["leads"])
		p["roas_contract"] = rules.ratio(p["contract_value"], p["spend"])
		p["roas_invoiced"] = rules.ratio(p["invoiced"], p["spend"])
		out.append(p)
	return out


# ---------------------------------------------------------------- Business Profile and social


def _business_profile(start, end):
	from erpnext_enhancements.marketing.core.utils import get_credentials, get_settings, is_connected
	from erpnext_enhancements.marketing.gbp import constants as G

	out = {"enabled": False, "connected": False, "listings": []}
	try:
		settings = get_settings()
		out["enabled"] = bool(cint(settings.get("enabled"))) and bool(cint(settings.get(G.ENABLED_FLAG)))
		out["connected"] = is_connected(G.PLATFORM_GBP, get_credentials())
	except Exception:
		pass
	if not _exists(GBP):
		return out
	cols = ("call_clicks", "website_clicks", "direction_requests", "maps_impressions", "search_impressions")
	for r in frappe.db.sql(
		f"""select location_id, max(location_title) as title, {", ".join(f"sum({c}) as {c}" for c in cols)}
		from `tab{GBP}` where metric_date between %s and %s group by location_id""",
		(start, end),
		as_dict=True,
	):
		listing = {"location_id": r.location_id, "title": r.title, **{c: cint(r[c]) for c in cols}}
		latest = frappe.db.sql(
			f"""select review_count, average_rating, unreplied_reviews, metric_date from `tab{GBP}`
			where location_id = %s and reviews_read = 1 order by metric_date desc limit 1""",
			(r.location_id,),
			as_dict=True,
		)
		if latest:
			listing.update(
				review_count=cint(latest[0].review_count),
				average_rating=flt(latest[0].average_rating),
				unreplied_reviews=cint(latest[0].unreplied_reviews),
				reviews_as_of=str(latest[0].metric_date),
			)
		out["listings"].append(listing)
	return out


def _social(start, end):
	from erpnext_enhancements.marketing.core.utils import get_settings
	from erpnext_enhancements.marketing.publish import page_stats

	out = {"enabled": False, "accounts": []}
	try:
		out["enabled"] = page_stats.enabled(get_settings())
	except Exception:
		pass
	if not _exists(SOCIAL):
		return out
	for r in frappe.db.sql(
		f"""select m.social_account, max(m.network) as network, max(a.account_name) as account_name,
			sum(m.reach) as reach, sum(m.views) as views, sum(m.follower_gain) as follower_gain
		from `tab{SOCIAL}` m left join `tabSocial Account` a on a.name = m.social_account
		where m.metric_date between %s and %s group by m.social_account""",
		(start, end),
		as_dict=True,
	):
		followers = frappe.db.sql(
			f"""select followers, metric_date from `tab{SOCIAL}` where social_account = %s
			and followers_read = 1 and metric_date <= %s order by metric_date desc limit 1""",
			(r.social_account, end),
			as_dict=True,
		)
		out["accounts"].append(
			{
				"account": r.social_account,
				"name": r.account_name or r.social_account,
				"network": r.network,
				# YouTube reports no reach; its rows hold the column's 0, which is not a figure.
				"reach": cint(r.reach) if r.reach is not None and r.network != "YouTube" else None,
				"follower_gain": cint(r.follower_gain) if r.network in ("LinkedIn", "YouTube") else None,
				"views": cint(r.views) if r.views is not None else None,
				"followers": cint(followers[0].followers) if followers else None,
			}
		)
	return out


# ---------------------------------------------------------------- health


def _health(today, ga_cover, gsc_cover, gbp, social, spend):
	def web(name, cover):
		if cover is None:
			return {"name": name, "state": "empty", "detail": _("Nothing stored yet. Run the backfill.")}
		age = (today - cover[1]).days
		return {
			"name": name,
			"state": "ok" if age <= STALE_DAYS else "stale",
			"detail": _("Through {0}, since {1}").format(cover[1].isoformat(), cover[0].isoformat()),
		}

	sources = [web(_("Google Analytics 4"), ga_cover), web(_("Search Console"), gsc_cover)]
	snapshot = None
	if _exists("Marketing Web Snapshot"):
		rows = frappe.get_all(
			"Marketing Web Snapshot",
			fields=["snapshot_date", "ga4_ok", "gsc_ok", "pull_error"],
			order_by="snapshot_date desc",
			limit=1,
			ignore_permissions=True,
		)
		if rows:
			snapshot = {
				"date": str(rows[0].snapshot_date),
				"ga4_ok": bool(rows[0].ga4_ok),
				"gsc_ok": bool(rows[0].gsc_ok),
				"error": (rows[0].pull_error or "").strip(),
			}
	sources.append(
		{
			"name": _("Google Business Profile"),
			"state": "ok" if gbp["connected"] and gbp["enabled"] else "off",
			"detail": _("Connected")
			if gbp["connected"]
			else _("Not set up yet: Google approves API access first"),
		}
	)
	sources.append(
		{
			"name": _("Ad platforms"),
			"state": "ok" if spend.get("connected") else "off",
			"detail": ", ".join(spend.get("connected") or []) or _("Not set up yet"),
		}
	)
	sources.append(
		{
			"name": _("Social pages"),
			"state": "ok" if social["enabled"] and social["accounts"] else "off",
			"detail": _("{0} account(s)").format(len(social["accounts"]))
			if social["accounts"]
			else _("Not set up yet"),
		}
	)
	last_runs = []
	if _exists("Marketing Sync Log"):
		last_runs = frappe.db.sql(
			"""select platform, sync_type, status, started_at, finished_at, window_from, window_to,
				progress_through, created_count, error_message
			from `tabMarketing Sync Log`
			where platform in ('Google Analytics 4', 'Search Console', 'Google Business Profile', 'Social Pages')
			order by creation desc limit 8""",
			as_dict=True,
		)
	return {
		"sources": sources,
		"snapshot": snapshot,
		"runs": [
			{
				**{k: (str(v) if v is not None else None) for k, v in r.items() if k != "error_message"},
				"error": (r.error_message or "")[:300],
			}
			for r in last_runs
		],
	}


# ---------------------------------------------------------------- the endpoint


def _require():
	views = rules.views_for(frappe.get_roles())
	if not views["page"]:
		frappe.throw(
			_("Marketing Performance is for the marketing team and Sales Manager."), frappe.PermissionError
		)
	return views


def _period(start, end, views, calls):
	"""The headline figures for one range: the trend, the KPIs, the funnel.

	``calls`` is every incoming call up to the latest range asked for, read once per request.
	"""
	totals, ga, gsc = _web_totals(start, end)
	opportunities = _opportunities(start, end)
	call_section, call_summary = _call_section(calls, opportunities, start, end, views)
	# A first-time caller who became an Opportunity is one inquiry, not two.
	inquiries = len(opportunities) + call_summary["first_time_unconverted"]
	return {
		"totals": totals,
		"ga": ga,
		"gsc": gsc,
		"opportunities": opportunities,
		"calls": call_section,
		"call_summary": call_summary,
		"kpis": {
			"sessions": totals["sessions"],
			"organic_clicks": totals["organic_clicks"],
			"opportunities": len(opportunities),
			"calls": call_section["incoming"],
			"first_time_callers": call_section["first_time"],
			"key_events": totals["key_events"],
			"won": _won(start, end),
		},
		"inquiries": inquiries,
	}


@frappe.whitelist(methods=["POST"])
def get_performance(from_date=None, to_date=None, compare=0):
	"""Every section of the page for ``from_date``..``to_date`` (default: the last 28 days)."""
	views = _require()
	today = getdate(nowdate())
	start, end = rules.parse_range(from_date, to_date, today)
	days = rules.days_between(start, end)
	calls = _calls(end)
	current = _period(start, end, views, calls)

	previous = None
	if cint(compare):
		p_start, p_end = rules.previous_range(start, end)
		p = _period(p_start, p_end, views, calls)
		previous = {"from_date": str(p_start), "to_date": str(p_end), "kpis": p["kpis"]}

	ga_cover, gsc_cover = _coverage(GA4), _coverage(GSC)
	opp_per_day, calls_per_day = {}, {}
	for opp in current["opportunities"]:
		opp_per_day[opp["day"]] = opp_per_day.get(opp["day"], 0) + 1
	for day, d in current["call_summary"]["per_day"].items():
		calls_per_day[day] = d["incoming"]
	everything = (datetime.date.min, datetime.date.max)
	trend = rules.trend(
		days,
		{
			"sessions": {d: cint(r.sessions) for d, r in current["ga"].items()},
			"organic_clicks": {d: cint(r.clicks) for d, r in current["gsc"].items()},
			"opportunities": opp_per_day,
			"calls": calls_per_day,
		},
		{"sessions": ga_cover, "organic_clicks": gsc_cover, "opportunities": everything, "calls": everything},
	)

	pages = rules.join_landing_pages(
		_dimension_rows(
			GA4, "Landing Page", ("sessions", "total_users", "engaged_sessions", "key_events"), start, end
		),
		_dimension_rows(GSC, "Page", ("clicks", "impressions"), start, end, weighted_position=True),
	)
	try:
		from erpnext_enhancements.marketing.core.utils import get_settings

		terms = rules.branded_terms(get_settings().get("branded_terms"))
	except Exception:
		terms = rules.DEFAULT_BRANDED_TERMS
	queries = rules.summarize_queries(
		_dimension_rows(GSC, "Query", ("clicks", "impressions"), start, end, weighted_position=True), terms
	)
	channels = rules.summarize_channels(
		_dimension_rows(GA4, "Channel", ("sessions", "engaged_sessions", "key_events"), start, end)
	)

	from erpnext_enhancements.crm_enhancements.attribution import UNKNOWN_LEAD_SOURCE

	unsourced = [
		o for o in current["opportunities"] if rules.is_unsourced(o["lead_source"], UNKNOWN_LEAD_SOURCE)
	]
	fix_list = {"count": len(unsourced), "total": len(current["opportunities"])}
	if frappe.has_permission("Opportunity", "read"):
		fix_list["rows"] = [
			{
				"name": o["name"],
				"title": o["title"],
				"status": o["status"],
				"created": str(o["day"]),
				"lead_source": o["lead_source"] or "",
			}
			for o in unsourced[:UNSOURCED_ROWS]
		]

	spend = _spend_section(start, end, views)
	gbp = _business_profile(start, end)
	social = _social(start, end)
	return {
		"from_date": str(start),
		"to_date": str(end),
		"views": views,
		"kpis": current["kpis"],
		"previous": previous,
		"trend": trend,
		"landing_pages": pages[:LANDING_PAGE_ROWS],
		"flags": {
			rules.FLAG_LOW_CTR: rules.flagged(pages, rules.FLAG_LOW_CTR),
			rules.FLAG_STRIKING: rules.flagged(pages, rules.FLAG_STRIKING),
			rules.FLAG_NO_KEY_EVENTS: rules.flagged(pages, rules.FLAG_NO_KEY_EVENTS),
			"queries_striking": [q for q in queries["rows"] if rules.FLAG_STRIKING in q["flags"]][:15],
		},
		"queries": queries,
		"branded_terms": list(terms),
		"channels": channels,
		"funnel": rules.funnel(
			current["totals"]["sessions"],
			current["inquiries"],
			len(current["opportunities"]),
			current["kpis"]["won"],
		),
		"unsourced": fix_list,
		"calls": current["calls"],
		"spend": spend,
		"business_profile": gbp,
		"social": social,
		"health": _health(today, ga_cover, gsc_cover, gbp, social, spend),
		"generated_at": str(get_datetime()),
	}
