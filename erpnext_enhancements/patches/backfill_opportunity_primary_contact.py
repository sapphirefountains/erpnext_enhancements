"""Seed Opportunity.primary_contact from contact_person where it is blank (v1.199.0).

Opportunity carries **two** Link-to-Contact fields, and until v1.199.0 both were
labelled "Full Name" — which is how the Kanban board ended up showing the wrong one.
`contact_person` is stock ERPNext and is the field the business has actually been
filling in; `primary_contact` is this app's, and the Kanban is moving onto it.

The numbers are the whole reason this patch exists. Of 814 Opportunities on production:

    have contact_person                          154
    have primary_contact                          21
    have contact_person but NOT primary_contact  142
    have both, and they differ                     5

Swapping the board's card field without this would blank roughly 142 cards that show a
name today — the board would get materially worse before it got better.

**Insert-only, and the five that differ are left alone.** Where both fields are already
set, somebody made a deliberate distinction between "the contact on this deal" and "the
primary contact", and this patch is not entitled to collapse it.

Runs after :mod:`dedupe_party_primary_flags` conceptually but does not depend on it:
this writes a doc-local Link field and never touches the account-wide
``is_primary_contact`` flag — that separation is exactly what v1.198.0 established.
Writes with ``update_modified=False`` and ``db.set_value`` rather than ``doc.save()``,
so a 142-row backfill does not fire ``on_update`` (and through it ``sync_from_main_doc``,
the Drive folder hooks, and the global Triton ``after_save``) 142 times.

Idempotent: a second run finds nothing blank left to fill.
"""

import frappe


def execute():
	if not frappe.db.has_column("Opportunity", "primary_contact"):
		# The field is created by setup/custom_fields.py on after_migrate, which runs
		# after patches. On a fresh site there is nothing to backfill anyway.
		return

	rows = frappe.db.sql(
		"""
		SELECT name, contact_person
		FROM `tabOpportunity`
		WHERE contact_person IS NOT NULL AND contact_person != ''
		  AND (primary_contact IS NULL OR primary_contact = '')
		""",
		as_dict=True,
	)
	if not rows:
		return

	filled = 0
	for row in rows:
		# The Contact may have been deleted out from under a stale link; skip rather
		# than write a dangling Link that fails validation on the next save.
		if not frappe.db.exists("Contact", row["contact_person"]):
			continue
		frappe.db.set_value(
			"Opportunity", row["name"], "primary_contact", row["contact_person"], update_modified=False
		)
		filled += 1

	frappe.logger().info(
		f"backfill_opportunity_primary_contact: filled {filled} of {len(rows)} blank primary_contact links"
	)
