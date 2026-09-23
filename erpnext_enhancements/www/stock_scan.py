# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the Stock Scan page at ``/stock-scan``.

Every warehouse QR label (``/warehouse-labels``) encodes ``<site>/stock-scan?w=<name>``,
so a phone's own camera app opens this page on that location with no app to install.
From there the page scans the next label itself (``public/js/stock_scan/scanner.js``), so
the camera prompt is answered once per run rather than once per shelf — iOS asks again on
every page load.

**The filename matters.** Frappe derives this module from the template's basename with
hyphens turned into underscores, so ``stock-scan.html`` needs ``stock_scan.py``. A
hyphenated controller is never imported and ``get_context`` silently never runs
(``scripts/check_www_controllers.py`` enforces it in CI). The route stays hyphenated.

**The query string survives the login round trip.** A technician whose session has lapsed
scans a label, logs in, and lands on the location they scanned — which is the whole reason
the URL is a query string and not a path (a path would need a ``website_route_rules``
entry and bring the ``website_404`` cache trap with it).

The boot payload resolves the scanned location server-side, so the first screen needs no
second round trip on a warehouse's phone signal. See ``api.stock_scan.boot_payload``.
"""

import frappe
from frappe import _

from erpnext_enhancements.api.stock_scan import boot_payload
from erpnext_enhancements.inventory_enhancements import stock_scan_rules as rules
from erpnext_enhancements.utils.deploy import get_deploy_version

# The shell embeds the caller's identity and recent saves; never serve one person's render to another.
no_cache = 1

#: The QR decoder for phones without the BarcodeDetector API (every iPhone). A raw /assets
#: path is safe here because the version is in the filename: this file never changes, and a
#: new jsQR would be a new name. Loaded only when needed; see scanner.js.
DECODER_PATH = "/assets/erpnext_enhancements/js/stock_scan/lib/jsQR-1.4.0.min.js"


def get_context(context):
	"""Render the page shell, or send a signed-out visitor to log in and come back."""
	if frappe.session.user in ("", None, "Guest"):
		# Encoded whole, so every query parameter comes back after login (stock_scan_rules).
		frappe.local.flags.redirect_location = rules.login_redirect(
			frappe.request.full_path if frappe.request else rules.SCAN_ROUTE
		)
		raise frappe.Redirect

	if not rules.SCAN_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("You do not have permission to use Stock Scan."), frappe.PermissionError)

	context.no_cache = 1
	context.deploy_version = get_deploy_version()
	boot = boot_payload(frappe.form_dict)
	boot["csrf_token"] = frappe.sessions.get_csrf_token()
	boot["build"] = context.deploy_version
	boot["decoder_url"] = DECODER_PATH
	context.boot = boot
	return context
