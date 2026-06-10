"""Additional Supplier Group child-table doctype controller.

Rows back the ``Supplier.custom_additional_supplier_groups`` Table MultiSelect
(see setup/supplier_groups.py and supplier_query.sync_supplier_groups).
Ported from the DB-only custom DocType that setup code previously created at
runtime; no custom controller logic.
"""

from frappe.model.document import Document


class AdditionalSupplierGroup(Document):
	pass
