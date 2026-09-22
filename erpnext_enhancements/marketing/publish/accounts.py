# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Social Account rows from what a publishing connection reaches (TASK-2026-01481).

A connection is one login; an account is one place a post can go. Meta's connection reaches
two -- the Facebook Page and the Instagram account linked to it -- so posts target accounts,
never connections. The rows are written when Connect succeeds and refreshed on every
reconnect; a person's *Enabled* choice on an existing row is never overwritten.

Token status is **not** copied onto the account: it lives on Marketing Connections, and the
sweep reads it there at send time, so there is one answer to "does this still work".
"""

from erpnext_enhancements.marketing.core.utils import field
from erpnext_enhancements.marketing.publish import constants as P

ACCOUNT = "Social Account"


def accounts_for(connection, creds):
	"""``[{network, external_id, account_name, handle}]`` a connection reaches. Pure given ``creds.get``."""

	def get(name):
		return (creds.get(field(connection, name)) or "").strip()

	rows = []
	if connection == P.CONNECTION_META:
		if get("page_id"):
			rows.append((P.NETWORK_FACEBOOK, get("page_id"), get("page_name"), ""))
		if get("instagram_user_id"):
			username = get("instagram_username")
			rows.append(
				(P.NETWORK_INSTAGRAM, get("instagram_user_id"), username, f"@{username}" if username else "")
			)
	elif connection == P.CONNECTION_LINKEDIN:
		if get("organization_id"):
			rows.append((P.NETWORK_LINKEDIN, get("organization_id"), get("organization_name"), ""))
	elif connection == P.CONNECTION_YOUTUBE:
		if get("channel_id"):
			rows.append((P.NETWORK_YOUTUBE, get("channel_id"), get("channel_title"), ""))
	return [
		{"network": n, "external_id": i, "account_name": name or i, "handle": handle}
		for n, i, name, handle in rows
	]


def account_name(network, external_id):
	"""The record name, matching the doctype's ``format:SACC-{network}-{external_id}``."""
	return f"SACC-{network}-{external_id}"


def sync(connection, creds):
	"""Create or refresh the Social Accounts ``connection`` reaches. Returns their names."""
	import frappe
	from frappe.utils import now_datetime

	names = []
	for row in accounts_for(connection, creds):
		name = account_name(row["network"], row["external_id"])
		values = {
			"account_name": row["account_name"],
			"handle": row["handle"],
			"connection": connection,
			"connected_on": now_datetime(),
		}
		if frappe.db.exists(ACCOUNT, name):
			frappe.db.set_value(ACCOUNT, name, values)
		else:
			frappe.get_doc(
				{"doctype": ACCOUNT, "network": row["network"], "external_id": row["external_id"], **values}
			).insert(ignore_permissions=True)
		names.append(name)
	return names
