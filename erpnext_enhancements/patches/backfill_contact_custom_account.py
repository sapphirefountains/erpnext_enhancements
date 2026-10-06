import frappe


def execute():
	"""One-time normalization of ``Contact.custom_account`` to its invariant AS IT WAS
	until v1.575.0 ("the first Customer link"). Ran on prod 2026-07-10. **Do not re-run
	it**: v1.575.0 replaced that invariant (contacts_ux.sync_contact_account_links),
	because "first Customer link" handed a supplier's employee the first Customer a
	directory fan-out linked them to. Re-running this would put those back, by
	``db.set_value`` and so with no Version row.

	When this ran, the field had just been made editable and kept in two-way sync
	with the Links grid by ``contacts_ux.sync_contact_account_links``, whose
	invariant was then "it mirrors the FIRST Customer Dynamic Link row" (until
	v1.575.0). Before that it was read-only and written only
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
