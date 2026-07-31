"""Keep OAuth Bearer Tokens for 90 days instead of 30 (v1.194.1).

Frappe's log-clearing job deletes `OAuth Bearer Token` rows once they age past
the retention in Log Settings, which defaults to 30 days. That is not just log
hygiene: the row *is* the refresh token. Frappe issues a fresh row on every
refresh and never revokes the old ones, so a user's stored refresh token stays
valid only as long as the row it came from survives — and a user who doesn't
touch the integration for a month comes back to a dead grant and a forced
relink, with the failure surfacing as an opaque 403 PermissionError on Guest.

Triton (the app that authenticates its users this way) now refreshes ahead of
expiry, so an active user rotates well inside any window. 90 days covers the
gaps that actually happen — leave, a slow season, a seldom-used login — without
keeping bearer tokens around indefinitely.

Idempotent, and never lowers a retention someone has deliberately raised.
"""

import frappe

RETENTION_DAYS = 90


def execute():
	settings = frappe.get_single("Log Settings")
	for row in settings.get("logs_to_clear") or []:
		if row.ref_doctype != "OAuth Bearer Token":
			continue
		if (row.days or 0) >= RETENTION_DAYS:
			return
		row.days = RETENTION_DAYS
		settings.save(ignore_permissions=True)
		return

	# Not listed yet — Frappe seeds the defaults lazily, so on a site that has
	# never opened Log Settings the row simply isn't there.
	settings.append("logs_to_clear", {"ref_doctype": "OAuth Bearer Token", "days": RETENTION_DAYS})
	settings.save(ignore_permissions=True)
