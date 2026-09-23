# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Which post produced what: engagement beside leads, won deals and revenue (TASK-2026-01488).

The organic half of the chain ``core/roas.py`` builds for paid, joined on the tags
``publish/tracking.py`` puts on every link to our own site:

* a Lead or Opportunity whose ``custom_utm_content`` is a post's name (as a slug) belongs to that
  post -- **exact**, the way ``utm_id`` is for paid;
* its ``custom_utm_source`` names the network (facebook, instagram, linkedin, youtube). A record
  that names the post but no network we know still counts, on a "network not recorded" row,
  rather than vanishing.

Revenue and the window are ``roas``'s, so the two reports cannot disagree: **contract value** is a
won Opportunity's ``opportunity_amount``, **invoiced** its Project's ``total_billed_amount``, and an
Opportunity created more than ``window_days`` after the touch it carries does not count (a repeat
customer's deal years later is not the post's).

The figures are each job's newest Social Post Metric row: a lifetime total (``metrics.py``).

Pure: rows in, rows out. ``report/social_post_performance`` fetches.
"""

from erpnext_enhancements.marketing.core import roas
from erpnext_enhancements.marketing.publish import metrics, tracking

NETWORK_NOT_RECORDED = "(network not recorded)"


def _blank(post, network, job=None):
	return {
		"social_post": post["name"],
		"post_title": post.get("title") or post["name"],
		"network": network,
		"account": (job or {}).get("account") or "",
		"published_at": (job or {}).get("published_at"),
		"permalink": (job or {}).get("permalink") or "",
		**dict.fromkeys(metrics.FIGURES),
		"leads": 0,
		"opportunities": 0,
		"won": 0,
		"contract_value": 0.0,
		"invoiced": 0.0,
	}


def build(posts, jobs, metric_rows, leads, opportunities, window_days=roas.DEFAULT_WINDOW_DAYS):
	"""One row per (post, network), plus a "network not recorded" row where one is needed.

	``posts``: ``{name, title}``. ``jobs``: ``{name, social_post, network, account, published_at,
	permalink}``. ``metric_rows``: ``{job: [Social Post Metric rows]}``. ``leads``: ``{name,
	custom_utm_content, custom_utm_source}``. ``opportunities``: the same plus ``status, creation,
	opportunity_amount, custom_attribution_captured_on, total_billed_amount, project``.
	"""
	by_slug = {tracking.slug(p["name"]): p for p in posts}
	rows = {}
	for job in jobs:
		post = next((p for p in posts if p["name"] == job["social_post"]), None)
		if post is None:
			continue
		row = rows.setdefault((post["name"], job["network"]), _blank(post, job["network"], job))
		figures = metrics.latest(metric_rows.get(job["name"]) or [])
		for key in metrics.FIGURES:
			if figures[key] is not None:
				row[key] = (row[key] or 0) + figures[key]

	def row_for(record):
		post = by_slug.get(tracking.post_key(record.get("custom_utm_content")))
		if post is None:
			return None
		network = tracking.network_of_source(record.get("custom_utm_source")) or NETWORK_NOT_RECORDED
		key = (post["name"], network)
		if key not in rows:
			rows[key] = _blank(post, key[1])
		return rows[key]

	for lead in leads:
		row = row_for(lead)
		if row is not None:
			row["leads"] += 1
	for opp in opportunities:
		row = row_for(opp)
		if row is None:
			continue
		touched = roas._as_datetime(opp.get("custom_attribution_captured_on") or opp.get("creation"))
		created = roas._as_datetime(opp.get("creation"))
		if touched and created and (created - touched).days > window_days:
			continue
		row["opportunities"] += 1
		if opp.get("status") in roas.WON_STATUSES:
			row["won"] += 1
			row["contract_value"] += float(opp.get("opportunity_amount") or 0)
			if opp.get("project"):
				row["invoiced"] += float(opp.get("total_billed_amount") or 0)

	out = sorted(
		rows.values(),
		key=lambda r: (str(r["published_at"] or ""), r["social_post"], r["network"] or ""),
		reverse=True,
	)
	for row in out:
		row["contract_value"] = round(row["contract_value"], 2)
		row["invoiced"] = round(row["invoiced"], 2)
	return out
