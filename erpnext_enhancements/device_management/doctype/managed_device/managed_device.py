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

It also holds a change of ``ownership`` to who may make it (``_guard_ownership_change``):
the remote-action executor reads that field to decide what a device accepts, so changing
it from BYOD to Company requires a System Manager, and every change is noted on the
device's timeline.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from erpnext_enhancements.device_management.compliance import (
	ASSIGNED_STATES,
	is_valid_transition,
)

#: Set by ``mdm_integration.api.confirm_device`` to the name of the device it is confirming,
#: for that one save. ``frappe.flags`` is request-local and built fresh by ``frappe.init``
#: (frappe v16.56.0 ``frappe/__init__.py`` line 169); no request parameter ever fills it.
CONFIRMING_DISCOVERED_FLAG = "mdm_confirming_discovered_device"


class ManagedDevice(Document):
	def validate(self):
		self._default_barcode()
		self._derive_assigned_user()
		self._normalise_identifiers()
		self._guard_status_transition()
		self._enforce_assignee_consistency()
		self._guard_ownership_change()

	def on_update(self):
		self._note_ownership_change()

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

	def _guard_ownership_change(self):
		"""Hold a change of ``ownership`` on an existing device to who may make it.

		``mdm_integration.actions.execute_device_action`` reads this field when an action is
		requested: a BYOD device accepts Locate and Lock only (``actions.BYOD_ACTIONS``). So the
		field is held to the same care as that rule, here in ``validate`` so it holds for the
		Desk form, the REST resource and ``frappe.client.set_value`` alike:

		* BYOD to Company widens what the device accepts, so it requires a System Manager on a
		  save that checks permissions. A save made with ``ignore_permissions`` (server code,
		  such as the provider sync in ``mdm_integration.mapping``) never makes this change.
		  The one exception is ``mdm_integration.api.confirm_device`` confirming a device whose
		  stored link state is still Discovered, which is how a person states its ownership for
		  the first time: that function names the device in ``frappe.flags``
		  (:data:`CONFIRMING_DISCOVERED_FLAG`) for that save only.
		* Company to BYOD narrows what the device accepts and stays open to anyone who may
		  write the device.
		* Every change is noted on the device's timeline (``_note_ownership_change``).
		"""
		self.flags.ownership_change = None
		if self.is_new():
			return
		previous = self.get_doc_before_save()
		if not previous:
			return
		old = previous.get("ownership") or ""
		new = self.get("ownership") or ""
		if old == new:
			return
		if old == "BYOD" and new == "Company" and not self._confirming_discovered(previous):
			if self.flags.ignore_permissions or "System Manager" not in frappe.get_roles():
				frappe.throw(
					_(
						"Only a System Manager can change a device's ownership from BYOD to Company. "
						"Ask a System Manager to make this change on the device's form."
					),
					frappe.PermissionError,
					title=_("Ownership Change Not Allowed"),
				)
		self.flags.ownership_change = (old, new)

	def _confirming_discovered(self, previous):
		"""True only inside ``confirm_device``'s save of this device while it is stored as Discovered."""
		return (
			getattr(frappe.flags, CONFIRMING_DISCOVERED_FLAG, None) == self.name
			and previous.get("mdm_link_state") == "Discovered"
		)

	def _note_ownership_change(self):
		"""Leave a Comment on the timeline saying who changed the ownership, from what to what, and when.

		Written after the save, so a refused change leaves none. The doctype tracks changes as
		well; the Comment sits on the timeline next to the device-action notes.
		"""
		change = self.flags.ownership_change
		if not change:
			return
		self.flags.ownership_change = None
		old, new = change
		self.add_comment(
			"Info",
			_("Ownership changed from {0} to {1} by {2} on {3}.").format(
				_(old), _(new), frappe.session.user, now_datetime()
			),
		)
