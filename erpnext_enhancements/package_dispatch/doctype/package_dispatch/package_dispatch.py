# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Package Dispatch — an official record for sending a package out.

Captures what's being shipped (with a per-item value so you know how much to
insure for), a structured recipient address (no handwriting, searchable later),
and the delivery tracking. On save it totals the declared value, mirrors it into
the amount-to-insure default, derives the delivery status from the shipped /
delivered dates, and writes a plain-English contents summary from the item list
when one hasn't been typed.

The item description/value auto-fill and the customer address auto-fill are
conveniences layered on by the client + ``package_dispatch.api`` (gated by the
Package Dispatch switch); everything here works whether or not that switch is on,
so a fully hand-typed dispatch totals and submits the same way.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

from erpnext_enhancements.package_dispatch.api import get_item_value


class PackageDispatch(Document):
	def validate(self):
		if not self.dispatch_date:
			self.dispatch_date = nowdate()
		if not self.requested_by:
			self.requested_by = frappe.session.user
		self._backfill_item_details()
		self._compute_values()
		self._derive_shipment_status()
		self._fill_contents_summary()

	def before_submit(self):
		if not self.items:
			frappe.throw(_("Add at least one item before submitting this dispatch."))

	def on_update_after_submit(self):
		"""Tracking / shipped / delivered dates are added after the official dispatch
		is submitted (they're ``allow_on_submit``); keep the delivery status in step
		with them. ``db_set`` avoids re-triggering validation on the submitted doc."""
		status = "Not Shipped"
		if self.delivered_date:
			status = "Delivered"
		elif self.shipped_date or self.tracking_number:
			status = "Shipped"
		if status != self.shipment_status:
			self.db_set("shipment_status", status)

	# ------------------------------------------------------------------ helpers

	def _backfill_item_details(self):
		"""Fill description/value for rows that picked a catalog item but left them
		blank — the client normally does this live, but a form saved via the API or
		an import wouldn't have. Never overwrites a value the user typed."""
		for row in self.items or []:
			if not row.item:
				continue
			if not row.description:
				row.description = frappe.db.get_value("Item", row.item, "item_name") or row.item
			if not flt(row.rate):
				row.rate = get_item_value(row.item)

	def _compute_values(self):
		total = 0
		for row in self.items or []:
			if not row.qty:
				row.qty = 1
			row.amount = flt(row.qty) * flt(row.rate)
			total += flt(row.amount)
		self.total_declared_value = total
		if not flt(self.insured_value):
			self.insured_value = total

	def _derive_shipment_status(self):
		"""Delivery status is a read-only reflection of the dates: delivered wins,
		then anything that says it's on its way (a ship date or a tracking number)."""
		if self.delivered_date:
			self.shipment_status = "Delivered"
		elif self.shipped_date or self.tracking_number:
			self.shipment_status = "Shipped"
		else:
			self.shipment_status = "Not Shipped"

	def _fill_contents_summary(self):
		"""Compose the "tell the store what's inside" line from the items when the
		user hasn't written their own."""
		if (self.contents_summary or "").strip():
			return
		parts = []
		for row in self.items or []:
			desc = (row.description or "").strip()
			if not desc:
				continue
			qty = flt(row.qty)
			if qty and qty != 1:
				qty_label = int(qty) if qty == int(qty) else qty
				parts.append(f"{qty_label}× {desc}")
			else:
				parts.append(desc)
		if parts:
			self.contents_summary = "; ".join(parts)
