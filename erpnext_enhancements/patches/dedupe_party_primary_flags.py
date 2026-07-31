"""De-duplicate accounts left with more than one primary Contact/Address (v1.198.0).

Until v1.198.0 the directory widget derived its account as
``frm.doc.customer || frm.doc.supplier || frm.doc.party_name || frm.doc.name``. On an
**Opportunity** that produced the pair ``("Opportunity", <a Customer/Lead id>)``, because
Opportunity's party discriminator is ``opportunity_from``, not ``party_type``. The
"unset the others" ``Dynamic Link`` query in ``sync_contact.set_primary_contact`` then
matched nothing, so the new flag was set without the previous one being cleared and
several Contacts (or Addresses) ended up flagged primary for the same account.

That class is repairable and this patch repairs it. On production at the time of
writing: four Customers with two primary contacts each (AE URBIA, Insomniac, Kapture
Vision, Michael Stone) and one with two primary addresses (Hess Construction LLC).

**The other class is not repairable, and this patch deliberately does not try.** A
"Set Primary" click on a **Project** passed that Project's Customer to the same endpoint,
which cleared the account's real primary and set a new one — overwriting the previous
value with no record anywhere of what it had been, and bumping ``modified`` in the
process. So this patch only ever *removes* a duplicate flag. It never invents a primary,
and it never moves one where exactly one already exists.

Winner, in order:

1. the account's own stock witness field (``customer_primary_contact`` /
   ``customer_primary_address`` / ``supplier_primary_address``). ERPNext maintains those
   and ``sync_contact`` has never written them, so where one is set it is the only
   uncorrupted record of intent;
2. otherwise the least recently modified flagged row, on the reasoning that the strays
   are the ones the buggy widget added on top of an existing primary.

``update_modified=False`` on purpose, mirroring ``backfill_contact_custom_account``: this
is pure denormalisation repair, and a real ``doc.save()`` would fan out through
``sync_contact.sync_from_contact`` into a re-save of every Project/Opportunity/Supplier/
Customer naming that Contact as primary — each of which then fires the global
``"*": {"after_save": ...triton_sync}`` hook.

Idempotent: a second run finds nothing to do.
"""

import frappe

#: (link doctype, record doctype, flag field, witness field on the account)
_TARGETS = (
	("Customer", "Contact", "is_primary_contact", "customer_primary_contact"),
	("Customer", "Address", "is_primary_address", "customer_primary_address"),
	("Supplier", "Contact", "is_primary_contact", None),
	("Supplier", "Address", "is_primary_address", "supplier_primary_address"),
)


def execute():
	cleared = 0
	for account_doctype, record_doctype, flag_field, witness_field in _TARGETS:
		# Fresh-DB safety: these hooks and patches run during ERPNext's own test
		# bootstrap, before custom columns exist.
		if not frappe.db.has_column(record_doctype, flag_field):
			continue
		if witness_field and not frappe.db.has_column(account_doctype, witness_field):
			witness_field = None

		cleared += _dedupe(account_doctype, record_doctype, flag_field, witness_field)

	if cleared:
		frappe.logger().info(f"dedupe_party_primary_flags: cleared {cleared} stray primary flag(s)")


def _dedupe(account_doctype, record_doctype, flag_field, witness_field):
	rows = frappe.db.sql(
		f"""
		SELECT dl.link_name AS account, r.name AS record, r.modified AS modified
		FROM `tabDynamic Link` dl
		JOIN `tab{record_doctype}` r ON r.name = dl.parent
		WHERE dl.parenttype = %(record_doctype)s
		  AND dl.link_doctype = %(account_doctype)s
		  AND r.`{flag_field}` = 1
		ORDER BY dl.link_name, r.modified
		""",
		{"record_doctype": record_doctype, "account_doctype": account_doctype},
		as_dict=True,
	)

	# Distinct *records* per account, not distinct link rows. One Contact linked to the
	# same Customer by two Dynamic Link rows is a different defect (duplicate links) and
	# arrives here as two rows naming one record — it is emphatically not two competing
	# primaries, and clearing "the other one" would unflag the only primary there is.
	# Live example: Customer "Michael  Stone".
	by_account = {}
	for row in rows:
		seen = by_account.setdefault(row["account"], {})
		seen.setdefault(row["record"], row)

	cleared = 0
	for account, distinct in by_account.items():
		flagged = list(distinct.values())
		if len(flagged) < 2:
			continue

		keep = _pick_primary(flagged, _witness(account_doctype, account, witness_field))
		for row in flagged:
			if row["record"] == keep:
				continue
			frappe.db.set_value(
				record_doctype, row["record"], flag_field, 0, update_modified=False
			)
			cleared += 1

	return cleared


def _witness(account_doctype, account_name, witness_field):
	if not witness_field:
		return None
	return frappe.db.get_value(account_doctype, account_name, witness_field)


def _pick_primary(flagged, witness):
	"""Which of several flagged records keeps the flag.

	Pure enough to reason about on its own: the witness wins if it is one of the
	candidates, otherwise the least recently modified does. ``flagged`` arrives ordered
	by ``modified`` ascending.
	"""
	if witness:
		for row in flagged:
			if row["record"] == witness:
				return witness
	return flagged[0]["record"]
