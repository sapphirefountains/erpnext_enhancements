# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Package tracking on a Purchase Order: the endpoints behind the order's banner (v1.819.0).

What question each answers:

* **"Where are this order's packages?"** ``get_packages``: each tracking number with the carrier's
  status, estimated arrival, last check and tracking page, plus what the caller may do and whether
  live status is switched on.
* **"Here is the tracking number."** ``add_tracking_number``: records it, and when the carrier is
  live, asks the carrier straight away so the banner shows a status.
* **"That one was wrong."** ``remove_tracking_number``.
* **"Check now."** ``refresh_order``.

What this module is careful about:

* **Who may add or remove a number on a submitted order** (``package_rules.MANAGE_ROLES``): anyone
  with *create* (to add) or *delete* (to remove) on Tracked Package, which its DocType grants to
  purchasing (PO Creator, Purchase User, Purchase Manager), the warehouse (Stock User, Stock
  Manager) and System Manager, **and** *read* on that order. A tracking number is a record of its
  own, not a row of the order, so neither needs write or submit on the Purchase Order: nobody's
  rights over the order itself are widened. Both checks are made here explicitly, and the
  DocType's own permissions (and ``TrackedPackage.validate``) make them again.
* **Every write is POST only**; ``get_packages`` is a read. Nothing takes a fieldname or filter from
  the client: the order name is the only key, and it is checked against the caller's permission.
* **Nothing secret leaves**: the rows carry ``last_error`` already masked by the client, and the
  settings are reported as booleans ("is FedEx live?"), never values.
* **No carrier call while the feature is off.** Adding a number then records it with its carrier
  link, and Refresh is not offered.
"""

import frappe
from frappe import _

from erpnext_enhancements.carrier_tracking import carriers as C
from erpnext_enhancements.carrier_tracking import package_rules, tracking

DOCTYPE = tracking.DOCTYPE


def _order(purchase_order):
	"""The order's name and state, after checking the caller may read it."""
	name = (purchase_order or "").strip()
	if not name or not frappe.db.exists("Purchase Order", name):
		frappe.throw(_("Purchase Order {0} was not found.").format(name or "?"), frappe.DoesNotExistError)
	if not frappe.has_permission("Purchase Order", "read", doc=name):
		frappe.throw(_("You cannot see Purchase Order {0}.").format(name), frappe.PermissionError)
	return frappe.db.get_value("Purchase Order", name, ["name", "docstatus", "status"], as_dict=True)


def _require(ptype):
	if not frappe.has_permission(DOCTYPE, ptype):
		words = {
			"create": _("You cannot add tracking numbers."),
			"delete": _("You cannot remove tracking numbers."),
			"read": _("You cannot see tracking numbers."),
		}[ptype]
		frappe.throw(words, frappe.PermissionError)


def _payload(purchase_order):
	live = tracking.live_carriers()
	rows = tracking.rows_for_order(purchase_order, tracking.DISPLAY_FIELDS)
	return {
		"packages": rows,
		"live_carriers": live,
		"live": bool(live),
		"can_add": bool(frappe.has_permission(DOCTYPE, "create")),
		"can_remove": bool(frappe.has_permission(DOCTYPE, "delete")),
		"can_refresh": bool(live) and any(row.get("resolved_carrier") in live for row in rows),
	}


@frappe.whitelist()
def get_packages(purchase_order):
	"""The order's tracking numbers and what the caller may do with them."""
	order = _order(purchase_order)
	if not frappe.has_permission(DOCTYPE, "read"):
		# Somebody who reads orders but not tracking (External Contractor) gets an empty banner,
		# not an error on every order they open.
		return {
			"packages": [],
			"live": False,
			"live_carriers": [],
			"can_add": False,
			"can_remove": False,
			"can_refresh": False,
		}
	return _payload(order.name)


@frappe.whitelist(methods=["POST"])
def add_tracking_number(purchase_order, tracking_number, carrier="Auto", link_url=None):
	"""Record a tracking number on an order and, when its carrier is live, check it now."""
	order = _order(purchase_order)
	_require("create")
	if order.docstatus == 2:
		frappe.throw(_("Purchase Order {0} is canceled.").format(order.name))
	fields, problem = package_rules.new_package_fields(tracking_number, carrier, link_url)
	if problem:
		frappe.throw(_(problem))

	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"reference_doctype": "Purchase Order",
			"reference_name": order.name,
			"tracking_number": fields["tracking_number"],
			"carrier": fields["carrier"],
			"link_url": link_url or "",
		}
	)
	doc.insert()

	if doc.resolved_carrier in C.TRACKABLE and doc.resolved_carrier in tracking.live_carriers():
		row = {field: doc.get(field) for field in tracking.ROW_FIELDS}
		tracking.check_rows([row], manual=True)

	return _payload(order.name)


@frappe.whitelist(methods=["POST"])
def remove_tracking_number(name):
	"""Delete one tracking number. The caller needs delete on Tracked Package and read on its order."""
	_require("delete")
	if not name or not frappe.db.exists(DOCTYPE, name):
		frappe.throw(_("That tracking number is no longer there."), frappe.DoesNotExistError)
	reference_doctype, purchase_order = frappe.db.get_value(
		DOCTYPE, name, ["reference_doctype", "reference_name"]
	)
	if reference_doctype != "Purchase Order":
		frappe.throw(_("That tracking number does not belong to a Purchase Order."), frappe.PermissionError)
	order = _order(purchase_order)
	frappe.delete_doc(DOCTYPE, name)
	return _payload(order.name)


@frappe.whitelist(methods=["POST"])
def refresh_order(purchase_order):
	"""Ask the carriers about every FedEx and UPS package on the order now (once a minute at most)."""
	order = _order(purchase_order)
	_require("read")
	checked = tracking.refresh_order(order.name)
	payload = _payload(order.name)
	payload["checked"] = checked
	payload["too_soon"] = checked is None
	return payload
