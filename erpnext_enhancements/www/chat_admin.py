# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the oversight viewer at ``/chat_admin``. Phase 6 §4.B.

**The filename is underscored on purpose.** Frappe derives this module from the template's
basename with hyphens turned into underscores, so a ``chat-admin.html`` would look for
``chat-admin.py``, never import it, and render the template with every variable undefined —
which is how ``stripe-return.py`` was broken in this app from the day it was written.
``scripts/check_www_controllers.py`` enforces it in CI, and this file is why the rule keeps
being restated: the failure is silent and the page still renders.

**A separate page, not a mode of the chat SPA.** They share no template, no bundle and no
socket. `chat/permissions.py` records that the retrieval gate has exactly two doors and the
employee app is not one of them; teaching the SPA an "oversight mode" is the fastest way to
make it a third.

**No socket, and that is G6-6 rather than a simplification.** Opening a room the auditor is
not in must not establish a realtime subscription to that room's doc room — a socket join is
a read this module cannot audit, because the audit row is written when the transcript is
fetched and a live event arriving later was never asked for. So the page renders server-side
and subscribes to nothing.

**Refused rather than 404'd, and the difference matters here.** The chat SPA 404s somebody
outside the pilot, because an empty application that fails on its first fetch is worse than
a missing page. This one answers 403 with a sentence: the people who reach it are
administrators who have been told the surface exists, and a 404 would send them to check
their URL instead of their role.
"""

from __future__ import annotations

from typing import Any

import frappe

from erpnext_enhancements.chat import audit, permissions

no_cache = 1


def get_context(context: dict[str, Any]) -> dict[str, Any]:
	"""Gate on the configured oversight role, and hand the template what it needs.

	Deliberately does **not** read any chat content. Every read on this page goes through
	``chat.governance.viewer``, which records it; a controller that pre-loaded a transcript
	"for convenience" would be an unaudited read on page load, and the most-used one.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(frappe._("Sign in to use the oversight viewer."), frappe.PermissionError)

	if not permissions._has_oversight(user):
		frappe.throw(
			frappe._(
				"The oversight viewer requires the role named in Chat Settings → Admin "
				"Oversight Role. That field ships blank, so this page is unreachable until "
				"somebody deliberately configures it."
			),
			frappe.PermissionError,
		)

	context.no_cache = 1
	context.title = "Chat Oversight"
	# The vocabulary comes from `chat.audit`, not from a copy in the template. A second list
	# would drift, and the one place it would show up is a subject being told a category the
	# writer then refuses to store.
	context.reason_categories = list(audit.REASON_CATEGORIES)
	context.reason_min_length = 12
	context.acting_user = user
	return context
