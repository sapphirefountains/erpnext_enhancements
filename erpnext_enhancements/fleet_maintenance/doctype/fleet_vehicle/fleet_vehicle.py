"""Fleet Vehicle — a company vehicle tracked for routine maintenance.

The master holds identification + assignment plus the "last done" date for each
maintenance cadence. The matching "due" dates and the headline ``maintenance_status``
are derived on every save from those last-dates and the cadence intervals in
ERPNext Enhancements Settings (see ``fleet_maintenance/status.py``).
"""

from frappe.model.document import Document

from erpnext_enhancements.fleet_maintenance.status import compute_derived


class FleetVehicle(Document):
	def validate(self):
		# Recompute the due dates + headline status from the last-done dates so the
		# form reflects a manually seeded baseline immediately. Submitted logs keep
		# the last-done dates current via status.recompute_vehicle_status().
		compute_derived(self)
