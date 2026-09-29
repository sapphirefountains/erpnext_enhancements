"""Web-page controller for the customer rental portal at ``/rentals`` (v1.565.0).

Authenticated: a guest goes to ``/login?redirect-to=`` this page (query kept), where customers sign
in with an emailed link (``portal_login``). Lists the signed-in customer's rentals; ``?booking=``
opens one, with its schedule, what is booked, its invoices (paid on ``/pay``), and the site-prep and
change-request forms. Every lookup is ownership-checked in ``asset_management/rental_portal.py``.
"""

from urllib.parse import quote

import frappe

from erpnext_enhancements.asset_management import rental_portal

no_cache = 1


def get_context(context):
	booking = frappe.form_dict.get("booking") or ""
	if frappe.session.user == "Guest":
		path = "/rentals" + (f"?booking={booking}" if booking else "")
		frappe.local.flags.redirect_location = "/login?redirect-to=" + quote(path, safe="/")
		raise frappe.Redirect

	context.no_cache = 1
	context.csrf_token = frappe.sessions.get_csrf_token()
	context.bookings = rental_portal.list_bookings()
	for row in context.bookings:
		row.status_label = rental_portal.CUSTOMER_STATUS.get(row.status, row.status)
	context.surfaces = rental_portal.SURFACES
	context.detail = None
	if booking:
		try:
			context.detail = rental_portal.booking_detail(booking)
		except frappe.PermissionError:
			frappe.clear_messages()
			context.not_found = True
	return context
