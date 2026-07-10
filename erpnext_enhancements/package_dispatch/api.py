"""Server helpers for the Package Dispatch form.

Two conveniences, both gated by the ``package_dispatch_enabled`` switch (ERPNext
Enhancements Settings → Package Dispatch):

* :func:`get_item_dispatch_details` — pull a catalog Item's name + selling value
  when someone picks it on a dispatch line, so the description and unit value fill
  in without retyping.
* :func:`get_customer_ship_to` — pull a Customer's primary address, name and phone
  into the recipient block.

The form itself works with the switch OFF — you just type the description, value
and address by hand. These endpoints only power the auto-fill, so they refuse when
the feature is disabled (matching the Product Configurator generation guards); the
client checks ``frappe.boot.ee_package_dispatch`` first and never calls them off.

Pricing follows the codebase convention (``api/maintenance_workflow.py`` /
``product_configurator``): the live **Standard Selling** Item Price, falling back
to the Item master's static rates. There is no client-side ``fetch_from`` for
Item pricing anywhere in this app.
"""

import frappe
from frappe.utils import flt

from erpnext_enhancements.feature_flags import throw_if_package_dispatch_disabled

SELLING_PRICE_LIST = "Standard Selling"


def get_item_value(item_code):
	"""Best available per-unit value for an Item, for the declared/insured value.

	Prefers the live Standard Selling price; falls back to the Item master's
	``standard_rate`` then ``valuation_rate``. Returns 0 when nothing is set.
	"""
	if not item_code:
		return 0
	rate = frappe.db.get_value(
		"Item Price",
		{"item_code": item_code, "price_list": SELLING_PRICE_LIST, "selling": 1},
		"price_list_rate",
	)
	if rate:
		return flt(rate)
	fallback = frappe.db.get_value("Item", item_code, ["standard_rate", "valuation_rate"]) or (0, 0)
	std, val = fallback
	return flt(std) or flt(val) or 0


@frappe.whitelist()
def get_item_dispatch_details(item_code):
	"""Description + unit value for a dispatch line when a catalog Item is picked."""
	throw_if_package_dispatch_disabled()
	if not item_code:
		return {}
	item_name = frappe.db.get_value("Item", item_code, "item_name") or item_code
	return {"description": item_name, "rate": get_item_value(item_code)}


@frappe.whitelist()
def get_customer_ship_to(customer):
	"""Recipient name / address / phone from a Customer's primary address.

	Everything returned is a suggestion the user can edit; blanks are simply left
	for manual entry (a customer with no primary address still fills the name).
	"""
	throw_if_package_dispatch_disabled()
	if not customer:
		return {}
	cust = frappe.db.get_value(
		"Customer",
		customer,
		["customer_name", "customer_primary_address", "customer_primary_contact"],
		as_dict=True,
	)
	if not cust:
		return {}
	out = {"recipient_name": cust.customer_name, "recipient_company": cust.customer_name}
	if cust.customer_primary_address:
		addr = frappe.db.get_value(
			"Address",
			cust.customer_primary_address,
			["address_line1", "address_line2", "city", "state", "pincode", "country", "phone"],
			as_dict=True,
		)
		if addr:
			out.update(
				{
					"address_line1": addr.address_line1,
					"address_line2": addr.address_line2,
					"city": addr.city,
					"state": addr.state,
					"pincode": addr.pincode,
					"country": addr.country,
				}
			)
			if addr.phone:
				out["recipient_phone"] = addr.phone
	if not out.get("recipient_phone") and cust.customer_primary_contact:
		phone = frappe.db.get_value(
			"Contact", cust.customer_primary_contact, "phone"
		) or frappe.db.get_value("Contact", cust.customer_primary_contact, "mobile_no")
		if phone:
			out["recipient_phone"] = phone
	# drop empty keys so the client only overwrites fields we actually resolved
	return {k: v for k, v in out.items() if v}
