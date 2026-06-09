"""Controller for the Sapphire Maintenance Consumable child doctype.

One part/consumable used during a visit on a Sapphire Maintenance Record
(``consumables`` table; ``istable``). Fields: ``item`` (Item), ``warehouse``,
``qty`` and optional ``serial_and_batch_bundle``. On record submit these rows are
issued via a "Material Issue" Stock Entry and billed on the draft Sales Invoice
(see ``api.maintenance_workflow``).

No custom controller logic; behaviour comes from the JSON field definitions.
"""

import frappe
from frappe.model.document import Document

class SapphireMaintenanceConsumable(Document):
	pass
