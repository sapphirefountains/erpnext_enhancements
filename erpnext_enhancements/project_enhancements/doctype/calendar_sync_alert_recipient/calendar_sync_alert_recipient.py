# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Calendar Sync Alert Recipient: one row of Calendar Sync Settings' *Time off alerts* (C4). The
user gets a bell when time off read in from Google lands on booked work, and when events wait in
Calendar Time Off Review. An empty table means whoever authorized the Google account.

The class name is the one Frappe derives (``doctype.replace(" ", "")``).
"""

from frappe.model.document import Document


class CalendarSyncAlertRecipient(Document):
	pass
