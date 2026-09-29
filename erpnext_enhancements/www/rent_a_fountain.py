"""Web-page controller for the public rental request form at ``/rent-a-fountain`` (v1.565.0).

Note the UNDERSCORE: frappe maps the template's hyphens to underscores to find this module, so a
controller named ``rent-a-fountain.py`` would never load (``scripts/check_www_controllers.py``).

Guest-accessible, and 404s unless ``Rental Settings.public_request_form`` is ticked. The boot is an
explicit allowlist (the Turnstile site key and the honeypot's name). The CSRF token is the session's
existing one only, never ``get_csrf_token()``, which would mint a token a guest's server never
stored (the ``/fountain-move`` rule). Submission: ``asset_management/rental_requests.submit_request``.
"""

import frappe

from erpnext_enhancements.asset_management import rental_requests

no_cache = 1


def get_context(context):
	if not rental_requests.enabled():
		raise frappe.DoesNotExistError
	context.no_cache = 1
	context.csrf_token = (getattr(frappe.session, "data", None) or {}).get("csrf_token") or ""
	context.boot = {
		"turnstile_sitekey": rental_requests.turnstile_site_key(),
		"turnstile_action": rental_requests.TURNSTILE_ACTION,
		"honeypot": rental_requests.HONEYPOT_FIELD,
	}
	return context
