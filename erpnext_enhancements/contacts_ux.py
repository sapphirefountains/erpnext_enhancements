"""Contacts & Addresses UX — server half.

Two independent pieces (see also
``public/js/global_enhancements/contact_address_quick_entry.js``):

1. **``sync_contact_account_links``** — keeps ``Contact.custom_account`` (the
   editable "Account" Link, Customer) and the ``links`` Dynamic Link grid in
   two-way sync. Wired to Contact ``before_insert`` + ``validate`` doc_events;
   it only mutates the in-flight doc (no saves), so it needs no loop guards
   and is a no-op inside ``sync_contact.sync_from_main_doc``'s re-save.

   Invariant: ``custom_account`` always mirrors the FIRST Customer link row.
   Precedence when both the field and the grid changed in one save: the grid
   wins — every programmatic path (``link_existing_record``, telephony
   auto-create, erpnext's ``create_primary_contact``…) mutates links, and a
   stale field value must never override a deliberate link change.

   Known bypass (accepted): raw ``frappe.db.set_value``/``db_set`` writes skip
   validate, so they skip this sync. Nothing in the codebase db-sets
   ``custom_account`` or Contact links today.

2. **``get_directory_onload``** — powers the no-reload refresh of the stock
   "Contacts & Addresses" section after a Contact/Address is created or saved
   elsewhere. Returns exactly what the party doc's ``onload`` would have put
   in ``__onload`` (for Opportunity that includes the party-merged lists,
   replicating ``Opportunity.onload``), so the client can re-render
   ``frappe.contacts.render_address_and_contact`` without ``frm.reload_doc()``
   — a reload would discard the user's unsaved edits.
"""

import frappe


def _customer_link_rows(links):
	return [link for link in (links or []) if link.link_doctype == "Customer"]


def _customer_title(name):
	return frappe.db.get_value("Customer", name, "customer_name") or name


def _reindex_links(doc):
	for i, link in enumerate(doc.links or []):
		link.idx = i + 1


def _insert_customer_link_first(doc, customer):
	"""Insert a Customer link as the FIRST row — row order matters: core
	``Contact.autoname`` names the record ``full_name-{links[0].link_name}``
	and this module's invariant reads the first Customer row."""
	row = doc.append(
		"links",
		{"link_doctype": "Customer", "link_name": customer, "link_title": _customer_title(customer)},
	)
	doc.links.remove(row)
	doc.links.insert(0, row)
	_reindex_links(doc)


def sync_contact_account_links(doc, method=None):
	"""Two-way ``custom_account`` <-> Customer link row sync (see module docstring).

	* Grid changed (or nothing changed): field := first Customer link. This is
	  also the self-heal path for legacy Contacts whose field the old read-only
	  client mirror never persisted.
	* Only the field changed — REPLACE semantics: the first Customer row is
	  swapped in place for the new customer (non-Customer links untouched;
	  in-place keeps row order, hence naming, stable). Clearing the field
	  removes that row; any remaining Customer link then becomes the account.

	Hooked to ``before_insert`` as well as ``validate`` because naming runs
	before validate — an API insert carrying only ``custom_account`` still gets
	its Customer row (and therefore its ``-Customer`` name suffix) in time.
	"""
	old = doc.get_doc_before_save()
	old_account = ((old.custom_account if old else "") or "").strip()
	new_account = (doc.custom_account or "").strip()
	old_customers = [link.link_name for link in _customer_link_rows(old.links if old else [])]
	new_customer_rows = _customer_link_rows(doc.links)
	new_customers = [link.link_name for link in new_customer_rows]

	links_changed = new_customers != old_customers
	account_changed = new_account != old_account

	if links_changed or not account_changed:
		# Grid wins / normalization: mirror the first Customer link.
		doc.custom_account = new_customers[0] if new_customers else None
		return

	if new_account:
		if new_account in new_customers:
			# Target already linked: drop the displaced first row if different.
			if new_customers[0] != new_account:
				doc.links.remove(new_customer_rows[0])
				_reindex_links(doc)
		elif new_customer_rows:
			# Swap the first Customer row in place (preserves row order).
			row = new_customer_rows[0]
			row.link_name = new_account
			row.link_title = _customer_title(new_account)
		else:
			_insert_customer_link_first(doc, new_account)
	elif new_customer_rows:
		# Field cleared: unlink the account row. A remaining Customer link (if
		# any) becomes the account below — the field mirrors the first row, so
		# it can only be blank when no Customer link remains.
		doc.links.remove(new_customer_rows[0])
		_reindex_links(doc)

	remaining = _customer_link_rows(doc.links)
	doc.custom_account = remaining[0].link_name if remaining else None


@frappe.whitelist()
def get_directory_onload(doctype, name):
	"""Fresh ``contact_list`` / ``addr_list`` for a party form's stock section.

	Permission model matches a form load: read permission on the party doc is
	required; the underlying display-list helpers additionally silently return
	[] without Contact/Address read permission (same as ``onload``).
	"""
	from frappe.contacts.doctype.address.address import get_address_display_list
	from frappe.contacts.doctype.contact.contact import get_contact_display_list

	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")

	contact_list = get_contact_display_list(doctype, name)
	addr_list = get_address_display_list(doctype, name)

	# Opportunity's onload shows the PARTY's contacts/addresses merged with its
	# own (party's first) — replicate, or the section would show a different
	# set after our refresh than after a reload (Opportunity.onload parity).
	if doctype == "Opportunity" and doc.get("opportunity_from") and doc.get("party_name"):
		party_contacts = get_contact_display_list(doc.opportunity_from, doc.party_name)
		party_addrs = get_address_display_list(doc.opportunity_from, doc.party_name)
		contact_list = party_contacts + [c for c in contact_list if c not in party_contacts]
		addr_list = party_addrs + [a for a in addr_list if a not in party_addrs]

	return {"contact_list": contact_list, "addr_list": addr_list}
