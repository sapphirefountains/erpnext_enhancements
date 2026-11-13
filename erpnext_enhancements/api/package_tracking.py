# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Package tracking on a Purchase Order: the endpoints behind the order's banner (v1.819.0).

What question each answers:

* **"Where are this order's packages?"** ``get_packages``: each tracking number with the carrier's
  status, estimated arrival, last check and tracking page, plus what the caller may do here.
* **"Here is the tracking number."** ``add_tracking_number``: records it, and when its carrier is
  live and the order's throttle allows, asks the carrier straight away.
* **"That one was wrong."** ``remove_tracking_number``.
* **"Check now."** ``refresh_order``.

What this module is careful about:

* **Off means off** (the Sapphire Manager's decision, review round 1). While Carrier Tracking
  Settings' master switch is off, ``get_packages`` answers ``{"enabled": false}`` after one cached
  settings read and nothing else, so the order form shows nothing; the three writes refuse with a
  sentence. With the switch on, credentials or not, numbers can be recorded (an *Other* carrier
  needs no credentials) and shown.
* **Which orders take a number** (``package_rules.order_accepts_packages``): a draft or submitted
  order that is not closed, completed, canceled, drop-shipped or fully received. A canceled order
  keeps its packages readable (no Add, Remove or Refresh), so an order that was canceled and not
  amended still shows what was on its way. An amended one has them moved to the amendment
  (``carrier_tracking/purchase_order_hooks.py``).
* **Who may add or remove a number on a submitted order** (``package_rules.MANAGE_ROLES``): anyone
  with *create* (to add) or *delete* (to remove) on Tracked Package, which its DocType grants to
  purchasing (PO Creator, Purchase User, Purchase Manager), the warehouse (Stock User, Stock
  Manager) and System Manager, **and** *read* on that order. A tracking number is a record of its
  own, not a row of the order, so neither needs write or submit on the Purchase Order: nobody's
  rights over the order itself are widened. Both checks are made here explicitly; the DocType's
  permissions, its ``has_permission`` hook and ``TrackedPackage.validate`` make them again.
* **Carrier calls inside a request are bounded** (``tracking.check_rows``): a few packages and a few
  seconds per request, one round per order per minute shared by Add and Refresh, and a per-person
  window. What is not reached waits for the hourly poller.
* **Every write is POST only**; ``get_packages`` is a read. Nothing takes a fieldname or filter from
  the client: the order name is the only key, and it is checked against the caller's permission.
* **Nothing secret leaves**: the rows carry ``last_error`` already masked by the client, and the
  settings are reported as booleans ("is FedEx live?"), never values.
"""

import frappe
from frappe import _

from erpnext_enhancements.carrier_tracking import carriers as C
from erpnext_enhancements.carrier_tracking import config, package_rules, tracking

DOCTYPE = tracking.DOCTYPE

SWITCHED_OFF = (
	"Package tracking is switched off. A System Manager can turn it on in Carrier Tracking Settings."
)

#: What ``get_packages`` answers while the feature is off: the form draws nothing.
OFF = {
	"enabled": False,
	"packages": [],
	"live": False,
	"live_carriers": [],
	"can_add": False,
	"can_remove": False,
	"can_refresh": False,
	"accepts_packages": False,
}


def _require_enabled():
	if not config.feature_enabled():
		frappe.throw(_(SWITCHED_OFF))


def _order(purchase_order):
	"""The order's name and state, after checking the caller may read it."""
	name = (purchase_order or "").strip()
	if not name or not frappe.db.exists("Purchase Order", name):
		frappe.throw(_("Purchase Order {0} was not found.").format(name or "?"), frappe.DoesNotExistError)
	if not frappe.has_permission("Purchase Order", "read", doc=name):
		frappe.throw(_("You cannot see Purchase Order {0}.").format(name), frappe.PermissionError)
	order = frappe.db.get_value(
		"Purchase Order", name, ["name", "docstatus", "status", "per_received"], as_dict=True
	)
	if not order:
		frappe.throw(_("Purchase Order {0} was not found.").format(name), frappe.DoesNotExistError)
	return order


def _accepts(order):
	return package_rules.order_accepts_packages(order.docstatus, order.status, order.per_received)


def _canceled(order):
	return int(order.docstatus or 0) == 2


def _require(ptype):
	if not frappe.has_permission(DOCTYPE, ptype):
		words = {
			"create": _("You cannot add tracking numbers."),
			"delete": _("You cannot remove tracking numbers."),
			"read": _("You cannot see tracking numbers."),
		}[ptype]
		frappe.throw(words, frappe.PermissionError)


def _payload(order):
	live = tracking.live_carriers()
	rows = tracking.rows_for_order(order.name, tracking.DISPLAY_FIELDS)
	accepts = _accepts(order)
	open_order = not _canceled(order)
	return {
		"enabled": True,
		"packages": rows,
		"live_carriers": live,
		"live": bool(live),
		"accepts_packages": accepts,
		"can_add": accepts and bool(frappe.has_permission(DOCTYPE, "create")),
		"can_remove": open_order and bool(frappe.has_permission(DOCTYPE, "delete")),
		"can_refresh": open_order and bool(live) and any(row.get("resolved_carrier") in live for row in rows),
	}


@frappe.whitelist()
def get_packages(purchase_order):
	"""The order's tracking numbers and what the caller may do with them. Cheap while off."""
	if not config.feature_enabled():
		return dict(OFF)
	order = _order(purchase_order)
	if not frappe.has_permission(DOCTYPE, "read"):
		# Somebody who reads orders but not tracking (External Contractor) gets an empty banner,
		# not an error on every order they open.
		return dict(OFF, enabled=True)
	return _payload(order)


@frappe.whitelist(methods=["POST"])
def add_tracking_number(purchase_order, tracking_number, carrier="Auto", link_url=None):
	"""Record a tracking number on an order and, when its carrier is live, check it now."""
	_require_enabled()
	order = _order(purchase_order)
	_require("create")
	if not _accepts(order):
		state = _("canceled") if _canceled(order) else _("closed or fully received")
		frappe.throw(
			_("Purchase Order {0} is {1}, so no package is on its way for it.").format(order.name, state)
		)
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

	# The first check shares the order's once-a-minute throttle with Refresh, so adding and removing
	# a number cannot be used to ask the carrier more often. When the throttle is taken, the poller
	# checks it within the hour.
	if (
		doc.resolved_carrier in C.TRACKABLE
		and doc.resolved_carrier in tracking.live_carriers()
		and tracking.claim_order_cooldown(order.name)
	):
		row = {field: doc.get(field) for field in tracking.ROW_FIELDS}
		tracking.check_rows([row], manual=True)

	return _payload(order)


@frappe.whitelist(methods=["POST"])
def remove_tracking_number(name):
	"""Delete one tracking number. The caller needs delete on Tracked Package and read on its order."""
	_require_enabled()
	_require("delete")
	# as_dict and a guard: get_value returns None for a row deleted a moment ago (two people removing
	# the same number), and a tuple-unpack of None would be a TypeError instead of a sentence.
	row = (
		frappe.db.get_value(DOCTYPE, name, ["reference_doctype", "reference_name"], as_dict=True)
		if name
		else None
	)
	if not row:
		frappe.throw(_("That tracking number is no longer there."), frappe.DoesNotExistError)
	if row.reference_doctype != "Purchase Order":
		frappe.throw(_("That tracking number does not belong to a Purchase Order."), frappe.PermissionError)
	order = _order(row.reference_name)
	if _canceled(order):
		frappe.throw(
			_("Purchase Order {0} is canceled; its packages are kept as they were.").format(order.name)
		)
	frappe.delete_doc(DOCTYPE, name)
	return _payload(order)


@frappe.whitelist(methods=["POST"])
def refresh_order(purchase_order):
	"""Ask the carriers about the order's FedEx and UPS packages now (once a minute at most, a few
	packages per request; the rest are the poller's)."""
	_require_enabled()
	order = _order(purchase_order)
	_require("read")
	checked = None if _canceled(order) else tracking.refresh_order(order.name)
	payload = _payload(order)
	payload["checked"] = checked
	payload["too_soon"] = checked is None and not _canceled(order)
	return payload
