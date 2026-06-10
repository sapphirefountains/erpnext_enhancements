"""Rent Deliverables child-table doctype controller.

Ported from the live site's DB-only custom DocType so that fresh installs
can create it before the Custom Field fixtures that reference it are
imported. No custom controller logic.
"""

from frappe.model.document import Document


class RentDeliverables(Document):
	pass
