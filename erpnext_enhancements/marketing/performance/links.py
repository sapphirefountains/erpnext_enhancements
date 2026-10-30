# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Tracked links and their QR codes (F5, v1.757.0).

A Tracked Link is a tagged address on Sapphire's own site for something offline -- a flyer, a truck,
a trade-show banner -- and a QR code that holds that address itself. **There is no counting redirect
and no public endpoint**: a redirect would be a new guest URL to keep safe, and GA4 already counts the
visit by its tags. Results come back from the stored GA4 Campaign rows (``web_sync``) and, once the
WordPress capture is installed, from the inquiries whose ``custom_utm_*`` match.

The rules (own domain only, never ``utm_id``, values slugged) are ``insights.tracked_url``.
"""

import io

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, nowdate

from erpnext_enhancements.marketing.performance import insights, rules

GA4_DOCTYPE = "GA4 Daily Metric"


def qr_svg(text):
	"""An SVG QR code for ``text``, or "" when the library is missing.

	``pyqrcode`` ships with frappe (its two-factor setup draws QR codes with it), so no new dependency.
	"""
	try:
		import pyqrcode
	except ImportError:
		return ""
	buffer = io.BytesIO()
	pyqrcode.create(text, error="M").svg(buffer, scale=6, quiet_zone=4, xmldecl=False, svgns=True)
	return buffer.getvalue().decode("utf-8")


def refresh_link(doc):
	"""``Tracked Link.validate``: the tagged address and its QR code, from the fields."""
	try:
		url = insights.tracked_url(doc.destination, doc.utm_source, doc.utm_medium, doc.utm_campaign)
	except ValueError as exc:
		frappe.throw(_(str(exc)))
	from erpnext_enhancements.marketing.publish import tracking

	# Store the slugged values, so the record says exactly what the link carries.
	doc.utm_source = tracking.slug(doc.utm_source)
	doc.utm_medium = tracking.slug(doc.utm_medium)
	doc.utm_campaign = tracking.slug(doc.utm_campaign)
	# Rebuilt on every save, never kept: the form draws qr_svg as markup, so a value sent through the
	# API by anyone who may write the record must not survive to another person's browser.
	doc.tagged_url = url
	doc.qr_svg = qr_svg(url)


def _require():
	views = rules.views_for(frappe.get_roles())
	if not views["page"]:
		frappe.throw(_("Tracked links are for the marketing team and Sales Manager."), frappe.PermissionError)
	return views


@frappe.whitelist(methods=["POST"])
def get_link_results(name, from_date=None, to_date=None):
	"""Sessions and key events GA4 counted under the link's tags, and the inquiries that carry them."""
	_require()
	doc = frappe.get_doc("Tracked Link", name)
	doc.check_permission("read")
	today = getdate(nowdate())
	start, end = rules.parse_range(from_date or doc.creation, to_date, today)
	ga = {"sessions": 0, "engaged_sessions": 0, "key_events": 0}
	if frappe.db.exists("DocType", GA4_DOCTYPE):
		row = frappe.db.sql(
			f"""select sum(sessions) as sessions, sum(engaged_sessions) as engaged_sessions,
				sum(key_events) as key_events
			from `tab{GA4_DOCTYPE}`
			where dimension = 'Campaign' and lower(dimension_value) = %s and lower(source_medium) = %s
			  and metric_date between %s and %s""",
			(doc.utm_campaign, f"{doc.utm_source} / {doc.utm_medium}", start, end),
			as_dict=True,
		)
		if row:
			ga = {k: cint(row[0].get(k)) for k in ga}
	inquiries = 0
	if frappe.db.has_column("Opportunity", "custom_utm_campaign"):
		from erpnext_enhancements.crm_enhancements.historical import exclude_filters

		# creation is a datetime: "between" two dates would drop the last day after midnight.
		inquiries = frappe.db.count(
			"Opportunity",
			[
				["custom_utm_campaign", "=", doc.utm_campaign],
				["custom_utm_source", "=", doc.utm_source],
				["creation", ">=", start],
				["creation", "<", add_days(end, 1)],
				*exclude_filters(),
			],
		)
	return {"from_date": str(start), "to_date": str(end), **ga, "inquiries": inquiries}
