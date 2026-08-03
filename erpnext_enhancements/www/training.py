# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Frappe web-page controller for the learner training player at ``/training``.

Chrome-free, mobile-first page where an assigned learner works through a course:
video with watch telemetry, in-video checkpoints and end-of-lesson quizzes. It
follows the traveler itinerary shell (``www/itinerary.py``), which in turn follows
the Time Kiosk shell minus the PWA/service-worker layer — the player has no
offline story on purpose, because progress that cannot reach the server is
progress that cannot be defended in a compliance conversation.

Two things about this file are load-bearing:

* **The filename must stay ``training.py``, underscored.** Frappe imports a web
  page's controller by hyphen-to-underscore-ing the *template* basename, so a
  hyphenated controller is never imported and ``get_context`` silently never
  runs — no exception, no log line (``scripts/check_www_controllers.py`` guards
  it; ``www/stripe-return.py`` was broken this way for months).
* **It is a shell and nothing more.** Every learner-visible fact comes from the
  one bootstrap call below, and every subsequent interaction goes back through
  ``api.training`` over ``fetch``. The page must run for *Website Users* with
  ``desk_access = 0`` (customer contacts holding Training Learner), so nothing
  here — and nothing in the player scripts — may rely on the desk bundle or a
  ``frappe.*`` global being present in the browser.

Enablement (``training_enabled`` / ``portal_enabled``) is decided inside
:func:`~erpnext_enhancements.api.training.get_learner_bootstrap`, not here: the
same answer has to be given to the API callers that follow this page load, and
one switch read in one place cannot drift from itself.

Cache busting: raw ``/assets`` URLs are served 1-year-immutable, so
``training.html`` appends ``?v={{ deploy_version }}`` to every mutable asset URL.
The token comes from :mod:`erpnext_enhancements.utils.deploy` — the canonical
home. (``itinerary.py`` still imports it from ``www.kiosk``, where it used to
live; do not copy that import.)
"""

import frappe

from erpnext_enhancements.api.training import get_learner_bootstrap
from erpnext_enhancements.utils.deploy import get_deploy_version

# Always render fresh per-user; never cache the authenticated shell.
no_cache = 1


def get_context(context):
	"""Route: ``/training`` (rendered by ``training.html``).

	Guests are redirected to ``/login?redirect-to=/training``. For an
	authenticated user this exposes ``boot_json`` (the single learner bootstrap
	payload, injected as ``window.TRAINING_BOOT``), ``csrf_token``
	(``window.TRAINING_CSRF``, needed for every ``fetch`` the player makes) and
	``deploy_version`` (asset cache-bust token).
	"""
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/training"
		raise frappe.Redirect

	boot = get_learner_bootstrap()

	context.no_cache = 1
	context.boot_json = frappe.as_json(boot)
	# Fall back to minting the token: without it every player POST 403s, and a
	# bootstrap that stops returning the key would take the page down silently.
	context.csrf_token = boot.get("csrf_token") or frappe.sessions.get_csrf_token()
	context.deploy_version = get_deploy_version()
	return context
