# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Carrier Tracking Settings: the FedEx and UPS API clients package tracking uses.

Everything is off by default and stays inert until a System Manager enters a client id and
secret, turns the carrier on, and turns on the master switch (``carrier_tracking.config``).

Nothing in the app writes to this Single: the health page reads the carriers' last answers from
the Tracked Package rows instead of keeping "last checked" stamps here. That is deliberate. The
first write of one field to a never-saved Single makes every other field load blank
(CLAUDE.md), and a stamp written by a scheduler job is exactly that write.

Saving drops the cached access tokens, so a corrected secret is used on the next check rather
than after the old token runs out.
"""

from frappe.model.document import Document


class CarrierTrackingSettings(Document):
	def on_update(self):
		# Imported here so a broken import in the client can never make this page unsaveable,
		# which would leave no way to switch the feature off.
		try:
			from erpnext_enhancements.carrier_tracking.config import forget_tokens

			forget_tokens()
		except Exception:
			pass
