# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the chat SPA shell (``/chat`` and every sub-path of it).

**The filename matters.** Frappe derives this module from the template's basename with
hyphens turned into underscores, so ``chat.html`` needs ``chat.py``. A hyphenated controller
is never imported, ``get_context`` silently never runs, and the template still renders with
every variable undefined — ``stripe-return.py`` in this app was broken exactly that way from
the day it was written. ``scripts/check_www_controllers.py`` enforces it in CI.

**Every sub-path renders this same shell.** ``hooks.py`` carries
``{"from_route": "/chat/<path:chat_path>", "to_route": "chat"}``, so a hard refresh at
``/chat/room/CHAT-ROOM-000123?message=chatmsg-abc`` reaches this controller rather than
404ing. That is not a nicety: every deep-link acceptance criterion in Phase 3, every
notification link Phase 4 will send, and every ``chat_message`` citation Phase 5 will
resolve, all depend on it. The client reads the path out of ``location`` and routes
itself — the server does not parse it, because a server that parses the route is a second
router to keep in step with the first.

**The ``website_404`` cache trap comes with shipping this rule.** Loading ``/chat/room/X``
*before* the rule exists caches that URL in the ``website_404`` cache until Redis is
flushed. A full deploy FLUSHDBs Redis and clears it; a hotfix without a restart does not.
So this route must not be advertised to anybody before the deploy that carries it lands.

**The gate is here, and it is a real gate.** ``Chat Settings.enabled`` plus the pilot
whitelist, checked server-side. The API enforces the same thing on every call
(``chat/api/_common.require_session``) — a gate implemented only in the page is decoration —
but refusing here means somebody outside the pilot gets a 404 instead of an empty
application that fails on its first fetch.

Indentation is tabs, per ``CLAUDE.md``.
"""

import frappe
from frappe.utils import cint

from erpnext_enhancements.utils.deploy import get_deploy_version

# The shell embeds the caller's identity and their bootstrap hints. Nothing here is another
# user's data, but a cached shell would hand the next visitor the previous visitor's name
# and last-open room, so it is never cached.
no_cache = 1


def get_context(context):
	"""Render the SPA shell, or 404 the whole route.

	``DoesNotExistError`` rather than ``PermissionError`` when chat is off or the caller is
	outside the pilot: a 403 confirms the feature exists and that this person is not on the
	list, which is information the route has no reason to publish. When chat is *on* and the
	caller is simply signed out, the redirect to login is the useful answer instead.
	"""
	if frappe.session.user in ("", None, "Guest"):
		# Not a 404: the page exists and signing in is the fix. `redirect_to_message` would
		# lose the deep link, so the full path is preserved through the login round trip.
		frappe.local.flags.redirect_location = "/login?redirect-to=" + frappe.utils.quoted(
			frappe.request.full_path if frappe.request else "/chat"
		)
		raise frappe.Redirect

	if not _chat_open_to_caller():
		raise frappe.DoesNotExistError

	context.no_cache = 1
	# Cache-busts the bundle references below on every deploy, the same mechanism the kiosk
	# and training shells use. The bundles themselves are content-hashed through assets.json;
	# this covers the shell's own inline boot payload.
	context.deploy_version = get_deploy_version()

	context.chat_user = frappe.session.user
	context.chat_full_name = frappe.db.get_value("User", frappe.session.user, "full_name") or ""
	# The site name the socket handshake needs. A standalone SPA at a website route does not
	# get Desk's `frappe.boot`, so anything the socket client needs has to be put on the page
	# deliberately — this is the single most common half-day sink in building one.
	context.chat_site_name = getattr(frappe.local, "site", "") or ""
	context.chat_socketio_port = _socketio_port()
	context.csrf_token = frappe.sessions.get_csrf_token()

	return context


def _chat_open_to_caller() -> bool:
	"""Master switch **and** pilot standing, collapsed into one boolean. Never raises.

	Mirrors ``boot._chat_visible`` deliberately — same two questions, same import-inside-the-
	function reason (a site that has this code but has not migrated has no ``Chat Settings``
	DocType at all, and an import-time failure on a website route is a 500 on a page that
	should simply not exist yet).
	"""
	try:
		from erpnext_enhancements.chat.doctype.chat_settings.chat_settings import is_user_allowed

		if not cint(frappe.db.get_single_value("Chat Settings", "enabled")):
			return False
		return bool(is_user_allowed())
	except Exception:
		return False


def _socketio_port() -> int:
	"""The socket.io port for a client that is **not** inside the Desk bundle.

	Inside Desk, ``frappe.realtime`` is already connected and the port is resolved from
	``frappe.boot``. A website-route SPA has neither, so it connects itself and needs this.

	``VERIFY:`` (unexecuted — this environment has no bench) that on the production host the
	load balancer routes ``/socket.io/`` to this port, and that the SPA's connection survives
	past 60 seconds without a reconnect. The GCLB's default backend timeout is 30 s and it
	applies to idle WebSockets; Phase 1 raised it to 3600 s
	(commit ``0e6c9980``), and §4.14 requires that be re-observed empirically in a browser on
	production rather than assumed. It is listed as an open item in the phase report.
	"""
	try:
		return cint(frappe.conf.get("socketio_port")) or 9000
	except Exception:
		return 9000
