# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Asset Out of Service — a fountain that cannot go out, and until when.

**Why this is not an Asset Booking.** A booking is a reservation and is refused when it
overlaps another one. Being broken is a fact, not a reservation: it has to be recordable
*over* rentals already on the calendar, and those rentals then need to hear about it. So an
outage is its own record. ``rental_availability.asset_conflicts`` reads it alongside the
calendar, which is what stops a new booking, and :meth:`AssetOutofService.after_insert`
comments on every live rental it lands on.

**What it blocks** is ``blocked_until`` (``rental_rules.out_of_service_end``): until it was
returned to service; while still out, through the expected-back date if there is one; with
no date, every future date.

Created by hand, or automatically (``asset_management/out_of_service.py``) from a Return
inspection that found damage and from a pending ERPNext Asset Repair.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, getdate, now_datetime

from erpnext_enhancements.asset_management import rental_rules as rules

OPEN = "Out of Service"
RETURNED = "Returned to Service"


class AssetOutofService(Document):
	def validate(self):
		self.asset_name = frappe.db.get_value("Asset", self.asset, "asset_name")
		if self.expected_back and getdate(self.expected_back) < getdate(self.out_from):
			frappe.throw(_("Expected Back cannot be before Out Since."))
		self.blocked_until = rules.out_of_service_end(
			self.status,
			returned_on=get_datetime(self.returned_on) if self.returned_on else None,
			expected_back=getdate(self.expected_back) if self.expected_back else None,
		)
		if self.status == OPEN:
			other = frappe.db.get_value(
				"Asset Out of Service", {"asset": self.asset, "status": OPEN, "name": ["!=", self.name]}, "name"
			)
			if other:
				frappe.throw(
					_("{0} is already out of service ({1}). Add to that record instead.").format(
						self.asset_name or self.asset, other
					)
				)

	def after_insert(self):
		self.flag_affected_rentals()

	def on_update(self):
		frappe.enqueue(
			"erpnext_enhancements.asset_management.doctype.asset_booking.asset_booking.update_asset_status",
			asset_name=self.asset,
		)

	@frappe.whitelist()
	def return_to_service(self):
		"""The form's Return to Service button."""
		self.check_permission("write")
		self.mark_returned()
		self.save()

	def mark_returned(self, returned_on=None):
		if self.status == RETURNED:
			return
		self.status = RETURNED
		self.returned_on = returned_on or now_datetime()
		self.returned_by = frappe.session.user

	def flag_affected_rentals(self):
		"""Comment on every live rental this outage lands on, and tell whoever filed it."""
		rows = frappe.db.sql(
			"""
			select distinct b.name, b.customer_name, b.status, f.block_from
			from `tabRental Booking Fountain` f
			inner join `tabRental Booking` b on b.name = f.parent
			where f.parenttype = 'Rental Booking'
				and f.asset = %(asset)s
				and b.status in ('Tentative', 'Confirmed', 'Out')
				and f.block_to > %(from)s
				and (%(until)s is null or f.block_from < %(until)s)
			order by f.block_from
			""",
			{"asset": self.asset, "from": self.out_from, "until": self.blocked_until},
			as_dict=True,
		)
		if not rows:
			return
		for row in rows:
			frappe.get_doc(
				{
					"doctype": "Comment",
					"comment_type": "Comment",
					"reference_doctype": "Rental Booking",
					"reference_name": row.name,
					"content": _(
						"<b>{0} went out of service</b> ({1}, {2}) and is on this booking. "
						"Swap in another fountain or arrange the repair before delivery."
					).format(
						frappe.utils.escape_html(self.asset_name or self.asset), _(self.reason), self.name
					),
				}
			).insert(ignore_permissions=True)
		frappe.msgprint(
			_("This fountain is on {0} live rental(s): {1}. Each has been flagged.").format(
				len(rows), ", ".join(r.name for r in rows)
			),
			title=_("Rentals affected"),
			indicator="orange",
		)
