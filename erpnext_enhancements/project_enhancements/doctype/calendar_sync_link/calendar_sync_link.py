# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Calendar Sync Link: one Google event ERPNext owns, and the hash of what was last written to it.

Written only by ``project_enhancements/calendar_sync/engine.py`` (with ``ignore_permissions``);
System Manager can read and delete. ``source_ref`` (``doctype|name|key``) is unique, so two jobs for
one item can never make two rows. ``source_name`` is a Data field, not a Dynamic Link, on purpose: a
link would make Frappe refuse to delete the Task or visit it points at.

Deleting a row here does not delete the event: the next sync of its record finds no row, inserts under
the same deterministic id, gets 409 and updates, which re-creates the row.

The class name is the one Frappe derives (``doctype.replace(" ", "")``).
"""

from frappe.model.document import Document


class CalendarSyncLink(Document):
	pass
