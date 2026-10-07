"""Contacts & Addresses UX — server half.

Three independent pieces (see also
``public/js/global_enhancements/contact_address_quick_entry.js``):

1. **``sync_contact_account_links``** — keeps ``Contact.custom_account`` (the
   editable "Account" Link, Customer) and the ``links`` Dynamic Link grid in
   two-way sync. Wired to Contact ``before_insert`` + ``validate`` doc_events;
   it only mutates the in-flight doc (no saves), so it needs no loop guards
   and is a no-op inside ``sync_contact.sync_from_main_doc``'s re-save.

   The Account says which Customer the person WORKS FOR, so it is always one of
   the Contact's Customer links, but not every Customer link is an employer: a
   consultant linked to a project's customer is on that customer's job, not on
   its payroll. Rules (v1.575.0):

   * A stored Account that is still linked is kept. Appending or reordering
     Customer rows never swaps it for another one.
   * A Customer the user picks in the Account field is honoured when the grid
     links it, even in the same save as a grid edit.
   * Otherwise a new Account is adopted only from a Customer row ADDED in this
     save that is also the Contact's first organisation link (no Supplier row
     before it).
   * Clearing the Account removes that one row and leaves the field blank, also
     in the same save as a grid edit. Another Customer row is never promoted.
   * An Account the Contact is no longer linked to is cleared on save.

   Until v1.575.0 the field simply mirrored the FIRST Customer row on every save.
   A supplier's employee has no Customer row of their own, so the first Customer
   the directory fanned them out to became their Account: five Supplier contacts
   on a Harwood project showed "Account: Harwood", and clearing the field promoted
   the next fanned-out Customer (Layton Construction) instead of clearing it.

   Precedence when both the field and the grid changed in one save: the grid
   wins — every programmatic path (``link_existing_record``, telephony
   auto-create, erpnext's ``create_primary_contact``…) mutates links, and a
   stale field value must never override a deliberate link change — EXCEPT that
   a field set to a Customer the grid links, or emptied, is the user's explicit
   choice and is honoured (a stale value cannot be either: the Desk rejects a
   stale form, and an unlinked value still loses).

   Known bypass (accepted): raw ``frappe.db.set_value``/``db_set`` writes skip
   validate, so they skip this sync. Nothing in the codebase db-sets
   ``custom_account`` or Contact links today.

2. **``AddressLinkGuard``** — wired through ``extend_doctype_class``: an Address created
   by a staff (System) user is never filed under that user's own Contact links. See the
   class for the frappe rule it narrows.

3. **``get_directory_onload``** — powers the no-reload refresh of the stock
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
	``Contact.autoname`` names the record ``full_name-{links[0].link_name}``,
	so an explicitly chosen Account names the record after the employer."""
	row = doc.append(
		"links",
		{"link_doctype": "Customer", "link_name": customer, "link_title": _customer_title(customer)},
	)
	doc.links.remove(row)
	doc.links.insert(0, row)
	_reindex_links(doc)


#: The parties a person can work for. A Lead or Prospect row is the same person
#: before conversion, and a Project/Opportunity row is a job, so neither decides it.
_ORGANISATION_DOCTYPES = ("Customer", "Supplier")


def _derived_account(doc, kept, old_customers):
	"""The Account after a save that changed the Customer rows (or changed nothing).

	``kept`` is the stored Account, or the Account picked in this save when the grid links it
	(on insert, the value the caller passed). It survives while it is still linked. Otherwise the only Customer adopted
	is one ADDED in this save that is also the first organisation row, so a Customer
	linked behind the person's own Supplier, or one they already had and were
	deliberately cleared from, never becomes their employer.
	"""
	linked = [link.link_name for link in _customer_link_rows(doc.links)]
	if kept and kept in linked:
		return kept
	first_org = next((link for link in doc.links or [] if link.link_doctype in _ORGANISATION_DOCTYPES), None)
	if first_org and first_org.link_doctype == "Customer" and first_org.link_name not in old_customers:
		return first_org.link_name
	return None


def sync_contact_account_links(doc, method=None):
	"""Two-way ``custom_account`` <-> Customer link row sync (see module docstring).

	* Grid changed AND the field emptied: an explicit clear (the old Account's row is
	  removed, nothing is promoted).
	* Grid changed (or nothing changed): keep the stored Account, or a Customer picked in
	  this save that the grid links, while it is still linked; else adopt only a newly
	  added Customer that is the first organisation row; else blank
	  (:func:`_derived_account`).
	* Only the field changed — REPLACE semantics on the OLD Account's row: it is
	  swapped in place for the new customer (non-Customer links untouched; in-place
	  keeps row order, hence naming, stable), or dropped when the new customer is
	  already linked. With no old Account row the new customer is inserted first.
	  Clearing the field removes the old Account's row and leaves the field blank.

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

	if links_changed and account_changed and not new_account and old_account:
		# The Account was emptied in the same save as a grid edit: an explicit clear, honoured
		# exactly like a field-only clear (remove that one row, promote nothing). Treating it as
		# "grid wins" handed the old Account straight back.
		old_row = next((row for row in new_customer_rows if row.link_name == old_account), None)
		if old_row:
			doc.links.remove(old_row)
			_reindex_links(doc)
		doc.custom_account = None
		return

	if links_changed or not account_changed:
		# Grid wins / normalization. What is "kept" is the stored Account, except when
		# the caller set the field in this same save (or this is an insert) to a Customer
		# the grid still links: an explicit, consistent choice, not a stale rider, so it
		# wins. Deleting the "Big D Construction" row and picking "Big-D Construction" in
		# one save must give Big-D, not a blank.
		chose_linked = new_account and new_account in new_customers and (not old or account_changed)
		kept = new_account if chose_linked else old_account
		doc.custom_account = _derived_account(doc, kept, set(old_customers))
		return

	old_row = next((row for row in new_customer_rows if row.link_name == old_account), None)
	if new_account:
		if new_account in new_customers:
			# Target already linked: drop the displaced Account row if different.
			if old_row and old_row.link_name != new_account:
				doc.links.remove(old_row)
				_reindex_links(doc)
		elif old_row:
			# Swap the old Account row in place (preserves row order).
			old_row.link_name = new_account
			old_row.link_title = _customer_title(new_account)
		else:
			_insert_customer_link_first(doc, new_account)
	elif old_row:
		# Field cleared: unlink the Account row. No other Customer row is promoted.
		doc.links.remove(old_row)
		_reindex_links(doc)

	doc.custom_account = new_account or None


class AddressLinkGuard:
	"""Address extension (``extend_doctype_class``): no fallback filing under the creator.

	frappe's ``Address.link_address`` runs on EVERY validate. When an Address has no links it
	looks up the Contact whose ``email_id`` is the address's ``owner`` (the user who created
	it) and copies all of that Contact's links onto the address. For a portal customer that
	is the point: their own Contact is their account, so an address they add lands there.
	For staff it is a silent misfiling. A staff member's own Contact can be linked to
	anything, and every address they ever create without a link inherits it. A stray Lead
	on James Harris's Contact filed eight company addresses (MGM Grand, CenterCal, Big-D…)
	under that Lead in 2025, and Nikolas Bradshaw's Contact carries a Customer, a Lead and an
	Opportunity left over from Fountain Move testing. A linkless address is reachable from the
	Desk: New Address on a form that has not been saved yet has no record to link to.

	So for an address created by a System User the fallback is skipped and the address stays
	unlinked, visibly, for a person to file. Portal (Website User) addresses keep frappe's
	behaviour. ERPNext extends the same method (``ERPNextAddress.link_address`` skips company
	addresses); this one runs first and defers to it for everyone else.
	"""

	def link_address(self):
		if not self.get("links") and _created_by_staff(self.get("owner")):
			return False
		return super().link_address()


def _created_by_staff(user):
	"""Whether ``user`` is a desk (System) user. Unknown or blank counts as not staff, so the
	portal behaviour is what an unexpected owner gets, exactly as before this guard."""
	if not user:
		return False
	return frappe.db.get_value("User", user, "user_type", cache=True) == "System User"


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
