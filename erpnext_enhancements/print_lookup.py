# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The facts a print format cannot find for itself: who a document is for, and what
its taxes table really holds.

The Sapphire formats (``enhancements_core/setup_*print_formats.py``) are Jinja, and
the print sandbox can read a field but cannot follow a chain of fallbacks across four
doctypes, check which custom fields a site has, or render an Address it was not handed.
These functions do that in Python and are registered in ``hooks.py`` as ``ps_*`` Jinja
globals beside :mod:`print_style`, which owns how the answers *look*.

**Why a party needs a lookup at all** (measured on production, 2026-09-24). A document
carries a snapshot of its party's address and a contact's phone and email, but on this
site the snapshot is mostly empty:

* ``address_display`` is set on 17 of 230 Purchase Orders and 0 of 10 Purchase
  Invoices. Suppliers are rarely given an Address on the order; 35 of the suppliers on
  those orders have a primary address on their own record.
* ``contact_mobile`` and ``contact_email`` are empty on every Purchase Order and on all
  but 12 of 1,629 Sales Invoices -- not because nobody has a phone, but because this
  app keeps them in its own fields (``Contact.custom_email``, ``custom_mobile_number``,
  ``custom_phone_number``; ``Supplier.custom_phone_number`` / ``custom_email``;
  ``Customer.custom_accounts_phone_number`` / ``custom_accounts_email_address``;
  see ``sync_contact.py``). ERPNext's ``get_contact_details`` reads only the stock
  fields, so it copies blanks onto the document.

So each value walks a fixed order -- the document, then the contact it names, then
the address it prints, then the party's own record -- and the first one found wins.
Every step is deterministic: a party with several contacts and none marked primary
yields nothing from them, rather than whichever one the database returns first.

**Defensive on purpose.** Every custom field is checked against the doctype's meta
before it is asked for (``frappe.db.get_value`` raises on a column that does not exist,
and a fresh site has none of this app's fields), and any failure falls back to what
the document itself carries. A print that loses a phone number is a small thing; a
print that raises is a blank page.
"""

import frappe

from erpnext_enhancements import print_style as ps

# What to read from each kind of party, in order. `address` entries are Link fields to
# Address; `address_text` is the party's own rendered snapshot, used only when no link
# resolves.
PARTY_FIELDS = {
	"Customer": {
		"title": ("customer_name",),
		"address": ("customer_primary_address", "custom_billing_address"),
		"address_text": ("primary_address",),
		"contact": ("customer_primary_contact",),
		"phone": ("custom_accounts_phone_number", "mobile_no"),
		"email": ("custom_accounts_email_address", "email_id"),
	},
	"Supplier": {
		"title": ("supplier_name",),
		"address": ("supplier_primary_address",),
		"address_text": ("primary_address",),
		"contact": ("supplier_primary_contact",),
		"phone": ("custom_phone_number", "mobile_no"),
		"email": ("custom_email", "email_id"),
	},
	"Lead": {
		"title": ("company_name", "lead_name"),
		"address": (),
		"address_text": (),
		"contact": (),
		"phone": ("mobile_no", "phone"),
		"email": ("email_id",),
	},
}

# A Contact's phone and email. The app's own fields first: they are the ones filled
# (Contact.custom_email on 1,741 of 2,792 contacts; the stock email_id on 436).
CONTACT_PHONE = ("custom_mobile_number", "custom_phone_number", "mobile_no", "phone")
CONTACT_EMAIL = ("custom_email", "email_id")
CONTACT_NAME = ("full_name", "first_name", "last_name")

ADDRESS_PHONE = ("phone",)
ADDRESS_EMAIL = ("email_id",)


def _fields(doctype, candidates):
	"""The subset of ``candidates`` the doctype actually has on this site."""
	try:
		meta = frappe.get_meta(doctype)
	except Exception:
		return []
	return [f for f in candidates if meta.has_field(f)]


def _values(doctype, name, candidates):
	"""``{field: value}`` for the existing ``candidates`` of one record, or ``{}``."""
	if not name:
		return {}
	fields = _fields(doctype, candidates)
	if not fields:
		return {}
	row = frappe.db.get_value(doctype, name, fields, as_dict=True)
	return dict(row or {})


def _first(*sources):
	"""The first non-blank value across ``(mapping, fields)`` pairs, stripped."""
	for mapping, fields in sources:
		for field in fields:
			value = (mapping or {}).get(field)
			if value and str(value).strip():
				return str(value).strip()
	return ""


def _render_address(name):
	"""An Address rendered through the site's Address Template, or ``""``.

	``check_permissions=False`` because the printing user already holds the document
	and is being shown its party's address, not browsing the Address list; frappe's own
	``get_company_address`` renders the same way.
	"""
	if not name:
		return ""
	from frappe.contacts.doctype.address.address import render_address

	return render_address(name, check_permissions=False) or ""


def _contact_name(values):
	full = (values.get("full_name") or "").strip()
	if full:
		return full
	return " ".join(p for p in ((values.get("first_name") or "").strip(), (values.get("last_name") or "").strip()) if p)


def party_of(doc):
	"""``(party_type, party_name)`` for a sales or buying document, or ``(None, None)``."""
	if doc.get("doctype") == "Quotation":
		return (doc.get("quotation_to") or "Customer"), doc.get("party_name")
	if doc.get("supplier"):
		return "Supplier", doc.get("supplier")
	if doc.get("customer"):
		return "Customer", doc.get("customer")
	return None, None


def party_details(doc):
	"""Name, address, attention line, phone and email for ``doc``'s party.

	Returns a dict with those five keys; any may be ``""``. The document's own values
	win at every step, so a print never contradicts the form it was printed from.
	"""
	party_type, party = party_of(doc)
	title = (doc.get("customer_name") or doc.get("supplier_name") or "").strip()
	details = {
		"name": title or (party or ""),
		"address": doc.get("address_display") or "",
		"attention": (doc.get("contact_display") or "").strip(),
		"phone": _first((doc, ("contact_mobile", "contact_phone"))),
		"email": _first((doc, ("contact_email",))),
	}
	try:
		_fill_from_records(doc, party_type, party, details)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Print party lookup")
	return details


def _fill_from_records(doc, party_type, party, details):
	spec = PARTY_FIELDS.get(party_type)
	if not spec or not party:
		return

	wanted = spec["title"] + spec["address"] + spec["address_text"] + spec["contact"] + spec["phone"] + spec["email"]
	master = _values(party_type, party, wanted)
	if not details["name"] or details["name"] == party:
		details["name"] = _first((master, spec["title"])) or details["name"]

	# The address the page prints, and the Address record behind it (for its phone and
	# email). The document's own link first; the party's links only when the document
	# printed no address at all.
	address_name = doc.get("customer_address") or doc.get("supplier_address") or ""
	if not details["address"] and address_name and frappe.db.exists("Address", address_name):
		# The document names an Address but its printed snapshot is blank (saved before the
		# address was filled in, or cleared by hand): the document's own choice still wins.
		details["address"] = _render_address(address_name)
	if not details["address"]:
		address_name = ""
		for field in spec["address"]:
			link = master.get(field)
			if link and frappe.db.exists("Address", link):
				details["address"] = _render_address(link)
				address_name = link
				break
		if not details["address"]:
			details["address"] = _first((master, spec["address_text"]))

	# The contact the document names, else the party's primary one -- for phone and
	# email only. The attention line stays the document's: printing "Attn:" at somebody
	# the document did not name would address it to the wrong person.
	contact_name = doc.get("contact_person") or _first((master, spec["contact"]))
	contact = _values("Contact", contact_name, CONTACT_PHONE + CONTACT_EMAIL + CONTACT_NAME)
	address = _values("Address", address_name, ADDRESS_PHONE + ADDRESS_EMAIL)

	if not details["phone"]:
		details["phone"] = _first(
			(contact, CONTACT_PHONE), (address, ADDRESS_PHONE), (master, spec["phone"])
		)
	if not details["email"]:
		details["email"] = _first(
			(contact, CONTACT_EMAIL), (master, spec["email"]), (address, ADDRESS_EMAIL)
		)


def ps_party(doc):
	"""The party block — name, address, Attn, phone, email — as print-ready HTML."""
	d = party_details(doc)
	return ps.party_block(d["name"], d["address"], d["attention"], d["phone"], d["email"])


# --- Request for Quotation -------------------------------------------------------------
# An RFQ has no party of its own: it names its suppliers in a child table. ERPNext prints
# it once per supplier, setting `vendor` before each render (and to the first supplier for
# the preview), so a render with `vendor` set is addressed to that one supplier.


def rfq_supplier_details(doc):
	"""One details dict per supplier the RFQ is addressed to (just ``vendor`` when set)."""
	rows = list(doc.get("suppliers") or [])
	vendor = doc.get("vendor")
	if vendor:
		matching = [r for r in rows if r.get("supplier") == vendor]
		rows = matching or [frappe._dict(supplier=vendor)]
	out = []
	for row in rows:
		supplier = row.get("supplier")
		details = {
			"name": (row.get("supplier_name") or "").strip() or (supplier or ""),
			"address": "",
			"attention": "",
			"phone": "",
			"email": (row.get("email_id") or "").strip(),
		}
		try:
			_fill_rfq_row(row, supplier, details)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Print party lookup")
		out.append(details)
	return out


def _fill_rfq_row(row, supplier, details):
	spec = PARTY_FIELDS["Supplier"]
	wanted = spec["title"] + spec["address"] + spec["address_text"] + spec["contact"] + spec["phone"] + spec["email"]
	master = _values("Supplier", supplier, wanted)
	if not details["name"] or details["name"] == supplier:
		details["name"] = _first((master, spec["title"])) or details["name"]

	address_name = ""
	for field in spec["address"]:
		link = master.get(field)
		if link and frappe.db.exists("Address", link):
			details["address"] = _render_address(link)
			address_name = link
			break
	if not details["address"]:
		details["address"] = _first((master, spec["address_text"]))

	# The row names its own contact, so it is also the attention line.
	contact = _values("Contact", row.get("contact"), CONTACT_PHONE + CONTACT_EMAIL + CONTACT_NAME)
	if contact:
		details["attention"] = _contact_name(contact)
	else:
		contact = _values(
			"Contact", _first((master, spec["contact"])), CONTACT_PHONE + CONTACT_EMAIL + CONTACT_NAME
		)
	address = _values("Address", address_name, ADDRESS_PHONE + ADDRESS_EMAIL)

	details["phone"] = _first((contact, CONTACT_PHONE), (address, ADDRESS_PHONE), (master, spec["phone"]))
	if not details["email"]:
		details["email"] = _first((contact, CONTACT_EMAIL), (master, spec["email"]), (address, ADDRESS_EMAIL))


def ps_rfq_suppliers(doc):
	"""Every addressed supplier's block, stacked; a dash when the RFQ names none."""
	blocks = [
		ps.party_block(d["name"], d["address"], d["attention"], d["phone"], d["email"])
		for d in rfq_supplier_details(doc)
	]
	blocks = [b for b in blocks if b]
	if not blocks:
		return f'<span style="color:{ps.INK_700}">&mdash;</span>'
	return '<div style="margin-top:8px"></div>'.join(blocks)


# --- Taxes and charges -----------------------------------------------------------------
# A sales document's taxes table on this site holds more than tax. QuickBooks writes a
# billable expense onto an invoice as a line with no Item, and the QBO sync books each one
# as an `Actual` charge in the taxes table (quickbooks_online/core/mapping.py,
# `_sales_passthrough_charges`) because there is no Item to put on a line: on production
# 154 invoices carry 1,030 of them -- the material ("HAS15841 HASA MURIATIC ACID") and its
# markup ("25% markup for HAS15841"), booked to Cost of Goods Sold and Income accounts.
# Printed as taxes they sat under "Subtotal" looking like tax, up to 98 rows deep. The
# customer was billed for them as lines, which is how QuickBooks printed them, so that is
# how they print here. A row is tax when its account is a Tax account, or when it is not
# a flat `Actual` amount (a percentage row is a rate, whatever it is booked to).


def _is_charge(row):
	if (row.get("charge_type") or "Actual") != "Actual":
		return False
	account = row.get("account_head")
	if not account:
		return False
	return (frappe.get_cached_value("Account", account, "account_type") or "") != "Tax"


def ps_charge_rows(doc):
	"""The non-tax rows of ``doc.taxes`` as ``[{description, amount}]`` -- lines, not tax."""
	out = []
	try:
		for row in doc.get("taxes") or []:
			if row.get("tax_amount") and _is_charge(row):
				out.append({"description": ps.rich_text(row.get("description")), "amount": row.get("tax_amount")})
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Print charge rows")
		return []
	return out


def ps_tax_rows(doc):
	"""The tax rows of ``doc.taxes`` as ``[{label, amount}]``, labels without the
	company suffix. Every non-zero row that `ps_charge_rows` does not claim."""
	abbr = None
	try:
		if doc.get("company"):
			abbr = frappe.get_cached_value("Company", doc.get("company"), "abbr")
	except Exception:
		abbr = None
	out = []
	for row in doc.get("taxes") or []:
		if not row.get("tax_amount"):
			continue
		try:
			if _is_charge(row):
				continue
		except Exception:
			pass
		out.append({"label": ps.escape_html(ps.clean_label(row.get("description"), abbr)), "amount": row.get("tax_amount")})
	return out
