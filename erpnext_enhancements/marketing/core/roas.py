# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The spend -> Lead -> Opportunity -> Project -> invoice join, as pure functions.

TASK-2026-01477: cost per lead, cost per won project, and ROAS against **booked revenue**,
not against what the ad platforms say converted. Standard library only; the report
(``marketing/report/ad_spend_roas``) fetches rows and hands them here, and
``tests/test_ad_spend_roas.py`` drives it with plain dicts.

## How a record finds its campaign (TASK-2026-01570, decided A + D)

1. ``custom_utm_id`` -> ``Ad Campaign.external_id`` -- each platform's own campaign ID,
   written by its URL macro. Exact. If the same ID exists on two platforms (it should
   not), ``custom_utm_source`` picks the platform; if that cannot decide, the record is
   left unjoined rather than guessed.
2. ``custom_gclid`` -> ``Ad Click`` -> campaign -- Google's click report (decision D).
3. Neither: a record with a paid signal (a click ID, a ``utm_id``, a paid ``utm_medium``,
   or Lead Source *Advertisement*) is **paid but unjoinable** and gets its own row, so a
   coverage gap reads as a gap and never as a free lead. Everything else is not paid and is
   left out.

## Why Opportunities are joined on their own fields

Attribution is propagated onto every Opportunity (``attribution.propagate_to_opportunity``,
from the Lead or, far more often here, from the Customer: 168 of 172 Opportunities created
in 2026 were Customer-party). So an Opportunity carries the same ``custom_utm_id`` /
``custom_gclid`` its Lead did, and joins directly -- no walking Lead -> Customer ->
Opportunity, which would miss every Customer that was not created from a Lead.

## Cohorts and the attribution window (decided 2026-09-22)

Rows are **cohorts by lead month**: a month's spend is set against the Leads that month
produced and against the Opportunities those Leads became, whenever they close. An
Opportunity's cohort is its ``custom_attribution_captured_on`` month -- the moment the
campaign touched the customer. Recent cohorts are incomplete and fill in as deals close.

Because Customer attribution is first-touch and propagates to every later deal, a repeat
customer's Opportunity years on would otherwise count against the ad that found them.
``window_days`` (default 365) caps how long after the touch an Opportunity still counts.

## Revenue: two columns (decided 2026-09-22)

* **Contract value** -- the won Opportunity's ``opportunity_amount``: known at signing.
* **Invoiced** -- the linked Project's ``total_billed_amount``: erpnext's own sum of
  submitted Sales Invoices, pre-tax, credit notes netted. The figure the Value Stream
  Performance report already uses, so the two cannot disagree.
"""

import datetime
from collections import defaultdict

WON_STATUSES = ("Closed Won",)
DEFAULT_WINDOW_DAYS = 365

UNJOINABLE = "(paid, not joinable)"

#: utm_source values -> platform, for disambiguating a utm_id.
SOURCE_PLATFORM = {
	"google": "Google Ads",
	"adwords": "Google Ads",
	"facebook": "Meta Ads",
	"fb": "Meta Ads",
	"instagram": "Meta Ads",
	"ig": "Meta Ads",
	"meta": "Meta Ads",
	"linkedin": "LinkedIn Ads",
	"li": "LinkedIn Ads",
}

VIA_UTM_ID = "utm_id"
VIA_GCLID = "gclid"


def month_of(value):
	"""``YYYY-MM`` for a date, datetime or ISO string; None for blank."""
	if not value:
		return None
	if isinstance(value, (datetime.date, datetime.datetime)):
		return f"{value:%Y-%m}"
	return str(value)[:7]


def _as_datetime(value):
	if isinstance(value, datetime.datetime):
		return value
	if isinstance(value, datetime.date):
		return datetime.datetime.combine(value, datetime.time())
	if not value:
		return None
	return datetime.datetime.fromisoformat(str(value)[:19])


def index_campaigns(campaigns):
	"""``{external_id: [campaign, ...]}`` -- a list, because IDs are unique per platform only."""
	index = defaultdict(list)
	for campaign in campaigns:
		if campaign.get("external_id"):
			index[str(campaign["external_id"])].append(campaign)
	return index


def resolve(record, campaigns_by_id, campaigns_by_name, clicks_by_gclid):
	"""``(campaign, via)`` for a Lead or Opportunity row; ``(None, None)`` when unjoined."""
	utm_id = (record.get("custom_utm_id") or "").strip()
	if utm_id:
		candidates = campaigns_by_id.get(utm_id) or []
		if len(candidates) > 1:
			platform = SOURCE_PLATFORM.get((record.get("custom_utm_source") or "").strip().lower())
			candidates = [c for c in candidates if c.get("platform") == platform]
		if len(candidates) == 1:
			return candidates[0], VIA_UTM_ID
	gclid = (record.get("custom_gclid") or "").strip()
	campaign = campaigns_by_name.get(clicks_by_gclid.get(gclid)) if gclid else None
	if campaign:
		return campaign, VIA_GCLID
	return None, None


def is_paid(record, paid_mediums):
	"""Any sign this record came from a paid click."""
	return bool(
		(record.get("custom_gclid") or "").strip()
		or (record.get("custom_utm_id") or "").strip()
		or (record.get("custom_utm_medium") or "").strip().lower() in paid_mediums
		or (record.get("custom_lead_source") or "") == "Advertisement"
	)


def _blank_row(month, platform, campaign, campaign_name):
	return {
		"month": month,
		"platform": platform,
		"campaign": campaign,
		"campaign_name": campaign_name,
		"spend": 0.0,
		"impressions": 0,
		"clicks": 0,
		"leads": 0,
		"leads_via_utm_id": 0,
		"leads_via_gclid": 0,
		"opportunities": 0,
		"won": 0,
		"won_projects": 0,
		"contract_value": 0.0,
		"invoiced": 0.0,
	}


def build(
	spend,
	leads,
	opportunities,
	campaigns,
	clicks_by_gclid,
	*,
	paid_mediums,
	window_days=DEFAULT_WINDOW_DAYS,
	group_by="campaign",
):
	"""Aggregate into report rows, one per (month, campaign) or (month, platform).

	``spend``: ``{campaign, metric_date, spend, impressions, clicks}``.
	``leads``: ``{name, creation, custom_utm_id, custom_gclid, custom_utm_source, custom_utm_medium, custom_lead_source}``.
	``opportunities``: the same attribution keys plus ``status, creation, opportunity_amount,
	custom_attribution_captured_on, project, total_billed_amount``.
	``campaigns``: ``{name, external_id, campaign_name, platform}``.
	``clicks_by_gclid``: ``{gclid: campaign name}``.

	Returns ``(rows, coverage)``. Metrics (cost per lead, ROAS...) are filled by
	:func:`finish`.
	"""
	by_id = index_campaigns(campaigns)
	by_name = {c["name"]: c for c in campaigns}
	rows = {}
	coverage = {"paid_leads": 0, "joined_utm_id": 0, "joined_gclid": 0, "unjoinable": 0, "outside_window": 0}

	def row_for(month, campaign):
		if campaign is None:
			key = (month, UNJOINABLE)
			if key not in rows:
				rows[key] = _blank_row(month, "", None, UNJOINABLE)
			return rows[key]
		if group_by == "platform":
			key = (month, campaign.get("platform") or "")
			if key not in rows:
				rows[key] = _blank_row(month, campaign.get("platform") or "", None, "")
			return rows[key]
		key = (month, campaign["name"])
		if key not in rows:
			rows[key] = _blank_row(
				month, campaign.get("platform") or "", campaign["name"], campaign.get("campaign_name") or ""
			)
		return rows[key]

	for s in spend:
		campaign = by_name.get(s.get("campaign"))
		if not campaign:
			continue
		row = row_for(month_of(s.get("metric_date")), campaign)
		row["spend"] += float(s.get("spend") or 0)
		row["impressions"] += int(s.get("impressions") or 0)
		row["clicks"] += int(s.get("clicks") or 0)

	for lead in leads:
		if not is_paid(lead, paid_mediums):
			continue
		coverage["paid_leads"] += 1
		campaign, via = resolve(lead, by_id, by_name, clicks_by_gclid)
		row = row_for(month_of(lead.get("creation")), campaign)
		row["leads"] += 1
		if via == VIA_UTM_ID:
			row["leads_via_utm_id"] += 1
			coverage["joined_utm_id"] += 1
		elif via == VIA_GCLID:
			row["leads_via_gclid"] += 1
			coverage["joined_gclid"] += 1
		else:
			coverage["unjoinable"] += 1

	for opp in opportunities:
		if not is_paid(opp, paid_mediums):
			continue
		touched = _as_datetime(opp.get("custom_attribution_captured_on") or opp.get("creation"))
		created = _as_datetime(opp.get("creation"))
		if touched and created and (created - touched).days > window_days:
			coverage["outside_window"] += 1
			continue
		campaign, _via = resolve(opp, by_id, by_name, clicks_by_gclid)
		row = row_for(month_of(touched), campaign)
		row["opportunities"] += 1
		if opp.get("status") in WON_STATUSES:
			row["won"] += 1
			row["contract_value"] += float(opp.get("opportunity_amount") or 0)
			if opp.get("project"):
				row["won_projects"] += 1
				row["invoiced"] += float(opp.get("total_billed_amount") or 0)

	return finish(
		sorted(rows.values(), key=lambda r: (r["month"] or "", r["platform"], r["campaign_name"]))
	), coverage


def _ratio(numerator, denominator):
	return round(numerator / denominator, 2) if denominator else None


def finish(rows):
	"""Add the derived columns. A ratio with a zero denominator is None, never 0 or inf."""
	for row in rows:
		row["cost_per_lead"] = _ratio(row["spend"], row["leads"])
		row["cost_per_won_project"] = _ratio(row["spend"], row["won_projects"])
		row["roas_contract"] = _ratio(row["contract_value"], row["spend"])
		row["roas_invoiced"] = _ratio(row["invoiced"], row["spend"])
		row["spend"] = round(row["spend"], 2)
		row["contract_value"] = round(row["contract_value"], 2)
		row["invoiced"] = round(row["invoiced"], 2)
	return rows
