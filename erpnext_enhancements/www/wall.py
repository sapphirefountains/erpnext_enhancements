"""Frappe web-page controller for the Wall/TV Display shell at ``/wall``.

A read-only, kiosk-friendly portfolio display for 24/7 wall screens
(Raspberry Pi / TV browsers), ported from Triton's DashboardView: a morning
briefing band (today's tasks / overdue / today's schedule), an auto-rotating
per-project task-completion carousel, and an Open-Meteo weather chip.

Architecture mirrors the Time Kiosk PWA (``www/kiosk.py``): authenticated
shell (guests bounce through ``/login``), role gate, server-injected boot
payload, per-deploy ``?v=`` cache busting, and a root-scope service worker
(``wall-sw.js``) so the display survives brief outages and self-reloads on
deploys. Sign the TV in once with a dedicated low-privilege user holding only
the **Wall Display** role (seeded by ``patches/seed_wall_display_role``).

Live data comes from ``api.task_dashboard.get_wall_dashboard_data``.
"""

import frappe

from erpnext_enhancements.api.task_dashboard import STAFF_ROLES, get_wall_dashboard_data
from erpnext_enhancements.utils.deploy import get_deploy_version

# Always render fresh per-user; never cache the authenticated shell.
no_cache = 1


def get_context(context):
	"""Authenticate the visitor and build the wall display's boot context.

	Route: ``/wall`` (rendered by ``wall.html``). Guests are redirected to
	``/login?redirect-to=/wall``; signed-in users must hold a staff role
	(``task_dashboard.STAFF_ROLES``, which includes Wall Display). Exposes:

	* ``boot_json`` — the initial dashboard payload (same shape the refresh
	  endpoint returns), injected as ``window.WALL_BOOT`` so the first paint
	  needs no extra round-trip.
	* ``deploy_version`` — per-deploy cache-bust token, appended as ``?v=``
	  to asset URLs and injected as ``window.WALL_BUILD``.
	"""
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/wall"
		raise frappe.Redirect

	if not STAFF_ROLES.intersection(set(frappe.get_roles())):
		frappe.throw(
			frappe._("You do not have permission to view the Wall Display."),
			frappe.PermissionError,
		)

	context.no_cache = 1
	context.boot_json = frappe.as_json(get_wall_dashboard_data())
	context.deploy_version = get_deploy_version()
	return context
