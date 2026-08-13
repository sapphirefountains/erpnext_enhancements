"""Populate the new ``Chat Settings.bot_user`` with whatever the old resolver actually returned.

--------------------------------------------------------------------------------------
Why a patch rather than a note in the release
--------------------------------------------------------------------------------------

``handler._bot_user`` now reads one field and raises when it is empty, which is the point of
splitting it. But the field is new, so on every existing site it *is* empty — and an empty
field there means **every ``@triton`` mention fails**, on the deploy, before anybody reads a
changelog. A split that needs a manual step to avoid an outage is not a split, it is a trap
with documentation.

So this freezes the previous behaviour into data: exactly what ``_bot_user`` would have
returned a minute earlier, written to the field that now owns the answer.

--------------------------------------------------------------------------------------
The order it tries, which is the old resolver's order
--------------------------------------------------------------------------------------

1. ``chat_app_service_account`` **if a User by that name exists** — the old first branch. On
   Sapphire prod it does not (that field holds a Google service account), but a site that
   happened to name a real User there keeps working.
2. ``triton@sapphirefountains.com`` if that User exists — the old hard-coded second branch.

If neither resolves, the field stays empty and the next mention raises with a message naming
the field to set. That is the honest outcome: this patch preserves a working configuration, it
does not invent one.

Registered in ``patches.txt`` rather than only on ``after_migrate``, deliberately: patches run
*before* ``sync_fixtures``, and on this deployment ``sync_fixtures`` has been dying — so an
``after_migrate`` hook is exactly the half that does not run. See the changelog for v1.277.12.

Safe to run twice: a populated field is left alone.
"""

import frappe

SETTINGS_DOCTYPE = "Chat Settings"
LEGACY_FALLBACK_USER = "triton@sapphirefountains.com"


def execute() -> None:
	set_chat_bot_user()


def set_chat_bot_user() -> str:
	"""Write the resolved bot User, and return it (empty when nothing resolved)."""
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return ""

	existing = frappe.db.get_single_value(SETTINGS_DOCTYPE, "bot_user") or ""
	if existing:
		return existing

	legacy = frappe.db.get_single_value(SETTINGS_DOCTYPE, "chat_app_service_account") or ""
	for candidate in (legacy, LEGACY_FALLBACK_USER):
		if candidate and frappe.db.exists("User", candidate):
			# set_single_value writes tabSingles directly and runs no validate — which matters
			# because Chat Settings' validate is strict and this must not depend on the rest of
			# the row being in a saveable state to repair one field.
			frappe.db.set_single_value(SETTINGS_DOCTYPE, "bot_user", candidate)
			frappe.clear_document_cache(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)
			return candidate

	return ""
