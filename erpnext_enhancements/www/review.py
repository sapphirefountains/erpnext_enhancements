# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the Review Room shell (``/review`` and every sub-path of it).

The Review Room is the design review app (WI-079 slice 5, ADR 0016 §2): a full-screen website
page, not a Desk page, because the Desk's chrome, CSS and widgets were in the way of showing
concept screens well. Everything it reads and writes goes through ``api/design_review.py``,
which checks participation, status and role on every call; this controller only decides who may
load the shell at all.

**Staff only.** A signed-out visitor is sent to log in and brought back to the same deep link. A
signed-in Website User (a customer contact) is refused with a page that says so: design reviews
are for people who sign in to the Desk, and the endpoints refuse them anyway.

**The filename matters**: ``review.html`` needs ``review.py`` (``scripts/check_www_controllers.py``).
``hooks.py`` routes ``/review/<path>`` here so a hard refresh at ``/review/DR-2026-001/learner/L3/S04``
renders this same shell and the bundle routes itself from ``location``.

Indentation is tabs, per ``CLAUDE.md``.
"""

import frappe

from erpnext_enhancements.utils.deploy import get_deploy_version

# The shell embeds the caller's identity, so it is never cached.
no_cache = 1


def get_context(context):
	if frappe.session.user in ("", None, "Guest"):
		frappe.local.flags.redirect_location = "/login?redirect-to=" + frappe.utils.quoted(
			frappe.request.full_path if frappe.request else "/review"
		)
		raise frappe.Redirect

	from frappe.permissions import is_system_user

	if not is_system_user(frappe.session.user):
		frappe.throw(
			frappe._("Design reviews are for staff accounts that sign in to the Desk."),
			frappe.PermissionError,
		)

	context.no_cache = 1
	# templates/web.html wraps the page in a fixed-width .container unless this is set.
	context.full_width = 1
	# Every endpoint is POST, and a website page gets no token unless it asks: without this line
	# each save is refused with a CSRF error (www/feedback.py does the same).
	context.csrf_token = frappe.sessions.get_csrf_token()
	context.deploy_version = get_deploy_version()
	context.review_user = frappe.session.user
	context.review_full_name = frappe.db.get_value("User", frappe.session.user, "full_name") or ""
	context.review_moderator = "System Manager" in frappe.get_roles()
