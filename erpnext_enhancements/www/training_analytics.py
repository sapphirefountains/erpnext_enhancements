# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Manager training analytics page at ``/training_analytics`` (WI-071 Phase H).

A read-only dashboard for training managers: org-wide completion, overdue work,
per-course and per-cohort progress, and the grading backlog. Unlike ``/training``
this is a **manager** surface — its audience holds the desk bundle — so it is
rendered server-side with Jinja (autoescaped, on the page at first paint) rather
than through the learner runtime. The one source of the numbers is
:func:`~erpnext_enhancements.training.analytics.get_training_analytics`, which the
`/training_analytics` MCP-style callers and this page share.

Two carried-over rules, both load-bearing (documented the same way in
``training.py`` / ``training_preview.py``):

* **The filename must stay ``training_analytics.py``, underscored.** Frappe imports
  a web page's controller by hyphen-to-underscore-ing the *template* basename; a
  hyphenated controller is never imported and ``get_context`` silently never runs
  (``scripts/check_www_controllers.py`` guards it).
* **A plain learner gets a 404, not a 403.** Reporting the page as missing rather
  than forbidden does not confirm the route exists to someone who should not know
  it does.
"""

import frappe

from erpnext_enhancements.training.analytics import get_training_analytics
from erpnext_enhancements.utils.deploy import get_deploy_version

no_cache = 1

# Who may open the dashboard: the same roles that are unscoped in
# training/permissions.py, because this reports across every learner.
ALLOWED_ROLES = {"System Manager", "Training Manager", "HR Manager"}


def get_context(context):
	"""Route: ``/training_analytics`` (rendered by ``training_analytics.html``)."""
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/training_analytics"
		raise frappe.Redirect

	if not (ALLOWED_ROLES & set(frappe.get_roles())):
		# Reported as missing, not forbidden — a 403 would confirm the route exists.
		raise frappe.DoesNotExistError

	context.no_cache = 1
	# The endpoint re-checks the manager role itself; the double gate is deliberate.
	context.analytics = get_training_analytics()
	context.deploy_version = get_deploy_version()
	return context
