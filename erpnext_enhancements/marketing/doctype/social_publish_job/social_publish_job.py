# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Social Publish Job -- the outbox row (TASK-2026-01481).

All behaviour is in ``marketing/publish/outbox.py`` (the state machine) and ``sweeper.py`` (the
five-minute sweep). Rows are written by approval and driven by the sweep; a person only ever
resolves an Unconfirmed or Failed one, through ``sweeper.resolve_job``.
"""

import frappe
from frappe.model.document import Document


class SocialPublishJob(Document):
	pass


def on_doctype_update():
	"""The sweep's two queries: due Pending jobs by time, and In Progress jobs by lease expiry."""
	frappe.db.add_index("Social Publish Job", ["state", "available_at"])
	frappe.db.add_index("Social Publish Job", ["state", "lease_expires_at"])
