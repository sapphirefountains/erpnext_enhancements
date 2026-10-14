"""What the Party entry component asks the server when the account changes (form kit P3.2).

TASK-2026-02755. The component (``public/js/form_kit/ui/party.js``) picks the account, then the
contact and the addresses from that account's own. When the account changes it re-suggests them
the way ERPNext's own Opportunity form does (``opportunity.js`` ``party_name`` ->
``erpnext.utils.get_party_details`` -> ``erpnext.accounts.party.set_contact_details`` /
``set_address_details``, at ``origin/version-16``), and to do that without overwriting a value
that is already right it needs two facts from here:

* **the account's defaults**, by ERPNext's own rules, read through ``frappe.get_list`` so the
  caller's permissions decide what is offered:

  - ``contact``: ``erpnext.accounts.party.get_default_contact``, the linked Contact flagged
    ``is_primary_contact``, else ``is_billing_contact``. Where neither flag is set, the account's
    own primary-contact field (this app's ``primary_contact`` on Customer, then ERPNext's
    ``customer_primary_contact``), because that is what ``Opportunity.map_fields`` copies into an
    empty ``primary_contact`` on the next save anyway; only when that Contact is linked to the
    account and readable.
  - ``billing``: ``frappe.contacts...address.get_default_address``, the linked, enabled Address
    flagged ``is_primary_address`` first, else the most recently changed one, exactly as ERPNext
    fills the Opportunity's ``customer_address``.
  - ``site``: ERPNext has no job site; the nearest rule it has is
    ``erpnext.accounts.party.get_party_shipping_address`` (flagged ``is_shipping_address``, else
    the only address of type Shipping). Production has 23 Shipping addresses against 843 Billing
    (2026-10-14), so on its own that rule would almost never suggest anything; an account with
    exactly **one** enabled address offers that one as the site too (a homeowner's house is
    both). Two or more and no shipping flag: nothing is suggested, and the person picks.

* **which of the record's current values are linked to the account, or to the record itself**, so
  a contact or address that belongs to the new account, or to the deal (a general contractor's job
  site), is kept without a note. The component never overwrites or empties a stored value anyway
  (``party.js`` ``plan_account_change``); this only decides whether it is flagged.

Permissions: the account kind must be one the Party block allows on the DocType
(``blocks.party_roles(...)["accounts"]``), the caller must be able to read the account, and every
Contact and Address comes through ``frappe.get_list``, never raw SQL, so user permissions and
sharing apply. Read-only: nothing is written.
"""

import json

import frappe
from frappe import _
from frappe.utils import escape_html

from erpnext_enhancements.form_kit.blocks import party_roles

#: How many names of each kind a request may ask about, and how many linked rows are read.
MAX_NAMES = 20
MAX_ROWS = 200

#: The account's own primary-contact fields, in the order they are trusted (see the module docstring).
ACCOUNT_CONTACT_FIELDS = ("primary_contact", "customer_primary_contact")


def _names(value):
	"""A JSON list (or list) of document names as text, at most :data:`MAX_NAMES`, blanks dropped."""
	if value in (None, ""):
		return []
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except ValueError:
			frappe.throw(_("Names must be sent as a JSON list."))
	if not isinstance(value, list | tuple):
		frappe.throw(_("Names must be sent as a JSON list."))
	out = []
	for item in value:
		if isinstance(item, str) and item.strip() and item not in out:
			out.append(item)
	return out[:MAX_NAMES]


def _link_filters(party_type, party):
	return [["Dynamic Link", "link_doctype", "=", party_type], ["Dynamic Link", "link_name", "=", party]]


def _linked(doctype, party_type, party, names, extra=None):
	"""Those of ``names`` that are ``doctype`` records linked to the party and readable by the caller.

	``distinct``: the Dynamic Link filter is a join, and a record with the same link twice (the Zoho
	import left such rows) would otherwise come back twice and, under a tight limit, push another
	linked record out of the answer (review of #1291). The limit is :data:`MAX_ROWS`, not
	``len(names)``, for the same reason.
	"""
	if not names:
		return []
	rows = frappe.get_list(
		doctype,
		filters=[*_link_filters(party_type, party), *(extra or []), ["name", "in", names]],
		fields=["name"],
		distinct=True,
		limit_page_length=MAX_ROWS,
		order_by="name asc",
	)
	found = {row.get("name") for row in rows}
	return [name for name in names if name in found]


def _linked_to_any(doctype, links, names):
	"""Those of ``names`` linked to any of ``links`` (``[(link_doctype, link_name), ...]``), in order."""
	found = set()
	for link_doctype, link_name in links:
		found.update(_linked(doctype, link_doctype, link_name, [n for n in names if n not in found]))
	return [name for name in names if name in found]


def default_contact(party_type, party):
	"""The account's contact by the rule in the module docstring, or None."""
	flagged = frappe.get_list(
		"Contact",
		filters=_link_filters(party_type, party),
		or_filters=[["is_primary_contact", "=", 1], ["is_billing_contact", "=", 1]],
		fields=["name"],
		order_by="is_primary_contact desc, is_billing_contact desc, modified desc",
		limit_page_length=1,
	)
	if flagged:
		return flagged[0].get("name")
	meta = frappe.get_meta(party_type)
	fields = [field for field in ACCOUNT_CONTACT_FIELDS if meta.has_field(field)]
	if not fields:
		return None
	own = frappe.db.get_value(party_type, party, fields, as_dict=True) or {}
	for field in fields:
		name = own.get(field)
		if name and _linked("Contact", party_type, party, [name]):
			return name
	return None


def default_addresses(party_type, party):
	"""``(billing, site)`` by the rules in the module docstring; either may be None."""
	found = frappe.get_list(
		"Address",
		filters=[*_link_filters(party_type, party), ["disabled", "=", 0]],
		fields=["name", "is_primary_address", "is_shipping_address", "address_type"],
		order_by="is_primary_address desc, modified desc",
		distinct=True,
		limit_page_length=MAX_ROWS,
	)
	# Once each, whatever the join returned: "exactly one address" counts addresses, not link rows.
	rows, seen = [], set()
	for row in found:
		if row.get("name") not in seen:
			seen.add(row.get("name"))
			rows.append(row)
	if not rows:
		return None, None
	billing = rows[0].get("name")
	shipping = [
		row for row in rows if row.get("is_shipping_address") or row.get("address_type") == "Shipping"
	]
	flagged = [row for row in shipping if row.get("is_shipping_address")]
	if flagged:
		site = flagged[0].get("name")
	elif len(shipping) == 1:
		site = shipping[0].get("name")
	elif len(rows) == 1:
		site = billing
	else:
		site = None
	return billing, site


def details(doctype, party_type, party, contacts=None, addresses=None, name=None):
	"""``form_kit.api.party_details``: see the module docstring. The caller has checked the DocType.

	``name`` is the record whose account changed: a Contact or Address linked to the record itself
	counts as linked too, so a general contractor's job site (linked to the Opportunity only, the
	2026-10-06 rule) is never reported as not belonging. Read on the record is required.
	"""
	roles = party_roles(doctype)
	if not roles or party_type not in roles["accounts"]:
		frappe.throw(
			_("{0} cannot be the account of {1} here.").format(escape_html(str(party_type)), _(doctype)),
			frappe.PermissionError,
		)
	if not isinstance(party, str) or not party.strip():
		frappe.throw(_("Choose an account first."))
	frappe.has_permission(party_type, "read", doc=party, throw=True)
	links = [(party_type, party)]
	if name not in (None, ""):
		if not isinstance(name, str):
			frappe.throw(_("The record must be named as text."))
		frappe.has_permission(doctype, "read", doc=name, throw=True)
		links.append((doctype, name))
	billing, site = default_addresses(party_type, party)
	return {
		"defaults": {"contact": default_contact(party_type, party), "billing": billing, "site": site},
		"linked": {
			"Contact": _linked_to_any("Contact", links, _names(contacts)),
			"Address": _linked_to_any("Address", links, _names(addresses)),
		},
	}
