"""Frappe web-page controller for the company travel policy at
``/travel_guidelines``.

A static, login-gated policy document (the Sapphire Fountains "General Travel
Guidelines"), version-controlled here like every other customization. The
content lives in the sibling ``travel_guidelines.html``; policy wording maps
each rule onto the Travel Management flows (Travel Trip rows, Travel POIs,
Expense Claims, the Time Kiosk).

Linked from the Travel workspace shortcut and the /itinerary page footer.
"""

import frappe

no_cache = 1  # the guest gate must run per visitor


def get_context(context):
	"""Route: ``/travel_guidelines`` (rendered by ``travel_guidelines.html``).

	Guests are redirected to ``/login?redirect-to=/travel_guidelines`` —
	internal policy, signed-in employees only. The only dynamic data is the
	session's CSRF token, for the capture widget's POSTs (WI-079 slice 2).
	v16 mints a token only when something asks for one, and the base
	template's ``frappe.csrf_token`` otherwise renders the string ``None``.
	"""
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/travel_guidelines"
		raise frappe.Redirect

	context.no_cache = 1
	context.csrf_token = frappe.sessions.get_csrf_token()
	return context
