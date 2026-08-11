# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One browser's Web Push registration.

**The fact that makes this table exist: a push subscription is addressed by a URL the browser
issues, and that URL is too long to be a database key.** Push endpoints routinely run past
255 characters, Frappe's ``Data`` defaults to ``varchar(140)``, and MariaDB truncates on
insert rather than refusing — so a unique index on the endpoint itself does not fail loudly,
it silently collides two distinct devices into one row and one of them stops receiving
anything. Hence the split: ``endpoint`` is ``Small Text`` and carries no index, and
``endpoint_hash`` is a 64-character SHA-256 that does.

**A row here is a capability, not a record.** Anybody holding the endpoint can push to that
browser, which is why it is never written to a log line, never returned by a read endpoint,
and why this DocType — like almost every other in this module — ships with **zero DocPerm**.
Nothing reaches it except the code in
:mod:`erpnext_enhancements.chat.notifications.webpush.subscriptions`.

**Deactivation is not deletion**, and the distinction earns its keep during a support
conversation: "my phone stopped buzzing" is answerable from a row that says the subscription
died and when, and unanswerable from a row that is gone. Only three things retire a row — the
push service reporting it gone (404/410, the one signal that means a device is genuinely
never coming back), ten consecutive non-terminal failures, and the sixty-day stale sweep for
the devices no service will ever mention again because they were simply wiped.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

from typing import Final

import frappe
from frappe.model.document import Document
from frappe.utils import cint

#: Rows deleted per batch by :meth:`ChatPushSubscription.clear_old_logs`. Batched with a
#: commit between, because a single unbounded delete on a table nobody watches is how a
#: nightly job takes a lock long enough to be noticed.
_PURGE_BATCH: Final[int] = 500

#: How long a deactivated row is kept before it is deleted outright. Long enough to answer
#: "when did my phone stop working" during the week somebody asks, short enough that the
#: table does not accumulate a decade of dead devices.
DEFAULT_RETENTION_DAYS: Final[int] = 90


class ChatPushSubscription(Document):
	def validate(self) -> None:
		"""Normalise, and derive the hash rather than trusting a caller to supply it.

		Deriving it here rather than in the writer is what makes the unique constraint mean
		what it says: a second writer that computed the hash differently — or forgot — would
		insert a duplicate device that no lookup could ever find again, and the only symptom
		would be one person's notifications arriving twice.
		"""
		from erpnext_enhancements.chat.notifications.webpush.subscriptions import endpoint_hash

		self.endpoint = (self.endpoint or "").strip()
		if not self.endpoint:
			frappe.throw(frappe._("A push subscription needs an endpoint."))

		self.endpoint_hash = endpoint_hash(self.endpoint)
		self.p256dh = (self.p256dh or "").strip()
		self.auth = (self.auth or "").strip()
		if not self.p256dh or not self.auth:
			frappe.throw(frappe._("A push subscription needs both of its keys."))

		if cint(self.failure_count) < 0:
			self.failure_count = 0

	@staticmethod
	def clear_old_logs(days: int | None = None) -> None:
		"""Delete long-dead subscriptions. **Its existence is load-bearing.**

		``remove_unsupported_doctypes()`` runs first in Frappe's ``run_log_clean_up`` and
		silently deletes the ``Logs To Clear`` row of any doctype whose controller does not
		implement this method — so retention would stop on the next daily run, with no error,
		and nobody would find out until the table was large.

		Only inactive rows are eligible. An active subscription is a working device however
		old it is, and deleting one would silently unsubscribe somebody who is using it.
		"""
		from frappe.utils import add_days, now_datetime

		cutoff = add_days(now_datetime(), -cint(days or DEFAULT_RETENTION_DAYS))
		while True:
			names = frappe.get_all(
				"Chat Push Subscription",
				filters={"is_active": 0, "modified": ("<", cutoff)},
				pluck="name",
				limit=_PURGE_BATCH,
			)
			if not names:
				return
			frappe.db.delete("Chat Push Subscription", {"name": ("in", names)})
			frappe.db.commit()
