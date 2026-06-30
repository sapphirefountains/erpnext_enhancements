"""Vehicle Maintenance Task — one checklist line on a Vehicle Maintenance Log
(child table). Items are seeded from the per-cadence default checklist
(``fleet_maintenance/checklists.py``) when a type is picked on the log."""

from frappe.model.document import Document


class VehicleMaintenanceTask(Document):
	pass
