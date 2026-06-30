"""Vehicle Maintenance Log — a single maintenance event on a Fleet Vehicle.

The form auto-fills its checklist from the picked Maintenance Type (see
``checklists.py``). On submit it rolls the vehicle's matching "last done" date
forward and recomputes its due dates + status (``status.recompute_vehicle_status``);
on cancel it recomputes from whatever submitted logs remain.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, nowdate

from erpnext_enhancements.fleet_maintenance.status import recompute_vehicle_status


class VehicleMaintenanceLog(Document):
	def validate(self):
		if not self.service_date:
			self.service_date = nowdate()
		self._warn_on_odometer_rollback()

	def before_submit(self):
		self._validate_mandatory_tasks()

	def on_submit(self):
		recompute_vehicle_status(self.vehicle, notify=False)

	def on_cancel(self):
		recompute_vehicle_status(self.vehicle, notify=False)

	# ------------------------------------------------------------------ helpers

	def _warn_on_odometer_rollback(self):
		"""A lower-than-recorded odometer is usually a typo — warn, don't block
		(a vehicle swap or a corrected earlier reading is legitimate)."""
		if not self.odometer or not self.vehicle:
			return
		current = cint(frappe.db.get_value("Fleet Vehicle", self.vehicle, "current_odometer"))
		if current and cint(self.odometer) < current:
			frappe.msgprint(
				_(
					"Odometer {0} is lower than the vehicle's last recorded reading ({1}). "
					"Please double-check before submitting."
				).format(self.odometer, current),
				indicator="orange",
				alert=True,
			)

	def _validate_mandatory_tasks(self):
		missing = [row.task for row in (self.checklist or []) if row.is_mandatory and not row.status]
		if missing:
			frappe.throw(
				_("These required checklist items still have no status:")
				+ "<br>"
				+ "<br>".join("• " + (task or "") for task in missing),
				title=_("Checklist Incomplete"),
			)
