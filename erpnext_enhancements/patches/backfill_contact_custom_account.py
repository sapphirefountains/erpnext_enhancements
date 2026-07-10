import frappe


def execute():
	"""One-time normalization of ``Contact.custom_account`` to its invariant.

	The field is now editable and kept in two-way sync with the Links grid by
	``contacts_ux.sync_contact_account_links`` (invariant: it mirrors the FIRST
	Customer Dynamic Link row). Historically it was read-only and written only
	by a client-side mirror that persisted whenever someone happened to save the
	Contact form — so existing values are stale ("first Customer link at some
	past save"), orphaned (link since removed), or missing entirely, and the
	field shows in list views. Every value is therefore recomputed from the
	links, not just backfilled where empty.

	Plain ``db.set_value(..., update_modified=False)`` on purpose: this is pure
	denormalization — full ``doc.save()``s would fire ``sync_from_contact``,
	gravatar lookups and per-link title fetches across every Contact on the
	site. Idempotent (second run finds nothing to change).
	"""
	if not frappe.db.has_column("Contact", "custom_account"):
		return

	rows = frappe.db.sql(
		"""
		select parent, link_name
		from `tabDynamic Link`
		where parenttype = 'Contact' and parentfield = 'links'
			and link_doctype = 'Customer'
		order by parent, idx
		"""
	)
	first_customer = {}
	for parent, link_name in rows:
		first_customer.setdefault(parent, link_name)

	changed = 0
	for contact in frappe.get_all("Contact", fields=["name", "custom_account"]):
		target = first_customer.get(contact.name)
		if (contact.custom_account or None) != (target or None):
			frappe.db.set_value(
				"Contact", contact.name, "custom_account", target, update_modified=False
			)
			changed += 1

	if changed:
		print(f"backfill_contact_custom_account: normalized {changed} contact(s)")
