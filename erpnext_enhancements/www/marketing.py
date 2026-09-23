# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the marketing app shell (``/marketing`` and every sub-path of it).

TASK-2026-01487, decision 8: a daily-driver tool for a non-technical marketing hire that keeps
them out of the Desk. Everything the app shows comes from ``marketing/publish/spa.py``.

**The filename matters.** Frappe derives this module from the template's basename with hyphens
turned into underscores, so ``marketing.html`` needs ``marketing.py``. A hyphenated controller
is never imported and ``get_context`` silently never runs (``stripe-return.py`` was dead from
the day it was written). ``scripts/check_www_controllers.py`` enforces it in CI.

**Every sub-path renders this same shell.** ``hooks.py`` carries
``{"from_route": "/marketing/<path:marketing_path>", "to_route": "marketing"}``, so a hard
refresh at ``/marketing/post/SPOST-00001`` reaches this controller rather than 404ing. The client
reads the path out of ``location`` and routes itself; the server never parses it, because a
server that parses the route is a second router to keep in step with the first.

**Who gets in.** A signed-out visitor is sent to log in and brought back to the same path.
Anybody signed in without Marketing Team, Marketing Manager or System Manager gets a 403: the
page is theirs to know about but not to use. The endpoints check again on every call, and the
DocPerms decide what each person then sees. There is no "publishing is off" gate here: writing
and approving posts while publishing is switched off is exactly how the team gets ready for it.

Indentation is tabs, per ``CLAUDE.md``.
"""

import frappe

from erpnext_enhancements.marketing.publish import spa_rules
from erpnext_enhancements.utils.deploy import get_deploy_version

# The shell embeds the caller's identity. A cached shell would hand the next visitor the previous
# visitor's name, so it is never cached.
no_cache = 1


def get_context(context):
	"""Render the app shell, send a signed-out visitor to log in, or refuse everyone else."""
	if frappe.session.user in ("", None, "Guest"):
		# Not a 404: the page exists and signing in is the fix. The full path survives the round
		# trip, so a link to one post still lands on that post.
		frappe.local.flags.redirect_location = "/login?redirect-to=" + frappe.utils.quoted(
			frappe.request.full_path if frappe.request else "/marketing"
		)
		raise frappe.Redirect

	if not spa_rules.can_use(frappe.get_roles()):
		frappe.throw(
			"The marketing app is for the Marketing Team, Marketing Managers and System Managers.",
			frappe.PermissionError,
		)

	context.no_cache = 1
	# Cache-busts the shell's own inline boot payload on every deploy, as the feedback, kiosk and
	# training shells do. The bundles are content-hashed through assets.json and need no help.
	context.deploy_version = get_deploy_version()
	context.marketing_user = frappe.session.user
	context.marketing_full_name = frappe.db.get_value("User", frappe.session.user, "full_name") or ""
	context.csrf_token = frappe.sessions.get_csrf_token()
	return context
