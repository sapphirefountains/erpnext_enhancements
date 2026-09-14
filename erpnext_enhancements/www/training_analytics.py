# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""``/training_analytics`` — a redirect to the Desk page that replaced it.

The dashboard moved to :mod:`~erpnext_enhancements.training.page.training_insights`
in v1.431.0. It was 233 lines of server-rendered Jinja at a website route,
manager-only, with no desk link and no workspace entry — a page for people who
live in the Desk, reachable only by typing a URL nobody had.

Unlike ``/training`` this route was **never emailed**, so keeping it is politeness
toward a bookmark rather than the necessity it is over there. It costs four lines.

The rollup itself,
:func:`~erpnext_enhancements.training.analytics.get_training_analytics`, is
untouched and is still the single source — the Desk page renders the dict it
returns and computes nothing of its own. That is deliberate: "overdue" is a
predicate, and a ``<`` filter on a nullable date silently matches NULLs, which is
how "no expiry" once became "expired" in this module.

**The filename must stay ``training_analytics.py``, underscored** — frappe imports
a controller by hyphen-to-underscore-ing the template basename, so a hyphenated one
is never imported and ``get_context`` silently never runs.
"""

import frappe

no_cache = 1


def get_context(context):
	"""Route: ``/training_analytics``. Redirects managers into the Desk."""
	user = frappe.session.user

	if not user or user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/app/training-insights"
		raise frappe.Redirect

	if frappe.db.get_value("User", user, "user_type") == "System User":
		frappe.local.flags.redirect_location = "/app/training-insights"
		raise frappe.Redirect

	# A Website User could never open this page anyway -- it is manager-only and a
	# non-manager got a 404. Sending them to /desk would show them a login form; the
	# template says what happened instead.
	context.no_cache = 1
	return context
