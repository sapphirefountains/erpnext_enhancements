# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Tracked Package: one carrier tracking number on a Purchase Order, and what the carrier last said.

A record of its own rather than a child table of Purchase Order, for three reasons (the design
note in ``carrier_tracking/README.md`` has the long form):

* **Adding a number must not edit the order.** A child row of a submitted order can only be added
  by someone who may submit orders, and the people who know the tracking number (purchasing and
  the warehouse) mostly may not. Here the right is this DocType's own *create*, plus *read* on the
  order (``validate`` checks it).
* **An open order form cannot erase it.** Frappe deletes, on save, every child row the form did not
  load. A number added from the banner (or a status written by the poller) while someone had the
  order open would be wiped by their next save.
* **It can be calendared.** Frappe's calendar and list views need a DocType of their own; a child
  table has neither. The arrivals calendar the warehouse asked for later reads
  ``estimated_delivery`` here.

People add and remove these from the Purchase Order's banner (``api/package_tracking.py``). The
carrier fields are written only by ``carrier_tracking.tracking``, with ``db.set_value``; nobody but
a System Manager holds *write* on this DocType.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class TrackedPackage(Document):
	def validate(self):
		from erpnext_enhancements.carrier_tracking import config, package_rules

		if self.reference_doctype != "Purchase Order":
			frappe.throw(_("A tracking number can only be added to a Purchase Order for now."))

		if not self.is_new():
			return

		# Off means off, for a REST insert as much as for the banner's endpoint.
		if not self.flags.ignore_permissions and not config.feature_enabled():
			frappe.throw(_("Package tracking is switched off in Carrier Tracking Settings."))

		# Every carrier-written field comes from here, at its starting value, whatever the request
		# sent (package_rules.CARRIER_FIELD_DEFAULTS): a create cannot arrive already "Delivered", with
		# a made-up estimate, or with a future last_checked the poller would skip.
		fields, problem = package_rules.new_package_fields(self.tracking_number, self.carrier, self.link_url)
		if problem:
			frappe.throw(_(problem))

		order = frappe.db.get_value(
			"Purchase Order",
			self.reference_name,
			["name", "docstatus", "status", "per_received", "supplier"],
			as_dict=True,
		)
		if not order:
			frappe.throw(_("Purchase Order {0} was not found.").format(self.reference_name))
		# The right to add a number is this DocType's create permission plus read on the order. The
		# endpoint checks both before inserting; this repeats the order half for a REST insert.
		if not self.flags.ignore_permissions and not frappe.has_permission(
			"Purchase Order", "read", doc=order.name
		):
			frappe.throw(_("You cannot see Purchase Order {0}.").format(order.name), frappe.PermissionError)
		if not package_rules.order_accepts_packages(order.docstatus, order.status, order.per_received):
			frappe.throw(_("Purchase Order {0} is canceled, closed or fully received.").format(order.name))

		if frappe.db.exists(
			"Tracked Package",
			{
				"reference_doctype": self.reference_doctype,
				"reference_name": order.name,
				"tracking_number": fields["tracking_number"],
			},
		):
			frappe.throw(_("Tracking number {0} is already on this order.").format(fields["tracking_number"]))

		self.update(fields)
		self.supplier = order.supplier
