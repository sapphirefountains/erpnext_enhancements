# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Event markers on the marketing trend (F1, v1.757.0).

A **Marketing Event** is something that happened on a day -- a site edit, a post, a campaign, a mention
-- so a jump or a dip on the trend has its reason drawn beside it. Three ways in:

* **By hand**, in the Desk (Marketing Team and Marketing Manager may create them).
* **Automatically**, nightly (``fill_events``): each published Social Publish Job, and each new Campaign
  (ERPNext's Campaign carries no dates, so its creation day). ``auto_key`` names the record a row came
  from, unique, so the fill never adds one twice.
* **By an agent**, through ``log_marketing_event`` (POST, the page roles) or the assistant tool of the
  same name -- the SEO session logs its own site edits this way.
"""

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

from erpnext_enhancements.marketing.performance import rules

DOCTYPE = "Marketing Event"
TYPES = ("Site Edit", "Social Post", "Campaign", "Ad", "Press", "Offline", "Other")
#: The nightly fill looks back this far, so a missed night (the deploy flushes the queue) is caught up.
FILL_DAYS = 14


def _insert(values):
	"""Insert one event unless its ``auto_key`` exists already. Returns the name, or None."""
	key = values.get("auto_key")
	if key and frappe.db.exists(DOCTYPE, {"auto_key": key}):
		return None
	doc = frappe.get_doc({"doctype": DOCTYPE, **values})
	try:
		doc.insert(ignore_permissions=True)
	except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
		return None
	return doc.name


def fill_events(today=None):
	"""Cron entry (hooks.py, 04:05): published posts and new campaigns of the last ``FILL_DAYS`` as events."""
	if not frappe.db.exists("DocType", DOCTYPE):
		return 0
	today = getdate(today or nowdate())
	since = frappe.utils.add_days(today, -FILL_DAYS)
	added = 0
	if frappe.db.exists("DocType", "Social Publish Job"):
		for job in frappe.get_all(
			"Social Publish Job",
			filters={"state": "Published", "published_at": [">=", since]},
			fields=["name", "social_post", "network", "published_at"],
		):
			title = frappe.db.get_value("Social Post", job.social_post, "title") if job.social_post else None
			if _insert(
				{
					"event_date": getdate(job.published_at),
					"title": _("Posted on {0}: {1}").format(job.network, title or job.social_post)[:140],
					"event_type": "Social Post",
					"reference_doctype": "Social Post" if job.social_post else None,
					"reference_name": job.social_post,
					"source": "Automatic",
					"auto_key": f"Social Publish Job:{job.name}",
				}
			):
				added += 1
	for campaign in frappe.get_all(
		"Campaign", filters={"creation": [">=", since]}, fields=["name", "campaign_name", "creation"]
	):
		if _insert(
			{
				"event_date": getdate(campaign.creation),
				"title": _("Campaign started: {0}").format(campaign.campaign_name or campaign.name)[:140],
				"event_type": "Campaign",
				"reference_doctype": "Campaign",
				"reference_name": campaign.name,
				"source": "Automatic",
				"auto_key": f"Campaign:{campaign.name}",
			}
		):
			added += 1
	frappe.db.commit()
	return added


def events_between(start, end):
	"""The markers to draw: ``[{date, title, type, link}]``, oldest first."""
	if not frappe.db.exists("DocType", DOCTYPE):
		return []
	return [
		{"date": str(e.event_date), "title": e.title, "type": e.event_type, "link": e.link or ""}
		for e in frappe.get_all(
			DOCTYPE,
			filters={"event_date": ["between", [start, end]]},
			fields=["event_date", "title", "event_type", "link"],
			order_by="event_date asc",
			limit_page_length=500,
			ignore_permissions=True,
		)
	]


def create_event(event_date, title, event_type="Other", link=None, notes=None, source="Manual"):
	"""Validate and insert one event as the caller (Marketing Event's own DocPerms apply)."""
	day = rules.as_date(event_date)
	if not day:
		frappe.throw(_("A date is required, as YYYY-MM-DD."))
	title = (title or "").strip()
	if not title:
		frappe.throw(_("Say what happened in the title."))
	if event_type not in TYPES:
		frappe.throw(_("Type must be one of: {0}").format(", ".join(TYPES)))
	link = (link or "").strip() or None
	if link and not link.lower().startswith(("http://", "https://")):
		frappe.throw(_("The link must be an http(s) address."))
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"event_date": day,
			"title": title[:140],
			"event_type": event_type,
			"link": link,
			"notes": (notes or "").strip() or None,
			"source": source,
		}
	)
	doc.insert()
	return {"name": doc.name, "event_date": str(day), "title": doc.title}


@frappe.whitelist(methods=["POST"])
def log_marketing_event(event_date, title, event_type="Other", link=None, notes=None):
	"""Log a marker: a site edit, a post, a campaign. The page roles, and Marketing Event's create permission."""
	if not rules.views_for(frappe.get_roles())["page"]:
		frappe.throw(
			_("Marketing events are for the marketing team and Sales Manager."), frappe.PermissionError
		)
	# A call made with an API key or token is a program's (the SEO session's, say), not a person typing.
	auth = (
		(frappe.get_request_header("Authorization") or "") if getattr(frappe.local, "request", None) else ""
	)
	source = "Assistant" if auth.lower().startswith(("token ", "bearer ")) else "Manual"
	return create_event(event_date, title, event_type, link, notes, source=source)
