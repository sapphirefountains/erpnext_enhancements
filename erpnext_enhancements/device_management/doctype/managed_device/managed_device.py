"""Controller for the Managed Device doctype — the MDM system of record.

A company- or employee-owned device (phone / tablet / laptop / desktop) tracked
through its lifecycle (In Stock -> Assigned -> In Repair -> Lost/Stolen ->
Retired), with an append-only custody history (``assignment_history``) and a
compliance posture. In Phase 1 the posture is self-attested
(``compliance_source = "Manual"``); the Phase-2 ``mdm_integration`` provider
layer overwrites it from a live feed and stamps ``"Provider"``.

Lifecycle moves and check-out/check-in are driven by
``erpnext_enhancements.api.device_management`` (and the form buttons in
``managed_device.js``); this controller only keeps the record self-consistent on
save: it derives the denormalised assignee User, normalises hardware
identifiers, guards illegal status jumps, and enforces that a current assignee
exists exactly when the device is Assigned. The lifecycle/compliance *rules*
live in the frappe-free ``device_management.compliance`` module so they unit-test
bench-free.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_enhancements.device_management.compliance import (
	ASSIGNED_STATES,
	is_valid_transition,
)


class ManagedDevice(Document):
	def validate(self):
		self._default_barcode()
		self._derive_assigned_user()
		self._normalise_identifiers()
		self._guard_status_transition()
		self._enforce_assignee_consistency()

	# ------------------------------------------------------------------ helpers

	def _default_barcode(self):
		"""A device's scan barcode defaults to its asset tag when not given."""
		if not self.barcode and self.asset_tag:
			self.barcode = self.asset_tag

	def _derive_assigned_user(self):
		"""Denormalise the assignee Employee's linked User (or clear it)."""
		if self.assigned_to_employee:
			self.assigned_to_user = frappe.db.get_value("Employee", self.assigned_to_employee, "user_id")
		else:
			self.assigned_to_user = None

	def _normalise_identifiers(self):
		"""Trim/upper hardware identifiers so uniqueness and scans are stable."""
		for field in ("serial_number", "imei", "mac_address", "barcode"):
			value = self.get(field)
			if value:
				self.set(field, value.strip().upper())
		if self.phone_number:
			self.phone_number = self.phone_number.strip()

	def _guard_status_transition(self):
		"""Reject illegal lifecycle jumps (e.g. anything out of Retired)."""
		if self.is_new():
			return
		previous = self.get_doc_before_save()
		if not previous or previous.status == self.status:
			return
		if not is_valid_transition(previous.status, self.status):
			frappe.throw(
				_("A device cannot move from {0} to {1}.").format(
					_(previous.status), _(self.status)
				),
				title=_("Invalid Status Change"),
			)

	def _enforce_assignee_consistency(self):
		"""A current assignee exists exactly when the device is Assigned.

		Every other state clears the assignee (the custody history retains who
		held it). This keeps the denormalised ``assigned_to_*`` fields honest no
		matter whether a row was edited in the desk or via the device API.
		"""
		if self.status in ASSIGNED_STATES:
			if not self.assigned_to_employee:
				frappe.throw(
					_("An Assigned device must have an Assigned To (Employee). Use Check In to release it."),
					title=_("Missing Assignee"),
				)
		elif self.assigned_to_employee:
			frappe.throw(
				_("Only an Assigned device may have a current holder — Check In or Transfer device {0} first.").format(
					self.name
				),
				title=_("Device Still Assigned"),
			)
