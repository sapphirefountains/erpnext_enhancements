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
	internal policy, signed-in employees only. No dynamic data beyond that.
	"""
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/travel_guidelines"
		raise frappe.Redirect

	context.no_cache = 1
	return context
