# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Getting a product order to the customer (TASK-2026-03231).

Hooks on Delivery Note:

* ``delivery_note_validate``: a Carrier shipping note gets its tracking number cleaned (upper case,
  no spaces or dashes) and its tracking link (``custom_tracking_url``) from the carrier's public page
  (``shipping_rules.carrier_for`` / ``tracking_url``). Any other method clears the link.
* ``delivery_note_before_submit``: a note that ships a configured product must say how it travels;
  any method chosen is held to what it needs (``shipping_rules.method_problems``). Runs after the QC
  gate (``qc_gate.delivery_note_before_submit``), so an untested unit is refused for that first.
* ``delivery_note_on_submit``: Carrier shipping emails the customer contact "Shipped" with the
  tracking link; Sapphire delivery assigns the note to the person delivering it, on the delivery
  date.
* ``delivery_note_on_update``: a draft Sapphire delivery is assigned as soon as it has a person, so
  they see it before the day.

Whitelisted, for the form:

* ``send_ready_for_pickup``: the "Ready for pickup" email, from a draft Pickup note whose units have
  all passed their tests. A person presses it; the note is submitted when the customer collects.
* ``resend_shipped``: the "Shipped" email again.
* ``carrier_status``: what FedEx or UPS says about the tracking number, through ``carrier_tracking``
  (its switch and credentials, its client). Nothing is stored.

Emails go through the shared shell (``email_style.render``, ``templates/emails/_shell.html``), queued
in Email Queue (the database, not Redis, so a deploy's FLUSHDB does not lose them), with a comment on
the note saying who was emailed.
"""

import frappe
from frappe import _
from frappe.utils import cint, formatdate, now_datetime

from erpnext_enhancements import email_style
from erpnext_enhancements.product_fulfillment import qc_gate
from erpnext_enhancements.product_fulfillment import shipping_rules as sr
from erpnext_enhancements.product_fulfillment.settings import get_settings, has_column

DN = "Delivery Note"
READY_TEMPLATE = "erpnext_enhancements/templates/emails/product_fulfillment/ready_for_pickup.html"
SHIPPED_TEMPLATE = "erpnext_enhancements/templates/emails/product_fulfillment/shipped.html"
DEFAULT_PICKUP = (
	"Reply to this email to arrange a time to collect it. Please bring this email or your order number."
)


def _installed():
	return has_column(DN, "custom_fulfillment_method")


def _carrier_table():
	try:
		doc = frappe.get_cached_doc("Product Build Settings")
		return [
			{"transporter": r.transporter, "carrier": r.carrier, "tracking_url": r.tracking_url}
			for r in doc.get("carriers") or ()
		]
	except Exception:
		return []


def _carrier(doc):
	return sr.carrier_for(doc.get("transporter"), doc.get("transporter_name"), _carrier_table())


# ---------------------------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------------------------


def delivery_note_validate(doc, method=None):
	"""Clean a Carrier shipping note's tracking number and set its tracking link."""
	if not _installed():
		return
	if doc.get("custom_fulfillment_method") != sr.CARRIER:
		doc.custom_tracking_url = None
		return
	if doc.get("lr_no"):
		doc.lr_no = sr.clean_tracking(doc.lr_no)
	carrier, template = _carrier(doc)
	doc.custom_tracking_url = sr.tracking_url(carrier, doc.get("lr_no"), template) or None


def delivery_note_before_submit(doc, method=None):
	"""A configured product says how it travels, and a chosen method has what it needs."""
	if not _installed() or cint(doc.get("is_return")):
		return
	required = bool(qc_gate.gate_rows(doc))
	problems = sr.method_problems(
		doc.get("custom_fulfillment_method"),
		doc.get("transporter"),
		doc.get("lr_no"),
		doc.get("custom_delivery_date"),
		doc.get("custom_delivery_person"),
		required=required,
	)
	if problems:
		frappe.throw(
			"<br>".join(_(sr.PROBLEM_MESSAGES[code]) for code in problems), title=_("How does it get there?")
		)


def ships_a_product(doc):
	"""The note ships at least one configured product unit (the QC gate's own rows).

	The two customer emails say the order passed its pre-shipment test and carries the product
	warranty, which is true only of a configured product. A note that ships anything else, even marked
	Carrier shipping, sends neither.
	"""
	return bool(qc_gate.gate_rows(doc))


def delivery_note_on_submit(doc, method=None):
	"""Shipped email for a carrier shipment of a product; the delivery person's assignment for a
	Sapphire delivery (of anything)."""
	if not _installed() or cint(doc.get("is_return")):
		return
	method = doc.get("custom_fulfillment_method")
	if method == sr.CARRIER and cint(get_settings().get("send_shipped_email", 1)) and ships_a_product(doc):
		_send(doc, sr.SHIPPED, quiet=True)
	elif method == sr.SAPPHIRE:
		_assign_delivery(doc)


def delivery_note_on_update(doc, method=None):
	"""A draft Sapphire delivery is assigned once it names a person."""
	if not _installed() or cint(doc.get("docstatus")) != 0:
		return
	if doc.get("custom_fulfillment_method") == sr.SAPPHIRE:
		_assign_delivery(doc)


def _assign_delivery(doc):
	"""Assign the note to its delivery person, once; never raises."""
	user = doc.get("custom_delivery_person")
	if not user:
		return
	try:
		if frappe.db.exists(
			"ToDo",
			{"reference_type": DN, "reference_name": doc.name, "allocated_to": user, "status": "Open"},
		):
			return
		# The whitelisted ``assign_to.add`` takes only ``args`` (v16.56.0); ``ignore_permissions`` belongs
		# to ``_add``, which also sends the assignment notification. The person saving the note may not
		# be allowed to assign others, and the assignment is a consequence of a note they may save.
		from frappe.desk.form import assign_to

		when = doc.get("custom_delivery_date")
		assign_to._add(
			{
				"assign_to": [user],
				"doctype": DN,
				"name": doc.name,
				"date": when,
				"description": _("Deliver {0} to {1} on {2}.").format(
					doc.name,
					doc.get("customer_name") or doc.get("customer"),
					formatdate(when) if when else _("the agreed day"),
				),
			},
			ignore_permissions=True,
		)
	except Exception:
		frappe.log_error(title="Product Fulfillment: delivery assignment", message=frappe.get_traceback())


# ---------------------------------------------------------------------------------------------
# The emails
# ---------------------------------------------------------------------------------------------


def recipient(doc):
	"""The customer contact's email: the note's own, else its contact's, else the customer's."""
	contact_email = None
	if doc.get("contact_person"):
		contact_email = frappe.db.get_value("Contact", doc.contact_person, "email_id")
	customer_email = (
		frappe.db.get_value("Customer", doc.get("customer"), "email_id") if doc.get("customer") else None
	)
	return sr.first_email(doc.get("contact_email"), contact_email, customer_email)


def _greeting_name(doc):
	if doc.get("contact_person"):
		first = frappe.db.get_value("Contact", doc.contact_person, "first_name")
		if first:
			return first
	return None


def _units(doc):
	"""``[[label, serials], ...]``: every line, its item and the serial numbers on it."""
	from erpnext_enhancements.product_fulfillment.serials import serials_of_row

	rows = []
	for row in doc.get("items") or ():
		label = row.get("item_name") or row.get("item_code")
		if row.get("item_name") and row.get("item_code") and row.item_code not in row.item_name:
			label = f"{row.item_name} ({row.item_code})"
		serials = ", ".join(serials_of_row(row)) or "-"
		qty = row.get("qty")
		if qty and float(qty) != 1:
			label = f"{label} x {float(qty):g}"
		rows.append([label, serials])
	return rows


def _references(doc):
	orders = sorted(
		{row.get("against_sales_order") for row in doc.get("items") or () if row.get("against_sales_order")}
	)
	refs = []
	if orders:
		refs.append((_("Order"), ", ".join(orders)))
	if doc.get("po_no"):
		refs.append((_("Your PO"), doc.po_no))
	return refs


def context_for(doc, kind):
	settings = get_settings()
	carrier, template = _carrier(doc)
	return {
		"greeting_name": _greeting_name(doc),
		"units": _units(doc),
		"references": _references(doc),
		"pickup_instructions": (settings.get("pickup_instructions") or "").strip() or DEFAULT_PICKUP,
		"carrier": doc.get("transporter_name") or doc.get("transporter") or carrier,
		"tracking_number": doc.get("lr_no"),
		"tracking_url": doc.get("custom_tracking_url")
		or sr.tracking_url(carrier, doc.get("lr_no"), template),
		"ship_date": formatdate(doc.get("lr_date") or doc.get("posting_date")),
	}


def _send(doc, kind, quiet=False):
	"""Send one customer email for the note and record it. Returns the address, or None.

	``quiet``: called from ``on_submit``, where a missing address or a failure must not undo the
	submit, so it says so in an orange message and returns.
	"""
	to = recipient(doc)
	what = _("Ready for pickup") if kind == sr.READY_FOR_PICKUP else _("Shipped")
	if not to:
		message = _("The {0} email was not sent: the customer contact on {1} has no email address.").format(
			what, doc.name
		)
		if quiet:
			frappe.msgprint(message, indicator="orange", alert=True)
			return None
		frappe.throw(message)
	try:
		if kind == sr.READY_FOR_PICKUP:
			subject = _("Your order is ready for pickup ({0})").format(doc.name)
			template, title = READY_TEMPLATE, _("Your order is ready for pickup")
		else:
			subject = _("Your order has shipped ({0})").format(doc.name)
			template, title = SHIPPED_TEMPLATE, _("Your order has shipped")
		frappe.sendmail(
			recipients=[to],
			subject=subject,
			message=email_style.render(
				template, context_for(doc, kind), title=title, eyebrow=_("Your order"), tagline=True
			),
			reference_doctype=DN,
			reference_name=doc.name,
		)
		field = (
			"custom_ready_email_sent_on" if kind == sr.READY_FOR_PICKUP else "custom_shipped_email_sent_on"
		)
		if has_column(DN, field):
			doc.db_set(field, now_datetime(), update_modified=False)
		doc.add_comment("Info", _("{0} email sent to {1}.").format(what, to))
		return to
	except Exception:
		frappe.log_error(title=f"Product Fulfillment: {what} email", message=frappe.get_traceback())
		if quiet:
			frappe.msgprint(
				_("The {0} email could not be sent; the error is in the Error Log.").format(what),
				indicator="orange",
				alert=True,
			)
			return None
		raise


@frappe.whitelist(methods=["POST"])
def send_ready_for_pickup(delivery_note):
	"""Email the customer that their order is ready at the shop. Draft Pickup notes only, and only
	once every unit on it has passed its test."""
	doc = frappe.get_doc(DN, delivery_note)
	doc.check_permission("write")
	if cint(doc.docstatus) != 0:
		frappe.throw(_("Send it from the draft, before the customer collects."))
	if doc.get("custom_fulfillment_method") != sr.PICKUP:
		frappe.throw(_("This note is not a pickup at the shop."))
	if not ships_a_product(doc):
		frappe.throw(_("This email is for a product order: nothing on this note is a configured product."))
	problems = qc_gate.gate_problems(doc)
	if problems:
		frappe.throw("<br>".join([_("Not ready yet:"), *problems]), title=_("Ready for pickup"))
	return {"sent_to": _send(doc, sr.READY_FOR_PICKUP)}


@frappe.whitelist(methods=["POST"])
def resend_shipped(delivery_note):
	"""Send the Shipped email again (a corrected address, a customer who lost it)."""
	doc = frappe.get_doc(DN, delivery_note)
	doc.check_permission("write")
	if cint(doc.docstatus) != 1 or doc.get("custom_fulfillment_method") != sr.CARRIER:
		frappe.throw(_("Only a submitted Carrier shipping note has a Shipped email."))
	if not ships_a_product(doc):
		frappe.throw(_("This email is for a product order: nothing on this note is a configured product."))
	return {"sent_to": _send(doc, sr.SHIPPED)}


@frappe.whitelist(methods=["GET"])
def carrier_status(delivery_note):
	"""What FedEx or UPS says about the note's tracking number now. Nothing is stored."""
	doc = frappe.get_doc(DN, delivery_note)
	doc.check_permission("read")
	carrier, _template = _carrier(doc)
	number = sr.clean_tracking(doc.get("lr_no"))
	if not number:
		return {"outcome": "no_number"}
	from erpnext_enhancements.carrier_tracking import config as tracking_config

	if carrier not in (sr.UPS, sr.FEDEX):
		return {"outcome": "unsupported", "carrier": carrier}
	if not tracking_config.feature_enabled():
		return {"outcome": "off", "carrier": carrier}
	return tracking_config.track(carrier, number)
