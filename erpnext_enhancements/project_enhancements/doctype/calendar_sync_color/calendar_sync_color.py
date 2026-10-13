# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Calendar Sync Color: one row of Calendar Sync Settings' colors, a value stream (or Day Note /
Unavailable) and the Google ``colorId`` (1-11) its events get. Rows override
``calendar_sync.payload.DEFAULT_COLORS`` one at a time.

The class name is the one Frappe derives (``doctype.replace(" ", "")``).
"""

from frappe.model.document import Document


class CalendarSyncColor(Document):
	pass
