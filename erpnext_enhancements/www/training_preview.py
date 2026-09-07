# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Dev-only preview harness for the learner training player at ``/training_preview``.

Boots the *real* ``TR.Player`` — the same four scripts and ``player.css`` the live
``/training`` page loads — against a **canned in-memory transport** defined in the
template: no bench data, no server round trips, no progress written. It is the
design workbench and screenshot-diff target for the states that only appear after
a server reply and are otherwise a nuisance to reach — the completion celebration,
the quiz review, the empty and dormant catalogs, every block type in one lesson.

It exists to make the ``TR.core`` refactor (and every later redesign step) checkable:
render all states, before and after, and compare.

Two things carry over from ``training.py`` and are load-bearing:

* **The filename must stay ``training_preview.py``, underscored.** Frappe imports a
  web page's controller by hyphen-to-underscore-ing the *template* basename, so a
  hyphenated controller is never imported and ``get_context`` silently never runs
  (``scripts/check_www_controllers.py`` guards it).
* **No ``frappe.*`` in the browser.** The player runs for Website Users with
  ``desk_access = 0``; the canned transport is plain objects and the player never
  learns it is not talking to the server.

Not a learner surface and never linked from one: restricted to the authoring and
admin roles, plus anyone on a developer-mode site. A plain learner gets a 404 —
reported as missing rather than forbidden, because a 403 would confirm the route.
"""

import frappe

from erpnext_enhancements.utils.deploy import get_deploy_version

no_cache = 1

# Who may open the workbench. The authoring/admin roles, never a plain learner.
ALLOWED_ROLES = {"System Manager", "Training Manager", "Training Author"}


def get_context(context):
	"""Route: ``/training_preview`` (rendered by ``training_preview.html``)."""
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/training_preview"
		raise frappe.Redirect

	if not (frappe.conf.get("developer_mode") or (ALLOWED_ROLES & set(frappe.get_roles()))):
		# Reported as missing, not forbidden — a 403 would confirm the route exists.
		raise frappe.DoesNotExistError

	context.no_cache = 1
	context.deploy_version = get_deploy_version()
	return context
