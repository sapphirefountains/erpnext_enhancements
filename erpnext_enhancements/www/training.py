# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""``/training`` — a redirect into the Desk, and the last thing left of the portal.

The learner player moved to :mod:`~erpnext_enhancements.training.page.learn` in
v1.429.0. **This route is kept, and only, because six code paths have emailed
``https://…/training`` since v1.208.0** — assignment and due/escalation digests
(``training/notifications.py``), answered questions (``training/qa.py``), sign-off
requests (``training/signoff.py``), graded submissions
(``training/submissions.py``) and evaluation invites
(``training/evaluations.py``). Every one of those messages is still in an inbox.
Deleting the route would 404 all of them, and a 404 on a link somebody was told to
follow reads as the feature being gone.

New mail points at ``/app/learn`` and lands in the Desk after frappe's own
``/app/(.*)`` → ``/desk/\1`` redirect, which is the same hop this app's other
emailed desk links already take.

**The filename must stay ``training.py``, underscored.** Frappe imports a web
page's controller by hyphen-to-underscore-ing the *template* basename, so a
hyphenated controller is never imported and ``get_context`` silently never runs —
no exception, no log line. ``scripts/check_www_controllers.py`` guards it, and as
of v1.428.0 also guards the other half: a controller with no sibling template is
not a page at all, so the route would not exist and this redirect would never fire.

**Why this is not an unconditional redirect.** A user with no desk access sent to
``/desk`` gets a login page, which is a worse answer than a sentence. Training
Learner keeps ``desk_access = 0`` — flipping it would turn every customer contact
into a System User and move the licensed-user count — so a customer contact
holding only that role is a Website User. There are none today; the branch exists
so that if one is ever created, the failure is a paragraph rather than a loop.
"""

import frappe

# Never cache: the answer depends on who is asking.
no_cache = 1


def get_context(context):
	"""Route: ``/training``. Redirects into the Desk for anyone who can open it."""
	user = frappe.session.user

	if not user or user == "Guest":
		# Signed-out visitors are almost certainly following an old training email.
		# Send them through login and on to the page they wanted.
		frappe.local.flags.redirect_location = "/login?redirect-to=/app/learn"
		raise frappe.Redirect

	if frappe.db.get_value("User", user, "user_type") == "System User":
		frappe.local.flags.redirect_location = "/app/learn"
		raise frappe.Redirect

	context.no_cache = 1
	return context
