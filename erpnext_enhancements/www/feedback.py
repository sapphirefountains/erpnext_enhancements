# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the feedback SPA shell (``/feedback`` and every sub-path of it).

**The filename matters.** Frappe derives this module from the template's basename with
hyphens turned into underscores, so ``feedback.html`` needs ``feedback.py``. A hyphenated
controller is never imported, ``get_context`` silently never runs, and the template still
renders with every variable undefined — ``stripe-return.py`` in this app was broken exactly
that way from the day it was written. ``scripts/check_www_controllers.py`` enforces it in CI.

**Every sub-path renders this same shell.** ``hooks.py`` carries
``{"from_route": "/feedback/<path:feedback_path>", "to_route": "feedback"}``, so a hard
refresh at ``/feedback/request/ER-2026-00001`` reaches this controller rather than 404ing.
That matters here because every notification this feature sends links to exactly such a URL.
The client reads the path out of ``location`` and routes itself — a server that parses the
route is a second router to keep in step with the first.

**The ``website_404`` cache trap comes with shipping that rule.** Loading
``/feedback/request/X`` *before* the rule exists caches that URL in the ``website_404`` cache
until Redis is flushed. A full deploy FLUSHDBs Redis and clears it; a hotfix without a
restart does not. So this route must not be advertised before the deploy carrying it lands.

**There is no feature gate here, on purpose.** This ships live: anybody who can sign in can
file a request. ``Product Feedback Settings.paused`` stops new *submissions* and is enforced
in ``api.feedback.submit_request``, not here — pausing intake should still let people read
what they already filed and let a reviewer finish the queue. A page that 404'd on pause would
take both away.

Indentation is tabs, per ``CLAUDE.md``.
"""

import frappe

from erpnext_enhancements.utils.deploy import get_deploy_version

# The shell embeds the caller's identity. A cached shell would hand the next visitor the
# previous visitor's name, so it is never cached.
no_cache = 1


def get_context(context):
	"""Render the SPA shell, or send a signed-out visitor to log in and come back."""
	if frappe.session.user in ("", None, "Guest"):
		# Not a 404: the page exists and signing in is the fix. `redirect_to_message` would
		# lose the deep link, so the full path is preserved through the login round trip —
		# which is the whole point for a notification that links to one request.
		frappe.local.flags.redirect_location = "/login?redirect-to=" + frappe.utils.quoted(
			frappe.request.full_path if frappe.request else "/feedback"
		)
		raise frappe.Redirect

	context.no_cache = 1
	# Cache-busts the shell's own inline boot payload on every deploy, the same mechanism the
	# kiosk, training and chat shells use. The bundles below are content-hashed through
	# assets.json and need no help.
	context.deploy_version = get_deploy_version()

	context.feedback_user = frappe.session.user
	context.feedback_full_name = frappe.db.get_value("User", frappe.session.user, "full_name") or ""
	context.csrf_token = frappe.sessions.get_csrf_token()

	return context
