# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Live Class — one scheduled synchronous session for a cohort.

Phase B of the LMS expansion (WI-071). Deliberately thin: it names a session, when
it runs, and the link members click to join. The player never hosts video itself —
it hands the learner the join link — so this record carries no media, only a
schedule and a URL. Members of the session's batch see upcoming and live sessions
on their training home (`api/training._learner_live_classes`).

The calendar-invite side (an ICS / Google Calendar event to members) is the
*native* half and is deliberately not here: the Google Calendar accounts are
disabled on prod (see the WI-071 plan), so wiring it is a separate increment.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class TrainingLiveClass(Document):
	def validate(self):
		self._check_join_url()

	def _check_join_url(self):
		"""The join URL is served to a learner as an ``<a href>`` and clicked. Keep it
		to http(s): the player refuses to render any other scheme (a ``javascript:``
		link would run on click), and refusing it here too means the manager finds out
		on save rather than the learner finding out never."""
		url = (self.join_url or "").strip()
		if url and not url.lower().startswith(("http://", "https://")):
			frappe.throw(
				_("The join URL must be a full http(s) link — the address members click to join the meeting.")
			)
		self.join_url = url
