# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Scheduler entry for the publishing connections' token upkeep (TASK-2026-01480).

One daily job, but not one shared timer: each connection has its own rule, because the
token lifetimes differ (``publish/oauth.maintain``). The gates, in order:

1. **Master switch.** ``Marketing Settings.enabled`` off -> return. Dormant means nothing
   runs, the same contract the ad connectors keep. A LinkedIn token left to lapse while the
   module is off is refreshed on first use, from its refresh token.
2. **Connected only.** A connection that is not *Connected* is left alone: *Auth Failed* is
   waiting for a person, and retrying it nightly would only fill the log.

The per-network publishing switches play no part. They decide whether an approved post may
go out, and a token kept alive while a network is switched off is what lets switching it on
work without a reconnect.

It runs inline rather than enqueuing: at most three token or identity calls, and Frappe
already runs scheduled jobs on a worker. A deploy that FLUSHDBs the queue costs one day's
upkeep, and every rule here has at least a week of margin.
"""

from erpnext_enhancements.marketing.publish import constants as P


def maintain_publishing_tokens(http=None):
	"""Cron entry (hooks.py). Returns ``{connection: outcome}`` for the connections it touched."""
	import frappe
	from frappe.utils import cint

	from erpnext_enhancements.marketing.core.utils import field, get_credentials, get_settings
	from erpnext_enhancements.marketing.publish import oauth

	if not cint(get_settings().get("enabled")):
		return {}
	creds = get_credentials()
	outcomes = {}
	for connection in P.PUBLISH_CONNECTIONS:
		if (creds.get(field(connection, "connection_status")) or "") != oauth.STATUS_CONNECTED:
			continue
		try:
			outcomes[connection] = oauth.maintain(connection, creds, http=http)
		except Exception:
			# A bug, not a platform failure (maintain() records those). Plain get_traceback():
			# never with_context, whose frame locals would include a decrypted token.
			frappe.log_error(
				f"Publishing token upkeep crashed on {connection}\n\n{frappe.get_traceback()}",
				"Marketing publishing upkeep crashed",
			)
			outcomes[connection] = "crashed"
	if outcomes:
		creds.save(ignore_permissions=True)
		frappe.db.commit()
	return outcomes
