# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Calendar Cutover Log: one change to a hand-made series' recurrence on the Maintenance calendar.

Written only by ``project_enhancements/calendar_sync/cutover.py`` (``end_series`` and ``undo_end``),
read-only for System Manager. ``before_recurrence`` of an *End* row is what *Undo* puts back; ``undone``
closes it once it has.

The class name is the one Frappe derives (``doctype.replace(" ", "")``).
"""

from frappe.model.document import Document


class CalendarCutoverLog(Document):
	pass
