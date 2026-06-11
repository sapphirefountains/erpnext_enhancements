"""Frappe web-page controller for the traveler itinerary at ``/itinerary``.

Mobile-friendly, chrome-free page where a traveler sees their day-by-day trip
itinerary (flights with PNRs, hotel confirmations, agenda stops with POI
locations and maps). Follows the Time Kiosk shell pattern (``www/kiosk.py``)
minus the PWA/service-worker layer — the itinerary has no offline queueing
needs. Live data comes from ``erpnext_enhancements.api.travel`` (session-trust
security model: the employee is derived server-side, trips are scoped by the
Travel Trip permission hooks).

Cache busting: raw ``/assets`` URLs are served 1-year-immutable, so
``itinerary.html`` appends ``?v={{ deploy_version }}`` to every mutable asset
URL (same rationale and token as the kiosk — see
:func:`erpnext_enhancements.www.kiosk.get_deploy_version`).
"""

import frappe

from erpnext_enhancements.api.travel import get_itinerary_bootstrap
from erpnext_enhancements.www.kiosk import get_deploy_version

# Always render fresh per-user; never cache the authenticated shell.
no_cache = 1


def get_context(context):
	"""Route: ``/itinerary`` (rendered by ``itinerary.html``).

	Guests are redirected to ``/login?redirect-to=/itinerary``. For an
	authenticated user this exposes ``boot_json`` (employee + their active
	trips + CSRF token, injected as ``window.ITIN_BOOT``), ``csrf_token``
	(``window.ITIN_CSRF``) and ``deploy_version`` (asset cache-bust token).
	"""
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/itinerary"
		raise frappe.Redirect

	boot = get_itinerary_bootstrap()

	context.no_cache = 1
	context.boot_json = frappe.as_json(boot)
	context.csrf_token = boot.get("csrf_token") or ""
	context.deploy_version = get_deploy_version()
	return context
