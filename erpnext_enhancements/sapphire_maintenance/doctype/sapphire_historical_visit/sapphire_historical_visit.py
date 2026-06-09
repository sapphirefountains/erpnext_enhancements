"""Controller for the Sapphire Historical Visit virtual child doctype.

Read-only rows (``istable`` + ``is_virtual``) shown in a Maintenance Record's
``historical_visits`` table. The rows are NOT stored: they are computed on read
by ``SapphireMaintenanceRecord.historical_visits`` (the last 5 submitted visits
for the same Project). Fields mirror that query: ``visit_date``, ``record_id``
(link back to the Sapphire Maintenance Record) and ``technician``.

No custom controller logic; behaviour comes from the JSON field definitions.
"""

import frappe
from frappe.model.document import Document

class SapphireHistoricalVisit(Document):
	pass
