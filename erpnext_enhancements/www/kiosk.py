"""Frappe web-page controller for the Time Kiosk PWA shell at ``/kiosk``.

This is the server side of the standalone Time Kiosk Progressive Web App. Frappe
serves the sibling ``kiosk.html`` template at the ``/kiosk`` route and calls
:func:`get_context` to populate its render context. The page is authenticated
(employees only) and never cached, so the boot payload is computed fresh on every
visit.

The browser-side app (``public/js/kiosk/app.js`` + ``geo.js``) and the root-scope
service worker (``kiosk-sw.js``) take over once the shell loads. Live data and the
geolocation upload endpoint live in ``erpnext_enhancements.api.time_kiosk``.
"""

import frappe

from erpnext_enhancements.api.time_kiosk import get_kiosk_bootstrap

# Shared per-deploy cache-bust token (extracted to utils/deploy.py in v1.13.0;
# re-exported here because kiosk docs/tooling historically reference it from
# this module).
from erpnext_enhancements.utils.deploy import get_deploy_version

# Always render fresh per-user; never cache the authenticated shell.
no_cache = 1


def get_context(context):
	"""Authenticate the visitor and build the kiosk's boot context.

	Route: ``/kiosk`` (rendered by ``kiosk.html``).

	Guests are redirected to ``/login?redirect-to=/kiosk`` (raising
	:class:`frappe.Redirect`); only signed-in users reach the app. For an
	authenticated user this calls
	:func:`erpnext_enhancements.api.time_kiosk.get_kiosk_bootstrap` and exposes
	on ``context``:

	* ``boot_json`` -- the JSON-serialized bootstrap payload (employee, current
	  interval, kiosk settings, CSRF token) injected into the template as
	  ``window.KIOSK_BOOT`` for the front-end to consume on load. It also
	  contains ``maps`` (API key and Map IDs).
	* ``csrf_token`` -- the same CSRF token, injected as ``window.KIOSK_CSRF`` and
	  forwarded to the service worker so it can authenticate batch uploads.
	* ``deploy_version`` -- the per-deploy cache-bust token (see
	  :func:`get_deploy_version`), appended as ``?v=`` to the shell's asset
	  URLs and injected as ``window.KIOSK_BUILD`` for the service-worker
	  registration.
	* ``no_cache`` -- forces a fresh, per-user render of the authenticated shell.
	"""
	# Auth gate: bounce guests to login and return here afterwards.
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/kiosk"
		raise frappe.Redirect

	boot = get_kiosk_bootstrap()
	boot["maps"] = _maps_config()

	context.no_cache = 1
	context.boot_json = frappe.as_json(boot)
	context.csrf_token = boot.get("csrf_token") or ""
	context.deploy_version = get_deploy_version()
	return context

def _maps_config():
	try:
		return {
			"api_key": frappe.db.get_single_value("Travel Settings", "google_maps_api_key") or "",
			"map_id_light": frappe.db.get_single_value("Travel Settings", "google_maps_map_id_light") or "",
			"map_id_dark": frappe.db.get_single_value("Travel Settings", "google_maps_map_id_dark") or "",
		}
	except Exception:
		return {"api_key": "", "map_id_light": "", "map_id_dark": ""}
